#!/usr/bin/env bash
# Static guarantees for the rendered service units + the single-worker
# trim-ladder invariant.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

IO="$ROOT/deploy/treadmill-io.service.in"
grep -qE '^After=local-fs.target' "$IO" \
  || fail "treadmill-io must stay network-independent (After=local-fs.target)"
grep -qiE '^(After|Wants|Requires)=.*network' "$IO" \
  && fail "treadmill-io must NOT depend on the network (Path A)"
grep -q 'WantedBy=.*treadmill-critical.target' "$IO" \
  || fail "treadmill-io must declare WantedBy treadmill-critical.target (Path A slot)"
pass "treadmill-io.service.in wired to Path A, network-independent"

# Trim ladder step 2: server must run a SINGLE uvicorn worker.
grep -qE 'uvicorn\.run\(app, host=.*port=port' "$ROOT/python/server.py" \
  || fail "server.py uvicorn.run signature changed — re-verify worker count"
grep -qE 'uvicorn[^#]*workers\s*=\s*[2-9]|workers\s*=\s*[2-9][^#]*\)\s*$' "$ROOT/python/server.py" \
  && fail "server.py must NOT pass uvicorn workers>=2 (512MB trim ladder)"
pass "server.py is single-uvicorn-worker (trim ladder step 2)"

echo "ALL TESTS PASSED"
