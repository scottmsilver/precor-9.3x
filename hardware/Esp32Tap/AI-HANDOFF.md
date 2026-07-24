# Esp32Tap Rev B — handoff and advice for Claude

**Status: HOLD. Do not submit an order, add the design to a production cart,
authorize substitutions, or pay. Do not connect an Emulate-capable build to
the treadmill.**

Claude: treat this file as the continuation brief. Preserve the separation
between repository evidence, vendor review, and physical safety evidence.
Never turn a passing model or KiCad check into a claim about an assembled
treadmill.

## Non-negotiable design decisions

- The only board power source is treadmill +8 V on the inline serial cable.
  J1/J2 carry both +8 V conductors and both grounds straight through; F1/D1
  protect only the local branch.
- USB-C is data and VBUS-presence only. VBUS has no path to VIN, +3V3,
  +5V_RLY, or the relay. Programming requires a USB cable and
  **current-limited +8 V bench power**. **USB alone cannot power or program Rev B.**
- K1 is a 5 V, 237 Ω Omron G6K-2F-Y relay. Pole A is the serial transfer:
  normally closed CONS6→MOT6, normally open TX_DRV→MOT6. Pole B is dry
  armature feedback. It cannot prove pole A is unwelded.
- `TREAD_OK` comes from the protected treadmill VIN window. Hardware computes
  `RELAY_GATE = RELAY_CMD AND TREAD_OK` and
  `TX_GATE = TX_ENABLE AND TREAD_OK`; firmware cannot override a bad window.
- U5 and Q1 are series relay-coil controls. U7 makes the motor TX output high
  impedance without `TX_GATE`.
- The module antenna extends 6.3 mm off the finished board. Every copper layer
  is kept out, and the enclosure leaves a further 15 mm plastic/air void.
  JLC carrier approval and installed RF measurements remain mandatory.

`tools/design.py` is the electrical source of truth. Change it and regenerate.
Do not hand-edit generated schematic connectivity, PCB nets, BOM/CPL rows, or
Gerbers.

## What repository evidence currently means

The checked design includes:

- a generated typed schematic;
- a 100 × 55 mm four-layer PCB with `JLC04161H-7628` stack metadata;
- one uninterrupted In1.Cu GND reference;
- an F.Cu-only native-USB pair with zero signal vias;
- 13 exact fabrication members and deterministic archive metadata;
- exact BOM/CPL/DNP/class/package/position parity;
- seven dual-engine behavioral ngspice decks;
- pinned, regenerated, watertight enclosure meshes and functional fit probes;
- a recent BOM-bound snapshot of 43 exact official JLC part pages;
- a host firmware safety model and deterministic build-manifest checker.

Those are internal consistency checks. They do not close the vendor, firmware,
or physical gates listed below.

The final PCB reroute is identified by PCB SHA-256
`353087eaddc0e548db4c084c814f7604a2476be857f8aa93b27ea9794c18555c`.
U2 VIN through C4 to required C3 is 4.206 mm total on 0.60 mm F.Cu with no
via; the bootstrap connection is 2.205 mm and its copper loop is 6.592 mm.
The matching deterministic fabrication ZIP SHA-256 is
`ec4c982ad43ada44846b0e20741df945f166b8d2c17858c47ae7d2ea09f73d83`.
Do not compare or upload a package with different hashes without reproducing
the entire validation record.

## Firmware contract Claude must implement

`firmware/safety_model.py` is an executable **host reference**, not production
ESP-IDF firmware. Production behavior must preserve:

- one owner tuple `(transport, concrete handle, generation)`;
- only that exact owner may mutate motion or renew liveness;
- **one 4 s manual total-silence lease**, with no second grace timer;
- exact-owner disconnect immediately commands zero and bypass;
- handle reuse/reconnect begins unowned with a newer generation;
- executor ownership is separate and survives RF loss only while all local
  safety inputs remain valid;
- console freshness comes only from a **complete valid parsed frame** and
  expires at **1.5 s**;
- due deadlines win over an operation arriving at the same timestamp;
- entry order is zero command → inverted-UART physical idle-low → TX enable →
  qualified gap → relay command → at least 1 ms continuously stable Emulate
  feedback sampled before 10 ms → first complete zero frame;
- entry aborts without moving K1 if no qualified gap arrives within 1 s;
- normal exit is complete zero frame → qualified gap → relay off → at least
  1 ms continuously stable bypass feedback sampled before 10 ms → TX off →
  owner release;
- the 1 s normal-exit gap deadline deasserts K1 immediately;
- TREAD_OK loss, stale console, lease expiry, emergency stop, brownout, reset,
  and WDT never wait for a gap;
- `BOTH_CLOSED` feedback is an immediate latched fault; boot feedback starts
  unknown; a timer without an actual GPIO sample never qualifies feedback.

GPIO7 is `VBUS_PRESENT_N`: LOW means VBUS present. Espressif's **stock self-powered TinyUSB VBUS-monitor input is active-high**. Do not pass GPIO7
directly to that API. Implement an explicit reviewed inversion/attach strategy
and verify D+ behavior at power-up, reset, ROM download, enumeration, and
unplug.

A physical STOP whose **encoded value was already zero** is not universally
detectable from a value-change parser unless captures establish a separate
wire event. The treadmill safety key remains authoritative.

## Production build identity

Every Emulate-capable build needs:

- exact ESP32-S3 target;
- 2 s task WDT subscribed to every task able to leave K1 energized;
- `CONFIG_ESP_TASK_WDT_PANIC=y`;
- immediate silent reboot and zero optional reboot delay;
- no panic halt, print-reboot, GDB/OpenOCD stub, OCD-aware halt, core dump, or
  apptrace delay;
- enabled brownout reset at the highest supported threshold below the measured
  minimum +3V3.

Generate and archive the manifest:

```bash
python3 hardware/Esp32Tap/firmware/build_safety_manifest.py \
  --application build/esp32tap.bin \
  --bootloader build/bootloader/bootloader.bin \
  --partition-table build/partition_table/partition-table.bin \
  --sdkconfig sdkconfig \
  --measured-min-3v3 <physical-volts> \
  --brownout-threshold <selected-documented-volts> \
  --output build/safety-manifest.json
```

Every bench record must name the emitted `bundle_sha256`.

## Reproduce before giving advice

From repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

For an explicit simulation run:

```bash
python3 hardware/Esp32Tap/sim/run_simulations.py \
  --host-ngspice /usr/bin/ngspice \
  --docker-image ngspice-cached:latest
```

The runner executes all seven decks three times on host ngspice 42 and pinned
offline Docker ngspice 39. Only manifest-listed numeric assertions may say
PASS. Every excluded phenomenon must remain `UNSUPPORTED`.

Immediately before vendor review, refresh the read-only stock evidence:

```bash
python3 hardware/Esp32Tap/tools/check_jlc_stock.py --refresh
```

Do not interpret public stock as a quote or assembly acceptance.

## Remaining gates Claude must keep open

- actual +8 V range, source impedance, current capacity, inrush, noise, surge,
  brownout, and load/thermal behavior;
- TPS54202 switching-loop stability, ripple, EMI, startup, and production
  capacitor derating;
- dead-board leakage and real serial voltage/edge/timing margin;
- K1 operation, release, bounce, welding, temperature, and contact-measured
  fault timing;
- 1,000 normal transitions with no MOT6 byte/frame splice;
- native USB reset/ROM attach, unplug below 3 ms, enumeration, and eye margin;
- production ESP-IDF implementation, security, WDT, brownout, radio
  coexistence, and complete safety matrix;
- exact-current JLC quote, DFM, stack/impedance, placement, THT service,
  substitution review, and antenna-overhang carrier drawing;
- enclosure material acceptance, physical plug/screw/mount fit, installed
  clearance, and RF range.

First treadmill contact is Proxy-only with relay energization compiled out.
Emulate contact is a separate later gate with the belt clear and the physical
safety key immediately accessible.

Advice to Claude in one sentence: preserve HOLD, reproduce the exact artifacts,
report model limits honestly, and ask the owner before any action that submits,
commits money, changes a vendor part, or exposes a treadmill to an
Emulate-capable build.
