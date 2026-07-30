from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import artifact_inputs
from artifact_inputs import (
    create_snapshot,
    declared_inputs,
    target_cache,
    verify_gate_input_completeness,
    working_digest,
)


RS = Path("hardware/Esp32Tap/firmware/esp32_rs")
GATES = (
    "check_unsafe_budget.py",
    "check_case_parity.py",
    "check_pins.py",
    "check_wdt_chain.py",
)


def write(root: Path, relative: str | Path, content: str = "input\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Artifact Input Test")
    return root


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", "fixture")


def restore_test_tree_write(root: Path, expected_parent: Path) -> None:
    """Make one explicit pytest-temp child removable without following links."""

    root = root.absolute()
    expected_parent = expected_parent.resolve(strict=True)
    if root.parent.resolve(strict=True) != expected_parent or root == expected_parent:
        raise ValueError(
            f"refusing broad test cleanup outside {expected_parent}: {root}"
        )
    if not os.path.lexists(root) or root.is_symlink():
        return
    for directory, dirnames, filenames in os.walk(
        root, topdown=False, followlinks=False
    ):
        base = Path(directory)
        for name in filenames:
            path = base / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)
        for name in dirnames:
            path = base / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)
        base.chmod(stat.S_IMODE(base.stat().st_mode) | stat.S_IWUSR)


def remove_test_tree(root: Path, expected_parent: Path) -> None:
    restore_test_tree_write(root, expected_parent)
    if not os.path.lexists(root):
        return
    if root.is_symlink():
        root.unlink()
    else:
        shutil.rmtree(root)


@pytest.fixture(autouse=True)
def cleanup_sealed_snapshots(tmp_path: Path):
    yield
    for child in tmp_path.iterdir():
        if child.name.startswith("snapshot"):
            remove_test_tree(child, tmp_path)


def test_digest_ignores_mtime_but_not_same_size_content(repo: Path) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "aaaa")
    commit_all(repo)
    original = working_digest(repo)

    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert working_digest(repo) == original

    source.write_text("bbbb", encoding="utf-8")
    assert working_digest(repo) != original


def test_chmod_only_change_changes_working_and_snapshot_digests(
    repo: Path, tmp_path: Path
) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "same bytes")
    source.chmod(0o644)
    commit_all(repo)
    digest_0644 = working_digest(repo)
    snapshot_0644 = create_snapshot(
        repo, tmp_path / "snapshot-0644", tmp_path / "target"
    )

    source.chmod(0o755)
    digest_0755 = working_digest(repo)
    snapshot_0755 = create_snapshot(
        repo, tmp_path / "snapshot-0755", tmp_path / "target"
    )

    assert digest_0755 != digest_0644
    assert snapshot_0644.digest == digest_0644
    assert snapshot_0755.digest == digest_0755
    assert snapshot_0755.digest != snapshot_0644.digest


def test_snapshot_is_immutable_against_later_live_edit(
    repo: Path, tmp_path: Path
) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "before")
    commit_all(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    source.write_text("after!", encoding="utf-8")

    assert (snapshot.root / source.relative_to(repo)).read_text(
        encoding="utf-8"
    ) == "before"
    assert snapshot.digest != working_digest(repo)


def test_published_snapshot_files_and_directories_are_sealed(
    repo: Path, tmp_path: Path
) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "sealed")
    source.chmod(0o755)
    commit_all(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")
    copied = snapshot.root / source.relative_to(repo)
    original_digest = snapshot.digest

    assert stat.S_IMODE(copied.stat().st_mode) == 0o555
    assert not stat.S_IMODE(snapshot.root.stat().st_mode) & 0o222
    assert all(
        not stat.S_IMODE(path.stat().st_mode) & 0o222
        for path in snapshot.root.rglob("*")
        if path.is_dir()
    )
    with pytest.raises(PermissionError):
        copied.write_text("mutated", encoding="utf-8")
    replacement = write(tmp_path, "replacement", "replacement")
    with pytest.raises(PermissionError):
        replacement.replace(copied)
    assert copied.read_text(encoding="utf-8") == "sealed"
    assert snapshot.digest == original_digest


def test_dirty_tracked_and_relevant_untracked_inputs_are_included(repo: Path) -> None:
    tracked = write(repo, RS / "esp32tap/src/main.rs", "old")
    commit_all(repo)
    tracked.write_text("dirty", encoding="utf-8")
    untracked_source = write(repo, RS / "new_crate/src/lib.rs", "new")
    untracked_config = write(repo, RS / "new_crate/Cargo.toml", "[package]\n")

    paths = declared_inputs(repo)

    assert tracked.relative_to(repo).as_posix() in paths
    assert untracked_source.relative_to(repo).as_posix() in paths
    assert untracked_config.relative_to(repo).as_posix() in paths


def test_tracked_deletion_changes_digest_and_is_absent_from_snapshot(
    repo: Path, tmp_path: Path
) -> None:
    deleted = write(repo, RS / "esp32tap/src/deleted.rs", "gone")
    write(repo, RS / "esp32tap/src/main.rs", "kept")
    commit_all(repo)
    before = working_digest(repo)

    deleted.unlink()
    after = working_digest(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    assert after != before
    assert deleted.relative_to(repo).as_posix() not in snapshot.paths
    assert not (snapshot.root / deleted.relative_to(repo)).exists()


def test_rename_changes_paths_and_digest(repo: Path) -> None:
    old = write(repo, RS / "esp32tap/src/old.rs", "same bytes")
    commit_all(repo)
    before_paths = declared_inputs(repo)
    before_digest = working_digest(repo)

    new = old.with_name("new.rs")
    old.rename(new)

    assert declared_inputs(repo) != before_paths
    assert working_digest(repo) != before_digest
    assert new.relative_to(repo).as_posix() in declared_inputs(repo)


def test_internal_symlink_is_preserved_and_target_is_added_transitively(
    repo: Path, tmp_path: Path
) -> None:
    target = write(repo, "shared/generated_target", "target bytes")
    link = repo / RS / "tools/check_pins.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(target, link.parent))
    git(repo, "add", link.relative_to(repo).as_posix())
    git(repo, "commit", "-qm", "fixture symlink")

    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")
    copied_link = snapshot.root / link.relative_to(repo)

    assert copied_link.is_symlink()
    assert os.readlink(copied_link) == os.readlink(link)
    assert copied_link.resolve().is_relative_to(snapshot.root.resolve())
    assert copied_link.read_text(encoding="utf-8") == "target bytes"
    assert target.relative_to(repo).as_posix() in snapshot.paths


def test_internal_symlink_chain_is_preserved_and_complete(
    repo: Path, tmp_path: Path
) -> None:
    target = write(repo, "shared/target.py", "chain target")
    middle = repo / "shared/middle.py"
    middle.symlink_to("target.py")
    link = repo / RS / "tools/check_pins.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(middle, link.parent))
    git(repo, "add", link.relative_to(repo).as_posix())
    git(repo, "commit", "-qm", "fixture symlink chain")

    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    copied_link = snapshot.root / link.relative_to(repo)
    copied_middle = snapshot.root / middle.relative_to(repo)
    assert copied_link.is_symlink()
    assert copied_middle.is_symlink()
    assert copied_link.resolve().is_relative_to(snapshot.root.resolve())
    assert copied_link.read_text(encoding="utf-8") == "chain target"
    assert {
        target.relative_to(repo).as_posix(),
        middle.relative_to(repo).as_posix(),
    }.issubset(snapshot.paths)


@pytest.mark.parametrize(
    ("link_target", "message"),
    [
        ("/etc/passwd", "absolute"),
        ("../../../../../../../../outside", "outside"),
        ("missing.py", "broken"),
    ],
)
def test_unsafe_symlinks_are_rejected(
    repo: Path, link_target: str, message: str
) -> None:
    link = repo / RS / "tools/link.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(link_target)
    git(repo, "add", link.relative_to(repo).as_posix())

    with pytest.raises(ValueError, match=message):
        declared_inputs(repo)


def test_symlink_swap_after_enumeration_is_revalidated(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "safe")
    commit_all(repo)
    original_collect = artifact_inputs._collect_paths

    def collect_then_swap(root: Path) -> tuple[str, ...]:
        paths = original_collect(root)
        source.unlink()
        source.symlink_to("/etc/passwd")
        return paths

    monkeypatch.setattr(artifact_inputs, "_collect_paths", collect_then_swap)

    with pytest.raises(ValueError, match="absolute symlink"):
        working_digest(repo)


def test_generated_outputs_are_excluded_by_exact_directory_name(repo: Path) -> None:
    excluded = (
        RS / "build/output.bin",
        RS / "build_qemu_test/output.bin",
        RS / ".artifacts/report.json",
        RS / "esp32tap/target/debug/app",
        RS / "esp32tap/__pycache__/cache.pyc",
        RS / "esp32tap/.pytest_cache/state",
    )
    included = (
        RS / "build.rs",
        RS / "build_support/source.rs",
        RS / "rebuild/source.rs",
        RS / "tools/build.sh",
        RS / "tools/build_image.sh",
        Path("hardware/Esp32Tap/firmware/build_safety_manifest.py"),
    )
    for path in excluded + included:
        write(repo, path)
    commit_all(repo)

    paths = set(declared_inputs(repo))

    assert not paths.intersection(path.as_posix() for path in excluded)
    assert paths.issuperset(path.as_posix() for path in included)


def test_unrelated_untracked_files_caches_and_secrets_are_excluded(repo: Path) -> None:
    tracked = write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    unrelated = (
        "notes.txt",
        "notes.py",
        "config.json",
        "Makefile",
        "download.bin",
        "scratch/random.dat",
        "scratch/notes.py",
        "scratch/config.json",
        "scratch/Makefile",
        "another_project/src/lib.rs",
        str(RS / "esp32tap/src/__pycache__/thing.py"),
        str(RS / "esp32tap/.pytest_cache/state.json"),
        str(RS / "esp32tap/.env"),
        str(RS / "esp32tap/credentials.json"),
        str(RS / "esp32tap/private.key"),
    )
    for path in unrelated:
        write(repo, path)

    paths = set(declared_inputs(repo))

    assert tracked.relative_to(repo).as_posix() in paths
    assert not paths.intersection(unrelated)


def test_tracked_inputs_are_limited_to_explicit_build_scope(repo: Path) -> None:
    included = (
        RS / "esp32tap/src/main.rs",
        Path("hardware/Esp32Tap/firmware/esp32/components/esp_hal/pins.hpp"),
        Path("hardware/Esp32Tap/tools/design.py"),
        Path("hardware/Esp32Tap/tests/test_firmware_safety_model.py"),
        Path("hardware/Esp32Tap/firmware/PLAN.md"),
        Path("hardware/Esp32Tap/firmware/safety_model.py"),
        Path("hardware/Esp32Tap/firmware/safety_manifest.schema.json"),
        Path("hardware/Esp32Tap/firmware/build_safety_manifest.py"),
        Path("cpp/captures/try3.csv"),
        Path("deploy/treadmill.avahi-service"),
    )
    unrelated = (
        Path("README.md"),
        Path("docs/build_notes.md"),
        Path("app/src/main.rs"),
        Path("scratch/config.json"),
        Path("token.json"),
        Path("hardware/Esp32Tap/REPORT.md"),
        Path("hardware/Esp32Tap/tools/unrelated.py"),
        Path("hardware/Esp32Tap/tests/unrelated.py"),
        Path("hardware/Esp32Tap/firmware/unrelated.md"),
        RS / ".env.production",
        RS / "token.json",
    )
    for path in included + unrelated:
        write(repo, path)
    commit_all(repo)

    paths = set(declared_inputs(repo))

    assert paths.issuperset(path.as_posix() for path in included)
    assert not paths.intersection(path.as_posix() for path in unrelated)


def test_unusual_git_path_with_newline_is_nul_safe(repo: Path, tmp_path: Path) -> None:
    unusual = write(repo, RS / "esp32tap/src/line\nbreak.rs", "unusual")
    commit_all(repo)

    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    relative = unusual.relative_to(repo).as_posix()
    assert relative in snapshot.paths
    assert (snapshot.root / relative).read_text(encoding="utf-8") == "unusual"


def test_target_cache_uses_physical_worktree_and_separates_kinds(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "README.md")
    commit_all(repo)
    other = tmp_path / "other-worktree"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))

    prod = target_cache(repo, "prod")
    qemu = target_cache(repo, "qemu")
    other_prod = target_cache(other, "prod")

    assert prod.parent != other_prod.parent
    assert prod.name == "prod"
    assert qemu.name == "qemu"
    assert prod.parent.name.startswith("esp32tap-target-")
    assert len(prod.parent.name.removeprefix("esp32tap-target-")) == 12
    with pytest.raises(ValueError, match="kind"):
        target_cache(repo, "debug")


def test_snapshot_mtimes_are_newer_than_newest_prior_target_output(
    repo: Path, tmp_path: Path
) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs")
    target = write(repo, "shared/target.py")
    link = repo / RS / "tools/check_pins.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(target, link.parent))
    commit_all(repo)
    cache = tmp_path / "target"
    output = write(cache, "debug/output", "artifact")
    future = max(output.stat().st_mtime_ns, source.stat().st_mtime_ns) + 2_000_000_000
    os.utime(output, ns=(future, future))

    snapshot = create_snapshot(repo, tmp_path / "snapshot", cache)

    for relative in snapshot.paths:
        copied = snapshot.root / relative
        assert copied.lstat().st_mtime_ns > future


def make_gate_fixture(repo: Path) -> None:
    tools = repo / RS / "tools"
    helper = write(repo, tools.relative_to(repo) / "gate_helper.py", "VALUE = 'ok'\n")
    data = write(repo, RS / "gate-data.txt", "ok\n")
    native = write(repo, RS / "native_gate", "#!/bin/sh\nexit 0\n")
    native.chmod(0o755)
    for gate in GATES:
        script = (
            "from pathlib import Path\n"
            "import os, site, subprocess, sys\n"
            "from gate_helper import VALUE\n"
            "assert sys.flags.isolated == 1\n"
            "assert site.ENABLE_USER_SITE is False\n"
            "assert os.environ['PYTHONNOUSERSITE'] == '1'\n"
            "root = Path(__file__).resolve().parents[5]\n"
            f"assert (root / {str(data.relative_to(repo))!r}).read_text().strip() == VALUE\n"
            f"subprocess.run([str(root / {str(native.relative_to(repo))!r})], check=True)\n"
        )
        write(repo, tools.relative_to(repo) / gate, script)
    commit_all(repo)
    assert helper.exists()


def test_completeness_runs_all_four_gates_from_snapshot(
    repo: Path, tmp_path: Path
) -> None:
    make_gate_fixture(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    unavailable = tmp_path / "source-unavailable"
    assert repo.parent == tmp_path
    assert not unavailable.exists()
    repo.rename(unavailable)
    try:
        verify_gate_input_completeness(snapshot.root)
    finally:
        unavailable.rename(repo)


def test_completeness_ignores_user_site_pth_and_sitecustomize(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_gate_fixture(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")
    marker = tmp_path / "user-site-loaded"
    user_base = tmp_path / "user-base"
    version = f"python{os.sys.version_info.major}.{os.sys.version_info.minor}"
    site_packages = user_base / "lib" / version / "site-packages"
    write(
        site_packages,
        "influence.pth",
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('pth')\n",
    )
    write(
        site_packages,
        "sitecustomize.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('sitecustomize')\n",
    )
    real_python = artifact_inputs.sys.executable
    wrapper = write(
        tmp_path,
        "python-with-user-site",
        "#!/bin/sh\n"
        f"export PYTHONUSERBASE={shlex.quote(str(user_base))}\n"
        f'exec {shlex.quote(real_python)} "$@"\n',
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(artifact_inputs.sys, "executable", str(wrapper))

    verify_gate_input_completeness(snapshot.root)

    assert not marker.exists()


def test_completeness_exposes_absolute_fallback_to_live_source(
    repo: Path, tmp_path: Path
) -> None:
    make_gate_fixture(repo)
    live_only = write(repo, "live-only.txt", "live\n")
    gate = repo / RS / "tools/check_unsafe_budget.py"
    gate.write_text(
        "from pathlib import Path\n"
        f"assert Path({str(live_only)!r}).read_text(encoding='utf-8') == 'live\\n'\n",
        encoding="utf-8",
    )
    commit_all(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    verify_gate_input_completeness(snapshot.root)
    unavailable = tmp_path / "source-unavailable"
    assert repo.parent == tmp_path
    assert not unavailable.exists()
    repo.rename(unavailable)
    try:
        with pytest.raises(
            RuntimeError,
            match="check_unsafe_budget.py.*failed|failed.*check_unsafe_budget.py",
        ):
            verify_gate_input_completeness(snapshot.root)
    finally:
        unavailable.rename(repo)


@pytest.mark.parametrize("missing", ["gate_helper.py", "gate-data.txt", "native_gate"])
def test_completeness_fails_for_missing_transitive_input(
    repo: Path, tmp_path: Path, missing: str
) -> None:
    make_gate_fixture(repo)
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")
    restore_test_tree_write(snapshot.root, tmp_path)
    matches = list(snapshot.root.rglob(missing))
    assert len(matches) == 1
    matches[0].unlink()

    with pytest.raises(RuntimeError, match="gate.*failed"):
        verify_gate_input_completeness(snapshot.root)


def test_snapshot_rejects_existing_destination(repo: Path, tmp_path: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    destination = tmp_path / "snapshot"
    destination.mkdir()

    with pytest.raises(FileExistsError, match="destination"):
        create_snapshot(repo, destination, tmp_path / "target")


@pytest.mark.parametrize("through_symlink", [False, True])
def test_snapshot_rejects_destination_inside_repo(
    repo: Path, tmp_path: Path, through_symlink: bool
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    if through_symlink:
        parent = tmp_path / "repo-alias"
        parent.symlink_to(repo, target_is_directory=True)
    else:
        parent = repo
    destination = parent / "snapshot"
    physical_destination = repo / "snapshot"

    try:
        with pytest.raises(ValueError, match="inside repo"):
            create_snapshot(repo, destination, tmp_path / "target")
    finally:
        remove_test_tree(physical_destination, repo)


def test_destination_race_does_not_replace_empty_directory(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    destination = tmp_path / "snapshot"
    staging_path: list[Path] = []
    attacker_inode: list[int] = []
    original_mkdtemp = artifact_inputs.tempfile.mkdtemp

    def race_destination(*args, **kwargs) -> str:
        result = Path(original_mkdtemp(*args, **kwargs))
        staging_path.append(result)
        destination.mkdir()
        attacker_inode.append(destination.stat().st_ino)
        return str(result)

    monkeypatch.setattr(artifact_inputs.tempfile, "mkdtemp", race_destination)

    with pytest.raises(FileExistsError):
        create_snapshot(repo, destination, tmp_path / "target")

    assert destination.is_dir()
    assert destination.stat().st_ino == attacker_inode[0]
    assert not list(destination.iterdir())
    assert not staging_path[0].exists()


def test_real_repository_declares_build_inputs_and_safe_pin_symlink(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[5]
    paths = set(declared_inputs(repo_root))
    required = {
        str(RS / "esp32tap/build.rs"),
        str(RS / "difftest/build.rs"),
        str(RS / "tools/build.sh"),
        "hardware/Esp32Tap/firmware/build_safety_manifest.py",
        str(RS / "tools/check_pins.py"),
        "hardware/Esp32Tap/firmware/esp32/tools/check_pins.py",
    }

    assert paths.issuperset(required)
    assert "README.md" not in paths
    assert "hardware/Esp32Tap/REPORT.md" not in paths
    snapshot = create_snapshot(repo_root, tmp_path / "snapshot", tmp_path / "target")
    copied = snapshot.root / RS / "tools/check_pins.py"
    assert copied.is_symlink()
    assert copied.resolve().is_relative_to(snapshot.root.resolve())


@pytest.mark.slow
def test_real_repository_snapshot_runs_all_current_host_gates(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[5]
    disposable = tmp_path / "disposable-checkout"
    subprocess.run(
        ["git", "clone", "-q", "--shared", str(repo_root), str(disposable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    snapshot = create_snapshot(disposable, tmp_path / "snapshot", tmp_path / "target")

    unavailable = tmp_path / "disposable-source-unavailable"
    assert disposable.parent == tmp_path
    assert not unavailable.exists()
    disposable.rename(unavailable)
    try:
        verify_gate_input_completeness(snapshot.root)
    finally:
        unavailable.rename(disposable)
