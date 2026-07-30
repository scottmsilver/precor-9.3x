# Esp32Tap QEMU behavioral harness

Injects Precor console/motor KV byte streams into the firmware's UARTs
under QEMU, captures what the firmware transmits, and asserts
protocol/safety behavior (proxy passivity, console-freshness semantics,
gap-safe emulate entry ordering, console takeover, on-MCU clamps). This is
the verification vehicle for the upcoming native-server / Gemini / BLE
workflows: they will call the exact same `SafetyController` entry points
the `QT` shim commands exercise.

## One command

```bash
tools/qemu_harness/run.sh     # builds both images (docker) + pytest -m qemu
```

Prereqs: docker with the pinned `espressif/idf:release-v5.5` image,
python3 + pytest on the host. Everything runs headless; the harness writes
only to `/tmp` and the docker-owned build dirs.

## Two firmware images

| Dir | Flags | Used by |
|-----|-------|---------|
| `build/` | none (production) | S6: unmodified `tools/qemu_smoke.sh` + strings gate |
| `build_qemu_test/` | `idf.py -B build_qemu_test -DESP32TAP_QEMU_TEST=1 build` | S1–S5, S7 behavioral scenarios |

`ESP32TAP_QEMU_TEST` is a CMake cache var consumed only by `main/`
(conditional source + `target_compile_definitions`); `components/` are
untouched. With the flag off the preprocessed sources are identical to
production, and S6 asserts the production binary contains none of
`QTAUDIT` / `QTSTATE` / `qemu_test`.

## QEMU chardev ground truth (proven by experiment)

* The pinned esp-QEMU (9.2.2) hard-wires `uart0=serial0`, `uart1=serial1`;
  **UART2 has no chardev and cannot be wired** by any `-serial` /
  `-global` / qom mechanism → the test image remaps the motor tap to
  UART0 RX.
* Chardev bytes are **LOGICAL**: `uart_set_line_inverse` is a no-op on
  this path in both directions; 0xFF passes clean. Inject/expect the KV
  text exactly as decoded in `cpp/captures/`.
* **No baud pacing**: bytes arrive in immediate chunks; the harness paces
  bursts in wall time itself (console pacer at 150 ms, replay gaps
  floored at 30 ms).
* The esp32s3 GPIO model is a stub with **zero input lines** (GPIO_IN
  always 0, qtest writes don't latch, `qdev_get_gpio_in_named` asserts) →
  K1 feedback / TREAD_OK / VBUS come from the scripted `QemuTestSafetyIo`
  (relay model: 2 ms break-before-make BOTH_OPEN transit, then the target
  pole state).
* Both chardevs use TCP `server=on,wait=on` and are invoked via
  `qemu-system-xtensa` directly (never `idf.py qemu`), so the guest boots
  only after both sockets are connected — no output is ever lost.

## Topology

```
harness                          QEMU guest (esp32s3, test image)
-------                          --------------------------------
serial0 <-> UART0   <- ESP log + QTAUDIT/QTSTATE lines
                    -> motor-sim bytes ([key:value], no 0xFF)
                    -> "\nQT ...\n" shim commands (line mux diverts them)
serial1 <-> UART1   -> console-sim bytes ([key:value]\xff 14-key cycle)
                    <- firmware motor-TX capture (emulate bursts)
```

Shim command surface (full list in `main/qemu_test/qemu_test_shim.h`):
`QT lease`, `QT emulate`, `QT motion <tenths> <half_pct>`, `QT exit`,
`QT tread <0|1>`, `QT vbus <0|1>`,
`QT k1 <auto|stuck|bypass|emulate|open|closed>` (script the K1 feedback
model — `stuck` freezes the poles, the rest force a pole state; exists to
reach the fail-closed feedback paths), `QT state`.

Observability is the SafetyController audit ring drained to `QTAUDIT
<abs_index> <event_text>` lines — the exact model event strings the host
suite already asserts (no parallel logging to drift). `QTSTATE` reports
controller intent (`relay=`/`tx=`) AND the shim-observed IO-boundary
levels (`io_relay=`/`io_tx=`, what `set_relay_cmd`/`set_tx_enable` last
drove — so relay/TX assertions are not controller self-reports) plus
`t_us=`, the guest monotonic clock at snapshot time, which the scenarios
use for hard guest-time deadline/cadence bounds. Note the entry audit
labels `command_zero` .. `wait_entry_gap` are batch-emitted intent
markers from `request_emulate`; actuation evidence is the feedback
qualification events (command-coupled K1 model), `io_relay`/`io_tx`, and
the byte-level UART1 TX capture.

## Scenarios

* **S1** proxy passive decode: try5 capture replay + synthetic cycles +
  malformed-frame fuzz tail; asserts frame decode counts, byte counters,
  zero TX, no emergencies, no reboot.
* **S2a/S2b** console-silence semantics: benign in PROXY; fatal while
  EMULATING (`emergency:console_stale`), motion zeroed, TX ceases — with
  a hard guest-time bracket (1.2–4.0 s via `t_us`) proving the 1.5 s
  `CONSOLE_FRESH_US` value, not just the event's existence, and a
  pre-stop EMULATING/no-emergency check so the event can only be caused
  by the injected silence.
* **S3** gap-safe emulate entry: exact ordered audit subsequence, first
  TX cycle is the zero frame, 5 bursts cover the 14 keys exactly once,
  owner motion (50/30 → `[hmph:1F4]`/`[inc:1E]`) mirrors only after the
  zero burst; burst cadence gets a hard guest-time upper bound (25
  bursts ≤ 8 s guest via `t_us`) on top of the advisory wall-clock check.
* **S4** console takeover: changed hmph value while emulating →
  `emergency:console_takeover`, PROXY, no latched fault, TX ceases.
* **S5** clamp enforcement: 121 tenths / 31 half-pct rejected on the real
  rejection path; exact limits 120/30 accepted.
* **S6** default-build guard: unmodified `qemu_smoke.sh` passes; test
  surface provably absent from the production image.
* **S7a/S7b** negative feedback paths (`QT k1`): entry with a stuck K1
  fails closed at the 10 ms feedback deadline
  (`emergency:entry_feedback_timeout`, latched fault, zero wire bytes,
  re-entry refused after healing); mid-EMULATING pole loss →
  `emergency:relay_feedback_invalid`, latched fault, relay/TX released
  at the IO boundary, TX ceases.

Run one scenario:

```bash
python3 -m pytest tools/qemu_harness/test_scenarios.py::test_s3_emulate_entry_happy_path -m qemu -v
```

## Timing philosophy

QEMU wall/guest time is elastic: protocol deadlines are asserted via
audit-event presence/order plus hard GUEST-clock bounds from `QTSTATE
t_us` (both endpoints are guest samples, so the wall/guest ratio cancels
out) — the 1.5 s console-freshness deadline (S2b) and the 100 ms burst
cadence (S3) are bounded this way. Guest heartbeat uptime covers coarser
liveness waits; wall clocks only pace injection and bound waits
generously; S3's wall-clock mean-gap check stays advisory (warn-only).

## Capture fixtures

`capture_streams.py` converts `cpp/captures/try*.csv` (read-only import
of the proven `decode_inverted.py` / `analyze_logic.py` decoders — ch5 =
console pin 6, ch2 = motor pin 3, stop_ok bytes only, 3 ms burst
grouping) into timed-burst JSONL cached under
`/tmp/esp32tap_qemu_harness_cache/` keyed by CSV mtime. Synthetic streams
(`synth.py`, encoder parity pinned to the cpp doctest golden vectors by
`test_encoders.py`) are the default fixtures; the capture replay is the
fidelity cross-check inside S1.

## Networking note

The docker QEMU publishes both serial TCP servers on host loopback via
`--network=host` (Linux). If host networking is unavailable, replace it
with `-p <port>:<port>` pairs for two fixed ports and pin `_free_port()`
accordingly.
