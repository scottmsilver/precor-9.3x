#!/usr/bin/env bash
# Verifies the unified cross toolchain produces a reproducible aarch64 ELF.
# Skips (exit 0 with notice) when Docker is unavailable so unit suites stay green.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker unavailable"; exit 0; }

( cd "$ROOT" && make cross-cpp ) || fail "make cross-cpp failed"
[ -f "$ROOT/build/treadmill_io" ] || fail "build/treadmill_io not produced"
file "$ROOT/build/treadmill_io" | grep -q 'ARM aarch64' \
  || fail "treadmill_io is not an aarch64 ELF: $(file "$ROOT/build/treadmill_io")"
pass "cross build produced an aarch64 treadmill_io"

h1=$(sha256sum "$ROOT/build/treadmill_io" | awk '{print $1}')
( cd "$ROOT" && make cross-cpp ) || fail "second cross build failed"
h2=$(sha256sum "$ROOT/build/treadmill_io" | awk '{print $1}')
[ "$h1" = "$h2" ] || fail "cross build not reproducible: $h1 != $h2"
pass "cross build is reproducible (identical sha256)"

echo "ALL TESTS PASSED"
