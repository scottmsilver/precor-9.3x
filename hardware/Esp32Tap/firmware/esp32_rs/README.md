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
├── reqbudget/      CRATE 4 — fixed per-request memory, no_std, zero deps
├── program_core/   CRATE 5 — the interval executor (port of ProgramState) and
│                             the stored-record codec, no_std, forbid(unsafe),
│                             depends only on safety_core
├── recstore/       CRATE 6 — fixed-record flash rings, no_std, ZERO deps,
│                             torn-write tested at every byte offset
├── ble_core/       CRATE 7 — FTMS encoding + HR parsing, no_std,
│                             forbid(unsafe), depends only on safety_core.
│                             Port of rust/ftms + rust/hrm PROTOCOL logic;
│                             their bluer transport is Linux-only and stayed
│                             behind. NO RADIO CODE — see "BLE" below
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

**Independent crates, no virtual workspace.** A workspace containing both
a `build-std` xtensa member and host members forces a default target on every
member, so `cargo test -p safety_core` would try to build tests for xtensa.
Split, each crate gets its own `.cargo/config.toml` and its own `Cargo.lock`.

---

## Commands

```bash
# Host — no Docker, no espup, plain stable rustc. The 148 ported cases.
cd safety_core && cargo test

# Host — the interval executor (67 cases) and the request budget. Sub-second;
# this is the fast inner loop when working on program logic.
cargo test --manifest-path program_core/Cargo.toml
cargo test --manifest-path reqbudget/Cargo.toml

# Host — the BLE protocol tier (89 cases, ~1 s). Every vector is the Pi
# daemon's, ported byte for byte. This is where nearly all the real BLE
# behaviour is verifiable; the radio is not (see "BLE" below).
cargo test --manifest-path ble_core/Cargo.toml

# Host — the AI coach's judgement (41 cases, ~0 s): the bounded streaming reply
# extractor, the clamps applied to a model's tool call, the truncation salvage.
# The fixtures are the shapes that BREAK things (a reply delivered one byte at a
# time, one cut off mid-argument, one 100x the buffer, an HTTP error envelope, an
# HTML 502 page), because the live endpoint is deliberately not a gate.
cargo test --manifest-path coach_core/Cargo.toml

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

* **`#![forbid(unsafe_code)]` on the pure crates** — `safety_core`,
  `program_core` and `ble_core` — compiler-enforced, and `forbid` cannot be
  lifted by an inner `allow` (that is a hard error). `check_unsafe_budget.py`
  now asserts the line is still THERE on all three: `program_core` and
  `ble_core` carried it with nothing verifying it, and deleting it is a
  one-character act no other gate would have noticed.
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
    (69 production lines across `hal/` + `log.rs`; 346 more in code the
    flashed image does not contain — `qemu_test/` behind `feature = "qemu-test"`
    and `net/` behind `feature = "net"`). Its counting rule is stated in the
    script, so the number is reproducible rather than asserted.
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

**The AI coach's live endpoint is not a gate, on purpose.** `net/coach.rs` +
`coach_core/` are proven against a local stub the test controls
(`tools/qemu_scenarios/test_coach.py`, 19 scenarios), and that stub is strictly
better than the real API at everything except two things it cannot do: confirm
that the request body Gemini ACCEPTS is the one we build, and confirm that the
embedded CA bundle validates the real chain. Those need a live per-device key and
are the opt-in `test_coach_live.py` (`COACH_LIVE_KEY=…`), tracked by bead
`precor-9_3x-zt8`. Wiring a real call into the sweep would buy those two facts and
pay for them with an intermittent — a live secret, nondeterministic words, and
somebody else's uptime.

**The Android app does not yet show a coach answer**, and that follows from the
shape of the call rather than from an oversight. `POST /api/chat` answers `202`
immediately and delivers the reply on a `/ws` `coach` frame (and at
`GET /api/chat`), because IDF runs ONE httpd worker and holding it for a
multi-second model round trip is a Stop-button outage with the belt moving. The
app decodes the 202 without complaint and renders nothing; bead
`precor-9_3x-lsx` is the one-place change in `TreadmillViewModel.handleMessage`,
and it is a no-op against the Pi.

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

### Slice 3 (TLS + mDNS) — what is proven, and what is not

Proven in QEMU, deterministically, by `tools/qemu_scenarios/`:

* a real TLS handshake against a certificate the DEVICE generated for itself,
  and the app-facing banner served over it (`test_tls.py`);
* that there is **no plaintext listener** on :8000 to fall back to, which is
  what makes the advertised `scheme=https` honest;
* that the identity **survives a real SoC reset** — the certificate is
  byte-identical across a `QT reboot`, and the second boot logs
  `tls: identity loaded from NVS` (`test_tls_persistence.py`);
* that the DNS-SD announcement is genuinely **on the wire**: the frames the NIC
  transmits are captured with QEMU `filter-dump` and the PTR/SRV/TXT/A records
  decoded from the pcap, then compared field-by-field against the Pi's
  `deploy/treadmill.avahi-service` (`test_mdns.py`).

Not proven, and not claimed:

* **The private key is stored in NVS in the clear.** Flash encryption is not
  enabled on this part, so anyone with physical access to the flash has the key
  — the same exposure as `key.pem` on the Pi's SD card. Trust-on-first-use is
  only as strong as that.
* **No certificate rotation, revocation or expiry handling.** The validity
  window is a fixed 2024–2044 because the device has no wall clock at boot.
* **Cross-power-cycle persistence on real hardware.** The reboot proof resets
  the SoC inside one QEMU process; the flash file survives because it is a file.
  A physical power cycle, and flash wear/corruption, need a board.
* **An mDNS QUERY cannot be answered through the QEMU harness**, and this is an
  emulator limit rather than a device one: slirp does not translate the source
  address of a `hostfwd`-ed packet, so a query arrives claiming to come from
  127.0.0.1, and `espressif/mdns` drops any packet whose source is outside the
  interface's subnet before parsing it. The capture proves the announcement;
  the query/response path needs a real L2 network.
* **The QEMU-test image is 838,560 bytes of the 1 MB factory partition (79%).**
  TLS (mbedtls X.509 write + PEM + ECDSA) and mDNS are what moved it and the
  headroom is not comfortable; the 8 MB N8R8 layout is the answer when it runs
  out, not a harness edit. `tools/build.sh` now parses the GENERATED partition
  table and fails the build if the image does not fit — the old hard-coded
  "factory partition = 2097152" label was wrong by 2x (the custom 8 MB table
  does not apply under esp-idf-sys, so the stock 2 MB single-app table with a
  1 MB factory partition is what is actually flashed), so every headroom figure
  derived from it was half the real utilisation. The **production image is
  454,176 bytes (43%)** and contains neither feature: `net` is off there, and
  `xtensa-esp32s3-elf-nm` on the two ELFs finds `httpd_ssl_start`, `mdns_init`
  and `mdns_service_add` in the qemu-test binary and **none of them** in the
  production one — `--gc-sections` drops them.

### Slice 4 (the interval executor) — what is proven, and what is not

`program_core/` is a port of `python/program_engine.py`'s `ProgramState`,
written against `python/tests/test_program_engine.py` as its specification: the
1 s tick loop, interval advance, pause/resume, skip, prev, extend,
adjust-duration and completion. 67 host cases, 0.00 s. Every deliberate
divergence is enumerated in that crate's `lib.rs` header; the load-bearing ones
are **bounded storage** (24 intervals, 20-byte names, durations capped at 24 h,
all derived from one `reqbudget` slot and asserted by a test), **no
encouragement engine** (a coaching-tier concern with no on-device consumer), and
**pause zeroing the belt** — which merges `server.py::_apply_pause_toggle` into
`toggle_pause`, because there is no layer above this one to forget to do it.

Proven in QEMU, deterministically, by `tools/qemu_scenarios/test_program.py`
(10 scenarios, no wall-clock sleeps — every wait is on a guest-observed fact):

* a program POSTed over HTTPS is stored, echoed back in the Pi's exact
  `to_dict()` shape, and **runs**: intervals advance on the GUEST clock with no
  request in flight, and the new interval's speed reaches the motor wire;
* starting one drives a **real relay transfer** and the first `[hmph:...]` on
  the wire after that transfer is `[hmph:0]` — PLAN entry step 6 — even though
  interval 0 commands a nonzero speed;
* a pause holds the program clock across 6 s of guest time AND zeroes the belt;
  a resume restores it; a skip advances immediately and the belt follows;
* a stop runs PLAN's polite exit and **hands the lease back**, so a manual
  command works again afterwards — without `control::release` the executor's
  `NoDeadline` lease would make the device unusable after one workout;
* an oversized program is refused at ADMISSION (413, before a byte is parsed)
  and a malformed one by the parser (400), neither disturbing what is loaded.

**Two real defects were found and fixed on the way**, both latent on the
existing HTTP path and both invisible to a test that issues one request:

1. `net/api.rs` minted a NEW connection generation per request, and
   `SafetyController::connect` emergency-stops when a fresh generation
   supersedes a lease-holding one. The **second** `POST /api/speed` would have
   dropped the relay and re-entered emulate, mid-stride.
   `test_repeated_manual_commands_do_not_cycle_the_relay` asserts it directly.
2. `request_emulate` zeroes commanded motion (PLAN "enter at zero", correct),
   which **discarded the motion accepted in the same call**. A single
   `POST /api/speed 3.0` from PROXY entered emulate and left the belt at zero;
   only a second request moved it. `control::command` now re-asserts the intent
   after a successful entry. The zero-frame guarantee is unaffected — it is
   enforced by the emulate cycle's session-edge gate, not by `speed_tenths`.

Both fixes live in `esp32tap/src/control.rs`, which is now **the one path to
the belt**: HTTP and the executor call the same function, so there is one
lease, one set of clamps, one auto-emulate policy and one `apply_outputs()`.
The refactor moved that logic out of an `unsafe extern "C"` handler body into a
`#![forbid(unsafe_code)]` module, which is why ten new endpoints cost only
+37 unsafe-attributed lines.

Not proven, and not claimed:

* **A loaded program does not survive a reboot.** It lives in RAM. That is a
  decision, not an omission: a reboot drops the relay and ends the run, so
  silently resuming a workout on the next boot would be wrong without an
  explicit human gesture. Slice 5 persists the program to history and offers
  `/{id}/resume` as exactly that gesture; the executor itself still writes no
  flash, which is what keeps it off any path that can block.
* **A manual speed change DURING a program is refused (409), not merged.** The
  executor owns the lease while a program runs, and taking it away would
  emergency-stop — relay open, belt dead. The Pi's answer is
  `ProgramState.split_for_manual`, which rewrites the running manual program at
  the new speed; that behaviour needs its own slice and is not faked here.
* **`_check_encouragement` is not ported at all** — see the divergence list.
* **Real hardware.** Everything above is QEMU. The executor has never driven a
  physical treadmill.

---

### Slice 5 (the persistence tier) — what is proven, and what is not

The device keeps its own data: program history, saved workouts, run records
and the profile. `/api/programs/history` (+ `/{id}/load`, `/{id}/resume`),
`/api/workouts` (list, save, rename, delete, load), `/api/runs` and
`PUT /api/profiles/{id}` are served straight out of flash — three `recstore`
rings on the `storage` partition (20 history, 20 workouts, 4 runs) plus one NVS
blob for the profile.

**Memory is the design, not a consideration**, and there are three mechanisms:

* `recstore::Ring` keeps `SLOTS*4+8` bytes resident and nothing else — 200
  bytes for all three rings, independent of what is stored.
* A record is read into a `reqbudget` slot, decoded, used and forgotten.
  Records are a **binary** format (`program_core::record`) rather than the JSON
  they are served as, precisely so the worst case is ~936 bytes and fits one
  2048-byte slot; the served JSON of the same record would not. Nothing parsed
  outlives the request.
* List responses are **chunked** through a fixed 512-byte buffer, so a
  20-entry, ~50 KB history body has no relationship to memory at all.

That is the property whose absence let ~15 unauthenticated requests exhaust the
C++ tier's heap and reboot it mid-run, dropping the relay.
`test_records.py::test_resident_memory_does_not_grow_with_writes_or_with_requests`
drives 240 writes and 10 full list reads and asserts both the ring residency
and the free heap.

Proven in QEMU by `tools/qemu_scenarios/test_records.py` (11 scenarios):
a saved workout survives a **reboot**, read back through the same endpoint;
history dedups by name and holds its cap of 20, losing the oldest;
rename rewrites the stored program too and delete un-marks the history entry
that claimed it; a run record is **created** once a session passes 5 s,
**checkpointed in the same slot** past 30 s (appending would empty the 4-slot
ring in two minutes) and **finalised** with the real reason — `user_stop` when
the program is stopped, `program_complete` when it finishes; and a profile
rename survives a reboot, which is what makes offering rename honest.

**Three defects were found by building it**, all of them stack, all of them a
reboot — and a reboot drops the relay:

1. The history write ran inside `net::program::post_impl`, already the largest
   frame in the image, and its lookup decoded a whole ~1 KB `Entry` per slot to
   compare a name. Fixed by `record::peek_entry`, an ~80-byte `Head` that every
   by-id and by-name scan uses instead, and by counting the write in
   `POST_FRAME_BYTES` so the compiler asserts it.
2. `HTTPD_STACK_BYTES` 10240 fitted the result by ~450 bytes, which is not a
   margin when level-1 interrupts run on the interrupted task's stack. Raised
   to 14336, with the arithmetic written down.
3. The session recorder overflowed a 6144-byte stack on one read-modify-write
   of a stored entry. Raised to 12288, measured rather than chosen.

**An adversarial review then found five more**, four of which are reachable by
any client on the LAN:

* **The store lock was held across a network write.** A list response chunked
  to the socket under the store lock, and a chunk flush blocks for up to
  `send_wait_timeout` (1 s). A client that stopped reading mid-list parked the
  WDT-supervised session recorder behind the httpd worker — 2 s watchdog,
  reboot, relay dropped. Each record is now read under a short hold and the
  lock is released before anything is written to the socket. The resume reply
  had the same shape with the PROGRAM lock, and now renders through
  `net::program::respond_state`, which buffers and releases before sending.
* **The action in the path was not matched.** `POST .../history/{id}/delete`
  loaded a program and `DELETE /api/workouts/{id}/load` deleted a workout,
  because a wildcard route hands the handler everything under its prefix and
  "not `resume`" was treated as "load". Every action is now matched exactly and
  anything else is 404. `PUT /api/profiles/{id}` did not check the id at all.
* **The history id and the loaded program were set separately**, so a 30 s
  checkpoint landing between them wrote one program's progress into the other's
  entry. Both are now published under the program lock, and the recorder reads
  them in one hold.
* **`hundredths * 2 / 100` wrapped.** `{"value":21474836.47}` parses to
  `i32::MAX`; doubling it is a negative incline in release and a panic in
  debug. It is `/ 50` now — identical for every representable value, and total.
* **A failed flash write reported success**, and a failed finalisation was
  never retried, so a finished run could read `in_progress` forever. Writes are
  checked, the checkpoint clock only advances on a write that landed, and a
  finalisation retries every tick until it does.

`tools/check_unsafe_budget.py` also grew a real fix: its lexer did not
understand Rust raw strings or char literals, so `br#"{"ok":false}"#` leaked
braces into "code" and two `unsafe extern "C"` functions in `net/api.rs` were
never counted at all. The published figure was 89 lines short of what the
stated rule measures. The rule now measures what it says (`470` at the previous
commit, `528` here).

`tools/sweep.sh` gained `records` and `storepers`. `test_store_persistence.py`
was committed, passing, and invoked by NOTHING — the same hole
`verify_harness_copy.py` was in — and it is the only gate that proves a record
reaches real flash and survives a real SoC reset.
`test_reviewer_attacks.py` is deliberately still not a gate, and the reason is
written at the site: it is RED BY DESIGN (4 of its 7 fail on an untouched
tree), a record of open defects in the safety/control tier rather than a
regression check, and gating on it would train everyone to ignore the sweep.

Not proven, and not claimed:

* **No timestamps.** There is no RTC and no SNTP, so `created_at` is `""` and
  `last_used`/`started_at`/`ended_at` are `null`. Both are shapes the Kotlin
  models already accept, and the app renders `usage_text`/`last_run_text`
  rather than raw dates — but a device that showed you when you ran would need
  a clock it does not have.
* **`last_run`/`last_run_text` are always `null`/`""`.** The Pi links runs to
  programs by fingerprint; this device does not.
* **`end_reason: "disconnect"` is never written.** The Pi produces it when the
  client's WebSocket drops; nothing about a run here depends on a client being
  present, so inventing that dependency to produce the value would be worse
  than not producing it.
* **Smaller than the Pi, deliberately.** 4 runs against its 200, 20 workouts
  against its unlimited. It is a notebook, not an archive.
* **Still one profile, still no avatars.** `POST /api/profiles` and
  `DELETE /api/profiles/{id}` are not implemented; `has_avatar` is `false` and
  says so.
* **Real hardware.** Everything above is QEMU.

### BLE (`ble_core`) — what is proven, and what is not

`ble_core` is the FTMS and Heart Rate **protocol** tier: characteristic
encoding, Control Point parsing, and the km/h <-> mph conversion. It is a port
of `rust/ftms/src/protocol.rs` and `rust/hrm/src/scanner.rs` — the working Pi
daemons — and it keeps their test vectors byte for byte, including the two
places they are lossy (the truncating mph->km/h divide that makes 12.0 mph
encode as 1930 while Speed Range advertises 1931; the uint24 distance field).
If this crate and a daemon disagree, this crate is wrong: a phone that has been
talking to the Pi must see the same bytes from the ESP32.

Proven, on the host, in about a second (92 cases):

* Every characteristic's bytes: Treadmill Data (13 bytes, fixed flags 0x040C),
  Feature, Speed Range, Incline Range, Training Status, Machine Status, and the
  three-byte Control Point response.
* Control Point parsing is TOTAL over untrusted input — every 1- and 2-byte
  input, and 3-byte inputs across the whole opcode/parameter space, return
  rather than panic; short payloads and unknown opcodes are refused.
* The unit conversion in both directions, including the daemon's truncation at
  the extremes, a round-trip within 0.1 mph across **every** speed the belt can
  be commanded (not the daemon's 8 samples), and the integer half-percent
  rounding proven equal to the daemon's floating-point rule over all 65 536
  values an `i16` Control Point write can carry.
* The belt edge: a Control Point write becomes a `CpEffect` in `safety_core`
  newtypes — never a raw integer — and a speed write cannot disturb the incline
  or vice versa. `esp32tap/src/control.rs` is THE ONE PATH TO THE BELT and owns
  the lease, the clamps and the auto-emulate policy.
* **Clamping here is asymmetric, and the asymmetry is the point.** ABOVE the
  range nothing is clamped: a peer asking for 40 mph converts faithfully, is
  refused by the controller, and is told `INVALID_PARAM` — where the Pi
  silently substituted 12 mph and moved the belt at a speed nobody asked for. A
  clamp in front of the controller would be a second opinion about what is
  safe. BELOW the range the daemon's clamp is kept: `SetTargetInclination(-10%)`
  becomes 0.0% and succeeds, because "go under your minimum" has exactly one
  safe reading, Supported Inclination Range already publishes the floor to the
  client, and refusing it left a route-simulating app's belt stuck on the last
  uphill grade for the whole descent. Both directions are pinned by tests so
  neither can be "tidied" into the other.

### The BLE tier on the device (`esp32tap/src/ble/`, feature `ble`)

`ble_core` is bytes; this is the radio. `ble/ftms.rs` registers the FTMS GATT
service and advertises; `ble/central.rs` scans for a heart-rate strap and
subscribes to its notifications; `ble/mod.rs` owns the NimBLE port lifecycle.
`src/hr.rs` holds the reading (fixed-size, `#![forbid(unsafe_code)]`) and
`net/hrm.rs` serves it on `/api/hrm*`, the `/ws` `hr` frame and `/api/status`.

**A Control Point write reaches the belt through `control::command` and nowhere
else** — the same function `POST /api/speed` and the interval executor use, so
one lease, one set of clamps, one auto-emulate policy. The BLE surface shares
`Surface::Http` deliberately: a third surface would be a third lease holder,
and a phone on Bluetooth would emergency-stop the same phone on HTTP every time
the user touched the other control.

**Stop is the exception, and it is not deniable.** Routing it like every other
effect meant that while the interval executor held the lease it came back
`Reject::NotOwner` — so a user running a program at 6 mph who pressed stop in
Zwift was answered `RESULT_FAILED` with the belt still running, and a BLE-only
peer cannot call `POST /api/program/stop`. `CpEffect::Stop` now takes the route
the app's stop button takes: `ProgramState::stop`, then `apply_plan` with
`release_belt = true`, so the zero is issued through `control::command` under
the lease the executor already holds and cannot be refused. Still one path to
the belt. (The obvious version of that fix — drive the plan, then command zero
again as `Surface::Http` — is WRONG and the QEMU scenario caught it:
`control::release` starts a gap-safe exit and holds the lease until it
completes, so the follow-up hit `NotOwner` too. `net/program.rs` had already
written that rule down for Quick Start.)

**Enabling NimBLE is two Kconfig keys and nothing else** — `CONFIG_BT_ENABLED=y`
plus `CONFIG_BT_NIMBLE_ENABLED=y`. They came from reading esp-idf-sys 0.37.2's
own `src/include/esp-idf/bindings.h:603-688`, which gates the NimBLE headers on
exactly those two symbols (BUILD-OPTIONS.md does not document them; it points
at a `menuconfig` flow that applies to the `pio` builder, not the `native` one
this port uses). `bt` is a BASE ESP-IDF component, so — unlike mDNS — no
`extra_components` and no `bindings_header` are involved.

#### THE FIRST BLE-ENABLED IMAGE REBOOT-LOOPED, and that is the design constraint

`nimble_port_init` does **not** return an error when the controller cannot come
up. Measured, under esp-QEMU 9.2.2 (esp32s3):

```
I (15990) BLE_INIT: BT controller compile version [b7de11e]
I (15991) BLE_INIT: Using main XTAL as clock source
assert failed: 0x4206ea5c <cached disabled>:1753
Backtrace: ...
Rebooting...
```

An `assert()` inside the closed-source BT blob is a panic; under this
firmware's PLAN-normative `CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT` a panic is an
immediate reset; **and a reset drops the relay mid-run**. The device booted,
served HTTPS for a moment, and died, every ~1.8 s, forever.

So "a failing radio is survivable" could not be written as a `match` on a
return code — by the time that call returns there is nothing left to handle. It
is a guard in FRONT of the call: `ble::identity_address` refuses to hand the
controller a part whose eFuse identity address is not a factory unicast MAC
(OUI not `00:00:00`, multicast bit clear). That is a question *about the radio*
and it is meaningful on hardware — a part with a blank eFuse block cannot
advertise a valid address — rather than an "am I an emulator?" sniff, which is
a check that lies on any hardware it has not met. QEMU reports
`00:00:00:00:00:02`, which is why a bare all-zero test was not enough.

The BLE task is also spawned **last**, at the lowest priority in the system,
after the belt is already controllable and the server is already answering, and
it is deliberately **not** WDT-supervised: the watchdog's remedy is a reboot,
and trading a working treadmill for a stalled radio is the wrong trade. That
exemption is written into the normative matrix in `tasks/mod.rs` with its cost
stated (a wedged NimBLE host is not detected or recovered; Bluetooth goes
quiet and nothing else changes).

#### Memory — measured, and one number honestly missing

Same image, same sdkconfig, `--features qemu-test,net` with and without `ble`:

| | no BLE | with BLE | delta |
|---|---|---|---|
| app image | 890 560 B | 1 141 104 B | **+250 544 B** (54% of the 2 MB factory partition) |
| internal RAM free at `heap_init` | 281 024 B (274 KiB) | 252 352 B (246 KiB) | **-28 672 B (28 KiB)** |

The 28 KiB is the static cost of *linking* the stack — `.data`/`.bss` plus the
IRAM carve-out, which on the S3 comes out of the same SRAM (from the link map:
~1.5 KB DRAM and ~18 KB IRAM attributable to `libbt.a`/`libbtdm_app.a`). It is
paid before `app_main` whether or not the radio ever starts.

**The runtime heap cost of a NimBLE host that actually initialises is
UNMEASURED, and no number is quoted for it here.** It cannot be measured
without a radio: the controller aborts before `nimble_port_init` returns. The
firmware carries the instrument for it — `bring_up` samples the free heap
either side of that call and logs `ble: heap cost N bytes (free X -> Y)` — so
the figure comes off the first real board rather than out of a blog post.
Whether the stack fits alongside TLS and the app tier is therefore **an open
question**, and it is the first thing to check on hardware.

#### What QEMU DOES prove: the device survives a dead radio

`tools/qemu_scenarios/test_ble_degraded.py` (sweep gate `bledegrade`, 7 cases,
~36 s) asserts, on a device whose radio was refused:

* one boot, no `Rebooting...`, no `assert failed:`, no Guru Meditation — and it
  fails loudly if NimBLE ever *does* come up under QEMU, because then the suite
  would be vacuous;
* HTTPS still serves the banner and a complete `/api/status` over a real TLS
  handshake;
* `/ws` still pushes `status`, `session` and `hr`;
* `POST /api/speed` still reaches the belt;
* `/api/hrm`, `/api/hrm/select`, `/api/hrm/forget`, `/api/hrm/scan` all answer
  rather than 404 — the Pi's contract with `hrm-daemon` stopped — and a
  malformed address is rejected without wedging the surface;
* the heap does not drift while the parked BLE task ticks.

The whole qemu-test image is built `--features qemu-test,net,ble`, so **every**
scenario in the tree already runs against a device whose radio failed to come
up. That file states the property; the other twenty would go red with it.

#### What QEMU ALSO proves: the whole belt edge, with no radio

The test line used to be drawn one layer too low. Only `access_cb`'s mbuf copy
needs Bluetooth; everything below it — `plan` -> `effect_of` -> `apply` ->
`control::command`, the lease, the clamps, the auto-emulate policy and the FTMS
result mapping — is ordinary Rust operating on the real safety controller and
the real relay. So the qemu-test-only shim verb **`QT ble_cp <hex>`** feeds
bytes to exactly those calls, in the same order `on_control_point` makes them,
and `tools/qemu_scenarios/test_ble_control_point.py` (sweep gate `blecp`, 9
cases, ~20 s) drives the belt edge end to end on a machine with no Bluetooth
adapter in it.

It is not a simulated radio and it is not a second path to the belt; it is the
same two function calls in a build that is never flashed. It exists because two
REAL defects were, until it, establishable only by reading:

* **FTMS Stop was denied by the lease exactly when it mattered.** Every effect
  went to `control::command(Surface::Http, ..)`, so while the interval executor
  owned the belt a Stop came back `Reject::NotOwner` and the peer was answered
  `RESULT_FAILED` with the belt still running at the program's speed. A
  BLE-only peer cannot call `POST /api/program/stop`, so there was no working
  stop at all during a program. Stop now takes the route the app's stop button
  takes. **Both Stop cases were run RED against the pre-fix image.**
* **A negative Set-Target-Inclination was refused** where the Pi daemon clamped
  to 0.0% and answered SUCCESS, so a route-simulating app on a descent left the
  belt on the previous uphill grade for the whole downhill.

Not proven, and not claimed — **QEMU has no BLE radio and there is no board**:

* No advertising, connection, pairing, bonding, MTU negotiation, notification
  or indication has ever run. Every line in `ble/ftms.rs` and `ble/central.rs`
  past the identity guard — and the mbuf copy in `access_cb` above the tested
  belt edge — is UNEXECUTED code.
* The NimBLE host task's stack was **unsized** until 2026-07-29 (the Kconfig key
  was absent, so the build took the IDF default of 4096 B) on the one task with
  the deepest untrusted call chain. It is now 8192 with the arithmetic written
  into `sdkconfig.defaults`, and `uxTaskGetStackHighWaterMark` is sampled after
  every Control Point write — but the high-water mark has never been READ,
  because nothing has ever written a Control Point over a radio.
* NimBLE's runtime heap cost alongside TLS and the app tier is unmeasured (see
  above), and unmeasured is the state in which a device reboots on a real board.
* No real client (Zwift, QZ, Kinomap, Apple Watch, Garmin, a chest strap) has
  connected to this device.
* The HRM strap address is kept in RAM only, not NVS — a known difference from
  the Pi's `hrm_config.json`, so a strap must be re-picked after a power cycle.

That work is tracked in **precor-9_3x-l0h**, which names each unproven item;
it needs a physical board, which does not exist yet.

---

## Dependencies

**Device runtime — 3 direct, all Espressif:** `esp-idf-hal` 0.46.2,
`esp-idf-sys` 0.37.2, plus our own `safety_core`. Build-dep `embuild` 0.33.

**`esp-idf-svc` is deliberately excluded** — it drags in `serde`,
`embedded-svc` and their tails, and its latest release pins `esp-idf-hal` ^0.45
-> `esp-idf-sys` ^0.36, a generation behind the 0.46/0.37 pair this port uses
(only one crate may link `esp_idf_hal`). Verified absent from the target graph.
NVS, TLS and mDNS are all called directly through `esp-idf-sys`.

**One managed (remote) ESP-IDF component: `espressif/mdns` ~1.8.0**, declared as
a `[[package.metadata.esp-idf-sys.extra_components]] remote_component` in
`esp32tap/Cargo.toml`. mDNS is not part of base ESP-IDF; the component manager
resolves it (1.8.2 today, recorded in the generated `components_esp32s3.lock`).

### How the bindings decide what exists (read this before "the symbol is missing")

Two features were blocked for a while on the belief that `esp-idf-sys` was
missing headers. It was not, and both causes are worth stating because the same
mistake is cheap to repeat:

* **`httpd_ssl_*` generated zero symbols** not because `esp_https_server` was
  absent from the build — it was being compiled all along — but because
  esp-idf-sys's stock `src/include/esp-idf/bindings.h` guards its
  `#include "esp_https_server.h"` with `#ifdef CONFIG_ESP_HTTPS_SERVER_ENABLE`,
  a **Kconfig symbol**. The generated sdkconfig said `is not set`. One
  `sdkconfig.defaults` line fixed it.
* **`mdns_*` generated zero symbols** because the component was not in the build
  at all. The same stock header already has
  `#if defined(ESP_IDF_COMP_ESPRESSIF__MDNS_ENABLED) / #include "mdns.h"`, and
  `-DESP_IDF_COMP_<NAME>_ENABLED` is emitted for every component that was
  actually compiled — so pulling the component in is sufficient and **no custom
  `bindings_header` is needed**.

`[package.metadata.esp-idf-sys] esp_idf_components` is the WRONG lever for
either: it is an exclusive whitelist that *trims* the build, so naming a few
components drops all the others and CMake fails. All of this is documented in
`BUILD-OPTIONS.md` inside the `esp-idf-sys` crate source.

`safety_core` and `difftest` have **zero dependencies**, dev or otherwise.

The ~50-crate transitive tail is pulled in **by Espressif's crates, not chosen
by us**, and is inherent to the std/ESP-IDF path.

**Written ourselves rather than taking a crate** — `FixedStr<N>` (instead of
`heapless::String`, which is already in the graph), the QT command queue
(instead of `heapless::spsc`), the structure-aware fuzz generator (instead of
`arbitrary`/`proptest`), and the `g++` invocation in `difftest/build.rs`
(instead of `cc`).
