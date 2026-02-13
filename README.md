# Precor 9.3x Treadmill Serial Protocol Tools

Reverse-engineering and control tools for the Precor 9.31 (and similar) treadmill serial bus between the console (Upper PCA) and motor controller (Lower PCA).

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

The Pi reads and writes RS-485 using pigpio's bit-banged serial with `invert=1`.

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
sudo apt install pigpio
sudo pigpiod        # must be running
pip install pigpio
pip install fastapi uvicorn  # for web UI only
```

## Tools

| File              | Description                                              |
|-------------------|----------------------------------------------------------|
| `dual_monitor.py` | Side-by-side curses UI with proxy and controller emulation |
| `server.py`       | FastAPI web server with WebSocket for phone control       |
| `listen.py`       | Simple KV listener with changes/unique filtering          |
| `gpio_serial.py`  | Raw GPIO serial reader for debugging                      |
| `gpio_pins.py`    | Shared library: GPIO config, KV parsing, serial I/O      |

### Quick Start

```bash
sudo pigpiod

# Main tool: dual monitor with side-by-side view
python3 dual_monitor.py

# Simple listener
python3 listen.py                    # console commands (default)
python3 listen.py motor_read         # motor responses
python3 listen.py --changes          # only show value changes

# Web UI (phone-friendly)
python3 server.py
# Open http://<pi-ip>:8000
```

### dual_monitor.py

The primary tool. Left pane shows console commands (or emulated commands), right pane shows motor responses.

```
 Console→Motor (GPIO 27→22)  [PROXY]│  Motor responses (GPIO 17)
────────────────────────────────────┼──────────────────────────────
   0.1  inc      0                  │   0.1  inc      0
   0.1  hmph     0                  │   0.2  belt     12A4
   0.1  amps     23                 │   0.2  type     6
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

## License

MIT
