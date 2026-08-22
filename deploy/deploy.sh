#!/usr/bin/env bash
set -euo pipefail

# cd to project root (parent of deploy/)
cd "$(dirname "$0")/.."
SCRIPT_DIR="$(pwd)"
LOCK_SCRIPT="$SCRIPT_DIR/scripts/pi-lock.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/deploy/lib-artifacts.sh"

PI_HOST="${PI_HOST:-rpi-zero}"     # Zero 2 W is primary; Pi 4 (rpi) is the spare
PI_DIR="${PI_DIR:-treadmill}"
VENV_DIR="${VENV_DIR:-.venv}"
MANIFEST="$SCRIPT_DIR/deploy/manifest.txt"
SERVER_PORT="${SERVER_PORT:-8000}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
SSH_OPTS=(-o BatchMode=yes -o "ConnectTimeout=$SSH_CONNECT_TIMEOUT")

# State the DEVICE owns and the repo can never regenerate. `rsync --delete`
# deletes whatever isn't in build/, so anything missing from this list is
# destroyed on the next deploy. treadmill.db (plus its -wal/-shm sidecars) holds
# profiles, run history and saved workouts; the JSON files are the pre-SQLite
# layout that server.py still migrates from on first boot of an old device.
# Add to this list whenever the app starts persisting something new on the Pi.
DEVICE_STATE_EXCLUDES=(
  --exclude='treadmill.db' --exclude='treadmill.db-wal' --exclude='treadmill.db-shm'
  --exclude='program_history.json' --exclude='saved_workouts.json'
  --exclude='run_history.json' --exclude='user_profile.json'
  --exclude='hrm_config.json' --exclude='background_advice.json'
)

render_service() {
  # PI_USER only resolved for real runs (needs ssh); dry-run uses a token.
  sed -e "s|@USER@|${PI_USER:-@USER@}|g" \
      -e "s|@DEPLOY_DIR@|$PI_DIR|g" \
      -e "s|@VENV_DIR@|$VENV_DIR|g" "$1"
}

stage() {
  echo "=== Staging build/ (from manifest) ==="
  mkdir -p build/services build/python
  cp python/server.py python/workout_session.py python/program_engine.py \
     python/treadmill_client.py python/hrm_client.py python/workout_db.py \
     python/db.py build/python/
  cp gpio.json pyproject.toml build/
  cp deploy/setup.sh deploy/lib-artifacts.sh deploy/manifest.txt deploy/treadmill.avahi-service build/
  chmod +x build/setup.sh
  for tmpl in deploy/*.service.in; do
    name=$(basename "$tmpl" .in)
    render_service "$tmpl" > "build/services/$name"
  done
  echo "Staged to build/ (binaries come from \`make cross\`)"
}

# Best-effort belt-safety: read-only probe of the live server's /api/status
# (no host mutation). A moving belt aborts unless FORCE=1. Unreachable server
# => warn + proceed (a down server cannot be mid-web-workout; treadmill_io is
# still restarted last+atomically so its safety gap is minimal).
# DEPLOY_STATUS_OVERRIDE lets the test inject a status without a host.
#
# CRITICAL: must check BOTH "speed" AND "emu_speed_mph". In emulate mode the
# server emits "speed": null (no motor reading) while the belt moves under
# emu_speed_mph (server.py build_status). Probing only "speed" would
# false-negative a moving emulate workout and let the deploy bounce
# treadmill_io mid-run. The quote-delimited "speed" token does not collide
# with "emu_speed"/"emu_speed_mph"; the digit-led capture ignores null.
belt_is_moving() {
  local json s key
  if [ -n "${DEPLOY_STATUS_OVERRIDE:-}" ]; then
    json="$DEPLOY_STATUS_OVERRIDE"
  else
    json=$(curl -sk --max-time 5 "https://$PI_HOST:$SERVER_PORT/api/status" 2>/dev/null || true)
  fi
  [ -n "$json" ] || { echo "WARN: could not read /api/status (server down?) — proceeding" >&2; return 1; }
  for key in '"speed"' '"emu_speed_mph"'; do
    s=$(printf '%s' "$json" | sed -n "s/.*$key[[:space:]]*:[[:space:]]*\\([0-9][0-9.]*\\).*/\\1/p")
    [ -n "$s" ] && awk -v v="$s" 'BEGIN{exit (v+0>0)?0:1}' && return 0
  done
  return 1
}

print_plan() {
  echo "=== Deploy plan -> $PI_HOST:~/$PI_DIR (host: $PI_HOST) ==="
  manifest_rows "$MANIFEST" | while IFS=$'\t' read -r kind src dest mode owner; do
    echo "  install $kind  $src  ->  $dest  ($mode $owner)"
  done
  echo "  restart order: treadmill-server, ftms, hrm  THEN  treadmill_io (last, atomic)"
  if belt_is_moving; then
    echo "  ABORT: belt is moving (speed>0) — refusing deploy (use --force to override)"
  fi
}

# Snapshot the device's database BEFORE rsync touches anything. Backups live in
# ~/treadmill-backups, deliberately OUTSIDE ~/treadmill: the deploy rsyncs with
# --delete, so anything inside the deploy dir is one missing --exclude away from
# being erased. Nothing here is ever rsync'd, so --delete cannot reach it.
#
# Uses sqlite3's backup API, not cp: the live DB runs in WAL mode, so the .db
# file alone can be stale or torn while the server is mid-write. The API takes a
# consistent snapshot including the WAL.
backup_device_state() {
  if [ "${SKIP_BACKUP:-0}" = 1 ]; then
    echo "backup: SKIPPED (SKIP_BACKUP=1)"
    return 0
  fi
  echo "=== Backing up device database (pre-deploy) ==="
  # Failure here aborts the deploy on purpose: losing the user's profiles, runs
  # and saved workouts is worse than a deploy that didn't happen.
  ssh "${SSH_OPTS[@]}" "$PI_HOST" bash -s "$PI_DIR" "${KEEP_BACKUPS:-10}" <<'REMOTE' || {
set -eu
umask 077
dir="$HOME/${1:?}"; keep="${2:?}"
db="$dir/treadmill.db"
out_dir="$HOME/treadmill-backups"
if [ ! -f "$db" ]; then
  echo "backup: no treadmill.db on device yet — nothing to back up"
  exit 0
fi
command -v python3 >/dev/null 2>&1 || { echo "backup: python3 missing on device" >&2; exit 1; }
mkdir -p "$out_dir"
chmod 700 "$out_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp=$(mktemp "$out_dir/treadmill-$stamp.XXXXXX.tmp")
trap 'rm -f "$tmp"' EXIT
python3 - "$db" "$tmp" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
result = d.execute("PRAGMA integrity_check").fetchone()
if not result or result[0] != "ok":
    raise RuntimeError(f"backup integrity check failed: {result!r}")
d.close(); s.close()
PY
[ -s "$tmp" ] || { echo "backup: snapshot is empty — refusing to continue" >&2; exit 1; }
chmod 600 "$tmp"
out="${tmp%.tmp}.db"
mv -f -- "$tmp" "$out"
trap - EXIT
# Prune oldest, keep the most recent $keep.
ls -1t "$out_dir"/treadmill-*.db 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
echo "backup: $out ($(wc -c < "$out") bytes, $(ls -1 "$out_dir"/treadmill-*.db | wc -l) kept)"
REMOTE
    echo "REFUSING: could not back up the device database. Fix it, or re-run with SKIP_BACKUP=1 to deploy anyway." >&2
    exit 1
  }
}

deploy_full() {
  manifest_rows "$MANIFEST" >/dev/null    # fail closed before any host contact
  stage
  PI_USER="${PI_USER:-$(ssh "${SSH_OPTS[@]}" "$PI_HOST" whoami)}"
  # Re-render now that PI_USER is resolved (stage() rendered with the @USER@
  # token for --stage-only; here we substitute the real deploy user).
  for tmpl in deploy/*.service.in; do
    name=$(basename "$tmpl" .in); render_service "$tmpl" > "build/services/$name"
  done
  if belt_is_moving && [ "${FORCE:-0}" != 1 ]; then
    echo "REFUSING: belt is moving on $PI_HOST. Stop the belt or set FORCE=1." >&2
    exit 1
  fi
  if [ -x "$LOCK_SCRIPT" ]; then
    source "$LOCK_SCRIPT" acquire "deploy from $(basename "$SCRIPT_DIR")"
  fi
  echo "=== Deploying to $PI_HOST:~/$PI_DIR ==="
  ssh "${SSH_OPTS[@]}" "$PI_HOST" "mkdir -p ~/$PI_DIR"
  backup_device_state
  # Never partial: rsync fully completes before any systemctl.
  # --delete removes anything on the Pi that isn't in build/, so EVERY file the
  # device owns must be excluded here or a deploy destroys it. That is user data
  # (profiles, runs, saved workouts), not rebuildable output.
  rsync -az --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=$SSH_CONNECT_TIMEOUT" \
    --exclude='*.o' --exclude='*.d' --exclude='*.test.o' \
    --exclude='.gemini_key' --exclude='*.pem' \
    "${DEVICE_STATE_EXCLUDES[@]}" \
    --exclude='__pycache__' \
    build/ "$PI_HOST":~/"$PI_DIR"/
  echo "Running setup (manifest install + ordered atomic restart)..."
  ssh "${SSH_OPTS[@]}" "$PI_HOST" "cd ~/$PI_DIR && bash setup.sh"
  echo "Done!  API: https://$PI_HOST:$SERVER_PORT/api/status"
}

# The Gemini API key is a per-device secret: gitignored, and deliberately
# rsync --exclude'd by deploy_full so a normal deploy NEVER clobbers or
# deletes the key already on the Pi. `deploy.sh key` is the one explicit,
# opt-in path that pushes the local ./.gemini_key to the target.
deploy_key() {
  local key="$SCRIPT_DIR/.gemini_key"
  if [ ! -s "$key" ]; then
    echo "REFUSING: no local ./.gemini_key to deploy (expected at $key)." >&2
    echo "Obtain the key (e.g. scp from a working Pi) before running 'deploy.sh key'." >&2
    exit 1
  fi
  echo "=== Deploying Gemini key -> $PI_HOST:~/$PI_DIR/.gemini_key ==="
  ssh "${SSH_OPTS[@]}" "$PI_HOST" "mkdir -p ~/$PI_DIR"
  # scp then tighten perms; the key is owner-only on the device.
  scp "${SSH_OPTS[@]}" -q "$key" "$PI_HOST":~/"$PI_DIR"/.gemini_key
  ssh "${SSH_OPTS[@]}" "$PI_HOST" "chmod 600 ~/$PI_DIR/.gemini_key && sudo systemctl restart treadmill-server"
  echo "Done! Key deployed ($(wc -c < "$key") bytes) and treadmill-server restarted."
}

case "${1:-}" in
  --dry-run)    print_plan ;;
  --stage-only) stage ;;
  key)          deploy_key ;;
  backup)       backup_device_state ;;
  *)            deploy_full ;;
esac
