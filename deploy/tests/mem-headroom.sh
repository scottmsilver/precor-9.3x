#!/usr/bin/env bash
# 512MB headroom gate. On the Pi (default), drives synthetic load across the
# full family then asserts steady-state MemAvailable >= 40MB and zero
# oom-kill in the journal. --eval evaluates injected MEM_AVAIL_KB/OOM_COUNT
# (pure logic, for tests). --selftest runs internal assertions, no Pi.
set -u
THRESHOLD_KB=$(( 40 * 1024 ))   # 40 MB
PI_HOST="${PI_HOST:-rpi-zero}"
SERVER_PORT="${SERVER_PORT:-8000}"

evaluate() {
  local avail=$1 ooms=$2
  # Fail CLOSED on a non-numeric/empty measurement: a fit gate must never
  # declare "fits" when it could not actually measure (a bad `[ -lt ]` on a
  # non-integer exits 2 → if-false → would silently fall through to PASS).
  case "$avail" in ''|*[!0-9]*) echo "HEADROOM FAIL: MemAvailable not a valid integer (${avail:-empty})"; return 1 ;; esac
  case "$ooms"  in ''|*[!0-9]*) echo "HEADROOM FAIL: oom-count not a valid integer (${ooms:-empty})"; return 1 ;; esac
  if [ "$ooms" -gt 0 ]; then
    echo "HEADROOM FAIL: $ooms oom-kill event(s) in journal"; return 1
  fi
  if [ "$avail" -lt "$THRESHOLD_KB" ]; then
    echo "HEADROOM FAIL: MemAvailable ${avail}kB < ${THRESHOLD_KB}kB (40MB)"; return 1
  fi
  echo "HEADROOM PASS: MemAvailable ${avail}kB, 0 oom-kill"; return 0
}

case "${1:-}" in
  --selftest)
    evaluate 51200 0  >/dev/null || { echo "selftest: 50MB should pass" >&2; exit 1; }
    evaluate 30720 0  >/dev/null && { echo "selftest: 30MB should fail" >&2; exit 1; }
    evaluate 80000 1  >/dev/null && { echo "selftest: oom should fail" >&2; exit 1; }
    echo "selftest OK"; exit 0 ;;
  --eval)
    evaluate "${MEM_AVAIL_KB:-0}" "${OOM_COUNT:-0}"; exit $? ;;
esac

# --- Real run on the Pi ------------------------------------------------------
echo "== mem-headroom: $PI_HOST, full family + synthetic load =="
ssh "$PI_HOST" 'sudo systemctl restart treadmill-io treadmill-server ftms hrm' || {
  echo "could not restart family on $PI_HOST" >&2; exit 1; }
sleep 20
# Synthetic load: one AI chat round-trip + an active run program. FTMS/HRM
# notify on their own once up.
curl -sk --max-time 30 -X POST "https://$PI_HOST:$SERVER_PORT/api/chat" \
  -H 'content-type: application/json' \
  -d '{"message":"start an easy 20 minute run"}' >/dev/null 2>&1 || true
curl -sk --max-time 10 -X POST "https://$PI_HOST:$SERVER_PORT/api/program/start" \
  -H 'content-type: application/json' -d '{}' >/dev/null 2>&1 || true
sleep 30
avail=$(ssh "$PI_HOST" "awk '/MemAvailable/{print \$2}' /proc/meminfo")
ooms=$(ssh "$PI_HOST" "journalctl -k --since '-5 min' 2>/dev/null | grep -c -i 'oom-kill\|Out of memory' || true")
ssh "$PI_HOST" 'curl -sk --max-time 10 -X POST https://localhost:'"$SERVER_PORT"'/api/program/stop -H "content-type: application/json" -d "{}"' >/dev/null 2>&1 || true
echo "MemAvailable=${avail}kB  oom-kill=${ooms}"
evaluate "${avail:-0}" "${ooms:-0}"
