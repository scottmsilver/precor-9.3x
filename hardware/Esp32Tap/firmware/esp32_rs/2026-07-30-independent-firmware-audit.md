# Esp32Tap independent firmware audit — 2026-07-30

## Scope and baseline

This audit examined `origin/main` commit `e50b31a`, not the contents of an
already-used development worktree. The intended behavior was derived from the
Raspberry Pi server and Android client source, then compared with the Rust
route registration, feature graph, generated artifacts, and fresh tests.
README statements and issue descriptions were treated as hypotheses, not
evidence.

The audit did not flash or connect to a treadmill. No ESP32-S3 serial device
was present on the host.

## Verdict

The Rust safety/controller and the full-feature QEMU image contain substantial,
well-tested work. The QEMU image exercises core control, programs, persistence,
TLS, mDNS announcements, WebSocket state, coach behavior, and the non-radio
part of BLE control.

The firmware does **not** yet work as the intended standalone Raspberry Pi
replacement on the target hardware:

1. A clean checkout cannot build because the required
   `partitions_esp32tap.csv` is absent from Git. The repository-wide `*.csv`
   ignore rule hid the development worktree's copy.
2. The default production build excludes both `net` and `ble`. Artifact
   inspection found no `/api/*`, `/ws`, HTTPS-server, mDNS, or NimBLE strings.
3. The only implemented network bring-up is QEMU OpenETH with a DP83848 PHY.
   There is no ESP32 WiFi station or provisioning path.
4. The QEMU API is a useful subset of the Pi contract, not the complete
   surface. Missing routes include `/api/reset`, `/api/emulate`, `/api/proxy`,
   `/api/program/generate`, `/api/gpx/upload`, `/api/config`, `/api/log`,
   `/api/tool`, the voice/TTS routes, and `/api/background/advise`. Profiles
   are deliberately single-profile/no-avatar, and `/ws` has no `kv` feed.
5. Three safety assertions fail: manual motion dies after four seconds and
   latches a fault; a running program retakes the relay after a physical
   console takeover; and a later speed request reports success/nonzero status
   while the latched fault keeps the relay open.
6. The radio path, runtime BLE heap, task-WDT-to-relay timing, real relay
   timing, WiFi, real power-cycle persistence, and treadmill electrical
   behavior remain unmeasured on hardware.

The LAN API also has no authentication. The Android client currently accepts
all server certificates and hostnames, so TLS encrypts traffic but does not
authenticate the treadmill.

## How it is built

`esp32_rs` contains independent Cargo crates rather than a workspace:

* `safety_core`: allocation-free `no_std` protocol/mode/safety logic.
* `program_core`: bounded program, JSON, and record logic.
* `reqbudget`: fixed request-slot admission.
* `ble_core`: host-testable FTMS and HR protocol bytes.
* `coach_core`: bounded Gemini request/reply interpretation.
* `difftest`: host comparison against the C++ reference.
* `esp32tap`: the ESP-IDF target binary.

`tools/build.sh` runs source/configuration gates, then uses the pinned
`esp32tap-rust:build` container (ESP-IDF 5.5.4 and Espressif Rust 1.90.0.0).
It builds:

* `build/` with no Cargo features: production safety/controller image;
* `build_qemu_test/` with `qemu-test,net,ble`: full-feature test image.

After supplying only the missing partition CSV in the disposable audit
checkout, the clean build succeeded:

| Artifact | Image size | Factory partition | Features |
|---|---:|---:|---|
| production | 473,520 B | 2,097,152 B (22%) | safety/controller |
| QEMU test | 1,299,008 B | 2,097,152 B (61%) | qemu-test, net, BLE |

The rebuilt QEMU application SHA-256 was
`a34ed57d3683f52f95585eb38ed64700c96d6be88f9d7d5b9561a7d50bcb318f`,
identical to the committed test image.

## Fresh verification evidence

The first clean-checkout `tools/sweep.sh` run took 590 seconds. All host and
QEMU behavioral suites it reached passed, while the sweep correctly returned
nonzero because the build lacked the partition CSV; log-contract and production
smoke were downstream failures. The archive-only harness-lock error was
rechecked in a true Git worktree and passed.

After injecting only the missing CSV:

* `tools/build.sh`: passed for production and QEMU-test images.
* `tools/check_log_contract.sh`: passed.
* `tools/qemu_smoke.sh`: passed on the production image; one boot, no panic,
  abort, or reboot.
* Selected full-feature scenarios: 51 passed in 208.06 seconds
  (`program`, `records`, `ws`, `tls`, `mdns`, BLE degraded-mode, and BLE
  control-point suites).
* `test_reviewer_attacks.py`: 3 failed, 4 passed in 65.69 seconds.
* Host suites from the sweep: safety 160, request budget 6, program 86, coach
  49, BLE protocol 92, and differential 19 tests passed.

The ordinary sweep intentionally excludes `test_reviewer_attacks.py` because
it is red by design. A green ordinary sweep therefore must not be interpreted
as “safe for treadmill contact.”

## Recommended verification workflow

The release workflow should have four explicit artifacts/gates:

1. **Clean-source gate:** create a clean checkout, assert every build input is
   tracked, build both images from scratch, and record source plus bundle
   hashes. Never accept committed binaries or ignored files as build inputs.
2. **Host contract gate:** run the pure-crate tests, C++ differential tests,
   pin/config/unsafe/WDT checks, and a machine-generated Pi/Android-to-ESP route
   coverage report.
3. **QEMU behavior gate:** run the normal sweep, deep memory/slow-client tests,
   and the reviewer-attack suite. A release candidate requires the adversarial
   suite to be green, rather than excluded.
4. **Hardware gate:** build a production image that actually enables the
   application and BLE tiers with real WiFi; then measure boot/provisioning,
   API/app compatibility, BLE advertising/control/HRM, heap and stack
   high-water marks, power-cycle persistence, watchdog-to-relay release,
   TREAD_OK/relay timing, and fault recovery before any treadmill contact.

Only the fourth gate can answer whether the radio, relay, power, and physical
treadmill behavior work as intended.

## Tracked follow-up

* `precor-9_3x-344`: clean-checkout partition-table build failure.
* `precor-9_3x-p0q`: no flashable standalone production artifact or real WiFi
  path.
* `precor-9_3x-d03`: program retakes the relay after console takeover.
* `precor-9_3x-3yk`: manual lease expiry and unrecoverable/misreported fault.
* `precor-9_3x-l0h`: BLE hardware verification.
