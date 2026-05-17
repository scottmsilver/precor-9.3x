#!/usr/bin/env bash
# Canonical time-to-SSH measurement rig. 3 reboot cycles by default.
# Authoritative metric: Pi-side /proc/uptime at first successful SSH key-auth.
# Robust: a cycle counts only if the Pi actually rebooted (post-uptime < pre
# AND < 600s). Failed-reboot cycles are retried once then excluded from the mean.
set -u
HOST="${FB_HOST:-192.168.1.206}"
KEY="${FB_KEY:-$HOME/.ssh/id_ed25519}"
CYCLES="${FB_CYCLES:-3}"
SSHO="-i $KEY -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
now(){ date +%s.%N; }
d(){ awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
piuptime(){ ssh $SSHO -o ConnectTimeout=4 root@"$HOST" 'cut -d" " -f1 /proc/uptime' 2>/dev/null; }
do_reboot(){ ssh $SSHO root@"$HOST" 'nohup sh -c "sleep 1; systemctl reboot" >/dev/null 2>&1 &' >/dev/null 2>&1; }
if [ "${1:-}" = "--selftest" ]; then
  t=$(d 10 12.5); [ "$t" = "2.5" ] || { echo "selftest math FAIL ($t)"; exit 1; }
  [ -n "$HOST" ] && [ -n "$KEY" ] && [ "$CYCLES" -ge 1 ] || { echo "selftest cfg FAIL"; exit 1; }
  echo "selftest OK"; exit 0
fi
label="${1:-unlabeled}"
echo "## measure: $label  ($(date -u +%FT%TZ))  host=$HOST cycles=$CYCLES"
sum=0; n=0; bad=0; min=; max=
for c in $(seq 1 "$CYCLES"); do
  ok=0
  for attempt in 1 2; do
    pre=$(piuptime); [ -z "$pre" ] && { echo "  cycle $c attempt $attempt: PRE-uptime unreachable"; sleep 5; continue; }
    do_reboot
    T0=$(now)
    # wait for DOWN (port 22 stops answering) - max 70s
    ds=$(now); down=0
    while timeout 2 bash -c "exec 3<>/dev/tcp/$HOST/22" 2>/dev/null; do
      [ "$(awk -v a=$ds -v b=$(now) 'BEGIN{print (b-a>70)?1:0}')" = 1 ] && break
      sleep 0.5
    done
    timeout 2 bash -c "exec 3<>/dev/tcp/$HOST/22" 2>/dev/null || down=1
    if [ "$down" != 1 ]; then echo "  cycle $c attempt $attempt: reboot not observed (no down) — retrying"; continue; fi
    # wait for port back - max 225s
    while ! timeout 2 bash -c "exec 3<>/dev/tcp/$HOST/22" 2>/dev/null; do
      [ "$(awk -v a=$T0 -v b=$(now) 'BEGIN{print (b-a>225)?1:0}')" = 1 ] && break
      sleep 0.25
    done
    # wait for key-auth + read boot-side uptime - max 60s
    bu=""; ae=$(now)
    while :; do
      bu=$(piuptime); [ -n "$bu" ] && break
      [ "$(awk -v a=$ae -v b=$(now) 'BEGIN{print (b-a>60)?1:0}')" = 1 ] && break
      sleep 0.25
    done
    # validate: actually rebooted (post < pre) AND sane (< 600s)
    valid=$(awk -v b="${bu:-}" -v p="$pre" 'BEGIN{ if (b=="" || b+0!=b) {print 0} else if (b<p && b<600) {print 1} else {print 0} }')
    if [ "$valid" = 1 ]; then
      echo "  cycle $c: boot-side-uptime-at-ssh=${bu}s (pre=${pre}s)"
      sum=$(awk -v s=$sum -v x=$bu 'BEGIN{print s+x}'); n=$((n+1))
      min=$(awk -v m="${min:-$bu}" -v x=$bu 'BEGIN{print (x<m)?x:m}')
      max=$(awk -v m="${max:-$bu}" -v x=$bu 'BEGIN{print (x>m)?x:m}')
      ok=1; break
    else
      echo "  cycle $c attempt $attempt: INVALID sample (bu=${bu:-none} pre=${pre}) — retrying"
    fi
  done
  [ "$ok" = 1 ] || { bad=$((bad+1)); echo "  cycle $c: FAILED (excluded)"; }
  sleep 5
done
if [ "$n" -gt 0 ]; then
  echo "  RESULT $label: mean=$(awk -v s=$sum -v n=$n 'BEGIN{printf "%.1f", s/n}')s min=${min}s max=${max}s valid=$n bad=$bad"
else
  echo "  RESULT $label: NO VALID CYCLES (bad=$bad) — measurement inconclusive"
fi
ssh $SSHO root@"$HOST" 'systemd-analyze 2>/dev/null; echo "--chain--"; systemd-analyze critical-chain ssh.service 2>/dev/null | head -8; echo "--pathA--"; journalctl -b -u fastboot-probe.service --no-pager 2>/dev/null | tail -2' 2>/dev/null | sed 's/^/  /'
