# Pi Zero 2 W Fast-Boot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Minimize time-to-SSH-key-auth on the live `rpi-zero` (Pi Zero 2 W, DietPi 10.3) by applying revertible, individually-measured boot-optimization layers until diminishing returns, while restructuring the boot so a future network-independent service can start early; fold validated wins back into the `provisioning/dietpi/` toolkit.

**Architecture:** Two decoupled boot paths — Path A (early, network-independent slot; defined+measured, not filled) and Path B (WiFi→IP→sshd, the measured KPI). Each optimization is a layer: snapshot → apply live over SSH → 3-cycle reboot measurement → keep+record or revert. Network layers self-recover (protect SSH as the recovery channel); OS strips are validated once then static (Bluetooth kept). Validated artifacts are staged by the toolkit so fresh images inherit them.

**Tech Stack:** DietPi/Debian Bookworm on ARMv8, ifupdown + dhclient, wpa_supplicant, systemd, bash, the existing dependency-free `provisioning/dietpi/tests/` harness.

**Conventions (used in every task):**
- Pi shell prefix: `PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'`
- Work dir on host: repo root `/home/ssilver/development/precor-9.3x`.
- New artifact home: `provisioning/dietpi/fastboot/` (host, version-controlled) staged to `/boot/fastboot/` on the Pi by the toolkit later.
- Running results log: `provisioning/dietpi/fastboot/fastboot-results.md` (host).
- **No git commits anywhere** (owner password gate). Steps that would normally `git commit` instead append to the results log and leave the tree dirty.
- "Diminishing returns / the wall": a layer whose 3-cycle mean improves the boot-side `/proc/uptime`-at-SSH by **< 0.5 s** vs the previous kept state, OR remaining time dominated by an irreducible floor (kernel init or WiFi-assoc RF time). On hitting the wall: stop, write the summary (Task 8), report to the user.

**Amendment 2026-05-17 (Bluetooth reality):** The base DietPi image ships **no** Bluetooth stack (no `bluez`/`bluetoothd`/`bluetooth.service`; onboard radio is kernel-attached via `krnbt` so `hci0` exists, but unusable without userspace). The appliance requires Bluetooth (FTMS/HRM daemons). Therefore: (a) `bluez` was installed on the live Pi (`apt-get install -y --no-install-recommends bluez`; `bluetooth.service` enabled+active; `hci0` UART UP RUNNING); (b) the canonical reference baseline is **`baseline-bt`** (BT present, with the L0 Path A slot already in place — L0 is kept by design: probe fires ~6.7 s, network-independent; the sshd drop-in is harmless/marginal and not the SSH KPI lever); (c) the BT gates in Tasks 5/6 are now **real** (BT is present to protect); (d) Task 9 fold-back MUST include an idempotent `bluez` install + `bluetooth.service` enable in `Automation_Custom_Script.sh` (DietPi's software catalog has no Bluetooth id on this version, so `apt` is the path).

**Amendment 2026-05-17 (harness hardening):** `measure-ttssh.sh` was hardened after a cycle captured a stale ~8715 s uptime (Pi hadn't rebooted within the old 45 s down-window, so the pre-existing uptime polluted the mean). The authoritative script (source of truth) now: captures pre-reboot uptime; issues `nohup sh -c "sleep 1; systemctl reboot"`; waits ≤70 s for the port to drop; **validates a cycle only if post-reboot uptime is both `< pre` and `< 600 s`**; retries a failed reboot once; excludes invalid cycles from the mean and reports `valid=/bad=`. `--selftest` semantics unchanged (test harness still green).

---

### Task 1: Reusable measurement harness + snapshot/revert helper + baseline

**Files:**
- Create: `provisioning/dietpi/fastboot/measure-ttssh.sh`
- Create: `provisioning/dietpi/fastboot/fbsnap.sh`
- Create: `provisioning/dietpi/fastboot/fastboot-results.md`
- Test: `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test**

Create `provisioning/dietpi/tests/test_fastboot.sh`:

```bash
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

echo "ALL TESTS PASSED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — `measure-ttssh.sh` not found / selftest missing.

- [ ] **Step 3: Write minimal implementation**

Create `provisioning/dietpi/fastboot/measure-ttssh.sh`:

```bash
#!/usr/bin/env bash
# Canonical time-to-SSH measurement rig. 3 reboot cycles by default.
# Authoritative metric: Pi-side /proc/uptime at first successful SSH key-auth.
set -u
HOST="${FB_HOST:-192.168.1.206}"
KEY="${FB_KEY:-$HOME/.ssh/id_ed25519}"
CYCLES="${FB_CYCLES:-3}"
SSHO="-i $KEY -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"
now(){ date +%s.%N; }
d(){ awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
if [ "${1:-}" = "--selftest" ]; then
  # No Pi: prove arg/math helpers work.
  t=$(d 10 12.5); [ "$t" = "2.5" ] || { echo "selftest math FAIL ($t)"; exit 1; }
  [ -n "$HOST" ] && [ -n "$KEY" ] && [ "$CYCLES" -ge 1 ] || { echo "selftest cfg FAIL"; exit 1; }
  echo "selftest OK"; exit 0
fi
label="${1:-unlabeled}"
echo "## measure: $label  ($(date -u +%FT%TZ))  host=$HOST cycles=$CYCLES"
sum=0; n=0; min=; max=
for c in $(seq 1 "$CYCLES"); do
  ssh $SSHO root@"$HOST" 'systemd-run --on-active=1 systemctl reboot >/dev/null 2>&1 || (sleep 1; reboot) &' >/dev/null 2>&1
  T0=$(now)
  # wait for down (max 45s)
  ds=$(now); while timeout 2 bash -c "exec 3<>/dev/tcp/$HOST/22" 2>/dev/null; do
    [ "$(awk -v a=$ds -v b=$(now) 'BEGIN{print (b-a>45)?1:0}')" = 1 ] && break; sleep 0.5; done
  # wait for port back (max 180s)
  while ! timeout 2 bash -c "exec 3<>/dev/tcp/$HOST/22" 2>/dev/null; do
    [ "$(awk -v a=$T0 -v b=$(now) 'BEGIN{print (b-a>225)?1:0}')" = 1 ] && { echo "  cycle $c TIMEOUT(port)"; continue 2; }
    sleep 0.25; done
  # wait for key-auth (max 60s) + read boot-side uptime
  bu=""; ae=$(now)
  while :; do
    bu=$(ssh $SSHO -o ConnectTimeout=4 root@"$HOST" 'cut -d" " -f1 /proc/uptime' 2>/dev/null)
    [ -n "$bu" ] && break
    [ "$(awk -v a=$ae -v b=$(now) 'BEGIN{print (b-a>60)?1:0}')" = 1 ] && { echo "  cycle $c TIMEOUT(auth)"; continue 2; }
    sleep 0.25; done
  echo "  cycle $c: boot-side-uptime-at-ssh=${bu}s"
  sum=$(awk -v s=$sum -v x=$bu 'BEGIN{print s+x}'); n=$((n+1))
  min=$(awk -v m="${min:-$bu}" -v x=$bu 'BEGIN{print (x<m)?x:m}')
  max=$(awk -v m="${max:-$bu}" -v x=$bu 'BEGIN{print (x>m)?x:m}')
  sleep 5
done
[ "$n" -gt 0 ] && echo "  RESULT $label: mean=$(awk -v s=$sum -v n=$n 'BEGIN{printf "%.1f", s/n}')s min=${min}s max=${max}s n=$n"
ssh $SSHO root@"$HOST" 'systemd-analyze 2>/dev/null; echo "--chain--"; systemd-analyze critical-chain ssh.service 2>/dev/null | head -8; echo "--pathA--"; journalctl -b -u fastboot-probe.service --no-pager 2>/dev/null | tail -2' 2>/dev/null | sed 's/^/  /'
```

Create `provisioning/dietpi/fastboot/fbsnap.sh`:

```bash
#!/usr/bin/env bash
# Snapshot/revert /etc + boot FAT on the Pi. Each snapshot is a tar pulled to the host.
set -u
HOST="${FB_HOST:-192.168.1.206}"; KEY="${FB_KEY:-$HOME/.ssh/id_ed25519}"
SSHO="-i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"
DIR="$HOME/fastboot-snapshots"
usage(){ echo "fbsnap.sh take <label> | restore <label> | list"; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  take) [ -n "${2:-}" ] || { usage; exit 2; }; mkdir -p "$DIR"
        ssh $SSHO root@"$HOST" 'BD=$([ -d /boot/firmware ] && echo /boot/firmware || echo /boot); tar czf - --ignore-failed-read --warning=no-failed-read /etc "$BD/cmdline.txt" "$BD/config.txt" 2>/dev/null' > "$DIR/$2.tgz" \
          && [ -s "$DIR/$2.tgz" ] && echo "snapshot saved: $DIR/$2.tgz" || { echo "snapshot FAILED" >&2; exit 1; } ;;
  restore) [ -n "${2:-}" ] && [ -f "$DIR/$2.tgz" ] || { echo "no snapshot $2" >&2; exit 1; }
        ssh $SSHO root@"$HOST" 'tar xzf - -C /' < "$DIR/$2.tgz" \
          && ssh $SSHO root@"$HOST" 'systemctl daemon-reload; systemctl reboot' \
          && echo "restored $2 + rebooting" || { echo "restore FAILED" >&2; exit 1; } ;;
  list) ls -1 "$DIR" 2>/dev/null ;;
  *) usage; exit 2 ;;
esac
```

Create `provisioning/dietpi/fastboot/fastboot-results.md` with header:

```markdown
# Fast-Boot Results Log (rpi-zero, Pi Zero 2 W, DietPi 10.3)
KPI = Pi-side /proc/uptime at first SSH key-auth (3-cycle mean). Lower = better.
Decision rule: keep if mean improves >=0.5s vs prior kept state & no SSH regression.

| Layer | mean (s) | min | max | kept? | notes |
|-------|----------|-----|-----|-------|-------|
```

`chmod +x provisioning/dietpi/fastboot/*.sh`

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: `ok: measure-ttssh selftest`, `ok: fbsnap arg surface`, `ALL TESTS PASSED`.

- [ ] **Step 5: Snapshot + baseline measurement (no commit — append to results)**

```bash
bash provisioning/dietpi/fastboot/fbsnap.sh take baseline
bash provisioning/dietpi/fastboot/measure-ttssh.sh baseline | tee -a provisioning/dietpi/fastboot/fastboot-results.md
```
Expected: a `RESULT baseline: mean=~16s` line; append the row to the table. (No `git commit` — password gate.)

---

### Task 2: Layer L0 — structural decouple (Path A slot + sshd off network.target)

**Files:**
- Create: `provisioning/dietpi/fastboot/treadmill-critical.target`
- Create: `provisioning/dietpi/fastboot/fastboot-probe.service`
- Create: `provisioning/dietpi/fastboot/10-ssh-fastboot.conf`
- Test: extend `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test** — append before `echo "ALL TESTS PASSED"`:

```bash
# --- L0 unit files well-formed ---
grep -q '^\[Unit\]' "$FB/treadmill-critical.target" || fail "treadmill-critical.target missing [Unit]"
grep -q 'DefaultDependencies=no' "$FB/treadmill-critical.target" || fail "Path A target must set DefaultDependencies=no"
grep -qE 'After=.*(local-fs|sysinit)' "$FB/treadmill-critical.target" || fail "Path A target must order After local-fs/sysinit"
grep -qiE '^(After|Wants|Requires|Requisite|BindsTo|PartOf)=.*network' "$FB/treadmill-critical.target" && fail "Path A target must NOT have a network ordering/dependency"
grep -q 'ExecStart=' "$FB/fastboot-probe.service" || fail "probe service missing ExecStart"
grep -qiE 'network|wpa' "$FB/10-ssh-fastboot.conf" && fail "ssh drop-in must not re-add network ordering"
pass "L0 unit files well-formed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — `treadmill-critical.target` missing.

- [ ] **Step 3: Write minimal implementation**

`provisioning/dietpi/fastboot/treadmill-critical.target`:
```ini
[Unit]
Description=Treadmill critical (network-independent) early slot
DefaultDependencies=no
After=local-fs.target sysinit.target
Requires=sysinit.target
```

`provisioning/dietpi/fastboot/fastboot-probe.service`:
```ini
[Unit]
Description=Fast-boot Path A probe (timestamps the early slot)
DefaultDependencies=no
After=local-fs.target sysinit.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo "pathA-fired uptime=$(cut -d\" \" -f1 /proc/uptime)"'
RemainAfterExit=yes
[Install]
WantedBy=treadmill-critical.target
```

`provisioning/dietpi/fastboot/10-ssh-fastboot.conf`:
```ini
[Unit]
After=
After=sysinit.target
Wants=
[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: `ok: L0 unit files well-formed` then `ALL TESTS PASSED`.

- [ ] **Step 5: Apply L0 live + measure (snapshot first)**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L0
for f in treadmill-critical.target fastboot-probe.service; do
  cat provisioning/dietpi/fastboot/$f | $PI "cat > /etc/systemd/system/$f"; done
$PI 'mkdir -p /etc/systemd/system/ssh.service.d'
cat provisioning/dietpi/fastboot/10-ssh-fastboot.conf | $PI 'cat > /etc/systemd/system/ssh.service.d/10-ssh-fastboot.conf'
$PI 'systemctl daemon-reload; systemctl enable fastboot-probe.service treadmill-critical.target; systemctl add-wants treadmill-critical.target fastboot-probe.service'
bash provisioning/dietpi/fastboot/measure-ttssh.sh L0-decouple | tee -a provisioning/dietpi/fastboot/fastboot-results.md
```
Expected: a `RESULT L0-decouple` line and a `pathA-fired uptime=` line in the chain output. Decision: keep if mean improved ≥0.5 s **and** SSH still reachable all 3 cycles; else `fbsnap.sh restore pre-L0`. Record the row + decision in the results table. (No commit.)

---

### Task 3: Layer L1 — cached-lease fast path + background DHCP validate

**Files:**
- Create: `provisioning/dietpi/fastboot/wifi-fastpath.sh`
- Create: `provisioning/dietpi/fastboot/fastboot-net.service`
- Test: extend `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test** — append before `echo "ALL TESTS PASSED"`:

```bash
# --- wifi-fastpath: parses a lease, no-cache => exits 'slow', stale => fallback ---
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — `wifi-fastpath.sh` not found.

- [ ] **Step 3: Write minimal implementation**

`provisioning/dietpi/fastboot/wifi-fastpath.sh`:
```bash
#!/usr/bin/env bash
# Cached-lease fast path: apply last lease immediately, then dhclient in bg to
# validate/renew. Self-recovering: stale/no cache => slow path (normal dhclient).
set -u
IF="${FB_IF:-wlan0}"
LEASE="${FB_LEASE:-/var/lib/dhcp/dhclient.$IF.leases}"
DRY="${FB_DRYRUN:-0}"
last_block(){ awk '/^lease /{b=""} {b=b $0 "\n"} /^}/{last=b} END{printf "%s",last}' "$1" 2>/dev/null; }
val(){ printf '%s\n' "$1" | grep -oE "$2[^;]*" | tail -1 | awk '{print $NF}' | tr -d '";'; }
mask2cidr(){ awk -F. '{c=0;for(i=1;i<=4;i++){m=$i;while(m){c+=m%2;m=int(m/2)}}print c}' <<<"$1"; }
if [ ! -s "$LEASE" ]; then echo "mode=slow (no lease cache)"; [ "$DRY" = 1 ] && exit 0
  exec dhclient -1 "$IF"; fi
B=$(last_block "$LEASE")
IP=$(val "$B" 'fixed-address'); NM=$(val "$B" 'subnet-mask'); GW=$(val "$B" 'option routers')
if [ -z "$IP" ] || [ -z "$NM" ]; then echo "mode=slow (lease unparseable)"; [ "$DRY" = 1 ] && exit 0
  exec dhclient -1 "$IF"; fi
CIDR=$(mask2cidr "$NM")
echo "mode=fast apply $IP/$CIDR gw=$GW"
[ "$DRY" = 1 ] && exit 0
ip addr flush dev "$IF" 2>/dev/null
ip addr add "$IP/$CIDR" dev "$IF" && ip link set "$IF" up
[ -n "$GW" ] && ip route replace default via "$GW" dev "$IF"
# Background validate/renew; if dhclient yields a different IP it reconfigures.
( dhclient -1 "$IF" >/dev/null 2>&1; ip addr show "$IF" | grep -q "$IP/" || logger -t fastboot "lease changed, dhclient reconfigured" ) &
exit 0
```

`provisioning/dietpi/fastboot/fastboot-net.service`:
```ini
[Unit]
Description=Fast-boot cached-lease network bring-up (wlan0)
DefaultDependencies=no
After=sysinit.target
Before=network.target
Wants=network.target
[Service]
Type=oneshot
ExecStart=/boot/fastboot/wifi-fastpath.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: `ok: wifi-fastpath cached-lease parsing + slow fallback` then `ALL TESTS PASSED`.

- [ ] **Step 5: Apply L1 live + measure + verify self-recovery**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L1
$PI 'mkdir -p /boot/fastboot'
cat provisioning/dietpi/fastboot/wifi-fastpath.sh | $PI 'cat > /boot/fastboot/wifi-fastpath.sh; chmod +x /boot/fastboot/wifi-fastpath.sh'
cat provisioning/dietpi/fastboot/fastboot-net.service | $PI 'cat > /etc/systemd/system/fastboot-net.service'
# Stop ifupdown from blocking on wlan0 dhcp: make it manual; fast path owns L3.
$PI "sed -i 's/^iface wlan0 inet dhcp/iface wlan0 inet manual/' /etc/network/interfaces; systemctl daemon-reload; systemctl enable fastboot-net.service"
bash provisioning/dietpi/fastboot/measure-ttssh.sh L1-cached-lease | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# Self-recovery check: poison the cache with a bogus IP, reboot, must still come back via dhclient fallback.
$PI "sed -i 's/fixed-address [0-9.]*/fixed-address 192.168.1.250/' /var/lib/dhcp/dhclient.wlan0.leases; systemctl reboot" ; sleep 60
bash provisioning/dietpi/fastboot/measure-ttssh.sh L1-selfrecover | tee -a provisioning/dietpi/fastboot/fastboot-results.md
```
Expected: `RESULT L1-cached-lease` improved; `L1-selfrecover` still returns SSH (proves dhclient fallback rewrote the lease). Keep/revert per rule; record rows + decision. If self-recovery fails → `fbsnap.sh restore pre-L1` and mark L1 rejected with reason.

---

### Task 4: Layer L2 — WiFi pinned BSSID/freq fast block + generic fallback + learn

**Files:**
- Create: `provisioning/dietpi/fastboot/wifi-learn.sh`
- Test: extend `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test** — append before `echo "ALL TESTS PASSED"`:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — `wifi-learn.sh` not found.

- [ ] **Step 3: Write minimal implementation**

`provisioning/dietpi/fastboot/wifi-learn.sh`:
```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: `ok: wifi-learn pins bssid/freq + retains generic fallback` then `ALL TESTS PASSED`.

- [ ] **Step 5: Apply L2 live + measure + verify fallback**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L2
cat provisioning/dietpi/fastboot/wifi-learn.sh | $PI 'cat > /boot/fastboot/wifi-learn.sh; chmod +x /boot/fastboot/wifi-learn.sh'
BSSID=$($PI 'iw dev wlan0 link 2>/dev/null | awk "/Connected to/{print \$3}"')
FREQ=$($PI 'iw dev wlan0 link 2>/dev/null | awk "/freq:/{print \$2}"')
$PI "/boot/fastboot/wifi-learn.sh $BSSID $FREQ; systemctl restart wpa_supplicant 2>/dev/null || true"
bash provisioning/dietpi/fastboot/measure-ttssh.sh L2-bssid-pin | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# Fallback check: write a wrong bssid into the pinned block, reboot, must still associate via the generic block.
$PI "sed -i 's/bssid=$BSSID/bssid=00:11:22:33:44:55/' /etc/wpa_supplicant/wpa_supplicant.conf; systemctl reboot"; sleep 70
bash provisioning/dietpi/fastboot/measure-ttssh.sh L2-fallback | tee -a provisioning/dietpi/fastboot/fastboot-results.md
$PI "/boot/fastboot/wifi-learn.sh $BSSID $FREQ"   # restore correct pin
```
Expected: `L2-bssid-pin` improved; `L2-fallback` still returns SSH (generic block worked). Keep/revert per rule; record. If fallback fails → `fbsnap.sh restore pre-L2`, mark rejected.

---

### Task 5: Layer L3 — OS unit strips (validated once, then static; Bluetooth KEPT)

**Files:** none new (live system actions; recorded in results log).

- [ ] **Step 1: Snapshot**

```bash
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L3
```

- [ ] **Step 2: Apply + measure each sub-strip independently (keep/revert each)**

For each item, run the disable, measure, decide, record. `PI` as defined above.

```bash
# L3a: fstrim out of boot path
$PI 'systemctl disable --now fstrim.timer fstrim.service 2>/dev/null; true'
bash provisioning/dietpi/fastboot/measure-ttssh.sh L3a-fstrim | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# L3b: headless input stack
$PI 'systemctl disable --now keyboard-setup.service console-setup.service triggerhappy.service 2>/dev/null; true'
bash provisioning/dietpi/fastboot/measure-ttssh.sh L3b-input | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# L3c: timesyncd non-blocking (do not gate boot on NTP)
$PI 'mkdir -p /etc/systemd/system/systemd-timesyncd.service.d; printf "[Unit]\nDefaultDependencies=no\nBefore=\n" > /etc/systemd/system/systemd-timesyncd.service.d/10-nonblock.conf; systemctl daemon-reload'
bash provisioning/dietpi/fastboot/measure-ttssh.sh L3c-timesyncd | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# L3d: skip extra FAT fsck (keep rootfs fsck)
$PI 'tune2fs -c -1 -i 0 $(findmnt -no SOURCE /) 2>/dev/null; sed -i "s/\(\/boot\/firmware\|\/boot\).*vfat.*[0-9] 2$/&/" /etc/fstab; true'
bash provisioning/dietpi/fastboot/measure-ttssh.sh L3d-fsck | tee -a provisioning/dietpi/fastboot/fastboot-results.md
```
Decision per sub-strip: keep if ≥0.5 s mean gain & function intact (verify `bluetoothctl show` still works → Bluetooth NOT broken; SSH all 3 cycles). Bluetooth is **never** touched. Revert a sub-strip via the snapshot only if it regressed or broke function. Record every sub-row + decision.

- [ ] **Step 3: Verify Bluetooth still healthy (mandatory gate)**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
$PI 'systemctl is-active bluetooth; bluetoothctl show 2>/dev/null | head -3'
```
Expected: `active` and a controller shown. If Bluetooth is not active → a strip wrongly hit it: `fbsnap.sh restore pre-L3`, investigate, re-apply without the offending item.

---

### Task 6: Layer L4 — firmware / cmdline / config (no disable-bt)

**Files:** none new (boot-FAT edits; recorded).

- [ ] **Step 1: Snapshot + apply**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L4
BOOT=$($PI '[ -d /boot/firmware ] && echo /boot/firmware || echo /boot')
$PI "grep -q '^disable_splash=1' $BOOT/config.txt || echo disable_splash=1 >> $BOOT/config.txt"
$PI "grep -q '^boot_delay=0' $BOOT/config.txt || echo boot_delay=0 >> $BOOT/config.txt"
$PI "grep -q '^initial_turbo=' $BOOT/config.txt || echo initial_turbo=30 >> $BOOT/config.txt"
# Explicitly DO NOT add dtoverlay=disable-bt (Bluetooth must keep working).
$PI "grep -q disable-bt $BOOT/config.txt && { echo 'ABORT: disable-bt present' ; exit 9; } || true"
```

- [ ] **Step 2: Measure + decide**

```bash
bash provisioning/dietpi/fastboot/measure-ttssh.sh L4-firmware | tee -a provisioning/dietpi/fastboot/fastboot-results.md
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
$PI 'systemctl is-active bluetooth'   # must still be active
```
Expected: `RESULT L4-firmware`; Bluetooth `active`. Keep/revert per rule; record. If `disable-bt` ever appears or Bluetooth breaks → `fbsnap.sh restore pre-L4`, mark rejected.

---

### Task 7: State machine (first-vs-steady) + recovery watchdog

**Files:**
- Create: `provisioning/dietpi/fastboot/fastboot-guard.sh`
- Create: `provisioning/dietpi/fastboot/fastboot-watchdog.sh`
- Test: extend `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test** — append before `echo "ALL TESTS PASSED"`:

```bash
# --- guard: cache-miss => slow/learn, cache-hit => fast ---
TD=$(mktemp -d); mkdir -p "$TD/data"
FB_MARK="$TD/data/fastboot.learned" FB_LEASE="$TD/none" FB_INSTALL="$TD/notdone" \
  bash "$FB/fastboot-guard.sh" 2>/dev/null | grep -q 'decision=slow' || fail "cache-miss must pick slow/learn"
: > "$TD/data/fastboot.learned"; echo 2 > "$TD/done"; : > "$TD/lease"
FB_MARK="$TD/data/fastboot.learned" FB_LEASE="$TD/lease" FB_INSTALL="$TD/done" \
  bash "$FB/fastboot-guard.sh" 2>/dev/null | grep -q 'decision=fast' || fail "cache-hit must pick fast"
rm -rf "$TD"
# watchdog dry-run prints the fallback action
FB_DRYRUN=1 bash "$FB/fastboot-watchdog.sh" 2>/dev/null | grep -q 'fallback=dhclient' || fail "watchdog must declare fallback"
pass "state-machine guard + watchdog logic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — `fastboot-guard.sh` not found.

- [ ] **Step 3: Write minimal implementation**

`provisioning/dietpi/fastboot/fastboot-guard.sh`:
```bash
#!/usr/bin/env bash
# Decide the NETWORK path only. cache-hit => fast; cache-miss => slow + (re)learn.
set -u
MARK="${FB_MARK:-/boot/fastboot/fastboot.learned}"
LEASE="${FB_LEASE:-/var/lib/dhcp/dhclient.wlan0.leases}"
INSTALL="${FB_INSTALL:-/boot/dietpi/.install_stage}"
done=0; [ -f "$INSTALL" ] && [ "$(cat "$INSTALL" 2>/dev/null)" = 2 ] && done=1
if [ "$done" = 1 ] && [ -f "$MARK" ] && [ -s "$LEASE" ]; then
  echo "decision=fast"
else
  echo "decision=slow (first-boot/cache-miss: run correct path, then learn)"
fi
```

`provisioning/dietpi/fastboot/fastboot-watchdog.sh`:
```bash
#!/usr/bin/env bash
# If no IP within 25s, force stock DHCP + full scan and log it.
set -u
IF="${FB_IF:-wlan0}"; DRY="${FB_DRYRUN:-0}"
if [ "$DRY" = 1 ]; then echo "watchdog: would wait 25s then fallback=dhclient+fullscan"; exit 0; fi
for _ in $(seq 1 25); do ip -4 addr show "$IF" 2>/dev/null | grep -q 'inet ' && exit 0; sleep 1; done
logger -t fastboot "watchdog: no IP after 25s — forcing stock DHCP + full scan"
wpa_cli -i "$IF" reconfigure >/dev/null 2>&1 || true
dhclient -1 "$IF" >/dev/null 2>&1 || true
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: `ok: state-machine guard + watchdog logic` then `ALL TESTS PASSED`.

- [ ] **Step 5: Wire live + verify both branches + induced-failure recovery**

```bash
PI='ssh -i ~/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@192.168.1.206'
bash provisioning/dietpi/fastboot/fbsnap.sh take pre-L5
for f in fastboot-guard.sh fastboot-watchdog.sh; do
  cat provisioning/dietpi/fastboot/$f | $PI "cat > /boot/fastboot/$f; chmod +x /boot/fastboot/$f"; done
# wifi-fastpath consults the guard; watchdog runs as a late oneshot.
$PI 'printf "[Unit]\nDescription=Fast-boot recovery watchdog\nAfter=fastboot-net.service\n[Service]\nType=oneshot\nExecStart=/boot/fastboot/fastboot-watchdog.sh\n[Install]\nWantedBy=multi-user.target\n" > /etc/systemd/system/fastboot-watchdog.service; systemctl daemon-reload; systemctl enable fastboot-watchdog.service'
# cache-hit path:
bash provisioning/dietpi/fastboot/measure-ttssh.sh L5-steady | tee -a provisioning/dietpi/fastboot/fastboot-results.md
# induced total network failure: blackhole the pinned bssid + delete lease; watchdog must recover SSH.
$PI 'rm -f /var/lib/dhcp/dhclient.wlan0.leases; sed -i "s/bssid=[0-9a-f:]*/bssid=00:00:00:00:00:00/" /etc/wpa_supplicant/wpa_supplicant.conf; systemctl reboot'; sleep 90
bash provisioning/dietpi/fastboot/measure-ttssh.sh L5-watchdog-recover | tee -a provisioning/dietpi/fastboot/fastboot-results.md
```
Expected: `L5-steady` ≈ best-so-far; `L5-watchdog-recover` still returns SSH (watchdog forced stock DHCP+scan). Record. If recovery fails → `fbsnap.sh restore pre-L5`; the watchdog is mandatory (it protects the recovery channel) so this must pass before proceeding.

---

### Task 8: Diminishing-returns determination + summary (the wall)

**Files:**
- Modify: `provisioning/dietpi/fastboot/fastboot-results.md` (append a "Summary" section)

- [ ] **Step 1: Analyze the results table**

Read `provisioning/dietpi/fastboot/fastboot-results.md`. Compute the cumulative best kept mean vs baseline. Identify the first layer (in applied order) after which each subsequent kept layer gained < 0.5 s, OR where the remaining time equals the irreducible floor: take the best run's `systemd-analyze` (kernel time) + the WiFi-assoc time from `critical-chain` as the floor.

- [ ] **Step 2: Write the summary** — append to `fastboot-results.md`:

```markdown
## Summary — The Wall
- Baseline mean: <X>s  →  Final kept mean: <Y>s  (reduction <X-Y>s)
- Kept layers: <list with per-layer deltas>
- Rejected/reverted: <list with reasons>
- Irreducible floor: kernel <k>s + WiFi-assoc <w>s ≈ <k+w>s (what bounds it)
- Path A slot fires at uptime ≈ <p>s (network-independent; future treadmill_io ceiling)
- Stopping reason: <"<0.5s gains" | "hit kernel/WiFi floor">
- Recovery validated: L1 stale-cache ✓ / L2 bad-BSSID ✓ / L5 watchdog ✓
- Bluetooth: active throughout ✓
```
Fill `<...>` from the actual recorded numbers (no placeholders in the final file).

- [ ] **Step 3: Leave Pi in best-kept state**

Ensure the Pi is running the final kept configuration (not a snapshot/induced-failure state): `bash provisioning/dietpi/fastboot/measure-ttssh.sh final-verify` → confirms SSH + records the final number. (No commit.)

---

### Task 9: Fold-back into the provisioning toolkit (TDD)

**Files:**
- Modify: `provisioning/dietpi/Automation_Custom_Script.sh`
- Modify: `provisioning/dietpi/prepare-sd.sh`
- Modify: `provisioning/dietpi/build-image.sh`
- Test: extend `provisioning/dietpi/tests/test_fastboot.sh`

- [ ] **Step 1: Write the failing test** — append before `echo "ALL TESTS PASSED"`:

```bash
# --- fold-back: only KEPT layers are wired; Automation script is idempotent & sources fastboot ---
KEPT="$FB/kept-layers.txt"   # written by Task 8 step 3 (one layer id per line)
[ -s "$KEPT" ] || fail "kept-layers.txt missing (Task 8 must record kept layers)"
grep -q 'fastboot' "$HERE/../Automation_Custom_Script.sh" || fail "Automation_Custom_Script.sh must apply fastboot artifacts"
grep -qE 'apt-get install.*bluez' "$HERE/../Automation_Custom_Script.sh" || fail "fold-back must idempotently install bluez (appliance needs Bluetooth; not in base image)"
# idempotent guard present:
grep -q 'fastboot.applied' "$HERE/../Automation_Custom_Script.sh" || fail "fold-back must be idempotent (marker guard)"
# build-image / prepare-sd stage the fastboot dir:
grep -q 'fastboot' "$HERE/../prepare-sd.sh" "$HERE/../build-image.sh" || fail "toolkit must stage provisioning/dietpi/fastboot"
pass "fold-back wired, idempotent, only kept layers"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh`
Expected: FAIL — Automation script has no fastboot wiring.

- [ ] **Step 3: Write `kept-layers.txt` + minimal fold-back**

Task 8 Step 3 also writes `provisioning/dietpi/fastboot/kept-layers.txt` (one id per kept layer, e.g. `L0`,`L1`,`L2`,`L3a`,...). Append to `provisioning/dietpi/Automation_Custom_Script.sh` (before its final `exit 0`), an idempotent block that, guarded by `/boot/fastboot/.fastboot.applied`: (i) installs `bluez` and enables `bluetooth.service` if not already present (`command -v bluetoothctl >/dev/null || apt-get install -y --no-install-recommends bluez; systemctl enable bluetooth`) — the appliance requires Bluetooth and the base image lacks it; (ii) installs the units/scripts for **only** the ids in `kept-layers.txt` and applies the static OS/firmware strips that were kept; then `touch /boot/fastboot/.fastboot.applied`. In `prepare-sd.sh` and `build-image.sh`, add `provisioning/dietpi/fastboot/` to the staged file set (same mechanism that stages `dietpi.txt`/`Automation_Custom_Script.sh`). Keep changes minimal and follow existing staging patterns.

- [ ] **Step 4: Run test + full toolkit suite**

Run: `bash provisioning/dietpi/tests/test_fastboot.sh && bash provisioning/dietpi/tests/test_lib.sh && bash provisioning/dietpi/tests/test_build_image.sh`
Expected: all three end `ALL TESTS PASSED`.

- [ ] **Step 5: Record (no commit)**

Append to `fastboot-results.md`: "Fold-back complete — kept layers wired into Automation_Custom_Script.sh + staged by prepare-sd.sh/build-image.sh; toolkit suites green." Leave the tree dirty for the owner's commit gate.

---

## Self-Review

**1. Spec coverage:**
- Two-path arch / Path A slot → Task 2 (units) + Task 8 (probe timing). ✓
- Method: snapshot→apply→3-cycle→keep/revert → Task 1 (harness+fbsnap) + every layer task. ✓
- B1 sshd decouple → Task 2. B2 cached-lease → Task 3. B3 BSSID pin+fallback → Task 4. B4 Path A → Task 2. ✓
- C1 OS strips validated-once (Bluetooth kept) → Task 5 (+ mandatory BT gate). C2 firmware no-disable-bt → Task 6. C3 state machine → Task 7. C4 watchdog/recovery → Task 7. ✓
- D1 KPI rig → Task 1. D2 per-layer protocol → Tasks 2–7. D3 fold-back → Task 9. ✓
- "The wall" stopping rule → Task 8. Self-recovery proofs → Tasks 3/4/7 step 5. ✓
No gaps.

**2. Placeholder scan:** The only `<...>` are in Task 8's summary template, explicitly required to be filled from recorded numbers ("no placeholders in the final file"); not a plan placeholder. All scripts are complete. No "TBD/handle errors" prose.

**3. Type/name consistency:** `measure-ttssh.sh`, `fbsnap.sh`, `wifi-fastpath.sh`, `wifi-learn.sh`, `fastboot-guard.sh`, `fastboot-watchdog.sh`, `treadmill-critical.target`, `fastboot-probe.service`, `fastboot-net.service`, `10-ssh-fastboot.conf`, `kept-layers.txt`, `fastboot-results.md`, `/boot/fastboot/`, the `PI` ssh prefix, and the keep-rule (≥0.5 s) are used identically across all tasks. Layer ids L0–L5/L3a–d are stable. `FB_*` env overrides match between scripts and their tests.

Self-review passed.
