# Esp32Tap Rev D — handoff and advice for Claude

**Status: HOLD. Do not submit an order, add the design to a production cart,
authorize substitutions, or pay. Do not connect an Emulate-capable build to
the treadmill.**

Claude: preserve the distinction between repository evidence, vendor evidence,
and physical evidence. A passing model is not an assembled-treadmill result.

## Rev D facts that must not regress

- The finished board is **95.0 × 58.0 mm**, four layers, on the modeled
  `JLC04161H-7628` stack.
- U1 is fully inside the finished outline. Its locked body-to-edge margins are
  **3.25 mm and 3.30 mm**. Its stock manufacturer keepout forbids tracks,
  vias, pads, footprints, and zone fill on every copper layer.
- J1/J2 are the identical right-angle **SMT Molex 0441440003** (LCSC
  `C585890`) 8P8C RJ45 jack, edge-mounted with the mating opening facing off
  the board's X=0 edge. There is no separate pigtail harness and no
  mechanical keying between console and motor; CONSOLE/MOTOR silkscreen is
  the only differentiator.
- The treadmill cable's +8 V conductors are the board's only power source.
  USB-C is native USB data plus VBUS presence detection only. It cannot power
  VIN, +3V3, +5V_RLY, or K1. **USB alone cannot power or program Rev D.**
  Programming requires USB data and **current-limited +8 V bench power**.
- USB is not galvanically isolated. Measure host-to-treadmill ground potential
  and connection current and review bonding/isolation before simultaneous
  treadmill and host attachment.
- K1 pole A transfers CONS6/MOT6/TX_DRV; pole B reports armature state. Pole B
  cannot prove pole A is unwelded. `TREAD_OK` hardware-gates both
  `RELAY_CMD` and `TX_ENABLE`.

`tools/design.py` is the electrical source of truth. Change generated hardware
only through its generators; never hand-edit PCB nets, BOM/CPL rows, or
Gerbers.

## Exact repository evidence

- The exact 2 A PCB trace-union solve is **98.379966 mV** supply-plus-return:
  +8 V is 14.279315 mΩ and the conservative trace-only GND return is
  34.910668 mΩ.
- The +8 V solve's maximum via current is **0.828913 A**; its conservative
  IPC-2221 internal-barrel rise is **7.504156 °C** and I²R is
  **0.999735 mW**.
- Every intended GND stitching via is 1.4/1.0 mm. Because the In1 plane is not
  solved exactly, each is independently qualified at the full 2.0 A:
  **12.273573 °C** rise and **2.328020 mW** I²R using a conservative
  **20 µm** barrel. JLC's live quote/DFM must confirm IPC-6012 Class 2
  20 µm average hole copper; the repository does not prove delivered plating.
- USB shortest paths are:
  D− A/B = 60.0528786214/59.0528786214 mm and
  D+ A/B = 60.0528777233/59.0528777233 mm. Per-side D+/D− skew is
  0.0000008981 mm. Signals stay on F.Cu with zero signal vias; controlled
  sections use 0.2906 mm copper and 0.2000 mm edge gap.
- The simulation gate has **eight decks**. Each runs three times on host
  ngspice 42 and pinned offline Docker ngspice 39.
- The fabrication export has exactly 13 deterministic members, and the
  assembly audit binds design, schematic, PCB, BOM, CPL, DNP state, package,
  position, side, and rotation.

These are model and consistency results. They do not establish complete
installed voltage drop, temperature, vendor acceptance, or treadmill safety.

## Firmware contract

`firmware/safety_model.py` is an executable **host reference**, not production
firmware. Production behavior must preserve:

- one owner tuple `(transport, concrete handle, generation)`;
- only that owner may mutate motion or renew liveness;
- **one 4 s manual total-silence lease**, with no reconnect grace;
- complete valid parsed frame freshness at **1.5 s**;
- due deadlines before operations at the same timestamp;
- zero command → idle-low → TX enable → qualified gap → relay command →
  stable Emulate feedback → first complete zero frame;
- complete zero frame → qualified gap → relay off → stable bypass feedback →
  TX off → owner release;
- immediate fail-closed action for TREAD_OK loss, stale console, lease expiry,
  emergency stop, brownout, reset, WDT, or `BOTH_CLOSED`;
- feedback qualification from real continuously stable GPIO samples, not a
  timer alone.

GPIO7 is `VBUS_PRESENT_N`: LOW means VBUS present.
Espressif's stock self-powered TinyUSB VBUS-monitor input is active-high;
production firmware needs a reviewed inversion/attach strategy. A physical
STOP whose encoded value was already zero is not universally detectable from a
value-change parser unless captures establish a separate wire event.

Every Emulate-capable ESP-IDF build needs the exact ESP32-S3 target, a 2 s
panic WDT subscribed to every task that can leave K1 energized, immediate
silent reboot, no debug halt/delay path, and an enabled brownout threshold
below the measured minimum +3V3. Archive the resulting `bundle_sha256`.

## Reproduce before advice

From repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

The eight simulations run with:

```bash
python3 hardware/Esp32Tap/sim/run_simulations.py \
  --host-ngspice /usr/bin/ngspice \
  --docker-image ngspice-cached:latest
```

Only manifest-listed numeric assertions may say PASS. The exact unsupported
harness list is: `RJ45_SINGLE_OPEN_2A`, `MINIMUM_VIN`, `SOURCE_IMPEDANCE`,
`AMBIENT_THERMAL`, `TRANSIENT_RESPONSE`, `COMPLETE_INSTALLED_DROP`,
`USB_RETURN_CURRENT`, `ESD`, `RF`, and `SWITCHING_LOOP`.

## Gates that remain open

- a live JLC review of the **current exact** ZIP, BOM, CPL, and placement,
  including Standard PCBA placement confirmation for the Extended-class RJ45
  jacks;
- confirmation of the selected stack/impedance and 20 µm Class 2 hole copper;
- J3's four plated mechanical stakes under normal top-side reflow—no
  manual/wave operation is requested;
- panel carrier/rail, tooling, fiducial, tab, and delivered-edge details;
- enclosure quote, material, physical fit, installed clearance, and RF range
  for the J1/J2 RJ45 wall apertures;
- treadmill +8 V/VIN/source-impedance/transient/load/thermal measurements;
- RJ45 single-open 2 A qualification and complete installed loop drop;
- relay contact timing/bounce/weld/temperature and 1,000 splice-free transfers;
- native USB ROM/reset attach, enumeration, unplug, eye margin, and ground
  current;
- production firmware, security, WDT/brownout evidence, and safety matrix.

The existing operator-observed JLCDFM JSON is bound to an older exact archive.
Preserve it as historical evidence; do not relabel it as review of Rev D's
current bytes. First treadmill contact remains Proxy-only with relay
energization compiled out.

Advice in one sentence: preserve HOLD, reproduce the current artifacts, state
model limits exactly, and obtain owner approval before any action that spends
money, changes a part, submits vendor files, or exposes a treadmill to
Emulate-capable hardware.
