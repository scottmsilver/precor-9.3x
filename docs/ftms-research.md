# FTMS (Fitness Machine Service) Research

## Overview

Bluetooth SIG's **Fitness Machine Service** (UUID `0x1826`) — a standardized GATT service that lets fitness equipment broadcast speed, incline, distance, and time to apps like Zwift, QZ Fitness, Apple Watch, and Garmin watches. It also supports a **Control Point** so apps can send speed/incline commands *back* to the treadmill.

## Architecture

Separate daemon (not embedded in `treadmill_io` or `server.py`):

```
Zwift / Watch  ──BLE──>  ftms_daemon  ──socket──>  treadmill_io  ──RS-485──>  Motor
                          (GATT server)              (GPIO I/O)
```

- Reads speed/incline/distance from `/tmp/treadmill_io.sock`
- Advertises FTMS service with Treadmill Data notifications at 1 Hz
- Optionally accepts Control Point writes (Set Target Speed/Incline) and forwards to treadmill_io
- Non-safety-critical — if it crashes, treadmill keeps working

## Language Comparison

| Language | Best Library | GATT Quality | Dev Speed | Memory | CPU | Reliability | New Toolchain? | Reuses Code | LOC Estimate |
|---|---|---|---|---|---|---|---|---|---|
| **Python** | `bluez-peripheral` | Good | ~1 day | ~20-40 MB | ~3-5% | Moderate | No | Yes (`treadmill_client.py`) | ~50-100 |
| **C/C++** | `bluez_inc` (C) | Fair | ~1 week | ~2-5 MB | <1% | Excellent | No | Partial | ~500-1000 |
| **Rust** | `bluer` (official BlueZ) | Excellent | ~2-3 days | ~3-8 MB | <1% | Excellent | Yes (cargo) | No | ~150-250 |
| **Go** | `tinygo-org/bluetooth` | Good | ~2 days | ~10-15 MB | ~1-2% | Good | Yes | No | ~100-200 |

## GATT Characteristics

A treadmill FTMS peripheral needs these characteristics:

| UUID | Name | Properties | Required? |
|---|---|---|---|
| 0x2ACC | Fitness Machine Feature | Read | Mandatory |
| 0x2ACD | Treadmill Data | Notify | Mandatory |
| 0x2AD4 | Supported Speed Range | Read | If Control Point supported |
| 0x2AD5 | Supported Inclination Range | Read | If Control Point supported |
| 0x2AD9 | Fitness Machine Control Point | Write, Indicate | Optional (enables remote control) |
| 0x2ADA | Fitness Machine Status | Notify | If Control Point supported |

## Treadmill Data Binary Format (0x2ACD)

All little-endian, notified at ~1 Hz. A practical payload for this treadmill:

```
[flags: 2B] [speed: 2B] [distance: 3B] [inclination: 2B] [ramp_angle: 2B] [elapsed_time: 2B]
= 13 bytes total, flags = 0x040C
```

| Field | Condition | Type | Size | Resolution | Unit |
|---|---|---|---|---|---|
| Instantaneous Speed | bit 0 = 0 | uint16 | 2B | 0.01 | km/h |
| Total Distance | bit 2 = 1 | uint24 | 3B | 1 | meters |
| Inclination | bit 3 = 1 | sint16 | 2B | 0.1 | percent |
| Ramp Angle Setting | bit 3 = 1 | sint16 | 2B | 0.1 | degrees |
| Elapsed Time | bit 10 = 1 | uint16 | 2B | 1 | seconds |

**Critical quirk**: Bit 0 is inverted — when bit 0 = 0, instantaneous speed IS included.

### Unit Conversions

- Speed: mph × 1.60934 × 100 → uint16 (e.g., 5.0 mph = 805)
- Incline: percent × 10 → sint16 (e.g., 3.0% = 30)
- Distance: accumulate from speed × time → uint24 meters

### Supported Ranges (Precor 9.31)

- Speed: 0.80–19.31 km/h (0.5–12.0 mph), increment 0.16 km/h (0.1 mph)
- Incline: 0–15%, increment 1.0%

## Control Point (0x2AD9)

Key opcodes for treadmill:

| Opcode | Name | Parameter | Notes |
|---|---|---|---|
| 0x00 | Request Control | None | Handshake, must succeed before other commands |
| 0x01 | Reset | None | Reset to defaults |
| 0x02 | Set Target Speed | uint16 (0.01 km/h) | Convert back to mph for treadmill_io |
| 0x03 | Set Target Inclination | sint16 (0.1%) | Convert back to integer for treadmill_io |
| 0x07 | Start or Resume | None | |
| 0x08 | Stop or Pause | uint8 (1=stop, 2=pause) | |
| 0x80 | Response Code | opcode + result | Sent as indication after each write |

## App Compatibility

- **Zwift**: Sends incline changes but NOT speed (safety) — users control speed manually
- **QZ Fitness**: Sends both speed AND incline — full remote control
- **Apple Watch / Garmin**: Mostly data consumers (read-only)
- **Peloton**: Reads treadmill data for display, generally no control commands

## Reference Projects

- [ESP32_TTGO_FTMS](https://github.com/lefty01/ESP32_TTGO_FTMS) — ESP32 C++ treadmill FTMS (closest analog)
- [kettlerUSB2BLE](https://github.com/360manu/kettlerUSB2BLE) — Node.js on RPi Zero, serial exercise bike to FTMS
- [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift) — C++/Qt fitness bridge, hundreds of machines
- [BluetoothTreadmill](https://github.com/eborchardt/BluetoothTreadmill) — ESP32, serial interception like this project

## Recommendation

**Python** for v1 — fastest to prototype, reuses `treadmill_client.py`, `bluez-peripheral` makes GATT server ~50-100 lines. Run under systemd for reliability.

**Rust with `bluer`** for long-term — official BlueZ bindings, best GATT server API, tiny reliable daemon. Worth it if Rust is in the toolbox.
