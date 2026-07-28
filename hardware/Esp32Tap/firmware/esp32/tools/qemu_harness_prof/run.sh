#!/usr/bin/env bash
# run.sh — single CI entrypoint for the Esp32Tap QEMU behavioral harness.
#
# 1. Builds the DEFAULT image (build/) and the ESP32TAP_QEMU_TEST image
#    (build_qemu_test/) via the pinned espressif/idf docker toolchain,
#    skipping a build when its esp32tap.bin is newer than the firmware
#    sources (FORCE_BUILD=1 overrides).
# 2. Runs the full harness: pytest -m qemu (S1..S5 + S7 behavioral
#    scenarios on the test image, S6 default-build smoke + production
#    strings gate) plus the unmarked encoder-parity tests.
#
# Env: IDF_IMAGE (default espressif/idf:release-v5.5 — keep pinned)
#      FORCE_BUILD=1 to rebuild both images unconditionally
#
# Note: builds use `idf.py -B <dir> ... build` WITHOUT set-target — the
# committed sdkconfig already pins esp32s3, and set-target would rewrite
# sdkconfig (dirtying the tree) on every run.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
ESP32_DIR="$(cd "$HARNESS_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$ESP32_DIR/../../../.." && pwd)"
REL="${ESP32_DIR#"$REPO_ROOT"/}"
IDF_IMAGE="${IDF_IMAGE:-espressif/idf:release-v5.5}"

build_image() { # build_image <build_dir> [extra idf.py -D flags...]
    local bdir="$1"; shift
    local binary="$ESP32_DIR/$bdir/esp32tap.bin"
    if [ "${FORCE_BUILD:-0}" != "1" ] && [ -f "$binary" ]; then
        local newer
        newer=$(find "$ESP32_DIR/main" "$ESP32_DIR/components" \
            \( -name '*.cpp' -o -name '*.h' -o -name '*.hpp' \
               -o -name 'CMakeLists.txt' \) -newer "$binary" 2>/dev/null \
            | head -1)
        if [ -z "$newer" ]; then
            echo "run.sh: $bdir up to date (FORCE_BUILD=1 to rebuild)"
            return
        fi
    fi
    echo "run.sh: building $bdir ..."
    docker run --rm -v "$REPO_ROOT":/project -w "/project/$REL" \
        "$IDF_IMAGE" idf.py -B "$bdir" "$@" build
}

build_image build
build_image build_qemu_test -DESP32TAP_QEMU_TEST=1

echo "run.sh: running behavioral harness ..."
python3 -m pytest "$HARNESS_DIR" -m qemu -v
echo "run.sh: running encoder parity tests ..."
python3 -m pytest "$HARNESS_DIR/test_encoders.py" -q
echo "run.sh: ALL GREEN"
