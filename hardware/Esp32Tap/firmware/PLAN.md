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
  `CONFIG_BT_NIMBLE_PINNED_TO_CORE=1`, WiFi task pinned to core 1, BT
  controller on core 1, `CONFIG_ESP_COEX_SW_COEXIST_ENABLE=y`, WiFi PS
  `MIN_MODEM`, `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=3`,
  `CONFIG_ESP_TASK_WDT_EN=y`, `CONFIG_ESP_TASK_WDT_INIT=y`,
  `CONFIG_ESP_TASK_WDT_TIMEOUT_S=2`,
  **`CONFIG_ESP_TASK_WDT_PANIC=y`**,
  `CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y`, and
  `CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0` (task-WDT stall must
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
engine.

WSS and BLE manual ownership use one **4 s total-silence deadline**. There is
no second timer and no 10 s reconnect grace. Owner disconnect immediately
commands zero, deasserts relay and TX enables, and releases ownership.
Reconnect begins unowned at zero and must explicitly acquire a new generation.
The on-device executor owns a non-network lease; RF loss does not end or
silently transfer it, but local safety events, reset, and WDT do.

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
   Emulate continuously for at least 1 ms, completing qualification within
   10 ms;
6. only then transmit the first complete zero frame.

If no gap arrives within 1 s, entry aborts without moving K1. Wrong or missing
feedback releases K1 and latches a fault.

Normal exit is exactly:

1. transmit and finish a complete zero frame;
2. wait for a capture-qualified gap, for at most 1 s;
3. deassert RELAY_CMD and require bypass feedback continuously for at least
   1 ms, completing qualification within 10 ms;
4. deassert TX_ENABLE;
5. release ownership.

At the normal-exit gap deadline, deassert RELAY_CMD immediately; remaining in
Emulate is less safe. TREAD_OK loss, stale console, lease expiry, explicit
emergency stop, brownout, reset, and watchdog action never wait for a gap.

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
missing/empty, if its output aliases any hashed input, if the 2 s task WDT is
not enabled and initialized with panic/reset, if the panic action is not an
immediate silent reboot, if brownout detection is absent, if any halt/debug
mode is enabled, or if the configured brownout selector is not the highest
documented ESP32-S3 threshold below the supplied physical minimum +3V3
measurement. Validation rechecks selector/voltage/measurement correspondence
even when a manifest's hashes were recomputed. The selector numbers are
inverse to voltage: for example, with a measured 3.05 V minimum, level 3
(approximately 2.98 V), not level 7, is the highest supported threshold below
the measurement.

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
