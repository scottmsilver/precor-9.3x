# Esp32Tap Rev C turnkey compact redesign

**Date:** 2026-07-24

**Status:** User-approved direction; detailed implementation and independent
review required before fabrication

**Issue:** `precor-9_3x-1dj`

## Purpose

Rev C replaces Rev B's board-mounted through-hole RJ45 jacks with compact,
machine-placeable connectors and factory-made plug-in harnesses. Its primary
manufacturing requirement is that the PCB assembler return a complete board:
the owner must not solder, crimp, install through-hole parts, or arrange a
secondary assembly process.

The finished device must be as small as practical without weakening Rev B's
electrical safety behavior, assembly yield, RF performance, or serviceability.
The board outline and existing connector locations are not constraints.

The owner will only plug factory-made harnesses into the assembled board and
connect USB and treadmill cables. Harness manufacture may be a separate line
item from JLCPCB assembly, but it must not require owner fabrication.

No fabrication order, cart submission, or payment is authorized by this
design. A quote may be prepared and saved for owner review, but the owner
makes the final purchase decision.

## Priority order

When requirements conflict, use this order:

1. Preserve Rev B's fail-safe bypass and hardware permission behavior.
2. Deliver a completely machine-assembled PCB with no owner soldering.
3. Prevent incorrect console/motor harness installation.
4. Minimize finished installed volume, including realistic cable bend space.
5. Minimize prototype and recurring cost.

“Smallest” means the smallest validated implementation, not the smallest
unrouted outline or a design that depends on unsupported assembly operations.

## External connection architecture

### Board connectors

J1 and J2 become low-profile, locking, surface-mount wire-to-board connectors.
The target class is a 1.0–1.25 mm-pitch side-entry connector with at least
eight circuits. Exact parts are selected only after confirming:

- current JLCPCB/LCSC stock and Standard PCBA placement support;
- reel packaging and top-side automated assembly;
- positive retention and a polarized housing;
- voltage and current ratings with measured treadmill-current derating;
- contact resistance and temperature-rise margin for the two +8 V and two
  ground conductors;
- an available mating housing, crimp terminal, and factory harness source;
- a manufacturer STEP model and recommended footprint;
- enough insertion life for installation and service.

The two board interfaces must be physically non-interchangeable. Preferred
implementations are different key codes or connector families. Using different
circuit counts, such as an 8-position and a 10-position connector with unused
positions, is acceptable when it provides robust keying without a material
size penalty. Color and labels are supplemental and are not the only defense
against swapping Console and Motor.

The connectors must not depend on hand-applied adhesive, staking, selective
solder, or a wave-solder pallet.

### Factory harnesses

Two separately keyed harnesses connect the board to the treadmill cabling.
Each terminates in the RJ45 gender required to preserve Rev B's inline
function, and each is factory-crimped and continuity-tested. The harness
drawing specifies:

- exact board housing and terminal part numbers;
- exact RJ45 termination and pin numbering;
- conductor gauge, insulation, length, and color;
- one-to-one signal mapping, including both +8 V and both ground conductors;
- Console versus Motor keying, label, and color;
- contact-resistance and continuity acceptance;
- strain relief and minimum bend radius.

No flying lead may be soldered directly to the PCB. Harnesses are replaceable
and plug into the finished board without tools.

## Electrical behavior

Rev C preserves Rev B's functional net behavior unless a separately reviewed
change is required by the module migration:

- the treadmill serial cable's nominal +8 V rail is the only board power;
- USB-C remains data-only and cannot power the board;
- both +8 V conductors and both grounds pass straight through between the two
  harness interfaces;
- F1/D1 protect only the local electronics branch;
- loss of power, reset, or hardware permission de-energizes K1 and restores
  the normally closed console-to-motor bypass;
- `TREAD_OK` gates relay actuation and motor transmit in hardware;
- the relay's second pole remains armature feedback;
- USB, RS-485/serial, relay, watchdog, brownout, and firmware safety contracts
  remain unchanged unless explicitly revalidated.

Connector and harness current ratings must be based on a documented maximum
continuous and transient pass-through current. Until treadmill measurements
close that gate, select contacts conservatively and retain both parallel power
and both parallel ground paths.

## Other assembly substitutions

SW1 and SW2 remain surface-mount tactile switches, but Rev C replaces the
current catalog selection if JLCPCB will not include it in placement. The
replacement must have an unambiguous exact manufacturer part number, supported
reel packaging, suitable actuator access, an official footprint, and confirmed
selection in the live BOM/CPL workflow.

Every populated component must be selected in the final JLCPCB placement
review. A matched catalog entry with an unchecked placement row does not count
as assembly acceptance. No populated component may be left for owner
installation.

## Module and layout compaction

The implementation compares the existing ESP32-S3-WROOM-1-N8 with a
JLC-placeable ESP32-S3-MINI N8-class module. Migrate only if a documented
pin-budget and capability table proves that the smaller module supports:

- native USB data;
- all relay command and feedback signals;
- the serial receive/transmit path;
- treadmill-voltage and VBUS presence inputs;
- required boot/program controls;
- production firmware, flash, RF, and watchdog requirements.

If the MINI migration is not clearly beneficial after antenna keepout and
routing are included, retain WROOM. In either case, place the module fully on
the delivered PCB and provide its complete manufacturer antenna keepout. Rev C
must not require an antenna-overhang carrier, automatic placement correction,
or a special depanelization notch.

Relayout from the safety-critical power and signal topology outward. The board
may change aspect ratio and mounting arrangement. Minimize the final enclosure
volume only after satisfying:

- switching-regulator hot-loop and feedback placement;
- input protection and TVS return geometry;
- continuous ground reference for native USB;
- controlled USB differential geometry on the selected stack;
- relay isolation, contact routing, and feedback separation;
- ESP antenna keepout and installed plastic/air clearance;
- connector insertion, latch access, and cable bend space;
- assembly courtyard, fiducial, tooling, and depanelization rules;
- accessible programming and diagnostic test points.

The enclosure is regenerated around the resulting PCB and plug-in connectors.
It provides keyed harness openings, cable strain relief, USB access, switch
access, RF clearance, and no opportunity to force the wrong harness into an
interface.

## Manufacturing acceptance

Rev C is order-reviewable only when all of the following are true:

- all populated board parts use automated SMT assembly;
- the live JLCPCB BOM resolves every exact manufacturer/JLC part;
- the live placement list includes every populated designator;
- the quote contains no unpriced manual, wave, fixture, or post-assembly work;
- the production preview preserves all coordinates, rotations, polarity, and
  the ESP antenna position without automatic realignment;
- fabrication DFM, production CAM, stackup, and impedance reviews refer to the
  exact deterministic Gerber archive;
- the quote clearly covers the requested PCB quantity and number of fully
  assembled units;
- both factory harnesses have buildable drawings and a supplier quote or
  purchasable exact part;
- the saved review state stops before cart submission or payment unless the
  owner separately authorizes those actions.

## Verification

The implementation updates the generated design source rather than hand-editing
derived KiCad or manufacturing artifacts. Verification includes:

- schematic ERC and PCB DRC with no unexplained violations;
- generated schematic/PCB/BOM/CPL/Gerber reproducibility tests;
- exact BOM, footprint, designator, position, side, and rotation parity;
- connector pinout and harness continuity tests in both directions;
- current-density, connector derating, voltage-drop, and thermal calculations;
- native-USB geometry and ground-return checks;
- ESP antenna keepout, enclosure clearance, and installed cable-bend checks;
- all existing ngspice safety, power, relay, and signal assertions on host and
  pinned Docker engines, updated only when the electrical design changes;
- new modeled harness contact resistance and supply-drop corners;
- final JLCDFM review bound to the deterministic fabrication archive;
- a live JLCPCB BOM/CPL preview showing no unselected populated parts;
- bench continuity and current-limited power-up before any treadmill contact.

Simulation remains behavioral evidence. It does not replace switching-loop,
EMI, RF, relay timing, harness temperature-rise, USB eye, or treadmill bench
measurements.

## Deliverables

Rev C produces:

- regenerated schematic, PCB, fabrication archive, BOM, and CPL;
- exact connector and switch selection records;
- Console and Motor harness drawings and supplier/manufacturer part list;
- updated enclosure source and fit-validation artifacts;
- updated validation and AI handoff documents;
- reproducible ngspice and repository quality-gate output;
- a complete turnkey JLCPCB quote saved for owner review without purchase.

