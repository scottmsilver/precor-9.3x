# Precor 9.3x Treadmill Serial Protocol Tools

Reverse-engineering and control tools for the Precor 9.31 (and similar) treadmill serial bus between the console (Upper PCA) and motor controller (Lower PCA).

## Architecture

A C binary (`treadmill_io`) handles all GPIO I/O directly via libpigpio, cutting out the pigpiod daemon for lower latency. Python tools connect via a Unix domain socket for monitoring and control.

```
┌──────────────────────────────────┐
│  treadmill_io (C, runs as root)  │
│  Links libpigpio directly        │
│                                  │
│  GPIO 27 read ──┐                │
│                 ├→ proxy → GPIO 22 write
│  GPIO 17 read   │                │
│  Emulate cycle ─┘                │
│  Unix socket server              │
└──────────┬───────────────────────┘
           │ /tmp/treadmill_io.sock
┌──────────┴───────────────────────┐
│  Python clients                  │
│  dual_monitor.py — curses TUI    │
│  server.py — FastAPI/WebSocket   │
│  listen.py — simple CLI          │
└──────────────────────────────────┘
```

## Hardware Setup

The console and motor communicate over a 6-pin cable at 9600 baud, 8N1, using RS-485 signaling (idle LOW, inverted polarity). Both pins carry the same KV text protocol:

- **Pin 6** (Controller to Motor) — `[key:value]` commands, 0xFF delimited
- **Pin 3** (Motor to Controller) — `[key:value]` responses

Pin 6 is **cut** and wired through the Raspberry Pi's GPIO, allowing it to intercept and proxy (or replace) controller commands. Pin 3 is **tapped** passively.

```
Console ──pin6──> [GPIO 27] Pi [GPIO 22] ──pin6──> Motor
                               Motor ──pin3──> [GPIO 17] Pi (tap)
```

GPIO pin assignments are configured in `gpio.json`:

| Logical Name    | GPIO | Physical Pin | Role                              |
|-----------------|------|--------------|-----------------------------------|
| `console_read`  | 27   | 13           | Reads KV commands from console    |
| `motor_write`   | 22   | 15           | Writes KV commands to motor       |
| `motor_read`    | 17   | 11           | Reads KV responses from motor     |

## Protocol

### KV Text Protocol

Both directions use `[key:value]` text framing. The console sends a repeating 14-key cycle to the motor, in bursts with ~100ms gaps:

```
inc, hmph, amps, err, belt, vbus, lift, lfts, lftg, part, ver, type, diag, loop
```

Commands with values: `[key:value]\xff` — Commands without: `[key]\xff`

The motor responds with `[key:value]` (no 0xFF delimiter).

### Speed Encoding

Speed is sent via the `hmph` key as mph x 100, in uppercase hex:

| Speed  | hmph value |
|--------|-----------|
| 1.2 mph | `78`     |
| 1.5 mph | `96`     |
| 2.5 mph | `FA`     |

## Prerequisites

```bash
sudo apt install pigpio    # libpigpio for C binary
pip install fastapi uvicorn  # for web UI only
```

## Building

```bash
make                # builds treadmill_io C binary
```

## Quick Start

```bash
# 1. Start the C I/O binary (must be root for GPIO access)
sudo ./treadmill_io

# 2. Run any Python client (no root needed)
python3 dual_monitor.py          # curses TUI
python3 server.py                # web UI at http://<pi-ip>:8000
python3 listen.py                # simple CLI listener
python3 listen.py --source motor # motor responses only
python3 listen.py --changes      # only show value changes
```

**Note:** `pigpiod` must NOT be running — `treadmill_io` links libpigpio directly and they conflict.

## Files

| File                | Description                                              |
|---------------------|----------------------------------------------------------|
| `treadmill_io.c`    | C binary: GPIO I/O, KV parsing, proxy, emulate, IPC     |
| `Makefile`          | Builds `treadmill_io` with `-lpigpio -lrt -pthread`      |
| `treadmill_client.py` | Python client library for the C binary (Unix socket)   |
| `dual_monitor.py`   | Side-by-side curses TUI with proxy and emulation         |
| `server.py`         | FastAPI web server with WebSocket for phone control      |
| `listen.py`         | Simple KV listener with source/changes/unique filtering  |
| `gpio.json`         | GPIO pin assignments (read by `treadmill_io` at startup) |
| `static/index.html` | Mobile-responsive web UI (Alpine.js)                     |

### Modes

- **Proxy mode** (default) — forwards intercepted console commands to the motor unchanged
- **Emulate mode** — replaces the console entirely, sending synthesized KV commands with adjustable speed/incline

Proxy and emulate are mutually exclusive.

### IPC Protocol

Python tools communicate with `treadmill_io` via newline-delimited JSON over `/tmp/treadmill_io.sock`.

**C → Python:**
```json
{"type":"kv","ts":1.23,"source":"console","key":"hmph","value":"78"}
{"type":"kv","ts":1.23,"source":"motor","key":"belt","value":"14"}
{"type":"status","proxy":true,"emulate":false,"emu_speed":0,"emu_incline":0}
```

**Python → C:**
```json
{"cmd":"proxy","enabled":true}
{"cmd":"emulate","enabled":true}
{"cmd":"speed","value":1.2}
{"cmd":"incline","value":5}
{"cmd":"status"}
```

### dual_monitor.py

The primary tool. Left pane shows console commands (or emulated commands), right pane shows motor responses.

```
 Console→Motor (via treadmill_io) [PROXY]│  Motor responses
─────────────────────────────────────────┼──────────────────────────────
   0.1  inc      0                       │   0.1  inc      0
   0.1  hmph     0                       │   0.2  belt     12A4
   0.1  amps     23                      │   0.2  type     6
```

Key bindings:

| Key       | Action                                        |
|-----------|-----------------------------------------------|
| `e`       | Toggle emulate mode (replace controller)      |
| `+` / `-` | Adjust speed by 0.5 mph (emulate mode)       |
| `]` / `[` | Adjust incline (emulate mode)                 |
| `p`       | Toggle proxy (forward controller to motor)    |
| `f`       | Toggle follow / pause scrolling               |
| `c`       | Show only changed values                      |
| `u`       | Show only unique values                       |
| `j` / `k` | Scroll down / up                              |
| `q`       | Quit                                          |

### Analysis Tools (offline)

- `analyze_logic.py` — decodes logic analyzer CSVs with standard UART polarity
- `decode_inverted.py` — decodes logic analyzer CSVs with inverted polarity detection

## License

MIT
