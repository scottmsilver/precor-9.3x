# DevKit Breadboard Wizard Design

## Goal

Provide a browser-based, step-by-step construction guide for the approved
ESP32-S3 DevKit sidecar harness. A person at the electronics bench must be able
to place every component and jumper without inferring connectivity from a
schematic or relying on a DevKit header's physical row position.

## Delivery

The guide is one self-contained HTML document served by the existing visual
companion. It uses an inline SVG for the breadboard, components, switches,
rails, and color-coded nets. It requires no package manager, remote assets, or
JavaScript dependencies.

The DevKit is drawn beside the breadboard. Connections terminate at labeled
DevKit pads (`3V3`, `GND`, `GPIO4`, and so on), because board header ordering
can vary. The user follows the board's printed labels.

## Interaction

The screen contains:

- a persistent breadboard overview with zoom controls;
- Back and Next buttons;
- one highlighted component or wire per step;
- a short placement instruction and electrical-purpose explanation;
- a confirmation checkbox for each step;
- a persistent netlist and expected switch-state table;
- a final unpowered inspection screen asking for an overhead photograph.

Completed steps remain visible but muted. The active step is visually strong;
future wiring is ghosted so the destination is visible without creating a
cluttered all-at-once diagram. Progress is stored only in browser local storage.

## Exact Circuit

- Rails: DevKit `3V3` to red rail and `GND` to ground rail. No `5V` connection.
- GPIO4 and GPIO5: individual 10 kOhm pull-ups to 3V3; each node goes to one
  DPDT common. One throw from each pole goes to ground; the other throws remain
  disconnected.
- GPIO6: 47 kOhm pull-down to ground; GPIO6 connects through an SPST and 1 kOhm
  resistor to 3V3.
- GPIO7: 10 kOhm pull-up to 3V3; GPIO7 connects through an SPST to ground.
- GPIO15 and GPIO21: each has a 47 kOhm pull-down to ground and a separate
  path through 1 kOhm then LED anode, with LED cathode to ground.
- GPIO16, GPIO17, GPIO18, and GPIO38 remain disconnected.

The BOM is exactly three 10 kOhm, three 47 kOhm, three 1 kOhm, two LEDs, one
DPDT, two SPST switches, and jumpers.

## Safety and Validation

The wizard begins with power disconnected and does not instruct the user to
connect the DevKit until all passive placement steps are confirmed. It warns
that DPDT lug arrangements vary and requires a continuity-meter check of both
commons and grounded throws. It shows LED anode/cathode orientation and rail
polarity on every relevant step.

The final screen verifies:

- no 5V, native USB, or treadmill connection;
- GPIO16/17/18/38 are empty;
- resistor values and LED polarity;
- no split breadboard rail is assumed continuous without a jumper;
- switches start in the documented baseline state;
- both tripwire LEDs remain dark after later power-up.

The page stops at an unpowered photo-review handoff. Live eight-state testing
is a later supervised step using the guarded Pi bench tool.

## Verification

The HTML contains a machine-readable netlist object used to render labels and
the textual netlist, preventing two independently maintained wiring sources.
A lightweight browser test checks all nets, BOM counts, step ordering, safety
warnings, disconnected pins, and that power-up is absent from the construction
steps. Manual browser verification covers desktop and phone-sized layouts,
Next/Back navigation, zoom, and local progress reset.
