# Esp32Tap Rev C validation record

**Status: HOLD.** Passing repository gates establish internal consistency
under declared models, not vendor acceptance or physical treadmill behavior.

**Evidence date:** 2026-07-24

## Canonical repository gate

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

The declared engines include KiCad/pcbnew 10.0.1, host ngspice 42, pinned
offline Docker ngspice 39, and three repetitions of every simulation deck.

## Evidence ledger

| Area | Result | Repository-supported statement |
|---|---|---|
| Schematic | PASS | Typed Rev C schematic; ERC policy clean |
| PCB | PASS | 95 × 58 mm, four layers, exact net/pad parity, DRC/parity clean |
| Antenna | PASS | U1 fully on-board; 3.25/3.30 mm margins; exact stock all-layer keepout |
| USB | PASS | F.Cu-only, no signal vias, cycle-free planar topology, both A/B paths matched |
| Power | PASS under model | Exact 2 A trace-union solve plus conservative independent GND-via envelope |
| Assembly | PASS | Design↔schematic↔PCB↔BOM↔CPL identity and placement parity |
| Fabrication | PASS | Exact deterministic 13-member package |
| Simulations | PASS under models | Eight decks × three runs on host 42 and Docker 39 |
| Firmware | Host reference only | Safety state-machine and manifest policy tests |

## Board geometry and routing

- Finished outline: 95.0 × 58.0 mm.
- U1 body-to-finished-edge margins: 3.25 and 3.30 mm.
- J1/J2: SMT Micro-Fit `430450809`/`430451010`; RJ45 is external pigtail
  hardware only.
- USB shortest paths:
  D− A/B 60.0528786214/59.0528786214 mm,
  D+ A/B 60.0528777233/59.0528777233 mm.
- D+/D− per-side skew: 0.0000008981 mm.
- Controlled USB geometry: 0.2906 mm width and 0.2000 mm edge gap.

## Exact PCB power model

At 2.0 A the emitted-copper planar-union solve gives:

| Quantity | Result |
|---|---:|
| +8 V resistance | 18.745090 mΩ |
| Conservative trace-only GND resistance | 19.665484 mΩ |
| Supply-plus-return drop | 76.821149 mV |
| +8 V maximum via current | 1.131922 A |
| +8 V maximum via rise | 15.234166 °C |
| +8 V maximum via I²R | 1.864230 mW |
| Maximum F/B track rise | 6.660309 °C |

The graph splits intersections and collinear overlaps, unions coincident
same-layer copper, preserves distinct parallel routes, and rejects duplicate
intended via occurrences.

In1 plane sharing is not solved exactly. Therefore each 1.4/1.0 mm GND via is
also qualified independently at the full 2.0 A using 20 µm barrel plating:
12.273573 °C rise and 2.328020 mW I²R. This is a conservative envelope, not
an installed thermal result. The 20 µm assumption is the IPC-6012 Class 2
average-hole-copper basis described by JLC; live quote/DFM confirmation is
mandatory.

## Eight-deck ngspice evidence

`input_protection`, `tread_permission`, `safety_truth_table`,
`relay_drive_release`, `vbus_present`, `buck_averaged`, `uart_taps`, and
`harness_supply_drop` each run three times on both engines. Representative
supported values remain recorded in `sim/README.md`; `sim/assertions.json` is
the pass/fail authority.

The exact machine-readable harness unsupported list is:

- `RJ45_SINGLE_OPEN_2A`
- `MINIMUM_VIN`
- `SOURCE_IMPEDANCE`
- `AMBIENT_THERMAL`
- `TRANSIENT_RESPONSE`
- `COMPLETE_INSTALLED_DROP`
- `USB_RETURN_CURRENT`
- `ESD`
- `RF`
- `SWITCHING_LOOP`

Scenario-specific unsupported simulation claims also remain unsupported:
PPTC thermal behavior; vendor diode leakage; out-of-envelope surges; real
treadmill rail/surge timing; regulator current-limit/thermal behavior; gate
propagation/partial-power leakage; RF coupling; relay contact motion and
guaranteed inductance; BC817 transient/combined-temperature saturation;
native USB attach/enumeration/eye margin; switching-loop margin/ripple/EMI;
vendor-model startup; real UART integrity/leakage; and all physical enclosure
effects.

## Current local artifact identity

| Artifact | SHA-256 |
|---|---|
| `kicad/Esp32Tap.kicad_pcb` | `af6208addc4253620bfacf9dbca51c0963ef676910b3934d1bcf307996803f1a` |
| `kicad/Esp32Tap-gerbers.zip` | `219562b21c51bf71e11474c5ea3fae9b698c56b279ad1a41950b440381507ed5` |
| `bom/BOM.csv` | `9e972a4008ede233bc63c19e05d1b15e43d6e0e15094e2106f43ec3737647f7c` |
| `bom/CPL-positions.csv` | `977f1a0ac2ba081d7f5c49f900a9250c8c2d5c26de7ca02cec59130619426e87` |

These identify repository bytes only. The operator-observed
`vendor/JLC-DFM-REVIEW.json` is bound to an older archive and must not be
presented as review of these bytes.

## Open vendor and physical gates

- live current ZIP/BOM/CPL placement and production CAM review;
- stack/impedance and 20 µm barrel-plating confirmation;
- J3 plated mechanical stakes accepted under normal top-side reflow, without
  manual/wave processing;
- carrier/rails/tooling/depanel details for the 58 mm board axis;
- exact turnkey Micro-Fit-to-RJ45 pigtail drawings and quote;
- current enclosure quote, material, fit, installed clearance, and RF tests;
- treadmill source, minimum VIN, transient, complete installed drop, ambient,
  current, and thermal measurements;
- RJ45 single-open 2 A qualification;
- relay contact timing/bounce/weld/temperature and 1,000 transitions;
- native USB attach/enumeration/unplug/eye/ground-current tests;
- production ESP-IDF binary, WDT/brownout, security, and safety matrix.

Until those applicable gates are closed and the owner authorizes purchase, the
correct decision remains HOLD.
