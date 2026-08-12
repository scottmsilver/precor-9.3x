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

Every Build section renders an ordered point-to-point table with these columns:

| Step | Ref | Color | From | To | Part | Note |
|---|---|---|---|---|---|---|

- **Step** is the cluster-local construction order and remains consecutive.
- **Ref** is the original point-to-point reference number from
  `esp32tap-breadboard-from-to.pdf` when the rendered physical connection is
  the same.
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

### Legacy-reference mapping

The old artifact models physical endpoint-to-endpoint wires, while the current
metadata often models each endpoint's membership in a named net. The table
therefore uses an explicit mapping layer instead of assuming one wiring record
equals one legacy reference:

- **Two endpoint records to one legacy wire:** combine the current records into
  one From-to-To row and show the legacy Ref once. Example: current
  `F1 lead 2 → FUSED_8V` plus `D1 anode → FUSED_8V` renders as legacy Ref 16,
  `RXEF075 lead 2 → 1N5822 anode`.
- **One grouped legacy reference to several physical wires:** render one row
  per physical wire and qualify the repeated legacy reference with a stable
  suffix, for example `70a`, `70b`, `70c`, `70d`. The Note states “legacy Ref
  70; four wires.”
- **Intentional opens:** grouped `NO WIRE` terminals use qualified suffixes in
  the same way, with every open pin named.
- **Direct one-to-one legacy match:** show the plain numeric Ref.
- **No legitimate legacy match:** show `NEW` plus the stable connection ID.

The mapping is stored explicitly and tested against an authoritative expected
mapping derived from the legacy from-to metadata. A numeric legacy reference
may repeat only through its declared qualified group. Every legacy Ref used by
the build guide must resolve to the same physical endpoints, color, wire count,
and note semantics as the reference artifact.

Each display row otherwise represents one physical wire or one intentional
open. `NO WIRE` rows remain visible where they communicate an intentional open
terminal.

## Data Contract

The embedded build-guide metadata remains the electrical source of truth. A
separate `build_rows` array per cluster is the presentation/traceability view
and contains:

- `step`
- `reference`
- `connection_ids` (one or more existing wiring records supporting the row)
- `action_ids` (the corresponding build actions, in operator order)
- `from`
- `to`
- `color`
- `part_description`
- `note`
- `directive` (the complete combined operator action/state instruction)

Existing `wiring` records and `actions.build` directives remain. Each build
action gains a stable `action_id`. Tests require every wiring record and every
build action to be covered by at least one `build_rows` entry. Reuse is allowed
only when the same endpoint membership participates in multiple physical
wires—for example an IC supply-pin record supports both the rail-to-pin row and
the pin-to-decoupling-capacitor row. The endpoint graph must prove every reuse;
arbitrary or unrelated ID reuse is rejected.

For each row, `action_ids` lists the supporting actions in operator order and
`directive` is their ordered, whitespace-normalized concatenation. Automated
comparison is exact after normalizing whitespace and terminal punctuation; no
fuzzy matcher is used. A reused action may therefore appear in more than one
row Note when it supplies necessary context for both physical wires.

The complete `directive` is operator-visible in the **Note** cell, following
any short legacy note/purpose text. It includes initials/evidence prompts,
staged sequencing, “only after PASS” conditions, removable-jumper state, wire
count, polarity, and leave-open/NO-WIRE instructions. Both HTML and extracted
PDF tests require the directive in that row's Note cell. This prevents the
parallel structures from diverging, hiding, or dropping operator-critical
text. Shared build/audit cluster fields are unchanged.

Legacy references are mapped from the existing from-to artifact and checked
for consistency. Reference ownership follows the cluster that constructs the
part in the depth-first guide, not the old artifact's cluster label; notably,
bulk-capacitor Refs 23–24 belong to current Cluster 1. New-only fixture,
removable-post, and staged construction records keep stable `NEW` references.

## Presentation

The table inherits the reference artifact's dark header, alternating rows,
uppercase color labels, and compact point-to-point scan pattern, adapted to the
existing US Letter portrait card width. Column widths prioritize From and To.
Rows may wrap, but a row cannot split across pages. Existing intentional
cluster continuations remain allowed; accidental table-only spill pages are
not.

Normally a Build section uses one contiguous point-to-point table. Cluster 11
is the sole intentional exception: its table is split after isolated-mapping
Step 1 so the existing writable **Independent isolated-map evidence — complete
before Step 2** table and signed gate remain physically between Step 1 and the
commoning steps. The second point-to-point table resumes at Step 2 with the
same seven columns. Automated and PDF-text checks enforce this ordering:

`Step 1 → isolated-map evidence/signoff → Step 2`.

No other test/evidence block is moved.

## Verification

Automated tests require:

1. all 11 Build sections to contain the seven columns in the required order;
2. every wiring record and build action to be covered at least once by the
   ordered `build_rows` mapping; repeated IDs are allowed only when exact
   endpoint-graph validation proves their use in multiple physical rows;
3. consecutive cluster-local Step values and unique displayed row identities;
4. valid wire colors;
5. accurate legacy Ref endpoints/colors/notes, with qualified repetitions only
   for declared groups and new-only records clearly marked;
6. visible HTML and extracted PDF values matching
   Step/Ref/Color/From/To/Part/Note, including the complete directive in Note;
7. exact normalized parity between every `build_rows.directive` and the
   ordered actions selected by its `action_ids`, including
   staged/PASS/jumper/initials instructions;
8. unchanged shared cluster contracts and unchanged test/evidence content,
   including Cluster 11's Step 1 → evidence/signoff → Step 2 ordering; and
9. US Letter output with no clipping, orphan spill page, browser header, or
   local path.

The focused cluster-guide suite and neighboring PDF tests must pass. Every
rendered build-guide page is inspected, with particular attention to the
densest construction tables.
