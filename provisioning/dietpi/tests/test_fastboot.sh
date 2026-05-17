#!/usr/bin/env bash
# Dependency-free unit tests for the fastboot artifacts. Exit non-zero on first failure.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FB="$HERE/../fastboot"
fail(){ echo "FAIL: $*" >&2; exit 1; }
pass(){ echo "ok: $*"; }

# --- measure-ttssh.sh --selftest must validate math/arg parsing with no Pi ---
bash "$FB/measure-ttssh.sh" --selftest >/dev/null 2>&1 || fail "measure-ttssh --selftest"
pass "measure-ttssh selftest"

# --- fbsnap.sh usage surface ---
bash "$FB/fbsnap.sh" --help >/dev/null 2>&1 || fail "fbsnap --help should exit 0"
( bash "$FB/fbsnap.sh" bogus 2>/dev/null ) && fail "fbsnap bad verb should exit nonzero"
pass "fbsnap arg surface"

# --- L0 unit files well-formed ---
grep -q '^\[Unit\]' "$FB/treadmill-critical.target" || fail "treadmill-critical.target missing [Unit]"
grep -q 'DefaultDependencies=no' "$FB/treadmill-critical.target" || fail "Path A target must set DefaultDependencies=no"
grep -qE 'After=.*(local-fs|sysinit)' "$FB/treadmill-critical.target" || fail "Path A target must order After local-fs/sysinit"
grep -qiE '^(After|Wants|Requires|Requisite|BindsTo|PartOf)=.*network' "$FB/treadmill-critical.target" && fail "Path A target must NOT have a network ordering/dependency"
grep -q 'ExecStart=' "$FB/fastboot-probe.service" || fail "probe service missing ExecStart"
grep -qiE 'network|wpa' "$FB/10-ssh-fastboot.conf" && fail "ssh drop-in must not re-add network ordering"
pass "L0 unit files well-formed"

# --- wifi-fastpath: real dhclient lease => fast; no cache => slow ---
TD=$(mktemp -d)
cat > "$TD/lease" <<'EOL'
lease {
  interface "wlan0";
  fixed-address 192.168.1.206;
  option subnet-mask 255.255.255.0;
  option routers 192.168.1.1;
  option domain-name-servers 192.168.1.1;
  renew 5 2030/1/1 00:00:00;
}
EOL
FB_LEASE="$TD/lease" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'apply 192.168.1.206/24' || fail "fastpath should derive cached IP/mask from a real dhclient lease"
FB_LEASE="$TD/none" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'mode=slow' || fail "no cache must select slow path"
rm -rf "$TD"
pass "wifi-fastpath cached-lease parsing + slow fallback"

# --- recovery watchdog: dry-run declares restore+reboot intent; service well-formed ---
FB_DRYRUN=1 bash "$FB/fastboot-recover.sh" 2>/dev/null | grep -q 'restore .* reboot' || fail "fastboot-recover dry-run must declare restore+reboot"
grep -q 'ExecStart=/boot/fastboot/fastboot-recover.sh' "$FB/fastboot-recover.service" || fail "recover.service ExecStart wrong"
pass "recovery watchdog logic + unit"

# --- wifi-learn rewrites a pinned block + keeps a generic fallback block ---
TD=$(mktemp -d)
cat > "$TD/wpa.conf" <<'EOW'
country=US
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=1
network={
	ssid="silver-2011"
	psk="SECRET"
	key_mgmt=WPA-PSK
}
EOW
FB_WPA="$TD/wpa.conf" bash "$FB/wifi-learn.sh" e0:63:da:28:69:fc 2437 >/dev/null 2>&1 || fail "wifi-learn run"
grep -q 'bssid=e0:63:da:28:69:fc' "$TD/wpa.conf" || fail "pinned bssid not written"
grep -q 'scan_freq=2437' "$TD/wpa.conf" || fail "scan_freq not written"
grep -q 'priority=' "$TD/wpa.conf" || fail "priority ordering not written"
[ "$(grep -c '^network={' "$TD/wpa.conf")" -ge 2 ] || fail "must keep a generic fallback network block"
grep -q 'psk="SECRET"' "$TD/wpa.conf" || fail "PSK must be preserved verbatim"
rm -rf "$TD"
pass "wifi-learn pins bssid/freq + retains generic fallback"

# --- safety hardening: periodic+reachability watchdog, validate-before-trust, safe restore ---
FB_DRYRUN=1 bash "$FB/fastboot-recover.sh" 2>/dev/null | grep -qiE 'every 60s.*unreachable.*consecutively' || fail "recover must be periodic + reachability + threshold based (not boot-only)"
grep -q 'OnUnitActiveSec=60' "$FB/fastboot-recover.timer" || fail "fastboot-recover.timer must re-fire every 60s"
grep -q 'OnBootSec=120' "$FB/fastboot-recover.timer" || fail "timer must not false-trigger during normal boot (OnBootSec=120)"
grep -q 'cached lease unreachable' "$FB/wifi-fastpath.sh" || fail "wifi-fastpath must validate cached IP and discard if unreachable"
grep -qE 'ping -c1 -W1 "\$GW"' "$FB/wifi-fastpath.sh" || fail "wifi-fastpath must gateway-validate before trusting cached lease"
grep -q 'daemon-reexec' "$FB/fbsnap.sh" || fail "fbsnap restore must daemon-reexec (avoid logind race)"
TD=$(mktemp -d)
printf 'lease {\n interface "wlan0";\n fixed-address 192.168.1.206;\n option subnet-mask 255.255.255.0;\n option routers 192.168.1.1;\n}\n' > "$TD/l"
FB_LEASE="$TD/l" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'mode=fast apply 192.168.1.206/24' || fail "hardened fastpath dry-run still derives IP"
FB_LEASE="$TD/none" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'mode=slow' || fail "hardened fastpath no-cache => slow"
rm -rf "$TD"
pass "safety hardening: periodic reachability watchdog + validate-before-trust + safe restore"

# --- Task 9 fold-back: kept layers wired idempotently + bluez + staged by toolkit ---
[ -s "$FB/kept-layers.txt" ] || fail "kept-layers.txt missing/empty (Task 8 records kept layers)"
grep -q 'fastboot' "$HERE/../Automation_Custom_Script.sh" || fail "Automation_Custom_Script.sh must apply fastboot artifacts"
grep -q 'fastboot.applied' "$HERE/../Automation_Custom_Script.sh" || fail "fold-back must be idempotent (applied-marker guard)"
grep -qE 'apt-get install.*bluez' "$HERE/../Automation_Custom_Script.sh" || fail "fold-back must idempotently install bluez (appliance needs BT; not in base image)"
grep -qE 'fastboot.tgz|fastboot' "$HERE/../prepare-sd.sh" || fail "prepare-sd.sh must stage the fastboot artifacts"
grep -qE 'fastboot.tgz|fastboot' "$HERE/../build-image.sh" || fail "build-image.sh must carry the fastboot artifacts into the image"
# L2 (rejected) must NOT be auto-enabled by the fold-back
grep -qE 'systemctl enable .*wifi-learn|enable .*L2' "$HERE/../Automation_Custom_Script.sh" && fail "fold-back must not enable rejected L2"
pass "Task 9 fold-back: kept layers wired, idempotent, bluez, staged; L2 excluded"

# --- Security-audit fixes (codex 2026-05-17): must-fix hardening ---
# F1: wifi-fastpath validates IPv4 (rogue-DHCP) before calling ip(8)
grep -qE 'valid_ip|valid_ipv4|^[0-9.]+ octet|is_ipv4' "$FB/wifi-fastpath.sh" || fail "F1: wifi-fastpath must validate IPv4 of IP/NM/GW before ip(8)"
TD=$(mktemp -d)
printf 'lease {\n fixed-address 999.1.2.3;\n option subnet-mask 255.255.255.0;\n option routers 192.168.1.1;\n}\n' > "$TD/bad"
FB_LEASE="$TD/bad" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'mode=slow' || fail "F1: malformed lease IP must abort to slow, not be applied"
printf 'lease {\n fixed-address 192.168.1.50;\n option subnet-mask 255.255.0.255;\n option routers 192.168.1.1;\n}\n' > "$TD/badmask"
FB_LEASE="$TD/badmask" FB_DRYRUN=1 bash "$FB/wifi-fastpath.sh" 2>/dev/null | grep -q 'mode=slow' || fail "F1: non-contiguous netmask must abort to slow"
rm -rf "$TD"
# F2: recover must not boot-loop — .recovered suppresses further reboot actions
grep -qE 'recovered.*(exit|return|skip|already)|already recovered' "$FB/fastboot-recover.sh" || fail "F2: recover must suppress repeat reboots once .recovered exists (boot-loop guard)"
# F4: fold-back marker only after success (not unconditional touch after ||true chain)
grep -qE '(grep -qE|tar -tzf|allowlist|--no-same-owner)' "$HERE/../Automation_Custom_Script.sh" || fail "F3: fold-back tar extract must be hardened (allowlist / safe flags)"
grep -q 'logger -t fastboot .*bluez' "$HERE/../Automation_Custom_Script.sh" || fail "F5: bluez install failure must be logged, not silent"
# F6: fbsnap label validation + 600 perms
grep -qE 'A-Za-z0-9._-|\[\[ .* =~ |label.*valid' "$FB/fbsnap.sh" || fail "F6: fbsnap must constrain snapshot label (no path traversal)"
grep -q 'chmod 600' "$FB/fbsnap.sh" || fail "F6/F8: fbsnap snapshots are secret-bearing -> chmod 600"
pass "security-audit fixes: IPv4 validation, boot-loop guard, safe tar fold-back, fbsnap hardening"

# --- F9: gw-validation must NOT block the boot critical path (it serialized
# ~9s waiting for WiFi assoc, killing the win). Apply cached IP fast; validate
# + correct in the BACKGROUND; rely on the proven periodic watchdog as backstop.
grep -qE 'fast path returns immediately|validate.*background|background.*validate|non-blocking' "$FB/wifi-fastpath.sh" || fail "F9: wifi-fastpath must not block boot on gw-validation (move it to background)"
# the foreground path must reach its fast 'exit 0' without a blocking ping loop
awk '/^ip route replace default/{r=NR} /^exit 0/{e=NR} END{exit (r&&e&&(e-r)<6)?0:1}' "$FB/wifi-fastpath.sh" || fail "F9: foreground fast path must exit promptly after applying cached IP (no inline ping-wait loop before exit)"
pass "F9: gw-validation is off the boot critical path (fast + watchdog-backed)"

echo "ALL TESTS PASSED"
