#!/usr/bin/env bash
# Cached-lease fast path. The foreground applies the cached IP and RETURNS
# IMMEDIATELY (it must not block boot) — validation + correction run in the
# BACKGROUND. Lockout safety comes from (a) the periodic reachability watchdog
# (fastboot-recover.timer, proven to self-heal a post-boot loss) and (b) the
# background dhclient which corrects a wrong/stale lease within seconds. Lease
# fields are influenced by the DHCP server, so each is strictly IPv4-validated
# up front (no network) before any ip(8) call; malformed => slow (blocking)
# dhclient. This keeps the ~9.8s win AND stays lockout-safe.
set -u
IF="${FB_IF:-wlan0}"
LEASE="${FB_LEASE:-/var/lib/dhcp/dhclient.$IF.leases}"
DRY="${FB_DRYRUN:-0}"
last_block(){ awk '/^lease /{b=""} {b=b $0 "\n"} /^}/{last=b} END{printf "%s",last}' "$1" 2>/dev/null; }
val(){ printf '%s\n' "$1" | grep -oE "$2[^;]*" | tail -1 | awk '{print $NF}' | tr -d '";'; }
valid_ipv4(){
  case "$1" in *[!0-9.]*|.*|*.|*..*) return 1;; esac
  local o n=0; local IFS=.
  for o in $1; do
    [ -n "$o" ] || return 1
    [ "$o" -ge 0 ] 2>/dev/null && [ "$o" -le 255 ] || return 1
    n=$((n+1))
  done
  [ "$n" -eq 4 ]
}
valid_mask(){
  valid_ipv4 "$1" || return 1
  local m; m=$(awk -F. '{printf "%d", ($1*16777216)+($2*65536)+($3*256)+$4}' <<<"$1")
  [ "$m" -eq 0 ] && return 0
  local inv=$(( 4294967295 - m )); [ $(( inv & (inv + 1) )) -eq 0 ]
}
mask2cidr(){ awk -F. '{c=0;for(i=1;i<=4;i++){m=$i;while(m){c+=m%2;m=int(m/2)}}print c}' <<<"$1"; }
slow(){ echo "mode=slow ($1)"; [ "$DRY" = 1 ] && exit 0; ip addr flush dev "$IF" 2>/dev/null; exec dhclient -1 "$IF"; }
if [ ! -s "$LEASE" ]; then slow "no lease cache"; fi
B=$(last_block "$LEASE")
IP=$(val "$B" 'fixed-address'); NM=$(val "$B" 'subnet-mask'); GW=$(val "$B" 'option routers')
valid_ipv4 "$IP" || slow "lease IP not valid IPv4"
valid_mask "$NM" || slow "lease netmask invalid/non-contiguous"
valid_ipv4 "$GW" || slow "lease gateway not valid IPv4"
CIDR=$(mask2cidr "$NM")
echo "mode=fast apply $IP/$CIDR gw=$GW (fast path returns immediately; validate+correct in background; watchdog-backed)"
[ "$DRY" = 1 ] && exit 0
ip addr flush dev "$IF" 2>/dev/null
ip addr add "$IP/$CIDR" dev "$IF" && ip link set "$IF" up
ip route replace default via "$GW" dev "$IF"
# Non-blocking: validate the cached IP and correct it in the background. Boot
# is NOT held on this; the periodic watchdog is the lockout backstop.
( v=0; for _ in 1 2 3 4 5 6 7 8; do ping -c1 -W1 "$GW" >/dev/null 2>&1 && { v=1; break; }; sleep 0.5; done; if [ "$v" != 1 ]; then logger -t fastboot "fastpath: cached lease unreachable — falling back to dhclient"; ip addr flush dev "$IF" 2>/dev/null; dhclient -1 "$IF" >/dev/null 2>&1; else dhclient -1 "$IF" >/dev/null 2>&1; ip addr show "$IF" | grep -q "$IP/" || logger -t fastboot "lease changed, dhclient reconfigured"; fi ) >/dev/null 2>&1 &
exit 0
