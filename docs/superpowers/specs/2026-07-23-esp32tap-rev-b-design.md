# Esp32Tap Rev B remediation design

**Date:** 2026-07-23

**Status:** User-approved architecture; implementation and independent review
required before fabrication

**Issue:** `precor-9_3x-e4z`

## Purpose

Rev B replaces the staged Rev A package. It keeps the normally closed,
power-independent treadmill bypass, removes USB as a power source, and adds a
treadmill-voltage interlock that gates both the relay and the ESP transmit
path in hardware. It also repairs the power supply, relay drive, USB layout,
enclosure, firmware safety contract, and generated manufacturing package.

This is a practical fail-safe verification design. It is not a certified
functional-safety system, a redundant safety controller, or a substitute for
the treadmill's safety key. In particular, the second pole of K1 reports the
relay armature position but cannot prove that the signal-transfer pole is not
welded.

No fabrication order, cart submission, or payment is authorized by this
design. JLC account access may be used for read-only DFM, stock, and quote
checks. The owner makes the final order decision.

## Fixed decisions

- The treadmill serial cable's nominal +8 V rail is the board's only power
  source.
- USB-C is data-only. Connecting USB without treadmill or current-limited
  bench +8 V does not power the board and is not a supported programming
  mode.
- Both RJ45 connectors retain direct, unfused pass-throughs for both +8 V
  conductors and both ground conductors. The local electronics branch is
  fused; the board does not insert a fuse into the treadmill's through path.
- With no power, during reset, or on a hardware permission fault, K1 is
  de-energized and its normally closed contact joins console pin 6 to motor
  pin 6.
- K1 uses one pole for the serial transfer and one pole as dry-contact
  armature feedback. The poles are not paralleled.
- The PCB remains 100 mm × 55 mm but becomes a four-layer board.
- The enclosure accepts ordinary unbooted or slim-boot RJ45 cables. Universal
  compatibility with arbitrary oversize molded boots is not claimed.

## System architecture

```text
J1/J2 +8 V pass-through
        |
        +-- F1 -- D1 -- VIN -- TPS54202 --------------------- +3V3
                         |          |                            |
                         |          +-- ESP32-S3                +-- U4 window monitor
                         |                                       +-- U6 hardware AND gates
                         +-- D3 TVS                              +-- U7 tri-state TX buffer
                         |
                         +-- U5 5 V LDO, EN=RELAY_GATE -- K1 coil

VIN --> U4 undervoltage + overvoltage window --> TREAD_OK

RELAY_CMD  AND TREAD_OK --> Q1 --> K1
TX_ENABLE  AND TREAD_OK --> U7.OE

K1 pole A: CONS6 -- NC/COM -- MOT6, or TX_DRV -- NO/COM -- MOT6
K1 pole B: GND -- NC/COM or NO/COM --> two pulled-up feedback inputs
```

The safety invariant is:

```text
K1 energized = RELAY_CMD AND TREAD_OK AND powered hardware
TX driven     = TX_ENABLE AND TREAD_OK AND powered hardware
```

Firmware can request either action but cannot bypass `TREAD_OK`.

## Power input and 3.3 V converter

### Input topology

The local branch is:

```text
+8V_RAW -> F1 -> +8V_F -> D1 -> VIN
VIN -> D3 -> GND
VIN -> C1, C2, C3, C4, U2, U5, and both U4 sense dividers
```

- F1 remains the 0.75 A, 16 V resettable fuse.
- D1 remains the SS34 series Schottky and provides reverse-polarity
  protection. All local bulk capacitance and active circuitry move to its
  protected `VIN` side.
- D2 and every USB-to-VIN connection are deleted.
- D3 changes from SMBJ12A to Littelfuse SMBJ10A, LCSC `C151250`. Its 10 V
  stand-off, 12.3 V breakdown, and 17 V maximum clamp preserve margin to the
  18 V TPS3700 limit and the 25 V capacitors. U4 itself is powered from
  +3V3, not VIN, so its supply never sees the clamped input transient.
- The TVS and input ceramic return have a short, low-inductance path to the
  ground plane. F1, D1, and D3 are placed before the long branch to the
  converter.

The supervisor intentionally disables Emulate before D3 conducts. D3 is a
surge limiter, not the normal overvoltage threshold.

### TPS54202 corrections

U2 remains TPS54202DDCR with its existing 100 kΩ / 22 kΩ feedback divider,
10 µH inductor, 100 nF bootstrap capacitor, and divided EN input.

The capacitor network changes to:

| Ref | Requirement | Selected assembly part |
|---|---|---|
| C1 | 100 µF, 25 V electrolytic | Existing `C72477` |
| C2, C3 | 10 µF, 25 V, X7R, 1206 | Samsung `C14860` |
| C4 | 100 nF ceramic at U2 VIN | Existing `C14663` |
| C6, C7 | 22 µF, 25 V, X7R, 1210 | Samwha `C2918511` |
| C12 | 56 pF, C0G, 0603, across R1 | Samsung `C39148` |

C2 and C3 are both on VIN and are placed close to U2, with C4 closest to the
VIN/GND pins. C6/C7 and their ground vias are close to L1/U2. The feedback
node is Kelvin-routed away from SW; C12 is directly across R1.

This matches TI's current 3.3 V starting point of 10 µH, 44 µF, 100 kΩ,
22.1 kΩ, and 56 pF. DC-bias curves and an averaged model can bound
capacitance and load-step energy, but they do not prove the encrypted
TPS54202 switching loop. Startup, ripple, temperature, and loop stability
remain bench gates.

## Treadmill-voltage permission

U4 is TPS3700DDCR, LCSC `C33002`, powered from +3V3 and decoupled with 100 nF.
Its two open-drain outputs are tied together as `TREAD_OK`.

| Function | U4 input | Top resistor | Bottom resistor | Filter |
|---|---|---:|---:|---:|
| Undervoltage | INA+ | 150 kΩ, 1%, `C22807` | 10 kΩ, 1%, `C25804` | 1 nF C0G |
| Overvoltage | INB- | 255 kΩ, 1%, `C23354` | 10 kΩ, 1%, `C25804` | 1 nF C0G |

The 1 nF filters use `C342541` and produce about a 10 µs divider time
constant. `TREAD_OK` has a 10 kΩ pull-up to +3V3 and a 100 kΩ pull-down to
ground. The pull-down makes the permission low while +3V3 is absent; it is
not claimed as protection against an internally failed supervisor.

Nominal VIN boundaries are 6.40 V undervoltage release and 10.60 V
overvoltage trip. The following guaranteed ranges include TPS3700 threshold
limits, 1% resistor limits, and worst-case input bias:

| Transition at protected VIN | Earliest corner | Latest corner |
|---|---:|---:|
| UV recovery, VIN rising | 6.215 V | 6.590 V |
| UV fault, VIN falling | 6.073 V | 6.525 V |
| OV fault, VIN rising | 10.290 V | 10.918 V |
| OV recovery, VIN falling | 10.056 V | 10.810 V |

These are protected-VIN thresholds after D1, not RJ45 rail thresholds.
D1 forward voltage, cable drop, temperature, and source impedance must be
included when the actual treadmill rail is characterized.

U4 has up to 450 µs startup delay and tens of microseconds of propagation
delay. Until +3V3 and U4 are valid, the pull-down keeps `TREAD_OK` false.

## Relay supply, drive, and feedback

### Coil supply and relay

K1 changes to Omron `G6K-2F-Y-TR DC5`, LCSC `C47190`, retaining the existing
footprint and terminal arrangement. The standard 5 V coil is 237 Ω ±10%,
21.1 mA nominal, and 100 mW nominal.

U5 is TPS70950DBVR, LCSC `C96028`:

- pin 1 IN = VIN;
- pin 2 GND = GND;
- pin 3 EN = RELAY_GATE;
- pin 4 NC is marked no-connect;
- pin 5 OUT = +5V_RLY.

U5 uses 1 µF, 25 V X7R input capacitance (`C106858`) and 4.7 µF, 25 V X7R
output capacitance (`C354262`). The 4.7 µF nominal part must retain at least
2.2 µF effective capacitance at 5 V. The coil's worst resistance corner is
well below U5's 150 mA rated output. At the lowest guaranteed UV fault
corner, VIN still provides more than 1 V of headroom over the 5 V output,
versus TPS70950's 500 mV maximum dropout at 50 mA.

### Hardware relay gate

ESP GPIO21 is renamed `RELAY_CMD` and gets a 100 kΩ pull-down. One channel of
U6, SN74LVC2G08DCTR (`C352973`), computes:

```text
RELAY_GATE = RELAY_CMD AND TREAD_OK
```

`RELAY_GATE` enables U5 and drives Q1 through the existing 1 kΩ base
resistor.
resistor. It also gets its own 10 kΩ pull-down, placed at U5.EN, because the
TPS709 EN pin defaults enabled if allowed to float. The existing 10 kΩ
base-to-ground resistor remains. This makes U5 and Q1 series control elements: a Q1
collector-emitter short is still blocked by disabled U5, and a failed-on U5
is still blocked by Q1. U6 is powered from +3V3, decoupled locally, and has
partial-power-down `Ioff` behavior.

Q1 changes to Nexperia BC817-40,215, LCSC `C52801`, with 45 V VCEO and the
same SOT-23 B/E/C pad assignment. Nominal base current is approximately
2.4 mA; even with a conservative 2.3 V U6 output it remains approximately
1.3 mA. Against the 18.8 mA to 24.0 mA coil-current corners this is a forced
beta of approximately 10 nominal and 18 at the conservative logic-output
bound. Worst specified Q1 saturation drop must still leave at least the
relay's 4.0 V must-operate voltage.

The Rev A ordinary flyback diode is replaced because a low-voltage diode
clamp can lengthen magnetic release. D4 becomes Littelfuse SMAJ6.0CA, LCSC
`C80275`, connected directly across the coil. It is bidirectional, has 6 V
stand-off, 6.67 V to 7.37 V breakdown, and a 10.3 V absolute clamp rating.
During release it applies reverse voltage locally across the coil while the
collector remains below approximately 5.11 V + 10.3 V, with wide margin to
Q1's 45 V rating. A shorted D4 shunts the coil and therefore prevents relay
operation instead of holding it energized. The coil's stored energy and
current-decay envelope are simulated across a documented inductance sweep.
Actual contact release and bounce are still measured on the assembled board.

With a constant-voltage approximation, the direct clamp reaches zero current
in 0.518 to 0.559 `L/R`, versus approximately 2.10 `L/R` for an ordinary
flyback diode. At 24 mA, even the TVS's overly conservative 10.3 V absolute
clamp implies only 0.247 W during decay, versus its 400 W 10/1000 µs pulse
rating. These calculations establish electrical stress and relative decay,
not contact timing. An open D4 can expose Q1 to avalanche and remains a
single-component limitation.

If Q1 is already collector-emitter shorted when `RELAY_GATE` falls, C16 can
discharge through the coil. Its nominal electrical time constant is about
1.1 ms (237 Ω × 4.7 µF) before capacitance derating, followed by mechanical
release. This single-fault tail is included in simulation and contact-timing
bench tests. A common `RELAY_GATE` or U6 stuck-high fault can still energize
both series elements and is an explicit limit of this practical design.

### Contact allocation

K1 pole A is the only signal-transfer pole:

- pin 3 COM_A = MOT6;
- pin 2 NC_A = CONS6;
- pin 4 NO_A = TX_DRV.

K1 pole B is dry armature feedback:

- pin 6 COM_B = GND;
- pin 7 NC_B = K1_NC_FB, pulled up 10 kΩ to +3V3 and read on GPIO4;
- pin 5 NO_B = K1_NO_FB, pulled up 10 kΩ to +3V3 and read on GPIO5.

Expected stable feedback is:

| Mechanical state | K1_NC_FB | K1_NO_FB |
|---|---:|---:|
| Bypass/de-energized | 0 | 1 |
| Emulate/energized | 1 | 0 |
| In transition, both contacts open | 1 | 1 |
| Invalid/fault indication | 0 | 0 |

Firmware allows 10 ms after a requested transition for a stable expected
state. A mismatch or impossible state latches a fault and requests bypass.
Feedback is an armature proxy. A welded pole-A NO contact can coexist with
apparently correct pole-B feedback, so this is diagnostics and transition
checking rather than certified contact monitoring.

Each closed feedback contact carries approximately 330 µA through its 10 kΩ
pull-up. That is above Omron's published 10 µA at 10 mV P-level reference
load while remaining negligible for the GPIO and contact ratings.

## Transmit isolation

ESP GPIO15 is `TX_ENABLE` and gets a 100 kΩ pull-down. U6's second channel
computes:

```text
TX_GATE = TX_ENABLE AND TREAD_OK
```

U7 is SN74LVC1G126DBVR (`C7834`), powered from +3V3:

- pin 1 OE = TX_GATE, with an additional 100 kΩ pull-down;
- pin 2 A = ESP_TX / GPIO17;
- pin 3 GND = GND;
- pin 4 Y = TX_BUF;
- pin 5 VCC = +3V3.

The existing 100 Ω R6 moves between TX_BUF and TX_DRV. U7 has `Ioff` and
guarantees a high-impedance output when unpowered. On normal Emulate entry,
firmware starts valid zero-speed frames, enables TX, verifies readiness, and
only then energizes K1. On exit it de-energizes K1 first and disables TX
after feedback reports bypass.

## USB data-only interface

VBUS connects only to the USB connector, U3's VBUS reference, a 100 nF
decoupling capacitor, a 10 kΩ discharge resistor, and Q2's gate. It has no
connection to VIN, +5V_RLY, or +3V3.

Q2 is 2N7002, LCSC `C8545`:

- gate = VBUS;
- source = GND;
- drain = `VBUS_PRESENT_N`;
- drain has a 10 kΩ pull-up to +3V3 and connects to GPIO7.

This active-low detector does not apply VBUS to an unpowered ESP GPIO.
At 4.4 V VBUS the selected MOSFET is above its 2.5 V maximum threshold; only
approximately 330 µA must be sunk. At 5.5 V the 10 kΩ VBUS discharge load is
0.55 mA. With 100 nF at the connector, worst-case discharge and MOSFET
threshold corners must report unplug within 3 ms.

R15 and R16 add 22 Ω series footprints in D- and D+ respectively, immediately
adjacent to U1. C13 and C14 are no-load 0603 shunt-capacitor tuning footprints
at the MCU side. U3 remains at the connector. The route is:

```text
J3 -> U3 -> coupled USB pair -> R15/R16 -> U1 GPIO19/GPIO20
```

The firmware controls native-USB attach using `VBUS_PRESENT_N`. It never
advertises a pull-up while VBUS is absent. Programming requires both a USB
cable and current-limited 8 V bench power.

GPIO0 also gains an external 10 kΩ pull-up to +3V3. SW2 continues to pull it
to ground; no large capacitor is placed on GPIO0.

## UART taps and unpowered behavior

R7 and R8 increase from 4.7 kΩ to 10 kΩ between the cable-side ESD clamps and
the ESP inputs. At a 3.3 V high level and a conservative 0.6 V unpowered
input clamp, this bounds first-order injection to 0.27 mA per asserted input
instead of Rev A's approximately 0.57 mA. The added RC delay remains small
relative to a 104.17 µs bit at 9600 baud and is included in the line-corner
simulation.

D5 and D7 remain bidirectional ground-referenced ESD devices on the cable
side of R7/R8. D6 remains on MOT6. No protection device on these nets is
referenced to an unpowered +3V3 rail. Exact dead-board clamp current and
through-path edge degradation remain bench measurements.

## GPIO assignment

| GPIO | Rev B function | Boot default |
|---:|---|---|
| 4 | K1_NC_FB | input, external pull-up |
| 5 | K1_NO_FB | input, external pull-up |
| 6 | TREAD_OK telemetry | input |
| 7 | VBUS_PRESENT_N | input, external pull-up |
| 15 | TX_ENABLE | output low, external pull-down |
| 16 | PIN3_RX | input |
| 17 | ESP_TX | UART output isolated by U7 |
| 18 | CONS_RX | input |
| 21 | RELAY_CMD | output low, external pull-down |
| 38 | STATUS_LED | output |
| 0 | BOOT | external pull-up, switch to GND |

GPIO0, GPIO3, GPIO45, and GPIO46 are the ESP32-S3 strapping pins; new safety
outputs do not use them.

## Four-layer PCB and USB constraints

The selected fabrication basis is JLCPCB
`JLC04161H-7628 (Standard/Finished thickness 1.59 mm ±10%)`: 1 oz outer
copper, 0.5 oz inner copper, 0.2104 mm 7628 RC49% prepreg at Dk 4.4 from
L1 to L2, a 1.065 mm NP-155F core at Dk 4.38, and the symmetric lower
prepreg. Layer use is:

- L1/F.Cu: components, USB, critical power loops, and most signals;
- L2/In1.Cu: uninterrupted ground plane, except the all-layer module-antenna
  keepout;
- L3/In2.Cu: +3V3 islands and slow power/signal routing, with no copper in the
  antenna keepout;
- L4/B.Cu: slow signals and local ground fill.

The pair uses 0.285 mm artwork width and 0.200 mm edge-to-edge gap with
solder mask. JLC's live calculator returned 89.9788 Ω differential for the
rounded geometry; its inverse solution for 90 Ω was 0.284829 mm artwork
width. The calculation uses JLC's non-coplanar
`DiffEdgeCoupledCoatedMicrostrip1B` structure. Same-layer copper stays at
least 0.8 mm from the pair.

This geometry is valid only for the named stackup. The order specifies 90 Ω
differential, ±10%, L1 referenced to L2. Any stackup substitution or vendor
width compensation requires a new calculation and written acceptance before
release.

USB requirements:

- D+ and D- remain together on F.Cu over continuous L2 ground;
- no signal vias or layer changes;
- equal topology through U3 and the two series resistors;
- length mismatch no greater than 0.5 mm;
- no stubs other than the short DNP capacitor pads;
- no plane split, antipad, high-current loop, or connector shield void below
  the pair;
- pair geometry is encoded in a named KiCad netclass/rule;
- final values are checked in JLC's order-time impedance tool.

All decoupling capacitors get adjacent ground vias. The U2 hot loop and SW
copper are compact and kept away from U1, USB, FB, U4 inputs, and UART taps.
The L2 ground plane is not cut by routed traces.

Silkscreen reference, polarity, pin-1, connector-side, and bypass/emulate
markings use at least 0.20 mm stroke and approximately 1.0 mm character
height. Gerber checks, not only PCB-source checks, verify legibility and
polarity.

The existing ESP32 module overhang and assembly-edge clearance still require
written JLC assembly-engineering approval and the actual production carrier
drawing. A repository drawing cannot close that vendor gate.

## Enclosure and RF

The enclosure source changes in two independent ways:

1. `ant_air_gap` increases from 3.0 mm to 15.0 mm, growing the antenna end of
   the enclosure by 12 mm while retaining the 100 mm × 55 mm PCB.
2. The Ø7 mm lid-post centers move 0.25 mm into the walls, producing positive
   overlap rather than tangent, non-manifold geometry:

```scad
post_d = 7.0;
post_wall_overlap = 0.25;
post_inset = post_d / 2 - post_wall_overlap;
```

The corrected RJ45 body centers remain 12.445 mm and 41.445 mm. The lid relief
diameter is `post_d + 0.6`. Both checked-in STLs are regenerated from the
corrected SCAD.

Plastic is required around the PCB antenna; conductive coatings, metal-filled
resin, and mounting hardware in the antenna keepout are prohibited. The
15 mm lateral void reduces enclosure interaction but does not close RF
performance. Final enclosure material, motor-hood placement, Wi-Fi/BLE RSSI,
throughput, and 24-hour coexistence are physical gates.

The base and lid must each be one watertight, winding-consistent valid volume,
with every edge incident to exactly two faces. The PCB-fit validator also
checks connector apertures, boss clearance, wall overlap, and the antenna
void. A real plug-fit test uses the intended unbooted/slim-boot cable before
the enclosure is accepted.

## Firmware safety contract

The hardware defaults are necessary but do not replace firmware state
ownership.

### Control lease

The mode engine owns one atomic lease:

```text
(source, concrete_connection_id, generation, expires_at_monotonic)
```

Only the current owner can renew it or issue motion mutations. WSS ownership
uses the actual connection object identity plus generation; BLE ownership uses
the concrete `conn_handle` plus generation. A recycled handle or a second
client cannot inherit or refresh an old lease.

- Explicit owner disconnect: immediately command zero, de-energize K1, and
  release the lease.
- Owner silence: one 4 s total-silence deadline, with no separate 10 s grace.
  Expiry commands zero and bypass.
- Non-owner traffic and heartbeats do not affect owner liveness.
- Reconnect starts unowned at zero and must explicitly acquire a new lease.
- An on-device executor owns its own lease and is supervised locally; network
  loss does not silently convert a manual lease into an executor lease.

### Console freshness and relay sequence

A monotonic timestamp tracks the most recent valid, fully parsed console
frame. Emulate entry requires a known baseline no older than 1.5 s. During
Emulate, stale/corrupt/absent console input for 1.5 s commands zero and bypass.
The threshold may change only through a recorded safety review backed by
capture statistics.

Entry order is:

1. verify TREAD_OK, bypass feedback, fresh console baseline, and no latched
   fault;
2. set speed and incline to zero;
3. begin transmitting valid zero frames and assert TX_ENABLE;
4. assert RELAY_CMD;
5. require energized feedback within 10 ms or release and latch fault.

Exit order is:

1. command zero;
2. clear RELAY_CMD;
3. require bypass feedback within 10 ms;
4. clear TX_ENABLE;
5. release the control lease.

The physical console STOP button is not claimed as universally detectable
when its encoded value was already zero unless a distinct wire event is
demonstrated. The independent safety key remains the authoritative physical
stop mechanism.

### Production watchdog and artifact identity

Every task whose stall can leave K1 energized is subscribed to the task WDT.
The production configuration requires task-WDT initialization, a 2 s timeout,
and panic/reset on timeout. Brownout reset is enabled at the highest supported
ESP-IDF threshold that remains below the measured minimum +3V3 rail. GDB stub,
panic halt, and debug configurations that can leave execution stopped with K1
energized are forbidden in an Emulate-capable build.

The flashed application binary, bootloader, partition table, `sdkconfig`, and
a machine-readable safety manifest are hashed together. Bench evidence names
that exact hash.

Acceptance latencies are:

- hardware TREAD_OK fault to stable bypass feedback: at most 10 ms;
- explicit disconnect or lease-expiry action to stable bypass feedback: at
  most 250 ms after the corresponding software event/deadline;
- injected supervised-task stall to stable bypass feedback: at most 2.25 s
  with the 2 s production WDT.

These values are measured at K1's contacts on the production artifact. GPIO
transitions alone are insufficient evidence.

## Simulation strategy

Local ngspice 42 and the cached Docker ngspice 39 image both run the committed
decks. The runner fails if their measured assertions disagree beyond declared
numeric tolerances.

Committed simulations cover:

- VIN ramp, UV/OV entry and recovery, threshold component corners, input
  filters, supervisor delay, TREAD_OK, and U5 enable/disable;
- all combinations of TREAD_OK, RELAY_CMD, TX_ENABLE, +3V3 present/absent,
  and default pull resistors;
- K1 coil current and Q1/clamp stress across coil resistance, drive,
  temperature proxy, clamp tolerance, and a documented inductance sweep;
- comparison of ordinary-diode and fast-release clamp current decay, without
  treating current decay as proof of contact motion;
- VBUS hot-plug/unplug, 100 nF and resistor tolerances, Q2 threshold corners,
  no unpowered-GPIO path, and a less-than-3-ms unplug indication;
- TPS54202 averaged input/output energy, source impedance, capacitor
  derating, startup load, and Wi-Fi-like load steps;
- UART tap RC timing and unpowered injection-current bounds;
- machine-readable safety truth-table assertions.

TI's public TPS54202 PSpice model is encrypted and is not treated as ngspice
compatible. The averaged deck must be labeled accordingly and cannot close
switching-loop phase margin, ripple, EMI, or physical startup. Relay contact
motion, RF, USB eye margin, and real treadmill behavior likewise remain bench
or vendor measurements.

Every deck records model assumptions beside its assertions. A passing
behavioral model means the specified topology satisfies those assumptions; it
does not certify the product.

## Generated artifacts and validation

`hardware/Esp32Tap/tools/design.py` remains the electrical source of truth.
Generators produce the schematic, PCB, NETLIST, BOM, CPL, reports, Gerbers,
drill files, and archive. Enclosure STLs are generated from the SCAD source.

Repository-closeable gates are:

- design-table validation, schematic/PCB net parity, and pin/footprint audits;
- KiCad ERC and DRC with zero unwaived errors;
- four-layer count, L2 plane continuity, antenna keepout, USB topology,
  width/gap, length, via-count, and reference-plane checks;
- BOM/CPL parity, exact MPN/LCSC/package checks, current stock snapshot, and
  explicit DNP exclusion;
- Gerber/drill archive contents, polarity/orientation, silkscreen, and board
  dimensions;
- both ngspice versions passing all asserted corners;
- OpenSCAD `Simple: yes` plus independent manifold/volume/fit checks;
- firmware host tests for owner/non-owner races, disconnect, silence expiry,
  handle reuse, console freshness, relay feedback faults, watchdog manifest,
  and every safety-matrix cell;
- documentation containing no stale Rev A “GO” or “order-ready” claim.

Vendor- or bench-only gates are:

- written JLC carrier/overhang approval, final DFM preview, exact stackup,
  controlled-impedance confirmation, part substitutions, and placement
  rotations;
- actual +8 V rail range/source capacity/noise, D1 drop, brownout, and surge
  behavior;
- USB enumeration and hot-unplug across hosts and cables, VBUS indication,
  and eye/TDR margin if needed;
- dead-board backfeed and stock serial signal integrity;
- K1 operate/release/bounce, transfer behavior, three-hour temperature, and
  trigger-to-NC timing on the production artifact;
- enclosure cable fit, RF performance, and Wi-Fi/BLE coexistence;
- proxy-only first treadmill contact, followed by separately gated Emulate
  contact with the belt clear and safety key immediately accessible.

Until every repository-closeable gate passes, all generated order documents
say **HOLD**. Passing repository checks changes the status only to
**READY FOR VENDOR AND BENCH GATES**, never directly to production-ready.

## Primary references

- [TI TPS3700 datasheet](https://www.ti.com/lit/ds/symlink/tps3700.pdf)
- [TI TPS709 datasheet](https://www.ti.com/lit/ds/symlink/tps709.pdf)
- [TI TPS54202 datasheet](https://www.ti.com/lit/ds/symlink/tps54202.pdf)
- [TI SN74LVC2G08 datasheet](https://www.ti.com/lit/ds/symlink/sn74lvc2g08.pdf)
- [TI SN74LVC1G126 datasheet](https://www.ti.com/lit/ds/symlink/sn74lvc1g126.pdf)
- [Omron G6K datasheet](https://components.omron.com/system/files/2026-06/datasheet_pdf/K106-E1.pdf)
- [Nexperia BC817 series datasheet](https://assets.nexperia.com/documents/data-sheet/BC817_SER.pdf)
- [Littelfuse SMAJ series datasheet](https://www.littelfuse.com/~/media/electronics/datasheets/tvs_diodes/littelfuse_tvs_diode_smaj_datasheet.pdf.pdf)
- [Espressif ESP32-S3 hardware design guidelines](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/esp-hardware-design-guidelines-en-master-esp32s3.pdf)
- [ESP32-S3 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf)
- [JLCPCB impedance calculator](https://jlcpcb.com/pcb-impedance-calculator/)
- [JLCPCB standard stackups](https://jlcpcb.com/help/article/multi-layer-pcb-standard-laminated-structures)

Assembly stock recorded in this document is a 2026-07-23 snapshot and must be
re-queried immediately before any quote is accepted.
