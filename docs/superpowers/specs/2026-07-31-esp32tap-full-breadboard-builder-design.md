# Esp32Tap Full-Breadboard Builder Design

**Issue:** `precor-9_3x-1y2.2`  
**Target:** owned ESP32-S3 DevKitC-1 N8R8, two all-pin RJ45 breakouts, and the
owned DigiKey prototype parts  
**Operator decision:** all eight treadmill conductors pass through the
solderless breadboard; this intentionally replaces the previously proposed
hybrid harness

## Purpose and boundary

Replace the obsolete jumper-input simulator with a browser-based construction
and bring-up guide for the actual relay interface. The guide takes the operator
from loose parts to a normally closed treadmill bypass, then to passive UART
observation, and finally to guarded relay transfer when separately qualified
functional firmware is available.

The guide is an assembly and evidence tool, not a declaration that a generic
breadboard is suitable for an unknown current. The owner reports that the prior
version carried this treadmill connection through a breadboard without a
high-current problem. This design preserves that architecture but makes the
claim measurable: the operator must prove continuity before power, measure
total pass-through current and voltage drop under the actual load, and measure
connector temperature before relay transfer is allowed. The prototype policy
limits measured treadmill pass-through current to 500 mA; a larger result
stops this full-breadboard path and requires a separately designed harness.

The currently flashed `devkit-bringup` image remains a no-control diagnostic
image. The browser must label it accordingly and must not present a successful
diagnostic boot as permission to energize the relay or transmit to the motor.

## Considered approaches

1. **Full breadboard, selected.** Both RJ45 breakouts, all eight pass-through
   conductors, relay, protection, power conversion, logic, and DevKit jumpers
   terminate on the breadboard. This matches the owner's proven earlier setup
   and makes the whole prototype visible in one construction view. Its cost is
   that the breadboard's unknown contact resistance must be qualified on this
   exact build.
2. **Hybrid high-current harness.** Power and ground bypass the breadboard
   while signals and control remain on it. This is electrically more
   conservative but was rejected by the owner because the observed load is low
   and the previous version worked through the breadboard.
3. **Relay exerciser only.** Build the relay driver without treadmill
   pass-through or UART taps. This reduces first-step scope but creates a
   disposable intermediate assembly and does not meet the request for the full
   interface.

## Physical architecture

The DevKit sits beside the breadboard so every printed header label remains
visible. The two RJ45 breakouts are labeled `CONSOLE` and `MOTOR`; their
orientation is not trusted. Before any other wiring, the wizard has the
operator map breakout terminal labels to plug contacts 1 through 8 using a
known patch cable and continuity mode.

The G5V-2 relay straddles the breadboard center trench. Its case orientation
mark and the Omron **bottom-view** numbering are both shown. The wizard still
requires an unpowered meter check because a top-view drawing reverses a
bottom-view pin diagram. For the owned standard G5V-2 DC5:

| Function | Relay pins |
|---|---|
| Coil, no polarity | 1 and 16 |
| Pole A | NC 4, COM 6, NO 8 |
| Pole B | NC 13, COM 11, NO 9 |

The relay coil is 5 V, 50 ohms, and 100 mA. This is materially different from
the lower-current G6K relay on the production PCB, so the production relay
driver values cannot be copied without measurements.

The browser targets one full-size, at-least-830-tie-point solderless breadboard
with a center trench and split power rails. The operator records its maker or
listing, a photograph, and the measured rail-break locations; no generic rail
continuity is assumed. The browser uses a conventional numbered breadboard coordinate system and a
persistent top-view drawing. Every step adds exactly one component or one
wire. It always displays both endpoint names and physical hole coordinates.
Parts with polarity or orientation receive a close-up inset. The final drawing
is generated from the same structured component and net data as the tabular
netlist, so the picture and netlist cannot silently diverge.

## Treadmill pass-through and serial transfer

The full eight-conductor path is:

| RJ45 contact | Breadboard behavior |
|---:|---|
| 1 | `CONSOLE.1` to `MOTOR.1`, ground pass-through |
| 2 | `CONSOLE.2` to `MOTOR.2`, +8 V pass-through and local fused tap |
| 3 | `CONSOLE.3` to `MOTOR.3`, direct pass-through plus 10 kOhm receive tap to GPIO16 |
| 4 | `CONSOLE.4` to `MOTOR.4`, direct pass-through |
| 5 | `CONSOLE.5` to `MOTOR.5`, direct safety pass-through |
| 6 | `CONSOLE.6` to relay pole-A NC; pole-A COM to `MOTOR.6`; pole-A NO to isolated ESP TX |
| 7 | `CONSOLE.7` to `MOTOR.7`, second ground pass-through |
| 8 | `CONSOLE.8` to `MOTOR.8`, second +8 V pass-through |

Pins 1 and 7 remain two separately wired ground paths, and pins 2 and 8 remain
two separately wired +8 V paths. Each first uses its own isolated five-hole
groups from console breakout to motor breakout, so the disconnected assembly
allows every conductor to be proved independently. Only after those checks,
two final links join the pin-1 and pin-7 paths to `GND`, and two final links
join pin-2 and pin-8 paths to `+8V_RAW`, matching the production netlist. The
fused local electronics tap starts at `+8V_RAW`.

Relay pole A is fail-safe: with no coil power, `CONSOLE.6` is connected to
`MOTOR.6`. Energizing the relay disconnects the console transmitter and joins
the buffered ESP transmitter to `MOTOR.6`. The console line remains connected
to the ESP receive tap through 10 kOhm in both states.

Relay pole B is dry feedback. COM pin 11 goes to ground, NC pin 13 goes to
GPIO4 with a 10 kOhm pull-up, and NO pin 9 goes to GPIO5 with a 10 kOhm
pull-up. The expected states are `(GPIO4, GPIO5) = (0, 1)` in bypass and
`(1, 0)` in transfer. `(0, 0)` and `(1, 1)` are faults, not transitional states
that software may accept indefinitely.

## Local power and permission

The direct +8 V treadmill pass-through is not fused by the prototype's local
fuse. Only the electronics tap is fused:

```text
+8V pass-through
  -> RXEF075 resettable fuse
  -> 1N5822 series Schottky
  -> protected VIN
     -> P6KE12A to ground
     -> TSR1-2433E -> 3.3 V logic rail
     -> TPS70950 -> 5 V relay rail
     -> TPS3700 voltage-window detector
```

The P6KE12A is oriented cathode to protected VIN and anode to ground. The
TSR1-2433E uses pin 1 VIN, pin 2 ground, and pin 3 3.3 V output when viewed as
specified by its manufacturer; the UI pairs that drawing with an output-voltage
test before attaching the DevKit. The TPS70950 is mounted on the owned SOT-23
adapter and mapped by IC pin name rather than by an assumed adapter
orientation: pin 1 IN, 2 GND, 3 EN, 4 NC, and 5 OUT. It receives a 1 uF input
capacitor and at least 4.7 uF at its output adjacent to the adapter.

The TPS3700 occupies the second adapter: pin 1 OUTA, 2 GND, 3 INA+, 4 INB-,
5 VDD, and 6 OUTB. The Aries adapters are treated as unknown until the wizard
has the operator meter continuity from each SOT land number to its DIP pin;
only that recorded map drives subsequent hole coordinates.

The TPS3700 uses the owned 150 kOhm/10 kOhm undervoltage divider and
255 kOhm/10 kOhm overvoltage divider, with 1 nF filtering at each sense input.
Its open-drain outputs are joined as `TREAD_OK`, with a 10 kOhm pull-up to
3.3 V and 100 kOhm pull-down to ground. A 4.7 kOhm series resistor exposes the
same state to GPIO6 as `TREAD_OK_MCU`; GPIO6 is never part of the permission
node itself. The measured rising undervoltage boundary must be 6.25 V to
6.55 V and the measured falling overvoltage boundary 10.30 V to 10.90 V.
`TREAD_OK` must be low below and above that window and high at 8.00 V.

SN74AHC08 gate 1 implements `RELAY_GATE = RELAY_CMD AND TREAD_OK`: pin 1 is
GPIO21/`RELAY_CMD`, pin 2 is `TREAD_OK`, and pin 3 is `RELAY_GATE`.
`RELAY_GATE` drives both TPS709 EN and the 560 ohm BC337 base resistor; a
10 kOhm pull-down is present on GPIO21 and another 10 kOhm pull-down from the
BC337 base to emitter. The BC337 emitter goes to ground and collector to the
low side of the coil. A P6KE6.8CA is placed directly across the coil. Because
the owned relay draws about 100 mA, the wizard must prove the resulting drive:
with VIN at 8.00 V, energized coil voltage must be at least 4.50 V, coil current
must be 90 mA to 110 mA, and BC337 collector-emitter voltage must not exceed
0.30 V. After five continuously energized minutes, BC337 and TPS709 case
temperature rise must each be no more than 10 degrees C and absolute
temperature no more than 45 degrees C. A failed result stops the build; the UI
does not suggest changing the resistor without a new reviewed design. The
BC337 pin order is confirmed against the physical lot and by diode-test steps
before insertion.

## UART isolation

The SN74AHC126N is powered from 3.3 V and provides the motor-transmit
tri-state. Gate 1 uses pin 1 (`1OE`) for `TX_GATE`, pin 2 (`1A`) for GPIO17,
and pin 3 (`1Y`) through 100 ohms to relay pole-A NO.

SN74AHC08 gate 2 computes `TX_GATE = TX_ENABLE AND TREAD_OK`: pin 4 is
GPIO15/`TX_ENABLE`, pin 5 is `TREAD_OK`, and pin 6 is `TX_GATE`; GPIO15 has a
10 kOhm pull-down. On the AHC08, unused inputs 9, 10, 12, and 13 go to ground;
unused outputs 8 and 11 remain open. On the AHC126, unused enables 4, 10, and
13 and unused inputs 5, 9, and 12 go to ground; unused outputs 6, 8, and 11
remain open. Both PDIP-14 parts use pin 14 VCC and pin 7 ground. Each has a
100 nF capacitor directly between those pins. The guide uses a top-view notch
marker and asks the operator to identify pin 1 before placement.

GPIO18 observes `CONSOLE.6` through 10 kOhm. GPIO16 observes pass-through pin
3 through 10 kOhm. The series resistors limit current into an unpowered DevKit;
they are receive taps, not level shifters. Their voltage and idle-state
behavior must be measured before firmware configures either UART.

GPIO7 is `VBUS_PRESENT_N`. The owned Diotec 2N7000 has its gate connected to
the DevKit 5 V/VBUS header through the mapped physical lead, source to ground,
and drain to GPIO7 with a 10 kOhm pull-up to 3.3 V. A separate 10 kOhm resistor
discharges the gate/VBUS node to ground. The physical lot's S/G/D lead order is
verified before insertion. GPIO7 must read low with UART USB present and high
in standalone power.

## Exact prototype inventory

The structured builder model identifies these purchased parts by MPN and uses
one of each unless a different count is shown:

| Qty used | Purchased part |
|---:|---|
| 1 | Espressif `ESP32-S3-DEVKITC-1-N8R8` |
| 1 | TI `TPS3700DDCR` on one Aries `LCQT-SOT23-6` adapter |
| 1 | TI `TPS70950DBVR` on one Aries `LCQT-SOT23-6` adapter |
| 1 | TI `SN74AHC08N` PDIP-14 |
| 1 | TI `SN74AHC126N` PDIP-14 |
| 1 | Omron/Aratas `G5V-2 DC5` |
| 1 | Diotec `BC337-40` and 1 Diotec `2N7000` |
| 1 each | MCC `1N5822-TP`, Littelfuse `P6KE6.8CA`, Littelfuse `RXEF075`, MCC `P6KE12A-TP` |
| 1 | Traco `TSR 1-2433E` |
| 1 each | Yageo 560 ohm, 150 kOhm, and 255 kOhm 1% 1/4 W resistors |
| 4 / 1 / 2 | Vishay `K104K15X7RF5TL2` 100 nF, TDK `FG28X7R1E105KRT06` 1 uF, TDK `FG28C0G1H102JNT06` 1 nF |
| 1 / 2 / 1 | Wurth `860020472004` 22 uF, Rubycon `50YXJ10M5X11` 10 uF, Wurth `860010672008` 4.7 uF |
| 1 each | Lite-On `LTL-4233` green LED and Wurth `151051RS11000` red LED |

The general resistor assortment supplies one 100 ohm, one 330 ohm, one 1 kOhm,
one 4.7 kOhm, twelve 10 kOhm, and one 100 kOhm; each is meter-checked before
placement.
Two user-supplied all-pin RJ45 breakouts, jumpers, and the breadboard have no
known manufacturer identity. The wizard records a photo and user-entered
identifier for each, then derives their electrical mapping by continuity; it
never substitutes a catalog pinout. The required bench tools are a fused DMM,
adjustable current-limited 0-12 V supply, and contact thermometer or
thermocouple. Missing tools lock the powered phases but not construction.

The builder links each orientation card to its authoritative manufacturer
document: Omron G5V-2 datasheet `K046-E1`, TI TPS3700 and TPS709 datasheets, TI
SN74AHC08 and SN74AHC126 datasheets, Diotec BC337-40 and 2N7000 datasheets,
Traco TSR 1E datasheet, and Littelfuse P6KE and RXEF datasheets. The repository
model stores the exact document URL and revision beside the asserted pin map.

## Complete prototype nets

The structured builder model uses these electrical nets; generated hole
coordinates may change after the adapters and breadboard are mapped, but none
of these named connections is optional:

- `GND_1`: `CONSOLE.1`, `MOTOR.1`, final link to `GND`.
- `GND_7`: `CONSOLE.7`, `MOTOR.7`, final link to `GND`.
- `P8_2`: `CONSOLE.2`, `MOTOR.2`, final link to `+8V_RAW`.
- `P8_8`: `CONSOLE.8`, `MOTOR.8`, final link to `+8V_RAW`.
- `+8V_RAW`: both final +8 V links and RXEF075 input.
- `VIN`: RXEF075 through 1N5822, P6KE12A cathode, TSR1 pin 1, TPS3700 pin 5,
  TPS709 pin 1, divider tops, 22 uF bulk, and one 10 uF TSR-input capacitor.
  The TVS anode and capacitor negatives are ground.
- `TSR_3V3`: TSR1 pin 3 and one 10 uF capacitor; the removable `STANDALONE
  POWER` link joins this to `LOGIC_3V3`. TSR1 pin 2 is ground.
- `LOGIC_3V3`: DevKit 3V3, AHC08 pin 14, AHC126 pin 14, 3.3 V pull-ups, and
  logic decouplers. Both logic pin 7s and decoupler returns are ground.
- `UV_SENSE`: VIN through 150 kOhm to TPS3700 pin 3, with 10 kOhm and 1 nF
  from that node to ground.
- `OV_SENSE`: VIN through 255 kOhm to TPS3700 pin 4, with 10 kOhm and 1 nF
  from that node to ground.
- `TREAD_OK`: TPS3700 pins 1/6, 10 kOhm to `LOGIC_3V3`, 100 kOhm to ground,
  AHC08 pins 2/5, and 4.7 kOhm to GPIO6.
- `RELAY_CMD`: GPIO21, AHC08 pin 1, and 10 kOhm to ground.
- `RELAY_GATE`: AHC08 pin 3, TPS709 pin 3, and 560 ohms to BC337 base; base
  also has 10 kOhm to grounded emitter.
- `+5V_RLY`: TPS709 pin 5, 4.7 uF to ground, and the removable `COIL POWER`
  link. TPS709 pin 2 and its 1 uF input-capacitor return are ground; pin 4 is
  unconnected.
- `RELAY_COIL+`: `COIL POWER`, G5V-2 pin 1, and one P6KE6.8CA end.
  `RELAY_COIL-` is G5V-2 pin 16, BC337 collector, and the other TVS end.
- `CONS6`: `CONSOLE.6`, G5V-2 pin 4, and 10 kOhm to GPIO18.
- `MOT6`: `MOTOR.6` and G5V-2 pin 6.
- `TX_DRV`: G5V-2 pin 8 through 100 ohms to AHC126 pin 3.
- `ESP_TX`: GPIO17 and AHC126 pin 2.
- `TX_ENABLE`: GPIO15, AHC08 pin 4, and 10 kOhm to ground.
- `TX_GATE`: AHC08 pin 6 and AHC126 pin 1.
- `PIN3`: both breakout pin-3 terminals and 10 kOhm to GPIO16.
- `PIN4_PASS`: both breakout pin-4 terminals.
- `PIN5_SAFETY`: both breakout pin-5 terminals.
- `K1_NC_FB`: G5V-2 pin 13, GPIO4, and 10 kOhm to `LOGIC_3V3`.
- `K1_NO_FB`: G5V-2 pin 9, GPIO5, and 10 kOhm to `LOGIC_3V3`; G5V-2 pin 11
  is ground.
- `VBUS_SENSE`: DevKit 5 V/VBUS, 2N7000 gate, and 10 kOhm to ground. The
  grounded-source drain is GPIO7 with 10 kOhm to `LOGIC_3V3`.
- `STATUS_LED`: GPIO38 through 330 ohms to green LED anode; cathode ground.
- `POWER_LED`: `LOGIC_3V3` through 1 kOhm to red LED anode; cathode ground.

Four 100 nF parts decouple TPS3700, AHC08, AHC126, and `LOGIC_3V3`. AHC08
unused inputs 9/10/12/13 and the listed AHC126 unused enable/input pins are
grounded; unused outputs remain open. Every counted capacitor and LED above is
therefore connected and receives a construction step.

## DevKit power states

The builder makes power-source state explicit and never asks the operator to
connect two supplies to the DevKit 3.3 V rail. `DEVKIT_3V3` always feeds the
logic rail. One clearly labeled removable `STANDALONE POWER` jumper can join
the TSR1 3.3 V output to that rail:

1. **USB diagnostic state:** the Pi connects only to the DevKit port labeled
   UART. Treadmill/bench VIN and `STANDALONE POWER` are disconnected.
2. **Bench exerciser state:** Pi UART USB powers the DevKit and 3.3 V logic;
   the current-limited supply powers VIN, TPS3700, TPS709, and the relay.
   `STANDALONE POWER` remains physically removed, so TSR1 output is unloaded
   and cannot oppose the DevKit regulator. A separately identified
   `relay-exerciser` image accepts only bounded single-shot UART commands and
   returns the GPIO4/5/6/7 feedback record. The no-control diagnostic image
   cannot satisfy this gate.
3. **Treadmill standalone state:** Pi UART USB is physically disconnected,
   then `STANDALONE POWER` is installed so TSR1 powers the DevKit and logic.
   Observation and commands use only the qualified firmware's Wi-Fi API and
   on-device bounded event log. If that build or Wi-Fi path is unavailable,
   treadmill observation and transfer stay locked.

Simultaneous USB and TSR1 power is prohibited. Bench VIN may coexist with USB
only while `STANDALONE POWER` is visibly absent. Treadmill attachment always
uses standalone power and no USB cable.

## Browser workflow and gates

The builder is a single mobile-friendly HTML application with persistent local
progress, zoomable breadboard drawing, one-step navigation, full netlist, and
an evidence field beside every meter check. It refuses to mark a later phase
complete while an earlier gate is missing.

1. **Inventory:** identify exact parts, resistor values, capacitor values,
   adapters, wire gauge, breadboard, meter, current-limited supply, and known
   patch cable.
2. **Map connectors:** number both RJ45 breakouts by continuity; label console
   and motor; verify that no two numbered terminals are shorted.
3. **Map components:** verify relay coil and both contact poles; map TPS709
   adapter pads; identify diode, LED, transistor, and IC orientations.
4. **Build power-off pass-through:** place both disconnected breakouts and add
   contacts 1, 2, 3, 4, 5, 7, and 8 one conductor at a time. Before adding the
   next conductor, verify the new path below 2 ohms and every other console
   terminal open to it. Keep pins 1/7 and 2/8 isolated for these four checks;
   only after they pass, add and verify the four final common-net links.
5. **Build relay path:** install G5V-2 pole A on contact 6 and pole B feedback.
   Prove unpowered `CONSOLE.6` to `MOTOR.6` continuity and prove the TX path is
   isolated.
6. **Build protected power:** add fuse, diode, TVS, TSR1, TPS709, TPS3700,
   dividers, and capacitors one item at a time.
7. **Build logic and taps:** add AHC08, AHC126, relay transistor, pull resistors,
   UART taps, and DevKit signal jumpers.
8. **Unpowered audit:** run all continuity, resistance, polarity, and rail-short
   checks from a generated checklist. An independent final visual comparison
   precedes power.
9. **Current-limited bench power:** start at 8.00 V with a 250 mA limit and the
   DevKit disconnected and `COIL POWER` open. Protected VIN must be 7.20 V to
   7.90 V, TSR output 3.20 V to 3.40 V, disabled TPS709 output below 0.25 V,
   and TREAD_OK must meet the measured window limits above. Rail-to-ground
   current must remain below 50 mA. Attach the DevKit only after these pass.
10. **Bench relay exercise:** use the Pi UART command path and exact
    `relay-exerciser` build identity in bench exerciser power state. Leave
    `COIL POWER` open, issue one bounded relay-on command, and require TPS709
    output 4.75 V to 5.25 V unloaded. Command off, remove both supplies, verify
    below 0.25 V, then install `COIL POWER`. Restore power and raise the supply
    limit no higher than 500 mA. Record the numerical coil, BC337, truth,
    timing, and five-minute temperature limits above. Removing USB, GPIO21,
    TREAD_OK, or VIN must restore NC bypass within 100 ms and feedback must
    settle to `(0,1)`; this 100 ms is a prototype gate, not a production timing
    qualification.
11. **Standalone observer firmware gate:** install only an identified image
    whose build manifest proves relay and TX outputs are disabled and whose
    Wi-Fi API returns GPIO/UART observations. Disconnect USB, install
    `STANDALONE POWER`, and bench-prove that power and observations work. The
    current `devkit-bringup` image does not satisfy this phase.
12. **Bypass-only treadmill test:** connect the treadmill only with standalone
    observer firmware. With treadmill power off, install the wizard's temporary
    fused-DMM current harness that brings both console +8 V contacts to the
    meter input and splits the meter output back to the two independent
    breadboard paths; confirm correct current-jack placement before power.
    Measured total current must be no more than 500 mA. Power off again and
    restore the two direct paths. Measure console-to-motor supply drop
    and motor-to-console ground-return drop; each must be no more than 50 mV.
    After 15 minutes in normal bypass operation, every breakout terminal,
    jumper endpoint, and breadboard power-path contact must remain at or below
    40 degrees C and no more than 10 degrees C above its recorded ambient.
    Observe UART only through Wi-Fi/on-device logs. Any failure requires the
    hybrid harness; no relay transfer is allowed.
13. **Functional firmware gate:** the UI accepts a separately generated build
    identity and production safety-test evidence. Only a qualified standalone
    Wi-Fi image exposes guarded single-transfer instructions. Diagnostic,
    exerciser, and observer firmware leave this phase visibly locked.

## Failure handling

Any incorrect continuity result, rail/current/drop/temperature measurement
outside the numerical limits above, invalid relay feedback pair, reset, or
missing UART idle level stops the workflow. The
default recovery is to remove treadmill and USB power, return the relay to its
de-energized NC state, and repeat the preceding unpowered gate. The UI never
suggests bypassing a failed check.

Browser progress is advisory. Reloading, editing local storage, or checking a
box does not create electrical permission; meter results and firmware identity
remain the evidence.

## Verification of the builder

Automated tests will parse the structured model and assert:

- exact owned part identities and all orientation-sensitive pin mappings;
- exact AHC08/AHC126 gate allocation, unused-input termination,
  `TREAD_OK_MCU`, and `VBUS_PRESENT_N` wiring;
- all eight RJ45 contacts appear exactly once at each breakout;
- pins 1/7 and 2/8 retain independent physical pass-through wires and are
  tested before any external cable can common them;
- relay NC bypass exists without power and relay NO cannot reach the motor in
  the unpowered model;
- pole-B feedback states match the physical G5V-2 contact map;
- no construction step applies power before the unpowered audit;
- treadmill transfer cannot appear before numerical bypass-load measurements
  and the functional-firmware gate;
- each power state names its source, jumper position, firmware identity,
  command transport, and observation transport;
- diagnostic firmware is never described as control-capable;
- every component and wire in the netlist has a drawing object and an atomic
  construction step;
- state persistence, reset, zoom, mobile layout, and phase locking work in a
  headless browser test.

The final implementation is exercised from the repository test suite, served
through the existing browser companion, and inspected at phone and desktop
widths.

## Completion boundary

This work is complete when the old jumper simulator has been replaced by the
tested full-breadboard builder, the exact relay and connector mappings are
unambiguous, all power and treadmill stages are gated as above, the committed
guide is pushed, and the live browser URL serves the same tested artifact.

It does not by itself qualify the breadboard, firmware, UART voltage levels,
relay timing, or treadmill operation. Those claims arise only from completing
the corresponding measurements on the physical assembly.
