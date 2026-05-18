# Treadmill HAT — Wiring Checklist

| # | From | To | Wire | ✓ |
|---|------|-----|------|---|
| **POWER RAILS** | | | | |
| 1 | J1 pin 1 | GND rail | thick | ☐ |
| 2 | J1 pin 7 | GND rail | thick | ☐ |
| 3 | J2 pin 1 | GND rail | thick | ☐ |
| 4 | J2 pin 7 | GND rail | thick | ☐ |
| 5 | U1 pin 2 (GND) | GND rail | thick | ☐ |
| 6 | A1 pin 6 (GND) | GND rail | thin | ☐ |
| 7 | C3 lead 1 | GND rail | thin | ☐ |
| 8 | C4 lead 1 | GND rail | thin | ☐ |
| 9 | J1 pin 2 | +8V rail | thick | ☐ |
| 10 | J1 pin 8 | +8V rail | thick | ☐ |
| 11 | J2 pin 2 | +8V rail | thick | ☐ |
| 12 | J2 pin 8 | +8V rail | thick | ☐ |
| 13 | U1 pin 3 (VIN) | +8V rail | thick | ☐ |
| 14 | C3 lead 2 | +8V rail | thin | ☐ |
| **+5V TO PI** (regulator output) | | | | |
| 15 | U1 pin 1 (VOUT) | A1 pin 2 (+5V) | thin | ☐ |
| 16 | C4 lead 2 | A1 pin 2 (+5V) | thin | ☐ |
| **CABLE PASS-THROUGH** | | | | |
| 17 | J1 pin 4 | J2 pin 4 | thin | ☐ |
| 18 | J1 pin 5 | J2 pin 5 | thin | ☐ |
| **MOTOR READ — TAPPED (pin 3)** | | | | |
| 19 | J1 pin 3 | J2 pin 3 | thin | ☐ |
| 20 | J1 pin 3 | R1 lead A | thin | ☐ |
| 21 | R1 lead B | A1 pin 11 (GP17) | thin | ☐ |
| **CONSOLE READ — CUT (pin 6 console side)** | | | | |
| 22 | J1 pin 6 | R2 lead A | thin | ☐ |
| 23 | R2 lead B | A1 pin 13 (GP27) | thin | ☐ |
| **MOTOR WRITE — CUT (pin 6 motor side)** | | | | |
| 24 | J2 pin 6 | R3 lead A | thin | ☐ |
| 25 | R3 lead B | A1 pin 15 (GP22) | thin | ☐ |

---

## ⚠ DO NOT CONNECT

- ❌ J1 pin 6 to J2 pin 6 (these MUST be separate — Pi is in the middle)
- ❌ U1 VOUT to A1 pin 1 or A1 pin 17 (those are +3.3V — would destroy Pi)

## Leave floating (no connection needed)

- U1 pin 4 (~SHDN)
- U1 pin 5 (PG)
- All RJ45 SH (shield) pads — plastic housing, not used

---

## Smoke test (before plugging in cable, before inserting Pi)

- ☐ Multimeter: GND rail beeps to all GND points
- ☐ Multimeter: +8V rail is one net (all +8V points beep)
- ☐ Multimeter: NO short between +8V and GND
- ☐ Multimeter: NO short between +5V and any other rail
- ☐ U1 oriented correctly (VOUT pin = pin 1)
- ☐ Pi NOT inserted yet

## First power-on test

- ☐ Plug treadmill cable into J2 (motor side) only
- ☐ Multimeter on +8V rail reads ~8V (between 7-9V is fine)
- ☐ Multimeter on U1 output (pin 1) reads ~5V (between 4.9-5.1V)
- ☐ NOW safe to insert Pi

## Pi software check

- ☐ Pi boots normally (LED on Pi PCB blinks)
- ☐ GPIO config: GP17=IN, GP27=IN, GP22=OUT
- ☐ Read GP17 — should see motor responses (when console also plugged in)

---

**Total connections: 25**
**Components: 5 (A1, J1, J2, U1) + 5 (R1, R2, R3, C3, C4)**
