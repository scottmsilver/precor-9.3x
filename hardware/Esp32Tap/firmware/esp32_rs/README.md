# Esp32Tap — Rust safety core

Rust port of the Esp32Tap ESP32-S3 **safety core**. The committed C++ core in
`../esp32/` is untouched and remains the reference and fallback; this tree is a
sibling, not a replacement-in-place.

The network/application tier is **out of scope** here. `../esp32/`'s
uncommitted net tier keeps building exactly as before.

---

## Layout

```
esp32_rs/
├── Dockerfile                    pinned toolchain (IDF v5.5.4, espup v0.15.1,
│                                 esp Rust 1.90.0.0, ldproxy 0.3.4)
├── sdkconfig.defaults            PLAN normative key set + Rust-specific keys
├── sdkconfig.defaults.qemu    -> ../esp32/sdkconfig.defaults.qemu   (symlink)
├── partitions_esp32tap.csv    -> ../esp32/partitions_esp32tap.csv   (symlink)
├── build/  build_qemu_test/      idf.py-shaped artifacts (gitignored)
│
├── safety_core/    CRATE 1 — portable, no_std, ZERO dependencies, host-tested
├── esp32tap/       CRATE 2 — the ESP32-S3 binary (esp-idf-hal + esp-idf-sys)
├── difftest/       CRATE 3 — host-only differential harness vs the C++ core
│
├── experiments/wdt_qemu_control/  plain-C control proving the task-WDT panic
│                                  path is unreachable under esp-QEMU
└── tools/
    ├── build.sh                  containerized build -> build/, build_qemu_test/
    ├── qemu_smoke.sh          -> ../../esp32/tools/qemu_smoke.sh    (symlink)
    ├── check_pins.py          -> ../../esp32/tools/check_pins.py    (symlink)
    ├── run_harness.sh            runs the COMMITTED harness against this image
    ├── check_case_parity.py      GATE: 148 cases, 1:1 names, 3-way chain
    ├── check_log_contract.sh     GATE: exact harness log strings
    ├── check_sdkconfig.py        GATE: build_safety_manifest.py's own rules
    └── dump_capture_fixtures.py  real capture data -> difftest/fixtures/
```

**Three independent crates, no virtual workspace.** A workspace containing both
a `build-std` xtensa member and host members forces a default target on every
member, so `cargo test -p safety_core` would try to build tests for xtensa.
Split, each crate gets its own `.cargo/config.toml` and its own `Cargo.lock`.

---

## Commands

```bash
# Host — no Docker, no espup, plain stable rustc. The 148 ported cases.
cd safety_core && cargo test

# Host — differential against the COMMITTED, UNMODIFIED C++ core.
cd difftest && cargo test

# Language-agnostic gates. All four are REQUIRED and run automatically at the
# top of tools/build.sh, before the container starts.
python3 tools/check_pins.py            # GPIO map vs design.py
python3 tools/check_case_parity.py     # 148/148 + the 3-way model chain
python3 tools/check_unsafe_budget.py   # the REAL unsafe containment (see below)
python3 tools/check_wdt_chain.py       # every checkable link of the WDT chain

# Generated-sdkconfig gate (also run inside tools/build.sh, which FAILS on it).
python3 tools/check_sdkconfig.py build/sdkconfig --label prod
python3 tools/check_sdkconfig.py build_qemu_test/sdkconfig --allow-qemu

# Device images (pinned container).
docker build -t esp32tap-rust:build .
tools/build.sh                 # -> build/ and build_qemu_test/
# Reproducible: three from-scratch builds (cargo target dirs wiped between
# each) are byte-identical. This is a PLAN requirement, not hygiene —
# `bundle_sha256` is how a bench log names the artifact it measured, so
# identical source must give identical bytes. Requires
# CONFIG_APP_REPRODUCIBLE_BUILD=y; without it IDF stamps __DATE__/__TIME__
# into esp_app_desc and every build differs.
tools/check_log_contract.sh

# The UNMODIFIED equivalence gates, against the RUST image.
IDF_IMAGE=esp32tap-rust:build tools/qemu_smoke.sh
tools/run_harness.sh            # S8 (below) + -m "not net": S1-S7 + S6 x2 + encoders = 15

# S8 alone — PLAN normal exit, on target. Lives OUTSIDE the committed harness
# (which stays byte-identical to HEAD) and imports it as a library.
cd tools/qemu_scenarios && python3 -m pytest . -v

# The C++ gates must still pass, untouched.
make -C ../esp32/host test
bash ../esp32/tools/qemu_harness/run.sh
```

`IDF_REF=v5.5.4` is a **hard requirement**, not hygiene: the
`espressif/idf:release-v5.5` tag tracks a branch, and IDF commit `b70607c08b7`
added a `sdmmc_host_t` field that esp-idf-hal 0.46.2 builds with an exhaustive
Rust struct literal — the crate stops compiling. Rust's exhaustive struct
literals give up the version tolerance the C++ deliberately keeps with
zero-init-then-assign.

---

## What the type system carries here

* **`#![forbid(unsafe_code)]` on `safety_core`** — compiler-enforced, and
  `forbid` cannot be lifted by an inner `allow` (that is a hard error).
  On the **binary**, the containment is partly compiler-enforced and partly
  gate-enforced, and the difference matters:
  * `src/tasks/`, `src/context.rs` and `src/pins.rs` carry their own
    module-level `#![forbid(unsafe_code)]` → compiler-enforced.
  * `main.rs` carries `#![deny(unsafe_code)]` and grants `#[allow]` to exactly
    `hal`, `log` and `qemu_test`. **`deny` alone does NOT contain anything**:
    it is a lint level any module can lift for itself with an inner
    `#[allow(unsafe_code)]`. An earlier version of this README (and of
    `hal/mod.rs`) claimed otherwise; a reviewer disproved it by counterexample
    (`qemu_test/mod.rs` does contain an unsafe block). Corrected 2026-07-28.
  * The hole `deny` leaves is closed by **`tools/check_unsafe_budget.py`**, a
    required gate in `tools/build.sh`: it asserts the allowlist of
    unsafe-bearing files, the allowlist of `allow(unsafe_code)` sites, a
    `// SAFETY:` comment on every unsafe block, and the exact line budget
    (69 production lines across `hal/` + `log.rs`; 22 more in the
    never-flashed `qemu_test/`). Its counting rule is stated in the script, so
    the number is reproducible rather than asserted.
* **`no_std` + never naming `alloc`** in `safety_core`, so a heap allocation in
  the serial read path or the emulate cycle is a compile error. The C++ relies
  on review for this and in fact allocates (`kv_build` and
  `EmulationCycle::value_for` both return `std::string` on the cycle path).
* **Newtyped units.** `SpeedHundredths` is reachable only through
  `SpeedTenths::to_hundredths()`, so the ×10 to the wire unit cannot be skipped
  or doubled. `Feedback::from_gpio(NcHigh, NoHigh)` cannot take its arguments
  swapped.
* **`Phase`/`TransferPhase` own the transfer deadline and feedback candidate**,
  so `self.phase = Phase::Proxy` destroys both. In C++ they are three
  independent fields every emergency path must remember to clear.
* **`Option<Lease>` + `LeaseExpiry`** replaces a four-field invariant.
* **`PrevValue` is owned and `Copy`**, so the dangling-`string_view` finding the
  C++ `key_cache.h` documents is impossible rather than test-guarded.
* **`SafetyTimeoutFired`** is an unforgeable token: the mode→controller
  back-mirror cannot be invoked without having observed the timeout.
* **`SafetyIo::apply(OutputIntent)`** is the only output write site, so
  tx-before-relay is single-sourced instead of a bypassable convention.

## What it does NOT carry — honest limits

Of the four defects found in the C++ core, Rust addresses **one**:

| Defect | Status |
|---|---|
| Dangling `string_view` into a destroyed frame | **Eliminated as a class** (`PrevValue` is owned) |
| FreeRTOS tick misconfiguration | Still possible. `const _: () = assert!(configTICK_RATE_HZ == 1000)` — **parity** with the C++ `static_assert`, not an improvement |
| 10 ms feedback-qualification cadence bug | Still possible. Carried ONLY by boot-envelope cases 9/10 and S3/S7a timing |
| First-frame-nonzero ordering bug | Still possible. Narrowed by the mirror signature, the timeout token and (since the re-entry fix) the `EmulateSessionId` argument; the property lives in `EmulateTaskPolicy` + boot-envelope cases 8 and the re-entry case + S3's `first14[0] == b"[inc:0]"`. **One concrete instance of this class was found and fixed here and NOT in the C++** — see the re-entry note below |

**The re-entry instance (found by verification, fixed in Rust only).** The C++
`EmulateTaskPolicy` edge-detects emulate entry on a BOOL. A gap-safe normal
exit + re-acquire + second entry fits inside one 100 ms emulate-task period, so
the bool reads `true, true`, the arm edge is missed, `cycle.reset()` never
runs, and the second session's first transmitted burst carries the owner's
motion. Reproduced, then fixed here by passing `Option<EmulateSessionId>`,
which cannot alias two sessions. The C++ core still has it (that tree is
reference/fallback and is not modified by this port); filed as a defect against
it. Note what this says about the honest-limits table: Rust did not PREVENT
this class — a test and a newtype did.

Uncheckable by any compiler and carried entirely by the model, the 148 cases and
the harness: relay entry/exit ordering, fail-closed on unknown feedback,
`BOTH_CLOSED` as a latched fault in every mode, `BOTH_OPEN` as transit-only, the
exact-deadline-loses rule, the 1.5 s console freshness, the 4 s lease, the
0–120 / 0–30 / 0–198 clamps, and the 3-hour timeout.

Two verification gaps that are **not Rust's fault** and must not be papered over:

* **The task-WDT panic path has never been validated in either language.** A
  plain-C control on the same emulator (`experiments/wdt_qemu_control/`) stalls
  identically with no panic, so `qemu_smoke.sh`'s
  `forbid "Task watchdog got triggered"` currently forbids an unreachable
  condition — so 11 of `qemu_smoke.sh`'s 12 assertions are load bearing and one
  is not, and "all 12 green" must not be read as covering the WDT chain.
  Pre-existing gap in the C++ firmware's own verification; needs bench hardware,
  and belongs on the treadmill-contact checklist as a bench gate.
  **What IS automated now (2026-07-28):** `tools/check_wdt_chain.py`, a required
  build gate, verifies every link of that chain that is checkable from the
  repository — all three supervised tasks subscribe to the task WDT, ABORT if
  the subscribe fails, and feed it; the bounded feedback window does NOT feed it
  (feeding a last-resort guard from inside a spin is how a stall goes
  unnoticed); the generated sdkconfig carries the 2 s panic/reset/no-delay key
  set (via `check_sdkconfig.py`); and `RELAY_CMD` has a pull-down to GND in
  `design.py`, so a Hi-Z pin releases K1 rather than floating. It narrows the
  gap to exactly one unverifiable link and it does **not** close it: that the
  panic actually resets the SoC and releases K1 within 2.25 s remains a bench
  measurement with a scope on the contacts.
* **The feedback window busy-waits holding the safety mutex** for up to ~10 ms
  (non-yielding `esp_rom_delay_us` at priority 10 on core 0), bounded by the
  controller's own 10 ms deadline and far under the 2 s task WDT. Since
  2026-07-28 it is bounded a SECOND time, without trusting the clock:
  `MAX_WINDOW_POLLS` (4x the normative poll budget) makes it fail closed on its
  own — worst case ~40 ms of relay-closed time on a defective clock instead of
  2 s and a reboot — and that path is now host-tested against a frozen clock
  (`fork_extensions.rs`) instead of being an unvalidated claim. It now
  re-samples TREAD_OK every 200 µs (the C++ window does not, and tests the
  cached value — up to ~15 ms of software blindness against a 10 ms bench
  gate), but the real TREAD_OK-to-NC latency is still a bench measurement, not
  a software guarantee.
* **QEMU timing is not faithful** (`Ets::delay_us(500)` measured 2041 µs). The
  200 µs `FEEDBACK_POLL_US` window and the 2 ms `K1_TRANSIT_US` model are
  emulator-tuned; the sub-millisecond feedback window needs a real relay on a
  real board regardless of language.

---

## Dependencies

**Device runtime — 3 direct, all Espressif:** `esp-idf-hal` 0.46.2,
`esp-idf-sys` 0.37.2, plus our own `safety_core`. Build-dep `embuild` 0.33.

**`esp-idf-svc` is deliberately excluded** — it is needed only for NVS, which
only the network tier uses, and it drags in `serde`, `embedded-svc` and their
tails. Verified absent from the target graph.

`safety_core` and `difftest` have **zero dependencies**, dev or otherwise.

The ~50-crate transitive tail is pulled in **by Espressif's crates, not chosen
by us**, and is inherent to the std/ESP-IDF path.

**Written ourselves rather than taking a crate** — `FixedStr<N>` (instead of
`heapless::String`, which is already in the graph), the QT command queue
(instead of `heapless::spsc`), the structure-aware fuzz generator (instead of
`arbitrary`/`proptest`), and the `g++` invocation in `difftest/build.rs`
(instead of `cc`).
