#!/usr/bin/env bash
# Build and attest the pinned ESP32Tap Rust toolchain image.
#
# --recipe hashes the exact Docker recipe without invoking Docker.
# --check performs one read-only `docker image inspect`, validates both OCI
# labels, and emits the canonical artifact_provenance.Toolchain JSON for the
# requested artifact kind. It never starts a container.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
ESP32_RS="$(cd "$HERE/.." && pwd -P)"
IMAGE="${RUST_IMAGE:-esp32tap-rust:build}"
DOCKER_TIMEOUT="${BUILD_IMAGE_DOCKER_TIMEOUT:-1800}"
PROBE_TIMEOUT="${BUILD_IMAGE_PROBE_COMMAND_TIMEOUT:-30}"
MAX_OUTPUT_BYTES=1048576
RECIPE_LABEL="org.treddy.esp32tap.recipe-sha256"
TOOLCHAIN_LABEL="org.treddy.esp32tap.toolchain-json"

usage() {
    echo "usage: tools/build_image.sh [--recipe | --check --kind production|qemu-test]" >&2
    exit 2
}

case "$DOCKER_TIMEOUT" in
    ''|*[!0-9]*) echo "build_image.sh: BUILD_IMAGE_DOCKER_TIMEOUT must be a positive integer" >&2; exit 2 ;;
    0) echo "build_image.sh: BUILD_IMAGE_DOCKER_TIMEOUT must be positive" >&2; exit 2 ;;
esac
case "$PROBE_TIMEOUT" in
    ''|*[!0-9]*) echo "build_image.sh: BUILD_IMAGE_PROBE_COMMAND_TIMEOUT must be a positive integer" >&2; exit 2 ;;
    0) echo "build_image.sh: BUILD_IMAGE_PROBE_COMMAND_TIMEOUT must be positive" >&2; exit 2 ;;
esac
case "$IMAGE" in
    [A-Za-z0-9]*) ;;
    *) echo "build_image.sh: invalid RUST_IMAGE tag" >&2; exit 2 ;;
esac
case "$IMAGE" in
    *[!A-Za-z0-9._/:@+-]*) echo "build_image.sh: invalid RUST_IMAGE tag" >&2; exit 2 ;;
esac
if [ "${#IMAGE}" -gt 16384 ]; then
    echo "build_image.sh: invalid RUST_IMAGE tag" >&2
    exit 2
fi

recipe_sha256() {
    python3 - "$ESP32_RS" <<'PY'
import hashlib
import os
import stat
import struct
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = ("Dockerfile", ".dockerignore")
limits = {"Dockerfile": 1024 * 1024, ".dockerignore": 64 * 1024}
digest = hashlib.sha256(b"esp32tap-docker-recipe-v1\0")
for relative in sorted(paths):
    path = root / relative
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SystemExit(f"build_image.sh: {relative} must be a single-link regular file")
    if info.st_size > limits[relative]:
        raise SystemExit(f"build_image.sh: {relative} exceeds its size limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(fd)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
    ):
        os.close(fd)
        raise SystemExit(f"build_image.sh: {relative} changed while opening")
    encoded = relative.encode("utf-8")
    mode = stat.S_IMODE(opened.st_mode)
    digest.update(struct.pack(">Q", len(encoded)))
    digest.update(encoded)
    digest.update(struct.pack(">I", mode))
    digest.update(struct.pack(">Q", opened.st_size))
    size = 0
    try:
        while block := os.read(fd, min(1024 * 1024, limits[relative] - size + 1)):
            size += len(block)
            if size > limits[relative]:
                raise SystemExit(f"build_image.sh: {relative} exceeds its size limit")
            digest.update(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (
        size != opened.st_size
        or (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_mode,
            opened.st_nlink,
        )
        != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
        )
    ):
        raise SystemExit(f"build_image.sh: {relative} changed while hashing")
print(digest.hexdigest())
PY
}

component_lock_sha256() {
    python3 - "$ESP32_RS/esp32tap/components_esp32s3.lock" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
    raise SystemExit("build_image.sh: component lock must be a single-link regular file")
if info.st_size > 4 * 1024 * 1024:
    raise SystemExit("build_image.sh: component lock exceeds its size limit")
fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
opened = os.fstat(fd)
if (
    not stat.S_ISREG(opened.st_mode)
    or opened.st_nlink != 1
    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
):
    os.close(fd)
    raise SystemExit("build_image.sh: component lock changed while opening")
digest = hashlib.sha256()
size = 0
try:
    while block := os.read(fd, min(1024 * 1024, 4 * 1024 * 1024 - size + 1)):
        size += len(block)
        if size > 4 * 1024 * 1024:
            raise SystemExit("build_image.sh: component lock exceeds its size limit")
        digest.update(block)
    after = os.fstat(fd)
finally:
    os.close(fd)
if size != opened.st_size or (
    opened.st_size,
    opened.st_mtime_ns,
    opened.st_ctime_ns,
    opened.st_mode,
    opened.st_nlink,
) != (
    after.st_size,
    after.st_mtime_ns,
    after.st_ctime_ns,
    after.st_mode,
    after.st_nlink,
):
    raise SystemExit("build_image.sh: component lock changed while hashing")
print(digest.hexdigest())
PY
}

bounded_docker() {
    local stdout_path="$1"
    local stderr_path="$2"
    shift 2
    if ! (
        ulimit -f 2048
        timeout --kill-after=2 "$DOCKER_TIMEOUT" docker "$@" \
            >"$stdout_path" 2>"$stderr_path"
    ); then
        echo "build_image.sh: docker command failed: docker $*" >&2
        if [ -s "$stderr_path" ]; then
            head -c "$MAX_OUTPUT_BYTES" "$stderr_path" >&2
            echo >&2
        fi
        return 1
    fi
    if [ "$(stat -c %s "$stdout_path")" -gt "$MAX_OUTPUT_BYTES" ] ||
       [ "$(stat -c %s "$stderr_path")" -gt "$MAX_OUTPUT_BYTES" ]; then
        echo "build_image.sh: docker output exceeds ${MAX_OUTPUT_BYTES} bytes" >&2
        return 1
    fi
}

check_image() (
    local kind="$1"
    local recipe component_sha check_tmp inspect_out inspect_err
    recipe="$(recipe_sha256)"
    component_sha="$(component_lock_sha256)"
    check_tmp="$(mktemp -d /tmp/esp32tap-image-check.XXXXXX)"
    inspect_out="$check_tmp/inspect.json"
    inspect_err="$check_tmp/inspect.err"
    trap 'rm -rf "$check_tmp"' EXIT
    bounded_docker "$inspect_out" "$inspect_err" image inspect "$IMAGE"
    python3 - \
        "$inspect_out" "$recipe" "$component_sha" "$IMAGE" "$kind" \
        "$RECIPE_LABEL" "$TOOLCHAIN_LABEL" <<'PY'
import json
import re
import sys
from pathlib import Path

(
    inspect_path,
    recipe,
    component_sha,
    image_tag,
    kind,
    recipe_label,
    toolchain_label,
) = sys.argv[1:]
path = Path(inspect_path)
if path.stat().st_size > 1024 * 1024:
    raise SystemExit("build_image.sh: docker inspect output exceeds its size limit")
try:
    inspected = json.loads(path.read_text(encoding="utf-8"))
except (UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"build_image.sh: malformed docker inspect JSON: {exc}")
if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
    raise SystemExit("build_image.sh: docker inspect must return exactly one image")
image = inspected[0]
image_id = image.get("Id")
if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
    raise SystemExit("build_image.sh: Docker image lacks an immutable SHA-256 ID")
config = image.get("Config")
labels = config.get("Labels") if isinstance(config, dict) else None
if not isinstance(labels, dict):
    raise SystemExit("build_image.sh: Docker image has no labels; run tools/build_image.sh")
if labels.get(recipe_label) != recipe:
    raise SystemExit("build_image.sh: Docker image recipe is stale; run tools/build_image.sh")
attestation_text = labels.get(toolchain_label)
if not isinstance(attestation_text, str) or len(attestation_text) > 1024 * 1024:
    raise SystemExit("build_image.sh: Docker image has no bounded toolchain attestation")
try:
    attestation = json.loads(attestation_text)
except (UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"build_image.sh: malformed toolchain label JSON: {exc}")
keys = {
    "schema_version",
    "idf_commit",
    "rustc_verbose",
    "target",
    "linker_version",
    "esptool_version",
    "component_lock_sha256",
}
if not isinstance(attestation, dict) or set(attestation) != keys:
    raise SystemExit("build_image.sh: toolchain label fields do not match schema")
canonical = json.dumps(attestation, sort_keys=True, separators=(",", ":"))
if attestation_text != canonical:
    raise SystemExit("build_image.sh: toolchain label JSON is not canonical")
if (
    type(attestation["schema_version"]) is not int
    or attestation["schema_version"] != 1
):
    raise SystemExit("build_image.sh: unsupported toolchain label schema")
for key in keys - {"schema_version"}:
    value = attestation[key]
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\0" in value
        or len(value) > 16384
    ):
        raise SystemExit(f"build_image.sh: invalid toolchain label field {key}")
if not re.fullmatch(r"[0-9a-f]{40,64}", attestation["idf_commit"]):
    raise SystemExit("build_image.sh: invalid attested IDF commit")
if not re.fullmatch(r"[0-9a-f]{64}", attestation["component_lock_sha256"]):
    raise SystemExit("build_image.sh: invalid attested component-lock digest")
if attestation["component_lock_sha256"] != component_sha:
    raise SystemExit("build_image.sh: component lock changed; run tools/build_image.sh")
if attestation["target"] != "xtensa-esp32s3-espidf":
    raise SystemExit("build_image.sh: unexpected attested Rust target")
if kind == "production":
    features = []
elif kind == "qemu-test":
    features = ["ble", "net", "qemu-test"]
else:
    raise SystemExit("build_image.sh: unsupported artifact kind")
result = {
    key: value
    for key, value in attestation.items()
    if key != "schema_version"
}
result.update(
    image_id=image_id,
    recipe_sha256=recipe,
    image_tag=image_tag,
    profile="release",
    features=features,
)
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
PY
)

if [ "$#" -eq 1 ] && [ "$1" = "--recipe" ]; then
    recipe_sha256
    exit 0
fi

if [ "$#" -eq 3 ] && [ "$1" = "--check" ] && [ "$2" = "--kind" ]; then
    case "$3" in
        production|qemu-test) check_image "$3" ;;
        *) usage ;;
    esac
    exit 0
fi

if [ "$#" -ne 0 ]; then
    usage
fi

exec python3 - \
    "$ESP32_RS" "$IMAGE" "$DOCKER_TIMEOUT" "$PROBE_TIMEOUT" \
    "$RECIPE_LABEL" "$TOOLCHAIN_LABEL" <<'PY'
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

(
    root_text,
    image_tag,
    docker_timeout_text,
    probe_timeout_text,
    recipe_label,
    toolchain_label,
) = sys.argv[1:]
root = Path(root_text).resolve(strict=True)
docker_timeout = float(docker_timeout_text)
probe_timeout = float(probe_timeout_text)
max_docker_output = 1024 * 1024
recipe_limits = {"Dockerfile": 1024 * 1024, ".dockerignore": 64 * 1024}
component_limit = 4 * 1024 * 1024


class BuildImageError(Exception):
    pass


class BuildCancelled(SystemExit):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(128 + signum)


cancel_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
cancellation_signum: int | None = None
cancellation_deferred = False


def cancel_build(signum: int, _frame: object) -> None:
    global cancellation_signum
    if cancellation_signum is None:
        cancellation_signum = signum
    if not cancellation_deferred:
        raise BuildCancelled(cancellation_signum)


def deliver_cancellation(*, force: bool = False) -> None:
    if cancellation_signum is not None and (force or not cancellation_deferred):
        raise BuildCancelled(cancellation_signum)


for watched_signal in cancel_signals:
    signal.signal(watched_signal, cancel_build)


def clean_excepthook(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: object,
) -> None:
    if issubclass(exception_type, BuildImageError):
        print(f"build_image.sh: {exception}", file=sys.stderr)
    else:
        sys.__excepthook__(exception_type, exception, traceback)


sys.excepthook = clean_excepthook


def stop_group(process: subprocess.Popen[bytes]) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(0.05)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)


def run_bounded(
    argv: list[str],
    *,
    timeout: float = docker_timeout,
    max_output: int = max_docker_output,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    deliver_cancellation()
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise BuildImageError(f"cannot execute {argv[0]}: {exc}") from exc
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            deliver_cancellation()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_group(process)
                raise BuildImageError(
                    f"command timed out after {timeout:g}s: {' '.join(argv[:3])}"
                )
            for key, _ in selector.select(min(remaining, 0.05)):
                block = os.read(key.fd, 64 * 1024)
                if not block:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                destination = output[key.data]
                destination.extend(block)
                if len(destination) > max_output:
                    stop_group(process)
                    raise BuildImageError(
                        f"command output exceeds {max_output} bytes: {' '.join(argv[:3])}"
                    )
        try:
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            stop_group(process)
            raise BuildImageError(
                f"command timed out after {timeout:g}s: {' '.join(argv[:3])}"
            ) from exc
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            with contextlib.suppress(Exception):
                pipe.close()
        if process.poll() is None:
            stop_group(process)
    deliver_cancellation()
    try:
        stdout = output["stdout"].decode("utf-8")
        stderr = output["stderr"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildImageError(f"command output is not UTF-8: {' '.join(argv[:3])}") from exc
    completed = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    if check and returncode != 0:
        detail = stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise BuildImageError(f"command failed ({returncode}): {' '.join(argv[:3])}{suffix}")
    return completed


def docker(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_bounded(["docker", *argv], check=check)


def safe_read(path: Path, limit: int, label: str) -> tuple[bytes, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BuildImageError(f"cannot inspect {label}: {exc}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > limit
    ):
        raise BuildImageError(f"{label} must be a bounded single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BuildImageError(f"cannot safely open {label}: {exc}") from exc
    digest_data = bytearray()
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise BuildImageError(f"{label} changed while opening")
        while block := os.read(fd, min(1024 * 1024, limit - len(digest_data) + 1)):
            digest_data.extend(block)
            if len(digest_data) > limit:
                raise BuildImageError(f"{label} exceeds its size limit")
        after = os.fstat(fd)
        identity = lambda value: (
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_mode,
            value.st_nlink,
        )
        if len(digest_data) != opened.st_size or identity(opened) != identity(after):
            raise BuildImageError(f"{label} changed while reading")
        return bytes(digest_data), stat.S_IMODE(opened.st_mode)
    finally:
        os.close(fd)


def recipe_from(values: dict[str, tuple[bytes, int]]) -> str:
    digest = hashlib.sha256(b"esp32tap-docker-recipe-v1\0")
    for relative in sorted(values):
        data, mode = values[relative]
        encoded = relative.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
        digest.update(struct.pack(">I", mode))
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


def read_recipe(directory: Path) -> tuple[dict[str, tuple[bytes, int]], str]:
    values = {
        relative: safe_read(directory / relative, limit, relative)
        for relative, limit in recipe_limits.items()
    }
    return values, recipe_from(values)


def component_digest(directory: Path) -> str:
    data, _ = safe_read(
        directory / "esp32tap" / "components_esp32s3.lock",
        component_limit,
        "component lock",
    )
    return hashlib.sha256(data).hexdigest()


def write_snapshot(context: Path, values: dict[str, tuple[bytes, int]]) -> None:
    context.mkdir(mode=0o700)
    for relative, (data, mode) in values.items():
        destination = context / relative
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(destination, flags, mode)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
    directory_fd = os.open(context, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


PROBE_PROGRAM = r'''
import contextlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time

LIMIT = 64 * 1024
TIMEOUT = float(os.environ.get("ESP32TAP_PROBE_COMMAND_TIMEOUT", "30"))

def stop(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(.05)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)

def probe(argv):
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + TIMEOUT
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop(process)
                raise SystemExit("toolchain probe timed out")
            for key, _ in selector.select(min(remaining, .05)):
                block = os.read(key.fd, 8192)
                if not block:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output[key.data].extend(block)
                if len(output[key.data]) > LIMIT:
                    stop(process)
                    raise SystemExit("toolchain probe output exceeds its size limit")
        try:
            returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop(process)
            raise SystemExit("toolchain probe timed out")
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            with contextlib.suppress(Exception):
                pipe.close()
        if process.poll() is None:
            stop(process)
    try:
        stdout = output["stdout"].decode("utf-8").strip()
        stderr = output["stderr"].decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SystemExit("toolchain probe output is not UTF-8")
    if returncode != 0:
        raise SystemExit(f"toolchain probe failed: {stderr}")
    if not stdout or "\0" in stdout or len(stdout) > 16384:
        raise SystemExit("invalid toolchain probe output")
    return stdout

value = {
    "schema_version": 1,
    "idf_commit": probe(["git", "-C", "/opt/esp/idf", "rev-parse", "--verify", "HEAD"]),
    "rustc_verbose": probe(["rustc", "+esp", "--version", "--verbose"]),
    "target": "xtensa-esp32s3-espidf",
    "linker_version": probe(["ldproxy", "--version"]),
    "esptool_version": probe([sys.executable, "-m", "esptool", "version"]),
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
'''


def validate_probe(text: str, component_sha: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BuildImageError(f"malformed toolchain probe JSON: {exc}") from exc
    keys = {
        "schema_version",
        "idf_commit",
        "rustc_verbose",
        "target",
        "linker_version",
        "esptool_version",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise BuildImageError("toolchain probe fields do not match schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise BuildImageError("unsupported toolchain probe schema")
    for key in keys - {"schema_version"}:
        item = value[key]
        if (
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or "\0" in item
            or len(item) > 16384
        ):
            raise BuildImageError(f"invalid toolchain probe field {key}")
    if not re.fullmatch(r"[0-9a-f]{40,64}", value["idf_commit"]):
        raise BuildImageError("invalid IDF commit")
    if value["target"] != "xtensa-esp32s3-espidf":
        raise BuildImageError("unexpected Rust target")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if text != canonical:
        raise BuildImageError("toolchain probe JSON is not canonical")
    value["component_lock_sha256"] = component_sha
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def label_change(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'LABEL "{key}"="{escaped}"'


def inspect_image(reference: str, *, missing_ok: bool = False) -> dict | None:
    completed = docker("image", "inspect", reference, check=False)
    if completed.returncode != 0:
        if missing_ok and "No such image" in completed.stderr:
            return None
        raise BuildImageError(
            f"cannot inspect Docker image {reference}: {completed.stderr.strip()}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BuildImageError(f"malformed docker inspect JSON: {exc}") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise BuildImageError("docker inspect must return exactly one image")
    return value[0]


def immutable_id(image: dict) -> str:
    value = image.get("Id")
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise BuildImageError("Docker image lacks an immutable SHA-256 ID")
    return value


def validate_candidate(image: dict, recipe: str, attestation: str) -> str:
    image_id = immutable_id(image)
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise BuildImageError("candidate image has no labels")
    if labels.get(recipe_label) != recipe:
        raise BuildImageError("candidate image recipe label mismatch")
    if labels.get(toolchain_label) != attestation:
        raise BuildImageError("candidate image toolchain label mismatch")
    return image_id


@contextlib.contextmanager
def publication_lock():
    # Docker tags are daemon-global. Keying this lock by a worktree would let
    # two checkouts race on the same mutable final tag, including rollback.
    # Conservatively serialize the exact validated image tag across the host.
    key = hashlib.sha256(image_tag.encode("utf-8")).hexdigest()[:24]
    path = Path("/tmp") / f"esp32tap-image-publish-{key}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    previous_umask = os.umask(0o077)
    try:
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise BuildImageError(f"cannot safely open image publication lock: {exc}") from exc
    finally:
        os.umask(previous_umask)
    try:
        opened = os.fstat(fd)
        def validate_named_lock() -> None:
            try:
                named = path.lstat()
            except OSError as exc:
                raise BuildImageError(f"cannot revalidate image publication lock: {exc}") from exc
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise BuildImageError("unsafe image publication lock")
        validate_named_lock()
        deadline = time.monotonic() + docker_timeout
        while True:
            deliver_cancellation()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BuildImageError("timed out waiting for image publication lock")
                time.sleep(0.05)
        validate_named_lock()
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def publication_lifecycle():
    global cancellation_deferred
    # Deliver cancellation only after the daemon-global publication lock has
    # been released. This preserves cleanup/rollback ordering across worktrees.
    try:
        with publication_lock():
            yield
    finally:
        cancellation_deferred = False
        deliver_cancellation(force=True)


@contextlib.contextmanager
def defer_cancellation_during_promotion():
    global cancellation_deferred
    # A final-tag update plus ID verification/rollback is one tiny critical
    # transaction. Deliver a pending cancellation only after it is known to be
    # fully published or fully restored.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, cancel_signals)
    previous_deferred = cancellation_deferred
    cancellation_deferred = True
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        cancellation_deferred = previous_deferred
        deliver_cancellation()


def verify_reference(reference: str, expected_id: str) -> None:
    image = inspect_image(reference)
    if image is None:
        raise BuildImageError(f"Docker reference {reference} is missing")
    if immutable_id(image) != expected_id:
        raise BuildImageError(
            f"Docker reference {reference} did not resolve to promoted image"
        )


def restore_final(prior_id: str | None) -> None:
    if prior_id is None:
        with contextlib.suppress(BuildImageError):
            docker("image", "rm", image_tag, check=False)
        if inspect_image(image_tag, missing_ok=True) is not None:
            raise BuildImageError("could not restore absent final image reference")
    else:
        with contextlib.suppress(BuildImageError):
            docker("tag", prior_id, image_tag)
        verify_reference(image_tag, prior_id)


def required_remove_container(reference: str) -> None:
    completed = docker("rm", "-f", reference, check=False)
    if completed.returncode != 0:
        raise BuildImageError(
            f"cannot remove probe container before publication: {completed.stderr.strip()}"
        )


def required_remove_image(reference: str) -> None:
    completed = docker("image", "rm", reference, check=False)
    if completed.returncode != 0:
        raise BuildImageError(
            f"cannot remove temporary image before publication: {completed.stderr.strip()}"
        )


with publication_lifecycle():
    token = secrets.token_hex(32)
    task_root = Path("/tmp") / f"esp32tap-image-build.{token}"
    context = task_root / "context"
    stage = f"esp32tap-rust-stage:{token}"
    candidate = f"esp32tap-rust-candidate:{token}"
    container = f"esp32tap-rust-probe-{token}"
    task_root_exists = False
    container_possible = False
    stage_possible = False
    candidate_possible = False
    publication_succeeded = False
    try:
        # Mark possible before the mkdir so a signal between the syscall and
        # the following Python bytecode still drives cleanup.
        task_root_exists = True
        try:
            task_root.mkdir(mode=0o700)
        except OSError as exc:
            raise BuildImageError(
                f"cannot create private image-build directory: {exc}"
            ) from exc

        recipe_values, recipe = read_recipe(root)
        component_sha = component_digest(root)
        write_snapshot(context, recipe_values)
        if read_recipe(context)[1] != recipe:
            raise BuildImageError("private Docker recipe snapshot changed")

        stage_possible = True
        docker("build", "--tag", stage, str(context))
        container_possible = True
        probe = docker(
            "run",
            "--name",
            container,
            "-e",
            f"ESP32TAP_PROBE_COMMAND_TIMEOUT={probe_timeout:g}",
            stage,
            "python3",
            "-c",
            PROBE_PROGRAM,
        )
        attestation = validate_probe(probe.stdout, component_sha)
        if read_recipe(context)[1] != recipe:
            raise BuildImageError("private Docker recipe snapshot changed during build")

        candidate_possible = True
        docker(
            "commit",
            "--change",
            label_change(recipe_label, recipe),
            "--change",
            label_change(toolchain_label, attestation),
            container,
            candidate,
        )
        candidate_image = inspect_image(candidate)
        if candidate_image is None:
            raise BuildImageError("candidate image is missing after commit")
        candidate_id = validate_candidate(candidate_image, recipe, attestation)

        # These resources are no longer needed once the candidate is attested.
        # Their cleanup is part of the transaction: failure leaves the prior
        # final tag untouched and makes this invocation fail.
        required_remove_container(container)
        container_possible = False
        required_remove_image(stage)
        stage_possible = False
        try:
            shutil.rmtree(task_root)
            task_root_exists = False
        except OSError as exc:
            raise BuildImageError(
                f"cannot remove private build context before publication: {exc}"
            ) from exc

        prior = inspect_image(image_tag, missing_ok=True)
        prior_id = None if prior is None else immutable_id(prior)
        if read_recipe(root)[1] != recipe or component_digest(root) != component_sha:
            raise BuildImageError(
                "recipe or component lock changed during image build"
            )
        with defer_cancellation_during_promotion():
            mutation_possible = True
            try:
                docker("tag", candidate_id, image_tag)
                verify_reference(image_tag, candidate_id)
                mutation_possible = False
                publication_succeeded = True
            except BaseException as promotion_error:
                if mutation_possible:
                    try:
                        restore_final(prior_id)
                    except BaseException as restore_error:
                        raise BuildImageError(
                            f"promotion failed ({promotion_error}); "
                            f"rollback failed ({restore_error})"
                        ) from restore_error
                raise
    finally:
        # From this point through publication_lifecycle.__exit__, handlers only
        # record the first signal. Every cleanup path and the publication-lock
        # release completes before the conventional 128+signal exit is raised.
        cancellation_deferred = True
        cleanup_warnings = []
        if container_possible:
            try:
                completed = docker("rm", "-f", container, check=False)
                if completed.returncode != 0:
                    cleanup_warnings.append(
                        f"cannot remove probe container: {completed.stderr.strip()}"
                    )
            except Exception as exc:
                cleanup_warnings.append(str(exc))
        for reference, possible in (
            (candidate, candidate_possible),
            (stage, stage_possible),
        ):
            if possible:
                try:
                    completed = docker("image", "rm", reference, check=False)
                    if completed.returncode != 0:
                        cleanup_warnings.append(
                            f"cannot remove temporary image {reference}: "
                            f"{completed.stderr.strip()}"
                        )
                except Exception as exc:
                    cleanup_warnings.append(str(exc))
        if task_root_exists:
            try:
                shutil.rmtree(task_root)
            except OSError as exc:
                cleanup_warnings.append(f"cannot remove private build context: {exc}")
        if cleanup_warnings:
            prefix = (
                "best-effort cleanup warning after successful publication"
                if publication_succeeded
                else "cleanup also failed"
            )
            print(
                f"build_image.sh: {prefix}: " + "; ".join(cleanup_warnings),
                file=sys.stderr,
            )

print(f"built {image_tag}")
PY
