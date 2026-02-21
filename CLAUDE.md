# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reverse-engineering and control toolkit for the Precor 9.31 treadmill serial bus. A Raspberry Pi intercepts the RS-485 serial communication between the console (Upper PCA) and motor controller (Lower PCA), enabling monitoring, proxying, and emulation of the controller.

## Deployment

The Raspberry Pi connected to the treadmill is at host `rpi`. All four services are managed via systemd and deployed with `make deploy`.

```bash
# Deploy everything to Pi (stages build/, rsyncs, builds on Pi, restarts all services):
make deploy                    # or: deploy/deploy.sh

# Stage build/ directory without deploying:
make stage

# Services on Pi (managed by systemd, auto-start on boot):
sudo systemctl status treadmill-io      # C++ GPIO daemon
sudo systemctl status treadmill-server  # FastAPI web server
sudo systemctl status ftms              # FTMS Bluetooth daemon
sudo systemctl status hrm               # HRM Bluetooth daemon

# Service dependency chain:
#   treadmill-io  ←  treadmill-server (After+Wants)
#   treadmill-io  ←  ftms (After+Wants)
#   bluetooth     ←  ftms (After+Requires)
#   bluetooth     ←  hrm (After+Requires)

# Service templates in deploy/*.service.in (rendered during stage)

# Manual tools (for debugging):
python3 dual_monitor.py        # Primary TUI (curses, side-by-side panes)
python3 listen.py              # Simple KV listener (--changes, --unique flags)
```

## Dependencies

- `pigpio` (system package, libpigpio) — linked by `treadmill_io` for GPIO access
- `fastapi`, `uvicorn`, `python-multipart` — web server (server.py)
- `google-genai` — Gemini SDK for AI coach + voice
- `gpxpy` — GPX route parsing (server.py)
- `pytest`, `pytest-asyncio` — test suite
- Build (C++): `make` (g++ with C++20, libpigpio-dev)
- Build (Rust/FTMS+HRM): `cross` for aarch64 cross-compilation, or `cargo build` on Pi
- Test deps (header-only, vendored): `doctest` (testing), `rapidjson` (JSON)

## Architecture

### Hardware Wiring

Pin 6 of the treadmill cable is **cut** through the Pi (intercept + proxy/emulate). Pin 3 is **tapped** passively.

```
Console ──pin6──> [GPIO 27] Pi [GPIO 22] ──pin6──> Motor
                               Motor ──pin3──> [GPIO 17] Pi (tap)
```

GPIO assignments live in `gpio.json` — all tools read from it at startup.

### RS-485 Inverted Polarity (Critical)

The serial bus uses RS-485 signaling which idles LOW (opposite of standard UART). All GPIO serial I/O must use `bb_serial_invert=1` for reads and manually inverted waveforms for writes. See `RS485_DISCOVERY.md` for the full investigation. The key takeaway: **both pins carry the same `[key:value]` KV text protocol** — earlier "binary frame" interpretations were caused by polarity confusion.

### C++ Binary — `treadmill_io`

All GPIO I/O is handled by a C++20 binary (`src/`) that links libpigpio directly (no daemon). It reads pin assignments from `gpio.json`, handles KV parsing, proxy forwarding, and emulation, and serves data to clients over a Unix domain socket (`/tmp/treadmill_io.sock`). Both the Python server and the FTMS daemon connect as socket clients. See `treadmill_client.py` for the Python IPC client library. Runs as a systemd service (`treadmill-io.service`).

### Protocol

Both directions use `[key:value]` text framing at 9600 baud, 8N1.

- **Console→Motor** (pin 6): `[key:value]\xff` or `[key]\xff`, repeating 14-key cycle in 5 bursts
- **Motor→Console** (pin 3): `[key:value]` responses (no `\xff` delimiter)
- **Speed encoding**: `hmph` key = mph × 100 in uppercase hex (e.g., 1.2 mph = `78`)
- **14-key cycle**: `inc, hmph, amps, err, belt, vbus, lift, lfts, lftg, part, ver, type, diag, loop`

### Application Modes

- **Proxy mode** — forwards intercepted console commands to the motor unchanged
- **Emulate mode** — replaces the console entirely, sending synthesized KV commands with adjustable speed/incline
- Proxy and emulate are mutually exclusive; transitions are **automatic** (see Auto Proxy/Emulate Mode below)
- Manual toggle available via debug mode (triple-tap connection dot in UI)

### FTMS Bluetooth — `ftms-daemon`

A Rust daemon (`ftms/`) that advertises the treadmill as a Bluetooth FTMS (Fitness Machine Service, UUID 0x1826) device. Connects to `treadmill_io` via the same Unix socket, reads speed/incline state, and broadcasts it over BLE so fitness apps (Zwift, QZ Fitness, Apple Watch, Garmin) can see the treadmill.

- **Crate**: `ftms/` with `bluer` (BlueZ bindings), `tokio`, `serde_json`
- **Modules**: `main.rs` (entry), `treadmill.rs` (socket client), `ftms_service.rs` (GATT server), `protocol.rs` (binary encoding/UUIDs), `debug_server.rs` (TCP debug port 8826)
- **GATT characteristics**: Feature (0x2ACC), Treadmill Data (0x2ACD, notifies at 1 Hz), Speed Range (0x2AD4), Incline Range (0x2AD5), Control Point (0x2AD9), Machine Status (0x2ADA)
- **Control Point**: Supports Set Target Speed, Set Target Incline, Start/Resume, Stop/Pause — converts km/h to mph and sends commands back through the socket
- **Cross-compile**: `cd ftms && cross build --release --target aarch64-unknown-linux-gnu`
- Runs as a systemd service (`ftms.service`), depends on `bluetooth.target` and `treadmill-io.service`

### HRM Bluetooth — `hrm-daemon`

A Rust daemon (`hrm/`) that acts as a BLE GATT client, scanning for and connecting to Bluetooth heart rate monitors (HR Service UUID 0x180D). Reads HR Measurement notifications (UUID 0x2A37) and serves data over a Unix domain socket so server.py and the UI can display real-time heart rate.

- **Crate**: `hrm/` with `bluer` (BlueZ bindings), `tokio`, `serde_json`
- **Modules**: `main.rs` (entry), `scanner.rs` (BLE scan + connect + HR parsing), `server.rs` (Unix socket server), `config.rs` (persist saved device), `debug_server.rs` (TCP debug port 8827)
- **Socket**: `/tmp/hrm.sock` — newline-delimited JSON, bidirectional. Broadcasts `{"type":"hr","bpm":142,"connected":true,...}` at 1 Hz
- **Commands**: `connect` (with address), `disconnect`, `forget`, `scan`, `status`
- **Device selection**: Auto-connects to saved device from `hrm_config.json`. If multiple devices found, sends `scan_result` to clients for user selection
- **Debug server**: TCP port 8827 — `mock <bpm>` injects fake HR data for testing without hardware, `mock off` resets
- **Cross-compile**: `cd hrm && cross build --release --target aarch64-unknown-linux-gnu` (requires custom Docker image for libdbus, see `hrm/Dockerfile.cross`)
- **Python client**: `hrm_client.py` — same pattern as `treadmill_client.py` (threaded reader, auto-reconnect with backoff)
- **Graceful degradation**: If hrm-daemon isn't running, server.py continues without HR. Auto-reconnects when daemon becomes available
- Runs as a systemd service (`hrm.service`), depends on `bluetooth.target`

### Web UI

`server.py` serves a React + TypeScript SPA (source in `ui/`, builds to `static/`) with WebSocket for real-time KV data streaming and REST endpoints for speed/incline/mode control. Runs as a systemd service (`treadmill-server.service`).

### AI Coach — Gemini Integration

`program_engine.py` handles Gemini API calls and interval program execution:
- **Gemini model**: `gemini-2.5-flash` via REST API with function calling
- **Tools**: `set_speed`, `set_incline`, `start_workout`, `stop_treadmill`, `pause/resume/skip`, `extend_interval`, `add_time`
- **ProgramState**: manages interval execution with 1s tick loop, pause/skip/extend support, encouragement milestones (25/50/75%)
- **GPX import**: `POST /api/gpx/upload` parses GPX routes into incline-based interval programs

### Program History

Recently generated/loaded programs are saved to `program_history.json` (max 10 entries). Programs are deduplicated by name. History is accessible via REST API and shown as a horizontal scroll in the UI.

### Auto Proxy/Emulate Mode

The C binary auto-detects mode transitions (no manual toggle needed):
- **Speed/incline command received** → auto-enables emulate mode
- **Console button press detected** (hmph/inc value change while emulating) → auto-switches to proxy mode

This logic lives in the C binary (not Python) so that mode transitions work even if the Python server crashes — the treadmill stays responsive to physical console buttons regardless of software state.

### Analysis Tools (offline)

- `analyze_logic.py` — decodes logic analyzer CSVs with standard UART polarity
- `decode_inverted.py` — decodes logic analyzer CSVs with inverted polarity detection

## Testing

```bash
# C++ unit tests (92 tests, runs from src/)
make test

# Deploy to Pi, build, restart binary, run hardware integration tests
# Requires: Pi reachable at `rpi`, treadmill powered on
make test-pi

# Full pre-commit gate: local unit tests + Pi hardware tests
make test-all

# FTMS Rust unit tests (27 tests, protocol encoding/decoding)
cd ftms && cargo test

# FTMS integration tests (17 tests, requires ftms-daemon + treadmill_io running on Pi)
cd ftms && cargo test --test debug_integration -- --ignored --test-threads=1

# HRM Rust unit tests (14 tests, HR parsing + config)
cd hrm && cargo test

# HRM Python client tests (6 tests, mock daemon)
python3 -m pytest tests/test_hrm_client.py -v

# Python unit tests (mocked sleep, <1s)
python3 -m pytest tests/test_program_engine.py tests/test_server_integration.py -v

# Python live integration tests (real asyncio.sleep, ~45s)
python3 -m pytest tests/test_live_program.py -v

# All non-hardware Python tests
python3 -m pytest -m "not hardware" -v
```

Note: `make test` automatically stops the `treadmill-io` service before running (to free the socket) and restarts it after, even if tests fail.

## API Reference

### Status & Control
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Current treadmill state (speed, incline, mode) |
| `/api/speed` | POST | Set belt speed. Body: `{"value": <mph>}` |
| `/api/incline` | POST | Set incline grade. Body: `{"value": <int>}` |
| `/api/emulate` | POST | Toggle emulate mode (debug). Body: `{"enabled": true}` |
| `/api/proxy` | POST | Toggle proxy mode (debug). Body: `{"enabled": true}` |

### Programs
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/program` | GET | Current program state |
| `/api/program/generate` | POST | Generate program via Gemini. Body: `{"prompt": "..."}` |
| `/api/program/start` | POST | Start the loaded program |
| `/api/program/stop` | POST | Stop program, zero speed/incline |
| `/api/program/pause` | POST | Toggle pause/resume |
| `/api/program/skip` | POST | Skip to next interval |
| `/api/program/extend` | POST | Adjust current interval. Body: `{"seconds": <int>}` |

### History & Import
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/programs/history` | GET | List recent programs (max 10) |
| `/api/programs/history/{id}/load` | POST | Reload a saved program |
| `/api/gpx/upload` | POST | Upload GPX route file (multipart form) |

### Heart Rate Monitor
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/hrm` | GET | HRM status (heart_rate, connected, device, available_devices) |
| `/api/hrm/select` | POST | Connect to a specific HRM. Body: `{"address": "AA:BB:CC:DD:EE:FF"}` |
| `/api/hrm/forget` | POST | Clear saved HRM device, disconnect |
| `/api/hrm/scan` | POST | Trigger a new BLE scan for HRM devices |

### AI Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send message to AI coach. Body: `{"message": "..."}`. Returns `{"text": "...", "actions": [...]}` |

### WebSocket
| Endpoint | Description |
|----------|-------------|
| `/ws` | Real-time KV data stream + program state updates. Receives JSON messages with `type: "status"` or `type: "program"`. |

## Code Review Standards

When reviewing or writing code in this project, enforce these principles:

### Docs Stay Current
- **CLAUDE.md must reflect reality.** If you add a feature, endpoint, mode, or dependency, update this file. Stale docs are a bug.
- Inline comments only where the "why" isn't obvious. Don't comment the "what."

### Tests Are Real
- **Two tiers required:** fast unit tests (mocked I/O, <1s) AND live integration tests (real `asyncio.sleep`, real timers, ~seconds).
- Unit tests verify logic in isolation. Live tests prove the system actually works end-to-end with real timing.
- Hardware tests (`@pytest.mark.hardware`) exist for Pi-only verification but aren't required to pass in CI.
- Every behavior-changing PR should have at least one test that would fail without the change.

### DRY
- Constants live in one place (e.g., `MAX_SPEED_TENTHS` in `treadmill_client.py`, shared by C and Python).
- Don't duplicate logic between `_exec_fn()` and REST endpoints — they should share the same code path.
- If you see the same 3+ lines in two places, extract it.

### C++ Safety Rules

All C++ code in `src/` must follow these rules. The environment is resource-constrained (Raspberry Pi) and timing-critical (9600 baud serial).

#### Memory & Performance

- **C++20**, compiled with `-std=c++20 -fno-exceptions -fno-rtti`. Use RAII for all resource management (locks, threads, file descriptors).
- **Hot path = zero allocation**: the serial read/write loop and emulate cycle must never use `new`, `malloc`, `std::string`, `std::vector`, or `std::stringstream`. Stack and static only — `std::array`, pre-allocated fixed buffers.
- **Cold path (IPC)** may use `std::string` for JSON building and `std::string` for config parsing — these paths are orders of magnitude slower than serial timing.

#### Type Safety

- **`std::string_view`** for input parameters, **`std::string`** for internal processing on cold paths. No raw `const char*` crossing function boundaries.
- **`std::span<const uint8_t>`** for binary data buffers (serial reads). View, don't copy — create subspans to reference parts of a buffer. Raw `uint8_t*` only at the pigpio C API boundary.
- **`.at()`** for all container/array indexing (bounds-checked; terminates on out-of-range with `-fno-exceptions`, safer than silent UB from `[]`).
- **No C-style casts**. Use `static_cast` for numeric conversions. Use `std::bit_cast` for type-punning (e.g., bytes → numeric). **Known exceptions**: `reinterpret_cast<const char*>(uint8_t*)` for `string_view` construction (standard-allowed character aliasing), and `reinterpret_cast<sockaddr*>` (POSIX socket API requirement).
- **`uint8_t`** for binary data (not `char`). `std::byte` is acceptable but verbose for bitwise operations.

#### Safety & Error Handling

- **No exceptions** (`-fno-exceptions`). Errors are expected control flow (noisy serial line), not exceptional events.
- **`std::optional<T>`** or `bool` + out-param for fallible functions. Prefer `std::optional` for new code.
- **No raw pointers or C-style string functions** (`strcmp`, `strstr`, `strlen`, `sscanf`, `snprintf`, `memcpy`). Use `std::string_view` operations, `std::from_chars`/`std::to_chars`, `.copy()`. **Exception**: the pigpio hardware boundary — keep it as thin as possible.
- **Input length validation**: all functions accepting external input (IPC commands, config files, serial data) must check maximum allowed length before processing.

### Clear Layers

**C++ binary** (`src/`): Transport layer only. This code must be:
- **Incredibly narrow in scope**: GPIO I/O, KV protocol parsing, proxy forwarding, emulation cycle. Nothing else.
- **Very fast**: bit-banged serial at 9600 baud with DMA waveforms. No allocations in hot paths, no blocking.
- **Safety-critical**: the 3-hour timeout, the zero-speed-on-emulate-start, and auto proxy/emulate detection live here because they must work even if Python is dead.
- No application logic, no knowledge of programs/workouts/AI. It just moves bytes and manages modes.
- Note: The C++ binary accepts incline 0-99 (hardware range). The application layer (Python/Gemini) enforces 0-15 for safety.

**FTMS daemon** (`ftms/`): BLE transport layer only. Reads treadmill state from the Unix socket, encodes it per the FTMS spec, and advertises over Bluetooth. Control Point writes are converted back to socket commands. No application logic, no knowledge of programs/workouts/AI.

**HRM daemon** (`hrm/`): BLE client layer only. Scans for heart rate monitors, connects, reads HR notifications, and serves data on a Unix socket. No application logic, no knowledge of programs/workouts/AI.

**Python clients** (`treadmill_client.py`, `hrm_client.py`): Thin IPC wrappers to daemon sockets. No business logic.

**Program engine** (`program_engine.py`): Interval execution + Gemini API. No HTTP, no GPIO, no imports from server.

**Server** (`server.py`): **All shared business logic lives here.** This is the single source of truth for:
- State management (speed, incline, mode, program)
- Endpoint validation and clamping
- Coordinating between program engine and treadmill client
- Multiple clients (web UI, FTMS daemon, future CLI, future watch app) all connect through the same socket — logic must not leak into any single client.

**UI** (`ui/` → `static/`): Display layer only. Principles:
- **No business logic.** All decisions happen server-side. The UI calls API endpoints and renders what comes back.
- **Safety first.** Stop button always visible when belt is moving. Emergency stop is one tap.
- **Minimal by default.** Show only what's needed right now. Debug info (mode badge, raw state) hidden behind triple-tap.
- **Beautiful and peaceful.** Warm muted palette, subtle texture, organic curves. No neon, no visual noise.
- **Progressive disclosure.** Essential info (speed, time, current interval) is prominent. Settings, history, and debug are tucked away but accessible.
- **Mobile/tablet first.** Touch targets 44px+, no hover-dependent interactions, responsive layout, haptic feedback.
