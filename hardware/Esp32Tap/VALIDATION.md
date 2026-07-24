# Esp32Tap Rev B validation record

**Status: HOLD.** This record distinguishes reproducible repository evidence
from vendor and physical claims. A passing row never promotes an unsupported
claim to a hardware result.

**Evidence date:** 2026-07-24

## Canonical gate

Run from the repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

The declared environment is Python 3.12/pytest 8, KiCad/pcbnew 10.0.1,
host ngspice 42, pinned offline Docker ngspice 39, and pinned Docker OpenSCAD
2021.01. Reproduction uses only declared source files in a temporary tree and
rejects undeclared outputs, symlinks, malformed summaries, stale generated
bytes, or an inexact fabrication set.

## Repository evidence ledger

| Area | Gate | Repository result | What it establishes |
|---|---|---|---|
| Electrical source | Design invariant and exact pin/type tests | PASS | Treadmill-only local power, direct +8 V/GND pass-through, hardware relay/TX gates, one transfer pole, one feedback pole, DNP and part locks |
| Schematic | Generated artifact plus KiCad ERC | PASS | Typed Rev B schematic; zero reported errors and warnings |
| PCB | Inspector, independent validator, KiCad DRC with schematic parity | PASS | 100 × 55 mm four-layer stack, exact pad/net parity, 0 DRC, 0 unconnected pads, 0 footprint errors |
| USB | Segment/topology/clearance tests | PASS | F.Cu-only pair, zero signal vias, 0.285/0.200 mm geometry, matched topology, route skew within the declared limit |
| Antenna | All-layer rule-area and copper audit | PASS | Copper keepout and intentional 6.3 mm module overhang match generated geometry |
| Assembly | Design↔schematic↔PCB↔BOM↔CPL audit | PASS | Exact populated/DNP sets, flags, codes, packages, quantities, positions, layers, and rotations |
| Fabrication | Clean export, normalized metadata, archive comparison | PASS | Exact 13-member four-layer Gerber/drill/job package with deterministic ZIP bytes |
| Enclosure | Pinned fresh render, canonical geometry, Trimesh and functional probes | PASS | Checked meshes match SCAD; each is one connected watertight body; declared cavity/openings/posts/antenna void exist |
| Firmware contract | Host safety model and manifest-builder tests | PASS as host reference | Deadline ordering, ownership, freshness, feedback, fail-closed transitions, and build-manifest policy |
| Parts | Official-page parser plus recent BOM-bound snapshot | PASS at recorded instant | Exact identity/class/package and public assembly stock for all 43 unique populated codes |

The DRC policy contains one intentional ignored check: silkscreen clipped by
the board edge at the module's off-board antenna geometry. The validator
requires that exact ignored set and rejects additions.

The final buck input layout was independently replayed after rerouting. The
U2 VIN pin-to-C4 path is 2.052 mm and C4-to-required-C3 path is 2.154 mm:
4.206 mm total on 0.60 mm F.Cu with no via. C4 and C3 ground branches reach
their vias in 1.229 and 1.030 mm; U2 ground reaches its via in 0.486 mm. The
bootstrap path is 2.205 mm and its complete modeled copper loop is 6.592 mm.
These measurements close the earlier placement/layout defect; switching
ripple, loop stability, EMI, and thermal behavior remain physical gates.

## Current artifact identity

These hashes identify the repository evidence reviewed here. They do not
replace a vendor preview or physical validation.

| Artifact | SHA-256 |
|---|---|
| `kicad/Esp32Tap.kicad_pcb` | `353087eaddc0e548db4c084c814f7604a2476be857f8aa93b27ea9794c18555c` |
| `kicad/Esp32Tap-gerbers.zip` | `ec4c982ad43ada44846b0e20741df945f166b8d2c17858c47ae7d2ea09f73d83` |
| `bom/BOM.csv` | `58fd75503d1d6af46115d48dd2a150731eb25bb720ef6c99a0cbf018ad2d340d` |
| `bom/CPL-positions.csv` | `4274b34c245ced0972235424dcd430626b52bf113d36990ea82fc58991b9b160` |
| `bom/JLC-STOCK-SNAPSHOT.json` | `6b7bd004e8121ca75cdd4b373ac8278e1670075b0626e9f14815498ee8e95284` |
| `enclosure/esp32tap_base.stl` | `4ec0ed81e3127cb441fa7fde67e19e435497c6f442c73ff881d35fcdc3162b77` |
| `enclosure/esp32tap_lid.stl` | `b61b33f5d91865cfa4b9e02049225de65f293b3328e116cb94a99d0aae9a3468` |

## Dual-ngspice evidence

Seven decks run three times on both engines. The assertion manifest fixes
engine identity, supported measurements, tolerances, and unsupported claims.
Representative values are:

| Deck | Supported result |
|---|---|
| Input protection | 8 V protected VIN 7.60–7.625 V; 20 V pulse VIN peak 12.531 V; modeled TVS pulse energies within declared bound |
| Tread permission | UV rising 6.224–6.600 V; OV rising 10.300–10.928 V; modeled OV disable 36.1 µs |
| Truth table | All 16 combinations match both hardware-AND equations; unpowered outputs low |
| Relay drive/release | Coil 18.79–23.90 mA; conservative forced beta 8.83; Q1 peak 12.27 V |
| VBUS presence | Active-low assertion in 4.083 µs; worst modeled unplug-to-high 1.912 ms; dead-domain injection zero in the isolated-gate model |
| Averaged buck | 90% startup 4.495 ms; 450 mA step minimum 3.233 V |
| UART taps | 1.110 µs rise; 0.286/0.572 mA one/two-tap unpowered injection |

Host/Docker and repeat-to-repeat values agree within the manifest tolerances.
See `sim/README.md` and `sim/assertions.json` for every measure and source
assumption.

## Enclosure evidence

| Mesh | SHA-256 | Bodies | Volume |
|---|---|---:|---:|
| `esp32tap_base.stl` | `4ec0ed81e3127cb441fa7fde67e19e435497c6f442c73ff881d35fcdc3162b77` | 1 | 47,294.952 mm³ |
| `esp32tap_lid.stl` | `b61b33f5d91865cfa4b9e02049225de65f293b3328e116cb94a99d0aae9a3468` | 1 | 30,663.317 mm³ |

The fresh render is compared by canonical triangle geometry, not raw facet
order. Both meshes are watertight, winding-consistent, and have exactly two
faces incident to every welded edge. RJ45 centers are derived from the PCB as
12.445 and 41.445 mm. The enclosure leaves a 15.0 mm void beyond the module
antenna.

## Stock evidence

`bom/JLC-STOCK-SNAPSHOT.json` records 43 exact parts for build quantity two,
with a BOM SHA-256 binding and official exact-page URLs. The recorded check is
`2026-07-24T09:16:59Z`. The smallest recorded assembly-stock ratio is the
54602-908LF RJ45: 3,484 available against four required.

`overseasStockCount` is treated as the public JLC assembly-stock field.
`canPresaleNumber` is retained as non-gating pre-order evidence because it can
be zero while the official page reports substantial assembly stock. The gate
rejects a snapshot older than 24 hours; refresh immediately before any vendor
review.

This does not establish assembly service compatibility, feeder choice,
placement acceptance, current price, or continuing availability.

## Explicitly unsupported

- Actual treadmill +8 V range, source impedance, ripple, surge repetition,
  startup, current capacity, brownout, and Wi-Fi/BLE load behavior.
- PPTC thermal trip/reset, TVS repetitive stress, capacitor DC-bias/temperature
  derating, and TPS54202 switching-loop/EMI performance.
- Real dead-board leakage, line levels, source impedance, edge quality, and
  serial timing margin.
- Relay operate/release/bounce/weld behavior, contact temperature, and
  contact-measured fault-to-NC latency.
- Native USB ROM/reset attach, enumeration, unplug, and eye margin.
- RF range under the treadmill hood and radio coexistence.
- Production ESP-IDF behavior, exact binary identity, watchdog action, and
  the complete safety matrix.
- JLC DFM, stackup/impedance acceptance, carrier treatment for the antenna
  overhang, placement preview, substitutions, and assembly service.
- Physical connector fit, resin shrink/warp, screw fit, installed clearance,
  and JLC3DP material acceptance.

## Physical acceptance gates

Before any treadmill contact, archive evidence against one production
`bundle_sha256` and perform:

1. rail, current, thermal, surge, brownout, and converter ripple/startup tests;
2. unpowered through-bus loading and serial waveform tests;
3. active-low VBUS presence plus reset/ROM/enumeration/unplug tests;
4. relay and TX hardware truth table, fault injection, feedback, and stable-NC
   timing measurements;
5. at least 1,000 contact-observed normal transitions with no MOT6 byte or
   frame splice;
6. three-hour load/thermal and radio-coexistence tests;
7. enclosure plug, mounting, material, and RF tests;
8. Proxy-only first treadmill contact, followed later by separately authorized
   Emulate testing.

Until those applicable gates and vendor review are recorded, the correct
decision remains HOLD.
