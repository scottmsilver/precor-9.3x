#!/usr/bin/env bash
# Regression: `rsync -az --delete build/ -> ~/treadmill/` must never delete the
# state the DEVICE owns. treadmill.db (profiles, run history, saved workouts) is
# not in build/, so without an --exclude for it --delete wipes the user's data on
# every deploy. Runs rsync for real against two temp dirs — no Pi, no ssh.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "ok: $1"; }

command -v rsync >/dev/null 2>&1 || { echo "SKIP: rsync not installed"; exit 0; }

# Pull the exclude list out of the real deploy.sh rather than restating it, so a
# future edit that drops an entry fails here instead of silently passing.
eval "$(sed -n '/^DEVICE_STATE_EXCLUDES=(/,/^)/p' "$ROOT/deploy/deploy.sh")"
[ "${#DEVICE_STATE_EXCLUDES[@]}" -gt 0 ] \
  || fail "DEVICE_STATE_EXCLUDES not found in deploy.sh (did it get renamed?)"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
src="$tmp/build"; dst="$tmp/pi"
mkdir -p "$src/python" "$dst/python"

# What a deploy ships. Content must differ in SIZE from the device's copy —
# rsync's default quick check is size+mtime, so same-size files written in the
# same second would be skipped and the "still updates" assertion would misfire.
echo "new-shipped-content" > "$src/python/server.py"
echo "new-shipped-content" > "$src/gpio.json"

# What the device already has: shipped files, device state, and one stale
# artifact that --delete SHOULD remove.
echo "old" > "$dst/python/server.py"
echo "old" > "$dst/gpio.json"
DEVICE_FILES=(
  treadmill.db treadmill.db-wal treadmill.db-shm
  program_history.json saved_workouts.json run_history.json user_profile.json
  hrm_config.json background_advice.json
  .gemini_key cert.pem key.pem
)
for f in "${DEVICE_FILES[@]}"; do echo "PRECIOUS-$f" > "$dst/$f"; done
echo "stale" > "$dst/removed_last_release.py"

rsync -a --delete \
  --exclude='*.o' --exclude='*.d' --exclude='*.test.o' \
  --exclude='.gemini_key' --exclude='*.pem' \
  "${DEVICE_STATE_EXCLUDES[@]}" \
  --exclude='__pycache__' \
  "$src/" "$dst/" || fail "rsync failed"

for f in "${DEVICE_FILES[@]}"; do
  [ -f "$dst/$f" ] || fail "deploy DELETED device state: $f"
  [ "$(cat "$dst/$f")" = "PRECIOUS-$f" ] || fail "deploy CLOBBERED device state: $f"
done
pass "deploy preserves all device-owned state (db, sidecars, json, key, certs)"

# The exclusions must not defeat --delete for genuinely stale shipped code.
[ -f "$dst/removed_last_release.py" ] && fail "--delete no longer prunes stale shipped files"
pass "--delete still prunes stale shipped files"

# And shipped files must still update.
[ "$(cat "$dst/python/server.py")" = "new-shipped-content" ] || fail "shipped file not updated"
pass "shipped files still update"

# --- The pre-deploy backup is the second line of defence -------------------
# An exclude list only protects files someone remembered to name. The backup
# protects the ones nobody thought of, so it must run BEFORE rsync and must land
# outside the deploy dir (anything inside it is in --delete's blast radius).
DEPLOY="$ROOT/deploy/deploy.sh"

grep -q 'backup_device_state' "$DEPLOY" || fail "deploy.sh must back up device state"

backup_line=$(grep -n '^\s*backup_device_state\s*$' "$DEPLOY" | head -1 | cut -d: -f1)
rsync_line=$(grep -n 'rsync -az --delete' "$DEPLOY" | head -1 | cut -d: -f1)
[ -n "$backup_line" ] || fail "backup_device_state is never invoked in the deploy path"
[ -n "$rsync_line" ] || fail "could not find the deploy rsync"
[ "$backup_line" -lt "$rsync_line" ] \
  || fail "backup must run BEFORE rsync --delete (backup:$backup_line rsync:$rsync_line)"
pass "device DB is backed up before rsync --delete runs"

grep -q 'out_dir="$HOME/treadmill-backups"' "$DEPLOY" \
  || fail "backups must be written to \$HOME/treadmill-backups"
grep -q 'rsync.*treadmill-backups' "$DEPLOY" \
  && fail "backup dir must never be an rsync source/dest (--delete would reach it)"
pass "backups live outside the deploy dir, beyond --delete's reach"

# cp of a WAL-mode DB can be stale or torn; the sqlite backup API cannot.
grep -q 's.backup(d)' "$DEPLOY" || fail "backup must use the sqlite3 backup API, not cp"
pass "backup uses sqlite3's consistent-snapshot API (WAL-safe)"

echo "ALL TESTS PASSED"
