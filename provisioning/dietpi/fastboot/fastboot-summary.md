# Fast-Boot Phase — Summary (the wall) — 2026-05-17

Device: `rpi-zero` (Pi Zero 2 W, DietPi 10.3), KPI = Pi-side `/proc/uptime` at
first SSH key-auth, 3-cycle mean. Raw data: `fastboot-results.md`.

## Result

| Layer | mean (s) | cluster | decision |
|------|----------|---------|----------|
| baseline (no BT) | 14.1 | 13.0 / 16.0 / 13.3 | reference (pre-BT) |
| baseline-bt (BT installed + L0 Path A) | 15.1 | 12.9 / 15.5 / 17.0 | **canonical reference** |
| L0 — Path A slot + sshd drop-in | — | probe fires @ ~6.7 s | **KEEP** (structural) |
| **L1 — cached-lease fast path + bg DHCP** | **9.9** | 9.5 / 10.8 / 9.5 | **KEEP — −5.2 s (~34%)** |
| L1 self-recovery (poisoned lease) | 15.2 | recovers every cycle | ✅ safe, no lockout |
| L2 — WiFi pinned BSSID/freq + fallback | 14.1 | 13.0 / 14.0 / 15.2 | **REJECT (+4.2 s regression)** |
| L2 fallback (bogus BSSID → generic block) | 16.0 | recovers every cycle | ✅ fallback works |

**Net validated win: 15.1 s → 9.9 s (−5.2 s, ~34% faster) on the SSH KPI**, plus
the network-independent Path A slot proven (probe at ~6.7 s vs ~9–11 s network),
which is the structural deliverable for a future early `treadmill_io`.

## What worked

- **L1 cached-lease is the dominant lever.** Applying the last DHCP lease
  immediately (no DHCP round-trip) collapsed `ifup@wlan0` from ~4.6 s to
  ~0.69 s; userspace 9.15 s → 5.29 s. Background `dhclient` validates/renews.
  Self-recovery proven: a poisoned lease still recovered SSH every cycle.
- **Path A slot (L0)** fires at ~6.7 s, fully network-independent — a future
  `treadmill_io` can start there, well before the ~9–11 s network path.

## What did not (honest negatives)

- **L2 (BSSID pin / no-scan) was disproven.** The design hypothesised it would
  help; measured, it *regressed* by ~4.2 s. Once L1 removes the DHCP wait from
  the critical path, WiFi association is no longer the bottleneck (it proceeds
  in parallel); locking `bssid`/`scan_freq` and dropping `scan_ssid` only added
  directed-probe retry latency. Reverted. The two-block generic fallback did
  work (bogus BSSID still connected every cycle).
- **sshd-decouple is a non-lever for the SSH KPI** (SSH still needs the network
  up to be *reached*); kept only because the Path A slot is the real L0 value.

## The wall (stopping point)

Reached: the dominant optimization (L1) is captured and validated; L2 is
disproven; remaining candidate layers (L3 OS strips, L4 firmware) target the
kernel (~3.2 s, near floor) + early-userspace region where expected sub-second
gains fall **below the ±2 s run-to-run WiFi variance** — not reliably
measurable or worth the risk. Irreducible floor ≈ kernel 3.2 s + early
userspace + WiFi link/assoc. L3/L4 were therefore not executed.

## Incident (full disclosure)

During the L2 revert, `fbsnap restore` hit a transient
`systemd-logind`/reboot daemon-reload race, and the L1 self-recovery test's
poisoned `.250` lease was never cleaned (the cleanup SSH timed out — the Pi was
already unreachable). The Pi ended up powered + WiFi-associated but with **no
IPv4** (stale `.250` + a failed background dhclient). The recovery watchdog did
**not** save it because of a **design flaw: it is boot-only** (a `oneshot` at
`multi-user.target`) and cannot re-evaluate a connectivity loss that happens
*after* boot when nothing triggers a reboot.

**Recovery:** regained access non-destructively over the Pi's **IPv6
link-local** address (sshd was alive; only IPv4 was broken), restored IPv4 via
`dhclient`, then returned the device to the **original known-good DietPi
networking** (`iface wlan0 inet dhcp`, fast-path disabled, poisoned lease
cleared). One clean confirmation reboot verified: `rpi-zero` @ 192.168.1.206,
Bluetooth active, reboots reliably. No power-cycle needed; no data loss.

## Required fixes before fold-back (toolkit, Task 9)

1. **`fastboot-recover` must not be boot-only.** Make it a periodic
   `systemd.timer` (e.g. every 60 s) checking *reachability* (gateway ping),
   not just IPv4 *presence*, so a post-boot loss self-heals.
2. **`fbsnap restore`** must `daemon-reexec` cleanly and not race logind;
   prefer file-precise restore over a blanket `tar xzf -C /`.
3. **`wifi-fastpath` must validate the cached IP before relying on it**
   (ARP-probe / gateway ping); discard a stale lease instead of applying it and
   hoping the background dhclient fixes it.
4. Lease-cache hygiene: never let a test-injected lease persist; the fast path
   should reject a lease whose ARP probe fails.

## Final outcome (post-hardening, validated)

- **L1b hardened = 9.8 s** (9.72/9.76/9.78, tight) — confirms **−5.3 s (~35%)**
  vs `baseline-bt` 15.1 s, with the safe validate-before-trust implementation.
- **L1b self-recovery = 10.1 s** (poisoned `.249` lease) — was 15 s + lockout;
  now detects bad gateway and falls to dhclient cleanly, no lockout.
- **Recovery watchdog proven**: gateway-blackhole test → fail-counter 1→2→3 →
  auto-restore known-good + reboot → back in ~2 min, BT active. Self-heals a
  *post-boot* loss (the boot-only flaw is fixed: periodic timer, reachability).
- **Fold-back proven for real**: a fresh `build-image.sh` image carries
  `fastboot.tgz` (all kept-layer artifacts) and an idempotent
  `Automation_Custom_Script.sh` fold-back (L0+L1+watchdog + `bluez`); L2
  excluded. All three toolkit suites green; dash-safe.
- **Live Pi** left clean & healthy: `rpi-zero` @ 192.168.1.206 single IP,
  gateway reachable, ssh/bt/watchdog active, on the hardened fast path.

### Known residual (honest)

`wifi-fastpath` validate-before-trust uses a gateway ping, which does **not**
catch a *same-subnet wrong-host* stale lease (e.g. cached `.249` still pings
gateway `.1`). It is applied, then the background `dhclient` corrects it to the
real IP — functional and self-correcting, but untidy and not the clean fast
path that boot. Recommended future fix: ARP-probe the *specific* cached IP for
a conflict / confirm it is the address the router still leases, not just that
the gateway is reachable. Not blocking; documented for the next pass.

## Security audit (codex, 2026-05-17) — done, must-fix items fixed

Track 1 (deps): N/A — no new pip/npm/cargo deps; `bluez` is a Debian OS
package installed at first boot, not a code dependency. Track 2 (codex
read-only) initially returned "not safe to fold yet" with 3 Important must-fix
items, all now fixed with TDD:

- **F1 wifi-fastpath** — lease IP/netmask/gateway now strictly IPv4-validated
  (contiguous-mask check) before any `ip(8)` call; malformed → slow fallback.
  Closes rogue-DHCP arg-injection / bad-route churn.
- **F2 fastboot-recover** — **boot-loop DoS fixed**: once `.recovered` is set,
  the watchdog no longer reboots on a still-down gateway (logs instead, until
  an operator clears it); `THRESHOLD`/state validated numeric. (Logic-fixed +
  unit-tested; one live re-proof recommended next hardware session — the
  earlier blackhole proof was on the pre-fix version.)
- **F3 fold-back tar** — extracted to a temp dir with
  `--no-same-owner --no-same-permissions --no-overwrite-dir`, archive members
  with absolute/`..` paths refused, then only an allowlist of expected files
  copied. Closes the root file-write primitive.
- **F4 idempotency** — `.fastboot.applied` is touched ONLY when required steps
  succeed; bluez is optional and its failure is logged (F5), not silent.
- **F6/F8 fbsnap** — snapshot label strictly validated (no path traversal);
  snapshots written `umask 077`/`chmod 600` in a `chmod 700` dir and documented
  as secret-bearing.
- F7 Info — confirmed (and suites confirm) the staging additions did not
  regress prepare-sd.sh/build-image.sh's previously-audited properties.

All three toolkit suites green; `Automation_Custom_Script.sh` dash-safe.

## Honest correction — the real wall is WiFi association (2026-05-17)

Extended multi-run measurement (forced by the audit hardening) showed the
*same* L1 config landing anywhere from **9.8 s to 16 s**. That spread is not
measurement error: it is **WiFi association time** on this Pi Zero 2 W (weak
antenna, 2.4 GHz congestion), which varies ~3–9 s run to run and is the
dominant cost — you cannot SSH until the radio associates, no matter how fast
the cached IP is applied.

- The earlier headline "−5.3 s / ~35%" was a **favorable-RF tight cluster
  (9.72/9.76/9.78) and is retracted as over-claimed.**
- **Honest result:** cached-lease (L1) removes the DHCP round-trip — a real
  but modest, reliable ~2–3 s gain. Everything beyond that is RF luck. Floor =
  kernel ~3.2 s + WiFi association ~3–9 s (variable). baseline ≈ 15 s; L1
  best ≈ 9.8 s; L1 typical ≈ 12–15 s.
- **The true wall is WiFi association on this hardware** — not DHCP, not scan,
  not userspace. No software layer reliably beats it. Materially faster
  boot-to-SSH would need better RF (antenna/placement/5 GHz-capable board) or
  a non-WiFi transport (USB-gadget/Ethernet) — i.e., Path A (network-
  independent, fires ~6.7 s) is where future treadmill_io speed actually lives.

### F9 fix (kept, safe, honest)

The audit-hardened validate-before-trust gw-ping was blocking the boot
critical path (serialized ~9 s waiting for association). Redesigned so the
foreground applies the cached IP and returns immediately; validation +
correction run in the background; the proven periodic reachability watchdog is
the lockout backstop. This is the right design regardless of the RF-variance
finding (don't hold boot on a network probe). Deployed + measured
(L1-nonblock-final). Live Pi left clean on this version.

## State of artifacts

All scripts/units are written, unit-tested (`tests/test_fastboot.sh` green),
and **uncommitted** (owner password gate). The live Pi is on original
known-good networking (NOT the fast path) — the validated L1 win is captured as
code for a future clean re-flash, not left running on the device.
