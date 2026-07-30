#!/usr/bin/env bash
# check_log_contract.sh — assert the EXACT log strings the QEMU harness greps
# are present in the built images, and that the test-only surface is absent
# from production.
#
# The C++ side has no equivalent gate. It exists because this port's whole
# safety argument rests on the harness being able to key on these strings: a
# silently reworded log line would turn a behavioural assertion into a
# vacuous one, and `qemu_smoke.sh` would fail with a confusing timeout rather
# than "you renamed a string".
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESP32_RS="$(cd "$HERE/.." && pwd)"
PROD="$ESP32_RS/build/esp32tap.bin"
TEST="$ESP32_RS/build_qemu_test/esp32tap.bin"

fail() { echo "check_log_contract: FAIL — $*" >&2; exit 1; }

[ -f "$PROD" ] || fail "missing $PROD (run tools/build.sh)"
[ -f "$TEST" ] || fail "missing $TEST (run tools/build.sh)"

# Strings BOTH images must contain (qemu_smoke.sh assertions 1-5, 11-12).
PRODUCTION_STRINGS=(
    "esp32tap phase-1 safety core started (Proxy)"
    "boot state: mode="
    "serial_engine task started (WDT-supervised)"
    "emulate_cycle task started (WDT-supervised)"
    "interval_executor task started (WDT-supervised)"
    "heartbeat uptime="
)

# Strings that must appear ONLY in the test image (S6).
TEST_ONLY_STRINGS=(
    "QTAUDIT"
    "QTSTATE"
    "qemu_test"
    "esp32tap QEMU-TEST build (never flash to hardware)"
)

# Materialise once. `strings ... | grep -q` under `set -o pipefail` fails with
# 141: grep exits on first match and `strings` dies of SIGPIPE.
PROD_STRINGS="$(strings "$PROD")"
TEST_STRINGS="$(strings "$TEST")"

for s in "${PRODUCTION_STRINGS[@]}"; do
    grep -qF -- "$s" <<<"$PROD_STRINGS" || fail "production image missing: $s"
    grep -qF -- "$s" <<<"$TEST_STRINGS" || fail "test image missing: $s"
    echo "check_log_contract: OK — both images contain: $s"
done

for s in "${TEST_ONLY_STRINGS[@]}"; do
    if grep -qF -- "$s" <<<"$PROD_STRINGS"; then
        fail "PRODUCTION image contains test-only string: $s"
    fi
    grep -qF -- "$s" <<<"$TEST_STRINGS" \
        || fail "test image missing its own test-only string: $s (positive control)"
    echo "check_log_contract: OK — test-only, absent from production: $s"
done

echo "check_log_contract: PASS"
