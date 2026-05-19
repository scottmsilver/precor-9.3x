# mDNS Device Discovery — Design Spec

**Date:** 2026-05-18
**Status:** Approved 2026-05-19 — user spec-review gate passed (corrected:
false "zero client changes" TLS claim fixed; HTTPS prerequisite added).
Proceeding to writing-plans.

## Problem

The native apps (Kotlin/Android, iOS "Treddy") require the user to manually
type the treadmill's URL/IP on a setup screen. The treadmill is a screenless
appliance with no good way to surface its address. The web UI is exempt — it
is served by the same process it talks to (same-origin), so it never needs
discovery.

Goal: clients find the treadmill on the LAN automatically, with manual entry
retained as a fallback. This also satisfies the project rule "never hard code a
server URL in code" — discovery replaces the hardcoded setup defaults.

Out of scope: BLE-based discovery/transport, and zero-network WiFi onboarding.
BLE onboarding is tracked as a separate backlog item, explicitly non-blocking.

## Prerequisite (satisfied 2026-05-19)

The `scheme=https` TXT record below is only honest because commissioned Pis
actually serve HTTPS — which was a *separate* fix, not part of this work:
`setup.sh` now mints a per-device self-signed cert on the Pi (`precor-9_3x-41a`,
commit `3b364b7`), the Android client trusts it (`trustAllTls()`, commit
`3e21412`, regression-tested `precor-9_3x-3y0`), and iOS already did
(`TrustAllDelegate`). That shipped, so the contract below is valid; mDNS does
not re-open it. (Live re-verify of the ship-check probe is tracked separately
under `precor-9_3x-454`.)

## Service Definition (the contract)

The Pi advertises exactly one DNS-SD service over mDNS:

- **Type:** `_treadmill._tcp` — a custom type so clients ignore unrelated
  `_http._tcp` services on the LAN; the port travels in the SRV record so no
  address is hardcoded.
- **Instance name:** `Treadmill on %h` — Avahi `%h` wildcard resolves to the
  Pi hostname (e.g. `rpi-zero`), giving each device a human-readable name.
- **Port:** `8000`.
- **TXT records:** `scheme=https`, `path=/`.

Clients construct the base URL as `<scheme>://<resolved-host>:<port><path>`,
where `<resolved-host>` is the `.local` name Avahi maps to the Pi's current IP.
The self-signed cert will not match the `.local` name — acceptable because
both apps trust it unconditionally. **Correction (this was not "free"):** a
`hostnameVerifier { _,_ -> true }` only bypasses CN/SAN *mismatch*, not chain
validation. Android needed a trust-all `X509TrustManager` added —
`trustAllTls()` in `AppModule.kt` (shipped under `precor-9_3x-41a`, commit
`3e21412`; regression-tested `precor-9_3x-3y0`). iOS needed no change:
`TrustAllDelegate`'s `URLCredential(trust:)` already bypasses chain validation
correctly. Net: with the Prerequisite shipped, mDNS discovery itself adds **no
further TLS work** on either client.

## Pi Side

Publishing approach: **static Avahi service file** (decided over runtime
registration / `avahi-publish` unit). The OS does the advertising, so it
survives a `server.py` crash, adds no dependency, and fits the existing
declarative manifest model. A brief stale advert while `treadmill-server` is
down is harmless: the client's connect-poll fails and falls back.

Changes:

1. **New file** `deploy/treadmill.avahi-service` (Avahi service XML),
   installed to `/etc/avahi/services/treadmill.service`, owner `root`, mode
   `0644`.
2. **`deploy/manifest.txt`**: add a `file` entry for it so a flashed image and
   an rsync'd Pi stay byte-identical (the single-source-of-truth guarantee).
3. **`deploy/lib-artifacts.sh`**: extend the manifest dest allowlist (currently
   `/usr/local/bin`, `/etc/systemd/system`, `~`/`/home/`) to also permit
   `/etc/avahi/services/`.
4. **`deploy/setup.sh`**: add `avahi-daemon` to the idempotent OS-prereq
   auto-install block (same pattern as `libpigpio1`), then
   `systemctl enable --now avahi-daemon`. A bare DietPi image may not ship it;
   this makes a bare and a fully-provisioned image converge.

No daemon code changes. `treadmill_io`, `ftms-daemon`, `hrm-daemon`, and
`server.py` are untouched.

## Client Side

Both Kotlin and iOS get the feature (project dual-platform rule). The web UI is
explicitly unchanged.

A small, isolated discovery unit per platform feeds the existing setup flow and
emits a common shape: `DiscoveredTreadmill { name, host, port, scheme }`.

- **Kotlin:** `NsdManager` (platform built-in, zero new dependencies). New
  `TreadmillDiscovery` class browses `_treadmill._tcp`, resolves results, and
  exposes discovery state to `SetupScreen`.
- **iOS:** `NWBrowser` (Network framework). New `TreadmillDiscovery` type with
  the same output shape. Requires `Info.plist` keys `NSBonjourServices`
  (`_treadmill._tcp`) and `NSLocalNetworkUsageDescription`. `SetupView`
  consumes the same states.

### Setup-screen behavior

1. If a saved server URL exists and connects, skip discovery entirely
   (preserves today's fast path; no behavior change for existing users).
2. Otherwise start a ~4 s background scan. The manual-entry affordance is
   visible the entire time and never blocked.
3. **Exactly one** result → auto-connect, briefly showing
   "Found your treadmill: ● Rpi-zero".
4. **Two or more** results (primary + hot-spare) → a picker list with friendly
   names and addresses; user taps one.
5. **Zero** results after the timeout → the current manual form, unchanged.

The chosen/discovered URL is written through the existing persistence path
(`ServerPreferences` on Kotlin, `UserDefaults` on iOS), so reconnect logic and
the rest of the app are untouched. Discovery runs only while the setup screen
is shown and stops on connect.

## Edge Cases / Error Handling

| Case | Behavior |
|---|---|
| No devices within timeout | Fall through to manual form (today's behavior) |
| Multicast blocked / different subnet / AP client isolation | Discovery finds nothing → manual fallback. Documented limitation. |
| iOS Local Network permission denied | Treated as "found nothing" → manual fallback, no crash |
| Stale advert (server.py down) | Connect-poll fails → existing error + manual option |
| Primary + hot-spare both advertising | Picker (case 4) |

Discovery is purely additive: it never blocks the user and never removes manual
entry.

## Testing

Two tiers, per project standard.

- **Unit (fast):** the pure mapping `service info (host/port/TXT) → base URL`,
  isolated from the platform discovery APIs. Kotlin test with a mocked
  `NsdServiceInfo`; Swift test for the equivalent mapping. This is the logic
  most likely to carry bugs.
- **Integration (Pi):** add a non-belt assertion to `deploy/ship-check.sh` —
  `avahi-browse -rpt _treadmill._tcp` resolves with the expected port and TXT
  records. Add a well-formed-XML check on the installed service file.
- **Manual:** documented on-LAN discovery test from a real phone, covering
  primary-only and primary+spare.

## Deliverables (beads issues)

1. **Pi:** Avahi service file + `manifest.txt` entry + `lib-artifacts.sh`
   allowlist + `setup.sh` avahi prereq/enable + `ship-check.sh` assertion.
2. **Kotlin:** `NsdManager` discovery + `SetupScreen` auto/picker/manual states
   + URL-mapping unit test.
3. **iOS:** `NWBrowser` discovery + `Info.plist` keys + `SetupView` states +
   URL-mapping unit test.
4. **Docs:** CLAUDE.md (discovery mechanism + `_treadmill._tcp` contract),
   provisioning note.
5. **Backlog (separate, non-blocking):** "BLE WiFi onboarding" future option,
   tracked but out of scope for this spec.

## Open Risks

- Avahi service-file publishing must be verified on real DietPi hardware (a
  bare image may lack `avahi-daemon`; `.local` SSH resolution alone does not
  prove the Pi can publish a DNS-SD service). `setup.sh` addresses this; verify
  end-to-end on the actual Pi.
