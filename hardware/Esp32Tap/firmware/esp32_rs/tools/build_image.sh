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

validate_probe() {
    local probe_path="$1"
    local component_sha="$2"
    local attestation_path="$3"
    python3 - "$probe_path" "$component_sha" "$attestation_path" <<'PY'
import json
import re
import sys
from pathlib import Path

source, component_sha, destination = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
if source.stat().st_size > 1024 * 1024:
    raise SystemExit("build_image.sh: toolchain probe output exceeds its size limit")
try:
    text = source.read_text(encoding="utf-8")
    value = json.loads(text)
except (UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"build_image.sh: malformed toolchain probe JSON: {exc}")
keys = {
    "schema_version",
    "idf_commit",
    "rustc_verbose",
    "target",
    "linker_version",
    "esptool_version",
}
if not isinstance(value, dict) or set(value) != keys:
    raise SystemExit("build_image.sh: toolchain probe fields do not match schema")
if type(value["schema_version"]) is not int or value["schema_version"] != 1:
    raise SystemExit("build_image.sh: unsupported toolchain probe schema")
for key in keys - {"schema_version"}:
    item = value[key]
    if (
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or "\0" in item
        or len(item) > 16384
    ):
        raise SystemExit(f"build_image.sh: invalid toolchain probe field {key}")
if not re.fullmatch(r"[0-9a-f]{40,64}", value["idf_commit"]):
    raise SystemExit("build_image.sh: invalid IDF commit")
if value["target"] != "xtensa-esp32s3-espidf":
    raise SystemExit("build_image.sh: unexpected Rust target")
canonical_probe = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
if text != canonical_probe:
    raise SystemExit("build_image.sh: toolchain probe JSON is not canonical")
if not re.fullmatch(r"[0-9a-f]{64}", component_sha):
    raise SystemExit("build_image.sh: invalid component-lock digest")
value["component_lock_sha256"] = component_sha
destination.write_text(
    json.dumps(value, sort_keys=True, separators=(",", ":")),
    encoding="utf-8",
)
PY
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

recipe="$(recipe_sha256)"
component_sha="$(component_lock_sha256)"
task_tmp="$(mktemp -d /tmp/esp32tap-image-build.XXXXXX)"
token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
stage_image="esp32tap-rust-stage:$token"
container="esp32tap-rust-probe-$token"
stage_created=0
container_created=0

cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP
    if [ "$container_created" -eq 1 ]; then
        timeout --kill-after=2 "$DOCKER_TIMEOUT" docker rm -f "$container" \
            >/dev/null 2>&1 || true
    fi
    if [ "$stage_created" -eq 1 ]; then
        timeout --kill-after=2 "$DOCKER_TIMEOUT" docker image rm "$stage_image" \
            >/dev/null 2>&1 || true
    fi
    rm -rf "$task_tmp"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

bounded_docker "$task_tmp/build.out" "$task_tmp/build.err" \
    build --tag "$stage_image" "$ESP32_RS"
stage_created=1

read -r -d '' PROBE_PY <<'PY' || true
import json
import subprocess
import sys

def probe(argv):
    completed = subprocess.run(
        argv,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    value = completed.stdout.strip()
    if not value or "\0" in value or len(value) > 16384:
        raise SystemExit("invalid toolchain probe output")
    return value

value = {
    "schema_version": 1,
    "idf_commit": probe(["git", "-C", "/opt/esp/idf", "rev-parse", "--verify", "HEAD"]),
    "rustc_verbose": probe(["rustc", "+esp", "--version", "--verbose"]),
    "target": "xtensa-esp32s3-espidf",
    # Cargo invokes the pinned ldproxy shim; this is the linker identity that
    # the configured Rust build actually selects.
    "linker_version": probe(["ldproxy", "--version"]),
    "esptool_version": probe([sys.executable, "-m", "esptool", "version"]),
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
PY

container_created=1
bounded_docker "$task_tmp/probe.json" "$task_tmp/probe.err" \
    run --name "$container" "$stage_image" python3 -c "$PROBE_PY"
validate_probe "$task_tmp/probe.json" "$component_sha" "$task_tmp/attestation.json"
attestation="$(<"$task_tmp/attestation.json")"
if [ "$(recipe_sha256)" != "$recipe" ] ||
   [ "$(component_lock_sha256)" != "$component_sha" ]; then
    echo "build_image.sh: recipe or component lock changed during image build" >&2
    exit 1
fi

recipe_change="LABEL \"$RECIPE_LABEL\"=\"$recipe\""
attestation_change="$(
    python3 - "$TOOLCHAIN_LABEL" "$attestation" <<'PY'
import sys
key, value = sys.argv[1:]
escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
print(f'LABEL "{key}"="{escaped}"')
PY
)"
bounded_docker "$task_tmp/commit.out" "$task_tmp/commit.err" \
    commit \
    --change "$recipe_change" \
    --change "$attestation_change" \
    "$container" "$IMAGE"

echo "built $IMAGE"
