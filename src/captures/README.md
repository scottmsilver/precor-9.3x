# Logic Analyzer Captures

Raw serial bus captures from the Precor 9.31 treadmill, taken with a Saleae-compatible logic analyzer.

## Format

Each CSV has 8 digital channels sampled at high frequency. The first row is a header:

```
Time[s], Channel 0, Channel 1, Channel 2, Channel 3, Channel 4, Channel 5, Channel 6, Channel 7
0.000000000000000, 0, 1, 1, 0, 0, 1, 0, 1
```

- **Channel 2** — Pin 3 (Motor → Console)
- **Channel 5** — Pin 6 (Console → Motor)

Both channels carry 9600 baud, 8N1 serial with RS-485 inverted polarity (idle LOW instead of the usual HIGH).

## Files

| File | Size | Notes |
|------|------|-------|
| `try2.csv` | 5.2 MB | Normal operation |
| `try3.csv` | 3.0 MB | Normal operation |
| `try5.csv` | 4.5 MB | Speed/incline changes |
| `try6.csv` | 5.5 MB | Speed/incline changes |
| `try7.csv` | 1.5 MB | Shorter capture |

All captures show integer-only incline values (0–8 observed). The protocol does not support fractional incline.

## How to Read Them

Two parsers live in this directory:

### decode_inverted.py (recommended)

Handles the RS-485 inverted polarity automatically. Tries both normal and inverted decoding and shows which produces valid KV data.

```bash
# Decode all active channels
python3 src/captures/decode_inverted.py src/captures/try6.csv

# Decode just pin 6 (Console → Motor)
python3 src/captures/decode_inverted.py src/captures/try6.csv 5

# Decode just pin 3 (Motor → Console)
python3 src/captures/decode_inverted.py src/captures/try6.csv 2
```

Output shows decoded `[key:value]` messages with timestamps.

### analyze_logic.py

Standard-polarity decoder with frame grouping and timing analysis. Useful for understanding the burst/cycle structure.

```bash
python3 src/captures/analyze_logic.py src/captures/try6.csv
```

Shows frame grouping, hex dumps, ASCII representations, and timing between bursts.
