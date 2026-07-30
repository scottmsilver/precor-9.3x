from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
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
    env["ESP32TAP_INPUT_DIGEST"] = "a" * 64
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path;"
                "from artifact_provenance import locked_exec;"
                f"locked_exec(Path({str(root)!r}),'production',"
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
    publish_generation_atomic(
        qemu_staging,
        rs / "build_qemu_test",
        manifest_for(qemu_staging, toolchain, kind="qemu-test"),
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
    monkeypatch.setenv("ESP32TAP_INPUT_DIGEST", "a" * 64)
    with pytest.raises(provenance._ExecIntercept):
        locked_exec_many(
            root,
            ("qemu-test", "production"),
            [sys.executable, "-c", "pass"],
        )
    assert len(seen[0]) >= 2


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
        "after_link_fsync",
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
    monkeypatch.setenv("ESP32TAP_INPUT_DIGEST", "a" * 64)

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


def test_module_compiles_as_standalone_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(MODULE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, result.stderr
