# Pi HAT — Treadmill Serial Tap — Wiring Guide

**For hand-wiring on perfboard or breadboard.**

---

## Components

| Ref | Part | Notes |
|-----|------|-------|
| A1 | Raspberry Pi Zero 2 W | 40-pin GPIO header |
| J1 | RJ45 jack — **"From Console"** | 8P8C through-hole, plug console cable here |
| J2 | RJ45 jack — **"To Motor"** | 8P8C through-hole, plug motor cable here |
| U1 | Pololu D24V10F5 | 5V step-down regulator (5-pin module) |
| R1 | 100Ω, 1/4W resistor | Series, motor read line |
| R2 | 100Ω, 1/4W resistor | Series, console read line |
| R3 | 100Ω, 1/4W resistor | Series, motor write line |
| C3 | 100nF ceramic disc | Decoupling, U1 VIN |
| C4 | 100nF ceramic disc | Decoupling, U1 VOUT |

---

## Pi GPIO Header pin reference (40-pin)

```
                    ┌─────────────┐
       3.3V    PIN 1│ ●         ● │PIN 2     +5V       ← VOUT goes here
   GP2 / SDA   PIN 3│ ●         ● │PIN 4     +5V
   GP3 / SCL   PIN 5│ ●         ● │PIN 6     GND       ← GND from regulator
   GP4         PIN 7│ ●         ● │PIN 8     GP14 / TXD
       GND     PIN 9│ ●         ● │PIN 10    GP15 / RXD
   GP17       PIN 11│ ●         ● │PIN 12    GP18         ← motor read (R1)
   GP27       PIN 13│ ●         ● │PIN 14    GND          ← console read (R2)
   GP22       PIN 15│ ●         ● │PIN 16    GP23         ← motor write (R3)
       3.3V   PIN 17│ ●         ● │PIN 18    GP24
   GP10/MOSI  PIN 19│ ●         ● │PIN 20    GND
   GP9 /MISO  PIN 21│ ●         ● │PIN 22    GP25
   GP11/SCLK  PIN 23│ ●         ● │PIN 24    GP8 /CE0
       GND    PIN 25│ ●         ● │PIN 26    GP7 /CE1
   GP0        PIN 27│ ●         ● │PIN 28    GP1
   GP5        PIN 29│ ●         ● │PIN 30    GND
   GP6        PIN 31│ ●         ● │PIN 32    GP12 /PWM0
   GP13/PWM1  PIN 33│ ●         ● │PIN 34    GND
   GP19       PIN 35│ ●         ● │PIN 36    GP16
   GP26       PIN 37│ ●         ● │PIN 38    GP20
       GND    PIN 39│ ●         ● │PIN 40    GP21
                    └─────────────┘
```

---

## RJ45 pin reference (looking at jack from front, tab on top)

```
        ┌───────────┐
        │ ┌───────┐ │
        │ │  TAB  │ │
        │ └───────┘ │
        │ 1 2 3 4 5 6 7 8 │
        └─────────────────┘
         ↑ pin 1 = leftmost when looking at jack from FRONT
```

**Cable function (per HARDWARE.md):**

| RJ45 Pin | Function |
|----------|----------|
| 1 | GND |
| 2 | +8V |
| 3 | Motor → Console (3.3V serial — TAPPED) |
| 4 | Unknown (pass-through) |
| 5 | Safety interlock (pass-through) |
| 6 | Console → Motor (3.3V serial — CUT, Pi in middle) |
| 7 | GND |
| 8 | +8V |

---

## Wiring connections

### POWER RAILS

**+8V rail** (input from cable):
- J1 pin 2 ────┐
- J1 pin 8 ────┤
- J2 pin 2 ────┤── **+8V rail**
- J2 pin 8 ────┤
- C3 pin 2 ────┤
- U1 pin 3 ────┘    (VIN)

**GND rail** (common ground):
- J1 pin 1 ────┐
- J1 pin 7 ────┤
- J2 pin 1 ────┤
- J2 pin 7 ────┤
- C3 pin 1 ────┤── **GND rail**
- C4 pin 1 ────┤
- U1 pin 2 ────┤
- A1 pin 6 ────┤   (any of: 6, 9, 14, 20, 25, 30, 34, 39)
- A1 pin 9 ────┘   (use at least one — multiple is fine)

**+5V to Pi** (regulator output):
- U1 pin 1 ────┐
- C4 pin 2 ────┤── **+5V rail**
- A1 pin 2 ────┘   (Pi +5V)  ⚠ NOT pin 1 or pin 17 — those are +3.3V!

### CABLE PASS-THROUGH (no Pi involvement)

| From | To | What |
|------|----|----|
| J1 pin 4 | J2 pin 4 | Unknown signal (just pass through) |
| J1 pin 5 | J2 pin 5 | Safety interlock (just pass through) |

### SIGNAL LINES (with series resistors)

**Pin 3 — Motor → Console (TAPPED):**

```
J1 pin 3 ──┬── J2 pin 3 ──── R1 (100Ω) ──── A1 pin 11 (GP17)
           └── shared net (Pi listens passively)
```

**Pin 6 — Console → Motor (CUT, Pi in middle):**

```
J1 pin 6 ──── R2 (100Ω) ──── A1 pin 13 (GP27)   [Pi reads from console]

J2 pin 6 ──── R3 (100Ω) ──── A1 pin 15 (GP22)   [Pi writes to motor]
```

⚠ J1 pin 6 and J2 pin 6 are **NOT connected to each other** — Pi is in the middle.

---

## U1 (D24V10F5) regulator pinout

Looking at module from above, pins facing down:

```
     ┌──────────────────┐
     │  ┌────────────┐  │
     │  │ D24V10F5   │  │
     │  └────────────┘  │
     │                  │
     │  1   2   3   4   5
     └──┴───┴───┴───┴───┴──┘
        │   │   │   │   │
       VOUT GND VIN SHDN PG
       (5V) (-) (8V) (NC) (NC)
```

**Connections to make:**
- Pin 1 (VOUT) → +5V rail (to A1 pin 2)
- Pin 2 (GND)  → GND rail
- Pin 3 (VIN)  → +8V rail (from cable)
- Pin 4 (~SHDN) → leave floating (or tie to VIN if you want to enable always)
- Pin 5 (PG)   → leave floating

---

## Wiring order (suggested)

1. **Power rails first.** Run +8V and GND rails on perfboard with thicker wire (22 AWG).
2. **Solder U1, C3, C4.** Get the regulator working — verify 5V on output with multimeter BEFORE plugging in Pi.
3. **Solder A1's GND and +5V connections.** Test: plug in cable, Pi should boot.
4. **Solder pass-through (pins 4, 5).** Test continuity J1↔J2 with multimeter.
5. **Solder signal lines with resistors.** R1 (motor read tap), R2 (console read), R3 (motor write).
6. **Final continuity check.** Verify nothing is shorted between rails.

---

## Pre-power smoke test

Before plugging in the cable for the first time:

- [ ] Multimeter continuity: GND rail is one big net (all GND points beep)
- [ ] Multimeter continuity: +8V rail is one net
- [ ] Multimeter NO continuity between +8V and GND (no shorts)
- [ ] Multimeter NO continuity between +5V and +8V or GND
- [ ] U1 oriented correctly (VOUT pin 1 going to +5V)
- [ ] Pi NOT YET inserted — test power rail without Pi first

Then plug cable into J2 (motor side) — multimeter should read +8V on the rail and +5V on U1 output. Now safe to insert Pi.

---

## Pi GPIO software setup

In your Pi code:

| GPIO | Direction | Purpose |
|------|-----------|---------|
| GP17 | INPUT | Read motor → console (pin 3 tap) |
| GP27 | INPUT | Read console → motor (pin 6 console side) |
| GP22 | OUTPUT | Write to motor (pin 6 motor side) |

**Critical:** GP22 must be OUTPUT, GP17 and GP27 must be INPUT. If GP22 is set as input, you can't drive the motor. If GP17/GP27 are set as output, you'll fight against the live signals on those wires.
