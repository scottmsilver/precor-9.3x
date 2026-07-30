#!/usr/bin/env bash
# Build production and QEMU flash bundles from one immutable, gated snapshot.
#
# The shell is intentionally only a stable entry point. Python owns the secure
# worktree lock, snapshot lifecycle, container invocation, provenance manifest,
# and atomic publication so every failure follows one cleanup path.
set -euo pipefail

case "${ONLY:-both}" in
    prod|qemu|both) ;;
    *)
        echo "build.sh: ONLY must be prod, qemu, or both" >&2
        exit 2
        ;;
esac
if [ -n "${PROFILE:-}" ]; then
    echo "build.sh: PROFILE is fixed to release and must not be overridden" >&2
    exit 2
fi
if [ -n "${NET_FEATURE:-}" ]; then
    echo "build.sh: NET_FEATURE is fixed by artifact kind and must not be overridden" >&2
    exit 2
fi

HERE="$(cd "$(dirname "$0")" && pwd -P)"
exec python3 - "$HERE" "${ONLY:-both}" <<'PY'
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

tools = Path(sys.argv[1]).resolve(strict=True)
only = sys.argv[2]
esp32_rs = tools.parent
repo_root = Path(
    subprocess.run(
        ["git", "-C", str(esp32_rs), "rev-parse", "--show-toplevel"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
).resolve(strict=True)
sys.path.insert(0, str(tools))

from artifact_inputs import (  # noqa: E402
    _read_input,
    _record,
    create_snapshot,
    remove_snapshot,
    target_cache,
    verify_gate_input_completeness,
    working_digest,
)
from artifact_provenance import (  # noqa: E402
    BUNDLE_MEMBERS,
    Toolchain,
    _current_toolchain,
    lock_path,
    make_manifest,
    publish_generation_atomic,
)


class BuildError(RuntimeError):
    pass


class BuildCancelled(SystemExit):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(128 + signum)


pending_signal: int | None = None
cleanup_in_progress = False


def request_cancellation(signum: int, _frame: object) -> None:
    global pending_signal
    if pending_signal is None:
        pending_signal = signum
    if not cleanup_in_progress:
        raise BuildCancelled(pending_signal)


for watched_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(watched_signal, request_cancellation)


def acquire_worktree_lock() -> None:
    """Securely open the physical-worktree lock, then retain it on fd 9."""

    path = lock_path(repo_root, "production")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        lock_fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BuildError(f"cannot open build lock {path}: {exc}") from exc
    try:
        opened = os.fstat(lock_fd)
        lexical = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(lexical.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or (opened.st_dev, opened.st_ino)
            != (lexical.st_dev, lexical.st_ino)
        ):
            raise BuildError(
                f"build lock must be one owned regular file at its exact path: {path}"
            )
        if stat.S_IMODE(opened.st_mode) != 0o600:
            os.fchmod(lock_fd, 0o600)
        os.dup2(lock_fd, 9, inheritable=True)
    finally:
        if lock_fd != 9:
            os.close(lock_fd)

    wait_text = os.environ.get("BUILD_LOCK_WAIT", "1800")
    try:
        wait_seconds = float(wait_text)
    except ValueError as exc:
        raise BuildError("BUILD_LOCK_WAIT must be a positive number") from exc
    if wait_seconds <= 0:
        raise BuildError("BUILD_LOCK_WAIT must be a positive number")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise BuildError(
                    f"could not acquire {path} within {wait_seconds:g}s; "
                    "a QEMU reader or another build still holds it"
                )
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    os.set_inheritable(9, True)
    locked = os.fstat(9)
    current = path.lstat()
    if (
        locked.st_nlink != 1
        or locked.st_uid != os.geteuid()
        or stat.S_IMODE(locked.st_mode) != 0o600
        or (locked.st_dev, locked.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise BuildError(f"build lock changed while it was acquired: {path}")


def requested_kinds() -> tuple[tuple[str, str], ...]:
    selected: list[tuple[str, str]] = []
    if only != "qemu":
        selected.append(("production", "prod"))
    if only != "prod":
        selected.append(("qemu-test", "qemu"))
    return tuple(selected)


def secure_cache_dir(path: Path) -> Path:
    """Create or validate one owned, non-symlink physical cache directory."""

    absolute = path.absolute()
    if absolute == Path("/tmp") or Path("/tmp") not in absolute.parents:
        raise BuildError(f"cache path is outside the fixed /tmp namespace: {absolute}")
    try:
        absolute.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise BuildError(f"cannot inspect cache path {absolute}: {exc}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or absolute.resolve(strict=True) != absolute
    ):
        raise BuildError(
            f"cache path must be an owned physical directory: {absolute}"
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        absolute.chmod(0o700)
    return absolute


def sealed_snapshot_digest(snapshot_root: Path, paths: tuple[str, ...]) -> str:
    """Rehash snapshot-local bytes and modes without consulting live Git."""

    digest = hashlib.sha256()
    selected = frozenset(paths)
    for relative in paths:
        kind, content, permissions = _read_input(
            snapshot_root, relative, selected
        )
        _record(digest, relative, kind, permissions, content)
    return digest.hexdigest()


def cleanup_staging(path: Path) -> None:
    """Remove only this invocation's direct-child staging directory."""

    absolute = path.absolute()
    if (
        absolute.parent.resolve(strict=True) != esp32_rs
        or not absolute.name.startswith(".snapshot-build-")
        or not os.path.lexists(absolute)
    ):
        return
    if absolute.is_symlink() or not absolute.is_dir():
        raise BuildError(f"refusing unsafe staging cleanup: {absolute}")
    for directory, dirnames, _ in os.walk(
        absolute, topdown=False, followlinks=False
    ):
        base = Path(directory)
        for name in dirnames:
            child = base / name
            if not child.is_symlink():
                child.chmod(stat.S_IMODE(child.stat().st_mode) | stat.S_IWUSR)
        base.chmod(stat.S_IMODE(base.stat().st_mode) | stat.S_IWUSR)
    shutil.rmtree(absolute)


def live_digest_without_staging_sdkconfigs(
    stagings: list[Path], task_root: Path
) -> str:
    """Hide the one output name that the input policy conservatively selects."""

    held: list[tuple[Path, Path]] = []
    try:
        for index, staging in enumerate(stagings):
            source = staging / "sdkconfig"
            destination = task_root / f"output-sdkconfig-{index}"
            os.rename(source, destination)
            held.append((source, destination))
        return working_digest(repo_root)
    finally:
        for source, destination in reversed(held):
            if os.path.lexists(destination):
                os.rename(destination, source)


def run_docker(
    snapshot_root: Path,
    staging: Path,
    target: Path,
    cargo_cache: Path,
    kind: str,
    cache_kind: str,
    toolchain: Toolchain,
) -> None:
    features = () if kind == "production" else ("qemu-test", "net", "ble")
    feature_text = ",".join(features)
    command = r'''
set -euo pipefail
source /opt/rust/esp-export.sh >/dev/null 2>&1
source /opt/esp/idf/export.sh >/dev/null 2>&1
export PATH="$PATH:$(dirname "$(ls /opt/esp/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa | head -1)")"

RS_DIR=/project/hardware/Esp32Tap/firmware/esp32_rs
CRATE="$RS_DIR/esp32tap"
SDK="$RS_DIR/sdkconfig.defaults"
if [ "$ARTIFACT_KIND" = qemu-test ]; then
    SDK="$SDK;$RS_DIR/sdkconfig.defaults.qemu"
fi

args=()
if [ -n "$BUILD_FEATURES" ]; then
    args=(--features "$BUILD_FEATURES")
fi
echo "== cargo release build: $ARTIFACT_KIND =="
ESP_IDF_SDKCONFIG_DEFAULTS="$SDK" \
    cargo +esp build --manifest-path "$CRATE/Cargo.toml" --release "${args[@]}" 2>&1 \
    | grep -vE "^[[:space:]]+(Compiling|Downloaded|Checking) " | tail -80

T="$CARGO_TARGET_DIR/xtensa-esp32s3-espidf/release"
sdk="$(find "$CARGO_TARGET_DIR" -type f -name sdkconfig -path "*esp-idf-sys*" -print -quit)"
[ -n "$sdk" ] || { echo "FATAL: generated sdkconfig not found" >&2; exit 1; }
FLASH_SIZE="$(grep -E '^CONFIG_ESPTOOLPY_FLASHSIZE=' "$sdk" | head -1 | cut -d\" -f2)"
[ -n "$FLASH_SIZE" ] || { echo "FATAL: flash size missing from sdkconfig" >&2; exit 1; }

cp "$T/bootloader.bin" /output/bootloader.bin
cp "$T/partition-table.bin" /output/partition-table.bin
python -m esptool --chip esp32s3 elf2image \
    --flash_mode dio --flash_freq 80m --flash_size "$FLASH_SIZE" \
    -o /output/esp32tap.bin "$T/esp32tap"
cat > /output/flash_args <<EOF
--flash_mode dio --flash_freq 80m --flash_size $FLASH_SIZE
0x0 bootloader.bin
0x8000 partition-table.bin
0x10000 esp32tap.bin
EOF
python -m esptool --chip esp32s3 image_info --version 2 /output/esp32tap.bin 2>/dev/null \
    | grep -qi "^Flash size: *$FLASH_SIZE$" \
    || { echo "FATAL: image header flash size mismatch" >&2; exit 1; }

grep -q "CONFIG_ESP_TASK_WDT_PANIC=y" "$sdk"
grep -q "CONFIG_FREERTOS_HZ=1000" "$sdk"
qemu_flag=()
if [ "$ARTIFACT_KIND" = qemu-test ]; then qemu_flag=(--allow-qemu); fi
python3 "$RS_DIR/tools/check_sdkconfig.py" "$sdk" \
    --label "$ARTIFACT_KIND" "${qemu_flag[@]}"
cp "$sdk" /output/sdkconfig

python3 - /output <<'PYFIT'
import pathlib, struct, sys
d = pathlib.Path(sys.argv[1])
table = d.joinpath("partition-table.bin").read_bytes()
factory = None
for index in range(0, len(table), 32):
    entry = table[index:index + 32]
    if entry[:2] != b"\xaa\x50":
        continue
    _, partition_type, _, _, size = struct.unpack("<HBBII", entry[:12])
    if partition_type == 0:
        factory = size
        break
if factory is None:
    raise SystemExit("FATAL: no app partition")
image = d.joinpath("esp32tap.bin").stat().st_size
print(f"app image: {image} bytes / {factory} factory partition ({image * 100 // factory}%)")
if image > factory:
    raise SystemExit(f"FATAL: image does not fit factory partition ({image} > {factory})")
PYFIT
'''
    arguments = [
        "docker", "run",
        "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{snapshot_root}:/project:ro",
        "-v", f"{staging}:/output",
        "-v", f"{target}:/target/{cache_kind}",
        "-v", f"{cargo_cache}:/cargo",
        "-e", "CARGO_HOME=/cargo",
        "-e", f"CARGO_TARGET_DIR=/target/{cache_kind}",
        "-e",
        "CARGO_WORKSPACE_DIR=/project/hardware/Esp32Tap/firmware/"
        "esp32_rs/esp32tap",
        "-e", "PROFILE=release",
        "-e", f"ARTIFACT_KIND={kind}",
        "-e", f"BUILD_FEATURES={feature_text}",
        "-w", "/project/hardware/Esp32Tap/firmware/esp32_rs/esp32tap",
        "--entrypoint", "bash",
        toolchain.image_id,
        "-lc", command,
    ]
    timeout_text = os.environ.get("ESP32TAP_BUILD_TIMEOUT", "3600")
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise BuildError("ESP32TAP_BUILD_TIMEOUT must be a positive number") from exc
    if timeout <= 0:
        raise BuildError("ESP32TAP_BUILD_TIMEOUT must be a positive number")
    process = subprocess.Popen(arguments, start_new_session=True)
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    if returncode != 0:
        raise BuildError(f"Docker {kind} build failed with status {returncode}")


import contextlib  # kept after the embedded container script for readability


def main() -> None:
    global cleanup_in_progress
    acquire_worktree_lock()
    kinds = requested_kinds()
    cache_root = target_cache(repo_root, "prod").parent
    secure_cache_dir(cache_root)
    task_root: Path | None = None
    snapshot = None
    stagings: list[Path] = []
    try:
        task_root = Path(
            tempfile.mkdtemp(prefix="esp32tap-snapshot-build.", dir="/tmp")
        )
        snapshot = create_snapshot(repo_root, task_root / "source", cache_root)
        print(f"== immutable source {snapshot.digest} ==")
        sealed_baseline = sealed_snapshot_digest(snapshot.root, snapshot.paths)

        # Recipe/toolchain validation deliberately precedes every host gate.
        toolchains = {
            kind: _current_toolchain(snapshot.root, kind)
            for kind, _ in kinds
        }
        verify_gate_input_completeness(snapshot.root)
        if sealed_snapshot_digest(snapshot.root, snapshot.paths) != sealed_baseline:
            raise BuildError("sealed source snapshot changed while gates were running")

        cargo_cache = secure_cache_dir(
            Path("/tmp") / f"esp32tap-cargo-{snapshot.worktree_key}"
        )
        manifests: dict[str, dict] = {}
        for kind, cache_kind in kinds:
            secure_cache_dir(cache_root)
            target = secure_cache_dir(target_cache(repo_root, cache_kind))
            staging = esp32_rs / (
                f".snapshot-build-{os.getpid()}-{uuid.uuid4().hex}-{cache_kind}"
            )
            staging.mkdir(mode=0o700)
            stagings.append(staging)
            run_docker(
                snapshot.root,
                staging,
                target,
                cargo_cache,
                kind,
                cache_kind,
                toolchains[kind],
            )
            names = {entry.name for entry in os.scandir(staging)}
            if names != set(BUNDLE_MEMBERS):
                raise BuildError(
                    f"{kind} build emitted {sorted(names)}, expected "
                    f"{list(BUNDLE_MEMBERS)}"
                )
            manifests[kind] = make_manifest(
                staging, kind, snapshot.digest, toolchains[kind]
            )

        if sealed_snapshot_digest(snapshot.root, snapshot.paths) != sealed_baseline:
            raise BuildError("sealed source snapshot changed while builds were running")
        if snapshot.digest != live_digest_without_staging_sdkconfigs(
            stagings, task_root
        ):
            raise BuildError(
                "source inputs changed while the snapshot build was running; "
                "nothing was published"
            )

        # Each public link is an independent atomic commit. If the process
        # crashes between kinds, readers can see a valid old/new mix; neither
        # link is ever absent or partial, and current verification detects it.
        watched = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
        try:
            for (kind, _), staging in zip(kinds, stagings, strict=True):
                public_name = (
                    "build" if kind == "production" else "build_qemu_test"
                )
                publish_generation_atomic(
                    staging,
                    esp32_rs / public_name,
                    manifests[kind],
                    lock_fd=9,
                )
                identity = manifests[kind]["manifest_sha256"]
                print(f"published {kind}: {identity}")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if only == "both":
            print("ONLY=both published both manifests")
    finally:
        cleanup_in_progress = True
        for staging in reversed(stagings):
            cleanup_staging(staging)
        if snapshot is not None and task_root is not None:
            remove_snapshot(snapshot.root, task_root)
        if task_root is not None:
            try:
                task_root.rmdir()
            except FileNotFoundError:
                pass
        if pending_signal is not None:
            raise BuildCancelled(pending_signal)


try:
    main()
except BuildError as exc:
    print(f"build.sh: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
