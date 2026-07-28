#!/usr/bin/env bash
# run_harness.sh — run the COMMITTED QEMU behavioral harness (S1-S7 + encoder
# parity) against the RUST image.
#
# The harness itself is NOT copied or forked: `ESP32TAP_FW_DIR` simply points
# its build/ and build_qemu_test/ lookups at esp32_rs/. Every assertion,
# timeout, bound and comparison is the committed one.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESP32_RS="$(cd "$HERE/.." && pwd)"
HARNESS="$ESP32_RS/../esp32/tools/qemu_harness"

[ -f "$ESP32_RS/build_qemu_test/esp32tap.bin" ] || {
    echo "build_qemu_test/esp32tap.bin missing — run tools/build.sh first" >&2
    exit 2
}

export ESP32TAP_FW_DIR="$ESP32_RS"
export IDF_IMAGE="${IDF_IMAGE:-esp32tap-rust:build}"

# Rust-image-only extra scenarios (S8 normal exit). They live OUTSIDE the
# committed harness so that directory stays byte-identical to HEAD, and they
# import it as a library. Run FIRST so a failure there is not buried under the
# 10-minute S1-S7 run. RS_SCENARIOS=0 to skip.
if [ "${RS_SCENARIOS:-1}" = "1" ] && [ -z "${1:-}" ]; then
    echo "run_harness.sh: Rust-only scenarios (tools/qemu_scenarios) ..."
    ( cd "$HERE/qemu_scenarios" && exec python3 -m pytest . -v -p no:cacheprovider )
fi

cd "$HARNESS"
# `-m "not net"` by default: test_net_scenarios.py belongs to the UNCOMMITTED
# C++ network tier, which this port deliberately does not include (the Rust
# image is the safety core only). Its `net_qemu` fixture boots the image with
# an openeth NIC and expects an HTTPS server, so those cases can only fail
# here — and a red run for an out-of-scope reason hides the in-scope result.
# Everything the mandate covers (S1, S2a, S2b, S3, S4, S5, S6 x2, S7a, S7b +
# the 6 encoder-parity cases) is selected. Pass -m to override.
exec python3 -m pytest -m "not net" "$@" -p no:cacheprovider
