from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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


def test_digest_ignores_mtime_but_not_same_size_content(repo: Path) -> None:
    source = write(repo, RS / "esp32tap/src/main.rs", "aaaa")
    commit_all(repo)
    original = working_digest(repo)

    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert working_digest(repo) == original

    source.write_text("bbbb", encoding="utf-8")
    assert working_digest(repo) != original


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
            "import subprocess\n"
            "from gate_helper import VALUE\n"
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
