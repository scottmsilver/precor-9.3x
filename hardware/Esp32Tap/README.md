# Esp32Tap Rev B

**Status: HOLD.** The repository design is a treadmill-powered verification
build. Do not submit fabrication, authorize substitutions, pay, or connect an
Emulate-capable build to a treadmill until the open vendor, firmware, and bench
gates are reviewed.

Esp32Tap sits inline between the console and motor-controller RJ45 cables of a
Precor 9.31. An ESP32-S3 observes the proprietary inverted 9600-baud serial
traffic. In Proxy mode, the console-to-motor path is a normally closed relay
contact; it does not depend on software forwarding. Emulate mode transfers that
path to a hardware-gated ESP32 transmitter.

## Fixed power and safety architecture

- The treadmill serial cable's nominal +8 V conductors are the board's only
  power source. They remain direct copper pass-throughs between J1 and J2.
- The local branch is `+8V_RAW → F1 → D1 → VIN`. D1 provides reverse-polarity
  protection; D3 clamps protected VIN; U2 converts VIN to +3V3.
- USB-C carries native USB data and VBUS presence only. It cannot energize VIN,
  +3V3, +5V_RLY, or K1. Programming needs a USB data cable and current-limited
  +8 V bench power at the RJ45 power pins.
- “Data only” does not mean isolated. J3 ground pins and shield connect to
  board/treadmill ground. Do not attach a treadmill-powered board to a USB
  host until host-to-treadmill ground potential and connection current have
  been measured safely and the isolation/bonding approach has been reviewed.
- U4 monitors protected VIN. Its window output `TREAD_OK` is hardware-ANDed
  with both `RELAY_CMD` and `TX_ENABLE`.
- K1 pole A is the serial transfer contact. Pole B reports armature position
  through `K1_NC_FB` and `K1_NO_FB`; it does not parallel the signal contact
  and cannot prove pole A is unwelded.
- U5 supplies the 5 V relay coil only when `RELAY_GATE` is true. U7 keeps the
  motor TX path high impedance unless `TX_GATE` is true.
- Loss of power, reset, or loss of `TREAD_OK` removes hardware permission and
  returns K1 toward its normally closed bypass. Actual contact timing is a
  bench measurement, not a repository claim.

The treadmill safety key remains the independent safety mechanism. This board
is not a certified functional-safety controller.

## Package map

| Path | Purpose |
|---|---|
| `tools/design.py` | Electrical source of truth: parts, pins, nets, DNP state, and invariants |
| `kicad/Esp32Tap.kicad_sch` | Generated typed schematic |
| `kicad/Esp32Tap.kicad_pcb` | Generated 100 × 55 mm four-layer PCB |
| `NETLIST.md` | Generated human-readable connectivity |
| `bom/BOM.csv` | Populated assembly BOM with exact JLC/LCSC identities |
| `bom/CPL-positions.csv` | Top-side placement file in JLC coordinates |
| `kicad/Esp32Tap-gerbers.zip` | Deterministic 13-member fabrication archive |
| `vendor/JLC-DFM-REVIEW.json` | Sanitized operator record of online DFM, bound to the exact local archive |
| `sim/` | Seven behavioral ngspice decks, assertions, and dual-engine runner |
| `enclosure/` | Parametric case, regenerated meshes, and independent fit validator |
| `firmware/PLAN.md` | Normative production-firmware and bench acceptance contract |
| `firmware/safety_model.py` | Executable host reference; not flashable firmware |
| `AI-HANDOFF.md` | Concise continuation instructions for Claude |
| `ORDERING.md` | Vendor-preview and eventual prototype-order procedure |

Generated artifacts must be changed through `tools/design.py` or the relevant
generator. Do not repair generated connectivity, BOM rows, or Gerbers by hand.

## Board facts

| Item | Rev B value |
|---|---|
| Finished outline | 100.0 × 55.0 mm |
| Stack | Four copper layers, 1.59 mm modeled finished thickness |
| Stackup metadata | `JLC04161H-7628`; 0.035 mm outer and 0.0152 mm inner finished copper |
| Inner reference | One In1.Cu GND zone, continuous below USB except normal antipads; explicit antenna keepout |
| USB routing | F.Cu-only, no signal vias; 0.2906 mm / 0.2000 mm controlled run plus four short 0.20 mm connector breakouts |
| MCU | ESP32-S3-WROOM-1-N8 |
| Relay | Omron G6K-2F-Y-TR DC5, 237 Ω nominal coil |
| Test access | TP1–TP13, including VIN, +5V_RLY, permission gates, TX, and both feedback contacts |
| Antenna | Module extends 6.3 mm beyond the board edge; copper keepout on every layer |

## Reproduce the repository evidence

From the repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

`clean-check` regenerates declared artifacts in an isolated directory and
compares every byte. `check` runs the test suite, reproduction check, host
ngspice 42 plus pinned Docker ngspice 39, enclosure validation, fabrication
audit, and the recent official JLC stock snapshot check. These commands are
offline with respect to treadmill hardware and never drive a belt.

Passing them proves internal consistency under the declared models. It does
not prove physical power integrity, relay contact behavior, RF performance,
USB enumeration, production firmware, or vendor manufacturability.

The sanitized JLCDFM file records the result an operator observed after
uploading the exact archive named by its SHA-256. It is not a vendor-signed
result or independent proof of upload provenance. It does not approve the
production stack, controlled impedance, the antenna-overhang carrier, the
RJ45 assembly fixture/process, BOM/CPL placement, or substitutions.

## Bench bring-up order

1. Inspect assembly polarity, part identity, soldering, and shorts with no
   cable attached. Confirm J1.6–J2.6 normally closed continuity and TX
   isolation.
2. Apply current-limited +8 V from a bench supply to the documented RJ45 power
   pins. Check VIN, +3V3, +5V_RLY-off, TREAD_OK, and thermal behavior.
3. Before attaching USB, measure host-to-board ground potential and connection
   current with a safe bench method and establish the reviewed
   isolation/bonding setup. Then attach USB data while bench +8 V remains
   present and verify active-low `VBUS_PRESENT_N`, ROM/reset attach behavior,
   enumeration, and unplug.
4. Use an isolated serial fixture and logic analyzer for receive loading,
   inverted idle level, gap capture, relay transfer, feedback, and complete
   zero-frame ordering.
5. Complete the production firmware manifest and all `firmware/PLAN.md`
   acceptance gates, including 1,000 contact-observed transitions.
6. First treadmill contact is Proxy-only with relay energization compiled out.
   Emulate testing is a separate later event with the belt clear and physical
   safety key immediately accessible.

The build remains on HOLD until the applicable stage-specific gates are
explicitly closed.
