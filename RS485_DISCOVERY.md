# RS-485 Polarity Inversion Discovery

## Date: 2026-02-12

## Summary

The "binary R...E frame protocol" observed on pin 3 and in logic analyzer
captures **never existed**. It was KV text misinterpreted due to UART signal
polarity inversion caused by reading RS-485 signaling with a TTL-level adapter.

Both pin 3 and pin 6 carry the same KV text protocol (`[key:value]`) over
RS-485 differential signaling.

## How We Found It

1. Binary emulation mode was added to `dual_monitor.py` — replaying captured
   "binary frames" to the motor. The motor never responded.

2. KV text emulation (`[diag:0]\xff`) continued to work fine.

3. User observed: "if you listen on ACM0 on pin 6 you get ASCII, if you listen
   on USB0 on pin 6 you get binary." Same wire, different data on different
   adapters.

4. User pointed out RS-485 and TTL are on the same bus.

5. Analyzed logic analyzer idle states — all gaps show `idle_val=0`. Standard
   UART idles HIGH (1). This is inverted polarity, characteristic of RS-485.

6. Wrote inverted UART decoder (idle=LOW, start=rising edge, invert data bits).
   Result: **100% good stop bits** and clean KV text on BOTH channels.

## The Evidence

### Standard polarity decode (WRONG):
- Channel 5 (pin 6): 9536 bytes, **59.7% good stop bits** ← red flag!
- Channel 2 (pin 3): 10484 bytes, **65.0% good stop bits** ← red flag!
- Produced "binary frames" starting with 0x52 ('R'), ending with 0x45 0x01/0x00

### Inverted polarity decode (CORRECT):
- Channel 5 (pin 6): 10950 bytes, **100% good stop bits**
- Channel 2 (pin 3): 10484 bytes, **100% good stop bits**
- Produced clean KV text: `[amps]`, `[inc:0]`, `[belt:14]`, `[ver:19A]`, etc.

## The Protocol

### Pin 6: Console → Motor (requests)
Format: `[key]\xff` (query) or `[key:value]\xff` (set command)

14-key repeating cycle:
```
[inc:0]     → set/report incline
[hmph:0]    → set/report speed (hex)
[amps]      → query current draw
[err]       → query error status
[belt]      → query belt counter
[vbus]      → query bus voltage
[lift]      → query lift position
[lfts]      → query lift status
[lftg]      → query lift goal
[part:6]    → report part number
[ver]       → query firmware version
[type]      → query machine type
[diag:0]    → diagnostic mode
[loop:5550] → loop counter
```

### Pin 3: Motor → Console (responses)
Format: `[key:value]` — NO `\xff` delimiter, with occasional `[]` empty brackets

Example response values:
```
[amps:FF]    → 255 (hex)
[belt:14]    → belt counter (hex, varies: 6, B, 12, 14, 16)
[inc:0]      → incline position (0-3 observed)
[lift:28]    → lift position (hex, varies: 23, 28, 2B, 2C, 34)
[lfts:1]     → lift status
[lftg:0]     → lift goal
[hmph:69]    → speed value (hex, varies: 0, 23, 64, 66, 69, 6B, 6E, 8C, 8D)
[loop:4C57]  → loop counter (hex, = 19543 decimal)
[type:20]    → machine type (hex)
[ver:19A]    → firmware version
[part:6]     → part identifier
[err]        → no error (empty value)
```

## Why the Adapters Differ

- **ACM0/ACM1 (USB-CDC)**: Has RS-485 level conversion built in. Correctly
  reads/writes the RS-485 signal as standard UART. Shows KV text.

- **USB0 (FTDI TTL)**: Reads the raw RS-485 differential signal at TTL levels.
  RS-485 idles LOW (opposite of UART idle HIGH). Every bit is inverted.
  Shows "binary" data that is actually bit-flipped KV text.

## What Was Wrong

Everything based on the "binary protocol" interpretation was incorrect:

- `protocol.py` frame parser (`parse_frame`, `decode_frames`) — parses phantom frames
- `protocol.py` base-16 digit encoding (`DIGITS`, `encode_base16`) — phantom encoding
- `protocol.py` direction hypothesis — phantom frame types
- `BIN_CYCLE` in `dual_monitor.py` — replays inverted KV text as binary
- `bin_probe.py` — sends inverted KV text, motor ignores it
- Pin 3 frame type correlations (0x54→diag, 0x52→inc) — correlated because
  the inverted bytes DO map to the ASCII KV keys, just bit-flipped

## Bit Inversion Examples

Standard UART `[` = 0x5B = 01011011
Inverted:                  10100100 = 0xA4... wait, that's not 0x52.

Actually the inversion is more nuanced — RS-485 inverts the entire UART frame
including start/stop bits, which shifts the byte boundaries. The UART decoder
with inverted polarity (start on rising edge, invert sampled bits) handles
this correctly. It's not a simple per-byte NOT operation.

## Next Steps

1. Remove binary emulation (`b` key) from `dual_monitor.py`
2. Fix right pane — decode USB0 (pin 3) data by inverting polarity in software,
   or replace FTDI with an RS-485 adapter
3. Simplify `protocol.py` — remove phantom binary frame parsing
4. The KV emulation (`e` key) is correct and should remain
5. Consider reading pin 3 motor responses via software inversion to get
   actual motor telemetry in the monitor
