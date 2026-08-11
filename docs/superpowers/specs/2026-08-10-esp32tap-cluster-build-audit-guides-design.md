# Esp32Tap Cluster Build and Audit Guides Design

## Purpose

Replace the whole-board-after-assembly checklist with two separate printable
guides that move depth-first through the same functional chain:

1. an **empty-board build-and-test guide** that adds one cluster and proves it
   before permitting the next cluster; and
2. an **assembled-board audit-and-test guide** that isolates and diagnoses the
   same clusters without requiring wholesale disassembly.

The guides describe exact component pins, named nets, wire colors, inputs,
outputs, and measurements. They deliberately do not prescribe breadboard-hole
coordinates; physical placement remains the operator's choice.

## Deliverables

Create these source/artifact pairs in `hardware/Esp32Tap/bringup/`:

- `esp32tap-cluster-build-and-test.html` and `.pdf`
- `esp32tap-cluster-audit-and-test.html` and `.pdf`

The existing `esp32tap-module-test-checklist.html/.pdf` is superseded and will
be removed so that an operator cannot accidentally follow the wrong workflow.
Git history retains it. Any navigation or documentation link that names the
superseded artifacts is removed or redirected to the two new guides.

Both PDFs use US Letter portrait pages, suppress browser headers/footers, have
large writable evidence fields, and carry the same cluster numbers and names.

## Shared Functional Order

1. **Raw protection:** `+8V_RAW → RXEF075 → FUSED_8V → 1N5822 → VIN`.
2. **TSR supply:** `VIN → TSR 1-2433E → TSR_3V3`.
3. **DevKit and logic supply:** USB-powered `LOGIC_3V3`, then safe standalone
   source selection through the removable `STANDALONE POWER` link.
4. **TPS3700 voltage monitor:** `VIN → UV_SENSE/OV_SENSE → TREAD_OK`.
5. **AHC08 permission logic:** command inputs plus `TREAD_OK` produce
   `RELAY_GATE` and `TX_GATE`.
6. **TPS709 and BC337 driver:** `RELAY_GATE` enables `+5V_RLY` and the coil
   low-side transistor.
7. **Relay coil, local contacts, and feedback:** prove the relay's local NC/NO
   endpoints, energized local transfer, feedback truth, fail release, current,
   voltage, and temperature. This does not claim an end-to-end RJ45 path.
8. **AHC126 and UART taps:** prove transmit high impedance when disabled,
   enabled signal following, receive taps, and relay-selected TX path.
9. **Indicators and VBUS sensing:** prove the 3.3 V power LED, GPIO38 status
   LED, and active-low USB-presence input without joining VBUS to local rails.
10. **Whole-device standalone bench test:** identified observer firmware,
    USB physically absent, TSR-powered boot, disabled relay/TX, Wi-Fi/event-log
    observation, and recorded UART idle levels.
11. **RJ45 pass-through and treadmill bypass:** individually prove the eight
    connector conductors, including the end-to-end `CONSOLE.6 ↔ MOTOR.6` NC
    bypass path, then perform the no-control bypass-only treadmill current,
    voltage-drop, UART-observation, and thermal gates.

This order prioritizes electrical dependencies over physical breadboard zones.
A cluster may refer to parts physically near another cluster, but it cannot be
energized until all upstream outputs it consumes have passed.

## Build-and-Test Guide Structure

Each cluster is a self-contained build card with:

1. purpose and dependency statement;
2. exact parts, values, ordered part numbers where available, and polarity or
   orientation notes;
3. named input and output nets;
4. point-to-point construction steps in a safe order, including wire color;
5. an unpowered continuity, resistance, mapping, and polarity gate;
6. an explicit power-source/jumper/firmware state;
7. powered input and output measurements with numerical pass limits;
8. a STOP box for every failure; and
9. a signed pass gate that unlocks only the following cluster.

No construction step assumes a global rail exists until the cluster that
creates or verifies that rail has passed. Cross-cluster links are installed
only when their destination cluster is ready to test. `COIL POWER` and
`STANDALONE POWER` remain removable, named, and state-controlled.

## Assembled-Board Audit Guide Structure

Each matching cluster card has:

1. cluster boundaries and the upstream/downstream links to open or leave open;
2. input, output, supply, and ground measurement points by device pin and net;
3. orientation and visual checks;
4. unpowered resistance/continuity evidence;
5. the exact source, stimulus, jumper, firmware, and observation state;
6. expected powered measurements;
7. a small failure tree ordered from source/input through component and output;
8. instructions to restore any lifted link before proceeding; and
9. a signed pass/stop record.

The audit guide never tells the operator to drive an ESP GPIO externally while
it remains connected to the DevKit. Manual AHC08 input injection requires the
corresponding GPIO15 or GPIO21 jumper to be removed. Coil exercise requires an
identified bounded `relay-exerciser` firmware image.

## Shared Electrical Gates

- USB connected means `STANDALONE POWER` is physically removed.
- `STANDALONE POWER` installed means USB is physically disconnected.
- `COIL POWER` remains removed through the unloaded TPS709 test.
- Initial raw-power test: 8.00 V, no more than 250 mA current limit; protected
  VIN 7.20–7.90 V; TSR output 3.20–3.40 V; coil-open current below 50 mA.
- TPS3700: `TREAD_OK` low below UV, high at 8.00 V, low above OV; rising UV
  boundary 6.25–6.55 V and specified falling OV boundary 10.30–10.90 V.
- Unloaded enabled TPS709 output 4.75–5.25 V; disabled/discharged output below
  0.25 V.
- Loaded relay: supply limit no more than 500 mA, coil 90–110 mA, coil voltage
  at least 4.50 V, BC337 VCE no more than 0.30 V, feedback `(1,0)` energized
  and `(0,1)` bypass; `00` or `11` stops the build.
- Removing USB logic power, GPIO21 command, `TREAD_OK`, or VIN restores NC
  bypass in no more than 100 ms.
- TPS709 and BC337 remain at or below 45°C and no more than 10°C over ambient
  during the five-minute coil hold.
- Treadmill bypass current is no more than 500 mA; supply and ground-return
  drops are each no more than 50 mV; every connector and breadboard power-path
  endpoint remains at or below 40°C and no more than 10°C over ambient after
  fifteen minutes.
- Any unexpected reset during a powered bench or treadmill test is a STOP.

## Firmware and Treadmill Gates

The current no-control diagnostic image may observe but cannot satisfy relay
or UART exercise gates. Relay/coil/TX tests record an exact bounded exerciser
build identity. Standalone and treadmill bypass tests record an observer build
identity plus manifest path or SHA proving relay and TX outputs are disabled.

No treadmill cable is attached before clusters 1–10 pass. Treadmill power,
bench power, and USB power are all physically off before either RJ45 cable is
attached or removed, before the fused-DMM harness is installed or changed, and
before either direct +8 V path is restored.

The only treadmill operation covered by these guides is bypass-only: USB
absent, standalone power installed, observer firmware verified, relay off, TX
disabled, and current measured through the reviewed dual-pin fused-DMM harness.
Power is removed before that harness is removed and the two independent direct
+8 V paths are restored. Voltage-drop and thermal tests occur only after the
direct paths are restored.

Treadmill relay transfer and ESP transmit are explicitly prohibited by both
guides. They remain locked for a separate future functional-bring-up procedure
that must require a qualified functional-firmware identity and matching
production safety-test evidence. Passing the bypass-only guide does not grant
permission for transfer.

## Verification

Automated artifact checks extract PDF text and require every cluster heading,
input/output boundary, jumper rule, numerical limit, firmware identity field,
STOP gate, and signed pass gate. `pdfinfo` must report US Letter pages. Every
rendered page is inspected as a contact sheet for clipping, accidental spill
pages, browser headers, local file paths, and usable evidence space.

The build guide is complete only if no cluster consumes an unverified upstream
output. The audit guide is complete only if every opened link has an explicit
restoration step. The two guides must use identical cluster numbering, net
names, limits, and source-state terminology.
