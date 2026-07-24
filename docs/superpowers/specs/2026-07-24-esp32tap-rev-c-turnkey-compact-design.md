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

The owner will only plug factory-made harnesses into the assembled board,
place the complete PCBA into a supplied tool-less or screw-fastened enclosure,
close that enclosure with supplied hardware, and connect USB and treadmill
cables. Harness manufacture may be a separate line item from JLCPCB assembly,
but it must not require owner fabrication. No permitted owner step includes
soldering, crimping, adhesive, wire dressing, component installation, or
special tooling.
The Rev C verification package must include the finished harnesses and
enclosures; drawings without delivered or exactly orderable parts are not a
turnkey result.

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
Select the smallest side-entry connector family with at least eight circuits
that satisfies the conservative electrical and assembly gates; pitch is not
fixed. Official evidence may provisionally select exact parts for schematic
and layout after confirming:

- current JLCPCB/LCSC identity and stock, with live Standard PCBA placement
  support explicitly pending;
- reel packaging and top-side automated assembly;
- positive retention and a polarized housing;
- voltage and current ratings with the approved conservative verification
  derating;
- contact resistance and temperature-rise margin for the two +8 V and two
  ground conductors;
- an available mating housing, crimp terminal, and factory harness source;
- a manufacturer STEP model and recommended footprint;
- enough insertion life for installation and service.

The later live BOM/CPL workflow must confirm Standard PCBA placement. A
missing or rejected exact row forces reselection and complete regeneration
before order readiness.

The two board interfaces must be physically non-interchangeable. Preferred
implementations are different key codes or connector families. Using different
circuit counts, such as an 8-position and a 10-position connector with unused
positions, is acceptable when it provides robust keying without a material
size penalty. Color and labels are supplemental and are not the only defense
against swapping Console and Motor.

Board-side keying alone is insufficient. The complete installed connection,
including both ordinary RJ45 treadmill plugs, must prevent Console/Motor
reversal mechanically. Acceptable implementations include installation-specific
harness lengths and captive routing that make the wrong endpoint unreachable,
or keyed RJ45 shrouds/sleeves and enclosure apertures that reject the wrong
plug. The selected scheme must be demonstrated on the actual installation.
Verification deliberately attempts every wrong board-side and treadmill-side
connection; none may mate or reach far enough to latch. Labels and color do
not satisfy this test.

The connectors must not depend on hand-applied adhesive, staking, selective
solder, or a wave-solder pallet.

Official manufacturer and catalog evidence may establish a provisional exact
part for schematic and layout. That provisional status must remain explicit
until the exact JLC BOM/CPL row is selected in the live workflow. Failure of
that later placement gate forces part reselection and regeneration; public
stock alone never authorizes an order.

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

Each assembled verification unit is supplied with two finished, tested,
strain-relieved harnesses. Before release, the repository records either an
exact orderable cable-assembly part number or a firm supplier quote covering
quantity, tooling/NRE, electrical testing, lead time, and delivery. The
project owner purchases the finished assemblies but does not arrange crimping
instructions with an operator or perform any harness fabrication.

A component-level harness recipe and production drawing are sufficient to
provisionally select PCB connectors and perform layout. The exact finished
assembly part number/firm quote, delivered harnesses, and installed
wrong-mating proof remain mandatory before the design can be described as
turnkey, order-ready, or physically validated.

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

### Owner-authorized conservative verification basis

The working `hardware/PiZeroHat` predecessor is accepted as empirical basis
for selecting parts for a Rev C verification build. It used the same two
parallel +8 V and two parallel ground conductors, specified 22 AWG power
wiring, and powered a Pololu 5 V / 1 A Pi regulator from the treadmill rail.
This is owner-attested field experience, not an instrumented current envelope.

For verification-board selection and quotation only, use a deliberately
conservative 2.0 A continuous total pass-through design envelope:

- every individual contact in the new SMT header, housing terminal, and
  harness-wire path is rated for at least 2.0 A after circuit-count and
  worst-case +85 °C derating, without credit for the parallel contact;
- each new SMT header, housing terminal, harness-wire path, and its PCB copper
  remains within rating with either parallel power or ground contact open;
  this single-open requirement excludes the standard 8P8C termination;
- both harnesses use 22 AWG or larger power/ground conductors;
- every conductive/insulating electrical element—SMT header, housing, crimp
  terminal, wire insulation, and RJ45 termination—is rated for at least 24 V
  and its qualified ambient range includes at least -20 °C through +85 °C;
- nonconductive strain-relief and enclosure elements require documented
  material, flammability where available, mechanical retention, and at least
  -20 °C through +85 °C environmental qualification; no fictitious electrical
  voltage rating is assigned to a purely mechanical part;
- modeled normal operation uses unequal contacts and a 2.0 A total load;
- transient selection uses the connector manufacturer's published envelope,
  or remains an explicit physical qualification gate if none is published.

The standard 8P8C treadmill termination is an unavoidable predecessor-interface
exception. It retains both parallel +8 V contacts (pins 2 and 8) and both
parallel grounds (pins 1 and 7) exactly as the working PiZeroHat. Select the
highest-current officially rated suitable non-magnetic RJ45 termination.
Document a worst-case unequal-sharing calculation proving that both RJ45 power
contacts and both ground contacts remain within their official circuit-count
and +85 °C derated ratings at 2.0 A total normal load. Do not claim a 2.0 A
single-RJ45-contact capability when its manufacturer does not publish one.
The RJ45 single-open 2.0 A case remains `UNSUPPORTED` and an open physical
deployment gate; it cannot be used to advance physical, production,
deployment, or turnkey status.

This basis may release conservative connector selection, PCB layout, a
verification fabrication package, and a no-purchase quote. Throughout those
activities `physical.json` remains literally `NOT_MEASURED`; no other physical
status is permitted. While it remains `NOT_MEASURED`, deployment, production
release, and `TURNKEY_QUOTED` are fail-closed. Installed current, voltage drop,
temperature, USB-ground current, and transient tests remain mandatory before
any of those statuses can advance.

No connector or harness is released for deployment until the actual treadmill
current envelope is measured. The selected interconnect then satisfies all of
these numeric acceptance limits:

- each individual +8 V and ground contact is rated, after manufacturer
  temperature and circuit-count derating, for at least 2× the measured
  worst-case continuous current assigned to that conductor;
- the design remains within ratings if one of the two nominally parallel +8 V
  contacts or one ground contact is open or has twice its allowed production
  resistance; equal current sharing is not assumed;
- every production harness power or ground conductor measures at most 100 mΩ
  end-to-end, including both terminations;
- the complete delivered Rev C pass-through—both production harnesses, every
  RJ45 interface, both board connectors, and all PCB pads, traces, and vias
  installed in the closed production enclosure—adds at most 250 mV
  supply-plus-return drop at measured worst-case continuous load;
- installed treadmill measurements establish maximum motor-hood ambient,
  airflow, conductor bundling, and duty cycle; a one-hour worst-case-current
  test reproduces that complete worst-case configuration, or a documented
  hotter equivalent chamber condition, and retains at least 20 °C margin to
  every derated connector, wire, solder joint, copper, and enclosure limit;
- measured transient current and duration remain within the manufacturer's
  published pulse envelope with at least 25% current margin;
- pads, traces, neck-downs, and vias are checked to the same continuous,
  single-degraded-contact, transient, voltage-drop, and temperature limits.

If a connector manufacturer publishes no transient-current envelope, release
requires either written manufacturer approval for the measured waveform or
empirical qualification of production-equivalent samples at 1.25× measured
peak current and duration for 1,000 cycles with no damage, latch degradation,
resistance increase over 10 mΩ, or thermal-limit violation.

Repeat the complete-path drop, temperature, and single-degraded-contact tests
with USB attached. A degraded or open treadmill ground contact must not divert
treadmill load current through the USB shield, cable, or host ground.

If the treadmill source or load cannot satisfy these limits with a compact
wire-to-board connector, size increases rather than weakening the limits.

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

The module comparison identifies an exact MPN and pad map and audits every
GPIO for fixed USB pins, strapping/reserved pins, reset and ROM-download
defaults, external pulls, ADC suitability, drive capability, RF coexistence,
flash capacity, decoupling, and safe output state through power-up, reset,
brownout, and ROM download. Migration requires regenerated package/pin audits,
a production firmware build, flash/boot tests, and the complete Rev B safety
matrix. A generic “S3-MINI compatible” claim is not sufficient.

The selected module uses its manufacturer's exact antenna keepout on every
layer and in the enclosure and preserves Rev B's minimum 15 mm axial
plastic/air clearance. Rev C does not trade that clearance for smaller volume.
The final panel drawing contains no antenna overhang, metal, tooling, carrier,
tab, or automatic realignment inside that volume. Installed RF testing under
the motor hood remains a physical gate, but it cannot be used to waive the
15 mm minimum in this revision.

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

Retain Rev B's four-layer `JLC04161H-7628` stack, 1 oz outer and 0.5 oz inner
copper, and its controlled-impedance contract. Any stack change requires a
separately approved design revision and repetition of controlled-impedance,
USB return-plane, converter-loop, relay/current-path, EMI, RF, DFM, and
fabrication validation against the exact newly quoted stack.

The enclosure is regenerated around the resulting PCB and plug-in connectors.
It provides keyed harness openings, cable strain relief, USB access, switch
access, RF clearance, and no opportunity to force the wrong harness into an
interface.

Compaction records at least three viable wire-to-board connector families, an
SMT-RJ45 baseline, and both qualified ESP module choices. For each candidate,
record PCB width, length, height, antenna volume, enclosure dimensions, cable
bend radius, service clearance, assembly support, and rejected constraints.
The final report states the PCB and installed enclosure bounding dimensions
and shows that each remaining size driver comes from an electrical, RF,
assembly, cable, or mechanical requirement.

## Manufacturing acceptance

Rev C is order-reviewable only when all of the following are true:

- all populated board parts use automated SMT assembly;
- the live JLCPCB BOM resolves every exact manufacturer/JLC part;
- the live placement list includes every populated designator;
- a saved, archive-bound PCBA preview records every populated designator's
  exact MPN/JLC code, quantity, side, rotation, and placement charge;
- JLCPCB explicitly accepts the exact side-entry connector footprint, body
  clearance, edge spacing, panelization, and depanelization without
  customer-supplied parts or manual handling;
- the quote contains no unpriced manual, wave, fixture, or post-assembly work;
- the production preview preserves all coordinates, rotations, polarity, and
  the ESP antenna position without automatic realignment;
- fabrication DFM, production CAM, stackup, and impedance reviews refer to the
  exact deterministic Gerber archive;
- the quote clearly covers the requested PCB quantity and number of fully
  assembled units;
- two finished harnesses per assembled unit have exact orderable assembly part
  numbers or a firm supplier quote and delivery path;
- the saved review state stops before cart submission or payment unless the
  owner separately authorizes those actions.

The consolidated turnkey delivered-cost package uses five fabricated PCBs,
two fully assembled PCBAs, four finished harnesses, and two finished
enclosures. It
itemizes bare fabrication, every component, setup, feeder, stencil, panel,
controlled-impedance, extended-part and placement charge, harness tooling/NRE
and unit cost, enclosure manufacturing, shipping, and applicable tax. Any
price unavailable before checkout is labeled as an omission; the record may
not call itself a complete delivered cost while a required item or operation
is unpriced. It contains separate archive-bound JLCPCB PCBA, harness-supplier,
and enclosure-supplier quotes; a JLCPCB-only subtotal is never described as
the complete turnkey cost. Owner-performed plug-in and screw/tool-less
enclosure closure is not a quoted manufacturing operation.

## Verification

The implementation updates the generated design source rather than hand-editing
derived KiCad or manufacturing artifacts. Verification includes:

- schematic ERC and PCB DRC with no unexplained violations;
- generated schematic/PCB/BOM/CPL/Gerber reproducibility tests;
- exact BOM, footprint, designator, position, side, and rotation parity;
- connector pinout and harness continuity tests in both directions;
- current-density, connector derating, voltage-drop, and thermal calculations;
- deliberate wrong-connection tests at both board and RJ45 ends;
- native-USB geometry and ground-return checks;
- ESP antenna keepout, enclosure clearance, and installed cable-bend checks;
- all existing ngspice safety, power, relay, and signal assertions on host and
  pinned Docker engines, updated only when the electrical design changes;
- new modeled harness contact resistance and supply-drop corners;
- production-length harness tests for serial edge integrity, capacitance,
  inductance, ESD exposure, ground behavior, and powered/dead-board leakage,
  compared with real treadmill captures;
- final JLCDFM review bound to the deterministic fabrication archive;
- a live JLCPCB BOM/CPL preview showing no unselected populated parts;
- bench continuity and current-limited power-up before any treadmill contact.

Simulation remains behavioral evidence. It does not replace switching-loop,
EMI, RF, relay timing, harness temperature-rise, USB eye, or treadmill bench
measurements.

Rev B's physical safety gates remain explicit:

- USB and treadmill ground are not isolated; measure host-to-treadmill ground
  potential and connection current and approve the bonding/isolation plan
  before simultaneous attachment;
- first treadmill contact is Proxy-only with relay energization compiled out;
- an Emulate-capable first article is not connected to the treadmill;
- later Emulate testing requires the belt clear, the physical safety key
  immediately accessible, and completion of the staged bench safety matrix.

An assembled Rev C remains **HOLD — READY FOR VENDOR AND BENCH GATES**, not
treadmill-ready, until those gates and Rev B's remaining physical evidence are
closed.

## Deliverables

Rev C produces:

- regenerated schematic, PCB, fabrication archive, BOM, and CPL;
- exact connector and switch selection records;
- Console and Motor harness drawings and supplier/manufacturer part list;
- updated enclosure source and fit-validation artifacts;
- updated validation and AI handoff documents;
- reproducible ngspice and repository quality-gate output;
- a consolidated turnkey delivered-cost package, including the archive-bound
  JLCPCB PCBA quote and firm harness/enclosure costs, saved for owner review
  without purchase.
