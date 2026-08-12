# Esp32Tap Build Guide From-To Table Design

## Purpose

Make the construction portion of the depth-first build-and-test guide use the
same point-to-point convention as `esp32tap-breadboard-from-to.pdf`, without
changing the guide's portrait cluster cards, electrical order, tests, safety
gates, or evidence workflow.

## Scope

Change only the **Build** section of each of the 11 cards in:

- `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html`
- `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf`

The Parts, Unpowered test, Powered test, STOP/PASS, identity, and signed
evidence sections remain in their current form. The assembled-board audit
guide is unchanged.

## Table Convention

Every Build section renders one ordered table with these columns:

| Step | Ref | Color | From | To | Part | Note |
|---|---|---|---|---|---|---|

- **Step** is the cluster-local construction order and remains consecutive.
- **Ref** is the original point-to-point reference number from
  `esp32tap-breadboard-from-to.pdf` when the connection is the same.
- Connections introduced by the newer depth-first guide that have no original
  reference use `NEW` plus a stable cluster-local identifier, such as
  `NEW C8-14`; they must never be assigned a misleading legacy number.
- **Color** uses the established palette: BLACK, RED, ORANGE, BLUE, VIOLET,
  YELLOW, GREEN, WHITE, or NO WIRE.
- **From** and **To** name exact component pins, component leads, removable
  jumper endpoints, connector terminals, or named nets. Direction follows the
  established from-to list where a legacy reference exists.
- **Part** identifies the physical connection medium or affected part, such as
  Wire, Jumper wire, Four wires, capacitor, or removable named jumper.
- **Note** carries orientation, state, multiplicity, or purpose information;
  safety-critical states such as “leave removed” remain explicit.

Grouped connections such as four unused-input ground ties may occupy one row
only when the Part/Note fields state the wire count and the row names every pin.
`NO WIRE` rows remain visible where they communicate an intentional open
terminal.

## Data Contract

The embedded build-guide metadata remains the source of truth. Each wiring
record gains the fields needed to render the convention:

- `step`
- `reference`
- `from`
- `to`
- `part_description`
- `note`

Existing `connection_id`, `part`, `pin`, `net`, and `color` fields remain for
the current electrical-completeness and HTML/PDF visibility checks. Shared
build/audit cluster fields are unchanged.

Legacy references are mapped from the existing from-to artifact and checked
for consistency. New-only fixture, removable-post, and staged construction
records keep stable `NEW` references.

## Presentation

The table inherits the reference artifact's dark header, alternating rows,
uppercase color labels, and compact point-to-point scan pattern, adapted to the
existing US Letter portrait card width. Column widths prioritize From and To.
Rows may wrap, but a row cannot split across pages. Existing intentional
cluster continuations remain allowed; accidental table-only spill pages are
not.

## Verification

Automated tests require:

1. all 11 Build sections to contain the seven columns in the required order;
2. exactly one visible table row per wiring metadata record, in metadata order;
3. consecutive cluster-local Step values;
4. valid wire colors;
5. unique and accurate legacy Ref values, with new-only records clearly marked;
6. visible HTML and extracted PDF values matching Step/Ref/Color/From/To/Part/Note;
7. unchanged shared cluster contracts and unchanged test/evidence content; and
8. US Letter output with no clipping, orphan spill page, browser header, or
   local path.

The focused cluster-guide suite and neighboring PDF tests must pass. Every
rendered build-guide page is inspected, with particular attention to the
densest construction tables.
