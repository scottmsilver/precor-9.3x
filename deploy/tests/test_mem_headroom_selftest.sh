#!/usr/bin/env bash
# Exercises mem-headroom.sh's pure logic with fixtures (no Pi).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
M="$HERE/mem-headroom.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

bash "$M" --selftest >/dev/null 2>&1 || fail "mem-headroom --selftest must pass"
pass "mem-headroom selftest"

# 50MB available, no oom => PASS (threshold 40MB)
out=$(MEM_AVAIL_KB=51200 OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM PASS' || fail "50MB/no-oom must PASS (got: $out)"
pass "50MB free, no oom => PASS"

# 30MB available => FAIL
out=$(MEM_AVAIL_KB=30720 OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "30MB must FAIL (<40MB)"
pass "30MB free => FAIL"

# oom-kill present => FAIL even with memory free
out=$(MEM_AVAIL_KB=80000 OOM_COUNT=1 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "any oom-kill must FAIL"
pass "oom-kill => FAIL regardless of free memory"

# non-numeric measurement must FAIL-CLOSED (a fit gate must never declare
# "fits" when it could not actually measure — no fail-open).
out=$(MEM_AVAIL_KB=garbage OOM_COUNT=0 bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "non-numeric MemAvailable must FAIL-CLOSED (got: $out)"
pass "non-numeric MemAvailable => FAIL (no fail-open)"
out=$(MEM_AVAIL_KB=51200 OOM_COUNT=xx bash "$M" --eval 2>&1)
echo "$out" | grep -q 'HEADROOM FAIL' || fail "non-numeric oom-count must FAIL-CLOSED (got: $out)"
pass "non-numeric oom-count => FAIL (no fail-open)"

echo "ALL TESTS PASSED"
