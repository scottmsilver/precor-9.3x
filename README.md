# Precor 9.3x Treadmill — Reverse Engineering & AI Control

A Raspberry Pi intercepts the serial bus on a ~2005 Precor 9.31 treadmill, giving it phone control, interval programs, voice commands, and an AI coach powered by Gemini. A Bluetooth FTMS daemon lets fitness apps like Zwift see it as a modern smart treadmill.

---

## 1. The Hardware

### Two Computers, One Cable

The treadmill has two independent circuit boards:

- **Console (Upper PCA)** — the display and buttons. It decides what speed and incline to request.
- **Motor controller (Lower PCA)** — drives the belt motor and lift motor. It reports back sensor readings.

They talk over a single 8P8C cable (the same connector as an Ethernet cable, but this is not Ethernet).

### Cable Pinout

| Pin | Function | Notes |
|-----|----------|-------|
| 1 | Ground | |
| 2 | VCC (~8V) | Power from motor controller to console |
| 3 | Motor → Console | 3.3V serial, RS-485 inverted polarity |
| 4 | Unknown | Possibly clock — unconfirmed |
| 5 | Safety interlock | Connected to the safety strap magnet; open circuit = motor stops |
| 6 | Console → Motor | 3.3V serial, RS-485 inverted polarity |
| 7 | Ground | |
| 8 | VCC (~8V) | |

Pins 3 and 6 are the interesting ones — they carry the serial protocol.

### The Protocol

Both directions use 9600 baud, 8N1 serial. Messages are plain ASCII text wrapped in square brackets.

#### Wire Format

Every message is a bracket-delimited key-value pair. On the wire, each byte is standard ASCII:

```
Set command:   [  k  e  y  :  v  a  l  u  e  ]  0xFF
               5B 6B 65 79 3A 76 61 6C 75 65 5D FF

Query command: [  k  e  y  ]  0xFF
               5B 6B 65 79 5D FF
```

A **set command** has a colon between key and value: `[inc:3]`. A **query** has no colon, just the key: `[amps]`. The `0xFF` byte terminates each message on pin 6 (console → motor). Pin 3 (motor → console) uses the same bracket format but omits the `0xFF`.

#### Console → Motor (pin 6)

The console sends a repeating cycle of 14 keys, grouped into 5 bursts with ~100ms pauses between them. Here's one full cycle as it appears on the wire, at 2.5 mph and incline 3:

```
Burst 1:  [inc:3] FF  [hmph:FA] FF              ← incline + speed
          ~100ms pause
Burst 2:  [amps] FF  [err] FF  [belt] FF        ← sensor queries
          ~100ms pause
Burst 3:  [vbus] FF  [lift] FF  [lfts] FF  [lftg] FF
          ~100ms pause
Burst 4:  [part:6] FF  [ver] FF  [type] FF      ← identity queries
          ~100ms pause
Burst 5:  [diag:0] FF  [loop:5550] FF           ← diagnostics
```

Then the whole cycle repeats. The console sends this continuously, roughly once per second.

Four keys always carry a fixed value (`inc`, `hmph`, `part`, `diag`, `loop`). The rest are bare queries — the console is asking the motor to report back.

#### Motor → Console (pin 3)

The motor responds to queries with the same bracket format, no `0xFF`:

```
[belt:14] [inc:0] [hmph:69] [amps:FF] [ver:19A] [lift:28] [type:20]
```

Responses arrive interleaved with the console's bursts.

#### Speed Encoding

The `hmph` key encodes speed as **mph × 100, in uppercase hex**:

| mph | × 100 | Hex | Wire bytes |
|-----|--------|-----|------------|
| 0 (stopped) | 0 | `0` | `[hmph:0]` → `5B 68 6D 70 68 3A 30 5D FF` |
| 1.2 | 120 | `78` | `[hmph:78]` → `5B 68 6D 70 68 3A 37 38 5D FF` |
| 2.5 | 250 | `FA` | `[hmph:FA]` → `5B 68 6D 70 68 3A 46 41 5D FF` |
| 6.0 | 600 | `258` | `[hmph:258]` → `5B 68 6D 70 68 3A 32 35 38 5D FF` |

Incline is a plain decimal integer: `[inc:3]` means incline 3.

### RS-485 Polarity — The Gotcha

These serial lines use RS-485 signaling, which idles LOW. Standard UART idles HIGH. If you connect a normal TTL serial adapter, you'll see what looks like binary garbage — it's actually the KV text with every bit flipped, and byte boundaries shifted because the start/stop bits are inverted too.

The full forensic investigation is in [`RS485_DISCOVERY.md`](RS485_DISCOVERY.md). The short version: we spent days analyzing a "binary protocol" that turned out to be regular ASCII read with the wrong polarity.

---

## 2. Tapping In

### What You Need

- Raspberry Pi (any model with GPIO)
- RJ45 pass-through breakout board ([example](https://www.amazon.com/dp/B0CQKBPGB6))
- Jumper wires

### Three Connections

The Pi connects to three points on the cable:

```
Console ──pin 6──▶ [GPIO 27] Pi [GPIO 22] ──pin 6──▶ Motor
                                Motor ──pin 3──▶ [GPIO 17] Pi (tap)
```

| Connection | Cable Pin | GPIO | Physical Pin | What It Does |
|------------|-----------|------|--------------|--------------|
| Console read | Pin 6 (console side) | 27 | 13 | Reads commands from console |
| Motor write | Pin 6 (motor side) | 22 | 15 | Sends commands to motor |
| Motor read | Pin 3 | 17 | 11 | Reads responses from motor |

Pin assignments are configured in [`gpio.json`](gpio.json) — the C binary reads this at startup.

### Why Cut Pin 6

Pin 6 (Console → Motor) is **cut** — the Pi sits in the middle. This lets us either forward the console's commands unchanged (proxy mode) or replace them entirely with our own (emulate mode).

Pin 3 (Motor → Console) is only **tapped** — the console still receives motor responses directly. We never need to fake motor responses, so a passive tap is enough.

If we only tapped pin 6, we could listen but not control anything. Cutting gives us the ability to intercept and substitute.

### Debugging Tools

If you're investigating the protocol or something isn't working:

- **Logic analyzer** + [`analyze_logic.py`](src/captures/analyze_logic.py) / [`decode_inverted.py`](src/captures/decode_inverted.py) — decode captured CSV traces. `decode_inverted.py` handles the RS-485 polarity inversion automatically. Raw captures and parsers live in [`src/captures/`](src/captures/).
- **`dual_monitor.py`** — live curses TUI showing both channels side-by-side. Console commands on the left, motor responses on the right.
- **`listen.py`** — simple CLI listener. Use `--changes` to only show value changes, `--source motor` to filter by direction.

---

## 3. What We Built

### The Goal

Turn a 20-year-old dumb treadmill into a smart one — phone control, interval programs, AI coaching, voice commands, Bluetooth fitness app support — without modifying the treadmill itself.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│            Web UI (React + Vite + TypeScript)        │
│               Framer Motion animations               │
│    Routes: Lobby, Running, Debug — display only      │
├────────┬──────────────────────────────────────┬──────┤
│  REST  │          WebSocket (real-time)       │ Live │
│        │  status, session, program, kv        │ Voice│
└────┬───┴──────────────────┬───────────────────┴──┬───┘
     │                      │                      │
     │    ┌─────────────────┴──────────────────┐   │
     │    │         server.py (FastAPI)         │   │
     │    │  Business logic: sessions, programs │   │
     │    │  AI chat, speed/incline, GPX import │   │
     │    ├──────────┬───────────┬──────────────┤   │
     │    │workout_  │program_   │ treadmill_   │   │
     │    │session.py│engine.py  │ client.py    │   │
     │    │Lifecycle │Intervals  │ IPC wrapper  │   │
     │    │dist/vert │+ Gemini   │              │   │
     │    └──────────┘───────────┘──────┬───────┘   │
     │                                  │ Unix sock  │
     │           ┌──────────────────────┴────────┐   │
     │           │   treadmill_io (C++20, root)  │   │
     │           │ GPIO serial, proxy, emulate   │   │
     │           │ Watchdog, 3h timeout, safety  │   │
     │           └──────────────┬────────────────┘   │
     │                          │                    │
     │           ┌──────────────┴────────────────┐   │
     │           │   ftms-daemon (Rust, root)    │   │
     │           │ Bluetooth FTMS service for    │   │ Gemini Live
     │           │ Zwift, Peloton, fitness apps  │   │ (browser →
     │           └───────────────────────────────┘   │  Google WS)
     └───────────────────────────────────────────────┘
```

### Why These Layers

**C++ binary** ([`src/`](src/)) — The safety-critical layer. Handles bit-banged serial I/O at 9600 baud and nothing else. Safety features live here so they work even if the server crashes:

- **3-hour timeout:** If no speed/incline change for 3 hours, belt stops.
- **IPC watchdog:** If the Python server disconnects for 4 seconds, belt stops.
- **Auto proxy/emulate:** When someone presses a physical console button during emulate mode, it instantly switches to proxy. Physical controls always win.
- **Zero-on-start:** Entering emulate mode always zeros speed and incline.

C++20 compiled with `-fno-exceptions -fno-rtti`. Hot paths (serial read/write) are zero-allocation — stack buffers only.

**Python client** ([`treadmill_client.py`](treadmill_client.py)) — Thin wrapper over the Unix socket. Auto-reconnects with exponential backoff. Sends heartbeats on a background thread.

**Workout session** ([`workout_session.py`](workout_session.py)) — Owns the session lifecycle: start, end, pause, resume. Tracks elapsed time (wall clock minus pauses), distance (cumulative from speed ticks), and vertical feet (from incline). Owns the ProgramState instance. No HTTP, no GPIO.

**Program engine** ([`program_engine.py`](program_engine.py)) — Interval program execution and Gemini AI calls. 1-second tick loop, interval progress tracking, encouragement at 25/50/75% milestones. No HTTP, no GPIO.

**Server** ([`server.py`](server.py)) — FastAPI on port 8000. Single source of truth for all application state. Coordinates workout sessions, program engine, and treadmill client. Multiple clients (web UI, future watch app) all go through the same server.

**FTMS daemon** ([`ftms/`](ftms/)) — Rust binary that exposes the treadmill as a Bluetooth FTMS (Fitness Machine Service) device. Connects to the Python server via REST, advertises BLE GATT characteristics for speed, incline, distance, and elapsed time. Fitness apps like Zwift and Peloton see it as a standard smart treadmill.

**Web UI** ([`ui/`](ui/)) — React 19 + TypeScript + Vite. Display layer only — every decision happens server-side. Touch-first, designed for a phone or tablet mounted on the treadmill console. Warm dark palette with Quicksand font. Framer Motion for layout animations and crossfade transitions.

### Proxy vs Emulate

The two operating modes:

- **Proxy mode** — the Pi forwards console commands to the motor unchanged. You can monitor everything, but the console stays in control.
- **Emulate mode** — the Pi replaces the console entirely, sending its own speed/incline commands. This is how phone control and AI programs work.

Transitions are automatic. Sending a speed or incline command from the server switches to emulate. Pressing a button on the physical console switches back to proxy. No manual toggle needed (though one exists in the debug panel).

### AI Coach

The AI coach uses Gemini 2.5 Flash with function calling. Chat via text or voice, and it directly controls the treadmill.

**Available tools** (Gemini function calls):
- `set_speed` / `set_incline` — direct control
- `start_workout` — describe what you want ("30 minute hill workout") and it generates a structured interval program
- `stop_treadmill` / `pause_program` / `resume_program` / `skip_interval`
- `extend_interval` / `add_time` — modify a running program

**GPX import:** Upload a GPX route file and the server converts elevation changes into an incline-based interval program.

Programs are saved to `program_history.json` (last 10, deduplicated by name) and shown in the UI for quick reload.

### Voice

Two voice modes:

**Gemini Live (primary)** — The browser opens a direct WebSocket to `generativelanguage.googleapis.com` using the `BidiGenerateContent` streaming API (`gemini-2.5-flash-native-audio-latest`). Audio goes straight from the mic to Gemini and back, with function calling for treadmill control. The server provides ephemeral API tokens (30-min expiry) via `/api/config`.

**Text fallback** — Gemini Live sometimes "thinks aloud" (narrates intent as text instead of making a tool call). When detected, the client sends the text to `/api/voice/extract-intent`, which uses Gemini Flash to parse it into function calls and execute them.

Voice requires HTTPS or Chrome's `--unsafely-treat-insecure-origin-as-secure` flag for `getUserMedia`. The server auto-detects `cert.pem`/`key.pem` and switches to HTTPS.

---

## Quick Start

### Prerequisites

```bash
# On the Pi
sudo apt install libpigpio-dev g++
pip install google-genai fastapi uvicorn python-multipart gpxpy
```

For the AI coach, create a `.gemini_key` file with your Gemini API key.

### Build and Run

```bash
make                    # Build C++ binary
sudo ./treadmill_io     # Start I/O (must be root, pigpiod must NOT be running)
python3 server.py       # Start server — http://<pi-ip>:8000
```

### Deploy to Pi

The deploy script handles everything — builds, copies files, manages systemd services:

```bash
./deploy.sh             # Full deploy: C binary, Python, UI, venv, services
./deploy.sh ui          # UI only (quick iteration)
```

This deploys to `~/treadmill/` on the Pi and manages three systemd services:
- `treadmill_io.service` — C binary (root)
- `treadmill-server.service` — Python server (user)
- `ftms.service` — Bluetooth daemon (root, optional)

### Build the UI

```bash
cd ui && npm install && npx vite build   # Outputs to static/
```

### Other Tools

```bash
python3 dual_monitor.py              # Curses TUI — both channels live
python3 listen.py                    # Simple CLI listener
python3 listen.py --changes          # Only show value changes
python3 listen.py --source motor     # Motor responses only
```

## Testing

```bash
# C++ unit tests (local, no hardware, <2s)
make test

# Deploy to Pi + hardware integration tests
make test-pi

# Both C++ tiers
make test-all

# Python tests (mocked, <1s)
python3 -m pytest tests/test_program_engine.py tests/test_server_integration.py \
                  tests/test_workout_session.py tests/test_session.py -v

# Python live integration tests (real timing, ~45s)
python3 -m pytest tests/test_live_program.py -v

# FTMS Rust tests
make test-ftms
```

## API Reference

### Control

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | `/api/status` | — | Speed, incline, mode, motor KV data |
| GET | `/api/session` | — | Elapsed time, distance, vertical feet |
| GET | `/api/config` | — | Client config (ephemeral Gemini token for Live voice) |
| POST | `/api/speed` | `{"value": 3.5}` | Set belt speed (mph) |
| POST | `/api/incline` | `{"value": 5}` | Set incline grade (0-15) |
| POST | `/api/reset` | — | Full reset: stop belt, zero speed/incline, end session |
| POST | `/api/emulate` | `{"enabled": true}` | Toggle emulate mode (debug) |
| POST | `/api/proxy` | `{"enabled": true}` | Toggle proxy mode (debug) |

### Programs

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | `/api/program` | — | Current program state |
| POST | `/api/program/generate` | `{"prompt": "..."}` | Generate program via Gemini |
| POST | `/api/program/quick-start` | `{"speed": 3.0, "incline": 0}` | Create manual program and start |
| POST | `/api/program/start` | — | Start loaded program |
| POST | `/api/program/stop` | — | Stop program, zero speed/incline |
| POST | `/api/program/pause` | — | Toggle pause/resume |
| POST | `/api/program/skip` | — | Skip to next interval |
| POST | `/api/program/prev` | — | Go to previous interval |
| POST | `/api/program/extend` | `{"seconds": 60}` | Adjust current interval duration |
| POST | `/api/program/adjust-duration` | `{"delta_seconds": 300}` | Adjust manual program total duration |
| GET | `/api/programs/history` | — | Recent programs (max 10) |
| POST | `/api/programs/history/{id}/load` | — | Reload a saved program |
| POST | `/api/gpx/upload` | multipart file | Upload GPX route file |

### Chat & Voice

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| POST | `/api/chat` | `{"message": "..."}` | Text chat with AI coach |
| POST | `/api/chat/voice` | `{"audio": "base64...", "mime_type": "..."}` | Voice transcribe + respond |
| POST | `/api/tts` | `{"text": "...", "voice": "Kore"}` | Text-to-speech via Gemini |
| POST | `/api/voice/extract-intent` | `{"text": "..."}` | Extract function calls from voice text |

### WebSocket

| Endpoint | Messages |
|----------|----------|
| `/ws` | `type: "status"` — speed, incline, mode |
|        | `type: "session"` — elapsed, distance, vert feet |
|        | `type: "program"` — interval progress, encouragement |
|        | `type: "connection"` — treadmill_io connected/disconnected |
|        | `type: "kv"` — raw serial bus key-value messages |

## License

MIT
