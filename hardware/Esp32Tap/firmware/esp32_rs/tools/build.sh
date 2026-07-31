#!/usr/bin/env bash
# Build production, QEMU, or DevKit flash bundles from one immutable snapshot.
#
# The shell is intentionally only a stable entry point. Python owns the secure
# worktree lock, snapshot lifecycle, container invocation, provenance manifest,
# and atomic publication so every failure follows one cleanup path.
set -euo pipefail

case "${ONLY:-both}" in
    prod|qemu|devkit|both) ;;
    *)
        echo "build.sh: ONLY must be prod, qemu, devkit, or both" >&2
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
export PYTHONDONTWRITEBYTECODE=1
exec python3 - "$HERE" "${ONLY:-both}" <<'PY'
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import struct
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
    verify_snapshot_matches_commit,
    verify_gate_input_completeness,
    working_digest,
)
from artifact_provenance import (  # noqa: E402
    BUNDLE_MEMBERS,
    DEVKIT_REQUIRED_SERIAL_DEVICE,
    Toolchain,
    _current_toolchain,
    lock_path,
    make_manifest,
    make_recipe_id,
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
cancellation_deferred = False


def request_cancellation(signum: int, _frame: object) -> None:
    global pending_signal
    if pending_signal is not None:
        return
    pending_signal = signum
    if not cleanup_in_progress and not cancellation_deferred:
        raise BuildCancelled(pending_signal)


def raise_pending_cancellation() -> None:
    if pending_signal is not None:
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
    if only == "devkit":
        return (("devkit-bringup", "devkit"),)
    selected: list[tuple[str, str]] = []
    if only != "qemu":
        selected.append(("production", "prod"))
    if only != "prod":
        selected.append(("qemu-test", "qemu"))
    return tuple(selected)


def clean_git_commit() -> str:
    """Return one canonical commit only when the live worktree is pristine."""

    status = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout
    if status:
        raise BuildError("devkit-bringup requires a clean Git worktree")
    commit = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise BuildError("devkit-bringup requires a canonical 40-hex Git commit")
    return commit


def validate_snapshot_identity(snapshot: object) -> None:
    """Fail closed unless the builder received artifact_inputs' sealed snapshot."""

    marker = snapshot.root / ".esp32tap-snapshot-v1"
    try:
        info = marker.lstat()
        payload = marker.read_text(encoding="ascii")
    except OSError as exc:
        raise BuildError(f"immutable snapshot marker is unavailable: {exc}") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o444
        or f"digest={snapshot.digest}\n" not in payload
        or f"worktree_key={snapshot.worktree_key}\n" not in payload
    ):
        raise BuildError("immutable snapshot marker is invalid or incomplete")


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


def cleanup_task_root(path: Path) -> None:
    """Remove only this invocation's owned direct-child /tmp directory."""

    absolute = path.absolute()
    if (
        absolute.parent != Path("/tmp")
        or re.fullmatch(
            r"esp32tap-snapshot-build\.[-A-Za-z0-9_]+", absolute.name
        )
        is None
        or not os.path.lexists(absolute)
    ):
        return
    info = absolute.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or absolute.resolve(strict=True) != absolute
    ):
        raise BuildError(f"refusing unsafe task-root cleanup: {absolute}")

    def raise_walk_error(error: OSError) -> None:
        raise error

    for directory, dirnames, _ in os.walk(
        absolute, topdown=False, onerror=raise_walk_error, followlinks=False
    ):
        base = Path(directory)
        for name in dirnames:
            child = base / name
            if not child.is_symlink():
                child_info = child.lstat()
                if (
                    not stat.S_ISDIR(child_info.st_mode)
                    or child_info.st_uid != os.geteuid()
                ):
                    raise BuildError(
                        f"refusing unsafe task-root member cleanup: {child}"
                    )
                child.chmod(stat.S_IMODE(child_info.st_mode) | stat.S_IWUSR)
        base_info = base.lstat()
        if (
            not stat.S_ISDIR(base_info.st_mode)
            or base_info.st_uid != os.geteuid()
        ):
            raise BuildError(f"refusing unsafe task-root directory cleanup: {base}")
        base.chmod(stat.S_IMODE(base_info.st_mode) | stat.S_IWUSR)
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
            held.append((source, destination))
            try:
                os.rename(source, destination)
            except BaseException:
                held.pop()
                raise
        return working_digest(repo_root)
    finally:
        for source, destination in reversed(held):
            if os.path.lexists(destination):
                os.rename(destination, source)


def clean_commit_without_stagings(stagings: list[Path], task_root: Path) -> str:
    """Hide owned build outputs while checking the complete live worktree."""

    held: list[tuple[Path, Path]] = []
    try:
        for index, source in enumerate(stagings):
            destination = task_root / f"status-staging-{index}"
            held.append((source, destination))
            try:
                os.rename(source, destination)
            except BaseException:
                held.pop()
                raise
        return clean_git_commit()
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
    source_digest: str,
    recipe_id: str | None,
    git_commit: str | None,
) -> None:
    global cancellation_deferred
    features = (
        ("qemu-test", "net", "ble") if kind == "qemu-test" else ()
    )
    feature_text = ",".join(features)
    command = r'''
set -euo pipefail
source /opt/rust/esp-export.sh >/dev/null 2>&1
source /opt/esp/idf/export.sh >/dev/null 2>&1
export PATH="$PATH:$(dirname "$(ls /opt/esp/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa | head -1)")"

RS_DIR=/project/hardware/Esp32Tap/firmware/esp32_rs
CRATE="$RS_DIR/esp32tap"
SDK="$RS_DIR/sdkconfig.defaults"
APP_NAME=esp32tap
if [ "$ARTIFACT_KIND" = qemu-test ]; then
    SDK="$SDK;$RS_DIR/sdkconfig.defaults.qemu"
fi
if [ "$ARTIFACT_KIND" = devkit-bringup ]; then
    [ -f /project/.esp32tap-snapshot-v1 ] || {
        echo "FATAL: direct DevKit build requires a valid immutable snapshot" >&2
        exit 1
    }
    grep -qx "digest=$SOURCE_INPUT_DIGEST" /project/.esp32tap-snapshot-v1 || {
        echo "FATAL: immutable snapshot digest mismatch" >&2
        exit 1
    }
    DEVKIT_BUILD_ROOT=/tmp/esp32tap-devkit-source
    mkdir "$DEVKIT_BUILD_ROOT"
    cp -a "$RS_DIR/devkit_bringup" "$DEVKIT_BUILD_ROOT/devkit_bringup"
    cp -a "$RS_DIR/bringup_core" "$DEVKIT_BUILD_ROOT/bringup_core"
    chmod -R u+w "$DEVKIT_BUILD_ROOT"
    CRATE="$DEVKIT_BUILD_ROOT/devkit_bringup"
    cp -f "$RS_DIR/esp32tap/Cargo.lock" "$CRATE/Cargo.lock"
    chmod u+w "$CRATE/Cargo.lock"
    SDK="$RS_DIR/sdkconfig.defaults.devkit"
    APP_NAME=devkit_bringup
fi

args=()
if [ -n "$BUILD_FEATURES" ]; then
    args=(--features "$BUILD_FEATURES")
fi
echo "== cargo release build: $ARTIFACT_KIND =="
metadata="$(mktemp /tmp/esp32tap-cargo-metadata.XXXXXX)"
messages="$(mktemp /tmp/esp32tap-cargo-messages.XXXXXX)"
if [ "$ARTIFACT_KIND" = devkit-bringup ]; then
    cargo +esp metadata --manifest-path "$CRATE/Cargo.toml" --format-version=1 >"$metadata"
    python3 - "$RS_DIR/esp32tap/Cargo.lock" "$CRATE/Cargo.lock" <<'PYLOCK'
import sys
import tomllib
from pathlib import Path

seed = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
derived = tomllib.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

def external_identity(package):
    source = package.get("source")
    if source is None:
        return None
    return (
        package.get("name"),
        package.get("version"),
        source,
        package.get("checksum"),
    )

seed_external = {
    identity
    for package in seed.get("package", [])
    if (identity := external_identity(package)) is not None
}
derived_packages = derived.get("package", [])
if not isinstance(derived_packages, list):
    raise SystemExit("FATAL: derived DevKit Cargo lock has invalid package schema")
local_names = {
    package.get("name")
    for package in derived_packages
    if external_identity(package) is None
}
if local_names != {"bringup_core", "devkit_bringup"}:
    raise SystemExit("FATAL: derived DevKit Cargo lock has unexpected local packages")
for package in derived_packages:
    identity = external_identity(package)
    if identity is not None and identity not in seed_external:
        raise SystemExit(
            "FATAL: derived DevKit Cargo lock contains an unpinned external package"
        )
PYLOCK
else
    cargo +esp metadata --manifest-path "$CRATE/Cargo.toml" --format-version=1 --locked \
        --filter-platform xtensa-esp32s3-espidf >"$metadata"
fi
esp_idf_sys_package="$(
python3 - "$metadata" <<'PYMETA'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.stat().st_size > 16 * 1024 * 1024:
    raise SystemExit("FATAL: cargo metadata exceeds 16 MiB")
value = json.loads(path.read_text(encoding="utf-8"))
packages = [
    package.get("id")
    for package in value.get("packages", [])
    if isinstance(package, dict) and package.get("name") == "esp-idf-sys"
]
if len(packages) != 1 or not isinstance(packages[0], str):
    raise SystemExit("FATAL: cargo metadata does not identify exactly one esp-idf-sys")
print(packages[0])
PYMETA
)"
set +e
ESP_IDF_SDKCONFIG_DEFAULTS="$SDK" \
    ESP32TAP_RECIPE_ID="$ESP32TAP_RECIPE_ID" \
    ESP32TAP_GIT_COMMIT="$ESP32TAP_GIT_COMMIT" \
    cargo +esp build --manifest-path "$CRATE/Cargo.toml" --release \
    --message-format=json-render-diagnostics "${args[@]}" >"$messages"
cargo_status=$?
set -e

T="$CARGO_TARGET_DIR/xtensa-esp32s3-espidf/release"
sdk="$(
python3 - "$messages" "$esp_idf_sys_package" "$CARGO_TARGET_DIR" \
    "$cargo_status" <<'PYSDK'
import json
import os
import re
import stat
import sys
from pathlib import Path

messages_path = Path(sys.argv[1])
expected_package = sys.argv[2]
target = Path(sys.argv[3]).absolute()
try:
    cargo_status = int(sys.argv[4])
except (IndexError, ValueError) as exc:
    raise SystemExit("FATAL: Cargo build status is invalid") from exc
if cargo_status < 0 or cargo_status > 255:
    raise SystemExit("FATAL: Cargo build status is invalid")
try:
    target = target.resolve(strict=True)
except OSError as exc:
    raise SystemExit(f"FATAL: Cargo target is unavailable: {exc}")
info = messages_path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
    raise SystemExit("FATAL: Cargo message stream is not one regular file")
if info.st_size > 64 * 1024 * 1024:
    raise SystemExit("FATAL: Cargo message stream exceeds 64 MiB")

out_dirs = set()
with messages_path.open(encoding="utf-8") as stream:
    for line_number, line in enumerate(stream, 1):
        try:
            message = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"FATAL: malformed Cargo JSON message at line {line_number}: {exc}"
            )
        if not isinstance(message, dict):
            raise SystemExit("FATAL: Cargo JSON message is not an object")
        if message.get("reason") == "compiler-message":
            compiler_message = message.get("message")
            rendered = (
                compiler_message.get("rendered")
                if isinstance(compiler_message, dict)
                else None
            )
            if isinstance(rendered, str):
                print(rendered, end="", file=sys.stderr)
        if (
            message.get("reason") == "build-script-executed"
            and message.get("package_id") == expected_package
        ):
            out_dir = message.get("out_dir")
            if not isinstance(out_dir, str) or not out_dir:
                raise SystemExit(
                    "FATAL: esp-idf-sys build message has no canonical out_dir"
                )
            out_dirs.add(out_dir)

if cargo_status != 0:
    print(f"FATAL: Cargo build failed with status {cargo_status}", file=sys.stderr)
    raise SystemExit(cargo_status)
if len(out_dirs) != 1:
    raise SystemExit(
        "FATAL: current Cargo build did not identify exactly one esp-idf-sys out_dir"
    )
out_dir = Path(out_dirs.pop()).absolute()
try:
    resolved_out = out_dir.resolve(strict=True)
    relative = resolved_out.relative_to(target)
except (OSError, ValueError) as exc:
    raise SystemExit(f"FATAL: esp-idf-sys out_dir escapes Cargo target: {exc}")
if resolved_out != out_dir:
    raise SystemExit("FATAL: esp-idf-sys out_dir contains a symlink")
parts = relative.parts
if (
    len(parts) != 5
    or parts[0] != "xtensa-esp32s3-espidf"
    or parts[1] != "release"
    or parts[2] != "build"
    or re.fullmatch(r"esp-idf-sys-[0-9a-f]{16}", parts[3]) is None
    or parts[4] != "out"
):
    raise SystemExit("FATAL: esp-idf-sys out_dir has an unexpected target layout")
sdkconfig = resolved_out / "sdkconfig"
try:
    sdk_info = sdkconfig.lstat()
except OSError as exc:
    raise SystemExit(f"FATAL: current generated sdkconfig is missing: {exc}")
if (
    not stat.S_ISREG(sdk_info.st_mode)
    or sdk_info.st_nlink != 1
    or sdk_info.st_uid != os.geteuid()
):
    raise SystemExit(
        "FATAL: current generated sdkconfig is not one owned regular file"
    )
print(sdkconfig)
PYSDK
)"
if [ "$ARTIFACT_KIND" = devkit-bringup ]; then
    python3 "$RS_DIR/tools/test_devkit_source_contract.py" generated \
        --sdkconfig "$sdk" \
        --elf "$T/$APP_NAME" \
        --recipe-id "$ESP32TAP_RECIPE_ID" \
        --git-commit "$ESP32TAP_GIT_COMMIT"
fi
FLASH_SIZE="$(grep -E '^CONFIG_ESPTOOLPY_FLASHSIZE=' "$sdk" | head -1 | cut -d\" -f2)"
[ -n "$FLASH_SIZE" ] || { echo "FATAL: flash size missing from sdkconfig" >&2; exit 1; }

cp -f "$T/bootloader.bin" /output/bootloader.bin
cp -f "$T/partition-table.bin" /output/partition-table.bin
FLASH_MODE=dio
python -m esptool --chip esp32s3 elf2image \
    --flash_mode "$FLASH_MODE" --flash_freq 80m --flash_size "$FLASH_SIZE" \
    -o /output/esp32tap.bin "$T/$APP_NAME"
cat > /output/flash_args <<EOF
--flash_mode $FLASH_MODE --flash_freq 80m --flash_size $FLASH_SIZE
0x0 bootloader.bin
0x8000 partition-table.bin
0x10000 esp32tap.bin
EOF
python -m esptool --chip esp32s3 image_info --version 2 /output/esp32tap.bin 2>/dev/null \
    | grep -qi "^Flash size: *$FLASH_SIZE$" \
    || { echo "FATAL: image header flash size mismatch" >&2; exit 1; }

if [ "$ARTIFACT_KIND" != devkit-bringup ]; then
    grep -q "CONFIG_ESP_TASK_WDT_PANIC=y" "$sdk"
    grep -q "CONFIG_FREERTOS_HZ=1000" "$sdk"
    qemu_flag=()
    if [ "$ARTIFACT_KIND" = qemu-test ]; then qemu_flag=(--allow-qemu); fi
    python3 "$RS_DIR/tools/check_sdkconfig.py" "$sdk" \
        --label "$ARTIFACT_KIND" "${qemu_flag[@]}"
fi
cp -f "$sdk" /output/sdkconfig

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
        (
            "CARGO_WORKSPACE_DIR=/tmp/esp32tap-devkit-source/devkit_bringup"
            if kind == "devkit-bringup"
            else "CARGO_WORKSPACE_DIR=/project/hardware/Esp32Tap/firmware/"
            "esp32_rs/esp32tap"
        ),
        "-e", "PROFILE=release",
        "-e", f"ARTIFACT_KIND={kind}",
        "-e", f"BUILD_FEATURES={feature_text}",
        "-e", f"SOURCE_INPUT_DIGEST={source_digest}",
        "-e", f"ESP32TAP_RECIPE_ID={recipe_id or ''}",
        "-e", f"ESP32TAP_GIT_COMMIT={git_commit or ''}",
        "-w", (
            "/project/hardware/Esp32Tap/firmware/esp32_rs/devkit_bringup"
            if kind == "devkit-bringup"
            else "/project/hardware/Esp32Tap/firmware/esp32_rs/esp32tap"
        ),
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
    process: subprocess.Popen[bytes] | None = None
    cancellation_deferred = True
    try:
        process = subprocess.Popen(arguments, start_new_session=True)
        cancellation_deferred = False
        if pending_signal is not None:
            raise BuildCancelled(pending_signal)
        returncode = process.wait(timeout=timeout)
    except BaseException:
        cancellation_deferred = False
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise
    finally:
        cancellation_deferred = False
    if returncode != 0:
        raise BuildError(f"Docker {kind} build failed with status {returncode}")


def validate_devkit_outputs(
    staging: Path, recipe_id: str, git_commit: str
) -> dict[str, object]:
    """Validate final flash bytes and return post-build geometry."""

    flash_args = (staging / "flash_args").read_text(encoding="ascii")
    lines = flash_args.splitlines()
    if (
        lines != [
            "--flash_mode dio --flash_freq 80m --flash_size 8MB",
            "0x0 bootloader.bin",
            "0x8000 partition-table.bin",
            "0x10000 esp32tap.bin",
        ]
    ):
        raise BuildError("DevKit flash arguments do not match exact 8 MB geometry")

    sdkconfig = (staging / "sdkconfig").read_bytes()
    for required in (
        b"CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y\n",
        b"CONFIG_ESPTOOLPY_FLASHMODE_DIO=y\n",
        b'CONFIG_ESPTOOLPY_FLASHMODE="dio"\n',
        b"CONFIG_SPIRAM=y\n",
        b"CONFIG_SPIRAM_MODE_OCT=y\n",
        b"CONFIG_ESP_CONSOLE_UART_DEFAULT=y\n",
        b"CONFIG_ESP_CONSOLE_UART=y\n",
        b"CONFIG_ESP_CONSOLE_SECONDARY_NONE=y\n",
    ):
        if required not in sdkconfig:
            raise BuildError(f"DevKit sdkconfig is missing {required.strip()!r}")
    for forbidden in (
        b"CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y\n",
        b"CONFIG_ESP_CONSOLE_USB_CDC=y\n",
        b"CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y\n",
        b"CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG_ENABLED=y\n",
        b"CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y\n",
        b"CONFIG_BT_ENABLED=y\n",
        b"CONFIG_ETH_USE_OPENETH=y\n",
        b"CONFIG_ESPTOOLPY_FLASHMODE_QIO=y\n",
    ):
        if forbidden in sdkconfig:
            raise BuildError(f"DevKit sdkconfig enables forbidden identity {forbidden.strip()!r}")

    images: dict[str, bytes] = {}
    for name in ("bootloader.bin", "esp32tap.bin"):
        image_bytes = (staging / name).read_bytes()
        if (
            len(image_bytes) < 3
            or image_bytes[0] != 0xE9
            or image_bytes[2] != 2
        ):
            raise BuildError(f"DevKit {name} image header is not DIO")
        images[name] = image_bytes

    image = images["esp32tap.bin"]
    identity = (
        recipe_id.encode()
        + git_commit.encode()
        + "ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS".encode()
    )
    if image.count(identity) != 1:
        raise BuildError("final DevKit app image has embedded recipe mismatch")
    for forbidden in (
        b"esp32tap QEMU-TEST build",
        b"qemu-test",
        b"esp_wifi_init",
        b"esp_wifi_start",
        b"esp_wifi_connect",
        b"nimble_port_init",
        b"esp_eth_driver_install",
    ):
        if forbidden in image:
            raise BuildError(
                f"final DevKit app image contains forbidden identity {forbidden!r}"
            )

    table = (staging / "partition-table.bin").read_bytes()
    factory: tuple[int, int] | None = None
    for index in range(0, len(table) - 31, 32):
        entry = table[index : index + 32]
        if entry[:2] != b"\xaa\x50":
            continue
        _, partition_type, _, offset, size = struct.unpack("<HBBII", entry[:12])
        if partition_type == 0:
            if factory is not None:
                raise BuildError("DevKit partition table has multiple app partitions")
            factory = (offset, size)
    if factory is None or factory[0] != 65_536:
        raise BuildError("DevKit partition table lacks the exact factory app offset")
    if len(image) > factory[1]:
        raise BuildError(
            f"DevKit app image exceeds factory partition ({len(image)} > {factory[1]})"
        )
    return {
        "chip": "esp32s3",
        "size": 8_388_608,
        "offsets": [0, 32_768, 65_536],
    }


import contextlib  # kept after the embedded container script for readability


def main() -> None:
    global cancellation_deferred, cleanup_in_progress
    acquire_worktree_lock()
    kinds = requested_kinds()
    devkit_commit = (
        clean_git_commit()
        if any(kind == "devkit-bringup" for kind, _ in kinds)
        else None
    )
    cache_root = target_cache(repo_root, "prod").parent
    secure_cache_dir(cache_root)
    task_root: Path | None = None
    snapshot = None
    stagings: list[Path] = []
    try:
        cancellation_deferred = True
        try:
            task_root = Path(
                tempfile.mkdtemp(prefix="esp32tap-snapshot-build.", dir="/tmp")
            )
        finally:
            cancellation_deferred = False
        raise_pending_cancellation()

        cancellation_deferred = True
        try:
            snapshot = create_snapshot(
                repo_root, task_root / "source", cache_root
            )
        finally:
            cancellation_deferred = False
        raise_pending_cancellation()
        print(f"== immutable source {snapshot.digest} ==")
        validate_snapshot_identity(snapshot)
        if devkit_commit is not None:
            try:
                verify_snapshot_matches_commit(snapshot, repo_root, devkit_commit)
            except (ValueError, RuntimeError) as exc:
                raise BuildError(
                    f"immutable snapshot does not match claimed Git commit: {exc}"
                ) from exc
            if clean_git_commit() != devkit_commit:
                raise BuildError("Git commit changed while snapshot was captured")
        sealed_baseline = sealed_snapshot_digest(snapshot.root, snapshot.paths)

        # Recipe/toolchain validation deliberately precedes every host gate.
        toolchains = {
            kind: _current_toolchain(snapshot.root, kind)
            for kind, _ in kinds
        }
        recipes = {
            kind: (
                make_recipe_id(
                    git_commit=devkit_commit,
                    kind=kind,
                    profile="release",
                    input_digest=snapshot.digest,
                    toolchain=toolchains[kind],
                )
                if kind == "devkit-bringup" and devkit_commit is not None
                else None
            )
            for kind, _ in kinds
        }
        verify_gate_input_completeness(
            snapshot.root, include_devkit=devkit_commit is not None
        )
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
            cancellation_deferred = True
            try:
                staging.mkdir(mode=0o700)
            finally:
                if os.path.lexists(staging):
                    stagings.append(staging)
                cancellation_deferred = False
            raise_pending_cancellation()
            run_docker(
                snapshot.root,
                staging,
                target,
                cargo_cache,
                kind,
                cache_kind,
                toolchains[kind],
                snapshot.digest,
                recipes[kind],
                devkit_commit if kind == "devkit-bringup" else None,
            )
            names = {entry.name for entry in os.scandir(staging)}
            if names != set(BUNDLE_MEMBERS):
                raise BuildError(
                    f"{kind} build emitted {sorted(names)}, expected "
                    f"{list(BUNDLE_MEMBERS)}"
                )
            if kind == "devkit-bringup":
                if recipes[kind] is None or devkit_commit is None:
                    raise BuildError("DevKit pre-build identity is incomplete")
                geometry = validate_devkit_outputs(
                    staging, recipes[kind], devkit_commit
                )
                manifests[kind] = make_manifest(
                    staging,
                    kind,
                    snapshot.digest,
                    toolchains[kind],
                    git_commit=devkit_commit,
                    dirty_state="clean",
                    profile="release",
                    required_serial_device=DEVKIT_REQUIRED_SERIAL_DEVICE,
                    flash_geometry=geometry,
                    recipe_id=recipes[kind],
                )
            else:
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
        if devkit_commit is not None:
            if clean_commit_without_stagings(stagings, task_root) != devkit_commit:
                raise BuildError("Git commit changed while DevKit build was running")

        # Each public link is an independent atomic commit. If the process
        # crashes between kinds, readers can see a valid old/new mix; neither
        # link is ever absent or partial, and current verification detects it.
        cancellation_deferred = True
        try:
            for (kind, _), staging in zip(kinds, stagings, strict=True):
                public_name = {
                    "production": "build",
                    "qemu-test": "build_qemu_test",
                    "devkit-bringup": "build_devkit_bringup",
                }[kind]
                publish_generation_atomic(
                    staging,
                    esp32_rs / public_name,
                    manifests[kind],
                    lock_fd=9,
                )
                identity = manifests[kind]["manifest_sha256"]
                print(f"published {kind}: {identity}")
        finally:
            cancellation_deferred = False
        raise_pending_cancellation()
        if only == "both":
            print("ONLY=both published both manifests")
    except BaseException:
        # Enter cleanup with watched signals latch-only. If a signal lands
        # before this assignment, it has already populated pending_signal, so
        # later deliveries are duplicates and cannot bypass the finally suite.
        cancellation_deferred = True
        raise
    else:
        cancellation_deferred = True
    finally:
        cleanup_in_progress = True
        failed_stagings: list[tuple[Path, BaseException]] = []
        for staging in reversed(stagings):
            try:
                cleanup_staging(staging)
            except BaseException as exc:
                failed_stagings.append((staging, exc))

        snapshot_failure: BaseException | None = None
        task_root_failure: BaseException | None = None
        if task_root is not None:
            try:
                if snapshot is not None:
                    remove_snapshot(snapshot.root, task_root)
            except BaseException as exc:
                snapshot_failure = exc
            finally:
                try:
                    cleanup_task_root(task_root)
                except BaseException as exc:
                    task_root_failure = exc

        cleanup_failures: list[BaseException] = []
        for staging, first_failure in failed_stagings:
            try:
                cleanup_staging(staging)
            except BaseException as retry_failure:
                cleanup_failures.extend((first_failure, retry_failure))
        if task_root_failure is not None:
            if snapshot_failure is not None:
                cleanup_failures.append(snapshot_failure)
            cleanup_failures.append(task_root_failure)

        for failure in cleanup_failures:
            print(f"build.sh: cleanup failed: {failure}", file=sys.stderr)
        cleanup_in_progress = False
        cancellation_deferred = False
        raise_pending_cancellation()
        if cleanup_failures:
            raise cleanup_failures[0]


try:
    main()
except BuildError as exc:
    print(f"build.sh: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
