# Esp32Tap Rev B release ledger

**Status: HOLD.** This file intentionally answers whether the exact package may
advance. It does not authorize an order or payment.

## Repository-closeable gates

| Gate | State | Evidence |
|---|---|---|
| Electrical source and pin maps | PASS | `tools/design.py`, design tests |
| Typed schematic and ERC | PASS | generated schematic, normalized `erc.rpt` |
| Four-layer PCB and parity DRC | PASS | inspector/validator, normalized `drc.rpt` |
| USB route and all-layer antenna keepout | PASS | PCB semantic tests |
| BOM/CPL/DNP/placement parity | PASS | exporter assembly audit |
| Exact deterministic fabrication package | PASS | 13-member export and reproduction gate |
| Dual-engine behavioral simulations | PASS within declared models | seven decks, three repeats, two ngspice majors |
| Enclosure source/mesh/fit checks | PASS geometrically | pinned render and functional probes |
| Host firmware safety contract | PASS as reference only | safety model and manifest-builder tests |
| Recent exact-page part identity/stock | PASS at snapshot time | BOM-bound sanitized snapshot |

These rows mean the repository artifacts are mutually consistent. They do not
mean a physical board has passed.

## Open vendor gates

| Gate | State | Required evidence |
|---|---|---|
| PCB DFM acceptance | OPEN | Read-only preview for the exact ZIP |
| Production stack and USB impedance | OPEN | JLC confirmation against `JLC04161H-7628` |
| Antenna-overhang carrier/panel | OPEN | Vendor drawing with antenna clearance |
| THT RJ45 assembly | OPEN | Service acceptance for exact unshielded jacks |
| BOM/CPL placement | OPEN | Exact-part resolution and visual placement review |
| Substitutions | OPEN | None, or a new engineering review |
| Enclosure material/mesh DFM | OPEN | JLC3DP acceptance without scaling or geometry changes |
| Current quote | OPEN | Fresh quote tied to artifact hashes |

No cart, submission, or payment action is needed to perform these read-only
checks.

## Open firmware gates

- Production ESP-IDF application implementing `firmware/PLAN.md`.
- Active-low VBUS attach strategy reviewed against ROM/reset behavior.
- Exact sdkconfig, application, bootloader, partition table, and
  `bundle_sha256`.
- 2 s task watchdog with immediate silent reset and no debug/halt path.
- Brownout threshold selected below the measured production minimum +3V3.
- WSS/BLE/executor ownership, deadlines, radio coexistence, and security
  implemented on the target.

The host model is a contract, not a firmware deliverable.

## Open physical gates

- Treadmill rail envelope, input current, surge/noise, startup, brownout, and
  thermal characterization.
- Converter switching waveform, ripple, loop response, and EMI.
- Unpowered serial loading and live edge/timing measurements.
- Relay operate/release/bounce/weld behavior and stable-NC timing under every
  fault.
- Native USB attach, reset, enumeration, hot-unplug, and signal integrity.
- 1,000 contact-observed transitions with no MOT6 byte/frame splice.
- Three-hour load/thermal and Wi-Fi/BLE coexistence runs.
- Enclosure connector, screw, mounting, resin, clearance, and RF checks.
- Proxy-only first treadmill contact and separately authorized Emulate test.

## Decision

The design may be described as ready for read-only vendor review and a possible
owner-authorized verification prototype after that review. It may not be
described as physically proven, production safe, or cleared for treadmill
operation.

Keep HOLD until the owner reviews the vendor results and explicitly decides
whether to accept the unavoidable pre-prototype physical unknowns. Regardless
of that prototype decision, production operation remains blocked until every
applicable firmware and bench gate is evidenced.
