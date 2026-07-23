# Esp32Tap — ESP32-S3 Precor serial-bus tap

Single-board replacement for the Pi Zero 2 W + PiZeroHat treadmill interceptor.
An ESP32-S3-WROOM-1 sits in the middle of the Precor 9.31 console↔motor RJ45
cable, runs the timing-critical KV serial engine + full safety envelope
on-device, and exposes FTMS BLE (peripheral), HRM BLE (central) and an
authenticated WSS/HTTPS control API over WiFi. Architecture and system-split
rationale: see `firmware/PLAN.md`.

**Status: rev A design.** ERC and DRC pass clean (`kicad-cli` 10.0.1,
`--severity-all`, 0 violations, 0 unconnected). Not yet fabricated — bench
bring-up checklist below must run before the board ever touches a treadmill.

## What's in this directory

| Path | Contents |
|------|----------|
| `NETLIST.md` | **Source of truth** — every component, pin and net (generated from `tools/design.py`) |
| `kicad/` | KiCad 10 project: `Esp32Tap.kicad_sch`, `Esp32Tap.kicad_pcb`, generated symbol lib, ERC/DRC reports, fab gerbers (`Esp32Tap-gerbers.zip`) |
| `bom/BOM.csv` | Full BOM with LCSC numbers, JLC Basic/Extended class, unit costs |
| `bom/CPL-positions.csv` | Pick-and-place file (JLC format, bottom-left origin) |
| `ORDERING.md` | Exact JLCPCB + JLC3DP order walkthrough with cost lines |
| `enclosure/` | Parametric OpenSCAD two-part case + `DIMENSIONS.md` |
| `firmware/PLAN.md` | ESP-IDF porting plan, watchdog state machine, test gates |
| `tools/` | `design.py` (master data) + generators for schematic/board/docs |

## Board overview (100 × 55 mm, 2 layer)

```
      antenna overhangs top edge (Espressif keep-out fully off-board)
   ┌──────────────────╨╨╨╨╨──────────────────┐
 ┌─┤ J1 RJ45           ESP32-S3-WROOM-1-N8   │
 │ │ CONSOLE   K1 relay      (U1)        SW2 │
 └─┤           G6K-2F-Y                LEDs  ├─┐
 ┌─┤ J2 RJ45   driver     SW1 EN       U3    │ │ J3 USB-C
 │ │ MOTOR                             ESD   ├─┘ (flash/JTAG/console)
 └─┤  8V→3.3V buck: F1 TVS bulk → TPS54202   │
   └─────────────────────────────────────────┘
```

* **J1/J2** — same Amphenol 54602-x08 jack family and footprints as the
  proven `hardware/PiZeroHat/`. RJ45 pins 1 (GND), 2 (+8V), 4 (unknown),
  5 (**safety interlock**), 7 (GND), 8 (+8V) are pure copper pass-through —
  they never touch silicon. Pin 3 (motor→console) is bridged through and
  passively tapped via 4.7 kΩ. Pin 6 (console→motor) is cut through the
  fail-safe relay.
* **B.Cu** is a solid GND plane; the pass-through bus runs as short B.Cu
  verticals between the jacks.

## The fail-safe relay (the design's central safety claim)

K1 is an Omron **G6K-2F-Y DC3** DPDT signal relay (LCSC C2153097), wired so
both poles in parallel switch the pin-6 path:

| Relay state | Console pin 6 | Motor pin 6 | ESP32 TX |
|---|---|---|---|
| **De-energized** (unpowered, boot, crash, watchdog, Proxy) | bridged to motor | bridged to console | **physically disconnected** (sits on NO contacts) |
| **Energized** (Emulate only) | released (4.7k RX tap only) | driven by ESP32 TX via 100R | connected |

Consequences, addressing the review-gate blockers:

1. **Unpowered = stock treadmill, verified electrically.** With the board
   dead, the ESP32's TX pin is open-circuit (relay NO), the RX taps sit
   behind 4.7 kΩ (worst-case back-feed through the GPIO clamp into the dead
   rail ≈ 0.3 mA — does not distort bus HIGH levels), and the ESD clamps
   (PESD3V3L1BA) are GND-referenced bidirectional parts that stay inert.
2. **Proxy mode is a hardware bridge, not software forwarding.** The MCU
   only listens in Proxy; bytes flow console→motor through relay contacts
   with zero latency. This is a deliberate improvement over the Pi (which
   must software-forward); the auto-proxy/auto-emulate *detection* logic is
   unchanged. Boot-time TX glitches on the motor line are impossible: the
   relay only energizes after the UART is configured and Emulate entered.
3. **Relay driver is glitch-safe**: NPN low-side driver (S8050) with 10 kΩ
   base pull-down — GPIO21 is Hi-Z during boot/reset, so the relay stays
   released. `esp_task_wdt` supervises **every task that can hold the relay
   energized** (serial engine, emulate cycle, interval executor), with
   `CONFIG_ESP_TASK_WDT_PANIC=y` so a stall panic-resets → GPIO Hi-Z →
   relay released (see `firmware/PLAN.md`).

## Electrical design notes

* **Serial**: 3.3 V single-ended TTL, 9600 8N1, **inverted polarity**
  (idle LOW) — handled by hardware UART inversion
  (`uart_set_line_inverse`), no transceivers (adding RS-485 differential
  drivers would break the bus).
* **GPIO map** (no ESP32-S3 strap pins 0/3/45/46, no USB pins 19/20):
  IO17 = UART1 TX (motor pin 6), IO18 = UART1 RX (console pin 6),
  IO16 = UART2 RX (pin 3 tap), IO21 = relay, IO38 = status LED.
* **Power**: treadmill +8 V (RJ45 pins 2/8) → 0.75 A/16 V polyfuse →
  SMBJ12A TVS → 100 µF/25 V bulk + 4.7 µF/50 V ceramic → SS34 ORing diode →
  TPS54202 buck → 3.3 V. A second SS34 ORs **USB VBUS** into the buck, so
  flashing on the bench needs no treadmill. Buck EN sits on a 100k/47k
  divider from VIN (not a bare pull-up): EN ≈2.6 V at 7.6 V VIN and ≈1.6 V
  at 4.7 V USB — above the ~1.21 V enable threshold on both sources and
  safely below the TPS54202's 7 V EN absolute maximum. Budget: ESP32-S3 WiFi TX bursts
  ~0.35–0.5 A at 3.3 V plus relay coil ~45 mA (Emulate only) — inside the
  TPS54202's 2 A and far inside the D24V10F5-proven +8 V budget
  (~0.25 A at 8 V worst case).
* **USB-C**: HRO TYPE-C-31-M-12 (C165948) on the S3's native USB —
  flash, JTAG and console over one connector, USBLC6-2SC6 ESD, 5.1 k CC
  pull-downs. EN/BOOT tactile switches for recovery.
* **Antenna**: module antenna section overhangs the top board edge, so the
  entire Espressif keep-out region (no copper any layer) is off-board.
  Enclosure is plastic with ≥3 mm air gap at the antenna end (see
  `enclosure/`).

## Bring-up (bench first — never treadmill-first)

1. **Visual + shorts**: check 3V3↔GND, 8V↔GND resistance before power.
2. **USB power only**: plug USB-C. 3.3 V rail present (TP3 vs TP4), power
   LED on, enumerate the S3's USB. Flash a hello-world over USB.
3. **Relay sanity, unpowered**: with the board unpowered, verify J1.6↔J2.6
   continuity (< 1 Ω) and ESP-TX-to-motor-pin-6 open. Energize GPIO21 in
   firmware: bridge opens, TX connects. Measure coil current (~45 mA).
4. **Bench 8 V**: feed 8 V into J1 pins 2/8 (GND 1/7) from a current-limited
   supply; verify 3.3 V, then both supplies together (ORing diodes share).
5. **Loopback serial rig**: second USB-UART with inversion, or a Pi running
   the existing `python/tools/listen.py`, replays captured console bursts
   into J1.6; verify the parsed KV stream matches `cpp/tests` golden
   vectors; verify TX emulate cycle timing on a logic analyzer against a
   Pi capture (M1/M2 gates in `firmware/PLAN.md`).
6. **Signal-integrity-while-dead test** (gate requirement): with a live
   bus between two bench UARTs through J1/J2 and the board **unpowered**,
   scope the pin-6 and pin-3 lines for level distortion.
7. Treadmill contact ONLY via the **treadmill-contact gate checklist** in
   `firmware/PLAN.md` — the single authoritative gate: M1–M3 green on the
   bench rig (they are bench-only by definition), evidence archived, then
   TC1 (proxy-only observation) before TC2 (first emulate). Belt clear,
   following the PiZeroHat WIRING-CHECKLIST discipline.

## Regenerating the design

Everything is generated from `tools/design.py` (components/pins/nets):

```bash
cd hardware/Esp32Tap/tools
python3 design.py                 # validate net tables
python3 gen_sch.py                # schematic + symbol lib
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 /usr/bin/python3 gen_pcb.py   # board (pcbnew API)
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 /usr/bin/python3 gen_docs.py  # NETLIST.md + BOM + CPL
cd ../kicad
kicad-cli sch erc --severity-all --exit-code-violations -o erc.rpt Esp32Tap.kicad_sch
kicad-cli pcb drc --severity-all --exit-code-violations -o drc.rpt Esp32Tap.kicad_pcb
```

Generators assert netlist parity between `design.py`, the exported schematic
netlist, and the board's pad-net assignments — the three can never drift.
