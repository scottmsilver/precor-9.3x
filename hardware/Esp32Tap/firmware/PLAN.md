# Esp32Tap firmware plan (ESP-IDF port of cpp/protocol + cpp/engine)

**Status: HOLD.** `safety_model.py` is an executable host reference contract,
not production ESP-IDF firmware. No Emulate-capable binary exists yet, and no
repository test substitutes for contact-measured bench evidence. Do not submit
an order, pay, connect this board to the treadmill, or represent it as safe to
operate on the strength of the host model.

Platform: **ESP-IDF 5.x, C++20, `-fno-exceptions -fno-rtti`** — matching the
existing `cpp/` style so `kv_protocol`, `mode_state` and `emulation_engine`
port nearly verbatim. The pigpio bb_serial / DMA-wave layer is replaced by
`driver/uart` with `uart_set_line_inverse(UART_SIGNAL_RXD_INV | UART_SIGNAL_TXD_INV)`
— hardware inversion replaces the hand-built inverted waveforms.

## System split (recap of the accepted architecture)

**ON-MCU (must survive total network loss):** KV streaming parser with all
Postel tolerances (skip 0xFF/0x00, empty `[]`, partial frames,
printable-ASCII guard, 64-byte caps); Proxy as boot/default mode; emulate
14-key/5-burst cycle (100 ms gaps, `part=6`/`diag=0`/`loop=5550`); hmph/inc
hex codecs with noise guards (>5000 hundredths, >500 half-pct rejected);
the FULL safety envelope; an interval executor (port of `ProgramState`'s 1 s
tick loop: intervals/pause/skip/extend) so a loaded workout survives RF
stalls; FTMS BLE peripheral wired directly to the local mode engine; HRM BLE
central with NVS device persistence; HTTPS/WSS control API carrying the
existing newline-JSON vocabulary **plus the HRM verbs**
(connect/disconnect/forget/scan); mDNS `_treadmill._tcp` with
`scheme=https`. Clamps on-MCU now include the application limits — speed
0–120 tenths AND incline 0–30 half-pct (15%) — since the remote box is no
longer a trust boundary (0–198 stays as the absolute hardware guard).

**OFF-DEVICE (server.py, graceful-degradation optional):** Gemini
coach/chat/voice, program *generation*, workout_db, histories/saved
workouts, GPX, profiles, web UI static + TLS, `/api/background/advise`.
server.py swaps its Unix-socket client for a WSS client (same JSON schema);
programs push down to the MCU (push-down-then-mirror; MCU authoritative for
the executing program). The MCU buffers 30 s run-record checkpoints in a
RAM/NVS ring during server outages and replays on reconnect.

## One deliberate hardware-enabled change: Proxy = relay bridge

On Rev B, Proxy mode is a **normally-closed relay bridge** — console
bytes reach the motor through copper, not software forwarding. The engine
still parses the console stream (for telemetry and auto-emulate/auto-proxy
detection) but the forwarding path has zero latency and zero firmware
dependence. Emulate entry = zero speed/incline, energize relay, start
cycle. Emulate exit / watchdog / crash / treadmill-derived power loss
deasserts the relay command and the independent hardware permission. Actual
NC-contact closure latency is a production-board measurement, not an
"instant" software guarantee.

**Task-WDT supervision — scope and action (normative).**
`esp_task_wdt` subscribes **every task whose stall can leave the relay
energized**, not just the serial engine: (1) the serial engine task,
(2) the emulate cycle task (it can deadlock on its TX mutex while the
serial task stays healthy), and (3) the interval executor task (1 s tick).
Any of these stalling while the relay is energized is a state PLAN lists as
uncharacterized (motor pin-6 silence), so each must independently trip the
WDT. The production timeout is **2 s**. The WDT **action must actually release
the relay**: ESP-IDF's task-WDT
default merely prints a warning, so `CONFIG_ESP_TASK_WDT_PANIC=y` is
mandatory (see the sdkconfig list below) — a stall then panics → reset →
GPIO21 Hi-Z → 10 k base pull-down → relay released. Without that option the
"task-WDT stall → relay released" column of the watchdog matrix is
unimplemented. Boot always releases the relay (GPIO21 has a 10 k pull-down
at the driver base).

## Task layout / core pinning (explicit sdkconfig — NOT the IDF defaults)

* **Core 0 (pinned, high prio): serial engine** — UART event-queue RX on
  both channels (≤5 ms poll parity), streaming kv_parse (4 KB buffer), mode
  state + auto-transitions, emulate cycle task (whole-message
  `uart_write_bytes` + tx-done wait behind a mutex; the S3's 128-byte TX
  FIFO makes a ≤50-byte KV message hardware-contiguous — no inter-byte
  gaps, which retires most of the M2 timing risk), safety timers on
  `esp_timer` (monotonic).
* **Core 1: everything RF** — NimBLE host (FTMS GATT server: Feature /
  Treadmill Data 1 Hz / Ranges / Control Point / Machine Status, porting
  `rust/ftms/src/protocol.rs` encodings; HRM central: 0x180D scan, 0x2A37
  subscribe, NVS persistence, mock-HR debug hook), WiFi STA +
  `esp_https_server` (WSS/REST, 1 KB command cap, malformed JSON ignored),
  mDNS, checkpoint buffer/replay.
* Required sdkconfig (defaults put WiFi/BT on core 0 — must override):
  `CONFIG_IDF_TARGET="esp32s3"`,
  `CONFIG_IDF_TARGET_ESP32S3=y`,
  `CONFIG_BT_NIMBLE_PINNED_TO_CORE=1`, WiFi task pinned to core 1, BT
  controller on core 1, `CONFIG_ESP_COEX_SW_COEXIST_ENABLE=y`, WiFi PS
  `MIN_MODEM`, `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=3`,
  `CONFIG_ESP_TASK_WDT_EN=y`, `CONFIG_ESP_TASK_WDT_INIT=y`,
  `CONFIG_ESP_TASK_WDT_TIMEOUT_S=2`,
  **`CONFIG_ESP_TASK_WDT_PANIC=y`**,
  `CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y`,
  `CONFIG_ESP_COREDUMP_ENABLE_TO_NONE=y`,
  `CONFIG_APPTRACE_DEST_NONE=y`, and
  `CONFIG_APPTRACE_DEST_UART_NONE=y`. If
  `CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS` is emitted it must be `0`;
  a generated silent-reboot sdkconfig may omit that hidden/default key.
  Core dumps, apptrace destinations, and nonzero panic/core-dump/apptrace
  waits are forbidden (task-WDT stall must
  panic-reset promptly so the relay releases—the IDF default only logs a
  warning). Brownout detection is enabled, with the highest ESP32-S3 threshold
  strictly below the measured minimum +3V3 of the exact production artifact.
  Panic halt/print-reboot/GDB-stub, runtime GDB stub, OpenOCD debug stubs, and
  `CONFIG_ESP_DEBUG_OCDAWARE=y` are forbidden in an Emulate-capable build.
  Residual core-0 ISRs are accepted;
  the 128-byte UART
  FIFOs (~133 ms of RX buffering at 9600) absorb scheduler jitter.
* **Task stack sizing (QEMU-validated constraint)**: `KvPair` is 128 bytes,
  so a single on-stack `KvPair[16]` array is 2 KB — two of them overflow
  the IDF default 3.5 KB main-task stack and hard crash-loop (observed:
  ~340 consecutive stack-overflow reboots in the esp32s3 QEMU PoC before
  the buffers were made static). Any task that owns parser buffers must
  either keep them static/heap-allocated or get an explicitly sized stack
  (`CONFIG_ESP_MAIN_TASK_STACK_SIZE` / `xTaskCreate` depth ≥ buffers +
  8 KB headroom).

## Executable safety contract (M3 entry gate)

`safety_model.py` is the normative host reference for this section. Its tests
must later run unchanged against the production implementation's adapter. It
does not drive GPIOs and is not flashable firmware.

### Atomic control lease

There is exactly one owner:

```text
(transport, concrete_connection_handle, monotonically_increasing_generation)
```

`transport` is WSS, BLE, or the local EXECUTOR. A WSS owner uses the concrete
connection object/handle; a BLE owner uses the concrete `conn_handle`.
Generation prevents a recycled socket or BLE handle from inheriting an old
lease. Only the exact owner may mutate speed/incline or renew liveness.
Non-owner commands, heartbeats, and disconnects are ignored by the motion
engine. Accepting a higher generation for the same concrete handle first
invalidates every lower-generation active identity. If a lower generation
owns the lease, supersession commands zero and Proxy before registering the
new connection; the new generation remains unowned until an explicit acquire.

WSS and BLE manual ownership use one **4 s total-silence deadline**. There is
no second timer and no 10 s reconnect grace. Owner disconnect immediately
commands zero, deasserts relay and TX enables, and releases ownership.
Reconnect begins unowned at zero and must explicitly acquire a new generation.
The on-device executor owns a non-network lease; RF loss does not end or
silently transfer it, but local safety events, reset, and WDT do.

Every public operation carrying a monotonic timestamp advances all due lease,
console-freshness, and transition deadlines before it may consume or mutate
state. A command or reentrant request at an exact deadline loses to that
deadline. In particular, a complete console frame arriving at age 1.5 s is
rejected before it can replace the stale timestamp.

| Command source ↓ / failure → | 4 s owner silence | WSS drop | BLE drop | reset/brownout | task-WDT |
|---|---|---|---|---|---|
| WSS manual | zero + Proxy | zero + Proxy if exact owner | no effect | hardware Proxy, no resume | hardware Proxy |
| BLE manual | zero + Proxy | no effect | zero + Proxy if exact owner | hardware Proxy, no resume | hardware Proxy |
| Console bridge | no effect | no effect | no effect | NC bridge remains/defaults | NC bridge |
| EXECUTOR | continues if console/safety inputs remain valid | continues | continues | Proxy, program discarded | Proxy, program discarded |

An executor interval override remains executor-owned; a transient WSS/BLE
client never becomes a second liveness authority. The three-hour no-change
policy remains separately testable, but it cannot extend a four-second manual
lease.

### Console freshness and physical STOP limitation

Freshness is the monotonic timestamp of the newest **complete, valid, fully
parsed** console frame. Partial, corrupt, oversized, or merely received bytes
do not refresh it. Emulate entry requires a known baseline younger than
1.5 s. While Emulating, age reaching 1.5 s commands zero and bypass
immediately.

A console STOP whose encoded value was already zero is not universally
detectable from value-change parsing unless captures prove a distinct observed
wire event. Do not claim otherwise. The treadmill's independent physical
safety key remains authoritative.

### Gap-safe relay transition

Normal Emulate entry is exactly:

1. require TREAD_OK, bypass feedback, a fresh console frame, no latched fault,
   and current ownership;
2. command speed/incline zero;
3. configure inverted 9600 8N1, verify ESP_TX physical idle-low, then assert
   TX_ENABLE without sending a byte;
4. wait for a capture-qualified console inter-frame gap, for at most 1 s;
5. assert RELAY_CMD and require the dry-contact feedback pole to report
   Emulate continuously for at least 1 ms, with an actual GPIO sample at the
   end of that interval and before the 10 ms deadline;
6. only then transmit the first complete zero frame.

If no gap arrives within 1 s, entry aborts without moving K1. Wrong or missing
feedback releases K1 and latches a fault.

Normal exit is exactly:

1. transmit and finish a complete zero frame;
2. wait for a capture-qualified gap, for at most 1 s;
3. deassert RELAY_CMD and require bypass feedback continuously for at least
   1 ms, with an actual GPIO sample at the end of that interval and before
   the 10 ms deadline;
4. deassert TX_ENABLE;
5. release ownership.

At the normal-exit gap deadline, deassert RELAY_CMD immediately; remaining in
Emulate is less safe. TREAD_OK loss, stale console, lease expiry, explicit
emergency stop, brownout, reset, and watchdog action never wait for a gap.
`BOTH_CLOSED` feedback is an immediate latched fault in every mode and releases
the relay. `BOTH_OPEN` may be observed as a break-before-make intermediate
state only while waiting for post-command feedback; it never qualifies a
transfer. Boot/reset feedback is unknown, not assumed bypass, until an actual
GPIO sample reports the bypass contact state. A timer tick alone never proves
the 1 ms feedback interval, and a timer or feedback callback at the exact
10 ms boundary produces the same fail-closed timeout.

The production acceptance test must perform at least 1,000 normal entry/exit
cycles and observe MOT6 plus actual K1 contacts. It rejects a byte/frame splice
or order violation. GPIO-only timing is insufficient. Required measured
latencies are: TREAD_OK fault to stable NC at most 10 ms; software
disconnect/lease deadline to stable NC at most 250 ms; injected supervised-task
stall to stable NC at most 2.25 s with the 2 s WDT.

### USB attach

Rev B is self-powered only from treadmill +8 V; USB VBUS is data/presence only.
GPIO7 is `VBUS_PRESENT_N`: LOW means VBUS present and HIGH means absent.
Production code must explicitly invert that signal and must not advertise the
native-USB D+ pull-up while it is HIGH. Espressif's stock self-powered
`vbus_monitor_io` path is active-high, so GPIO7 cannot be passed to it without
an explicit, reviewed inversion/attach strategy. Reset/ROM behavior and
hot-unplug below 3 ms remain bench gates.

Programming requires **both** a USB data cable and current-limited +8 V bench
power on the treadmill-power pins. USB alone cannot power or program Rev B.

## Security (must land before the WSS port is ever enabled)

Per-device self-signed cert (matches the `scheme=https` `_treadmill._tcp`
contract) + per-device bearer token provisioned at first flash. Because
clients are trust-all TLS today, the token is MITM-stealable on the LAN:
**clients TOFU-pin the device cert fingerprint** (web/Kotlin/iOS) and the
token is bound to the cert fingerprint server-side. No plaintext TCP. No
hardcoded URLs anywhere — discovery via mDNS only (MCU advertises the belt
endpoint; the app server is discovered as a second service). MCU-side
clamps (12 mph / 15%) are the last line of defense against a compromised
app-plane host.

## RAM budget (N8, no PSRAM — measured gate at M5)

~380 KB usable SRAM must hold: TLS server sessions (~40–50 KB each),
NimBLE dual-role (~90 KB), WiFi buffers, 4 KB parse buffer, executor state.
Policy: **cap concurrent TLS clients at 2**, reduce
`MBEDTLS_SSL_MAX_CONTENT_LEN` (4 KB is ample for newline-JSON), and publish
a measured `heap_caps` budget at M5. Escape hatch: ESP32-S3-WROOM-1-**N8R2**
(C2913204) is a BOM-only swap (same footprint) if the measured budget
fails.

## Exact production artifact identity

`build_safety_manifest.py` is a fail-closed, non-flashing tool. It validates
the exact production `sdkconfig`, hashes the application, bootloader, partition
table, sdkconfig, host safety model, builder, JSON schema, and this plan, then
emits one deterministic `bundle_sha256`. The machine-readable contract is
hashed first; that hash is combined with the four flash/config artifacts so
there is no circular self-hash.

The build snapshots every input once and fails if any artifact is
missing/empty, if any two hashed inputs share one filesystem identity, if the
application/bootloader do not have ESP image headers, if the partition table
does not have the ESP partition-table form, if its output path or inode aliases
any hashed input, if the 2 s task WDT is
not enabled and initialized with panic/reset, if the panic action is not an
immediate silent reboot, if core dump/apptrace panic work can delay reset, if
the target is not exactly ESP32-S3, if brownout detection is absent, if any halt/debug
mode is enabled, or if the configured brownout selector is not the highest
documented ESP32-S3 threshold below the supplied physical minimum +3V3
measurement. Validation rechecks selector/voltage/measurement correspondence
even when a manifest's hashes were recomputed. The selector numbers are
inverse to voltage: for example, with a measured 3.05 V minimum, level 3
(approximately 2.98 V), not level 7, is the highest supported threshold below
the measurement. The exact schema bytes used for validation are the same
single snapshot recorded in the manifest hash.

The exact sdkconfig gate remains mandatory on every flashed build:

```bash
grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig
```

Every bench log, scope/logic-analyzer capture, and contact-timing record names
the resulting `bundle_sha256`. Rebuilding or changing any covered byte creates
a different identity and invalidates evidence recorded for the prior bundle.

## Milestones (each gates the next)

M1–M3 are **bench-only by definition** — no milestone before the
treadmill-contact gate involves the treadmill. Treadmill work happens only
in TC1/TC2 inside the gate section below.

* **M1 — host-parity build (bench).** `kv_protocol`/`mode_state`/
  `emulation` compiled for linux + ESP32; the existing `cpp/tests` doctest
  suite runs as the golden parity suite on both. UART loopback rig proves
  inverted 9600 8N1 both directions against a recorded Pi capture.
  *Status note (2026-07-27):* the repository-closeable host-side half of
  M1 is done — the portable core (`firmware/esp32/components/portable_core`,
  forked from `cpp/` with per-file provenance) compiles for linux and
  esp32s3, the forked `cpp/tests` doctest suite is green on the host, and
  the new safety-controller host suite (C++ port of `safety_model.py`)
  passes. The esp32s3 build is pinned to `espressif/idf:release-v5.5`
  (the prescribed ESP-IDF 5.x; `-fno-rtti` verified applied via IDF's
  `build/toolchain/cxxflags` response file). The UART loopback-rig half
  of M1 and all bench items remain open; Status stays **HOLD**.
* **M2 — serial engine on the bench rig (bench).** Loopback rig (two
  inverted bench UARTs, or a Pi running `python/tools/listen.py`, replaying
  recorded console bursts through J1/J2): hardware bridge continuity;
  RX-parsed stream matches the `cpp/tests` golden vectors and a
  logic-analyzer diff vs a Pi capture; emulate TX cycle timing measured on
  the analyzer against a Pi capture, characterizing UART-FIFO pacing on the
  emulate TX path (confirmation, not discovery, given the 128-byte FIFO).
* **M3 — emulate + full safety envelope on the bench rig (bench).**
  Exact lease identity/generation, 4 s owner silence, 1.5 s complete-frame
  freshness, gap-safe entry/exit, zero-on-entry, relay-feedback faults,
  physical STOP limitation, 3 h timer, clamps, active-low USB attach, and
  relay release on task-WDT stall of **each** supervised task (serial engine,
  emulate cycle, interval executor — stalled one at a time). The entire matrix
  above has one regression test per cell and actual-contact timing on the
  production artifact.

## Treadmill-contact gate — the single first-contact checklist

The board must never touch the treadmill until every box below is checked.
**This checklist is the only authoritative phrasing of the gate**; README
bring-up step 7 and ORDERING.md point here. (It supersedes the earlier,
self-contradictory wording that defined M2/M3 on the treadmill while also
requiring M3 before treadmill contact.)

1. M1, M2, M3 green **on the bench rig**, evidence (test logs,
   logic-analyzer captures) archived.
2. Safety-matrix bench evidence explicitly includes the WDT column: proof
   that stalling each supervised task releases the relay via panic reset.
   **HARD CONFIG GATE (independent verification, not just a behavior test):**
   `grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig` MUST pass on the *exact*
   sdkconfig of the binary being flashed to the treadmill — the IDF default
   only logs the stall and never releases the relay, so a build missing this
   flag is silently unsafe even though the board looks alive. The WDT-release
   behavior test in this item must have been run on THAT build's sdkconfig,
   not a debug build. The manifest builder must pass all other WDT,
   brownout, and no-halt checks, and the evidence must name its exact
   `bundle_sha256`. (This is the specific gap an independent 2026-07-23
   review flagged: behavior can pass on one build and regress on the flashed
   one; verify the flag on the artifact, every flash.)
3. The 1,000-cycle no-splice analyzer gate and 10 ms / 250 ms / 2.25 s
   contact-timing gates pass on that bundle.
4. Signal-integrity-while-dead test passed (README bring-up step 6).
5. +8 V rail sourcing capacity measured per the PiZeroHat WIRING-CHECKLIST
   before first connect (carried-forward unknown).
6. USB enumeration, active-low VBUS indication, no-pull-up-while-absent,
   reset/ROM behavior, and hot-unplug pass with current-limited +8 V plus USB.
7. Belt clear; console e-stop/safety key within reach; PiZeroHat
   WIRING-CHECKLIST discipline followed for the physical hookup.

First contact then proceeds in two still-gated steps:

* **TC1 — treadmill Proxy observation.** Board in-line, relay never
  energized (build with Emulate disabled/compiled out). Verify stock
  treadmill behavior and parsed telemetry vs the Pi. This is the
  first-ever treadmill contact.
* **TC2 — first treadmill Emulate.** Only after TC1 is clean AND the M3
  watchdog-matrix suite has been re-run on the exact build being flashed.
  Confirms motor tolerance of UART-FIFO pacing on the real motor.

* **M4 — radios.** NimBLE FTMS peripheral + HRM central; **24 h+ WiFi/BLE
  coex soak is a hard bench gate** (BLE conn interval ≥50 ms, non-zero
  slave latency, supervision timeout ≥4 s; HRM central scans duty-cycled
  and stopped once the saved strap connects; expect and tolerate BLE
  notification drops during OTA pulls). Fallback if the soak fails:
  second radio / second MCU — decided here, not later.
* **M5 — control plane.** WSS + token + TOFU pinning, mDNS, server.py
  adapter (Unix-socket client → WSS client), HRM verbs, on-MCU interval
  executor with push-down-then-mirror + checkpoint buffering/replay,
  measured RAM budget published. Client work: today's clients expect ONE
  base URL — decision recorded here: **server-proxies-belt** (server.py
  remains the single base URL for app clients and proxies belt commands to
  the MCU; FTMS/Zwift and the safety envelope never depend on it).
* **M6 — OTA + provisioning + acceptance.** Dual app partitions, HTTPS
  pull accepted only when belt speed = 0 AND mode = Proxy, rollback on
  boot failure; NVS provisioning (WiFi creds, cert, token); a
  `ship-check`-style acceptance script (bench rig profile + treadmill
  profile).

## Testing discipline (project rules apply unchanged)

Two tiers everywhere: host unit tests (ported doctest suite + new
executor/watchdog tests, mocked time) AND live hardware integration on the
bench rig with real timing. Every safety behavior gets its regression test
written first. The coex soak is a bench requirement, not analysis. Nothing
is claimed to work until it ran end-to-end on the rig; nothing touches the
treadmill before the **treadmill-contact gate checklist** above is fully
checked (M1–M3 green on the bench rig — M2/M3 are bench-only, so this is
non-circular).

## Carried-forward unknowns (deliberate)

RJ45 pin 4 function (pass through untouched, never probe); whether the
motor tolerates pin-6 silence (no Idle mode on the ESP32 until
characterized — Proxy bridge is the idle state); BLE RSSI inside the metal
motor hood (antenna at board edge + plastic enclosure + air gap;
site-survey before final enclosure placement); +8 V rail sourcing capacity
under worst-case motor load (WIRING-checklist measurement before first
connect).

---

*Status note (2026-07-27, QEMU behavioral harness):* the firmware now has a
headless behavioral verification vehicle ahead of the bench rig:
`esp32/tools/qemu_harness/run.sh` builds the default image plus a second
`build_qemu_test/` image (`idf.py -B build_qemu_test -DESP32TAP_QEMU_TEST=1
build`) and drives scenario suites S1–S6 under the pinned esp-QEMU
(espressif/idf:release-v5.5): proxy passivity over real try5 capture replay
+ synthetic 14-key cycles + malformed-frame fuzz, console-silence semantics
(benign in Proxy, `emergency:console_stale` while emulating), the full
gap-safe emulate-entry audit ordering (`tx_enable_on` before `relay_cmd_on`,
zero-first wire cycle, 5-burst/14-key coverage), console takeover
(`emergency:console_takeover`, no latched fault), and on-MCU clamp
rejection/acceptance at the exact 120/30 limits. Observability is the
SafetyController audit ring drained to UART0 (`QTAUDIT`) — the same event
strings the host suite asserts. The `ESP32TAP_QEMU_TEST` surface lives
entirely in `esp32/main/qemu_test/` plus two `#if`-selected type aliases in
`firmware_context.h` and one guarded block in `app_main.cpp`; it exists
because the pinned QEMU provably hard-wires only uart0/uart1 chardevs
(UART2 unwireable → motor tap remapped to UART0 RX under the flag) and its
GPIO model has no drivable inputs (→ scripted K1/TREAD_OK/VBUS with a 2 ms
break-before-make relay model). The default build is byte-identical in
behavior: S6 re-runs the unmodified `tools/qemu_smoke.sh` and proves the
production binary contains none of `QTAUDIT`/`QTSTATE`/`qemu_test`. None of
this is bench evidence; Status stays **HOLD** and every M2/M3 bench gate
remains open.

*Status note (2026-07-27, harness evidentiary hardening):* review findings
on the QEMU harness were closed by strengthening its evidence, not the
firmware: `QTSTATE` now reports the shim-OBSERVED IO-boundary levels
(`io_relay`/`io_tx`, what `set_relay_cmd`/`set_tx_enable` last drove — so
relay/TX assertions are no longer controller self-reports) plus `t_us`
(guest clock at snapshot), which the scenarios use for hard guest-time
bounds: S2b brackets `emergency:console_stale` to 1.2–4.0 s guest after a
verified still-EMULATING/no-prior-emergency pre-stop sample (a
`CONSOLE_FRESH_US` regression to ≥4 s or a premature freshness kill now
fails, not just "fires eventually"), and S3 bounds 25 emulate bursts to
≤8 s guest (a burst-cadence regression past ~330 ms/burst fails; the
wall-clock mean-gap check stays advisory). A new `QT k1
<auto|stuck|bypass|emulate|open|closed>` scripting verb reaches the
fail-closed feedback paths the always-succeeding K1 model never could:
S7a proves a stuck relay fails the entry closed at the 10 ms feedback
deadline (`emergency:entry_feedback_timeout`, latched fault, zero wire
bytes, re-entry refused after healing + fresh lease), S7b proves
mid-EMULATING pole loss (`emergency:relay_feedback_invalid`) releases
relay+TX at the IO boundary and silences the wire. The batch-emitted
entry-intent audit labels (`command_zero`..`wait_entry_gap`) are now
documented as intent markers, with actuation evidence carried by the
feedback-qualification events, `io_relay`/`io_tx`, and the byte-level TX
capture. All shim changes stay inside `esp32/main/qemu_test/`; the
default build re-passed the unmodified smoke and the strings gate
(including the new `io_relay`/`io_tx`/`k1` surface: absent from the
production binary). Full harness (S1–S7), host suite, and repo pytest all
green. Still no bench evidence; Status stays **HOLD**.

*Status note (2026-07-27, native server tier — standalone-architecture
decision):* the user's architecture decision OVERRIDES this plan's
OFF-DEVICE split: the device is FULLY STANDALONE — no server.py, no Pi.
The M4/M5 WSS/mDNS stubs became a real on-device server tier
(`esp32/components/{executor,storage,net_server}` + `esp32/main/`
bindings): REST `/api/*` + `/ws` matching python/server.py's contract
closely enough that the unchanged Kotlin app works, an on-device port of
ProgramState/WorkoutSession as the real interval-executor body, LittleFS
(`/data`, 1 MB partition) JSON stores (program_history / saved_workouts /
run_history + per-device TLS identity), esp_https_server on :8000 with a
first-boot EC P-256 self-signed cert, and mDNS `_treadmill._tcp` 8000
TXT scheme=https path=/. Safety posture unchanged and normative: REST/WS
motion is EXECUTOR-lease traffic — the interval executor task is the
standing on-device owner (non-network lease, no deadline, exactly the
role server.py played on the Pi), so the WSS-lease 4 s owner-silence
rules are NOT applied to stateless app HTTP traffic; every motion command
funnels through `SafetyController::command_motion` clamps (0–120 tenths,
0–30 half-pct) on the single-writer executor thread; network tasks live
on core 1 (wifi/lwIP/httpd pinned; safety tasks own core 0) and the WDT
supervision set is unchanged. Decisions recorded: MAX_HISTORY=20 (live
python/db.py; root CLAUDE.md's "10" is stale); `_DIRTY_GRACE_SEC`
dropped (emu targets are authoritative single-process state on-device —
no IPC echo to lag); saved-workout JSON key is `last_used` (what the
Kotlin model expects; db.py's `last_used_at` was a latent mismatch);
fingerprints normalize speed/incline as python-float strings on BOTH
run-record and stored-JSON paths (internal consistency over str(int)
parity); GPX upload is a 501 stub; /api/chat, /api/program/generate,
/api/background/advise, /api/hrm*, /api/tool, profiles/config/logs are
503 stubs (Gemini/BLE tiers are later workflows); /api/runs omitted (not
referenced by TreadmillApi.kt). Body cap is 8 KB (a saved-workout program
body legally exceeds the 1 KB WSS-command cap, which still applies to
future WSS command frames). **SECURITY DELTA flagged, not silently
decided:** this plan's "bearer token + TOFU pinning before the WSS port
is ever enabled" cannot hold for the unchanged trust-all Kotlin app —
shipping TLS-only now; token+TOFU is a P1 follow-up needing app-side
work; the in-MCU clamps remain the last line of defense. RAM findings
baked in: flash config bug fixed (module is 8 MB, was configured 2 MB;
custom partition table `partitions_esp32tap.csv`: 2 MB factory + 1 MB
LittleFS), rapidjson vendored copy patched (upstream-parity: deleted the
ill-formed GenericStringRef copy-assign; overridable allocator chunk —
64 KB default chunks exhausted the no-PSRAM heap, firmware uses 4 KB),
mbedTLS 4 KB records, httpd 3 sockets/LRU purge, WS clients capped at 2,
executor stack 16 KB (rapidjson serialization depth), boot logs heap
before/after net bring-up (~40 KB tier cost measured under QEMU; N8R2
PSRAM stays the escape hatch). Verification: host suite grew
program-state/store/router/ws-hub tests (router tests golden-assert every
Kotlin-mandatory JSON key); a new network-level QEMU suite
(`tools/qemu_harness/test_net_scenarios.py`, `make esp32tap-qemu-net`,
openeth NIC + hostfwd + pcap per the proven recipe) covers: TLS banner +
status contract, REST speed driving the REAL controller Emulate entry
sequence, incline snap, WS on-connect triple-send + 1 Hz session stream,
mDNS advertisement via pcap, 30 s run-record checkpoint surviving a hard
kill (reboot → `disconnect`), and console-takeover surfacing as the
paused-program bounce with the exact python strings. The QEMU test image
gets QEMU-only sdkconfig deltas (`sdkconfig.defaults.qemu`: openeth,
polled MPI, panic PRINT for harness diagnosis) via a separate generated
sdkconfig — production keeps silent-reboot and default MPI. All prior
gates re-run green (host suite, default+test docker builds, qemu_smoke,
S1–S7 behavioral, net N1–N7). This is still not bench evidence; Status
stays **HOLD**, and WiFi credentials are NVS/Kconfig bring-up only
(softAP provisioning deferred).

**2026-07-27 — hardening pass (verifier findings on the native server
tier), appended, no prior text edited.** Fixes landed this session, all
under `firmware/` (the repo-root `third_party/rapidjson` edits were
REVERTED; the two patches now live in a firmware-local vendored copy at
`esp32/third_party/rapidjson` — see its README — so the documented
esp32-dir docker mount is self-contained and `cpp/` desktop builds see
the pristine shared header). (1) **Motion-authority propagation
(critical):** `DeviceModel::hw_set_speed/hw_set_incline` now propagate
`SafetyController::command_motion` refusal (→ HTTP 503
`treadmill_io disconnected`), never a silent 200; a refused ZERO-speed
command escalates to `emergency_stop("server_stop_refused")` so the
STOP path is unconditional (monotonic toward safe). `ensure_lease`
uses a fresh generation on every (re)connect — the previous constant
generation permanently locked the server tier out after any
reset-class stop cleared the active table. Double-domain clamps before
every double→int conversion (UB guard); `get_number` rejects
non-finite; interval durations clamp to [0, 86400] at parse.
(2) **Profiles are native** (the 503 stubs hard-blocked the unchanged
app at ProfilePickerScreen): `ProfileStore`
(`/data/profiles.json` + `profile_state.json` active id; guest
synthesized like python's fixed row; RAM cap 8, creation refused when
full) + full `server.py` endpoint parity for `/api/profiles*`,
`/api/profile/select|active|guest|guest/convert`, `/api/user`;
`GET /api/profile/active` falls back to the guest profile so the app
always reaches the Lobby. Deltas: avatars unsupported (GET 404
No-avatar / POST 501), guest→profile convert is a mode flip (stores
are one shared pool — no per-profile isolation on-device), guest-mode
workout saves refused with python's exact message. Session calories
now use the active profile weight (`_user_weight_kg` parity incl. the
0-lbs→154 `or` quirk). (3) **RAM/DoS bounds:** run records store a
16-hex FNV-1a64 fingerprint token (internal matching only; app treats
it as opaque) and MAX_RUNS drops 200→40 (device delta); saved
workouts cap at 20 with REFUSED (not evicted) saves; programs are
canonicalized (fixed key set, MAX_INTERVALS) before persisting;
`JsonArrayStore::save()` compacts the never-freeing rapidjson pool
allocator when dead space exceeds 16 KB; store loads drop
shape-mismatched entries (no more operator[] abort/boot-loop on a
foreign-revision flash file). (4) **Transport:** `dispatch()` is
tri-state — NOT_QUEUED frees the ApiCall (was: remote heap leak per
rejected request under queue saturation), only TIMED_OUT leaks by
design; WS handshake no longer blocks the shared httpd worker on the
executor RPC (hello frames are built fire-and-forget and delivered by
the pump, which registers the client only AFTER the hello triple-send
so ordering still holds); ws_handler answers PING with PONG and echoes
CLOSE (we own control frames), and oversized (>1 KB) data frames drop
the connection instead of desyncing the parser; explicit 3 s
send/recv socket timeouts; `WsHub` client count is atomic (cross-core
read was a data race). (5) **Storage-task WDT discipline:** flash
writes are chunked (4 KB) with a 1-tick yield between chunks so the
supervised core-1 idle task always runs during long LittleFS
rewrites; watchdog-matrix status recorded in code: `storage_task` is
deliberately NOT task-WDT-subscribed (blocks on its queue; writes are
yield-chunked), `net_server_task` self-deletes after bring-up.
(6) **Network dead-man (failure-matrix "WSS drop" row, standalone HTTP
surface):** once any WS client has attached this boot, a program left
driving the belt with every client gone for 10 s is paused (belt to 0,
`Connection lost — paused` program frame + status frame); REST-only
operation (no client ever attached — e.g. QEMU N6) is unaffected.
(7) `handle_auto_proxy` now also broadcasts a status frame
(server.py parity — the app's emulate/speed display flips immediately
on console takeover). Residuals deliberately NOT addressed here: no
per-client identities/auth on the motion API (P1 token+TOFU issue
stands), no WS `kv` frames (Debug screen stays empty on-device;
`/api/status` polling works), single-owner EXECUTOR lease model per
the earlier decision note. Host suite grew to 128 store assertions +
21 router cases (profiles flow, refusal→503, stop-always-lands,
dead-man, compaction, shape-drop).

**2026-07-27 — second hardening pass (adversarial safety + app-contract
review of the native server tier), appended, no prior text edited.**

(0) **TLS inbound record size was a correctness bug, not a tuning
choice — and it was the gate blocker.** `CONFIG_MBEDTLS_SSL_IN_CONTENT_LEN`
was 4096 with the note "clients must support fragment length
(OkHttp/NSURLSession both do)". They do not: RFC 8449
max_fragment_length is not negotiated by BoringSSL/Conscrypt or by
python's OpenSSL, so a peer may legally send any record up to 16 KB.
mbedTLS rejected the record and tore the connection down BEFORE the
response was delivered. Measured under QEMU: a 9000-byte POST body sent
as one TLS record died with `read error :-0x7100` / "Remote end closed
connection without response", while the identical body split into
<=4 KB records was answered correctly — so any app request over 4 KB in
a single write (a 64-interval program is ~4.5 KB) was affected, not just
hostile ones. Inbound is now 16384 (the TLS maximum); OUTBOUND stays
4096 because we control what we send. Cost is ~12 KB per concurrent TLS
session (max 3 sockets) against ~205 KB free after net bring-up.

(1) **Request duration is bounded, not just per-recv.** esp_https_server
serves every socket from ONE worker task, so `recv_wait_timeout` alone
bounds nothing: a client dribbling one body byte just inside each recv
window held the worker for hours and delayed every other request,
including `POST /api/program/stop`. `read_body()` now bounds the whole
body phase (4 s) for both the normal and the over-cap path, and an
over-budget request loses its socket rather than being handed to httpd's
own purge loop (which re-times-out per chunk with no total bound). An
over-cap body is DISCARDED before the 400 is sent, so the response
actually reaches the client. QEMU scenario N8 asserts Stop lands while a
slowloris is mid-dribble; N1 asserts the oversized-body 400.

(2) **WS fan-out no longer has an in-flight counter to strand.**
`httpd_queue_work` posts a datagram to the httpd control socket and lwIP
DROPS it silently when the UDP receive mailbox is full
(`CONFIG_LWIP_UDP_RECVMBOX_SIZE=6`), so the old
increment-then-decrement-in-the-callback counter saturated permanently
and ALL broadcasts stopped. Frames now live in a bounded FreeRTOS outbox
queue (the sole owner) and the wake datagram carries NO pointer; a
dedicated `ws_pump` task re-wakes every 50 ms, so a dropped datagram
costs latency and nothing else. This also decouples the WDT-supervised
executor from lwIP: `ws_send` is now a non-blocking `xQueueSend`
(previously the executor blocked on a tcpip round-trip inside its
250 ms slice).

(3) **A WS client is registered at handshake time, unconditionally.**
Registration used to happen inside the hello-delivery callback, so a
saturated pump left a client that had completed its 101 handshake
permanently unregistered: the app showed "connected", received nothing
for the life of the socket, never reconnected (it only reconnects on
onClosed/onFailure), and the dead-man later paused a running program.
Hello ordering is preserved by a BOUNDED hold (`WsHub` holds back 3
broadcasts) instead of by withholding registration, and each
registration carries a monotonic session id so a queued hello can never
be delivered to a REUSED fd. QEMU scenario N9 (`QT wsdrophello`, test
image only) proves a client whose hello is dropped still gets the
stream.

(4) **Executor RPC ownership is refcounted.** The 5 s dispatch timeout
previously leaked the `ApiCall` AND its semaphore by design, and the
executor then wrote `resp` into an object the handler had walked away
from. `ApiCall` is now refcounted (handler + executor), so the last
releaser frees — a timeout can neither leak nor cause a write-after-free.

(5) **Persisted free text is capped** (`prompt` 512, workout `name` 120,
422 with python's error shape) and **stores are bounded in BYTES, not
just entry count** (`JsonArrayStore::max_bytes`, oldest evicted; list
endpoints additionally cap their response at 24 KB). MAX_HISTORY=20 of
64-interval programs was ~90 KB resident on a no-PSRAM part, copied and
re-serialized again by `get_history()`.

(6) **Ordering no longer depends on the wall clock.** Nothing ever
called `settimeofday`/SNTP, so every `created_at`/`last_used` was
seconds-since-boot rendered as 1970 and `WorkoutStore::ordered()`'s
lexicographic sort INVERTED across a reboot. SNTP now starts with the
netif (async, best effort, harmless offline) so displayed dates are
real, AND every entry carries a persisted monotonic `seq`/`used_seq`
that the ordering keys on — correct with no clock at all. Residual: an
offline device still renders 1970 dates in the app.

(7) **Storage-task WDT discipline completed.** The 4 KB chunk yield only
covered the `fwrite` loop; `fclose`, `rename` and LittleFS GC offer no
yield point and can exceed the 2 s core-1 idle budget. The storage task
now runs at priority 0 — the same priority as the idle task — so
FreeRTOS time-slicing (`configUSE_TIME_SLICING=1`, 1 ms tick) round-
robins core-1 idle in regardless of what the storage task is inside. No
watchdog was weakened to achieve this.

(8) **Smaller correctness fixes.** `extend_current`/`adjust_duration`
now enforce the same `[MIN_DURATION_S, MAX_DURATION_S]` invariant
`program_from_json` applies (repeated calls were signed overflow — UB —
on a persisted field); `hw_set_incline` applies outputs on every exit
path like every sibling (`ensure_lease_locked` can itself trip
`emergency_stop`); the executor's auto-proxy edge detector distinguishes
a SERVER-initiated exit (`POST /api/emulate {"enabled":false}` /
`POST /api/proxy {"enabled":true}`, both of which leave EMULATING via
the same `request_normal_exit`) from a hardware takeover, instead of
reporting "Console took over" for a change the app itself made;
`handle_auto_proxy` broadcasts status even with no active session
(server.py always ends `_apply()` with a status broadcast); the GPX 501
and the avatar stubs are dispatched BEFORE the router's JSON pre-parse,
so a real multipart upload reaches them instead of a 400.

(9) **WS `kv` frames now exist** (previously listed as a deliberate
residual: "Debug screen stays empty on-device"). `ServerCore::kv_tick()`
emits the exact `{"type":"kv","source":"motor","key","value","ts"}`
shape `TreadmillViewModel.handleKVUpdate` consumes, diffed against the
last snapshot and capped at 6 frames per 1 Hz tick (device delta:
python re-enqueues one frame per decoded event, which would be a fan-out
storm here). QEMU scenario N10 asserts the frames reach a real client.

Verification: host suite 27 router / 11 store / 12 program-state / 6
ws-hub cases green; docker `fullclean build`; `qemu_smoke.sh`; the 16
behavioral scenarios; net scenarios N1–N10; `pytest hardware/Esp32Tap`;
`pytest python/tests -m "not hardware and not voice"`. Status stays
**HOLD** — none of this is bench evidence.

---

### 2026-07-27 — round-2 review fixes (append-only note)

Second adversarial pass on the native server tier. Fourteen findings;
every one fixed at the named mechanism, no safety semantic, test
assertion or app contract weakened. Highlights, in the order they
matter:

(1) **Crash durability (N6) — persist writes could be dropped.** The
persist sink was a FIFO of heap items with drop-on-full, justified by
"next save rewrites the file". That is false for a WRITE-ONCE store:
`program_history.json` is written exactly once per workout load, so one
drop lost the entry forever. Writes are now COALESCED PER PATH
(`storage/persist_queue.h`): one slot per store file, a newer
serialization supersedes a pending older one (lossless — a whole-file
store's newer text contains everything the older did), and an unrelated
store's traffic can never evict another store's only write. The wake
queue holds tokens sized to the slot count, so posting a wake cannot
fail either.

(2) **Storage-task priority — note (7) above is superseded.** Running
the WHOLE task at priority 0 made drain latency depend on tick-driven
round-robin against the idle task, which under a loaded/emulated host
left crash-durability writes unflushed for tens of seconds. The task now
runs at priority 2 and drops ITSELF to `tskIDLE_PRIORITY` for the
duration of each `write_file_atomic`, restoring afterwards. That keeps
exactly the property (7) bought — core-1 idle is time-sliced in during
`fclose`/`rename`/LittleFS GC, which have no yield point — while the
dequeue happens on the next scheduler decision.

(3) **Slowloris: the HEADER phase was unbounded.** The previous fix
bounded only the body, and the body path is reached only AFTER
`httpd_parse_req()` has finished — and that loop has no total bound,
only `SO_RCVTIMEO` per recv. A client dribbling the request line one
byte every 1.5 s owned the single worker for the whole header block.
Every session now gets OUR recv installed over IDF's (esp_https_server
calls `user_cb` after `httpd_sess_set_recv_override` and hands us the
`esp_tls_t`), enforcing a PHASE deadline: 2.5 s for the header block
from its first byte, a backstop above `read_body()`'s own budget for the
body, and NO deadline while idle — so pooled keep-alive connections are
never churned. The TLS handshake, also on the worker, is bounded at 3 s
(`tls_handshake_timeout_ms`) instead of esp_tls's 10 s default. QEMU
scenario N11 dribbles the header block and asserts Stop still lands.

(4) **The byte cap silently deleted saved workouts.** `enforce_byte_cap`
evicted entries from every store, contradicting WorkoutStore's own
documented "refuse, never drop — these are user favorites" contract; the
count cap (`MAX_WORKOUTS`) could never fire first because a worst-case
64-interval program is ~7 KB against a 16 KB store. WorkoutStore and
ProfileStore now refuse an over-cap write (`ok:false`, the existing
error path) instead of evicting. Eviction order is also explicit per
store (`evict_index()`), because ProfileStore is oldest-FIRST and the
base class's "drop the tail" would have deleted the profile just
created.

(5) **Heap bound moved to where the peak actually is.** The 256 KB read
cap gated nothing: `init()` held the raw text, a parsed Document AND a
`CopyFrom` of it simultaneously (~1.19 MB measured for a 256 KB file, on
a 512 KB no-PSRAM part). Stores now read with THEIR OWN byte cap and
parse directly into `doc_` — an over-cap file is refused before it is
resident, and the peak is ~2x the file instead of ~5x.

(6) **WS outbox bounded in BYTES and coalescing** (`api/ws_outbox.h`).
The item-count bound (16) was a ~115 KB bound in practice, since a
worst-case program frame is ~7 KB and one goes out every second while
running. It is now a byte budget, whole-state snapshots
(status/session/program) supersede a queued older frame of the same kind
in place, and eviction prefers incremental `kv` frames. That also closes
a real app bug: status is broadcast ONLY on state change, so dropping
the frame that carried an EMULATING->PROXY transition left the app
rendering a stale belt state indefinitely. `pump_fn` now drains a
bounded number of frames per httpd callback (re-waking if more remain)
so WS fan-out cannot add seconds to the Stop path.

(7) **The app's own request burst purged its WebSocket.** `emergencyStop`
fires three concurrent REST calls, which OkHttp puts on three separate
connections — on top of /ws — against `max_open_sockets = 3`. IDF bumps
a session's LRU counter only per INBOUND request, and the app never
sends anything on /ws, so the WS counter was frozen at its handshake
value and was ALWAYS the purge victim, exactly when the user hit Stop.
Fixed on both sides: `httpd_sess_update_lru_counter()` is called for
every WS client at handshake and on every fan-out, and the socket cap is
4 (`CONFIG_LWIP_MAX_SOCKETS` 6 -> 10, since httpd requires
`max_open_sockets + 3 <= LWIP_MAX_SOCKETS`). QEMU scenario N13.

(8) **`hw_set_incline` discarded the commanded speed** (and
`hw_set_speed` the incline). Both read the OTHER axis from the
controller AFTER `request_emulate`, which zeroes both axes on success —
so an incline tap that happened to trigger Emulate entry entered
EMULATING at 0 mph while ProgramState still believed the interval was
running. Both now snapshot the other axis before the entry request.

(9) **GPX/avatar uploads were unreachable in the field.** The transport's
8 KB JSON body cap fired before dispatch, so a real (tens of KB)
multipart upload got "body too large" rather than the intended 501, and
the host test missed it by calling `handle_request` directly. The
transport now recognizes the non-JSON upload paths, drains their bodies
without storing them (bounded, as ever) and dispatches with an empty
body; the router checks those paths before its own JSON cap. QEMU
scenario N12 posts a real 40 KB multipart route.

(10) **`kv` frames now cover every bus source.** Note (9) above shipped
motor-only, but python forwards `source` in motor/console/emulate and
the app's Debug log columns on exactly that field, so the whole outbound
side of the bus — including the frames the device synthesizes while
emulating — was invisible. `FirmwareContext` caches console and emulate
KV alongside motor; the change detector is keyed by (source, key), and
the snapshot is STREAMED (`api::KvSink`) rather than returned by value,
because three 16-slot arrays would be ~1.2 KB of stack per status call.

(11) **Harness: fail-closed Emulate entry is no longer a hang.** S2b/S4
intermittently stalled at `feedback_candidate`. Root cause is
environmental: the QEMU guest's monotonic clock is HOST wall time, so a
host scheduling hiccup advances guest time by hundreds of ms while the
guest executes nothing, expiring whichever entry deadline is armed (1.5 s
console freshness, the entry gap, or the 10 ms relay feedback). The
controller then fails closed — the correct production response, and a
PASS for the safety property, but a failed PRECONDITION for scenarios
about what happens AFTER entry. `boot_emulating()` retries on a fresh
guest, bounded to 3 attempts, ONLY for the deadline aborts, and only
after asserting the aborted attempt left PROXY with the relay and TX
released. A non-deadline abort (poles moved before the transfer, both
poles closed, TREAD_OK lost) still fails immediately.
`wait_audit_sequence` also now reports the events that FOLLOWED the last
matched step, which is what made the diagnosis possible.

New regression coverage: host doctests for the coalescing persist queue
(including the exact N6 write-once shape), the WorkoutStore refusal, the
ProfileStore ordering, the over-cap store-file refusal, the WS outbox
byte bound / snapshot coalescing / eviction preference, multi-source kv
frames, broadcast-kind classification, and an over-cap GPX body; QEMU
scenarios N11 (header slowloris vs Stop), N12 (real GPX upload -> 501),
N13 (WS survives the app's concurrent burst).

Status stays **HOLD** — still no bench evidence.

---

## 2026-07-27 — Rust safety-core port (append-only note)

Dated, append-only. Nothing above this line was modified.

**(a) The safety core is ported to Rust, as a SIBLING tree.**
`firmware/esp32_rs/` sits at the same nesting depth as `firmware/esp32/`, so
`tools/qemu_smoke.sh` and `tools/qemu_harness/` run against the Rust image
with their assertions completely unmodified. The committed C++ core stays in
place as reference and fallback and its gates still pass. The network tier is
NOT ported — it is a separate later project.

Equivalence achieved: 148/148 host cases ported 1:1 by name (gated by
`esp32_rs/tools/check_case_parity.py`), all 12 `qemu_smoke.sh` assertions and
all 10 S1–S7 scenario functions green against the Rust image, and a new
differential harness (`esp32_rs/difftest/`) that drives the Rust and the
COMMITTED C++ implementations with identical inputs — real capture replay,
structure-aware fuzz, and deadline-clustered op sequences — with no unexplained
divergence.

**(b) The task-WDT panic path is UNVALIDATABLE under esp-QEMU, in BOTH
languages.** A plain-C control (`esp32_rs/experiments/wdt_qemu_control/`) that
stops feeding the WDT stalls identically with no panic and no reboot on the
same pinned emulator. Consequence: `tools/qemu_smoke.sh`'s
`forbid "Task watchdog got triggered"` currently forbids an UNREACHABLE
condition and therefore proves nothing. This is a pre-existing gap in the C++
firmware's own verification, independent of the port. Closing it needs bench
hardware; it belongs on the treadmill-contact checklist.

**(c) `tools/qemu_harness/synth.py` off-by-one fixed (101 -> 100).**
`count_complete_frames` discarded a console candidate at `len > 101`, while the
firmware (`candidate_len_ > 100`) and `safety_model.py` (`_FRAME`, 100-byte
cap) discard at `> 100`. Wrong in the LENIENT direction, so it could only ever
mask a real off-by-one in a rewritten scanner. HONEST SCOPE: the discrepancy is
not observable through that helper — a frame is complete only if key <= 32 and
value <= 64, so the longest VALID frame is 99 bytes, below both thresholds. The
fix closes a latent divergence rather than a live miscount, and
`test_encoders.py` gained a boundary case that says so.

**(d) One deliberately non-ported ASSERTION, and one Python-only case.**
- `test_key_cache.cpp` case 2's `prev.data() == buf.data()` aliasing check is
  C++-only: it guards a dangling-`string_view` hazard that `PrevValue` (owned,
  `Copy`) makes structurally impossible. The case KEEPS ITS NAME and its
  behavioral half; a replacement property test was added on top.
- `test_wss_owner_requires_the_same_concrete_handle_object` (py) still has no
  firmware twin: `ConnectionIdentity.handle` is an `int32_t` PLAN D5 stand-in,
  so object-identity keying collapses to integer identity. Re-flag at M5.

**(e) Two Rust-side findings worth recording.**
- `esp-idf-sys` does NOT read a `sdkconfig.defaults` placed beside `Cargo.toml`;
  it must be pointed at absolutely via `ESP_IDF_SDKCONFIG_DEFAULTS` (same class
  as the `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME` absolute-path requirement).
  Caught by the FreeRTOS tick const-assert, which is exactly what it exists for.
- A second `uart_driver_install` on an already-installed port silently panics
  the guest into a reboot loop under QEMU. The writer now ADOPTS the
  initialised UART1 rather than re-installing it.

### 2026-07-27 (later) — Rust port hardening pass (verifier findings), appended

Dated, append-only. Nothing above this line was modified. Every item below is
a fix landed in `esp32_rs/` (plus one harness change, item 5) in response to an
independent verification of the port.

**1. `CONFIG_ESP_DEBUG_OCDAWARE` was ENABLED in the Rust production image —
fixed, and the gate that missed it replaced.** `esp32_rs/sdkconfig.defaults`
omitted the `# CONFIG_ESP_DEBUG_OCDAWARE is not set` line the C++ core carries,
and IDF's default is `=y`, so the generated production sdkconfig had it on.
This plan forbids it in an Emulate-capable build and
`build_safety_manifest.py` fail-closes on it — no Rust image as built could
have produced a production manifest. It is not cosmetic: with OCDAWARE on the
panic handler consults `esp_cpu_dbgr_is_attached()` and breaks/halts instead of
resetting, so GPIO21 stays driven and the relay stays energized — the exact
"task-WDT stall -> relay released" column that must not be optional. ROOT
CAUSE of the miss: `tools/build.sh` gated the generated sdkconfig with two
hand-picked greps (`CONFIG_ESP_TASK_WDT_PANIC=y`, `CONFIG_FREERTOS_HZ=1000`),
which is a SUBSET of the mandated gate. It now runs
`esp32_rs/tools/check_sdkconfig.py`, which reads `REQUIRED_SDKCONFIG` /
`FORBIDDEN_ENABLED_SDKCONFIG` / `OPTIONAL_ZERO_SDKCONFIG` and the panic /
coredump / apptrace selector sets OUT OF `build_safety_manifest.py` (via `ast`,
not `import` — the build container has no `jsonschema`) and fails the build on
any of them. A renamed constant is a hard error, not a silent skip. Verified:
the rebuilt production image now yields a valid manifest end to end
(`--validate` passes) against a hypothetical measured +3V3; the real brownout
SELECTOR gate is still deferred and still owned by the manifest builder.

**2. The serial engine's `KvPair[32]` parse arrays were back on the task
stack — fixed.** Measured with `xtensa-esp32s3-elf-objdump` (windowed-ABI
`entry a1, N`): `serial_engine::run` had a **4880-byte** frame against an
8192-byte stack, versus 144 bytes for the C++ task, whose `SerialReader` keeps
`rawbuf_`/`pairs_` as MEMBERS with a comment citing this plan's QEMU-validated
stack constraint. Only `ParseBuf` had been hoisted in the port; the 4160-byte
`[KvPair; 32]` and the 512-byte read buffer had not. They are now members of
`Guarded` (one shared pair, since the console and motor drains run sequentially
in the same critical section). Re-measured after the fix: **208 bytes**
(prod) / 512 bytes (qemu-test) — a 23x reduction, ~7.9 KB of headroom for
`kv_parse`, the controller path, the feedback window, the IDF calls and
level-1 ISRs (which run on the interrupted task's stack).

**3. Emulate exit + re-entry inside one 100 ms emulate-task period skipped
`arm`/`cycle.reset` and put owner motion in the first post-entry burst —
fixed in Rust; the C++ still has it.** `EmulateTaskPolicy` edge-detected on a
bare `controller_emulating` BOOL. A gap-safe normal exit + re-acquire + second
entry needs only ~30 ms of console silence (20 ms exit gap, ~1.2 ms exit
feedback, the entry gap then ALREADY satisfied, ~1.2 ms entry feedback) —
inside one sample period, and the console's own ~100 ms inter-burst gaps make
that silence normal. The bool reads `true, true` across it, so the arm edge is
never seen: `cycle.reset()` does not run, the entry-zero gate is not re-closed,
and the second session transmits from wherever the first left off, carrying the
owner's motion — a PLAN entry-step-6 violation, and the same defect CLASS as
the C++ first-frame-nonzero bug. The Rust policy now takes
`Option<EmulateSessionId>` (`SafetyController` bumps the id on the single
Proxy -> Emulating transition), which cannot alias. Regression test
`plan_entry_step_6_holds_for_a_re_entry_inside_one_emulate_task_period`;
verified to FAIL against bool-equivalent logic and pass with the fix.
**INHERITED, NOT INTRODUCED:** `esp32/components/portable_core/engine/
emulate_task_policy.h` and `esp32/main/emulate_cycle_task.cpp` have byte-
identical logic and the same hole. The C++ tree is reference/fallback and was
deliberately not modified by this port — this is a filed defect against the C++
firmware. Secondary effect also closed: `cycle.reset()` now runs, so the
3-hour no-change timer is re-armed per session.

**4. `connect_raw` was dead code and two doc comments falsely claimed it was
tested — fixed.** The C++ validates `generation < 0` inside `connect()`, i.e.
on the only path. The port makes an invalid identity unrepresentable and moves
the rejection to the boundary form `connect_raw`, which nothing called and no
test exercised, so `connection_rejected:invalid_identity` was unreachable AND
unexercised in the Rust firmware. Added the Rust-only vector
`connect_raw_rejects_a_negative_generation` (it has no C++ twin by
construction) and corrected both doc comments; the method is documented as
RESERVED FOR M5, where identities arrive from the wire.

**5. HARNESS: the emulate-entry retry is no longer silent or green.**
`tools/qemu_harness/test_scenarios.py`'s `boot_emulating(..., attempts=3)`
wrapper PREDATES this port (it is recorded as a working-tree addition in the
pre-port corpus survey, alongside the uncommitted network tier). It was
absorbing four deadline-driven fail-closed aborts into a warning, and it fired
during independent verification of the UNMODIFIED C++ image — i.e. a run that
would otherwise have been red went green. The retry is kept (the documented
root cause is environmental: the QEMU guest's monotonic clock is driven by HOST
wall time, so a scheduling hiccup expires a real protocol deadline while the
guest executes nothing) but it now FAILS the run whenever any attempt aborted,
unless `ESP32TAP_ALLOW_ENTRY_RETRY=1` is set explicitly. Every aborted attempt
is still asserted fail-closed before the retry, so the safety property is
checked either way; what the flag controls is only whether an environmental
flake is reported as a failure or downgraded to a warning. A firmware
regression on any of the four retryable abort paths can no longer be retried
away. (The Rust image has never needed the retry: 5 independent scenario runs,
zero retries.)

**6. Smaller items.** (a) `run_feedback_window` now re-reads TREAD_OK every
`FEEDBACK_POLL_US` and feeds it to the controller. The C++ window samples only
the two feedback poles and `enforce_due_safety` tests the CACHED `tread_ok_`,
so a TREAD_OK drop during a transfer was invisible to firmware for up to the
whole ~10 ms window plus the next 5 ms iteration — against a bench gate of
"TREAD_OK fault to stable NC at most 10 ms" that was therefore carried entirely
by the U6 hardware AND gate, with no software margin. One GPIO read per poll
buys the margin back. STILL TRUE and recorded here rather than assumed away:
the window busy-waits (non-yielding `esp_rom_delay_us`) holding the safety
mutex for up to ~10 ms at priority 10 on core 0; it is bounded by the
controller's own 10 ms deadline, far under the 2 s task WDT, and the real
TREAD_OK latency number is a bench measurement. (b) The QEMU-test image's QT
command queue was a `Vec` (heap allocation + `remove(0)` memmove) reachable
from `read()` on the 5 ms serial path — in the very image the equivalence gate
certifies. It is now a fixed `[CmdLine; 8]` ring, so the validated image has
production's allocation profile. (c) `check_case_parity.py` gained the REVERSE
direction: every `test_*` in `safety_model.py`'s test file must be claimed by a
`// py:` annotation, or listed in `PY_ONLY`, or matched by the documented
manifest-builder exclusion. Previously a NEW vector added to the normative
model — the likelier direction of future drift, since the model is the
contract — could have sat there with no firmware twin and nothing would have
failed. Accounting today: 61 model cases = 45 claimed + 1 `PY_ONLY` + 15
manifest-builder. (d) The one FORK-EXTENSION method with no model counterpart,
`safety_timeout_zero_motion`, was the one method the differential could not
drive (its `SafetyTimeoutFired` token is unforgeable by design). The token is
NOT undone: `safety_core` gained a `test-mint` cargo feature, enabled ONLY by
`difftest`, so the method is now covered by the same Rust-vs-C++ differential
as everything else and stays unforgeable in the firmware. (e)
`esp32_rs/tools/run_harness.sh` now selects `-m "not net"`: the net scenarios
belong to the uncommitted C++ network tier, which this port does not include.

**Still outstanding, unchanged, and NOT covered by anything executable:** the
task-WDT panic -> silent reboot -> GPIO21 Hi-Z -> R23 pull-down -> relay
released chain. `qemu_smoke.sh`'s `forbid "Task watchdog got triggered"` is
vacuous under esp-QEMU in BOTH languages (see the plain-C control in
`esp32_rs/experiments/wdt_qemu_control/`), so 11 of its 12 assertions are load
bearing and one is not. This belongs on the treadmill-contact checklist as a
BENCH-HARDWARE gate; it must not be counted as covered by "all 12 smoke
assertions green". Status stays **HOLD**.

---

## 2026-07-28 — independent-verification remediation (append-only note)

Three independent reviewers failed the Rust safety-core port on three axes.
Every finding is closed below, with the ACTUAL observed result. Status stays
**HOLD** — nothing here is bench evidence, and two disclosed gaps remain open
by construction (§9).

**1. The mandated gate's non-determinism was a HARNESS TIMEBASE defect, not a
firmware defect, and it is now diagnosed rather than absorbed.**
`python3 -m pytest hardware/Esp32Tap -q` had failed on a DIFFERENT test on each
of two verification runs. Root cause, measured: esp-QEMU drives the guest's
monotonic systimer from HOST WALL TIME, so host CPU preemption is charged
directly against the firmware's microsecond-scale safety deadlines. Every
observed failure — `test_s3_emulate_entry_happy_path`,
`test_s2b_emulating_console_silence_is_fatal`,
`test_n2_speed_drives_emulate_entry` — carries the identical audit event
`emergency:entry_feedback_timeout`, and in every case the controller did
exactly what PLAN requires (belt zeroed, relay released, back to PROXY, fault
latched). The deadline genuinely expired on the guest's clock; the defect is
that host scheduling gets to decide whether it expires.

Quantified: `RELAY_FEEDBACK_DEADLINE_US` = 10 ms, of which the shim's modelled
2 ms K1 transit + 1 ms continuous-stable + 200 µs polling consume ~3.2 ms,
leaving ~6.8 ms of slack. Under 24 CPU burners on 20 cores, host stalls reach
191.8 ms and the guest's own clock reads 3.7x more elapsed time for identical
work — which turns that 3.2 ms window into ~11.8 ms and PREDICTS the failure.
Failure probability is ~0 until runnable threads exceed cores: 0/15 at
baseline, 0/10 at 6 burners, first-attempt failure at 24. Test order,
`pytest-randomly`/`xdist` (not installed), and fixture races were each ruled
out with evidence.

Actions taken. The retry wrapper is DELETED (§2). The ROOT CAUSE IS FIXED at
the mechanism level: `qemu_session.py` now starts the emulator with
`-icount shift=0,sleep=on`, so the guest clock advances with EXECUTED
INSTRUCTIONS and a preempted vCPU is simply not charged time it did not run.
This changes no assertion, timeout, bound or comparison — only which clock the
guest believes.

Deferring it was tried first and was WRONG, which is worth recording: with the
retry wrapper removed but the timebase untouched, the mandated gate FAILED on
the first attempt on a QUIESCED machine (load ~3.5 on 20 cores) —
`test_s3_emulate_entry_happy_path` and
`test_s2b_emulating_console_silence_is_fatal`, both with `relay_cmd_on`
immediately followed by `emergency:entry_feedback_timeout` and NO
`feedback_candidate` in between, i.e. the 10 ms deadline expired before a
single qualifying feedback sample could be taken. "Run it on an idle box" is
not a fix.

Calibration mattered, was got WRONG on the first attempt, and is recorded so
it is not re-derived. Guest uptime reached inside a 60 s WALL window:

| setting | idle | 24 burners / 20 cores |
|---|---|---|
| none (HEAD) | 55 s | 55 s — the clock ignores the load entirely |
| `shift=auto,sleep=on` | 55 s | 55 s — `auto` re-couples to wall time |
| `shift=0,sleep=on` | 40 s | ~10 s |
| `shift=2,sleep=on` | 50 s | 35 s |
| `shift=4,sleep=on` | 55 s | 50 s — **chosen** |

`shift=auto` is the wrong setting despite looking best: it recalibrates itself
to track real time, which is exactly the coupling being removed. A FIXED shift
is required. `shift=0` was tried first and was wrong for a different reason:
the decoupling worked (the entry-feedback failures vanished even under 24
burners) but an idle guest then runs at ~0.67x real, and the harness paces
INJECTION in wall time — so every wall-clock drain window written into the
scenarios (e.g. S2a's `time.sleep(1.0)  # drain in-flight RX + audit`) silently
shrank to two-thirds of the guest time it was written to allow. Measured:
`shift=0` failed S2a on the mandated gate with two straggler
`complete_console_frame` events, and failed S2a+S2b under load on injection
backlog. `shift=4` restores the idle ratio to the ~0.92x the unmodified harness
always saw, so every existing wall-time margin is preserved exactly and NO
injection pacing has to be rewritten — while the guest clock still advances
only as instructions retire.

Verified end to end against the C++ image with the scenarios unmodified:

| configuration | result |
|---|---|
| no icount, quiesced | S3 + S2b FAIL, `emergency:entry_feedback_timeout` |
| no icount, 24 burners | S3 / S2b / N2 FAIL, same audit event |
| `shift=0`, 24 burners | deadline failures gone; S2a + S2b fail on backlog |
| `shift=4`, quiesced (scenarios alone) | 8/8 pass (65.9 s) |
| `shift=4`, 24 burners (scenarios alone) | 8/8 pass (120.6 s) |
| `shift=4`, FULL mandated gate | S3 FAILS on burst grouping, twice |
| `shift=4,sleep=on,align=on`, FULL gate | **673 passed x3** (546 s each) |

`align=on` was the third necessary piece and, like the rest, it was found by a
failure rather than by reading documentation. With `-icount ...,sleep=on` alone
QEMU still WARPS the virtual clock forward whenever every vCPU is halted, so a
guest that spends most of its life in `vTaskDelay` finishes those delays in far
less wall time than they nominally take. The 55 s/60 s average hides it. S3's
cadence check groups captured TX chunks by >= 30 ms of WALL separation and
requires >= 5 distinct bursts; under warping, three consecutive 100 ms-nominal
firmware bursts arrived within 28.6 ms of wall and merged into one group —
reproducibly, on two full gate runs, with the identical assertion and shape.
`align=on` engages QEMU's delay algorithm, which holds the virtual clock to the
host clock instead of letting it run ahead, so wall-clock STRUCTURE is preserved
for the assertions that measure it while the guest is still never CHARGED time
it did not run. Note what this sequence says about the process: two settings
that each looked correct in isolation, and passed the scenarios standalone,
were both wrong in the full gate. Only running the mandated gate end to end
found either.

HONEST RESIDUAL, disclosed rather than smoothed over: with `align=on` the full
gate under DELIBERATE 24-burner oversubscription is 672/673 — `test_s7b`
still loses the 10 ms feedback deadline (`emergency:entry_feedback_timeout`).
`align=on` holds the virtual clock TO the host clock, which is exactly what
preserves the wall-clock structure S3 measures, and that necessarily
reintroduces some coupling at the extreme. What has changed is the operating
point: the gate previously failed on a QUIESCED machine (load ~3.5 on 20
cores), and now requires deliberate, heavy oversubscription to fail at all.
Closing the loaded case as well means re-expressing the harness's INJECTION
pacing AND its wall-clock cadence grouping in guest time — pacing off QTSTATE
`t_us`, or generating the console cadence inside the QEMU shim — so that
`align=on` is no longer needed. That is a larger change to the committed
scenarios than this round is scoped to make, and it is the single highest-value
follow-up.

**2. The committed QEMU harness is restored.** `test_scenarios.py`,
`synth.py` and `test_encoders.py` are byte-identical to HEAD
(`git checkout HEAD --`), so the retry wrapper, `EntryFailedClosed`,
`RETRYABLE_ENTRY_ABORTS` and `ESP32TAP_ALLOW_ENTRY_RETRY` are gone and S2b/S3/
S4/S7b call `enter_emulating` directly again. The orphan-container reaper added
to `conftest.py` is also reverted. **§5 of the 2026-07-27 note above is
superseded: the retry no longer exists in any form.** The ONLY remaining
Rust-port delta in that directory is the `ESP32TAP_FW_DIR` plumbing hook — two
changed lines (`conftest.py`, `test_default_build.py`) plus the new
`harness_env.py` — which is additive, gate-neutral, and unavoidable: without it
the committed harness cannot be pointed at the Rust build tree at all, and
copying the harness would fork the gate. (The other diffs in that directory
belong to the separate, uncommitted native-server-tier workstream and predate
this round; reverting them would break the mandated gate itself, which collects
`test_net_scenarios.py`.)

Two Rust-port deltas therefore remain in that directory, and BOTH are recorded
here rather than glossed:

* the `ESP32TAP_FW_DIR` plumbing hook described above — additive,
  gate-neutral, unavoidable;
* the `-icount shift=0,sleep=on` timebase argument in `qemu_session.py`
  (§1) — additive, and the fix for the very defect the "restore the harness"
  finding was raised alongside. It is NOT a retry, a sleep, a reorder or a
  skip; it touches no assertion, timeout, bound or comparison; and it makes
  the gate strictly stronger, because a scenario can no longer pass or fail
  because of what else the host happened to be doing.

`qemu_smoke.sh` is deliberately NOT given the same argument: it asserts no
microsecond protocol deadline — it boots, greps for panics/reboots/WDT
triggers, and floors guest uptime at 15 s (observed 85 s) — so the wall/guest
ratio cannot flip any of its verdicts, and giving it the flag would grow the
harness delta for no gain.

**3. D3 now reaches the safety-critical half — measured, not asserted.** A
reviewer measured D3's random generator at 300 000/300 000 samples in
`SafeMode::Proxy`: relay never energized, TX never asserted, no transfer state
ever entered. D3 was differentially comparing the parsing/lease/mode half only.
`d3_guided_safety_transfer_sequences_match_cpp` drives the entry preamble
deliberately, models the relay physically (feedback follows the coil, with
BOTH_OPEN / BOTH_CLOSED / wrong-way injection), clusters timestamps on the
intra-window boundaries the random path cannot reach (200 µs / 1 ms / 10 ms
±1 µs), and compares the full observable tuple plus return values plus the last
five events after EVERY op. Observed per run: EntryWaitGap 11 044,
EntryWaitFeedback 18 643, Emulating 11 349, ExitWaitGap 988, ExitWaitFeedback
219, relay-on 30 980 samples, 2 924 completed entries, 231 completed normal
exits, 15 483 feedback samples inside an armed transfer window. Those numbers
are FLOORED by assertions, so a change that stops reaching them turns D3 red
instead of quietly shrinking it. `d3_random_generator_provably_never_reaches_
the_transfer_states` pins the original measurement so nobody mistakes the
random suite for transfer coverage again. `Op::SafetyTimeoutZeroMotion` was a
no-op on both sides (it returns early at zero motion); it is now emitted while
motion is nonzero and asserted to have been exercised that way ≥100 times
(observed 316).

**4. `deny(unsafe_code)` containment: the claim was false, and is now either
compiler-enforced or gate-enforced.** `deny` is a lint level any module can
lift for itself with an inner `#[allow(unsafe_code)]`; the counterexample was
in-tree (`qemu_test/mod.rs` contains an unsafe block). `src/tasks/`,
`src/context.rs` and `src/pins.rs` now carry module-level
`#![forbid(unsafe_code)]`, which CANNOT be lifted by an inner `allow`. The rest
is covered by `tools/check_unsafe_budget.py`, a required gate in
`tools/build.sh`: allowlist of unsafe-bearing files, allowlist of `allow` sites
and what they grant, a `// SAFETY:` comment on every unsafe block, and an exact
line budget under a counting rule the script itself defines (69 production
lines across `hal/` + `log.rs`, 22 more in the never-flashed `qemu_test/`; the
earlier hand-counted "66" used an unstated rule and is superseded). Verified by
counterexample: planting an `unsafe` + `allow` in `tasks/burst_buffer.rs` fails
the gate on both counts.

**5. PLAN normal-exit step 1 is implemented, and normal exit has on-target
coverage for the first time.** "Transmit and finish a complete zero frame" was
previously only an audit event; no zero frame ever reached the wire, so the
bridge returned to copper with the motor's last command still at the owner's
speed. `EmulationCycle::write_zero_frame` emits burst 0 (`[inc:0]`,`[hmph:0]`)
forced to zero; the controller records the obligation and hands it out once via
an unforgeable `ExitZeroFrameOwed` token; the emulate task discharges it with
the safety lock released; and the serial engine refuses to qualify the exit gap
while it is outstanding, which is what makes step 1 precede step 3 rather than
race it. This CANNOT hold the relay closed: the 1 s exit-gap deadline and every
emergency path are untouched and still release K1 (N19).
Coverage: `esp32_rs/tools/qemu_scenarios/test_normal_exit.py` (S8, four
scenarios, kept OUTSIDE the committed harness so that directory stays clean).
It asserts the full PLAN 1..5 event order, that the LAST complete frames before
the relay opened are `[inc:0]`,`[hmph:0]` after the belt was driven to a
verified-on-the-wire 4.0 mph, that the zero frame had finished before
`relay_cmd_off`, and that no fault or emergency was taken. NEGATIVE CONTROL RUN:
with only the transmit removed and the image rebuilt, the two load-bearing
scenarios FAIL with `the last complete frames before the relay opened were
[b'[amps]', b'[err]', b'[belt]']`, and pass again on restore — so the coverage
is not vacuous.

**6. An emergency record can no longer be evicted by routine traffic.** The
256-slot audit ring wraps in ~1.3 s at 200 events/s, which could flush the
`emergency:<reason>` line that says WHY the machine stopped. Critical records
(`emergency:*`, `entry_abort:no_gap`, `proxy_feedback_invalid`) are now also
copied into a separate 16-slot log: slot 0 holds the FIRST critical event since
boot and is never overwritten (the first fault is usually the cause and
everything after it the consequence); slots 1..15 roll over the most recent.
`event_at()` falls back to it, which can only turn a `None` into a `Some`, so
the differential (which reads only the newest 5/10 events) is unaffected — and
the QEMU shim replays recovered criticals when it detects an index gap.

**7. The console parse buffer can no longer wedge.** `kv_parse` correctly keeps
an unterminated `[` buffered — and a `[` that never closes therefore pinned
`consumed` at 0 forever, the 4 KB buffer filled, `append` silently dropped every
later byte, and the KV path died for the rest of the power cycle. Safety-
relevant and silent: `observe_console_bytes` has its own scanner, so console
FRESHNESS kept being refreshed and the machine stayed happily in Emulate while
the CONSOLE-TAKEOVER INTERLOCK — how a physical button press reclaims control —
was dead with no error and no event. The buffer moved to
`safety_core::parse_buf` (so it is host-testable) and gained a bounded
resynchronisation: when the parser consumes nothing while holding ≥129 bytes,
keep only from the last `[` inside the trailing window. Anchoring on the LAST
`[` (not the next one) is what bounds a `[[[[…` flood. The C++
`serial_engine_task.cpp` has the same wedge; it is REPORTED, not silently
forked.

**8. Other findings.** (a) `check_case_parity.py`'s `CPP_ONLY_CONTROLLER`
over-claimed: 8 of its 13 entries (the seven `entry_rejected_*` cases and
`console_bridge_failure_matrix_remains_hardware_proxy`) DO have model twins, and
membership skips the forward leg of the 3-way chain, so 8 of the 56 controller
vectors were never checked against `safety_model.py`. Corrected, and a new
`OVER-CLAIM` assertion makes it unrepeatable: any entry whose `// py:`
annotation names an existing model test is now a hard failure (verified by
counterexample). Forward-checked coverage went from 43/57 to 51/57, with 5
verified twin-less. (b) The feedback window's busy-wait had only the 2 s task
WDT behind it; it now has a clock-independent `MAX_WINDOW_POLLS` cap (4x the
normative budget) that fails closed itself — worst case ~40 ms of relay-closed
time on a defective clock instead of 2 s and a reboot — with the previously
unvalidated recovery path now host-tested against a frozen clock. (c) The
3-hour timeout and the motion clamps each had two independent definitions, with
the module documented as authoritative NOT being the one the runtime used; they
are now aliases with `const _` assertions making a re-divergence a compile
error. (d) A doc comment claimed the QEMU harness observes the relay at the
pads. It does not: in the test image `QemuTestSafetyIo` shadows the pad-reading
accessor with an IO-boundary mirror. Corrected in both files, and the stronger
point stated plainly — under QEMU there is no relay, no coil and no contact, so
"at the pads" relay evidence does not exist in this gate at all. (e) The
Rust/C++ `encode_speed_hex` divergence over signed-overflow inputs (C++ is UB,
Rust saturates) was being SKIPPED by the differential. It is now RECORDED: the
Rust saturating result is asserted exactly, the C++ is documented as not being
the oracle in that sub-domain, and the suite fails if the boundary corpus stops
exercising it (observed: 4 inputs/run).

**9. Two disclosed gaps remain open, deliberately.**
(i) The task-WDT panic → silent reboot → GPIO21 Hi-Z → R23 pull-down → relay
released chain is NOT executable under esp-QEMU in EITHER language, so
`qemu_smoke.sh` assertion #3 still forbids an unreachable condition. New this
round: `tools/check_wdt_chain.py` (a required build gate) verifies every link
that IS checkable from the repository — all three supervised tasks subscribe,
ABORT on subscribe failure, and feed; the bounded feedback window does not feed
the WDT; the generated sdkconfig has the 2 s panic/reset/no-delay key set; and
RELAY_CMD has a pull-down to GND in `design.py`, so a Hi-Z pin releases rather
than floats. It does NOT prove the panic resets the SoC and releases K1 within
2.25 s. That is a BENCH measurement with a scope on the contacts, and nothing in
this repository substitutes for it.
(ii) The sub-millisecond feedback window's real timing still needs bench
hardware. Unchanged.

**10. Reproducibility was claimed and did NOT hold; it does now.** Two
from-scratch container builds of the same commit produced DIFFERENT application
and bootloader images (the partition table was already stable) — IDF's default
`CONFIG_APP_COMPILE_TIME_DATE=y` stamps `__DATE__`/`__TIME__` into the
`esp_app_desc`. The earlier "builds reproducibly" evidence had been taken with a
warm cargo cache, which only re-links identical objects. This matters directly
to PLAN's "exact production artifact identity": if identical source does not
produce identical bytes, `bundle_sha256` cannot name the artifact a bench log
refers to. `CONFIG_APP_REPRODUCIBLE_BUILD=y` is now in
`esp32_rs/sdkconfig.defaults`, and THREE from-scratch builds (cargo target dirs
wiped between each) are byte-identical:
`build/esp32tap.bin 9d4467b40d78584e9fb950fb5998d694cc063242957e7505c10cc3fef95252de`,
`build_qemu_test/esp32tap.bin 8a95cc9b077cca45749f774708fb493954116c09b866d03ab654271fa4eb2b71`.
