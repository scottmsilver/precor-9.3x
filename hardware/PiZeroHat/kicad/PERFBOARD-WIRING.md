# Perfboard Wiring Sheet — Treadmill HAT

**Board:** plain perfboard, 24 cols × 20 rows. Holes referenced as (row, col).

**Layout:**
- Pi GPIO socket: pins 1-40 in rows 3-4, cols 3-22
- +8V bus (bare wire): row 6, cols 2-22
- GND bus (bare wire): row 8, cols 2-22
- Components (R1, R2, R3, U1, C3, C4): rows 10-12
- J1 (Console) 2×4 IDC header: rows 17-18, cols 2-5 (odd pins on row 18 / near edge, even pins on row 17)
- J2 (Motor) 2×4 IDC header: rows 17-18, cols 13-16 (odd pins on row 18 / near edge, even pins on row 17)

---

## Grid layout

```
        01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24
R01     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R02     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R03     .  .  01 03 05 07 09 11 13 15 17 19 21 23 25 27 29 31 33 35 37 39 .  .
R04     .  .  02 04 06 08 10 12 14 16 18 20 22 24 26 28 30 32 34 36 38 40 .  .
R05     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R06     .  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  +  .  .
R07     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R08     .  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  G  .  .
R09     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R10     .  .  .  .  .  .  .  .  .  .  .  .  Vo Gu Vi Sd Pg .  .  .  .  .  .  .
R11     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R12     .  .  .  .  .  R1 .  .  .  R1 .  .  .  .  .  .  .  .  .  .  .  .  .  .
R13     .  .  .  .  .  R2 .  .  .  R2 .  .  .  .  .  .  .  .  .  .  .  .  .  .
R14     .  .  .  .  .  R3 .  .  .  R3 .  .  .  .  .  .  .  .  .  .  .  .  .  .
R15     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R16     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R17     .  2  4  6  8  .  .  .  .  .  .  .  2  4  6  8  .  .  .  .  .  .  .  .
R18     .  1  3  5  7  .  .  .  .  .  .  .  1  3  5  7  .  .  .  .  .  .  .  .
R19     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
R20     .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .  .
```

**Legend:**
- `.` — empty perfboard hole
- `01`–`40` — Pi GPIO pin (row 3 = odd pins, row 4 = even pins). Pi sits in 2×20 socket at cols 3–22.
- `+` — +8V bus (bare wire across row 6, cols 2–22)
- `G` — GND bus (bare wire across row 8, cols 2–22)
- `Vo` — U1 pin 1 VOUT (+5V output)
- `Gu` — U1 pin 2 GND
- `Vi` — U1 pin 3 VIN (+8V input)
- `Sd` — U1 pin 4 ~SHDN (leave floating)
- `Pg` — U1 pin 5 PG (leave floating)
- `R1`/`R2`/`R3` — resistor leads at cols 6 and 10 (body spans cols 7–9 over the board)
- Numbers `1`–`8` at rows 17–18 — RJ45 pin via 2×4 IDC ribbon header. Standard IDC numbering: **odd pins on row 18 (near edge), even pins on row 17 (far edge)**. Left block (cols 2–5) is **J1 (Console)**, right block (cols 13–16) is **J2 (Motor)**.
- C3 leads share holes (10,14) and (10,15) with U1 GND/VIN. C4 leads share (10,13) and (10,14) with U1 VOUT/GND. Solder caps directly across the U1 pins on top side; U1 pins go through the same holes.

---

## Component placements

| Component | Position | Notes |
|-----------|----------|-------|
| **U1** D24V10F5 (5 pins horizontal) | row 10, cols 13-17 | Pin 1 (VOUT) at (10, 13), Pin 5 (PG) at (10, 17) |
| **C3** ceramic disc 100nF | leads share holes with U1 | Lead 1 in (10, 14) [U1 GND]; Lead 2 in (10, 15) [U1 VIN] |
| **C4** ceramic disc 100nF | leads share holes with U1 | Lead 1 in (10, 13) [U1 VOUT]; Lead 2 in (10, 14) [U1 GND] |
| **R1** 100Ω horizontal | leads at (12, 6) and (12, 10) | Motor read line — left lead → J1/J2 pin 3, right lead → Pi GP17 |
| **R2** 100Ω horizontal | leads at (13, 6) and (13, 10) | Console read — left → J1 pin 6, right → Pi GP27 |
| **R3** 100Ω horizontal | leads at (14, 6) and (14, 10) | Motor write — left → J2 pin 6, right → Pi GP22 |

(R1/R2/R3 stacked tightly at cols 6 and 10. Adjust spacing if leads touch.)

---

## Hole → Pi GPIO pin reference

Pi pin N is at: **odd N → row 3, col 3 + (N-1)/2**; **even N → row 4, col 3 + (N-2)/2**

Key pins you'll use:

| Pi Pin | Function | Hole |
|--------|----------|------|
| 2 | +5V | (4, 3) |
| 6 | GND | (4, 5) |
| 11 | GP17 | (3, 8) |
| 13 | GP27 | (3, 9) |
| 15 | GP22 | (3, 10) |

---

## Jumper list (back of board, insulated wire)

### Power rails (most critical — do first)

| # | From hole | To hole | Wire |
|---|-----------|---------|------|
| 1 | J1 pin 1 (18, 2) | GND bus (8, 2) | thin (long, row 18 → row 8) |
| 2 | J1 pin 7 (18, 5) | GND bus (8, 5) | thin (long, row 18 → row 8) |
| 3 | J2 pin 1 (18, 13) | GND bus (8, 13) | thin (long) |
| 4 | J2 pin 7 (18, 16) | GND bus (8, 16) | thin (long) |
| 5 | U1 GND (10, 14) | GND bus (8, 14) | thin |
| 6 | Pi pin 6 GND (4, 5) | GND bus (8, 5) | thin (shares hole with #2 at the bus end) |
| 7 | J1 pin 2 (17, 2) | +8V bus (6, 2) | thin (insulated, hops over GND bus) |
| 8 | J1 pin 8 (17, 5) | +8V bus (6, 5) | thin (insulated, hops over GND bus) |
| 9 | J2 pin 2 (17, 13) | +8V bus (6, 13) | thin (insulated, hops over GND bus) |
| 10 | J2 pin 8 (17, 16) | +8V bus (6, 16) | thin (insulated, hops over GND bus) |
| 11 | U1 VIN (10, 15) | +8V bus (6, 15) | thin |

### +5V from regulator to Pi

| # | From | To | Wire |
|---|------|----|------|
| 12 | U1 VOUT (10, 13) | Pi pin 2 +5V (4, 3) | thin |

### Pass-through (J1 ↔ J2 direct wires)

| # | From | To | Wire |
|---|------|----|------|
| 13 | J1 pin 4 (17, 3) | J2 pin 4 (17, 14) | thin (long, spans 11 cols, runs along row 17) |
| 14 | J1 pin 5 (18, 4) | J2 pin 5 (18, 15) | thin (long, spans 11 cols, runs along row 18) |

### Motor read tap (pin 3, both jacks share, Pi listens)

| # | From | To | Wire |
|---|------|----|------|
| 15 | J1 pin 3 (18, 3) | J2 pin 3 (18, 14) | thin (long, spans 11 cols, runs along row 18 — parallel to #14, keep them apart) |
| 16 | J1 pin 3 (18, 3) | R1 left lead (12, 6) | thin |
| 17 | R1 right lead (12, 10) | Pi GP17 pin 11 (3, 8) | thin (will hop over buses — use insulated wire) |

### Console read (Pi reads from J1 side of pin 6)

| # | From | To | Wire |
|---|------|----|------|
| 18 | J1 pin 6 (17, 4) | R2 left lead (13, 6) | thin |
| 19 | R2 right lead (13, 10) | Pi GP27 pin 13 (3, 9) | thin (hops over buses) |

### Motor write (Pi writes to J2 side of pin 6)

| # | From | To | Wire |
|---|------|----|------|
| 20 | J2 pin 6 (17, 15) | R3 left lead (14, 6) | thin (long, spans 9 cols) |
| 21 | R3 right lead (14, 10) | Pi GP22 pin 15 (3, 10) | thin (hops over buses) |

---

## Total: 21 jumper wires + 2 power buses

---

## Smoke test (BEFORE inserting Pi, BEFORE plugging in cable)

- ☐ Multimeter beep mode: GND bus (row 8) is one continuous net
- ☐ Multimeter: +8V bus (row 6) is one continuous net
- ☐ NO continuity between +8V bus and GND bus (no short!)
- ☐ NO continuity between +5V trace (10, 13) and any other rail
- ☐ U1 oriented correctly: pin 1 VOUT is leftmost (col 13), pin 5 PG is rightmost (col 17)
- ☐ Pi NOT inserted yet
- ☐ J1 and J2 headers seated firmly

## First power test

- ☐ Plug treadmill cable into J2 only (the motor side, since that's where +8V comes from)
- ☐ Multimeter on +8V bus reads ~8V (acceptable: 7-9V)
- ☐ Multimeter on U1 VOUT (10, 13) reads ~5V (acceptable: 4.9-5.1V)
- ☐ NOW insert Pi

## Pi software setup

- ☐ Pi boots (PWR LED solid, ACT LED blinks)
- ☐ Configure GPIOs: GP17 = INPUT, GP27 = INPUT, GP22 = OUTPUT
- ☐ Test reading GP17 — should see motor data when console is also connected

---

## ⚠ DO NOT

- ❌ Do NOT wire J1 pin 6 to J2 pin 6 (they MUST be separate — Pi is in the middle)
- ❌ Do NOT wire U1 VOUT to Pi pin 1 or pin 17 (those are +3.3V — would destroy Pi)
- ❌ Do NOT power up the Pi until you've verified +5V is correct on U1 output

---

## Bus jumpers cross-over note

Use **insulated wire on the back of the board for ALL jumpers** — too many cross over the bare bus wires:
- #7, #8, #9, #10 (J1/J2 +8V wires) cross OVER the GND bus on the way from row 17 up to row 6.
- #17, #19, #21 (resistor right leads → Pi GPIO) cross OVER both the +8V bus (row 6) and GND bus (row 8) on the way from rows 12–14 up to row 3.
- #14 and #15 (pin 5 pass-through and pin 3 tap) both run parallel along row 18 for ~11 cols. Keep them physically separated or use a slight offset — they're adjacent (row 18, cols 3–4 to cols 14–15).

Bare wire on any of these = short. Insulated wire (24 AWG hookup) only.
