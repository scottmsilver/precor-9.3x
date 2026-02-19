# FTMS Bluetooth Daemon

Exposes the Precor 9.31 treadmill as a standard Bluetooth FTMS (Fitness Machine Service) device. Fitness apps like Zwift, Peloton, and Kinomap see it as a modern smart treadmill — real-time speed, incline, distance, and elapsed time, plus remote control.

## How It Works

```
┌──────────────┐   BLE GATT    ┌──────────────────┐   Unix socket   ┌────────────────┐
│  Fitness App  │◄────────────►│   ftms-daemon     │◄──────────────►│  treadmill_io   │
│  (Zwift etc.) │  0x1826 FTMS │  (this binary)    │  /tmp/         │  (C++ GPIO)     │
└──────────────┘               └──────────────────┘  treadmill_io   └────────────────┘
                                       │              .sock
                                       │ TCP :8826
                                 ┌─────┴──────┐
                                 │ Debug shell │
                                 │ (optional)  │
                                 └────────────┘
```

The daemon reads treadmill state (speed, incline) from the `treadmill_io` C binary via its Unix socket, converts units to FTMS format, and broadcasts via BLE notifications at 1 Hz. Control point writes from fitness apps are translated back into treadmill commands.

## GATT Service

Advertises as **"Precor 9.31"** with FTMS service UUID `0x1826`.

### Characteristics

| Characteristic | UUID | Properties | Description |
|----------------|------|------------|-------------|
| Feature | 0x2ACC | Read | Supported features: distance, incline, elapsed time, speed/incline targets |
| Treadmill Data | 0x2ACD | Notify | Speed, distance, incline, elapsed time — updated every second |
| Speed Range | 0x2AD4 | Read | 0.8–19.3 km/h (0.5–12.0 mph) in 0.16 km/h steps |
| Incline Range | 0x2AD5 | Read | 0–15% in 1% steps |
| Control Point | 0x2AD9 | Write + Indicate | Speed/incline targets, start/stop commands |
| Machine Status | 0x2ADA | Notify | Status changes (speed/incline changed, stopped) |

### Treadmill Data (0x2ACD) — 13 bytes at 1 Hz

```
Offset  Bytes  Type     Field
0       2      uint16   Flags (0x008C)
2       2      uint16   Speed (km/h * 100)
4       3      uint24   Total Distance (meters)
7       2      sint16   Inclination (% * 10)
9       2      sint16   Ramp Angle (0)
11      2      uint16   Elapsed Time (seconds)
```

### Control Point (0x2AD9)

| Opcode | Command | Payload | Description |
|--------|---------|---------|-------------|
| 0x00 | Request Control | — | Client claims control (always succeeds) |
| 0x02 | Set Target Speed | uint16 LE (km/h * 100) | Set belt speed |
| 0x03 | Set Target Incline | sint16 LE (% * 10) | Set incline |
| 0x07 | Start/Resume | — | Start the belt |
| 0x08 | Stop/Pause | uint8 (1=stop, 2=pause) | Stop or pause |

Responses are indicated as `[0x80, request_opcode, result_code]` where result 0x01 = success.

### Unit Conversions

The treadmill operates in mph (tenths) internally. FTMS uses km/h (hundredths). Conversions use integer math to avoid floating point:

- mph tenths → km/h hundredths: `value * 1609 / 100`
- km/h hundredths → mph tenths: `value * 100 / 1609`

## Building

### Cross-compile for Raspberry Pi (from x86 host)

```bash
# Build the custom Docker image (once)
docker build -f Dockerfile.cross -t ftms-cross-aarch64 .

# Cross-compile
cross build --target aarch64-unknown-linux-gnu --release

# Binary at: target/aarch64-unknown-linux-gnu/release/ftms-daemon
```

Or from the project root: `make ftms`

### Native compile (on the Pi)

```bash
sudo apt install libdbus-1-dev
cargo build --release
```

## Running

The daemon needs Bluetooth and `treadmill_io` running:

```bash
# Manual
sudo ./ftms-daemon

# With options
sudo ./ftms-daemon --socket /tmp/treadmill_io.sock --debug-port 8826

# Via systemd (installed by deploy.sh)
sudo systemctl start ftms
```

### Debug Shell

Connect to port 8826 for a text-based debug interface:

```bash
nc localhost 8826
```

Commands:

| Command | Description |
|---------|-------------|
| `state` | Current speed, incline, elapsed, distance |
| `td` | Treadmill Data characteristic as hex |
| `feat` | Feature characteristic as hex |
| `sr` | Supported Speed Range as hex |
| `ir` | Supported Incline Range as hex |
| `cp <hex>` | Write to Control Point (e.g., `cp 023200` = set speed 5.0 km/h) |
| `sub` | Subscribe to 1 Hz treadmill data stream |

Control point examples:
```
cp 00              → Request Control
cp 02 3200         → Set speed to 5.0 km/h (0x0032 = 50, but LE so 3200)
cp 03 3200         → Set incline to 5.0% (0x0032 = 50, but LE so 3200)
cp 07              → Start
cp 08 01           → Stop
```

## Testing

```bash
# Unit tests (protocol encoding, parsing, conversions, fuzz)
cargo test

# Integration tests via debug server (requires running daemon)
cargo test --test debug_integration -- --ignored --test-threads=1

# BLE hardware tests (requires two Bluetooth adapters: hci0 server, hci1 client)
cargo test --test integration -- --ignored --test-threads=1
```

The unit test suite includes fuzz coverage: every possible single-byte opcode, all 65,536 two-byte combinations, and max/min boundary values for all numeric fields.

## systemd

```ini
# /etc/systemd/system/ftms.service
[Unit]
After=bluetooth.target treadmill_io.service
Requires=bluetooth.target
Wants=treadmill_io.service

[Service]
ExecStart=/usr/local/bin/ftms-daemon
Environment=RUST_LOG=info
Restart=always
```

Installed automatically by `deploy.sh` when the cross-compiled binary exists.
