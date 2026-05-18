# Design: Fast-Boot Phase — Pi Zero 2 W DietPi Appliance

**Date:** 2026-05-17
**Status:** Approved (design); implementation plan pending
**Scope:** Minimize boot time on the live `rpi-zero` (Pi Zero 2 W, DietPi 10.3,
ifupdown + dhclient), using time-to-SSH-key-auth as a measurable proxy, while
restructuring the boot so a future network-independent critical service
(treadmill_io) can start early and independently of WiFi. Validated wins fold
back into the `provisioning/dietpi/` toolkit.

## Context

Baseline measured on `rpi-zero`: kernel ~3.2 s + userspace ~11–14 s; boot-side
`/proc/uptime` at first SSH-key-auth ≈ 16 s (range 15–18 s over 3 cycles);
realistic power-on→SSH ≈ 18–22 s including the uninstrumented Pi
firmware/bootloader stage. The single dominant cost is `ifup@wlan0` ≈ 6.5 s
(WiFi association + DHCP), because `ssh.service` is ordered `After=network.target`
which waits on it. `sshd` itself starts in ~0.33 s — it is not the bottleneck.
The stack is DietPi-managed ifupdown (`/etc/network/interfaces`,
`networking.service`) with dhclient; a cached lease already exists at
`/var/lib/dhcp/dhclient.wlan0.leases`; `wpa_supplicant.conf` has one network
block with no `bssid=` and `scan_ssid=1` (full-channel active scan every boot);
regdomain `US`; current AP BSSID `e0:63:da:28:69:fc`, channel 6 (2437 MHz).

## Locked Decisions

| Decision | Choice |
|---|---|
| KPI | Time-to-SSH-key-auth (proxy). Structural deliverable = decoupled two-path boot. |
| Target | Best-effort minimize, layer by layer, until a real wall (kernel/WiFi-RF floor). No numeric gate. |
| Risk posture | Network path must auto-recover (protects SSH as the recovery channel). OS-level strips validated once, then static (wrong strip = manual/reflash, no runtime fallback). |
| Network approach | Cached-lease fast path + background DHCP validate, on ifupdown/dhclient, with fallback to full DHCP + cache refresh. |
| Stack | Keep DietPi's ifupdown (no networkd/NM switch — avoid fighting DietPi). |
| treadmill_io | Not deployed this phase. Only the early, network-independent insertion slot is defined and measured. |
| Bluetooth | Kept fully enabled (service + radio). Not stripped. |

## Architecture

**Two independent critical paths, no ordering dependency between them:**

- **Path A (future, network-independent) — defined, not filled.** A systemd
  target `treadmill-critical.target` (`DefaultDependencies=no`,
  `After=local-fs.target sysinit.target`, explicitly **not** `After`/`Wants`
  `network*`) so a future daemon (treadmill_io) can start as soon as kernel +
  `/dev` + udev are ready, never waiting on WiFi/DHCP. This phase ships only the
  target plus a throwaway `fastboot-probe.service` in it that logs a timestamp +
  `/proc/uptime`, establishing and measuring the Path A floor.
- **Path B (this phase's KPI) — WiFi → IP → sshd**, optimized independently.

**Method:** layered, live, measured, revertible. Snapshot `/etc` and the boot
FAT to `~/fastboot-snapshots/<timestamp>/` before any change. Apply one
optimization layer at a time; measure with the canonical rig; keep only on a
repeatable improvement with zero SSH-reachability regression, else revert.

**"The wall" stopping rule:** proceed down the layer stack (sshd-decouple →
cached-lease → BSSID-pin → unit strips → firmware/cmdline) until a layer yields
< ~0.5 s repeatable improvement or remaining time is an irreducible floor
(kernel init, WiFi association RF time). Record the floor and what bounds it.

## Path B Mechanism

**B1 — sshd decoupled from `network.target`.** Drop-in
`/etc/systemd/system/ssh.service.d/fastboot.conf` removes the network ordering
(`After=sysinit.target`, no `network-online`). sshd listens within ~kernel+1 s;
it simply cannot be *reached* until WiFi associates, but no longer serializes
behind it.

**B2 — Cached-lease fast path + background DHCP validate.** A script (ifupdown
`pre-up` / oneshot) reads the last lease from `dhclient.wlan0.leases`; if
present and unexpired, assigns that IP/route/DNS statically and non-blocking
(interface usable in << 1 s), then runs `dhclient` in the background to
confirm/renew. Cached IP fails ARP-probe or dhclient returns a different lease →
reconfigure to the DHCP answer and rewrite the cache. No/expired cache → normal
blocking DHCP (first-boot path). Self-recovering by construction.

**B3 — WiFi pinned fast block + generic fallback block.**
`wpa_supplicant.conf` carries two networks: a high-`priority` block with
`bssid=`, `scan_freq=`/`freq_list=` for the learned AP and no `scan_ssid`; and a
lower-`priority` generic block (SSID+PSK, full scan = current behavior).
wpa_supplicant attempts the pinned single-channel association first
(sub-second when the AP is unchanged); on association-timeout it falls through
to the generic full-scan block. BSSID/freq are re-learned on every successful
association and the pinned block rewritten, so a router channel change
self-heals on the next boot. Regdomain stays `US`.

**B4 — Path A slot (defined, not filled).** `treadmill-critical.target` +
`fastboot-probe.service` as described in Architecture; proves and measures the
network-independent early slot without porting treadmill_io.

## OS Strips & Firmware (Validated Once, Then Static)

**C1 — Unit strips**, each its own measured layer, kept only if it helps with
no function/SSH regression; no runtime fallback (per risk posture):
`fstrim.service`/`.timer` out of the boot path (timer later, not at boot);
`keyboard-setup`/`console-setup` (headless); extra `systemd-fsck@` on the FAT
boot partition (rootfs fsck retained); `triggerhappy`; `dietpi-ramlog` only if
log-loss is acceptable (validate); `systemd-timesyncd` made non-blocking.
**`bluetooth.service` is NOT stripped — kept enabled.**

**C2 — Firmware / `cmdline.txt` / `config.txt`:** `disable_splash=1`,
`boot_delay=0`, `initial_turbo=30`, remove plymouth if present, trim unused
HDMI/audio init. **`dtoverlay=disable-bt` is NOT applied** (would disable the
onboard Bluetooth radio; Bluetooth must keep working — the FTMS/HRM BT daemons
run on this Pi later). Boot-partition files are snapshotted and revertible.

**C3 — First-boot vs steady-state state machine.** A guard script chooses the
*network* path from cache markers:
- First boot / cache-miss (`/boot/dietpi/.install_stage` incomplete, OR no
  lease cache, OR no learned-BSSID marker): run the slow correct path (full
  first-run, DHCP DISCOVER, full scan); on success persist learned facts (lease
  on disk, learned BSSID/freq into the pinned wpa block, a `fastboot.learned`
  marker on the data partition).
- Steady-state / cache-hit: run the fast path (B2/B3); every fast-path boot
  refreshes the cache so it stays self-correcting.
OS strips (C1/C2) apply unconditionally (static by decision); the state machine
governs only the network fast-vs-slow choice, which must self-recover.

**C4 — Recovery-channel safety.** The cached-lease and BSSID-pin layers always
retain the DHCP/full-scan fallback. A `fastboot-watchdog` oneshot forces a clean
fallback to stock DHCP+scan and logs it if no IP is obtained within 25 s
(generous — exceeds a worst-case full-scan + DHCP DISCOVER, so it trips only on
genuine failure, not a slow-but-working boot).
The snapshot/revert discipline is the human escape hatch.

## Measurement & Fold-Back

**D1 — Canonical KPI rig:** the existing 3-cycle reboot harness. Per cycle
records reboot→port22, reboot→key-auth, the authoritative Pi-side `/proc/uptime`
at first SSH-key-auth, `systemd-analyze`, `critical-chain ssh.service`, and the
Path A probe timestamp. Report mean/min/max over 3 cycles. A layer is accepted
only on a repeatable improvement with zero SSH-reachability regression across
all 3 cycles.

**D2 — Per-layer protocol:** for each layer in order
(sshd-decouple → cached-lease → BSSID-pin → unit strips → firmware/cmdline):
snapshot → apply → 3-cycle measure → keep+record or revert. A running
`fastboot-results.md` logs before/after and the decision so "the wall" is
visible in the data.

**D3 — Fold-back into the toolkit (reproducibility):**
- Network/WiFi fast-path scripts, the sshd drop-in, and
  `treadmill-critical.target` ship as files staged by
  `prepare-sd.sh`/`build-image.sh`.
- One-time OS strips + firmware/cmdline edits applied idempotently by
  `Automation_Custom_Script.sh` (already runs once at end of first-run setup).
- `dietpi.txt` knobs used where one exists rather than scripting.
- "Learned" markers live on the data partition (survive reboots, regenerate on
  a fresh flash → first boot = cache-miss = correct slow path).
- The toolkit test harness gains a check that the fast-path files/units are
  present and well-formed.

## Out of Scope

Porting/deploying treadmill_io; switching network manager (networkd/NM);
numeric SSH-time SLA; Bluetooth changes; multi-AP roaming design; kernel
rebuild/initramfs surgery; anything on the Pi 4 (`rpi`).

## Testing

Per project rule (tests are real): the boot/timing behavior is verified by the
documented live 3-cycle measurement protocol (D1/D2) on real hardware — that is
the acceptance evidence. The fold-back artifacts get dependency-free unit
checks in the existing `provisioning/dietpi/tests/` harness (fast-path files
present, units well-formed, state-machine guard logic). No mocked "it boots
fast" tests — the KPI is measured on `rpi-zero`.
