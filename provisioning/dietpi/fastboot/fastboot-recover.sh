#!/usr/bin/env bash
# Lockout-recovery watchdog. Driven by fastboot-recover.timer (OnBootSec=120,
# OnUnitActiveSec=60): RE-EVALUATES every 60s (not boot-only), checks real
# REACHABILITY (gateway ping). After THRESHOLD consecutive unreachable checks
# it ONCE restores known-good networking + reboots. The /boot/fastboot/.recovered
# marker then SUPPRESSES any further recovery reboots (boot-loop guard) until an
# operator clears it — so a legitimately-down gateway (router off overnight)
# cannot drive an endless restore+reboot loop. Counter in tmpfs (/run).
set -u
ORIG="${FB_ORIG:-/boot/fastboot/interfaces.orig}"
STATE="${FB_STATE:-/run/fastboot-recover.fails}"
MARK="${FB_MARK:-/boot/fastboot/.recovered}"
THRESHOLD="${FB_THRESHOLD:-3}"
DRY="${FB_DRYRUN:-0}"
case "$THRESHOLD" in ''|*[!0-9]*) THRESHOLD=3 ;; esac   # numeric-only, else default
gw(){ ip route 2>/dev/null | awk '/^default/{print $3; exit}'; }
reachable(){ local g; g=$(gw); [ -n "$g" ] || return 1; ping -c1 -W2 "$g" >/dev/null 2>&1; }
if [ "$DRY" = 1 ]; then
  echo "recover: every 60s; if gateway unreachable ${THRESHOLD}x consecutively -> restore $ORIG, disable fastboot-net, reboot ONCE; .recovered then suppresses further reboots (boot-loop guard, exit) until operator clears it"
  exit 0
fi
# Boot-loop guard: we already performed our one recovery. Do NOT reboot again
# on a still-down gateway; that is the environment's problem, not ours.
if [ -f "$MARK" ]; then
  reachable || logger -t fastboot "recover: gateway still unreachable but .recovered set — NOT looping (clear $MARK after fixing the network)"
  exit 0
fi
if reachable; then echo 0 > "$STATE" 2>/dev/null || true; exit 0; fi
n=0; [ -r "$STATE" ] && n=$(cat "$STATE" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1)); echo "$n" > "$STATE" 2>/dev/null || true
logger -t fastboot "recover: gateway unreachable (consecutive=$n/$THRESHOLD)"
if [ "$n" -ge "$THRESHOLD" ]; then
  logger -t fastboot "recover: threshold hit — restoring known-good network + rebooting ONCE"
  [ -f "$ORIG" ] && cp "$ORIG" /etc/network/interfaces
  systemctl disable fastboot-net.service 2>/dev/null || true
  touch "$MARK" 2>/dev/null || true
  systemctl daemon-reexec 2>/dev/null || true
  systemctl reboot
fi
