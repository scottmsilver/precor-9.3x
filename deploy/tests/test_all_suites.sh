#!/usr/bin/env bash
# Meta-suite: every dependency-free harness must be green and `make image`
# must exist as a real target.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

for t in test_manifest.sh test_deploy_dryrun.sh test_setup_logic.sh \
         test_service_units.sh test_mem_headroom_selftest.sh test_device_state.sh; do
  bash "$HERE/$t" >/dev/null 2>&1 || fail "$t not green"
  pass "$t green"
done
for t in test_lib.sh test_build_image.sh test_fastboot.sh test_family_bake.sh; do
  bash "$ROOT/provisioning/dietpi/tests/$t" >/dev/null 2>&1 || fail "provisioning/$t not green"
  pass "provisioning/$t green"
done

grep -qE '^image:' "$ROOT/Makefile" || fail "Makefile must define an 'image' target"
pass "make image target exists"

echo "ALL TESTS PASSED"
