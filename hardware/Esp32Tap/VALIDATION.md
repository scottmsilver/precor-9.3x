# Esp32Tap Rev E validation record

**Status: HOLD.** Passing repository gates establish internal consistency
under declared models, not vendor acceptance or physical treadmill behavior.

**Evidence date:** 2026-07-25

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
| Schematic | PASS | Typed Rev E schematic; ERC policy clean |
| PCB | PASS | 95 × 58 mm, four layers, exact net/pad parity, DRC/parity clean |
| Antenna | PASS | U1 fully on-board; 3.25/3.30 mm margins; exact stock all-layer keepout, router-blocked and audited to zero track/via copper |
| USB | PASS | F.Cu-only, no signal vias, cycle-free planar topology, both A/B paths matched |
| Power | PASS under model | Exact 2 A trace-union solve plus conservative independent GND-via envelope |
| Assembly | PASS | Design↔schematic↔PCB↔BOM↔CPL identity and placement parity |
| Fabrication | PASS | Exact deterministic 13-member package |
| Simulations | PASS under models | Eight decks × three runs on host 42 and Docker 39 |
| Firmware | Host reference only | Safety state-machine and manifest policy tests |

## Board geometry and routing

- Finished outline: 95.0 × 58.0 mm.
- U1 body-to-finished-edge margins: 3.25 and 3.30 mm.
- J1/J2: identical Molex `0441440003` right-angle SMD 8P8C RJ45 jacks (LCSC
  `C585890`), edge-mounted with the mating opening facing off a short
  board edge (Rev E: J1 off X=0, J2 off X=95); no external pigtail
  hardware. There is no mechanical keying
  between the console and motor interfaces — CONSOLE/MOTOR silkscreen plus
  housing color are the only differentiator.
- USB shortest paths:
  D− A/B 55.9528786214/54.9528786214 mm,
  D+ A/B 55.8729617233/54.8729617233 mm.
- D+/D− per-side skew: 0.0799168981 mm (gate 0.5 mm).
- Controlled USB geometry: 0.2906 mm width and 0.2000 mm edge gap.

## Exact PCB power model

At 2.0 A the emitted-copper planar-union solve gives:

| Quantity | Result |
|---|---:|
| +8 V resistance | 24.677226 mΩ |
| Conservative explicit-copper GND resistance | 24.173777 mΩ |
| Supply-plus-return drop | 97.702006 mV |
| +8 V maximum via current | 0.827864 A |
| +8 V maximum via rise | 7.482581 °C |
| +8 V maximum via I²R | 0.685491 mW |
| Maximum track rise (either net) | 6.660685 °C |

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

The exact machine-readable unsupported list is:

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
| `kicad/Esp32Tap.kicad_pcb` | `fecbc139b52bc873add8ddb1625db63f899dc9ac98c7ca8db442febc2edeea96` |
| `kicad/Esp32Tap-gerbers.zip` | `ad4d98149b7ca04f6cf20692860a7fcc517fb5daee0bce5eda2193b56cc55b85` |
| `bom/BOM.csv` | `431c19cac0d0ec19c25b0aa46be198026619b64a86df6025a4dc9881d1633047` |
| `bom/CPL-positions.csv` | `a475ff84cb210053e400e619b3c0073af0af9ac7cf57f4d3e85049bf5cc3014d` |

These identify repository bytes only. The operator-observed
`vendor/JLC-DFM-REVIEW.json` is bound to an older archive and must not be
presented as review of these bytes.

## Open vendor and physical gates

- live current ZIP/BOM/CPL placement and production CAM review, including
  the Extended-class RJ45 jacks' Standard PCBA placement support;
- stack/impedance and 20 µm barrel-plating confirmation;
- J3 plated mechanical stakes accepted under normal top-side reflow, without
  manual/wave processing;
- carrier/rails/tooling/depanel details for the 58 mm board axis;
- current enclosure quote, material, fit, installed clearance, and RF tests
  for the RJ45 aperture geometry;
- treadmill source, minimum VIN, transient, complete installed drop, ambient,
  current, and thermal measurements;
- RJ45 single-open 2 A qualification;
- relay contact timing/bounce/weld/temperature and 1,000 transitions;
- native USB attach/enumeration/unplug/eye/ground-current tests;
- production ESP-IDF binary, WDT/brownout, security, and safety matrix.

Until those applicable gates are closed and the owner authorizes purchase, the
correct decision remains HOLD.
