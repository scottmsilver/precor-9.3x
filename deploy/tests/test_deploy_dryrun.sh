#!/usr/bin/env bash
# Dependency-free tests for deploy.sh planning logic. No Pi/ssh required:
# --dry-run prints the deploy plan from the manifest and performs only a
# read-only /api/status probe (no host mutation, no ssh/rsync).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

out=$(cd "$ROOT" && PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1) \
  || fail "--dry-run must exit 0 (got: $out)"

echo "$out" | grep -q 'examplehost' || fail "--dry-run must show target host"
echo "$out" | grep -q '/usr/local/bin/treadmill_io' \
  || fail "--dry-run must list manifest binary dest"
echo "$out" | grep -q 'treadmill_io .*last' \
  || fail "--dry-run must state treadmill_io restarts last"
# Must NOT attempt ssh/rsync in dry-run. Match BOTH an explicit ssh/rsync
# invocation AND the resolver/connection errors a stray ssh/rsync would emit
# (a real `ssh examplehost` prints "Could not resolve hostname", which the
# literal 'ssh examplehost' pattern alone would miss).
echo "$out" | grep -qiE 'rsync -|ssh examplehost|could not resolve|connection refused|name or service not known' \
  && fail "--dry-run must not execute ssh/rsync (no network mutation)"
pass "--dry-run prints plan, only the read-only status probe"

# Default host is the Zero 2 W (Pi 4 is the spare).
out=$(cd "$ROOT" && bash deploy/deploy.sh --dry-run 2>&1) || fail "default --dry-run failed"
echo "$out" | grep -q 'rpi-zero' || fail "default PI_HOST must be rpi-zero"
pass "default target is rpi-zero"

# Belt-moving refusal: feed a fake status with non-zero speed via the hook.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"speed":2.5}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  || fail "non-zero speed must surface a belt-moving abort in the plan"
pass "belt-moving refusal detected from status"

# SAFETY REGRESSION: in emulate mode the server emits "speed": null while the
# belt moves under emu_speed_mph. Probing only "speed" would false-negative a
# moving belt and let the deploy bounce treadmill_io mid-workout.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"type":"status","emulate":true,"emu_speed":30,"emu_speed_mph":3.0,"speed":null}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  || fail "emulate-mode moving belt (speed:null, emu_speed_mph>0) must still abort"
pass "emulate-mode moving belt detected (speed:null + emu_speed_mph)"
# And a genuinely stopped belt (both zero/null) must NOT abort.
out=$(cd "$ROOT" && DEPLOY_STATUS_OVERRIDE='{"type":"status","emulate":false,"emu_speed_mph":0.0,"speed":0}' \
      PI_HOST=examplehost bash deploy/deploy.sh --dry-run 2>&1)
echo "$out" | grep -qi 'belt is moving' \
  && fail "stopped belt (speed:0, emu_speed_mph:0.0) must NOT abort"
pass "stopped belt does not false-abort"

# INTEGRATION SEAM (Task 2 cross-build must not be discarded): the rewritten
# deploy.sh must NOT 'rm -rf build' in stage() and must NOT build C++ on the
# Pi (no 'make -C cpp' over ssh). The cross-built build/treadmill_io must
# survive staging and reach the Pi via the manifest. Static guard:
grep -qE 'rm[[:space:]]+-rf[[:space:]]+build([[:space:]/]|$)' "$ROOT/deploy/deploy.sh" \
  && fail "deploy.sh stage() must NOT rm -rf build (would delete cross-built binaries)"
grep -qE 'make[[:space:]]+-C[[:space:]]+cpp' "$ROOT/deploy/deploy.sh" \
  && fail "deploy.sh must NOT build C++ on the Pi (cross-build retired build-on-Pi)"
grep -q 'manifest' "$ROOT/deploy/deploy.sh" \
  || fail "deploy.sh must drive install from the shared manifest"
pass "cross-build integration seam intact (no rm -rf build, no on-Pi C++ build, manifest-driven)"

# PI_HOST default must agree with the Makefile (both rpi-zero) so `make deploy`
# and a bare `deploy.sh` target the same host.
grep -qE 'PI_HOST.*:-rpi-zero' "$ROOT/deploy/deploy.sh" \
  || fail "deploy.sh PI_HOST default must be rpi-zero (matches Makefile)"
pass "deploy.sh PI_HOST default agrees with Makefile (rpi-zero)"

echo "ALL TESTS PASSED"
