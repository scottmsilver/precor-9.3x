# Esp32Tap firmware plan (ESP-IDF port of cpp/protocol + cpp/engine)

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

On this board, Proxy mode is a **normally-closed relay bridge** — console
bytes reach the motor through copper, not software forwarding. The engine
still parses the console stream (for telemetry and auto-emulate/auto-proxy
detection) but the forwarding path has zero latency and zero firmware
dependence. Emulate entry = zero speed/incline, energize relay, start
cycle. Emulate exit / watchdog / crash / power loss = relay releases →
instant stock treadmill.

**Task-WDT supervision — scope and action (normative).**
`esp_task_wdt` subscribes **every task whose stall can leave the relay
energized**, not just the serial engine: (1) the serial engine task,
(2) the emulate cycle task (it can deadlock on its TX mutex while the
serial task stays healthy), and (3) the interval executor task (1 s tick).
Any of these stalling while the relay is energized is a state PLAN lists as
uncharacterized (motor pin-6 silence), so each must independently trip the
WDT. The WDT **action must actually release the relay**: ESP-IDF's task-WDT
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
  `MIN_MODEM`, `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=3`, and
  **`CONFIG_ESP_TASK_WDT_PANIC=y`** (task-WDT stall must panic-reset so the
  relay releases — the IDF default only logs a warning; see the supervision
  section above). Residual core-0 ISRs are accepted; the 128-byte UART
  FIFOs (~133 ms of RX buffering at 9600) absorb scheduler jitter.
* **Task stack sizing (QEMU-validated constraint)**: `KvPair` is 128 bytes,
  so a single on-stack `KvPair[16]` array is 2 KB — two of them overflow
  the IDF default 3.5 KB main-task stack and hard crash-loop (observed:
  ~340 consecutive stack-overflow reboots in the esp32s3 QEMU PoC before
  the buffers were made static). Any task that owns parser buffers must
  either keep them static/heap-allocated or get an explicitly sized stack
  (`CONFIG_ESP_MAIN_TASK_STACK_SIZE` / `xTaskCreate` depth ≥ buffers +
  8 KB headroom).

## Watchdog / mode state machine (complete matrix — M3 entry gate)

Sessions have exactly one *controlling liveness source*; the mode engine
tracks `(mode, program_active, session_source)` where
`session_source ∈ {NONE, WSS, BLE, EXECUTOR}`.

Liveness definitions:
* **WSS session**: any manual speed/incline held via WSS. Heartbeat = any
  inbound command; 1 Hz client heartbeats; timeout **4 s**
  (`HEARTBEAT_TIMEOUT_SEC` parity). All-WS-clients-disconnected =
  immediate reset (Layer-1 parity).
* **BLE session** (gate fix): an FTMS Control Point connection **is a
  client of the mode engine**; the BLE **supervision timeout (set ≈4 s)**
  is its heartbeat. BLE disconnect during a BLE-initiated manual session ⇒
  same zero + revert-to-proxy as WS-client loss. This restores exact
  parity with today's ftms-daemon 1 Hz heartbeat, and works with the home
  server off.
* **EXECUTOR session**: an on-MCU program is executing. Network/BLE
  silence does NOT touch the belt; only program completion/stop, an
  explicit stop command, a console button (auto-proxy), or a safety event
  ends it.

Full matrix — cells are the required behavior AND each cell gets a
regression test (test-first rule) before M3 closes:

| Command source ↓ / failure → | RF stall >4 s | client crash / WS drop | BLE drop | MCU reboot | task-WDT stall |
|---|---|---|---|---|---|
| WSS manual session | zero + Proxy (after grace, below) | zero + Proxy (immediate on last client) | n/a | boots to Proxy, relay released | relay released → hardware Proxy |
| BLE (FTMS CP) manual session | n/a (BLE supervision governs) | n/a | zero + Proxy | boots to Proxy | relay released |
| Console (physical buttons) | no effect (hardware bridge) | no effect | no effect | bridge never opens | bridge closes |
| EXECUTOR (program running) | **program continues** | program continues; server re-mirrors on reconnect | program continues (FTMS just stops notifying) | **boots to Proxy, NO program resume** (zero-on-emulate-entry philosophy; resume requires explicit safety-review approval) | relay released → Proxy; program state discarded |
| Hybrid: WSS/BLE tweak *during* a program | the tweak is folded into the executor (interval override, same as today's `split_for_manual`); the 4 s session watchdog does **not** arm — executor liveness governs; loss of the tweaking client changes nothing | same | same | as EXECUTOR | as EXECUTOR |

Additional rules:
* 3 h no-change timeout: unchanged — zeros speed/incline, stays Emulating.
* Auto-emulate on any speed/incline command; auto-proxy on console
  hmph/inc value change (prior-value-known rule) — executor abort ordering
  on console button: mode flips to Proxy first (relay releases), then the
  executor observes `!is_emulating()` and self-terminates — watchdog reset
  never depends on cleanly stopping the emulate/executor task (Pi parity).
* **Reconnect grace (gate fix, explicit accepted behavior):** TLS
  reconnects commonly take 2–6 s, so a strict 4 s WSS timeout would revert
  manual sessions on routine RF blips far more often than today's zero
  occurrences. Manual WSS sessions therefore get a **bounded grace window
  of 10 s total silence** (speed/incline frozen during grace, then zero +
  Proxy). This is a documented safety-semantics choice for the safety
  review; BLE sessions and the executor are unaffected.

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
  Zero-on-entry, auto-proxy-on-console-change (driven by replayed
  captures), 3 h timer, clamps, relay release on task-WDT stall of **each**
  supervised task (serial engine, emulate cycle, interval executor —
  stalled one at a time), **the entire watchdog matrix above with one
  regression test per cell** — all passing on the bench rig.

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
3. Signal-integrity-while-dead test passed (README bring-up step 6).
4. +8 V rail sourcing capacity measured per the PiZeroHat WIRING-CHECKLIST
   before first connect (carried-forward unknown).
5. Belt clear; console e-stop/safety key within reach; PiZeroHat
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
