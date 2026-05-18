#!/usr/bin/env bash
#
# find-pis.sh — discover Raspberry Pis on the local network.
#
# No dependencies beyond coreutils + iproute2 (ip, ping, getent). No root
# required: it does a parallel ping sweep to populate the kernel neighbor
# table, then identifies Pis by TWO independent signals:
#
#   1. MAC vendor  — looked up in the system IEEE OUI database
#                    (/var/lib/ieee-data/oui.txt) when present, else a
#                    small built-in table of known Raspberry Pi OUIs.
#   2. Hostname    — reverse-DNS/mDNS name matching a Pi/DietPi pattern.
#
# Signal 2 matters: a Pi behind a USB Wi-Fi dongle, with a randomized MAC,
# or whose OUI predates the local IEEE snapshot has a NON-Pi MAC. Matching
# on hostname catches those (e.g. a DietPi-provisioned Zero named rpi-zero).
#
# Usage:
#   ./find-pis.sh                 # scan the /24 of your default route
#   ./find-pis.sh 192.168.4.0/24  # scan a specific /24
#   ./find-pis.sh --all           # list every host found, not just Pis
#
set -euo pipefail

# Fallback OUI table — used only when the IEEE database is unavailable.
declare -A PI_OUI=(
  ["b8:27:eb"]="Raspberry Pi Foundation"
  ["dc:a6:32"]="Raspberry Pi Trading Ltd"
  ["e4:5f:01"]="Raspberry Pi Trading Ltd"
  ["28:cd:c1"]="Raspberry Pi Trading Ltd"
  ["d8:3a:dd"]="Raspberry Pi Ltd"
  ["2c:cf:67"]="Raspberry Pi Ltd"
)
IEEE_DB=""
for f in /var/lib/ieee-data/oui.txt /usr/share/ieee-data/oui.txt; do
  [[ -f "$f" ]] && { IEEE_DB="$f"; break; }
done

# Hostnames that strongly imply a Pi / DietPi image.
HOST_RE='raspberry|raspberrypi|(^|[^a-z])rpi([^a-z]|$)|dietpi|pi-?zero|pizero'

SHOW_ALL=0
SUBNET_ARG=""
for arg in "$@"; do
  case "$arg" in
    --all) SHOW_ALL=1 ;;
    -h|--help) sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) SUBNET_ARG="$arg" ;;
  esac
done

# Resolve a MAC's vendor: IEEE DB first, then built-in table, else "".
vendor_of() {
  local mac="$1" oui6 hit
  oui6="$(echo "$mac" | tr -d ':' | cut -c1-6 | tr 'a-z' 'A-Z')"
  if [[ -n "$IEEE_DB" ]]; then
    hit="$(grep -m1 -i "^${oui6}[[:space:]]*(base 16)" "$IEEE_DB" 2>/dev/null \
           | sed 's/.*(base 16)[[:space:]]*//')"
    [[ -n "$hit" ]] && { echo "$hit"; return; }
  fi
  echo "${PI_OUI[$(echo "$mac" | cut -d: -f1-3 | tr 'A-Z' 'a-z')]:-}"
}

# Determine the /24 to scan.
if [[ -n "$SUBNET_ARG" ]]; then
  prefix="$(echo "${SUBNET_ARG%%/*}" | cut -d. -f1-3)"   # 192.168.4.0/24 -> 192.168.4
else
  src_ip="$(ip -o -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || true)"
  if [[ -z "$src_ip" ]]; then
    echo "Could not determine local IP. Pass a subnet, e.g. ./find-pis.sh 192.168.1.0/24" >&2
    exit 1
  fi
  prefix="$(echo "$src_ip" | cut -d. -f1-3)"
fi

echo "Scanning ${prefix}.0/24 for Raspberry Pis ..." >&2

# Parallel ping sweep to seed the neighbor table (1 packet, 1s timeout).
for i in $(seq 1 254); do
  ping -c1 -W1 "${prefix}.${i}" >/dev/null 2>&1 &
done
wait

printf '\n%-15s  %-17s  %-22s  %-26s  %s\n' "IP" "MAC" "HOSTNAME" "VENDOR" "WHY"
printf '%-15s  %-17s  %-22s  %-26s  %s\n' "---------------" "-----------------" \
       "----------------------" "--------------------------" "---"

found=0
# ip neigh: 192.168.1.42 dev eth0 lladdr b8:27:eb:12:34:56 REACHABLE
while read -r ip _ _ _ mac state; do
  [[ -z "${mac:-}" || "$mac" == "FAILED" ]] && continue
  [[ "$ip" != ${prefix}.* ]] && continue

  host="$(getent hosts "$ip" | awk '{print $2}' | head -n1)"
  [[ -z "$host" ]] && host="-"
  vendor="$(vendor_of "$mac")"

  why=""
  [[ "$vendor" =~ [Rr]aspberry\ [Pp]i ]] && why="oui"
  if echo "$host" | grep -qiE "$HOST_RE"; then
    why="${why:+$why+}host"
  fi

  if [[ -z "$why" && $SHOW_ALL -eq 0 ]]; then
    continue
  fi
  printf '%-15s  %-17s  %-22s  %-26s  %s\n' \
    "$ip" "$mac" "$host" "${vendor:-?}" "${why:--}"
  found=$((found+1))
done < <(ip neigh | sort -t. -k4 -n)

echo >&2
if [[ $found -eq 0 ]]; then
  echo "No Raspberry Pis found on ${prefix}.0/24." >&2
  echo "Tip: the Pi must be powered on and have answered the ping sweep. Re-run, or try --all." >&2
else
  echo "Found $found host(s).${IEEE_DB:+  (vendor lookups via $IEEE_DB)}" >&2
fi
