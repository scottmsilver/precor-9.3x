# PRECOR 9.3x Treadmill Protocol Tools

Tools for reverse-engineering and controlling PRECOR 9.31 (and similar) treadmills via the serial bus between the console (Upper PCA) and motor controller (Lower PCA).

## Hardware Setup

- FTDI USB-to-serial adapter (FT232R or similar)
- Connect to the serial bus: 9600 baud, 8N1, 5V TTL UART
- Tap into communication between console and motor controller

## Protocol Summary

### Frame Format
```
52 [type] [payload...] 45 01
│         │            └─ Frame end ('E' + 0x01)
│         └─ Payload bytes
└─ Frame start ('R' = 0x52)
```

### Known Packets

**SET_SPD (0x2A) - Speed:**
```
52 2A 1F 2F 8B [base16 speed] 45 01
```
Speed encoded as hundredths of mph (3.5 mph = 350)

**SET_INC (0x4B) - Incline:**
```
52 4B CA 5A [base16 incline] 45 01
```
Incline encoded as half-percent (2.0% = 4)

**Custom Base-16 Digit Set:**
```
Value:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
Byte:  9F   9D   9B   99   97   95   93   91   8F   8D   7D   7B   79   77   75   73
```

### Unknown Packets
- `UNK_52` (0x52), `UNK_54` (0x54), `UNK_9A` (0x9A), `UNK_A2` (0xA2), `UNK_D4` (0xD4)

## Tools

| File | Description |
|------|-------------|
| `protocol.py` | Shared protocol library (encoding/decoding) |
| `monitor.py` | Curses packet monitor with recording |
| `emulate.py` | Console emulator to control motor directly |
| `send_cmd.py` | Command-line packet sender |
| `sniff.py` | Simple unique packet sniffer |

### Quick Start
```bash
pip install pyserial

# Monitor packets
python3 monitor.py

# Send speed command
python3 send_cmd.py speed 3.5

# Emulate console (disconnect real console first!)
python3 emulate.py --speed 2.0
```

## License

MIT
