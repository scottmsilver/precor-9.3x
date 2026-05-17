#!/usr/bin/env bash
# Rewrite wpa_supplicant.conf as: [pinned fast block: bssid+scan_freq, priority=10]
# + [generic fallback block: ssid/psk, full scan, priority=1]. Idempotent.
set -u
BSSID="${1:?bssid}"; FREQ="${2:?freq}"
WPA="${FB_WPA:-/etc/wpa_supplicant/wpa_supplicant.conf}"
[ -s "$WPA" ] || { echo "no wpa conf" >&2; exit 1; }
SSID=$(grep -m1 -oE 'ssid="[^"]*"' "$WPA" | head -1 | sed 's/ssid=//')
PSK=$(grep -m1 -oE 'psk="[^"]*"|psk=[0-9a-f]{64}' "$WPA" | head -1 | sed 's/psk=//')
KM=$(grep -m1 -oE 'key_mgmt=[A-Z-]+' "$WPA" | head -1 | sed 's/key_mgmt=//'); KM="${KM:-WPA-PSK}"
[ -n "$SSID" ] && [ -n "$PSK" ] || { echo "could not extract ssid/psk" >&2; exit 1; }
HDR=$(grep -E '^(country=|ctrl_interface=|update_config=|p2p_disabled=)' "$WPA")
tmp=$(mktemp)
{
  printf '%s\n' "$HDR"
  printf 'network={\n\tssid=%s\n\tpsk=%s\n\tkey_mgmt=%s\n\tbssid=%s\n\tscan_freq=%s\n\tfreq_list=%s\n\tpriority=10\n}\n' "$SSID" "$PSK" "$KM" "$BSSID" "$FREQ" "$FREQ"
  printf 'network={\n\tssid=%s\n\tpsk=%s\n\tkey_mgmt=%s\n\tpriority=1\n}\n' "$SSID" "$PSK" "$KM"
} > "$tmp"
chmod 600 "$tmp"; mv "$tmp" "$WPA"
echo "wifi-learn: pinned $BSSID@$FREQ + generic fallback"
