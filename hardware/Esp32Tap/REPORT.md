# Esp32Tap Rev C engineering review

**Status: HOLD.** This report is not authorization to fabricate, pay, deploy,
or operate a treadmill.

## Executive conclusion

Rev C is internally coherent under the checked repository models:

- 95 × 58 mm four-layer PCB;
- U1 fully inside the board with 3.25/3.30 mm margins and its stock all-layer
  manufacturer keepout intact;
- SMT Micro-Fit board interfaces and separate turnkey RJ45 pigtail concept;
- serial +8 V as the only board power source and USB as data-only;
- hardware-qualified relay and TX permissions;
- cycle-free, via-free, closely matched native USB routing;
- exact 2 A emitted-copper trace-union analysis with independent GND-via
  envelope;
- exact schematic/PCB/BOM/CPL/fabrication parity;
- eight repeated dual-engine behavioral simulation decks.

This closes repository-detectable consistency defects. It does not close
vendor, harness, firmware, or physical safety work.

## Safety and power architecture

```text
Micro-Fit J1/J2 serial +8 V pass-through
        |
        +-- F1 -- D1 -- VIN -- U2 ------------------------- +3V3
                         |                                   |
                         +-- D3 clamp                        +-- U6 gates
                         +-- U4 voltage window                   |
                         +-- U5 gated relay supply               +-- U7 TX

RELAY_GATE = RELAY_CMD AND TREAD_OK
TX_GATE    = TX_ENABLE AND TREAD_OK
```

USB VBUS ends in protection, bypass, discharge, and presence detection. It has
no local-power path. The USB and treadmill grounds are common, so safe
simultaneous attachment remains a bench gate.

The relay's feedback pole is an armature proxy, not direct continuity sensing
of the transfer pole. Contact operation, release, bounce, weld behavior, and
fault-to-stable-NC time remain unsupported.

## PCB power disposition

The exact 2 A trace-union solve gives:

- +8 V: 18.745090 mΩ;
- conservative trace-only GND: 19.665484 mΩ;
- combined loop drop: **76.821149 mV**.

Coincident trace primitives are treated as one physical copper union, while
geometrically separate branches remain parallel. Duplicate coincident
intended vias fail validation.

At the worst exact +8 V case, maximum via current is 1.131922 A, barrel rise
is 15.234166 °C, and I²R is 1.864230 mW. The trace model does not solve In1
plane current sharing, so every 1.4/1.0 mm GND via receives a separate
full-2.0-A envelope: 12.273573 °C and 2.328020 mW at 20 µm plating.

Twenty micrometres is the IPC-6012 Class 2 average PTH copper basis cited by
JLC, not measured delivered copper. The live quote/DFM must confirm it.
Complete installed drop, ambient temperature, airflow, solder joints, RJ45
terminations, treadmill source impedance, and transients remain unsupported.

## USB disposition

USB stays on F.Cu with zero signal vias. The controlled sections use
0.2906 mm traces and a 0.2000 mm edge gap. Exact shortest paths are:

| Side | D− | D+ | Skew |
|---|---:|---:|---:|
| A | 60.0528786214 mm | 60.0528777233 mm | 0.0000008981 mm |
| B | 59.0528786214 mm | 59.0528777233 mm | 0.0000008981 mm |

Production stack/impedance, native USB ROM/reset attachment, enumeration,
unplug behavior, eye margin, and USB/treadmill return current remain open.

## Interfaces and manufacturing

J1/J2 are `430450809` and `430451010` right-angle SMT Micro-Fit headers. The
external treadmill interfaces require two separately quoted, keyed,
dimensioned Micro-Fit-to-RJ45 pigtails. Their RJ45 source, pin mapping, crimp,
strain relief, continuity/pull testing, and single-open 2 A behavior are not
qualified.

J3 is a standard-reflow USB-C part. Its four plated S1 holes are mechanical
stakes in the stock footprint; no manual/wave operation is requested.

U1 is fully on-board with the locked margins above. The 58 mm short axis still
requires a dimensioned vendor response for carrier/rails, tooling, fiducials,
tabs, support, depanelization, and delivered-edge treatment.

The existing operator-observed online JLCDFM JSON is preserved as historical
evidence bound to an older archive. A new live review must use the current
exact 13-member ZIP together with its exact BOM, CPL, and placement preview.

## Simulation and evidence boundary

Eight decks run three times on host ngspice 42 and pinned Docker ngspice 39:
input protection, treadmill permission, safety truth table, relay drive,
VBUS presence, averaged buck, UART taps, and harness supply drop.

The exact harness unsupported list remains `RJ45_SINGLE_OPEN_2A`,
`MINIMUM_VIN`, `SOURCE_IMPEDANCE`, `AMBIENT_THERMAL`,
`TRANSIENT_RESPONSE`, `COMPLETE_INSTALLED_DROP`, `USB_RETURN_CURRENT`, `ESD`,
`RF`, and `SWITCHING_LOOP`. The simulation manifest additionally keeps
device thermal/leakage, real surge/rail behavior, relay contact motion,
native-USB channel behavior, regulator switching/EMI, real UART integrity,
and physical enclosure/RF effects unsupported.

The host firmware model is not production ESP-IDF firmware and cannot prove
GPIO timing, watchdog release, brownout behavior, security, radio coexistence,
or treadmill safety.

## Recommendation

Keep HOLD. The next allowed actions are read-only/live vendor reviews of the
current exact ZIP/BOM/CPL, 20 µm barrel plating, standard-reflow J3 stakes,
carrier/rails, pigtail drawings, and enclosure quote. Purchase requires owner
authorization after those reviews. First treadmill contact remains Proxy-only
with relay energization compiled out; Emulate requires a later explicit gate.
