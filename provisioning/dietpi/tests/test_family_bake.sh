#!/usr/bin/env bash
# The bake path must carry the full software family and install it on first
# boot from the manifest. Dependency-free; no SD/Pi.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
PREP="$ROOT/provisioning/dietpi/prepare-sd.sh"
ACS="$ROOT/provisioning/dietpi/Automation_Custom_Script.sh"
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

grep -qE 'family\.tgz|build/' "$PREP" \
  || fail "prepare-sd.sh must stage the built family (build/) into the image"
pass "prepare-sd.sh stages the family payload"

# First-boot script installs the family via the shared manifest, idempotently,
# and reuses the audited safe-extract guard (no absolute/.. members).
grep -q 'family.tgz' "$ACS" || fail "Automation_Custom_Script must unpack family.tgz"
grep -q 'setup.sh' "$ACS"   || fail "first-boot install must run the manifest-driven setup.sh"
# Audited unsafe-path guard retained for the family extract (fixed substring,
# same guard the fast-boot fold-back uses): refuse absolute / '..' members.
grep -F "tzf \"\$FW/family.tgz\" 2>/dev/null | grep -qE '^/|(^|/)\\.\\.(/|\$)'" "$ACS" >/dev/null \
  || fail "first-boot family extract must keep the audited unsafe-path guard"
grep -q '.family.applied' "$ACS" \
  || fail "first-boot family install must be idempotent (applied marker)"
grep -q 'refusing family.tgz with symlink members' "$ACS" \
  || fail "first-boot family extract must also reject symlink members (execute-path hardening)"
pass "first-boot install: manifest-driven, idempotent, safe-extract + symlink-reject"

# dash-safe (DietPi runs Automation_Custom_Script.sh under /bin/dash).
command -v dash >/dev/null 2>&1 && { dash -n "$ACS" || fail "ACS not dash-safe"; }
pass "Automation_Custom_Script.sh is dash-safe"

echo "ALL TESTS PASSED"
