# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reverse-engineering and control toolkit for the Precor 9.31 treadmill serial bus. A Raspberry Pi intercepts the RS-485 serial communication between the console (Upper PCA) and motor controller (Lower PCA), enabling monitoring, proxying, and emulation of the controller.

## Deployment

The Raspberry Pi connected to the treadmill is at host `rpi`. Use `ssh rpi` to access it, and `scp` to copy files over. All tools must be run on the Pi (they need GPIO access).

```bash
# On the Pi:
make                           # build C binary
sudo ./treadmill_io            # start GPIO I/O (must be root, pigpiod must NOT be running)
python3 dual_monitor.py        # Primary TUI (curses, side-by-side panes)
python3 listen.py              # Simple KV listener (--changes, --unique flags)
python3 server.py              # FastAPI web server on port 8000
```

## Dependencies

- `pigpio` (system package, libpigpio) — linked by `treadmill_io` for GPIO access
- `fastapi`, `uvicorn` — web server (server.py only)
- Build: `make` (gcc, libpigpio-dev)

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

### C Binary — `treadmill_io`

All GPIO I/O is handled by a C binary that links libpigpio directly (no daemon). It reads pin assignments from `gpio.json`, handles KV parsing, proxy forwarding, and emulation, and serves data to Python clients over a Unix domain socket (`/tmp/treadmill_io.sock`). See `treadmill_client.py` for the Python IPC client library.

### Protocol

Both directions use `[key:value]` text framing at 9600 baud, 8N1.

- **Console→Motor** (pin 6): `[key:value]\xff` or `[key]\xff`, repeating 14-key cycle in 5 bursts
- **Motor→Console** (pin 3): `[key:value]` responses (no `\xff` delimiter)
- **Speed encoding**: `hmph` key = mph × 100 in uppercase hex (e.g., 1.2 mph = `78`)
- **14-key cycle**: `inc, hmph, amps, err, belt, vbus, lift, lfts, lftg, part, ver, type, diag, loop`

### Application Modes

`dual_monitor.py` and `server.py` share the same mode logic:
- **Proxy mode** — forwards intercepted console commands to the motor unchanged
- **Emulate mode** — replaces the console entirely, sending synthesized KV commands with adjustable speed/incline
- Proxy and emulate are mutually exclusive (toggling one disables the other)

### Web UI

`server.py` serves `static/index.html` — a mobile-responsive Alpine.js interface with WebSocket for real-time KV data streaming and REST endpoints for speed/incline/mode control.

### Analysis Tools (offline)

- `analyze_logic.py` — decodes logic analyzer CSVs with standard UART polarity
- `decode_inverted.py` — decodes logic analyzer CSVs with inverted polarity detection
