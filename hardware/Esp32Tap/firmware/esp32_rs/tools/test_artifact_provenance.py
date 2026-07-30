from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

import artifact_provenance as provenance
from artifact_provenance import (
    BUNDLE_MEMBERS,
    EXIT_INTERNAL,
    EXIT_INVALID,
    EXIT_MISSING,
    EXIT_STALE,
    MANIFEST_NAME,
    Toolchain,
    locked_exec_many,
    make_manifest,
    manifest_bytes,
    publish_generation_atomic,
    shared_bundle,
    verify_locked,
)


TOOLS = Path(__file__).resolve().parent
MODULE = TOOLS / "artifact_provenance.py"


@pytest.fixture
def toolchain() -> Toolchain:
    return Toolchain(
        image_id="sha256:" + "1" * 64,
        recipe_sha256="2" * 64,
        image_tag="esp32tap-rust:build",
        idf_commit="3" * 40,
        rustc_verbose="rustc 1.88.0-dev\ncommit-hash: abc",
        target="xtensa-esp32s3-espidf",
        linker_version="GNU ld 2.43.1",
        esptool_version="esptool.py v4.9.0",
        component_lock_sha256="4" * 64,
        profile="release",
        features=("qemu-test", "ble", "net"),
    )


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    rs = root / "hardware/Esp32Tap/firmware/esp32_rs"
    rs.mkdir(parents=True)
    staging = rs / "staging-production"
    staging.mkdir()
    for index, member in enumerate(BUNDLE_MEMBERS):
        (staging / member).write_bytes(f"{index}:{member}\n".encode())
    return root, rs, staging


def manifest_for(
    staging: Path,
    toolchain: Toolchain,
    *,
    kind: str = "production",
    digest: str = "a" * 64,
) -> dict:
    selected = toolchain
    if kind == "production":
        selected = replace(toolchain, features=())
    return make_manifest(staging, kind, digest, selected)


def canonical_toolchain_json(toolchain: Toolchain) -> str:
    value = {
        **toolchain.__dict__,
        "features": list(toolchain.features),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def publish(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    *,
    kind: str = "production",
) -> tuple[Path, dict]:
    _, rs, staging = layout
    public = rs / ("build" if kind == "production" else "build_qemu_test")
    manifest = manifest_for(staging, toolchain, kind=kind)
    publish_generation_atomic(staging, public, manifest)
    return public, manifest


def wait_for_path(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def test_manifest_round_trip_is_exact_canonical_and_deterministic(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    _, _, staging = layout
    manifest = manifest_for(staging, toolchain)
    encoded = manifest_bytes(manifest)

    assert encoded.endswith(b"\n")
    assert encoded == (
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )
    assert json.loads(encoded) == manifest
    identity = manifest["manifest_sha256"]
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    assert identity == hashlib.sha256(manifest_bytes(unsigned)).hexdigest()
    assert (
        make_manifest(staging, "production", "a" * 64, replace(toolchain, features=()))
        == manifest
    )
    assert tuple(entry["name"] for entry in manifest["members"]) == BUNDLE_MEMBERS


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_bundle", EXIT_MISSING),
        ("missing_manifest", EXIT_MISSING),
        ("stale", EXIT_STALE),
        ("malformed", EXIT_INVALID),
        ("missing_member", EXIT_INVALID),
        ("extra_member", EXIT_INVALID),
        ("changed_member", EXIT_INVALID),
    ],
)
def test_verification_exit_classification(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    mutation: str,
    code: int,
) -> None:
    public, _ = publish(layout, toolchain)
    bundle = public
    if mutation == "missing_bundle":
        public.unlink()
    elif mutation == "missing_manifest":
        (bundle / MANIFEST_NAME).unlink()
    elif mutation == "malformed":
        (bundle / MANIFEST_NAME).write_text("{", encoding="utf-8")
    elif mutation == "missing_member":
        (bundle / BUNDLE_MEMBERS[0]).unlink()
    elif mutation == "extra_member":
        (bundle / "extra.bin").write_bytes(b"extra")
    elif mutation == "changed_member":
        (bundle / BUNDLE_MEMBERS[0]).write_bytes(b"changed")

    result = verify_locked(
        bundle,
        replace(toolchain, features=()),
        "b" * 64 if mutation == "stale" else "a" * 64,
    )
    assert result.code == code


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "directory", "fifo"])
def test_bundle_members_must_be_regular_non_symlink_single_link_files(
    layout: tuple[Path, Path, Path], toolchain: Toolchain, unsafe: str
) -> None:
    public, _ = publish(layout, toolchain)
    member = public / BUNDLE_MEMBERS[0]
    member.unlink()
    if unsafe == "symlink":
        member.symlink_to(BUNDLE_MEMBERS[1])
    elif unsafe == "hardlink":
        os.link(public / BUNDLE_MEMBERS[1], member)
    elif unsafe == "directory":
        member.mkdir()
    else:
        os.mkfifo(member)

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


def test_manifest_must_be_safe_regular_single_link_file(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    public, _ = publish(layout, toolchain)
    manifest = public / MANIFEST_NAME
    contents = manifest.read_bytes()
    manifest.unlink()
    target = public / "manifest-target"
    target.write_bytes(contents)
    manifest.symlink_to(target.name)

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_id", "sha256:" + "9" * 64),
        ("recipe_sha256", "8" * 64),
        ("image_tag", "other:tag"),
        ("idf_commit", "7" * 40),
        ("rustc_verbose", "rustc other"),
        ("target", "different-target"),
        ("linker_version", "different linker"),
        ("esptool_version", "different esptool"),
        ("component_lock_sha256", "6" * 64),
        ("profile", "debug"),
        ("features", ("different",)),
    ],
)
def test_each_toolchain_fact_mismatch_is_invalid(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    field: str,
    value: object,
) -> None:
    public, _ = publish(layout, toolchain)
    expected = replace(toolchain, features=())
    expected = replace(expected, **{field: value})
    assert verify_locked(public, expected, "a" * 64).code == EXIT_INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 99),
        ("kind", "qemu-test"),
        ("input_digest", "not-a-digest"),
    ],
)
def test_manifest_schema_kind_and_digest_are_strictly_validated(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    field: str,
    value: object,
) -> None:
    public, manifest = publish(layout, toolchain)
    manifest[field] = value
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes(unsigned)).hexdigest()
    (public / MANIFEST_NAME).write_bytes(manifest_bytes(manifest))

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


def test_toolchain_sorts_features_and_rejects_duplicates_and_ambiguous_values(
    toolchain: Toolchain,
) -> None:
    assert replace(toolchain, features=("net", "ble")).features == ("ble", "net")
    with pytest.raises(ValueError, match="duplicate"):
        replace(toolchain, features=("net", "net"))
    with pytest.raises(ValueError):
        replace(toolchain, features=(" net",))
    with pytest.raises(ValueError):
        replace(toolchain, features=("NET",))
    with pytest.raises(ValueError):
        replace(toolchain, recipe_sha256="ABC")
    with pytest.raises(ValueError):
        replace(toolchain, image_id="mutable-image-name")
    with pytest.raises(ValueError):
        replace(toolchain, image_tag="image tag")


def test_lock_path_matches_build_script_physical_worktree_algorithm(
    layout: tuple[Path, Path, Path],
) -> None:
    root, rs, _ = layout
    expected = Path("/tmp") / (
        "esp32tap-build-"
        + hashlib.md5(str(rs.resolve()).encode(), usedforsecurity=False).hexdigest()[
            :12
        ]
        + ".lock"
    )
    assert provenance.lock_path(root, "production") == expected
    assert provenance.lock_path(root, "qemu-test") == expected


def test_shared_lock_blocks_exclusive_and_exception_releases(
    layout: tuple[Path, Path, Path],
) -> None:
    root, _, _ = layout
    lock = provenance.lock_path(root, "production")
    with pytest.raises(RuntimeError):
        with shared_bundle(root, "production"):
            probe = open(lock, "a+")
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise RuntimeError("release me")
            finally:
                probe.close()
    probe = open(lock, "a+")
    try:
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        probe.close()


def test_publish_with_caller_owned_exclusive_fd_does_not_self_deadlock(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
) -> None:
    root, rs, staging = layout
    manifest = manifest_for(staging, toolchain)
    source = (
        "import fcntl,json,os;"
        "from pathlib import Path;"
        "from artifact_provenance import lock_path,publish_generation_atomic;"
        f"root=Path({str(root)!r});"
        "fd=os.open(lock_path(root,'production'),os.O_RDWR|os.O_CREAT,0o600);"
        "os.set_inheritable(fd,True);fcntl.flock(fd,fcntl.LOCK_EX);"
        f"publish_generation_atomic(Path({str(staging)!r}),"
        f"Path({str(rs / 'build')!r}),json.loads({json.dumps(manifest)!r}),"
        "lock_fd=fd)"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(TOOLS)

    completed = subprocess.run(
        [sys.executable, "-c", source],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    assert (rs / "build").is_symlink()


@pytest.mark.parametrize("mode", ["wrong", "shared", "noninheritable"])
def test_publish_rejects_incorrect_caller_lock_fd(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
    mode: str,
) -> None:
    root, rs, staging = layout
    path = (
        tmp_path / "wrong.lock"
        if mode == "wrong"
        else provenance.lock_path(root, "production")
    )
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.set_inheritable(fd, mode != "noninheritable")
        fcntl.flock(fd, fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX)
        with pytest.raises(provenance.InvalidError, match="lock"):
            publish_generation_atomic(
                staging,
                rs / "build",
                manifest_for(staging, toolchain),
                lock_fd=fd,
            )
    finally:
        os.close(fd)


def test_locked_exec_child_inherits_lock_until_it_exits(
    layout: tuple[Path, Path, Path], toolchain: Toolchain, tmp_path: Path
) -> None:
    root, _, _ = layout
    publish(layout, toolchain)
    ready = tmp_path / "ready"
    code = (
        "import pathlib,time;"
        f"pathlib.Path({str(ready)!r}).write_text('ready');"
        "time.sleep(.6)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(TOOLS)
    expected = replace(toolchain, features=())
    expected_json = canonical_toolchain_json(expected)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import json;"
                "from pathlib import Path;"
                "import artifact_provenance as p;"
                "facts=json.loads(" + repr(expected_json) + ");"
                "facts['features']=tuple(facts['features']);"
                "p._current_input_digest=lambda _:'a'*64;"
                "p._current_toolchain=lambda _,_kind:p.Toolchain(**facts);"
                f"p.locked_exec(Path({str(root)!r}),'production',"
                f"[{sys.executable!r},'-c',{code!r}])"
            ),
        ],
        env=env,
    )
    try:
        wait_for_path(ready)
        probe = open(provenance.lock_path(root, "production"), "a+")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()
        assert proc.wait(timeout=5) == 0
        probe = open(provenance.lock_path(root, "production"), "a+")
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()
    finally:
        proc.kill()
        proc.wait()


def test_exec_many_sorts_unique_kinds_and_preserves_two_fds(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, rs, staging = layout
    publish(layout, toolchain)
    qemu_staging = rs / "staging-qemu"
    qemu_staging.mkdir()
    for member in BUNDLE_MEMBERS:
        (qemu_staging / member).write_bytes((staging / member).read_bytes())
    qemu_expected = replace(toolchain, profile="qemu-release")
    publish_generation_atomic(
        qemu_staging,
        rs / "build_qemu_test",
        make_manifest(qemu_staging, "qemu-test", "a" * 64, qemu_expected),
    )
    seen: list[tuple[str, ...]] = []

    def fake_exec(_file: str, argv: list[str]) -> None:
        inheritable = tuple(
            str(fd)
            for fd in range(3, 256)
            if provenance._fd_is_open_and_inheritable(fd)
        )
        seen.append(inheritable)
        raise provenance._ExecIntercept

    monkeypatch.setattr(os, "execvp", fake_exec)
    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "a" * 64)
    monkeypatch.setattr(
        provenance,
        "_current_toolchain",
        lambda _root, kind: (
            replace(toolchain, features=()) if kind == "production" else qemu_expected
        ),
    )
    with pytest.raises(provenance._ExecIntercept):
        locked_exec_many(
            root,
            ("qemu-test", "production"),
            [sys.executable, "-c", "pass"],
        )
    assert len(seen[0]) >= 2


@pytest.mark.parametrize("mismatched_kind", ["production", "qemu-test"])
def test_exec_many_rejects_either_kind_specific_toolchain_mismatch(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    mismatched_kind: str,
) -> None:
    root, rs, staging = layout
    production = replace(toolchain, features=())
    publish(layout, toolchain)
    qemu_staging = rs / "staging-qemu"
    qemu_staging.mkdir()
    for member in BUNDLE_MEMBERS:
        (qemu_staging / member).write_bytes((staging / member).read_bytes())
    publish_generation_atomic(
        qemu_staging,
        rs / "build_qemu_test",
        make_manifest(qemu_staging, "qemu-test", "a" * 64, toolchain),
    )

    def current(_root: Path, kind: str) -> Toolchain:
        expected = production if kind == "production" else toolchain
        if kind == mismatched_kind:
            return replace(expected, profile="current-profile-changed")
        return expected

    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "a" * 64)
    monkeypatch.setattr(provenance, "_current_toolchain", current)
    monkeypatch.setattr(
        provenance.os,
        "execvp",
        lambda *_args: pytest.fail("exec must not run for kind mismatch"),
    )
    with pytest.raises(provenance.InvalidError):
        locked_exec_many(root, ("production", "qemu-test"), ["true"])


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("toolchain", provenance.InvalidError),
        ("digest", provenance.StaleError),
    ],
)
def test_locked_exec_rejects_current_fact_or_source_drift_before_exec(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    error: type[Exception],
) -> None:
    root, _, _ = layout
    publish(layout, toolchain)
    current = replace(toolchain, features=())
    if mode == "toolchain":
        current = replace(current, linker_version="current linker changed")
    monkeypatch.setattr(provenance, "_current_toolchain", lambda _root, _kind: current)
    monkeypatch.setattr(
        provenance,
        "_current_input_digest",
        lambda _root: ("b" if mode == "digest" else "a") * 64,
    )
    monkeypatch.setattr(
        provenance.os,
        "execvp",
        lambda *_args: pytest.fail("exec must not run for drift"),
    )

    with pytest.raises(error):
        provenance.locked_exec(root, "production", ["true"])


def test_publication_uses_digest_generation_and_relative_symlink(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    public, manifest = publish(layout, toolchain)
    expected = Path(".artifacts/prod") / manifest["manifest_sha256"]
    assert public.is_symlink()
    assert Path(os.readlink(public)) == expected
    generation = public.parent / expected
    assert set(path.name for path in generation.iterdir()) == {
        *BUNDLE_MEMBERS,
        MANIFEST_NAME,
    }
    assert (generation / MANIFEST_NAME).read_bytes() == manifest_bytes(manifest)


def test_publication_failure_before_swap_preserves_old_generation(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public, _ = publish(layout, toolchain)
    old_target = os.readlink(public)
    _, _, staging = layout
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"new bytes")
    manifest = manifest_for(staging, toolchain)

    def fail(point: str) -> None:
        if point == "before_link_swap":
            raise OSError("injected")

    monkeypatch.setattr(provenance, "_failure_point", fail)
    with pytest.raises(provenance.InternalError):
        publish_generation_atomic(staging, public, manifest)
    assert public.is_symlink()
    assert os.readlink(public) == old_target


@pytest.mark.parametrize(
    "point",
    [
        "after_generation",
        "after_legacy_backup",
        "before_link_swap",
        "after_link_swap",
        "before_commit",
    ],
)
def test_legacy_directory_migration_rolls_back_on_every_failure(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    _, rs, staging = layout
    public = rs / "build"
    public.mkdir()
    marker = public / "tracked-old"
    marker.write_text("old", encoding="utf-8")
    manifest = manifest_for(staging, toolchain)

    def fail(actual: str) -> None:
        if actual == point:
            raise OSError("injected")

    monkeypatch.setattr(provenance, "_failure_point", fail)
    with pytest.raises(provenance.InternalError):
        publish_generation_atomic(staging, public, manifest)
    assert public.is_dir() and not public.is_symlink()
    assert marker.read_text(encoding="utf-8") == "old"
    assert not list((rs / ".artifacts").glob(".legacy-build-*"))


def test_successful_legacy_migration_removes_task_specific_backup(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    _, rs, _ = layout
    public = rs / "build"
    public.mkdir()
    (public / "tracked-old").write_text("old", encoding="utf-8")
    publish(layout, toolchain)
    assert public.is_symlink()
    assert not list((rs / ".artifacts").glob(".legacy-build-*"))


def test_existing_symlink_update_is_never_lexically_absent(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, rs, staging = layout
    public, _ = publish(layout, toolchain)
    old_target = os.readlink(public)
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"new generation")
    manifest = manifest_for(staging, toolchain)
    expected_targets = {
        old_target,
        str(Path(".artifacts/prod") / manifest["manifest_sha256"]),
    }
    stop = threading.Event()
    absent: list[bool] = []
    observed: set[str] = set()

    def watch() -> None:
        while not stop.is_set():
            if not os.path.lexists(public):
                absent.append(True)
                continue
            try:
                observed.add(os.readlink(public))
            except FileNotFoundError:
                absent.append(True)

    original = provenance._failure_point

    def widen(point: str) -> None:
        if point in {"after_legacy_backup", "before_link_swap", "after_link_swap"}:
            time.sleep(0.04)
        original(point)

    monkeypatch.setattr(provenance, "_failure_point", widen)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        publish_generation_atomic(staging, public, manifest)
    finally:
        stop.set()
        watcher.join(timeout=2)
    assert not watcher.is_alive()
    assert not absent
    assert observed == expected_targets


@pytest.mark.parametrize("point", ["after_link_swap", "before_commit"])
def test_existing_symlink_precommit_failure_atomically_restores_old_link(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    _, _, staging = layout
    public, _ = publish(layout, toolchain)
    old_target = os.readlink(public)
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"replacement")

    def fail(actual: str) -> None:
        if actual == point:
            raise OSError("precommit")

    monkeypatch.setattr(provenance, "_failure_point", fail)
    with pytest.raises(provenance.InternalError):
        publish_generation_atomic(staging, public, manifest_for(staging, toolchain))
    assert public.is_symlink()
    assert os.readlink(public) == old_target


def test_postcommit_failure_returns_success_with_new_durable_link(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, staging = layout
    public, _ = publish(layout, toolchain)
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"replacement")
    manifest = manifest_for(staging, toolchain)

    def fail(point: str) -> None:
        if point == "after_commit":
            raise OSError("postcommit")

    monkeypatch.setattr(provenance, "_failure_point", fail)
    publish_generation_atomic(staging, public, manifest)
    assert os.readlink(public) == str(
        Path(".artifacts/prod") / manifest["manifest_sha256"]
    )


def test_postcommit_rollback_link_fsync_error_does_not_report_failure(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, rs, staging = layout
    public, _ = publish(layout, toolchain)
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"replacement")
    manifest = manifest_for(staging, toolchain)
    committed = False
    real_fsync_dir = provenance._fsync_dir

    def mark(point: str) -> None:
        nonlocal committed
        if point == "after_commit":
            committed = True

    def fail_cleanup_fsync(path: Path) -> None:
        if committed and path == rs:
            raise RuntimeError("postcommit fsync")
        real_fsync_dir(path)

    monkeypatch.setattr(provenance, "_failure_point", mark)
    monkeypatch.setattr(provenance, "_fsync_dir", fail_cleanup_fsync)
    publish_generation_atomic(staging, public, manifest)
    assert os.readlink(public) == str(
        Path(".artifacts/prod") / manifest["manifest_sha256"]
    )


def test_precommit_link_fsync_failure_restores_old_link(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, rs, staging = layout
    public, _ = publish(layout, toolchain)
    old_target = os.readlink(public)
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"replacement")
    armed = False
    real_fsync_dir = provenance._fsync_dir

    def arm(point: str) -> None:
        nonlocal armed
        if point == "after_link_swap":
            armed = True

    def fail_link_fsync(path: Path) -> None:
        if armed and path == rs:
            raise OSError("link fsync")
        real_fsync_dir(path)

    monkeypatch.setattr(provenance, "_failure_point", arm)
    monkeypatch.setattr(provenance, "_fsync_dir", fail_link_fsync)
    with pytest.raises(provenance.InternalError):
        publish_generation_atomic(staging, public, manifest_for(staging, toolchain))
    assert os.readlink(public) == old_target


def test_legacy_cleanup_failure_keeps_recoverable_retired_backup(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, rs, staging = layout
    public = rs / "build"
    public.mkdir()
    (public / "tracked-old").write_text("recover me", encoding="utf-8")

    def fail(point: str) -> None:
        if point == "before_retired_cleanup":
            raise OSError("cleanup")

    monkeypatch.setattr(provenance, "_failure_point", fail)
    publish_generation_atomic(staging, public, manifest_for(staging, toolchain))
    retired = list((rs / ".artifacts").glob(".retired-legacy-build-*"))
    assert public.is_symlink()
    assert len(retired) == 1
    assert (retired[0] / "tracked-old").read_text(encoding="utf-8") == "recover me"


def test_postcommit_legacy_retire_fsync_failure_returns_success(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, rs, staging = layout
    public = rs / "build"
    public.mkdir()
    (public / "tracked-old").write_text("recover me", encoding="utf-8")
    retired = False
    real_replace = provenance.os.replace
    real_fsync_dir = provenance._fsync_dir

    def track_replace(source: Path, destination: Path) -> None:
        nonlocal retired
        real_replace(source, destination)
        if Path(destination).name.startswith(".retired-legacy-"):
            retired = True

    def fail_retire_fsync(path: Path) -> None:
        if retired and path == rs / ".artifacts":
            raise OSError("retire fsync")
        real_fsync_dir(path)

    monkeypatch.setattr(provenance.os, "replace", track_replace)
    monkeypatch.setattr(provenance, "_fsync_dir", fail_retire_fsync)
    publish_generation_atomic(staging, public, manifest_for(staging, toolchain))
    backups = list((rs / ".artifacts").glob(".retired-legacy-build-*"))
    assert public.is_symlink()
    assert len(backups) == 1
    assert (backups[0] / "tracked-old").read_text(encoding="utf-8") == "recover me"


@pytest.mark.parametrize(
    "bad",
    [
        "outside_staging",
        "outside_public",
        "symlink_staging",
        "traversal_manifest",
        "destination_collision",
    ],
)
def test_publication_refuses_unsafe_layout_and_collisions(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
    bad: str,
) -> None:
    _, rs, staging = layout
    public = rs / "build"
    manifest = manifest_for(staging, toolchain)
    if bad == "outside_staging":
        staging = tmp_path / "outside"
        staging.mkdir()
        for member in BUNDLE_MEMBERS:
            (staging / member).write_bytes(b"x")
    elif bad == "outside_public":
        public = tmp_path / "build"
    elif bad == "symlink_staging":
        real = staging
        staging = rs / "linked-staging"
        staging.symlink_to(real.name)
    elif bad == "traversal_manifest":
        manifest["members"][0]["name"] = "../escape"
    else:
        generation = rs / ".artifacts/prod" / manifest["manifest_sha256"]
        generation.mkdir(parents=True)
        (generation / "wrong").write_bytes(b"wrong")

    with pytest.raises((provenance.InvalidError, provenance.InternalError)):
        publish_generation_atomic(staging, public, manifest)


def test_publication_refuses_symlinked_artifact_store(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
) -> None:
    _, rs, staging = layout
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    (rs / ".artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(provenance.InvalidError, match="artifact"):
        publish_generation_atomic(
            staging,
            rs / "build",
            manifest_for(staging, toolchain),
        )
    assert not list(outside.iterdir())


def test_verification_rejects_noncanonical_public_link(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    public, _ = publish(layout, toolchain)
    target = Path(os.readlink(public))
    public.unlink()
    public.symlink_to(target.parent / ".." / target.parent.name / target.name)

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


def test_verification_rejects_real_public_build_directory(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    public, _ = publish(layout, toolchain)
    generation = public.resolve()
    public.unlink()
    shutil.copytree(generation, public)

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


@pytest.mark.parametrize("level", ["artifacts", "kind", "generation"])
def test_verification_rejects_symlinked_generation_ancestry(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    level: str,
) -> None:
    _, rs, _ = layout
    public, _ = publish(layout, toolchain)
    artifacts = rs / ".artifacts"
    kind_root = artifacts / "prod"
    generation = public.resolve()
    selected = {
        "artifacts": artifacts,
        "kind": kind_root,
        "generation": generation,
    }[level]
    outside = rs / f".outside-{level}"
    selected.rename(outside)
    selected.symlink_to(outside, target_is_directory=True)

    assert (
        verify_locked(public, replace(toolchain, features=()), "a" * 64).code
        == EXIT_INVALID
    )


def test_publication_rejects_lexical_path_traversal(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    _, rs, staging = layout
    (rs / "child").mkdir()
    public = rs / "child" / ".." / "build"

    with pytest.raises(provenance.InvalidError, match="public"):
        publish_generation_atomic(
            staging,
            public,
            manifest_for(staging, toolchain),
        )


def test_existing_generation_with_symlink_manifest_is_a_collision(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
) -> None:
    public, manifest = publish(layout, toolchain)
    generation = public.resolve()
    manifest_path = generation / MANIFEST_NAME
    outside = tmp_path / "outside-manifest"
    outside.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(outside)

    with pytest.raises(provenance.InvalidError, match="collid"):
        publish_generation_atomic(layout[2], public, manifest)


def test_same_generation_publication_is_idempotent(
    layout: tuple[Path, Path, Path], toolchain: Toolchain
) -> None:
    public, manifest = publish(layout, toolchain)
    inode = (public / MANIFEST_NAME).stat().st_ino
    publish_generation_atomic(layout[2], public, manifest)
    assert (public / MANIFEST_NAME).stat().st_ino == inode


def test_publication_fsyncs_files_and_generation_parents(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes: list[int] = []
    real_fsync = provenance.os.fsync

    def recording_fsync(fd: int) -> None:
        modes.append(provenance.os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(provenance.os, "fsync", recording_fsync)
    publish(layout, toolchain)

    assert sum(stat.S_ISREG(mode) for mode in modes) >= len(BUNDLE_MEMBERS) + 1
    assert sum(stat.S_ISDIR(mode) for mode in modes) >= 3


def test_concurrent_publishers_serialize_complete_generations(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
) -> None:
    _, rs, staging_a = layout
    staging_b = rs / "staging-b"
    staging_b.mkdir()
    for member in BUNDLE_MEMBERS:
        (staging_b / member).write_bytes(b"B:" + member.encode())
    manifests = (
        manifest_for(staging_a, toolchain, digest="a" * 64),
        manifest_for(staging_b, toolchain, digest="b" * 64),
    )
    commands = []
    for staging, manifest in zip((staging_a, staging_b), manifests, strict=True):
        source = (
            "import json;"
            "from pathlib import Path;"
            "from artifact_provenance import publish_generation_atomic;"
            f"publish_generation_atomic(Path({str(staging)!r}),"
            f"Path({str(rs / 'build')!r}),json.loads({json.dumps(manifest)!r}))"
        )
        commands.append([sys.executable, "-c", source])
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(TOOLS)
    processes = [
        subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for command in commands
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)

    generations = rs / ".artifacts/prod"
    assert {
        path.name for path in generations.iterdir() if not path.name.startswith(".")
    } == {manifest["manifest_sha256"] for manifest in manifests}
    active = json.loads((rs / "build" / MANIFEST_NAME).read_bytes())
    assert active in manifests
    assert verify_locked(
        rs / "build",
        Toolchain(
            **{
                **active["toolchain"],
                "features": tuple(active["toolchain"]["features"]),
            }
        ),
        active["input_digest"],
    ).ok


def test_lock_aware_reader_and_publisher_see_only_complete_generations(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
) -> None:
    root, rs, staging = layout
    public, _ = publish(layout, toolchain)
    old_bytes = (public / BUNDLE_MEMBERS[0]).read_bytes()
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"new complete generation")
    manifest = manifest_for(staging, toolchain)
    ready = tmp_path / "reader-ready"
    result = tmp_path / "reader-result"
    reader_source = "\n".join(
        [
            "from pathlib import Path",
            "import time",
            "from artifact_provenance import shared_bundle",
            f"root=Path({str(root)!r})",
            "with shared_bundle(root,'production') as bundle:",
            f"    first=(bundle/{BUNDLE_MEMBERS[0]!r}).read_bytes()",
            f"    Path({str(ready)!r}).write_text('ready')",
            "    time.sleep(.3)",
            f"    second=(bundle/{BUNDLE_MEMBERS[0]!r}).read_bytes()",
            f"    Path({str(result)!r}).write_bytes(first+b'\\0'+second)",
        ]
    )
    publisher_source = (
        "import json;"
        "from pathlib import Path;"
        "from artifact_provenance import publish_generation_atomic;"
        f"publish_generation_atomic(Path({str(staging)!r}),"
        f"Path({str(public)!r}),json.loads({json.dumps(manifest)!r}))"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(TOOLS)
    reader = subprocess.Popen(
        [sys.executable, "-c", reader_source],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    publisher: subprocess.Popen[str] | None = None
    try:
        wait_for_path(ready)
        publisher = subprocess.Popen(
            [sys.executable, "-c", publisher_source],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.05)
        assert publisher.poll() is None
        reader_stdout, reader_stderr = reader.communicate(timeout=5)
        assert reader.returncode == 0, (reader_stdout, reader_stderr)
        publisher_stdout, publisher_stderr = publisher.communicate(timeout=5)
        assert publisher.returncode == 0, (publisher_stdout, publisher_stderr)
    finally:
        reader.kill()
        reader.wait()
        if publisher is not None:
            publisher.kill()
            publisher.wait()
    first, second = result.read_bytes().split(b"\0", 1)
    new_bytes = (public / BUNDLE_MEMBERS[0]).read_bytes()
    assert first == second == old_bytes
    assert new_bytes == b"new complete generation"
    with shared_bundle(root, "production") as bundle:
        assert (bundle / BUNDLE_MEMBERS[0]).read_bytes() == new_bytes


def test_reader_observes_old_or_new_never_absent_during_swap(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, staging = layout
    public, _ = publish(layout, toolchain)
    old = (public / BUNDLE_MEMBERS[0]).read_bytes()
    (staging / BUNDLE_MEMBERS[0]).write_bytes(b"new generation")
    manifest = manifest_for(staging, toolchain)
    observed: list[bytes] = []
    original = provenance._failure_point

    def observe(point: str) -> None:
        if point == "after_link_swap":
            observed.append((public / BUNDLE_MEMBERS[0]).read_bytes())
        original(point)

    monkeypatch.setattr(provenance, "_failure_point", observe)
    publish_generation_atomic(staging, public, manifest)
    new = (public / BUNDLE_MEMBERS[0]).read_bytes()
    assert {old, *observed, new} == {old, new}
    with shared_bundle(root, "production") as held:
        assert held == public
        assert held.exists()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (provenance.MissingError("missing"), EXIT_MISSING),
        (provenance.StaleError("stale"), EXIT_STALE),
        (provenance.InvalidError("invalid"), EXIT_INVALID),
        (OSError("boom"), EXIT_INTERNAL),
    ],
)
def test_cli_boundary_preserves_exit_contract(
    exc: Exception,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def injected(_root: Path, _kind: str, _argv: list[str]) -> None:
        raise exc

    assert (
        provenance.main(
            ["exec", "--kind", "production", "--", "true"],
            exec_one=injected,
        )
        == expected
    )
    assert capsys.readouterr().err


def test_cli_rejects_empty_commands_and_duplicate_kinds() -> None:
    assert provenance.main(["exec", "--kind", "production"]) == EXIT_INVALID
    assert (
        provenance.main(
            [
                "exec-many",
                "--kind",
                "production",
                "--kind",
                "production",
                "--",
                "true",
            ]
        )
        == EXIT_INVALID
    )


def test_cli_classifies_missing_manifest_as_missing(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = layout
    public, _ = publish(layout, toolchain)
    (public / MANIFEST_NAME).unlink()
    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "a" * 64)
    monkeypatch.setattr(
        provenance,
        "_current_toolchain",
        lambda _root, _kind: replace(toolchain, features=()),
    )

    assert (
        provenance.main(
            [
                "--repo-root",
                str(root),
                "exec",
                "--kind",
                "production",
                "--",
                "true",
            ]
        )
        == EXIT_MISSING
    )


@pytest.mark.parametrize("kind", ["production", "qemu-test"])
def test_current_toolchain_runs_kind_specific_snapshot_check(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    kind: str,
) -> None:
    root, rs, _ = layout
    checker = rs / "tools/build_image.sh"
    checker.parent.mkdir()
    checker.write_text("#!/bin/sh\n", encoding="utf-8")
    checker.chmod(0o755)
    calls: list[tuple[list[str], dict]] = []

    expected = replace(toolchain, features=()) if kind == "production" else toolchain

    def kind_runner(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=canonical_toolchain_json(expected),
            stderr="",
        )

    assert provenance._current_toolchain(root, kind, runner=kind_runner) == expected
    assert calls == [
        (
            [str(checker), "--check", "--kind", kind],
            {
                "cwd": root,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "check": False,
            },
        )
    ]


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("missing", provenance.InvalidError),
        ("nonexecutable", provenance.InvalidError),
        ("noncanonical", provenance.InvalidError),
        ("malformed", provenance.InvalidError),
        ("policy", provenance.InvalidError),
        ("failed", provenance.InvalidError),
        ("runner_error", provenance.InternalError),
    ],
)
def test_current_toolchain_error_classification(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    mode: str,
    error: type[Exception],
) -> None:
    root, rs, _ = layout
    checker = rs / "tools/build_image.sh"
    if mode != "missing":
        checker.parent.mkdir()
        checker.write_text("#!/bin/sh\n", encoding="utf-8")
        checker.chmod(0o644 if mode == "nonexecutable" else 0o755)

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if mode == "runner_error":
            raise OSError("exec failed")
        output = canonical_toolchain_json(toolchain)
        if mode == "noncanonical":
            output = json.dumps(json.loads(output), indent=2) + "\n"
        elif mode == "malformed":
            output = "{"
        elif mode == "policy":
            value = json.loads(output)
            value["features"] = ["NOT-CANONICAL"]
            output = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        return subprocess.CompletedProcess(
            argv,
            7 if mode == "failed" else 0,
            stdout=output,
            stderr="check failed",
        )

    with pytest.raises(error):
        provenance._current_toolchain(root, "production", runner=runner)


def test_verify_cli_uses_live_digest_and_current_toolchain(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = layout
    public, _ = publish(layout, toolchain)
    expected = replace(toolchain, features=())
    calls: list[Path] = []
    monkeypatch.setattr(
        provenance,
        "_current_toolchain",
        lambda actual, actual_kind: (
            expected if actual == root and actual_kind == "production" else None
        ),
    )

    def digest(actual: Path) -> str:
        calls.append(actual)
        return "a" * 64

    monkeypatch.setattr(provenance, "_current_input_digest", digest)
    assert (
        provenance.main(
            [
                "--repo-root",
                str(root),
                "verify",
                "--kind",
                "production",
                str(public),
            ]
        )
        == 0
    )
    assert calls == [root]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_id", "sha256:" + "9" * 64),
        ("recipe_sha256", "8" * 64),
        ("image_tag", "other:tag"),
        ("idf_commit", "7" * 40),
        ("rustc_verbose", "rustc current-other"),
        ("target", "other-target"),
        ("linker_version", "other linker"),
        ("esptool_version", "other esptool"),
        ("component_lock_sha256", "6" * 64),
        ("profile", "debug"),
        ("features", ("other",)),
    ],
)
def test_verify_cli_rejects_each_changed_current_toolchain_fact(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    root, _, _ = layout
    public, _ = publish(layout, toolchain)
    current = replace(replace(toolchain, features=()), **{field: value})
    monkeypatch.setattr(provenance, "_current_toolchain", lambda _root, _kind: current)
    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "a" * 64)

    assert (
        provenance.main(
            [
                "--repo-root",
                str(root),
                "verify",
                "--kind",
                "production",
                str(public),
            ]
        )
        == EXIT_INVALID
    )


def test_verify_cli_rejects_changed_live_source_digest(
    layout: tuple[Path, Path, Path],
    toolchain: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = layout
    public, _ = publish(layout, toolchain)
    monkeypatch.setattr(
        provenance,
        "_current_toolchain",
        lambda _root, _kind: replace(toolchain, features=()),
    )
    monkeypatch.setattr(provenance, "_current_input_digest", lambda _root: "b" * 64)

    assert (
        provenance.main(
            [
                "--repo-root",
                str(root),
                "verify",
                "--kind",
                "production",
                str(public),
            ]
        )
        == EXIT_STALE
    )


def test_module_compiles_as_standalone_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(MODULE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, result.stderr
