# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reverse-engineering and control toolkit for the Precor 9.31 treadmill serial bus. A Raspberry Pi intercepts the RS-485 serial communication between the console (Upper PCA) and motor controller (Lower PCA), enabling monitoring, proxying, and emulation of the controller.

## Deployment

The treadmill controller is the Pi Zero 2 W at host `rpi-zero` (primary); the Pi 4 `rpi` is a hot spare. `PI_HOST` selects the target (default `rpi-zero`). All four services are systemd-managed.

**OS image (your hardware).** The Pi runs a stock Debian-based Pi OS that you
build for your board with the reproducible image builder in `provisioning/`
(see [`provisioning/dietpi/README.md`](provisioning/dietpi/README.md) — DietPi
is the reference implementation; adapt regulatory/board settings for other
hardware). You never hand-install OS packages: the deploy/setup step
**auto-installs the OS runtime prerequisites** (`python3`, `python3-venv/pip`,
`libpigpio1`, `rsync`) idempotently if missing, so a bare provisioned image
and a fully-loaded one converge. This works on any Pi OS whose apt provides
`libpigpio1` (DietPi and Raspberry Pi OS both pull `1.79-1+rpt1` from
`archive.raspberrypi.com`).

**Software.** Compiled code (C++ `treadmill_io`, Rust `ftms-daemon`/`hrm-daemon`) is **cross-built off-Pi** in one aarch64 Docker toolchain; build-on-Pi is retired. The Python venv is still `pip`-installed on the Pi. Install is driven by the single source of truth `deploy/manifest.txt` (parsed as data), shared by the live deployer and the image baker so a flashed Pi and an rsync'd Pi are byte-identical. The Gemini API key is a per-device secret — gitignored and deliberately rsync-excluded so a normal deploy never clobbers it; push it explicitly once per device with `make deploy-key` (local `./.gemini_key` → Pi). Deploy refuses to run while the belt is moving (queries /api/status; `FORCE=1` overrides). **Device-owned state is sacred:** the deploy rsyncs with `--delete`, so every file the Pi owns and the repo can't regenerate is listed in `DEVICE_STATE_EXCLUDES` in `deploy/deploy.sh` — `treadmill.db` plus its `-wal`/`-shm` sidecars (profiles, runs, saved workouts), the pre-SQLite migration JSONs, `hrm_config.json`. Anything left out of that list is destroyed on the next deploy. As a second line of defence the deploy takes a `sqlite3`-backup-API snapshot of `treadmill.db` **before** rsync, into `~/treadmill-backups` (outside the deploy dir, so `--delete` can never reach it); a failed backup aborts the deploy (`SKIP_BACKUP=1` overrides, `KEEP_BACKUPS` sets retention, default 10). `deploy/deploy.sh backup` takes one on demand. Guarded by `deploy/tests/test_device_state.sh`. `treadmill_io` is wired into the network-independent Path A slot (`treadmill-critical.target`) so belt control starts early. On the 512MB Zero, run the headroom gate after deploy: `bash deploy/tests/mem-headroom.sh` (asserts ≥40MB MemAvailable, 0 oom-kill). The whole stack can be acceptance-checked end-to-end with `make ship-check` (belt-clear) / `make ship-check-nobelt`.

```bash
# Build all 3 aarch64 binaries in containers -> build/
make cross

# Cross-build + manifest rsync + ordered atomic restart (treadmill_io last):
make deploy                    # or: deploy/deploy.sh

# Push the per-device Gemini key (local ./.gemini_key -> Pi; once per device):
make deploy-key

# Snapshot the Pi's database on demand (-> ~/treadmill-backups on the Pi):
deploy/deploy.sh backup

# Bake a flashable full-appliance .img (provisioning toolkit):
make image

# Acceptance-check a live device end-to-end (one READY/NOT-READY verdict):
make ship-check            # drives the belt — belt MUST be clear
make ship-check-nobelt     # non-moving checks only (no treadmill needed)

# Assemble build/ without deploying:
make stage

# Target the Pi 4 spare instead of the Zero:
PI_HOST=rpi make deploy

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
#   treadmill-critical.target  ←  treadmill-io (Path A, network-independent early start)

# Service templates in deploy/*.service.in (rendered during stage)

# Manual tools (for debugging):
python3 python/tools/dual_monitor.py        # Primary TUI (curses, side-by-side panes)
python3 python/tools/listen.py              # Simple KV listener (--changes, --unique flags)
```

## Local Development

Local dev runs `python/server.py` directly — it is the API + WebSocket backend (no web UI is served).

```bash
# First time (or per worktree): allocate unique ports
scripts/setup-worktree.sh        # creates worktree.env with random free ports

# Launch the dev server
./scripts/dev.sh                 # connects to real Pi
TREADMILL_MOCK=1 ./scripts/dev.sh  # mock mode, no Pi needed

# The server URL is printed on startup.
# Ports are dynamic (from worktree.env). Do NOT hardcode port numbers.
```

**Key files:** `scripts/dev.sh` (launcher), `scripts/worktree-env.sh` (port sourcing), `scripts/setup-worktree.sh` (port allocation).

**Verifying UI changes:** The UI is the Android app (`kotlin/`). Build and install it on the emulator or tablet (see Kotlin/Android notes) and point it at the dev server or the Pi.

## Dependencies

- `pigpio` (system package, libpigpio) — linked by `treadmill_io` for GPIO access
- `fastapi`, `uvicorn`, `python-multipart` — web server (server.py)
- `google-genai` — Gemini SDK for AI coach + voice
- `gpxpy` — GPX route parsing (server.py)
- `pytest`, `pytest-asyncio` — test suite
- Build (C++): `make` (g++ with C++20, libpigpio-dev)
- Build (Rust/FTMS+HRM): `cross` for aarch64 cross-compilation, or `cargo build` on Pi
- Test deps (header-only, vendored): `doctest` (testing), `rapidjson` (JSON)

### Device Discovery (mDNS)

The Pi advertises one DNS-SD service via a static Avahi file
(`/etc/avahi/services/treadmill.service`, installed from
`deploy/treadmill.avahi-service` through the manifest): type
`_treadmill._tcp`, port 8000, TXT `scheme=https`, `path=/`. Native apps
(Android `NsdManager`, iOS `NWBrowser`) discover it and either auto-connect
(single result) or show a picker (multiple), with manual entry as the
zero-result fallback. The `scheme=https` contract depends on the
per-device self-signed cert work (`precor-9_3x-41a`).

## Architecture

### Hardware Wiring

Pin 6 of the treadmill cable is **cut** through the Pi (intercept + proxy/emulate). Pin 3 is **tapped** passively.

```
Console ──pin6──> [GPIO 27] Pi [GPIO 22] ──pin6──> Motor
                               Motor ──pin3──> [GPIO 17] Pi (tap)
```

GPIO assignments live in `gpio.json` — all tools read from it at startup.

The physical interface is the custom Pi Zero 2 W hat in
[`hardware/PiZeroHat/`](hardware/PiZeroHat/README.md) (dual RJ45
`From Console`/`To Motor`, onboard D24V10F5 buck off the treadmill's `+8V`;
KiCad sources + gerbers + wiring docs there). A breadboard tap is the
quick alternative — see [`HARDWARE.md`](HARDWARE.md).

### RS-485 Inverted Polarity (Critical)

The serial bus uses RS-485 signaling which idles LOW (opposite of standard UART). All GPIO serial I/O must use `bb_serial_invert=1` for reads and manually inverted waveforms for writes. See `RS485_DISCOVERY.md` for the full investigation. The key takeaway: **both pins carry the same `[key:value]` KV text protocol** — earlier "binary frame" interpretations were caused by polarity confusion.

### C++ Binary — `treadmill_io`

All GPIO I/O is handled by a C++20 binary (`cpp/`) that links libpigpio directly (no daemon). It reads pin assignments from `gpio.json`, handles KV parsing, proxy forwarding, and emulation, and serves data to clients over a Unix domain socket (`/tmp/treadmill_io.sock`). Both the Python server and the FTMS daemon connect as socket clients. See `python/treadmill_client.py` for the Python IPC client library. Runs as a systemd service (`treadmill-io.service`). Internal layout: `protocol/` (KV/IPC parsing), `gpio/` (hardware abstraction), `engine/` (mode state + emulation), `ipc/` (socket server), plus entry point and config at root.

**DMA crash recovery**: pigpio allocates ~23 GPU DMA memory handles via the VideoCore mailbox (`/dev/vcio`). These handles are managed by the GPU firmware and are NOT freed when a Linux process dies (SIGKILL, segfault). `GpioSession` (RAII, `gpio/gpio_session.h`) wraps the pigpio lifecycle and uses `DmaGuard` (`gpio/dma_guard.h`) to track allocated handles in a crash journal at `/run/treadmill-io.dma-handles`. On clean shutdown, the destructor calls `gpioTerminate()` and deletes the journal. On crash, the journal persists (tmpfs) and the next startup frees exactly those leaked handles before reinitializing. This prevents the `initMboxBlock: init mbox zaps failed` death spiral that previously required a Pi reboot to fix.

### Protocol

Both directions use `[key:value]` text framing at 9600 baud, 8N1.

- **Console→Motor** (pin 6): `[key:value]\xff` or `[key]\xff`, repeating 14-key cycle in 5 bursts
- **Motor→Console** (pin 3): `[key:value]` responses (no `\xff` delimiter)
- **Speed encoding**: `hmph` key = mph × 100 in uppercase hex (e.g., 1.2 mph = `78`)
- **Incline encoding**: `inc` key = half-percent units in uppercase hex (e.g., 5% incline = `A`, 15% incline = `1E`)
- **14-key cycle**: `inc, hmph, amps, err, belt, vbus, lift, lfts, lftg, part, ver, type, diag, loop`

### Application Modes

- **Proxy mode** — forwards intercepted console commands to the motor unchanged
- **Emulate mode** — replaces the console entirely, sending synthesized KV commands with adjustable speed/incline
- Proxy and emulate are mutually exclusive; transitions are **automatic** (see Auto Proxy/Emulate Mode below)
- Manual toggle available via debug mode (triple-tap connection dot in UI)

### FTMS Bluetooth — `ftms-daemon`

A Rust daemon (`rust/ftms/`) that advertises the treadmill as a Bluetooth FTMS (Fitness Machine Service, UUID 0x1826) device. Connects to `treadmill_io` via the same Unix socket, reads speed/incline state, and broadcasts it over BLE so fitness apps (Zwift, QZ Fitness, Apple Watch, Garmin) can see the treadmill.

- **Crate**: `rust/ftms/` with `bluer` (BlueZ bindings), `tokio`, `serde_json`
- **Modules**: `main.rs` (entry), `treadmill.rs` (socket client), `ftms_service.rs` (GATT server), `protocol.rs` (binary encoding/UUIDs), `debug_server.rs` (TCP debug port 8826)
- **GATT characteristics**: Feature (0x2ACC), Treadmill Data (0x2ACD, notifies at 1 Hz), Speed Range (0x2AD4), Incline Range (0x2AD5), Control Point (0x2AD9), Machine Status (0x2ADA)
- **Control Point**: Supports Set Target Speed, Set Target Incline, Start/Resume, Stop/Pause — converts km/h to mph and sends commands back through the socket
- **Proxy mode values**: In proxy mode, speed/incline come from `bus_speed`/`bus_incline` in the C++ status event (decoded motor KV readings). In emulate mode, uses `emu_speed`/`emu_incline`.
- **Cross-compile**: `cd rust/ftms && cross build --release --target aarch64-unknown-linux-gnu`
- Runs as a systemd service (`ftms.service`), depends on `bluetooth.target` and `treadmill-io.service`

### HRM Bluetooth — `hrm-daemon`

A Rust daemon (`rust/hrm/`) that acts as a BLE GATT client, scanning for and connecting to Bluetooth heart rate monitors (HR Service UUID 0x180D). Reads HR Measurement notifications (UUID 0x2A37) and serves data over a Unix domain socket so server.py and the UI can display real-time heart rate.

- **Crate**: `rust/hrm/` with `bluer` (BlueZ bindings), `tokio`, `serde_json`
- **Modules**: `main.rs` (entry), `scanner.rs` (BLE scan + connect + HR parsing), `server.rs` (Unix socket server), `config.rs` (persist saved device), `debug_server.rs` (TCP debug port 8827)
- **Socket**: `/tmp/hrm.sock` — newline-delimited JSON, bidirectional. Broadcasts `{"type":"hr","bpm":142,"connected":true,...}` at 1 Hz
- **Commands**: `connect` (with address), `disconnect`, `forget`, `scan`, `status`
- **Device selection**: Auto-connects to saved device from `hrm_config.json`. If multiple devices found, sends `scan_result` to clients for user selection
- **Debug server**: TCP port 8827 — `mock <bpm>` injects fake HR data for testing without hardware, `mock off` resets
- **Cross-compile**: `cd rust/hrm && cross build --release --target aarch64-unknown-linux-gnu` (requires custom Docker image for libdbus, see `rust/hrm/Dockerfile.cross`)
- **Python client**: `python/hrm_client.py` — same pattern as `python/treadmill_client.py` (threaded reader, auto-reconnect with backoff)
- **Graceful degradation**: If hrm-daemon isn't running, server.py continues without HR. Auto-reconnects when daemon becomes available
- Runs as a systemd service (`hrm.service`), depends on `bluetooth.target`

### Server (API + WebSocket)

`python/server.py` is an API + WebSocket backend only — there is no web UI. `GET /` returns a small JSON banner (`{"service": "precor-treadmill", "api": "/api", "ws": "/ws"}`); clients (Android app, iOS app) discover the server via mDNS and talk to `/api/*` and `/ws`. Runs as a systemd service (`treadmill-server.service`).

### Adaptive Text Readability (on-photo legibility)

The running screen draws a full-bleed background photo; all text/widgets over it are made legible by an adaptive system rather than hand-tuned opacities. Design: [`docs/superpowers/specs/2026-06-03-adaptive-text-readability-design.md`](docs/superpowers/specs/2026-06-03-adaptive-text-readability-design.md).

- **Pure engine** — APCA Lc contrast + a minimized "beauty cost" pick one coherent screen `Theme` (photo-derived tint + ivory/charcoal text + blur) plus per-region scrim. The single implementation is `kotlin/.../ui/theme/readability/` (Kotlin), asserting against the canonical spec vectors in `docs/bg-lab/golden.json` (**load-bearing**: `GoldenSyncTest` hard-codes that path and fails the Kotlin build if it drifts — do not move or delete it). APCA is the on-device guarantee. (A TS twin in `web/src/bglab/` and its `/bg-lab` tuning bench were retired with the web UI.)
- **Compose bridge** (`kotlin/.../ui/theme/GlassTheme.kt`) — `LegibleGlassPanel` darkens each panel just enough for its accents to clear APCA over the real pixels behind it (`PhotoSampler`); `LegibleText` solves text color (photo-aware: passthrough off-photo, e.g. the Lobby); buttons solve their brand-color opacity *as a scrim* with the same math (`OpacityGroup` keeps a row uniform); the hero timer solves its own polarity (`solveFreeText`) and sits below a display cutout.
- **Structural guard** — `OverlayLegibilityGuardTest` scans `screens/running/` and **fails the build** on raw `Text(`/`BasicText(`/`ClickableText(`/`drawText(` not routed through the system (or marked `// legible-exempt: why`), so on-photo text can't be added without the guarantee.
- **Server advisor** — `POST /api/background/advise` returns a cached Gemini "prior" (palette hue / polarity / mood) that only *nudges* the engine; it is **non-authoritative** (APCA still decides).

### AI Coach — Gemini Integration

`python/program_engine.py` handles Gemini API calls and interval program execution:
- **Gemini model**: `gemini-2.5-flash` via REST API with function calling
- **Tools**: `set_speed`, `set_incline`, `start_workout`, `stop_treadmill`, `pause/resume/skip`, `extend_interval`, `add_time`, `load_workout`, `query_workout_data`
- **Workout query**: `query_workout_data` gives Gemini a read-only SQL interface to an in-memory SQLite DB (`python/workout_db.py`) populated from workout history, saved workouts, run records, and the live active program. Gemini writes its own SQL to query interval structures, compare past runs, and give contextual coaching. Engine-level read-only enforcement via `set_authorizer()`.
- **ProgramState**: manages interval execution with 1s tick loop, pause/skip/extend support, encouragement milestones (25/50/75%)
- **Program time is real time**: `total_elapsed` always equals time actually run. Skip therefore truncates the interval being left to the time spent in it (never jumps the clock to the next planned boundary), so remaining intervals shift earlier and `total_duration` shrinks. The Ridgeline map draws boundaries straight from interval durations, so its milestones track the timer instead of drifting ahead of it.
- **GPX import**: `POST /api/gpx/upload` parses GPX routes into incline-based interval programs

### Persistence (SQLite)

All user data lives in a SQLite DB (`treadmill.db`, override with `TREADMILL_DB`) owned by
`python/db.py` (`TreadmillDB`). Tables: `profiles`, `runs`, `saved_workouts`,
`program_history`, `coach_messages`, `app_state`. **Everything is scoped per profile** —
the app is multi-user (see Profiles below). Legacy flat files (`program_history.json`,
`run_history.json`, `saved_workouts.json`, `user_profile.json`) are imported once on first
boot and renamed `*.migrated`; they are never written again.

### Program History

Recently generated/loaded programs go to the `program_history` table, capped at
`MAX_HISTORY` (20) per profile. Programs are deduplicated by name. Accessible via REST API
and shown as a horizontal scroll in the UI.

### Run History

Completed and in-progress sessions are persisted to the `runs` table (no insert-time cap;
`GET /api/runs` returns the most recent 200). Records are created once a session passes 5s
elapsed (as `end_reason: "in_progress"`), saved every 30 seconds with current metrics
(elapsed, distance, calories), and finalized when the session ends with the actual end
reason (`user_stop`, `program_complete`, `disconnect`). This ensures run data survives
server crashes. Records include `program_fingerprint` for matching runs to workouts.

### Saved Workouts

Users can save favorite programs from history to the `saved_workouts` table (uncapped) for
permanent access. Saved workouts persist independently of the rolling history window and
track usage stats (times used, last used). Accessible via REST API and shown in a "My
Workouts" section in the Kotlin/Android UI.

### Profiles

The app is multi-user. Each profile owns its runs, saved workouts, program history, coach
messages, and an optional avatar image (stored as a BLOB). A **guest mode** lets someone
run without creating a profile; guest data can be converted into a real profile afterwards.
Profile switching is blocked mid-session. The Android app opens on a profile picker, so the
`/api/profiles*` and `/api/profile/*` endpoints are required for the app to get past its
first screen.

### Auto Proxy/Emulate Mode

The C binary auto-detects mode transitions (no manual toggle needed):
- **Speed/incline command received** → auto-enables emulate mode
- **Console button press detected** (hmph/inc value change while emulating) → auto-switches to proxy mode

This logic lives in the C binary (not Python) so that mode transitions work even if the Python server crashes — the treadmill stays responsive to physical console buttons regardless of software state.

### Analysis Tools (offline)

- `cpp/captures/analyze_logic.py` — decodes logic analyzer CSVs with standard UART polarity
- `cpp/captures/decode_inverted.py` — decodes logic analyzer CSVs with inverted polarity detection

## Testing

```bash
# C++ unit tests (148 tests including DMA guard + config parsing, runs from cpp/)
make test

# Deploy to Pi, build, restart binary, run hardware integration tests
# Requires: Pi reachable at `rpi`, treadmill powered on
make test-pi

# Full pre-commit gate: local unit tests + Pi hardware tests
make test-all

# FTMS Rust unit tests (33 tests: protocol encoding/decoding + alive-signal logic)
cd rust/ftms && cargo test

# FTMS debug integration tests (19 tests, requires ftms-daemon + treadmill_io running on Pi)
cd rust/ftms && cargo test --test debug_integration -- --ignored --test-threads=1

# FTMS BLE integration tests (8 tests, requires hci1 USB dongle on Pi)
make test-ftms-ble   # or: ssh rpi 'sudo bash ~/treadmill/rust/ftms/tests/ble_integration.sh'

# HRM Rust unit tests (16 tests, HR parsing + config)
cd rust/hrm && cargo test

# HRM Python client tests (6 tests, mock daemon)
python3 -m pytest python/tests/test_hrm_client.py -v

# Python unit tests (mocked sleep, <1s)
python3 -m pytest python/tests/test_program_engine.py python/tests/test_server_integration.py python/tests/test_workout_db.py -v

# Python live integration tests (real asyncio.sleep, ~45s)
python3 -m pytest python/tests/test_live_program.py -v

# All non-hardware Python tests
python3 -m pytest python/tests -m "not hardware" -v
```

Other unit-tested modules not named above: `test_db.py` (SQLite persistence),
`test_profile_adversarial.py` (multi-profile isolation + guest mode),
`test_session.py` / `test_session_ws.py` (run records, heartbeat, WS session frames),
`test_background_advice.py`, `test_extract_intent.py`. The voice suite
(`test_voice_commands.py` + `voice_test_cases.py` + `generate_voice_audio.py`) needs a live
Gemini API key and is nondeterministic — exclude it with `-m "not hardware and not voice"`.

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
| `/api/program/skip` | POST | Skip to next interval (truncates the one being left to the time actually run) |
| `/api/program/prev` | POST | Go back to the previous interval |
| `/api/program/extend` | POST | Adjust current interval. Body: `{"seconds": <int>}` |
| `/api/program/adjust-duration` | POST | Adjust total duration of a manual program |
| `/api/program/quick-start` | POST | Build a single-interval manual program and start it immediately |
| `/api/session` | GET | Active workout session state (elapsed, distance, calories) |
| `/api/reset` | POST | Full reset: stop belt, clear program, zero session |

### History & Import
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/programs/history` | GET | List recent programs (max 20) |
| `/api/programs/history/{id}/load` | POST | Reload a saved program |
| `/api/programs/history/{id}/resume` | POST | Resume a history entry from its saved position |
| `/api/runs` | GET | List run records for the active profile (most recent 200) |
| `/api/gpx/upload` | POST | Upload GPX route file (multipart form) |

### Saved Workouts
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/workouts` | GET | List all saved workouts |
| `/api/workouts` | POST | Save a workout. Body: `{"history_id": "<id>"}` or `{"program": {...}, "source": "...", "prompt": "..."}` |
| `/api/workouts/{id}` | PUT | Rename a workout. Body: `{"name": "..."}` |
| `/api/workouts/{id}` | DELETE | Delete a saved workout |
| `/api/workouts/{id}/load` | POST | Load a saved workout into the program engine |

### Profiles
The Android app opens on a profile picker — these endpoints are required for it to reach the Lobby.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/profiles` | GET / POST | List profiles / create a profile |
| `/api/profiles/{id}` | PUT / DELETE | Rename or update / delete a profile |
| `/api/profiles/{id}/avatar` | POST / GET / DELETE | Upload, fetch, or clear a profile avatar image |
| `/api/profile/active` | GET | Active profile + `guest_mode` flag |
| `/api/profile/select` | POST | Switch active profile (rejected mid-session) |
| `/api/profile/guest` | POST | Enter guest mode |
| `/api/profile/guest/convert` | POST | Convert guest-session data into a real profile |
| `/api/user` | GET / PUT | Legacy shim: weight/vest of the active profile |

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
| `/api/tool` | POST | Generic tool execution (used by voice clients). Body: `{"name": "...", "args": {...}, "context": "..."}`. Forwards to `_exec_fn()`. |
| `/api/background/advise` | POST | Get cached Gemini overlay prior for a background. Body: `{"image_hash":"...","image_b64":"..."}` |

### Voice
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat/voice` | POST | Voice turn: transcribe audio, respond as the coach |
| `/api/tts` | POST | Gemini TTS speech synthesis |
| `/api/voice/prompt/{prompt_id}` | GET | Fetch a canned voice-injection prompt |
| `/api/voice/extract-intent` | POST | Recover a function call from Gemini Live "thinks aloud" text |

### Client Support
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Client config: ephemeral Gemini Live token, tool declarations |
| `/api/device-log` | POST | Accept debug logs from iOS/Android clients |
| `/api/log` | GET | Tail the `treadmill_io` log |

### WebSocket
| Endpoint | Description |
|----------|-------------|
| `/ws` | Real-time state stream. Broadcasts JSON messages with `type`: `"status"` (treadmill + profile state), `"program"` (interval program state), `"session"` (active workout metrics), `"kv"` (raw motor/console key-value pairs — the Debug screen's live feed), `"connection"` (treadmill_io connect/disconnect), `"hr"` (heart rate). |

## Code Review Standards

When reviewing or writing code in this project, enforce these principles:

### Postel's Law (Robustness Principle)
**Be conservative in what you send, be liberal in what you accept.**

Clients (Android, iOS) must tolerate unexpected data from the server: unknown fields, null where a value was expected, missing optional fields, extra fields added later. The server evolves faster than clients update, so clients that crash on unfamiliar input are bugs.

In practice:
- **Kotlin**: kotlinx.serialization uses `ignoreUnknownKeys`, `coerceInputValues` (null → default), `isLenient`, `explicitNulls = false`. All model fields that could ever be null from the server must be nullable with a default. See `AppModule.kt` Json config.
- **Swift (iOS)**: Don't assert response shapes. Optional properties with defaults; decode leniently. If a field might not exist, handle it.
- **Python (server)**: Validate at system boundaries (user input, API requests). Trust internal data structures. Send clean, well-typed JSON.

### Docs Stay Current
- **CLAUDE.md must reflect reality.** If you add a feature, endpoint, mode, or dependency, update this file. Stale docs are a bug.
- **README screenshots must reflect main.** When a change that alters UI shown in
  `docs/screenshots/` lands on main, recapture those screenshots (prefer the real
  tablet) and land the refreshed images too. A landing isn't complete while the
  README shows the old UI.
- Inline comments only where the "why" isn't obvious. Don't comment the "what."

### Tests Are Real
- **Two tiers required:** fast unit tests (mocked I/O, <1s) AND live integration tests (real `asyncio.sleep`, real timers, ~seconds).
- Unit tests verify logic in isolation. Live tests prove the system actually works end-to-end with real timing.
- Hardware tests (`@pytest.mark.hardware`) exist for Pi-only verification but aren't required to pass in CI.
- Every behavior-changing PR should have at least one test that would fail without the change.
- **Bug fix workflow: test first, then fix.** When fixing a bug, write a regression test that reproduces the failure *before* writing the fix. Run it to verify it fails, then apply the fix and confirm the test passes. Never fix a bug without a test that proves it was broken.

### DRY
- Constants live in one place (e.g., `MAX_SPEED_TENTHS` in `python/treadmill_client.py`, shared by C and Python).
- Don't duplicate logic between `_exec_fn()` and REST endpoints — they should share the same code path.
- If you see the same 3+ lines in two places, extract it.

### C++ Safety Rules

All C++ code in `cpp/` must follow these rules. The environment is resource-constrained (Raspberry Pi) and timing-critical (9600 baud serial).

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

**C++ binary** (`cpp/`): Transport layer only. This code must be:
- **Incredibly narrow in scope**: GPIO I/O, KV protocol parsing, proxy forwarding, emulation cycle. Nothing else.
- **Very fast**: bit-banged serial at 9600 baud with DMA waveforms. No allocations in hot paths, no blocking.
- **Safety-critical**: the 3-hour timeout, the zero-speed-on-emulate-start, auto proxy/emulate detection, and motor KV decoding (hmph/inc → `bus_speed`/`bus_incline` in status events) live here because they must work even if Python is dead.
- No application logic, no knowledge of programs/workouts/AI. It just moves bytes and manages modes.
- Note: The C++ binary accepts incline 0-99 (hardware range). The application layer (Python/Gemini) enforces 0-15 for safety.

**FTMS daemon** (`rust/ftms/`): BLE transport layer only. Reads treadmill state from the Unix socket, encodes it per the FTMS spec, and advertises over Bluetooth. Control Point writes are converted back to socket commands. No application logic, no knowledge of programs/workouts/AI.

**HRM daemon** (`rust/hrm/`): BLE client layer only. Scans for heart rate monitors, connects, reads HR notifications, and serves data on a Unix socket. No application logic, no knowledge of programs/workouts/AI.

**Python clients** (`python/treadmill_client.py`, `python/hrm_client.py`): Thin IPC wrappers to daemon sockets. No business logic.

**Persistence** (`python/db.py`): `TreadmillDB` — the SQLite store (`treadmill.db`) behind all user data: profiles, runs, saved workouts, program history, coach messages, avatar BLOBs, plus one-time JSON migration. Profile-scoped. No HTTP, no business logic.

**Workout DB** (`python/workout_db.py`): In-memory SQLite read-only query interface for Gemini. Populated from `TreadmillDB` (history, workouts, runs) for the active profile, plus the live active program. No HTTP, no business logic.

**Program engine** (`python/program_engine.py`): Interval execution + Gemini API. No HTTP, no GPIO, no imports from server.

**Server** (`python/server.py`): **All shared business logic lives here.** This is the single source of truth for:
- State management (speed, incline, mode, program)
- Endpoint validation and clamping
- Coordinating between program engine and treadmill client
- Multiple clients (Android app, FTMS daemon, future CLI, future watch app) all connect through the same socket — logic must not leak into any single client.

**UI** (`kotlin/` — Android is the primary UI; `ios/` Treddy exists but is secondary): Display layer only. Principles:
- **No business logic.** All decisions happen server-side. The UI calls API endpoints and renders what comes back.
- **Safety first.** Stop button always visible when belt is moving. Emergency stop is one tap.
- **Minimal by default.** Show only what's needed right now. Debug info (mode badge, raw state) hidden behind triple-tap.
- **Beautiful and peaceful.** Warm muted palette, subtle texture, organic curves. No neon, no visual noise.
- **Progressive disclosure.** Essential info (speed, time, current interval) is prominent. Settings, history, and debug are tucked away but accessible.
- **Mobile/tablet first.** Touch targets 44px+, no hover-dependent interactions, responsive layout, haptic feedback.
- **No external CDN dependencies.** The app runs on a treadmill that may not have internet. All assets (fonts, scripts, styles) must be bundled with the app. Fonts live in `kotlin/app/src/main/res/font/` (Android) and the Xcode asset bundle (iOS).


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Working Principles (earned the hard way — see git history)

**Reuse before you build.** Check whether the platform already does it. A
hand-rolled flash record store produced two real bugs in an hour (a 4 KB sector
erase destroying the 15 records packed into it; an erased 0xFFFFFFFF sequence
sorting as the newest record) — LittleFS would have had neither. If you find
yourself reimplementing power-loss safety, a filesystem, an HTTP server or an
mDNS responder, stop and find the component.

**Read before you probe.** Signatures come from generated bindings, not C
headers or memory. Build-system questions come from the crate's documentation
read ONCE. Two blockers were filed as "impossible" and both turned out to be a
single line found by reading BUILD-OPTIONS.md: TLS needed one Kconfig symbol,
mDNS needed `extra_components` (which adds) rather than `esp_idf_components`
(which is an exclusive whitelist and trims). Each probe costs a 60-180s
rebuild; a doc read costs one minute.

**Guard iteration speed actively.** Measure what a gate costs and cut what does
not earn it. Do NOT run the full sweep after every edit — run the gates the
change can actually reach. Feature-gated work needs only its feature's gates.
The full sweep belongs at commit boundaries; the 5-minute soak belongs behind
DEEP=1. When something gets slow, interrogate the cause rather than tolerating
it: "CPU starvation" on a 20-core machine turned out to be a TOCTOU in port
allocation and a shared flash image two sessions raced on.

**An intermittent is worse than a hard failure.** It costs an investigation
every time and the investigations land on wrong theories. Never add a retry, a
sleep, or a loosened bound to make one pass — find the mechanism. Silence is
the worst failure mode of all: a command ring that dropped silently looked
exactly like a wedged device.

**Simplify toward less code.** Deleted code has no bugs. Prefer the thin device
over the clever one, the fixed budget over the growing pool, the single path to
the belt over two paths that agree today.

**Right-size the model.** Research, inventory and mechanical edits go to a cheap
model; only hard design and adversarial review need the expensive one.

## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
