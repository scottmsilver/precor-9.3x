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

# --- Execute the real backup path through a fake ssh transport --------------
# The fake transport runs deploy.sh's remote heredoc locally with an isolated
# HOME. This tests behavior without a Pi or network while retaining the exact
# ssh argument and remote-shell contract used in production.
DEPLOY="$ROOT/deploy/deploy.sh"
fake_bin="$tmp/bin"; remote_home="$tmp/remote-home"; ssh_log="$tmp/ssh.log"
mkdir -p "$fake_bin" "$remote_home/treadmill"
cat > "$fake_bin/ssh" <<'SH'
#!/usr/bin/env bash
set -eu
printf '<%s>\n' "$@" >> "${FAKE_SSH_LOG:?}"
[ "${FAKE_SSH_FAIL:-0}" != 1 ] || exit 77
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) shift 2 ;;
    *) shift; break ;;
  esac
done
HOME="${FAKE_REMOTE_HOME:?}" "$@"
SH
cat > "$fake_bin/date" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = -u ]; then
  echo 20260102T030405Z
else
  exec /bin/date "$@"
fi
SH
chmod +x "$fake_bin/ssh" "$fake_bin/date"

# Keep a writer connected so treadmill.db remains a live WAL database while
# the backup API reads it. The committed WAL row must appear in the snapshot.
ready="$tmp/wal-ready"; stop_writer="$tmp/stop-writer"
python3 - "$remote_home/treadmill/treadmill.db" "$ready" "$stop_writer" <<'PY' &
import os, sqlite3, sys, time
db, ready, stop = sys.argv[1:]
conn = sqlite3.connect(db)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA wal_autocheckpoint=0")
conn.execute("CREATE TABLE precious(value TEXT)")
conn.execute("INSERT INTO precious VALUES ('from-live-wal')")
conn.commit()
open(ready, "w").close()
while not os.path.exists(stop):
    time.sleep(0.05)
conn.close()
PY
writer_pid=$!
trap 'touch "$stop_writer" 2>/dev/null || true; wait "$writer_pid" 2>/dev/null || true; rm -rf "$tmp"' EXIT
for _ in $(seq 1 100); do [ -f "$ready" ] && break; sleep 0.05; done
[ -f "$ready" ] || fail "WAL writer did not become ready"
[ -s "$remote_home/treadmill/treadmill.db-wal" ] || fail "test database is not live in WAL mode"

run_backup() {
  PATH="$fake_bin:$PATH" FAKE_SSH_LOG="$ssh_log" FAKE_REMOTE_HOME="$remote_home" \
    PI_HOST=fake-pi PI_DIR=treadmill KEEP_BACKUPS=2 bash "$DEPLOY" backup
}

run_backup >/dev/null || fail "backup subcommand failed against a live WAL database"
backup_dir="$remote_home/treadmill-backups"
[ -d "$backup_dir" ] || fail "backup directory was not created outside deploy dir"
[ ! -e "$remote_home/treadmill/treadmill-backups" ] || fail "backup landed inside deploy directory"
[ "$(stat -c %a "$backup_dir")" = 700 ] || fail "backup directory mode is not 700"
first_backup=$(find "$backup_dir" -maxdepth 1 -name 'treadmill-*.db' -print -quit)
[ -n "$first_backup" ] || fail "backup snapshot was not created"
[ "$(stat -c %a "$first_backup")" = 600 ] || fail "backup snapshot mode is not 600"
python3 - "$first_backup" <<'PY' || fail "snapshot is unreadable, corrupt, or missing WAL data"
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert conn.execute("SELECT value FROM precious").fetchone()[0] == "from-live-wal"
conn.close()
PY
pass "live WAL database produces a readable integrity-checked snapshot"
pass "backup directory and snapshot use modes 700 and 600"
pass "backups live outside the deploy dir, beyond --delete's reach"

# A fixed fake timestamp forces all calls into the same second. Unique temp
# names plus atomic rename must still create distinct snapshots; retention then
# keeps only the newest two.
run_backup >/dev/null || fail "second same-timestamp backup failed"
run_backup >/dev/null || fail "third same-timestamp backup failed"
[ "$(find "$backup_dir" -maxdepth 1 -name 'treadmill-*.db' | wc -l)" -eq 2 ] \
  || fail "same-second uniqueness or KEEP_BACKUPS=2 retention failed"
[ -z "$(find "$backup_dir" -maxdepth 1 -name '*.tmp' -print -quit)" ] \
  || fail "partial temporary snapshot remained after success"
pass "same-second snapshots are unique, atomic, and retention is enforced"

# Backup failure must stop the command; SKIP_BACKUP must avoid ssh entirely.
if PATH="$fake_bin:$PATH" FAKE_SSH_LOG="$ssh_log" FAKE_REMOTE_HOME="$remote_home" \
     FAKE_SSH_FAIL=1 PI_HOST=fake-pi PI_DIR=treadmill bash "$DEPLOY" backup >/dev/null 2>&1; then
  fail "backup transport failure did not fail closed"
fi
pass "backup failure aborts fail-closed"

: > "$ssh_log"
PATH="$fake_bin:$PATH" FAKE_SSH_LOG="$ssh_log" FAKE_REMOTE_HOME="$remote_home" \
  SKIP_BACKUP=1 PI_HOST=fake-pi PI_DIR=treadmill bash "$DEPLOY" backup >/dev/null \
  || fail "SKIP_BACKUP=1 failed"
[ ! -s "$ssh_log" ] || fail "SKIP_BACKUP=1 still contacted ssh"
pass "SKIP_BACKUP=1 bypasses backup transport"

# Every remote command in this deploy workflow shares fail-fast SSH options.
run_backup >/dev/null || fail "backup failed while checking ssh options"
grep -q '<-o>' "$ssh_log" || fail "ssh options were not passed"
grep -q '<BatchMode=yes>' "$ssh_log" || fail "ssh is not fail-fast noninteractive"
grep -Eq '<ConnectTimeout=[1-9][0-9]*>' "$ssh_log" || fail "ssh has no bounded connect timeout"
grep -q 'SSH_OPTS' "$DEPLOY" || fail "ssh/scp options are not centralized"
unsafe_remote_calls=$(grep -nE '^[[:space:]]*(ssh|scp)[[:space:]]|\$\((ssh|scp)[[:space:]]' "$DEPLOY" \
  | grep -v 'SSH_OPTS' || true)
[ -z "$unsafe_remote_calls" ] || fail "ssh/scp call bypasses SSH_OPTS: $unsafe_remote_calls"
grep -q 'rsync .*BatchMode=yes.*ConnectTimeout=' "$DEPLOY" \
  || fail "rsync remote shell is not noninteractive with a bounded timeout"
pass "ssh/scp workflow is noninteractive with bounded connection timeout"

# The deploy must still invoke backup before its destructive rsync.
backup_line=$(grep -n '^[[:space:]]*backup_device_state[[:space:]]*$' "$DEPLOY" | head -1 | cut -d: -f1)
rsync_line=$(grep -n 'rsync -az --delete' "$DEPLOY" | head -1 | cut -d: -f1)
[ -n "$backup_line" ] && [ -n "$rsync_line" ] && [ "$backup_line" -lt "$rsync_line" ] \
  || fail "backup must run before rsync --delete"
pass "device DB is backed up before rsync --delete runs"

echo "ALL TESTS PASSED"
