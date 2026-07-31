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

Every construction step has one required confirmation. `Next` remains disabled
until it is checked; the DPDT step additionally requires confirmation that both
common/throw pairs were identified with a continuity meter. The final photo
handoff is inaccessible until every prior confirmation is complete. `Back`
never clears confirmations. A visible `Reset progress` control asks for
confirmation, clears only this page's local-storage key, and returns to Step 1.

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
- GPIO6 at column 10 upper, with the far side of its SPST at column 12 upper;
- GPIO7 at column 15 upper;
- GPIO15 at column 20 upper, with the 1 kOhm crossing the trench to column 20
  lower, LED anode at column 20 lower, and cathode at column 22 lower;
- GPIO21 at column 25 upper, with the 1 kOhm crossing the trench to column 25
  lower, LED anode at column 25 lower, and cathode at column 27 lower.

The DPDT is shown off-board with flying leads: its commons go to columns 4 and
6 upper, its two grounded throws go to the ground rail, and its other throws
are individually insulated and disconnected. The GPIO6 SPST connects columns
10 and 12 upper; a 1 kOhm resistor connects column 12 upper to 3V3. The GPIO7
SPST connects column 15 upper to ground. Every resistor, LED lead, switch lead,
rail jumper, and DevKit jumper receives explicit endpoint labels in the SVG.

## Construction Order

1. Disconnect UART USB and all other power; inventory the exact BOM.
2. Identify one continuous 3V3 rail segment and one continuous ground rail
   segment covering columns 1-27 with a continuity meter.
3. Place the GPIO4/GPIO5 10 kOhm pull-ups and continuity-identified DPDT leads.
4. Place the GPIO6 47 kOhm pull-down, SPST, and 1 kOhm 3V3 path.
5. Place the GPIO7 10 kOhm pull-up and ground-switch path.
6. Place both 47 kOhm tripwire pull-downs, 1 kOhm resistors, and LEDs, checking
   anode/cathode orientation.
7. Perform unpowered resistance/continuity and rail-short checks.
8. With USB still disconnected, connect DevKit GND, then 3V3.
9. Connect GPIO4, GPIO5, GPIO6, GPIO7, GPIO15, and GPIO21 by printed board label.
10. Confirm GPIO16/17/18/38 are empty and submit an overhead photo for review.

No construction step applies power. Powered testing is a separate supervised
workflow after photo approval.

## Switch Truth Table

The baseline is DPDT **away from its grounded throws**, GPIO6 SPST open, and
GPIO7 SPST open. It reads `(GPIO4,GPIO5,GPIO6,GPIO7) = (1,1,0,1)`.

The DPDT grounded position produces GPIO4/GPIO5 = `0/0`; away produces `1/1`.
GPIO6 open/closed produces `0/1`. GPIO7 open/closed produces `1/0`.

| DPDT | GPIO6 SPST | GPIO7 SPST | Expected GPIO4,5,6,7 |
|---|---|---|---|
| Away | Open | Open | 1,1,0,1 |
| Away | Open | Closed | 1,1,0,0 |
| Away | Closed | Open | 1,1,1,1 |
| Away | Closed | Closed | 1,1,1,0 |
| Grounded | Open | Open | 0,0,0,1 |
| Grounded | Open | Closed | 0,0,0,0 |
| Grounded | Closed | Open | 0,0,1,1 |
| Grounded | Closed | Closed | 0,0,1,0 |

## Safety and Validation

The wizard begins with power disconnected and does not instruct the user to
connect the DevKit until all passive placement steps are confirmed. It warns
that DPDT lug arrangements vary and requires a continuity-meter check of both
commons and grounded throws. It shows LED anode/cathode orientation and rail
polarity on every relevant step.

The final unpowered screen verifies only presently inspectable items:

- no 5V, native USB, or treadmill connection;
- GPIO16/17/18/38 are empty;
- resistor values and LED polarity;
- no split breadboard rail is assumed continuous without a jumper;
- switches start in the documented baseline state;

The page stops at an unpowered photo-review handoff. Live eight-state testing
is a later supervised step using the guarded Pi bench tool. Both tripwire LEDs
remaining dark is an explicit acceptance criterion of that later powered test,
not something the unpowered wizard claims to verify.

## Verification

The HTML contains a machine-readable netlist object used to render labels and
the textual netlist, preventing two independently maintained wiring sources.
A lightweight browser test checks all nets, BOM counts, step ordering, safety
warnings, disconnected pins, the eight-state truth table, required-confirmation
gating, photo-handoff gating, and that power-up is absent from construction
steps. Manual browser verification covers desktop and phone-sized layouts,
Next/Back navigation, zoom, persistent progress, and confirmed progress reset.
