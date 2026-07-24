# Esp32Tap Rev B engineering review

**Status: HOLD.** Repository-closeable design checks are separated from the
vendor, production-firmware, and physical evidence that still does not exist.
This report is not authorization to fabricate, pay, or operate a treadmill.

## Executive conclusion

The confirmed pre-fabrication defects that triggered the redesign have been
addressed in source:

- USB VBUS no longer powers the board or relay.
- Treadmill-derived voltage permission independently gates relay power and the
  motor transmit buffer.
- The relay uses one signal pole and one feedback pole instead of parallel
  signal contacts.
- The coil is the 5 V Omron part and is driven from a gated 5 V LDO through a
  BC817-40 with a 560 Ω base resistor.
- The protected input, TVS, local ceramics, output capacitors, and TPS54202
  feed-forward network were revised.
- Receive taps are 10 kΩ, reducing modeled dead-board injection.
- Native USB is a short, matched, F.Cu-only pair on a declared four-layer
  stackup.
- The antenna keepout, board test access, enclosure connector centers,
  antenna void, posts, and meshes are generated and independently checked.
- Schematic, PCB, BOM, CPL, reports, Gerbers, stock evidence, and meshes now
  have fail-closed reproduction gates.

That is enough to call the design internally coherent. It is not enough to
call the product physically validated.

## Safety path review

```text
J1/J2 +8 V pass-through
        |
        +-- F1 -- D1 -- VIN -- U2 ------------------------- +3V3
                         |                                   |
                         +-- D3 clamp                        +-- U6 AND gates
                         +-- U4 voltage window                   |
                         +-- U5 gated 5 V relay supply           +-- U7 TX buffer

RELAY_GATE = RELAY_CMD AND TREAD_OK
TX_GATE    = TX_ENABLE AND TREAD_OK
```

| Condition | Pole A | TX path | Repository-supported statement |
|---|---|---|---|
| No board power | NC bypass | High impedance | Passive topology and generated connectivity agree |
| Boot/reset | Command pull-downs request bypass | Disabled | Static circuit behavior is fail-to-bypass |
| Proxy | NC bypass | Disabled | Console path does not need firmware forwarding |
| Emulate request without TREAD_OK | NC bypass requested | Disabled | U6 blocks both permissions in hardware |
| Qualified Emulate | NO connects TX_DRV to MOT6 | Enabled | Requires firmware ordering and measured feedback |
| Power/permission loss | Coil supply and TX permission fall | Disabled | Electrical decay is modeled; contact closure time remains unmeasured |

Single-fault limitations remain. Pole-B feedback is an armature proxy, not
direct continuity sensing of pole A. A common gate fault, open suppression
part, welded signal contact, component misload, or layout/manufacturing defect
can defeat an assumption. The treadmill safety key is authoritative.

## Electrical disposition

### Input and rails

The local branch uses a 0.75 A/24 V PPTC, SS34 series diode, SMBJ10A TVS,
100 µF bulk, two 10 µF/25 V X7R ceramics, and local 100 nF bypass. The
TPS54202 starting network is 10 µH, two 22 µF/25 V X7R outputs, 100 kΩ /
22 kΩ feedback, and 56 pF feed-forward compensation.

U4's modeled protected-VIN release/trip corners are approximately 6.22–6.60 V
for undervoltage recovery and 10.30–10.93 V for overvoltage trip. These are
not treadmill-connector limits; D1 drop, cable drop, source impedance, noise,
temperature, and actual component tolerances must be measured.

The ngspice buck deck is an averaged energy-envelope model. It supports a
4.495 ms 90% startup and 3.233 V minimum for the declared 450 mA load step.
It does not establish TPS54202 control-loop margin, ripple, EMI, pulse
skipping, thermal margin, or production capacitor derating.

### Relay path

The 5 V/237 Ω relay draws 18.79–23.90 mA across declared coil corners.
The conservative drive case yields 1.82 mA base current, 16.11 mA coil
current, 4.20 V across the high-resistance coil, and forced beta 8.83.
The SMAJ6.0CA suppression model keeps Q1 peak voltage at 12.27 V and provides
faster current decay than an ordinary flyback diode under the assumed
0.1/0.5/1 H sweep.

Coil-current decay is not contact movement. Operate, release, bounce, welding,
three-hour temperature, and fault-to-stable-NC timing remain bench gates.

### Serial and USB

Both passive receive taps are 10 kΩ. The modeled 10–90% rise is 1.110 µs
against a 104.167 µs bit, while one/two simultaneous unpowered injections are
0.286/0.572 mA. Real cable capacitance, source impedance, ESP32 leakage, clamp
behavior, and framing margin require a scope and actual hardware.

USB VBUS terminates in protection, bypass, discharge, and the Q2 presence
detector. It has no local-power path. The USB pair is routed entirely on F.Cu
with zero signal vias and sub-micrometre route-length skew in the generated
board. Stackup and impedance still require vendor confirmation, followed by
enumeration and eye-margin testing.

## Layout and manufacturing disposition

- Board outline: 100.0 × 55.0 mm.
- Copper: F.Cu, In1.Cu, In2.Cu, and B.Cu.
- In1.Cu is the continuous GND reference plane.
- The project records `JLC04161H-7628` geometry and a 90 Ω USB netclass.
- ERC reports zero errors and warnings under the committed severity policy.
- The combined DRC/schematic-parity report records zero DRC violations, zero
  unconnected pads, and zero footprint errors. The locked ignored item is the
  intentional silkscreen clipping associated with the off-board antenna
  geometry.
- The deterministic archive has exactly 13 expected members, including both
  inner copper layers, Excellon drill data, and Gerber job metadata.
- Assembly audit binds design, schematic, PCB, DNP flags, BOM, CPL, LCSC code,
  class, package, position, layer, and rotation.

The ESP32 module extends 6.3 mm beyond the finished board edge. JLC must
approve the production carrier/panel treatment without copper or metal near
the antenna. Do not infer approval from local DRC.

## Enclosure disposition

The checked meshes are regenerated from pinned OpenSCAD source. Independent
mesh checks find one connected, watertight, consistently wound body per file.
Functional probes cover the 100 × 55 mm cavity, mounting posts, both RJ45
openings, USB overmold access, lid reliefs, and the 15 mm antenna void.

Physical plug fit, material shrink/warp, screw behavior, installed clearance,
RF range, and JLC3DP acceptance remain open.

## Evidence boundaries

The current stock snapshot binds 43 exact JLC/LCSC identities to the BOM for a
two-board quantity. It was read from official anonymous JLC part-detail pages
and expires after 24 hours. Stock is not a quote, placement acceptance, or
permission to substitute.

`firmware/safety_model.py` exercises lease, deadline, freshness, relay
transition, feedback, reset, and watchdog semantics on the host. No production
ESP-IDF application is present. No repository test can prove its GPIO timing,
watchdog release, native USB behavior, radio coexistence, or treadmill safety.

## Recommendation

Keep the package on HOLD. The next permitted activity is read-only vendor
review of the exact regenerated archive/BOM/CPL and both meshes. If that review
is clean, the owner may decide whether to authorize a current-limited
verification prototype while explicitly accepting that physical evidence can
only be gathered after assembly. Production operation remains blocked by the
firmware and complete bench matrices.
