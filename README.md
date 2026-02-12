# Precor 9.3x Treadmill Serial Protocol Tools

Reverse-engineering and control tools for the Precor 9.31 (and similar) treadmill serial bus between the console (Upper PCA) and motor controller (Lower PCA).

## Hardware Setup

The console and motor communicate over a 6-pin cable at 9600 baud, 8N1, 5V TTL. Two pins carry data:

- **Pin 6** (Controller to Motor) — text-based `[key:value]` protocol, 0xFF delimited
- **Pin 3** (Motor to Controller) — binary `R...E` frame protocol

### Wiring

Pin 6 is **cut** and connected through two RS485 USB adapters, allowing the Raspberry Pi to intercept and proxy (or replace) controller commands. Pin 3 is **tapped** passively with an FTDI TTL adapter.

```
Console ──pin6──> [ACM0] Pi [ACM1] ──pin6──> Motor
                                   Motor ──pin3──> [USB0] Pi (tap)
```

Port mappings are configured in `ports.json`:

| Logical Name    | Device        | Role                                        |
|-----------------|---------------|---------------------------------------------|
| `controller_tx` | `/dev/ttyACM0` | Reads KV commands from the console          |
| `motor_rx`      | `/dev/ttyACM1` | Writes KV commands to the motor (proxy)     |
| `motor_tx`      | `/dev/ttyUSB0` | Reads binary responses from the motor (tap) |

## Protocols

### Pin 6 — KV Text Protocol

The console sends a repeating 15-key cycle to the motor. Each command is `[key]` or `[key:value]` followed by `0xFF`. Keys are sent in bursts of 3-5 with ~100ms between bursts.

Cycle: `inc, hmph, mph, amps, err, belt, vbus, lift, lfts, lftg, part, ver, type, diag, loop`

### Pin 3 — Binary Frame Protocol

The motor responds with binary frames:

```
52 [type] [payload...] 45 00
|         |            |
|         |            +-- Frame end (0x45 0x00, or 0x45 followed by 0x52)
|         +-- Payload bytes (often contain 0x8B separator + base-16 digits)
|
+-- Frame start (0x52)
```

Note: Pin 6 frames end with `45 01`; pin 3 frames end with `45 00`.

#### Known Frame Types

| Type | Name     | Correlation | Description                |
|------|----------|-------------|----------------------------|
| 0x2A | SET_SPD  | —           | Speed setting              |
| 0x4B | SET_INC  | —           | Incline setting            |
| 0x4F | DISP1    | —           | Display data               |
| 0x51 | DISP2    | —           | Display data               |
| 0x52 | INC_R    | `inc` 70%   | Incline response           |
| 0x54 | DIAG_R   | `diag` 83%  | Diagnostic response        |
| 0x49 | BELT_R   | `belt` 75%  | Belt response              |
| 0x22 | MPH_R    | `mph` 100%  | Speed response             |
| 0x12 | HMPH_R   | `hmph` 71%  | Half-mph response          |
| 0xAA | LIFT_R   | `lift` 55%  | Lift response              |
| 0xD4 | TYPE_R   | `type` 61%  | Type response              |
| 0x4D | PART_R   | `part` 39%  | Part response              |

#### Custom Base-16 Digit Set

Speed and incline values use a non-standard base-16 encoding:

```
Value:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
Byte:  9F   9D   9B   99   97   95   93   91   8F   8D   7D   7B   79   77   75   73
```

## Tools

| File              | Description                                              |
|-------------------|----------------------------------------------------------|
| `dual_monitor.py` | Side-by-side curses UI: KV (pin 6) and binary (pin 3) with proxy and controller emulation |
| `monitor.py`      | Single-port curses packet monitor with recording         |
| `emulate.py`      | Console emulator — replays captured binary heartbeat cycle to control motor directly |
| `sniff.py`        | Packet sniffer with unique/all/parsed/graph modes        |
| `send_cmd.py`     | One-shot command sender (speed, incline, raw bytes)      |
| `listen.py`       | Serial listener with auto-detect, changes, and unique filtering |
| `protocol.py`     | Shared protocol library (encoding, decoding, constants)  |
| `ports.py`        | Port configuration helper (resolves logical names from `ports.json`) |

### Quick Start

```bash
pip install pyserial

# Main tool: dual monitor with side-by-side view
python3 dual_monitor.py

# Emulate the controller (talk directly to the motor)
#   Press 'e' in dual_monitor to enter emulate mode, or:
python3 emulate.py --speed 2.0

# Sniff unique packets for 30 seconds
python3 sniff.py

# Send a single speed command
python3 send_cmd.py speed 3.5
```

### dual_monitor.py

The primary tool. Shows KV text commands on the left pane and binary motor responses on the right, with independent scrolling.

```
 pin6 KV (ACM0↔ACM1)        [PROXY ON]│  MOT→CTRL pin3 bin (USB0)
──────────────────────────────────────┼────────────────────────────
   0.1  inc      0                    │   0.1  INC_R    =0
   0.1  hmph     0                    │   0.2  DIAG_R   =0
   0.1  mph      0                    │   0.2  BELT_R   =0
```

Key bindings:

| Key       | Action                                        |
|-----------|-----------------------------------------------|
| `e`       | Toggle emulate mode (replace controller)      |
| `+` / `-` | Adjust speed (emulate mode)                   |
| `]` / `[` | Adjust incline (emulate mode)                 |
| `p`       | Toggle proxy (forward controller to motor)    |
| `f`       | Toggle follow / pause scrolling               |
| `c`       | Show only changed values                      |
| `u`       | Show only unique values                       |
| `j` / `k` | Scroll down / up                              |
| `q`       | Quit                                          |

## License

MIT
