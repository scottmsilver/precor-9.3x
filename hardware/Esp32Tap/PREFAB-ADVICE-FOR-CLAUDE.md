# Superseded Rev A pre-fabrication advice for Claude

**Archival status:** This audit is retained unchanged for traceability. Its
findings drove the Rev B redesign; none of its topology, part, fabrication,
stock, quote, or release statements describe the current package. Use
`AI-HANDOFF.md` and the generated Rev B evidence instead.

**Review date:** 2026-07-23

**Decision:** **HOLD — do not submit or pay for the currently staged PCBA or
enclosure orders.**

This is not merely a list of bench-only unknowns. The current package contains
confirmed mechanical defects, a power-source interaction that defeats the
stated relay fallback, and component/design margins that need an explicit
engineering disposition before fabrication.

The bare PCB manufacturing data is internally coherent. That does not make the
assembled product order-ready.

## Required action summary

Treat these as pre-order gates:

1. Correct both RJ45 aperture centerlines and repair the non-manifold base
   model; regenerate and revalidate the STLs.
2. Make relay fallback independent of USB power. Loss of treadmill power must
   force K1 to its normally closed state in hardware, even if USB and firmware
   remain alive.
3. Correct or justify the TPS54202 input capacitance and feedback compensation,
   then validate the regulator with the real capacitor deratings.
4. Correct the 3 V relay-coil drive and review the paralleled DPDT contacts as a
   fault/transition hazard.
5. Obtain JLC's actual production carrier/panel drawing and approval for the
   overhanging ESP32 module before payment.
6. Resolve or explicitly accept the USB-routing, self-powered USB, RF-clearance,
   boot-strap, ESD, and silkscreen risks described below.

## 1. Confirmed enclosure defects

### RJ45 cutouts are shifted by 8.890 mm

The current values in `enclosure/esp32tap_case.scad:25-31` are:

```text
j1_yc = 3.555
j2_yc = 32.555
```

The jack bodies calculated from the actual KiCad F.Fab geometry center at:

```text
J1 = 12.445 mm
J2 = 41.445 mm
```

The footprint peg holes independently confirm those centers. With the current
SCAD values, each wall opening overlaps only about 7.610 mm of a 15.300 mm jack
body, leaving about 7.690 mm behind the wall. A plug will not seat normally.

The previous `12.25/41.25` values were only 0.195 mm from the real body centers.
The later “fix” applied the KiCad Y-axis/rotation sign in the wrong direction.
Therefore the “enclosure fixed and print-ready” statement in
`AI-HANDOFF.md:35-37` is false.

### The base STL is not a valid 2-manifold

Fresh mesh validation of the checked-in STLs found:

| Part | Watertight | Valid volume | Faces | Extents |
|---|---:|---:|---:|---|
| `esp32tap_base.stl` | No | No | 2,546 | 123.0 × 78.3 × 23.6 mm |
| `esp32tap_lid.stl` | Yes | Yes | 2,204 | 109.0 × 78.3 × 4.2 mm |

The base has seven edges with four incident faces. The Ø7 lid posts are exactly
tangent to the cavity walls because their centers are 3.5 mm from the wall and
their radius is also 3.5 mm (`esp32tap_case.scad:87-89,109,143-150`). Give the
posts positive overlap or positive separation, regenerate both STLs, and require
OpenSCAD `Simple: yes` plus an independent watertight/volume check.

The checked-in STLs match the current SCAD geometry, so this is present in the
actual order artifact rather than being stale documentation alone.

## 2. USB power defeats the claimed fail-safe

The netlist has:

- treadmill `+8V_RAW` feeding VIN through D1;
- USB VBUS feeding the same VIN through D2;
- the K1 coil powered from `+3V3`.

See `NETLIST.md:69-77`. There is no treadmill-power sense input and no
treadmill-derived hardware interlock in the relay drive.

Consequently, if USB remains connected when treadmill +8 V disappears, USB can
keep the ESP32 and an energized relay alive. Firmware cannot determine which
diode-OR leg is sustaining VIN. This directly contradicts the statement in
`firmware/PLAN.md:40-42` that power loss releases the relay and instantly
restores the stock treadmill.

Fresh behavioral ngspice simulation, repeated identically three times after
giving the disconnected USB leg a realistic high-impedance reference, produced:

| Condition after +8 V removal | VIN | +3V3 | K1 current |
|---|---:|---:|---:|
| USB still connected | 4.614 V | 3.305 V | 35.54 mA |
| No USB connected | 0.095 V at 80 ms | 0 V | 0 mA |

USB takeover began at approximately 19.971 ms during a 20 ms treadmill-source
drop. The modeled buck output is behavioral rather than a switching-loop model,
but that is sufficient to prove the topology problem: an alternate source
exists and can continue powering the relay.

### Required deployable fix

K1's permission to enter or remain in Emulate must be hardware-ANDed with a
treadmill-derived power-good signal having appropriate threshold, hysteresis,
and reset behavior. A GPIO that merely reports treadmill power is diagnostic;
it is not an independent interlock.

Removing D2 is a possible verification-build containment, but it is not by
itself a complete deployable safety architecture.

## 3. Regulator and USB-power margins

### TPS54202 input capacitance is below TI's recommendation

Only C3 = 4.7 µF and C4 = 0.1 µF are local to U2 after the ORing diodes. C1/C2
are upstream of D1 and unavailable to the USB leg when D1 is reverse-biased;
C11 adds only 1 µF before D2.

TI recommends a ceramic decoupling capacitor **over 10 µF** at VIN. See the
[TPS54202 datasheet, section 7.2.3.1](https://www.ti.com/lit/ds/symlink/tps54202.pdf).

### The recommended 3.3 V feed-forward capacitor is absent

The installed 10 µH, nominal 44 µF, 100 kΩ/22 kΩ network matches TI's 3.3 V
starting point, except no capacitor is connected across the upper feedback
resistor. TI warns that ceramic-dominated COUT can produce low phase margin and
recommends 56 pF for this 3.3 V combination.

Using TI's crossover estimate:

- nominal 44 µF gives approximately 27.2 kHz;
- any effective COUT below approximately 29.9 µF raises the estimate above
  40 kHz;
- a sensitivity case using 23.936 µF (54.4% of nominal) gives approximately
  50.0 kHz. This is not a measured value for the installed capacitors because
  their complete bias/temperature curves are not in the repository;
- TI advises less than 40 kHz when no feed-forward capacitor is used.

This does not prove instability, but it disproves the claim that the existing
prose-only droop simulation closes the control-loop question.

TI's downloadable TPS54202 PSpice library is encrypted and could not be run in
ngspice. Do not claim closed-loop phase margin, startup, ripple, or transient
validation until there is a compatible device model, a bench measurement, or
an accepted design calculation.

### USB-only startup is not guaranteed

TPS54202's recommended VIN minimum is 4.5 V and its worst-case rising UVLO is
4.4 V. A diode model fitted to the selected SS34 produced:

| Assumed input power | VIN with 4.75 V at USB connector | USB voltage needed for VIN = 4.50 V |
|---:|---:|---:|
| 0.50 W | 4.428 V | 4.821 V |
| 1.00 W | 4.404 V | 4.845 V |
| 1.35 W | 4.391 V | 4.858 V |
| 1.80 W | 4.378 V | 4.870 V |

Cable and connector loss were not included. Therefore “USB-only programming is
guaranteed” is not supported at valid low-voltage host corners.

## 4. Relay findings

### The selected coil is not the coil used in the old simulation

The BOM selects the standard `G6K-2F-Y-TR DC3`. Omron specifies 3.0 V, 91 Ω,
33 mA, and approximately 100 mW. The earlier 49.1 mA result used a 64 Ω
high-temperature coil model, which is not the selected relay.

Fresh transistor/coil simulations using the selected standard coil found:

| Case | Coil current | Q1 VCE | Approximate coil power |
|---|---:|---:|---:|
| 3.305 V nominal | 35.89 mA | 39.0 mV | 117 mW |
| 3.170 V, +10% coil R | 31.22 mA | 45.3 mV | 97.6 mW |
| 3.445 V, −10% coil R | 41.38 mA | 56.3 mV | 140 mW |
| Artificial Q1 β = 50 | 35.46 mA | 78.4 mV | — |

Q1 has ample base drive, and the modeled flyback peak was only 4.113 V. The
problem is continuous coil overvoltage/heating, not transistor saturation.
Omron says rated voltage should normally be applied and warns that continuous
overvoltage changes coil temperature, electrical life, and insulation. See the
[Omron G6K datasheet](https://components.omron.com/system/files/2026-06/datasheet_pdf/K106-E1.pdf).

Use a properly rated coil supply/part or a deliberately calculated drive
network, and validate hot/cold operate and release margins plus the intended
three-hour duty cycle.

### Paralleled DPDT poles do not provide absolute isolation

Both COM contacts connect to MOT6, both NC contacts to CONS6, and both NO
contacts to TX_DRV (`NETLIST.md:84-86`). Static Proxy and Emulate states are
wired as intended.

The two poles are not guaranteed to transfer simultaneously. During a
transition, one pole can still be on NC while the other has reached NO,
momentarily joining the console and ESP drivers. One welded or stuck contact
also defeats the claimed physical isolation. Omron explicitly warns that
potential-difference circuits on separate relay poles can short because of
small operating-time differences:
[Omron relay FAQ](https://components.omron.com/eu-en/faq/relays/FAQE10037).

Tri-stating ESP TX around commanded transitions is a useful normal-operation
mitigation, but it does not address a stuck/welded contact or make this relay a
certified safety element.

## 5. Signal, USB, protection, and RF risks

### UART taps

Fresh simulation supports adequate 9600-baud timing margin under the tested
assumptions:

- nominal bus rise: 45 ns;
- nominal ESP RX rise: 157 ns;
- stress case with 1 kΩ source and 2 nF line: 4.43 µs;
- one 9600-baud bit: 104.17 µs.

This is encouraging, but actual treadmill source impedance, cable capacitance,
edge rate, and inter-byte-gap tolerance remain bench-only.

### Unpowered-board backfeed

At the instant an unpowered ESP input is driven high through 4.7 kΩ, the model
found about 0.560 mA per tap and 1.119 mA for two simultaneous high taps. A
first-order calculation gives 0.57–0.64 mA per input depending on clamp voltage.

Therefore the `<0.3 mA` claim in `AI-HANDOFF.md:60-63` is false. The eventual
current can fall as the dead rail rises and the rail LED loads it, but exact
unpowered ESP clamp behavior is not specified and must be measured.

### Native USB routing

The USB traces are ordinary 0.30 mm nets, not a constrained differential pair.
For the U3-to-U1 MCU-side segments:

- MCU-side D+ is approximately 49.11 mm;
- MCU-side D− is approximately 54.84 mm;
- each uses two vias;
- the long bottom-layer runs are separated by about 5.7 mm;
- there is no continuous adjacent reference plane or paired return vias;
- no 22/33 Ω tuning-resistor footprints are reserved.

The full J3-to-U1 copper totals are approximately 58.02 mm for D+ and 62.23 mm
for D−, a 4.21 mm mismatch.

This violates Espressif's recommendations for equal-length 90 Ω differential
routing, a continuous reference, minimized transitions, return vias, and tuning
footprints. Full-speed USB may still work, but enumeration and signal margin
are unproven. See
[Espressif's ESP32-S3 USB layout guidance](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/pcb-layout-design.html#usb).

When treadmill-powered, this is also a self-powered USB device, but VBUS is not
sensed by any GPIO. Add the required VBUS monitoring or formally constrain USB
use to a supported power state.

### Other hardware advisories

- GPIO0 has no external pull-up despite Espressif's recommendation.
- D5-D7 are approximately 100 pF TVS devices. The 4.7 kΩ RX resistors limit
  clamp current well, but the TX pin has only 100 Ω and its IEC-pulse survival
  is unproven.
- The PCB antenna body is only about 3.35 mm from the enclosure's interior wall;
  Espressif recommends at least 15 mm housing clearance in all directions and
  end-product range testing. Plastic helps but does not remove the risk,
  especially under a metal treadmill hood.

## 6. JLC assembly and documentation risks

U1 overhangs the board by approximately 5.95 mm. JLC says a surface-mount
carrier is required when components exceed the PCB outline:
[JLC assembly fixture guidance](https://jlcpcb.com/help/article/pcb-assembly-fixtures).

The repository's proposed 100 × 71 mm production panel puts the extra width on
the 55 mm axis. A contiguous top rail would collide with U1 unless it is notched
or a separate carrier is used. This is a high conditional CAM risk, not proof
that JLC cannot build it. Require JLC's final carrier/panel drawing and written
approval before payment.

Also correct these order-document contradictions:

- `ORDER-READY.md:10-18` says U1 forces Standard PCBA, while
  `ORDER-READY.md:112-115` instructs selecting Economic.
- The enclosure instructions point to stale/nonexistent scratchpad STLs.
- The documented enclosure sizes and manifold result do not match the current
  files.
- The cart quantity is reported as both two and five in different documents.
- C45783 is actually a Samsung 22 µF, 25 V X5R capacitor, not the documented
  16 V part. The higher voltage rating is acceptable, but effective capacitance
  under bias and temperature has not been validated.
- Fabricated F.Silk has no reference designators, and 0.12 mm marks are below
  JLC's stated 0.15 mm legend capability. Visually approve every polarized and
  oriented component.

## 7. What passed

The following results are genuine and useful:

- KiCad 10 schematic ERC: 0 violations.
- KiCad 10 board DRC: 0 violations and 0 unrouted items.
- Generated design audit: 50 schematic components, 177 pins, 35 nets, 28
  intentional no-connects. The PCB has three additional mechanical mounting-hole
  footprints.
- Board: exactly 100 × 55 mm, two copper layers, coherent drill/edge geometry.
- Fresh Gerber and Excellon output matches the staged 11-file bundle after
  ignoring generated timestamps.
- BOM: 33 rows, 46 fitted references, 31 unique LCSC numbers, no missing
  populated references.
- CPL: exact one-to-one coverage of all 46 fitted references; values, packages,
  X/Y positions, rotations, and top-side assignments match the PCB.
- Connector pin maps, relay terminal numbering, flyback polarity, Q1 pinout,
  ESP32 module pins, USB-C CC/data mapping, fuse/TVS polarity, buck pin map,
  bootstrap network, and nominal feedback ratio are correct.
- Calculated +3V3 tolerance of approximately 3.170–3.444 V is within the ESP32
  module's 3.0–3.6 V supply range.

Qualification: `tools/gen_sch.py:56` declares all 177 custom-symbol pins
`passive`. A clean ERC therefore validates labels/connectivity but cannot catch
source conflicts, functional direction errors, or missing power drivers.
Strict `--schematic-parity` also reports 135 metadata issues: 50 missing PCB
LCSC fields, 54 footprint-name/attribute mismatches, 28 intentional-NC
conflicts, and three mechanical footprints absent from the schematic. These are
not copper faults, but “parity clean” is an overstatement.

## 8. What simulation cannot close

Do not present simulation as proof of:

- TPS54202 phase margin, startup, ripple, or transient response with the real
  encrypted vendor model and real MLCC derating;
- treadmill +8 V source capacity, brownouts, surge spectrum, or motor-hood
  temperature;
- relay contact transfer skew, welding, release mechanics, thermal rise, or
  three-hour lifetime;
- exact unpowered ESP32 GPIO clamp behavior;
- USB enumeration, eye margin, or recovery across hosts and cables;
- RF/BLE range inside the final enclosure and treadmill hood;
- JLC's production panel/carrier;
- physical RJ45/USB plug insertion, resin tolerances, mounting, and cable slack;
- real motor acceptance of UART scheduling and inter-byte gaps.

These require bench measurements, physical fit tests, or vendor CAM approval.

## 9. Build decision

### Current staged PCBA and enclosure

**NO-GO. Do not submit or pay.**

### Conditional two-board, bench-only verification build

This is defensible only after all of the following:

1. Correct and revalidate the enclosure, or omit the enclosure from the order.
2. Mark D2 DNP in the actual assembly data: split the combined D1/D2 BOM row,
   remove D2 from the placement data, and confirm D2 is unpopulated in JLC's
   placement preview.
3. Power only from a current-limited bench 8 V source.
4. Treat USB as data-only while the board is bench powered; verify that USB
   alone produces no +3V3 and cannot energize K1.
5. Obtain JLC approval of the carrier/panel around U1.
6. Resolve or intentionally bodge and instrument the regulator input
   capacitance, feed-forward compensation, and relay-coil drive.
7. Measure all rails, startup, relay temperature, relay release, UART edges,
   dead-board backfeed, and USB enumeration before any treadmill connection.
8. Explicitly prohibit treadmill contact, belt operation, and unattended
   operation under this exception.

This exception produces engineering prototypes, not deployable hardware.

### Deployable treadmill revision

Respins must close the hardware power-good interlock, regulator, relay-coil,
relay-contact, USB, protection, RF, enclosure, carrier, and firmware safety
gates. Then repeat ERC/DRC/parity/package checks, independent simulation,
current-limited bring-up, physical fit, and the complete treadmill-contact test
plan before installation.

## 10. Tracking

The completed independent review is recorded in `precor-9_3x-eoj`.
Corrective work is tracked as:

- `precor-9_3x-cyq` — P0: make relay fallback independent of USB power.
- `precor-9_3x-0k8` — P0: repair RJ45 alignment and base manifold.
- `precor-9_3x-4py` — P1: correct buck and relay component margins.
- `precor-9_3x-bkf` — P1: obtain JLC carrier approval and close layout DFM
  risks.
- `precor-9_3x-8bw` — P1: close firmware safety ownership and watchdog gates.

Do not update the order-ready claims until the corresponding design artifacts
and fresh verification evidence have changed. Do not operate the treadmill or
submit either order based on the current package.
