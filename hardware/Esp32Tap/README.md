# Esp32Tap Rev D

**Status: HOLD.** Do not submit fabrication, authorize substitutions, pay, or
connect an Emulate-capable build to a treadmill until the applicable vendor,
firmware, harness, and bench gates are closed.

Esp32Tap sits inline between the console and motor-controller serial cables of
a Precor 9.31. In Proxy mode a normally closed relay preserves the passive
console path. Emulate mode transfers that path to a hardware-gated ESP32-S3
transmitter. The treadmill safety key remains authoritative; this board is not
a certified functional-safety controller.

## Rev D architecture

- J1/J2 are both the identical Molex 0441440003 right-angle SMD 8P8C RJ45
  jack (LCSC C585890), edge-mounted with the mating opening facing off the
  board's X=0 edge. There is no separate pigtail harness and no mechanical
  keying between console and motor any more; CONSOLE/MOTOR silkscreen is
  the only differentiator.
- The treadmill cable's +8 V rails are the only board power input and pass
  directly between J1/J2. F1/D1 protect only the local branch.
- USB-C is native data and VBUS-presence sensing only. USB cannot energize the
  board or relay; programming needs current-limited serial +8 V bench power.
- `TREAD_OK` independently hardware-gates relay supply and the motor TX buffer.
- K1 uses one transfer pole and one armature-feedback pole; feedback does not
  prove transfer-contact continuity.

## Package map

| Path | Purpose |
|---|---|
| `tools/design.py` | Electrical source of truth |
| `kicad/Esp32Tap.kicad_sch` | Generated typed schematic |
| `kicad/Esp32Tap.kicad_pcb` | Generated 95 × 58 mm four-layer PCB |
| `bom/BOM.csv` / `bom/CPL-positions.csv` | Exact assembly inputs |
| `kicad/Esp32Tap-gerbers.zip` | Deterministic 13-member fabrication archive |
| `kicad/models/` | Committed RJ45 jack 3D model (STEP + WRL, LCSC C585890) |
| `sim/` | Eight ngspice decks and dual-engine assertion runner |
| `firmware/PLAN.md` | Production firmware and bench contract |
| `firmware/safety_model.py` | Host reference, not flashable firmware |
| `ORDERING.md` | HOLD-state vendor review procedure |
| `AI-HANDOFF.md` | Concise continuation brief |

Do not repair generated hardware or assembly outputs by hand.

## Locked board facts

| Item | Rev D value |
|---|---|
| Finished outline | 95.0 × 58.0 mm |
| Stack | Four layers; modeled 1.59 mm `JLC04161H-7628` |
| Board connectors | J1/J2 both `0441440003` (LCSC C585890), right-angle SMD 8P8C RJ45, unkeyed |
| Antenna | U1 fully on-board; 3.25/3.30 mm body margins; stock all-layer keepout |
| USB | F.Cu-only, zero signal vias, 0.2906 mm width / 0.2000 mm controlled edge gap |
| USB paths | D− A/B 60.0528786214/59.0528786214 mm; D+ A/B 60.0528777233/59.0528777233 mm |
| USB per-side skew | 0.0000008981 mm |
| 2 A PCB trace-union drop | 98.379966 mV supply plus return |
| +8 via maximum | 0.828913 A; 6.660309 °C rise |
| GND-via envelope | Full 2.0 A in any via; 12.273573 °C at 20 µm plating |

The GND return result is a conservative trace-only solve. The independent
full-current GND-via envelope covers omitted plane sharing; neither result
claims complete installed behavior.

## Reproduce

From repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

The simulation runner executes eight decks three times on host ngspice 42 and
pinned Docker ngspice 39. Passing proves only the declared models and artifact
parity.

## Bring-up sequence

1. Inspect identity, polarity, soldering, shorts, NC bypass, and TX isolation.
2. Apply current-limited +8 V at the documented RJ45 power contacts;
   verify VIN, +3V3, relay-off supply state, TREAD_OK, current, and temperature.
3. Establish a reviewed USB/treadmill bonding or isolation setup, then test
   VBUS presence, ROM/reset attach, enumeration, and unplug.
4. Use isolated serial fixtures for line loading, inverted idle, gap capture,
   relay transfer, feedback, and zero-frame ordering.
5. Complete the production firmware manifest and all `firmware/PLAN.md` gates.
6. Make first treadmill contact Proxy-only with relay energization compiled
   out. Emulate is a later, separately authorized test.

The exact unsupported harness claims are `RJ45_SINGLE_OPEN_2A`,
`MINIMUM_VIN`, `SOURCE_IMPEDANCE`, `AMBIENT_THERMAL`,
`TRANSIENT_RESPONSE`, `COMPLETE_INSTALLED_DROP`, `USB_RETURN_CURRENT`, `ESD`,
`RF`, and `SWITCHING_LOOP`.

Live JLC review of the current archive/BOM/CPL/placement, 20 µm hole copper,
J3 mechanical-stake reflow, carrier/rails, and enclosure quote remains open.
The correct decision remains HOLD.
