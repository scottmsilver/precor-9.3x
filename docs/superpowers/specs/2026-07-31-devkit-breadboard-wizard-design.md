# DevKit Breadboard Wizard Design

## Goal

Provide a browser-based, step-by-step construction guide for the approved
ESP32-S3 DevKit sidecar harness. A person at the electronics bench must be able
to place every component and jumper without inferring connectivity from a
schematic or relying on a DevKit header's physical row position.

## Delivery

The guide is one self-contained HTML document served by the existing visual
companion. It uses an inline SVG for the breadboard, components, removable jumpers,
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
- a persistent netlist and expected jumper-state table;
- a final unpowered inspection screen asking for an overhead photograph.

Every construction step has one required confirmation. `Next` remains disabled
until it is checked. The final photo
handoff is inaccessible until every prior confirmation is complete. `Back`
never clears confirmations. A visible `Reset progress` control asks for
confirmation, clears only this page's local-storage key, and returns to Step 1.

Completed steps remain visible but muted. The active step is visually strong;
future wiring is ghosted so the destination is visible without creating a
cluttered all-at-once diagram. Progress is stored only in browser local storage.

## Exact Circuit

- Rails: DevKit `3V3` to red rail and `GND` to ground rail. No `5V` connection.
- GPIO4 and GPIO5: individual 10 kOhm pull-ups to 3V3. One removable mode jumper
  is installed from exactly one of these nodes to ground in a sampled state:
  GPIO4 grounded/GPIO5 open is `BYPASS`; GPIO4 open/GPIO5 grounded is `EMULATE`.
- GPIO6: 47 kOhm pull-down to ground; a removable jumper connects GPIO6 to a
  second tie group that reaches 3V3 through a 1 kOhm resistor.
- GPIO7: 10 kOhm pull-up to 3V3; a removable jumper connects GPIO7 to ground.
- GPIO15 and GPIO21: each has a 47 kOhm pull-down to ground and a separate
  path through 1 kOhm then LED anode, with LED cathode to ground.
- GPIO16, GPIO17, GPIO18, and GPIO38 remain disconnected.

The BOM is exactly three 10 kOhm, three 47 kOhm, three 1 kOhm, two LEDs, and
removable jumper wires. No SPST, DPDT, relay, or other switch is required.

## Breadboard Geometry and Coordinates

The drawing uses a conventional solderless breadboard coordinate system:
numbered columns `1` through `30`, upper tie groups `a-e`, lower tie groups
`f-j`, and a center trench between `e` and `f`. Holes within one five-hole tie
group at one column are connected; the two sides of the trench are not. Only
columns 1 through 27 and one continuous pair of power rails are used. If the
user's rail is visibly split or lacks continuity from column 1 through 27, the
user must use one unsplit segment instead; the wizard never asks the user to
assume or bridge an unknown split.

Exact signal tie groups are:

- GPIO4 at column 4 upper (`a4-e4`), GPIO5 at column 6 upper;
- GPIO6 at column 10 upper, with the 3V3-side tie group at column 12 upper;
- GPIO7 at column 15 upper;
- GPIO15 at column 20 upper, with the 1 kOhm crossing the trench to column 20
  lower, LED anode at column 20 lower, and cathode at column 22 lower;
- GPIO21 at column 25 upper, with the 1 kOhm crossing the trench to column 25
  lower, LED anode at column 25 lower, and cathode at column 27 lower.

The single removable mode jumper is drawn with two labeled possible paths:
column 4 upper to ground for `BYPASS`, or column 6 upper to ground for
`EMULATE`. In the baseline photo it is installed in the `BYPASS` path. The
GPIO6 removable jumper connects columns 10 and 12 upper; a
1 kOhm resistor connects column 12 upper to 3V3. The GPIO7 removable jumper
connects column 15 upper to ground. Every resistor, LED lead, removable jumper,
rail jumper, and DevKit jumper receives explicit endpoint labels in the SVG.

## Construction Order

The following are phases in the progress display. Each placement phase expands
into atomic UI steps containing exactly one resistor, LED, jumper, or wire:

1. Disconnect UART USB and all other power; inventory the exact BOM.
2. Identify one continuous 3V3 rail segment and one continuous ground rail
   segment covering columns 1-27 with a continuity meter.
3. Place, in order: GPIO4 10 kOhm pull-up; GPIO5 10 kOhm pull-up; install the
   single mode jumper from GPIO4 to ground for baseline `BYPASS`; identify
   GPIO5-to-ground as the alternate `EMULATE` placement without adding a second
   jumper.
4. Place, in order: GPIO6 47 kOhm pull-down; GPIO6 1 kOhm-to-3V3 resistor;
   identify columns 10 and 12 as the removable-jumper endpoints, leaving the
   jumper off the board for baseline `OPEN`.
5. Place the GPIO7 10 kOhm pull-up and identify the GPIO7-to-ground removable
   jumper endpoints, leaving the jumper off the board for baseline `OPEN`.
6. Place, in order: GPIO15 47 kOhm pull-down; GPIO15 1 kOhm resistor; LED1;
   LED1-cathode ground wire; GPIO21 47 kOhm pull-down; GPIO21 1 kOhm resistor;
   LED2; LED2-cathode ground wire.
7. Perform the pre-DevKit unpowered checks defined below.
8. With USB still disconnected, place the DevKit GND jumper, then the DevKit
   3V3 jumper as two separate atomic steps.
9. Place six separate signal jumpers in this order: GPIO4, GPIO5, GPIO6, GPIO7,
   GPIO15, GPIO21, always following the printed board label.
10. Perform the post-connection unpowered checks defined below, confirm
    GPIO16/17/18/38 are empty, then complete the photo handoff.

No construction step applies power. Powered testing is a separate supervised
workflow after photo approval.

### Normative unpowered meter checks

All checks use a de-energized board with UART USB disconnected. A low-resistance
continuity result is less than 2 Ohm. A short is a stable reading below 100 Ohm;
ignore only the meter's initial capacitive transient and wait five seconds.

Before DevKit jumpers are installed:

- the selected 3V3 rail is continuous from its first to last used hole;
- the selected ground rail is continuous from its first to last used hole;
- 3V3-to-ground is not a short;
- each LED cathode has low-resistance continuity to ground;
- baseline `BYPASS` gives GPIO4-to-ground continuity below 2 Ohm and no
  GPIO5-to-ground continuity; fully removing and then reinstalling the same
  mode jumper from GPIO5 to ground gives the exact opposite result, then it is
  returned to GPIO4 for the photo;
- GPIO6 and GPIO7 have no jumper continuity in baseline and show continuity
  only while their respective removable jumper is installed.

After every DevKit jumper is installed, repeat 3V3-to-ground (not a short), then
require low-resistance continuity for DevKit GND-to-ground rail, DevKit
3V3-to-3V3 rail, and each of the six DevKit GPIO labels to its named signal tie
group. Confirm no jumper or component touches GPIO16, GPIO17, GPIO18, or GPIO38.
These results are individual required confirmations before the photo screen.

## Jumper Truth Table

The baseline is `BYPASS`: the GPIO4 ground jumper is installed, the GPIO5
ground jumper is removed, and the GPIO6 and GPIO7 jumpers are removed. Baseline
reads `(GPIO4,GPIO5,GPIO6,GPIO7) = (0,1,0,1)`.

For `EMULATE`, completely remove the mode jumper from GPIO4-to-ground before
installing that same jumper from GPIO5-to-ground. Never add a second mode
jumper. `BYPASS` produces GPIO4/GPIO5 = `0/1`; `EMULATE` produces
`1/0`. GPIO6 removed/installed produces `0/1`. GPIO7 removed/installed produces
`1/0`.

| Mode jumper | GPIO6 jumper | GPIO7 jumper | Expected GPIO4,5,6,7 |
|---|---|---|---|
| BYPASS (GPIO4 grounded) | Removed | Removed | 0,1,0,1 |
| BYPASS (GPIO4 grounded) | Removed | Installed | 0,1,0,0 |
| BYPASS (GPIO4 grounded) | Installed | Removed | 0,1,1,1 |
| BYPASS (GPIO4 grounded) | Installed | Installed | 0,1,1,0 |
| EMULATE (GPIO5 grounded) | Removed | Removed | 1,0,0,1 |
| EMULATE (GPIO5 grounded) | Removed | Installed | 1,0,0,0 |
| EMULATE (GPIO5 grounded) | Installed | Removed | 1,0,1,1 |
| EMULATE (GPIO5 grounded) | Installed | Installed | 1,0,1,0 |

## Safety and Validation

The wizard begins with power disconnected and does not instruct the user to
connect the DevKit until all passive placement steps are confirmed. It warns
that GPIO4 and GPIO5 ground jumpers are mutually exclusive and requires the
complementary continuity check above. It shows LED anode/cathode orientation and rail
polarity on every relevant step.

The final unpowered screen verifies only presently inspectable items:

- no 5V, native USB, or treadmill connection;
- GPIO16/17/18/38 are empty;
- resistor values and LED polarity;
- no split breadboard rail is assumed continuous without a jumper;
- removable jumpers start in the documented `BYPASS`, GPIO6-removed,
  GPIO7-removed baseline state, with exactly one mode jumper present;

The page stops at an unpowered photo-review handoff. Live eight-state testing
is a later supervised step using the guarded Pi bench tool. Both tripwire LEDs
remaining dark is an explicit acceptance criterion of that later powered test,
not something the unpowered wizard claims to verify.

The handoff screen says: `Keep UART USB unplugged. Take one sharp photo directly
overhead with every rail, resistor band, removable jumper, LED lead, and DevKit label
visible. Attach that photo in this chat and wait for approval before applying
power.` The final checkbox is `I attached the overhead photo in this chat`; it
records the handoff but does not imply electrical approval.

## Verification

The HTML contains a machine-readable netlist object used to render labels and
the textual netlist, preventing two independently maintained wiring sources.
A lightweight browser test checks all nets, BOM counts, step ordering, safety
warnings, disconnected pins, the eight-state truth table, required-confirmation
gating, photo-handoff gating, and that power-up is absent from construction
steps. Manual browser verification covers desktop and phone-sized layouts,
Next/Back navigation, zoom, persistent progress, and confirmed progress reset.
