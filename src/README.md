# treadmill_io — GPIO Serial Daemon

The safety-critical transport layer for the Precor 9.31 treadmill. This C++ binary is the only code that touches GPIO hardware. It reads and writes the treadmill's serial bus, manages proxy/emulate mode transitions, and serves parsed data to application-layer clients over a Unix socket. It has no knowledge of programs, workouts, AI, or HTTP — it just moves bytes and enforces safety invariants.

## What It Does

The treadmill's console and motor controller talk over an RS-485 serial bus at 9600 baud. Pin 6 (console → motor) is **cut** through the Pi; pin 3 (motor → console) is **tapped** passively. This binary sits on both wires.

```
Console ──pin 6──▶ [GPIO 27] ──▶  treadmill_io
treadmill_io [GPIO 22] ──▶ ──pin 6 ──▶ Motor
Motor ──pin 3──▶ [GPIO 17] treadmill_io

(treadmill_io is passively reading pin3, but proxying pin 6
```

In **proxy mode** (the default), raw bytes from the console are forwarded to the motor with minimal latency — the treadmill works normally. In **emulate mode**, the binary replaces the console entirely, sending a synthesized command cycle so the application layer can control speed and incline.

## The Wire Protocol

Both directions use `[key:value]` text framing. The console sends a repeating 14-key cycle in 5 bursts with ~100ms gaps:

```
Burst 1:  [inc:A][hmph:FA]             ← incline (5%) + speed
Burst 2:  [amps][err][belt]            ← sensor queries
Burst 3:  [vbus][lift][lfts][lftg]
Burst 4:  [part:6][ver][type]          ← identity
Burst 5:  [diag:0][loop:5550]          ← diagnostics
```

Speed is encoded as `mph × 100` in uppercase hex (`hmph:FA` = 2.50 mph). Incline is encoded as half-percent units in uppercase hex (`inc:A` = 5%, `inc:1E` = 15%). The motor responds to queries with the same bracket format. Pin 6 messages are terminated with `0xFF`; pin 3 messages are not.

**RS-485 polarity**: the bus idles LOW (opposite of standard UART). All GPIO reads use `bb_serial_invert=1` and all writes use manually inverted DMA waveforms. See [`captures/RS485_DISCOVERY.md`](captures/RS485_DISCOVERY.md) for the full investigation.

## Safety First

Safety is the design principle that drives every decision in this binary. A treadmill belt that won't stop is dangerous. Every feature, every mode transition, every watchdog exists to guarantee that if anything goes wrong — server crash, network drop, software bug — the belt stops or the physical console regains control. These invariants live here in C++, not in Python or the UI, because they must work unconditionally:

- **Zero-on-emulate-start**: entering emulate mode always zeros speed and incline before sending any commands to the motor.
- **3-hour timeout**: if speed and incline don't change for 3 hours during emulate, both are reset to zero.
- **Heartbeat watchdog**: if no IPC command arrives for 4 seconds during emulate, the binary exits emulate and returns to proxy. The physical console regains control immediately.
- **Client disconnect watchdog**: if all IPC clients disconnect during emulate, same behavior — exit to proxy.
- **Auto proxy-on-console-change**: if someone presses a button on the physical console while emulating (detected as a change in the console's `hmph` or `inc` values), the binary instantly switches to proxy. Physical controls always win.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    TreadmillController                        │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │SerialReader  │  │SerialReader  │  │ EmulationEngine      │ │
│  │(console,     │  │(motor,      │  │ Synthesizes 14-key   │ │
│  │ GPIO 27)     │  │ GPIO 17)    │  │ cycle, writes via    │ │
│  │  on_raw →    │  │  on_kv →    │  │ SerialWriter         │ │
│  │  proxy fwd   │  │  ring push  │  │ (GPIO 22)            │ │
│  │  on_kv →     │  │             │  │                      │ │
│  │  auto-detect │  │             │  │ 3-hour safety timer  │ │
│  └──────────────┘  └─────────────┘  └──────────────────────┘ │
│          │                │                    │              │
│          ▼                ▼                    ▼              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                RingBuffer (2048 slots)                   │ │
│  │  KV events + status snapshots, lock-free consumer reads │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              IpcServer (Unix socket)                     │ │
│  │  /tmp/treadmill_io.sock — up to 4 clients               │ │
│  │  Inbound: JSON commands (speed, incline, mode, etc.)    │ │
│  │  Outbound: JSON events drained from ring buffer         │ │
│  │  Heartbeat watchdog runs in IPC poll loop               │ │
│  └─────────────────────────────────────────────────────────┘ │
│                           │                                  │
│  ┌────────────────────────┴────────────────────────────────┐ │
│  │              ModeStateMachine                            │ │
│  │  Single authority on proxy/emulate transitions           │ │
│  │  Mutex-protected control plane, lock-free data reads     │ │
│  │  Speed clamped 0–120 tenths (12.0 mph)                  │ │
│  │  Incline clamped 0–99                                    │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

Three threads run concurrently:
- **Console read** — polls GPIO 27, fires raw callback (proxy forwarding) and KV callback (auto-detect)
- **Motor read** — polls GPIO 17, pushes parsed KV events to the ring
- **IPC** — accepts socket connections, dispatches commands, drains ring to clients, runs heartbeat watchdog

A fourth thread runs only during emulate mode:
- **Emulation** — sends the 14-key cycle to the motor via DMA waveforms on GPIO 22

## Modules

| File | Role |
|------|------|
| `treadmill_io.cpp` | `main()`, signal handling, GPIO init |
| `treadmill_io.h` | `TreadmillController` — top-level wiring, thread lifecycle |
| `serial_io.h` | `SerialReader` (inverted bit-bang read) + `SerialWriter` (DMA waveforms) |
| `kv_protocol.h/cpp` | `[key:value]` parser + builder, speed hex encoding. Hot path — zero allocation |
| `emulation_engine.h` | 14-key cycle generator, 3-hour safety timeout |
| `mode_state.h/cpp` | Proxy/emulate state machine, speed/incline clamping, atomic snapshots |
| `ipc_server.h/cpp` | Unix socket server, JSON command dispatch, ring buffer drain |
| `ipc_protocol.h/cpp` | Typed command/event structs, RapidJSON parsing |
| `ring_buffer.h` | Lock-free-read circular buffer (2048 × 256-byte slots) |
| `config.h` | `gpio.json` loader, GPIO pin validation |
| `gpio_port.h` | GPIO interface contract (constants, documentation) |
| `gpio_pigpio.h` | Production `PigpioPort` — thin wrapper around libpigpio C API |
| `gpio_mock.h` | Test `MockGpioPort` — records calls, no hardware |

## IPC Protocol

Clients connect to `/tmp/treadmill_io.sock` and exchange newline-delimited JSON.

**Inbound commands** (client → binary):

| Command | JSON | Effect |
|---------|------|--------|
| Set speed | `{"cmd":"speed","value":3.5}` | mph float, auto-enables emulate |
| Set incline | `{"cmd":"incline","value":5}` | Integer 0–99, auto-enables emulate |
| Enable emulate | `{"cmd":"emulate","value":true}` | Zeros speed/incline, starts cycle |
| Enable proxy | `{"cmd":"proxy","value":true}` | Stops emulation, resumes forwarding |
| Get status | `{"cmd":"status"}` | Pushes a status event |
| Heartbeat | `{"cmd":"heartbeat"}` | Resets watchdog timer |
| Quit | `{"cmd":"quit"}` | Shuts down the binary |

**Outbound events** (binary → client):

| Event | Fields | Description |
|-------|--------|-------------|
| KV | `{"type":"kv","source":"console\|motor\|emulate","key":"...","value":"...","ts":1.234}` | Every parsed `[key:value]` pair from the wire |
| Status | `{"type":"status","proxy":true,"emulate":false,"emu_speed":0,"emu_incline":0,...}` | Mode + speed/incline snapshot |

## Building

```bash
# From project root (delegates to src/Makefile, output in build/)
make

# From src/ directly
make            # -> ../build/treadmill_io

# Run (must be root, pigpiod must NOT be running)
sudo ../build/treadmill_io
```

Requires `libpigpio-dev`. Compiled with C++20, `-fno-exceptions -fno-rtti`. Hot paths (serial read/write, proxy forwarding) are zero-allocation — stack buffers and fixed-size arrays only. Heap allocation (`std::string`, RapidJSON) is limited to the IPC cold path.

## Testing

```bash
make test       # 92 tests across 8 binaries
```

This automatically stops the `treadmill-io` systemd service (to free the socket), runs all tests, and restarts it — even if tests fail.

| Test binary | What it covers |
|-------------|----------------|
| `test_kv_protocol` | `[key:value]` parsing, speed hex encode/decode, edge cases |
| `test_ipc_protocol` | JSON command parsing, event building, malformed input |
| `test_ring_buffer` | Push/drain, wraparound, concurrent access |
| `test_mode_state` | Proxy/emulate transitions, clamping, auto-detect, safety reset |
| `test_emulation` | 14-key cycle output, speed/incline encoding |
| `test_integration` | Full controller with mock GPIO, end-to-end IPC |
| `test_ipc_server` | Socket accept, command dispatch, client disconnect |
| `test_controller_live` | Controller startup/shutdown, thread lifecycle |

All tests use `MockGpioPort` — no hardware required. The `gpio_mock.h` records all GPIO calls for assertion.

## Pin Configuration

GPIO assignments live in [`gpio.json`](../gpio.json) at the project root. The binary reads it at startup:

```json
{
  "console_read": {"gpio": 27, "physical_pin": 13},
  "motor_write":  {"gpio": 22, "physical_pin": 15},
  "motor_read":   {"gpio": 17, "physical_pin": 11}
}
```
