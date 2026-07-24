# Esp32Tap Rev B — handoff and advice for Claude

**Status: HOLD. Do not submit an order, add the design to a production cart,
authorize substitutions, or pay. Do not connect an Emulate-capable build to
the treadmill.**

This is the concise continuation brief for the hardware under
`hardware/Esp32Tap/`. Repository checks can establish internal consistency;
they cannot close the production-firmware, physical-bench, or vendor gates.

## Fixed design decisions

- The only board power source is treadmill **+8 V on the inline serial
  cable**. J1 and J2 pass that rail through directly; the local branch is
  protected by F1, D1, and D3 before VIN.
- USB-C is **data and VBUS-presence only**. VBUS has no path to VIN, +5V_RLY,
  or +3V3. Programming therefore requires a USB data cable **and**
  current-limited +8 V bench power. USB alone cannot power or program Rev B.
- K1 is a 5 V, 237 ohm G6K-2F-Y relay. Its NC pole is the default console-to-
  motor bypass. Treadmill-derived TREAD_OK is hardware-ANDed with both relay
  and TX permission; firmware cannot override an unsafe VIN window.
- K1 pole B is an armature-state proxy: bypass is NC low / NO high; Emulate is
  NC high / NO low. It cannot prove pole A did not weld or that every contact
  transferred at the same instant.
- U7 makes the motor TX path high impedance unless TX_ENABLE and TREAD_OK are
  both true. Normal transitions use a captured inter-frame gap; emergency
  fallback never waits for a gap.
- The ESP32 antenna keepout is clear on every copper layer and the enclosure
  target is a 15 mm plastic/air void. RF performance under the motor hood
  still requires measurement.

`tools/design.py` is the electrical source of truth. Do not hand-edit a
generated netlist, schematic connection, BOM, or PCB to repair a source
problem.

## Firmware contract Claude must implement

`firmware/safety_model.py` is an executable **host reference**, not production
ESP-IDF code. Production behavior must match its tests:

- one owner tuple `(WSS or BLE transport, concrete handle, generation)`;
- only the exact owner mutates motion or renews liveness;
- one 4 s manual total-silence lease, with no second grace timer;
- exact owner disconnect immediately commands zero and bypass;
- reconnect/handle reuse starts unowned with a newer generation;
- the local executor owns separately and survives RF loss only while every
  local safety input remains valid;
- console freshness comes only from a complete valid parsed frame and expires
  at 1.5 s;
- entry order is zero → inverted-UART physical idle-low → TX enable → qualified
  gap → relay command → Emulate feedback within 10 ms → first complete zero
  frame;
- entry aborts without moving K1 if no gap arrives within 1 s;
- normal exit finishes a zero frame → qualified gap → relay off → bypass
  feedback within 10 ms → TX off → lease release;
- normal exit deasserts K1 at the 1 s gap deadline; TREAD_OK loss, stale
  console, lease expiry, emergency stop, brownout, reset, and WDT never wait;
- GPIO7 is `VBUS_PRESENT_N`: LOW permits native-USB attach, HIGH requires the
  D+ pull-up detached.

Espressif's stock self-powered TinyUSB VBUS-monitor input is active-high,
whereas GPIO7 is active-low. Do not pass GPIO7 directly to that API and claim
completion. Implement an explicit reviewed inversion/attach strategy, then
verify D+ at boot/reset, enumeration, and unplug on hardware.

A physical STOP whose encoded value was already zero is not universally
detectable from a value-change parser unless a distinct wire event is proven
in captures. The independent treadmill safety key remains authoritative.

## Production build identity

Every Emulate-capable build must use a 2 s task WDT with panic/reset, enabled
brownout reset at the highest supported threshold below the measured minimum
+3V3, and no panic-halt/GDB-stub mode. Preserve this exact gate:

```bash
grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig
```

Generate the deterministic evidence identity with:

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

The emitted `bundle_sha256` binds the application, bootloader, partition
table, sdkconfig, model, builder, schema, and this plan. Every bench record
must name it.

## Reproduce repository evidence

From the repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check

python3 hardware/Esp32Tap/sim/run_simulations.py \
  --host-ngspice /usr/bin/ngspice \
  --docker-image ngspice-cached:latest

git diff --check
```

The simulation runner must execute all seven decks on host ngspice 42 and the
cached Docker ngspice 39 image. Repeat it three times. A supported numeric
assertion may pass; an out-of-envelope pulse stays `UNSUPPORTED`. The
TPS54202 deck is an averaged behavioral model and does not prove loop margin,
switch-node ripple, EMI, or vendor-model startup.

## Evidence that remains physical or vendor-only

- actual +8 V range, source impedance/current capacity, inrush, noise, and
  brownout under Wi-Fi/BLE and relay load;
- TPS54202 switching-loop/EMI behavior and production capacitor derating;
- dead-board backfeed and real serial edge/timing margin;
- K1 operate/release/bounce, pole-A weld behavior, three-hour temperature,
  and actual-contact timing;
- at least 1,000 normal transitions with no MOT6 byte/frame splice;
- USB reset/ROM attach, unplug indication below 3 ms, enumeration, and eye
  margin;
- enclosure plug fit, mesh/material acceptance, RF range, and coexistence;
- exact current JLC stock/class, placement rotations, substitutions, carrier
  drawing for the antenna overhang, DFM preview, and stackup/impedance
  confirmation;
- production ESP-IDF implementation and the complete bench safety matrix.

The first treadmill contact remains Proxy-only with relay energization compiled
out. Emulate contact is a separate later gate with the belt clear and the
physical safety key immediately accessible.

Until all applicable repository checks pass, keep the package at HOLD. Even
after they pass, describe it only as ready for vendor and bench review—not as
permission to fabricate, pay, or operate.
