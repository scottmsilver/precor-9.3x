# Esp32Tap Build Guide From-To Tables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the build guide's prose construction lists with traceable Step/Ref/Color/From/To/Part/Note tables while preserving its portrait cluster test cards and electrical contracts.

**Architecture:** Keep the existing `wiring` and `actions.build` metadata as the electrical/action sources, then add an explicit `build_rows` presentation mapping that handles endpoint pairs, qualified grouped legacy references, and new-only connections. Render those rows into compact portrait tables, with Cluster 11 split around its mandatory isolated-map signoff.

**Tech Stack:** Static HTML/CSS, embedded JSON metadata, Python/pytest, Poppler (`pdftotext`, `pdfinfo`), headless Google Chrome.

---

## File Structure

- Modify `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`: table schema, legacy mapping, action parity, HTML/PDF visibility, and Cluster 11 ordering tests.
- Modify `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html`: `build_rows` metadata, stable build action IDs, from-to tables, and print CSS.
- Regenerate `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf`: printable artifact.
- Reference only `hardware/Esp32Tap/bringup/esp32tap-breadboard-from-to.html`: authoritative legacy rows and references.

### Task 1: Lock the from-to presentation contract

**Files:**
- Modify: `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`
- Reference: `docs/superpowers/specs/2026-08-11-esp32tap-build-guide-from-to-tables-design.md`
- Reference: `hardware/Esp32Tap/bringup/esp32tap-breadboard-from-to.html`

- [ ] **Step 1: Add authoritative mapping constants and table helpers**

Add these exact test names:

- `test_build_from_to_rows_cover_wiring_and_actions_once`
- `test_build_from_to_legacy_references_match_source`
- `test_build_from_to_tables_render_in_html`
- `test_build_from_to_tables_render_in_pdf`
- `test_build_from_to_cluster_11_keeps_map_gate_between_steps`

Parse the legacy source table rows (`#`, From, To, Part, Note) and its
`wire_colors` JSON into `LEGACY_ROWS[1..126]`. Parse `build_rows`, normalize
action punctuation/whitespace, and extract each cluster's visible Build
section.

Lock legacy ownership with this exact reference plan:

```python
EXPECTED_LEGACY_REFS_BY_CLUSTER = {
    1: {*range(15, 20), 23, 24},
    2: {20, 21, 22, 25, 26, 27, 28, 31},
    3: {29, 30},
    4: set(range(32, 58)),
    5: set(range(58, 74)),
    6: set(range(74, 88)),
    7: {*range(88, 92), *range(105, 112)},
    8: set(range(92, 101)),
    9: set(range(112, 127)),
    10: set(),
    11: {*range(1, 15), *range(101, 105)},
}
QUALIFIED_LEGACY_GROUPS = {70: 4, 71: 2, 97: 6, 98: 3}
```

Connections not covered by this ownership map are `NEW <connection-id>`.
This includes the additional DevKit posts/decoupling in Cluster 3, local-only
test fixture/relay-selection rows in Cluster 8, observer/setup rows in Cluster
10, and any depth-first staging link with no identical legacy endpoints.
Do not steal a reference from another cluster merely because it shares a net.

- [ ] **Step 2: Add schema and consumption tests**

Require every row to contain `step`, `reference`, `connection_ids`, `action_ids`, `from`, `to`, `color`, `part_description`, `note`, and `directive`. Require consecutive steps and require every wiring/action ID to be covered at least once. Permit repeated IDs only when the endpoint-graph check proves that endpoint participates in each physical row; reject arbitrary reuse or permutation.

Preserve each existing `actions.build[*].connection_id`; also set
`action_id == connection_id`. Update
`test_each_build_connection_has_one_numbered_wiring_record` so it continues to
require one wiring record per build action and matching ordered connection IDs,
but replaces its prose-list assertion with coverage through `build_rows`.
Many wiring/actions may feed one physical endpoint-pair row; the underlying
one-to-one wiring/action relationship remains unchanged.

- [ ] **Step 3: Add reference mapping tests**

Require plain legacy refs for one-to-one/endpoint-pair mappings, qualified refs only for declared groups, and `NEW <connection-id>` for unmapped rows. Assert mapped legacy endpoints, color, part/wire count, and note semantics against the reference artifact.

For a plain legacy row, normalized `from`, `to`, `color`, and
`part_description` equal `LEGACY_ROWS[ref]`. For qualified groups, suffixes are
alphabetic in pin order (`70a`…`70d`, `71a`…`71b`, `97a`…`97f`,
`98a`…`98c`), and each Note contains the unsuffixed source Note plus
`legacy Ref <n>; <count> wires` (or `<count> intentional opens`). Assert the
union of plain/qualified numeric bases per cluster equals the exact ownership
map above.

- [ ] **Step 4: Add action parity and visibility tests**

Require each row directive to equal the ordered normalized concatenation of its selected build actions. Require HTML and PDF Build tables to render the seven headers and every row value, including the complete directive in Note.

- [ ] **Step 5: Lock Cluster 11 ordering**

Require extracted HTML/PDF order `Step 1 → Independent isolated-map evidence → Step 2`.

- [ ] **Step 6: Run the focused tests and confirm RED**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py \
  -k 'test_build_from_to_'
```

Expected: the five named tests are collected and FAIL because `build_rows` and
rendered table headers do not exist (never exit 5 for zero collection).

- [ ] **Step 7: Commit contract tests**

```bash
git add hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py
git commit -m "test(Esp32Tap): lock build-guide from-to tables"
```

### Task 2: Map construction records and render tables

**Files:**
- Modify: `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html`
- Test: `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`

- [ ] **Step 1: Add stable action IDs and build_rows**

Assign stable IDs to every `actions.build` entry. Add ordered `build_rows` for all 11 clusters. Use explicit endpoint-pair mappings, qualified grouped references, and stable `NEW` references exactly as the approved design specifies.

Use the ownership and group constants from Task 1 as the complete legacy
mapping checklist. For each legacy row, select the existing endpoint records
whose `part + pin + net` describe its From/To endpoints; store all selected
`connection_ids` and matching `action_ids` in original operator order. The
`directive` is the normalized concatenation of those actions. Do not alter or
delete `connection_id`.

- [ ] **Step 2: Verify electrical/action coverage**

Run the schema, coverage, legacy mapping, and action parity tests. Fix mappings until every existing wiring record and action is covered without changing shared fields or electrical content. Reuse an ID only where exact endpoint-graph validation proves that the same endpoint participates in multiple physical legacy wires.

- [ ] **Step 3: Add table CSS**

Add a `.from-to` table with portrait widths for Step, Ref, Color, From, To, Part, and Note; dark header, alternating rows, no row splitting, compact text, and visible uppercase color labels.

- [ ] **Step 4: Replace prose Build lists**

Render each cluster's rows in metadata order. Keep all other card sections byte-equivalent in visible meaning. Split Cluster 11 after Step 1, leave the writable signoff in place, then resume the table at Step 2.

- [ ] **Step 5: Run HTML tests**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py -k 'not pdf'
```

Expected: PASS.

### Task 3: Render, inspect, verify, and publish

**Files:**
- Modify: `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf`
- Verify: all files above

- [ ] **Step 1: Render the PDF**

```bash
google-chrome --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$PWD/hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf" \
  "file://$PWD/hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html"
```

- [ ] **Step 2: Run focused and neighboring tests**

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py \
  hardware/Esp32Tap/tests/test_point_to_point_clustered_pdf.py \
  hardware/Esp32Tap/tests/test_schematic_docs.py
```

Expected: PASS.

- [ ] **Step 3: Inspect extracted content and page size**

Run these exact commands:

```bash
pdftotext -layout \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf \
  /tmp/esp32tap-build-from-to.txt
rg -n 'Step +Ref +Color +From +To +Part +Note|Independent isolated-map evidence' \
  /tmp/esp32tap-build-from-to.txt
pdfinfo -f 1 -l 999999 \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf \
  | rg '^(Pages|Page size|Page +[0-9]+ size)'
```

The table test performs the objective row/directive/order assertions. Every
reported page size must be `612 x 792 pts`.

- [ ] **Step 4: Inspect every rendered page**

Run:

```bash
review_dir=$(mktemp -d /tmp/esp32tap-from-to.XXXXXX)
pdftoppm -png -r 120 \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf \
  "$review_dir/page"
montage "$review_dir"/page-*.png -thumbnail 255x330 -tile 4x \
  -geometry +8+8 "$review_dir/contact.png"
```

Inspect `contact.png`, then inspect every page containing Clusters 4, 5, 8, 9,
and 11 at original resolution. Reject any page with a row split across pages,
clipped rightmost Note column, text below the footer, table-only orphan with no
heading/continuation label, or Note text too small to read at 100%. Adjust CSS
and rerender until none remain.

- [ ] **Step 5: Commit implementation**

```bash
git add \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf
git commit -m "docs(Esp32Tap): use from-to build tables"
```

- [ ] **Step 6: Final review and push**

Request spec and quality review, resolve Important findings, run fresh verification, close `precor-9_3x-zl6`, then:

```bash
git status --short
git -c rebase.autoStash=true pull --rebase
git push origin feat/esp32tap-devkit-bringup
bd dolt push
git status
```

Before and after pull/push, verify the unrelated existing modifications to
`hardware/Esp32Tap/bringup/full-breadboard-model.json` and
`hardware/Esp32Tap/tests/test_breadboard_wizard.py` remain present and
unstaged. Stage only the files named by this plan. Final `git status` must say
the branch is up to date with origin while listing those two preserved edits.
