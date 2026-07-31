from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

import artifact_inputs
from artifact_inputs import (
    create_snapshot,
    declared_inputs,
    remove_snapshot,
    target_cache,
    verify_gate_input_completeness,
    working_digest,
)


RS = Path("hardware/Esp32Tap/firmware/esp32_rs")
SNAPSHOT_MARKER = ".esp32tap-snapshot-v1"
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


@pytest.fixture(autouse=True)
def cleanup_sealed_snapshots(tmp_path: Path):
    yield
    markers = [
        marker
        for marker in tmp_path.rglob(SNAPSHOT_MARKER)
        if (
            marker.is_file()
            and not marker.is_symlink()
            and marker.stat().st_nlink == 1
            and stat.S_IMODE(marker.stat().st_mode) == 0o444
        )
    ]
    for marker in sorted(markers, key=lambda path: len(path.parts), reverse=True):
        root = marker.parent
        if os.path.lexists(root):
            remove_snapshot(root, root.parent)


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
        RS / "build_devkit_bringup/output.bin",
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


def test_exact_untracked_legacy_provenance_markers_are_excluded(repo: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    markers = (
        RS / f".artifact-provenance-legacy-build-{'a' * 64}.json",
        RS / f".artifact-provenance-legacy-build_qemu_test-{'0' * 64}.json",
    )
    for marker in markers:
        write(repo, marker, "generated provenance\n")

    paths = set(declared_inputs(repo))

    assert not paths.intersection(marker.as_posix() for marker in markers)
    assert working_digest(repo) == original


@pytest.mark.parametrize(
    "marker",
    [
        RS / f".artifact-provenance-legacy-build-{'A' * 64}.json",
        RS / f".artifact-provenance-legacy-build-{'a' * 63}.json",
        RS / f".artifact-provenance-legacy-build-{'a' * 65}.json",
        RS / f".artifact-provenance-legacy-build_debug-{'a' * 64}.json",
        RS / f"x.artifact-provenance-legacy-build-{'a' * 64}.json",
        RS / f".artifact-provenance-legacy-build-{'a' * 64}-extra.json",
        RS / "nested" / f".artifact-provenance-legacy-build-{'a' * 64}.json",
        RS / f".artifact_provenance-legacy-build-{'a' * 64}.json",
    ],
)
def test_legacy_provenance_marker_lookalikes_remain_inputs(
    repo: Path, marker: Path
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    write(repo, marker, "not an exact generated marker\n")

    assert marker.as_posix() in declared_inputs(repo)
    assert working_digest(repo) != original


def test_tracked_exact_legacy_provenance_marker_remains_an_input(repo: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    marker = RS / f".artifact-provenance-legacy-build-{'b' * 64}.json"
    write(repo, marker, "tracked source with generated-looking name\n")
    commit_all(repo)

    assert marker.as_posix() in declared_inputs(repo)
    assert working_digest(repo) != original


def test_exact_untracked_legacy_swap_trees_do_not_change_digest(repo: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    swaps = (
        RS / f".artifact-provenance-legacy-build-{'c' * 64}.swap",
        RS / f".artifact-provenance-legacy-build_qemu_test-{'1' * 64}.swap",
    )
    generated = tuple(
        child
        for swap in swaps
        for child in (swap / "sdkconfig", swap / "metadata.json")
    )
    for child in generated:
        write(repo, child, "retained transaction evidence\n")

    paths = set(declared_inputs(repo))

    assert not paths.intersection(child.as_posix() for child in generated)
    assert working_digest(repo) == original


@pytest.mark.parametrize(
    "child",
    [
        RS / f".artifact-provenance-legacy-build-{'C' * 64}.swap" / "sdkconfig",
        RS / f".artifact-provenance-legacy-build-{'c' * 63}.swap" / "sdkconfig",
        RS / f".artifact-provenance-legacy-build-{'c' * 65}.swap" / "sdkconfig",
        RS / f".artifact-provenance-legacy-build_debug-{'c' * 64}.swap" / "sdkconfig",
        RS / f"x.artifact-provenance-legacy-build-{'c' * 64}.swap" / "sdkconfig",
        RS / f".artifact-provenance-legacy-build-{'c' * 64}.swap-extra" / "sdkconfig",
        RS
        / "nested"
        / f".artifact-provenance-legacy-build-{'c' * 64}.swap"
        / "sdkconfig",
        RS / f".artifact_provenance-legacy-build-{'c' * 64}.swap" / "sdkconfig",
    ],
)
def test_legacy_swap_tree_lookalikes_remain_inputs(repo: Path, child: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    write(repo, child, "not exact retained transaction evidence\n")

    assert child.as_posix() in declared_inputs(repo)
    assert working_digest(repo) != original


def test_tracked_exact_legacy_swap_tree_descendant_remains_an_input(
    repo: Path,
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    original = working_digest(repo)
    child = RS / f".artifact-provenance-legacy-build-{'d' * 64}.swap" / "sdkconfig"
    write(repo, child, "tracked transaction-shaped source\n")
    commit_all(repo)

    assert child.as_posix() in declared_inputs(repo)
    assert working_digest(repo) != original


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


def test_devkit_target_cache_is_separate_and_generated_bytes_are_ignored(
    repo: Path,
) -> None:
    source = write(repo, RS / "devkit_bringup/src/main.rs", "fn main() {}\n")
    write(repo, RS / "bringup_core/src/lib.rs", "pub const SAFE: bool = true;\n")
    write(repo, RS / "sdkconfig.defaults.devkit", 'CONFIG_IDF_TARGET="esp32s3"\n')
    commit_all(repo)
    original = working_digest(repo)

    generated = write(repo, RS / "build_devkit_bringup/esp32tap.bin", "generated\n")
    target = write(repo, RS / "devkit_bringup/target/release/devkit_bringup", "elf\n")

    assert source.relative_to(repo).as_posix() in declared_inputs(repo)
    assert generated.relative_to(repo).as_posix() not in declared_inputs(repo)
    assert target.relative_to(repo).as_posix() not in declared_inputs(repo)
    assert working_digest(repo) == original
    assert target_cache(repo, "devkit") not in {
        target_cache(repo, "prod"),
        target_cache(repo, "qemu"),
    }


def test_devkit_sources_config_builders_and_planned_bench_gates_are_inputs(
    repo: Path,
) -> None:
    required = (
        RS / "bringup_core/src/lib.rs",
        RS / "devkit_bringup/src/main.rs",
        RS / "sdkconfig.defaults.devkit",
        RS / "tools/build.sh",
        RS / "tools/build_image.sh",
        RS / "tools/devkit_bench.py",
        RS / "tools/test_devkit_bench.py",
        RS / "tools/test_devkit_source_contract.py",
    )
    for path in required:
        write(repo, path, f"input {path.name}\n")
    commit_all(repo)

    paths = set(declared_inputs(repo))
    assert paths.issuperset(path.as_posix() for path in required)


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
    matches = list(repo.rglob(missing))
    assert len(matches) == 1
    matches[0].unlink()
    snapshot = create_snapshot(repo, tmp_path / "snapshot", tmp_path / "target")

    with pytest.raises(RuntimeError, match="gate.*failed"):
        verify_gate_input_completeness(snapshot.root)


def test_snapshot_rejects_existing_destination(repo: Path, tmp_path: Path) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    destination = tmp_path / "snapshot"
    destination.mkdir()

    with pytest.raises(FileExistsError, match="destination"):
        create_snapshot(repo, destination, tmp_path / "target")


def test_remove_snapshot_removes_sealed_tree_and_is_idempotent(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    parent = tmp_path / "snapshots"
    parent.mkdir()
    snapshot = create_snapshot(repo, parent / "snapshot", tmp_path / "target")

    artifact_inputs.remove_snapshot(snapshot.root, parent)

    assert not os.path.lexists(snapshot.root)
    artifact_inputs.remove_snapshot(snapshot.root, parent)


def test_published_snapshot_has_sealed_cleanup_capability_outside_digest(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, RS / "esp32tap/src/main.rs")
    commit_all(repo)
    parent = tmp_path / "snapshots"
    parent.mkdir()
    expected_digest = working_digest(repo)

    snapshot = create_snapshot(repo, parent / "snapshot", tmp_path / "target")
    marker = snapshot.root / SNAPSHOT_MARKER

    assert marker.is_file()
    assert not marker.is_symlink()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o444
    assert SNAPSHOT_MARKER not in snapshot.paths
    assert snapshot.digest == expected_digest


def test_remove_snapshot_rejects_git_repository_before_chmod_or_rmtree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = write(repo, "DO_NOT_DELETE", "git repository")
    protected.chmod(0o444)
    original_mode = stat.S_IMODE(protected.stat().st_mode)
    rmtree_called = False

    def forbidden_rmtree(*args, **kwargs) -> None:
        nonlocal rmtree_called
        rmtree_called = True
        raise AssertionError("rmtree must not be reached for a Git repository")

    monkeypatch.setattr(artifact_inputs.shutil, "rmtree", forbidden_rmtree)

    with pytest.raises(ValueError, match="Git|snapshot|capability"):
        remove_snapshot(repo, tmp_path)

    assert not rmtree_called
    assert (repo / ".git").is_dir()
    assert protected.read_text(encoding="utf-8") == "git repository"
    assert stat.S_IMODE(protected.stat().st_mode) == original_mode


def test_remove_snapshot_rejects_linked_worktree_before_chmod_or_rmtree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo, "tracked.txt", "tracked")
    commit_all(repo)
    worktree = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "-q", "-b", "cleanup-worktree", str(worktree))
    protected = write(worktree, "DO_NOT_DELETE", "linked worktree")
    protected.chmod(0o444)
    original_mode = stat.S_IMODE(protected.stat().st_mode)
    rmtree_called = False

    def forbidden_rmtree(*args, **kwargs) -> None:
        nonlocal rmtree_called
        rmtree_called = True
        raise AssertionError("rmtree must not be reached for a Git worktree")

    monkeypatch.setattr(artifact_inputs.shutil, "rmtree", forbidden_rmtree)

    with pytest.raises(ValueError, match="Git|snapshot|capability"):
        remove_snapshot(worktree, tmp_path)

    assert not rmtree_called
    assert (worktree / ".git").is_file()
    assert protected.read_text(encoding="utf-8") == "linked worktree"
    assert stat.S_IMODE(protected.stat().st_mode) == original_mode


def test_remove_snapshot_rejects_existing_non_snapshot_before_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "snapshots"
    ordinary = parent / "ordinary-directory"
    protected = write(ordinary, "DO_NOT_DELETE", "ordinary")
    protected.chmod(0o444)
    original_mode = stat.S_IMODE(protected.stat().st_mode)

    def forbidden_rmtree(*args, **kwargs) -> None:
        raise AssertionError("rmtree must not be reached without snapshot capability")

    monkeypatch.setattr(artifact_inputs.shutil, "rmtree", forbidden_rmtree)

    with pytest.raises(ValueError, match="snapshot|capability"):
        remove_snapshot(ordinary, parent)

    assert protected.read_text(encoding="utf-8") == "ordinary"
    assert stat.S_IMODE(protected.stat().st_mode) == original_mode


@pytest.mark.parametrize(
    "case",
    [
        "root",
        "home",
        "worktree",
        "same",
        "wrong",
        "outside",
        "missing_parent",
        "outside_missing",
    ],
)
def test_remove_snapshot_rejects_broad_or_wrong_parent(
    repo: Path, tmp_path: Path, case: str
) -> None:
    safe_parent = tmp_path / "snapshots"
    safe_parent.mkdir()
    wrong_parent = tmp_path / "wrong-parent"
    wrong_parent.mkdir()
    if case == "root":
        snapshot_root = Path("/task2-missing-snapshot")
        expected_parent = Path("/")
    elif case == "home":
        snapshot_root = Path.home() / "task2-missing-snapshot"
        expected_parent = Path.home()
    elif case == "worktree":
        snapshot_root = repo / "task2-missing-snapshot"
        expected_parent = repo
    elif case == "same":
        snapshot_root = safe_parent
        expected_parent = safe_parent
    elif case == "wrong":
        snapshot_root = safe_parent / "snapshot"
        expected_parent = wrong_parent
    elif case == "outside":
        snapshot_root = tmp_path / "outside"
        expected_parent = safe_parent
    elif case == "missing_parent":
        snapshot_root = tmp_path / "missing-parent" / "snapshot"
        expected_parent = tmp_path / "missing-parent"
    else:
        snapshot_root = tmp_path / "missing-outside" / "snapshot"
        expected_parent = safe_parent

    with pytest.raises(ValueError, match="parent|broad|worktree"):
        artifact_inputs.remove_snapshot(snapshot_root, expected_parent)


def test_remove_snapshot_rejects_symlink_root(repo: Path, tmp_path: Path) -> None:
    parent = tmp_path / "snapshots"
    parent.mkdir()
    outside = tmp_path / "outside"
    marker = write(outside, "marker", "untouched")
    snapshot_link = parent / "snapshot"
    snapshot_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        artifact_inputs.remove_snapshot(snapshot_link, parent)

    assert snapshot_link.is_symlink()
    assert marker.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize("marker_kind", ["symlink", "fifo", "hardlink"])
def test_remove_snapshot_rejects_invalid_capability_types(
    tmp_path: Path, marker_kind: str
) -> None:
    parent = tmp_path / "snapshots"
    root = parent / "ordinary"
    protected = write(root, "DO_NOT_DELETE", "untouched")
    protected.chmod(0o444)
    marker = root / SNAPSHOT_MARKER
    outside = write(tmp_path, f"{marker_kind}-outside", "not a capability")
    if marker_kind == "symlink":
        marker.symlink_to(outside)
    elif marker_kind == "fifo":
        os.mkfifo(marker)
    else:
        os.link(outside, marker)

    with pytest.raises(ValueError, match="capability"):
        remove_snapshot(root, parent)

    assert protected.read_text(encoding="utf-8") == "untouched"
    assert outside.read_text(encoding="utf-8") == "not a capability"


def test_remove_snapshot_rejects_invalid_capability_content(tmp_path: Path) -> None:
    parent = tmp_path / "snapshots"
    root = parent / "ordinary"
    protected = write(root, "DO_NOT_DELETE", "untouched")
    protected.chmod(0o444)
    marker = write(root, SNAPSHOT_MARKER, "forged")
    marker.chmod(0o444)

    with pytest.raises(ValueError, match="capability.*invalid"):
        remove_snapshot(root, parent)

    assert protected.read_text(encoding="utf-8") == "untouched"
    marker.chmod(0o644)


def test_remove_snapshot_never_follows_internal_symlink(
    repo: Path, tmp_path: Path
) -> None:
    target = write(repo, RS / "shared/target.py", "inside")
    link = repo / RS / "tools/internal-link.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(target, link.parent))
    commit_all(repo)
    parent = tmp_path / "snapshots"
    parent.mkdir()
    snapshot = create_snapshot(repo, parent / "snapshot", tmp_path / "target")
    outside = tmp_path / "outside"
    marker = write(outside, "marker", "untouched")
    copied_link = snapshot.root / link.relative_to(repo)
    copied_link.parent.chmod(0o755)
    copied_link.unlink()
    copied_link.symlink_to(outside, target_is_directory=True)
    copied_link.parent.chmod(0o555)

    artifact_inputs.remove_snapshot(snapshot.root, parent)

    assert not os.path.lexists(snapshot.root)
    assert marker.read_text(encoding="utf-8") == "untouched"


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

    with pytest.raises(ValueError, match="inside repo"):
        create_snapshot(repo, destination, tmp_path / "target")


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
