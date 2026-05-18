#!/usr/bin/env bash
# Dependency-free checks of setup.sh's static guarantees (no Pi needed):
# it must be manifest-driven, wire Path A, set a zram margin, and restart
# treadmill-io LAST.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
S="$HERE/../setup.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

bash -n "$S" || fail "setup.sh has a syntax error"
pass "setup.sh parses"

grep -q 'lib-artifacts.sh' "$S" || fail "setup.sh must source lib-artifacts.sh"
grep -q 'manifest_rows' "$S"    || fail "setup.sh must install from the manifest"
pass "setup.sh is manifest-driven"

# Payload is build/ flattened into setup.sh's dir: src must have the
# staging-root 'build/' prefix stripped, missing src must hard-fail, and
# rows already in place (app tree) must be identity-skipped (not self-copied).
grep -qE '\$\{src#build/\}' "$S" \
  || fail "setup.sh must strip the 'build/' staging-root prefix from manifest src"
grep -qE 'manifest src missing in payload' "$S" \
  || fail "setup.sh must hard-fail when a manifest src is absent from the payload"
grep -qE 'realpath .*-- "\$srcfile"' "$S" && grep -qE 'already in place' "$S" \
  || fail "setup.sh must identity-skip rows already placed by the payload flatten"
pass "setup.sh resolves payload-relative src, fails closed on missing, skips identity"

# OS runtime prereqs: bare DietPi lacks python3 + libpigpio1 (treadmill_io's
# runtime .so); setup.sh must install them (idempotently) before venv/restart.
grep -q 'OS runtime prerequisites' "$S" \
  || fail "setup.sh must install OS runtime prerequisites (python3/libpigpio1)"
grep -q 'libpigpio1' "$S" \
  || fail "setup.sh must ensure libpigpio1 (treadmill_io links libpigpio.so.1)"
grep -qE 'command -v python3 .*\|\| need=' "$S" \
  || fail "setup.sh must install python3 when absent (server venv)"
pass "setup.sh installs OS prereqs (python3 + libpigpio1) idempotently"

grep -q 'add-wants treadmill-critical.target' "$S" \
  || fail "setup.sh must wire treadmill_io into Path A (treadmill-critical.target)"
pass "Path A wiring present"

grep -qE 'zram' "$S" || fail "setup.sh must enable a zram thin margin (trim ladder step 4)"
pass "zram thin margin present"

# treadmill-io restarted LAST: its restart line must come after the others.
awk '
  /systemctl restart treadmill-server|systemctl restart ftms|systemctl restart hrm/ {others=NR}
  /systemctl restart treadmill-io( |$)/ {io=NR}
  END { exit (io>others && others>0)?0:1 }
' "$S" || fail "treadmill-io must be restarted AFTER server/ftms/hrm"
pass "treadmill-io restarts last (atomic, minimal safety-daemon downtime)"

echo "ALL TESTS PASSED"
