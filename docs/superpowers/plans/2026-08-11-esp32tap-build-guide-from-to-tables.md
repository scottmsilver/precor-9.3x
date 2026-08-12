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

- [ ] **Step 1: Add table metadata helpers**

Parse `build_rows`, index legacy from-to metadata by Ref, normalize action punctuation/whitespace, and extract each cluster's visible Build section.

- [ ] **Step 2: Add schema and consumption tests**

Require every row to contain `step`, `reference`, `connection_ids`, `action_ids`, `from`, `to`, `color`, `part_description`, `note`, and `directive`. Require consecutive steps and require every wiring/action ID to be consumed exactly once.

- [ ] **Step 3: Add reference mapping tests**

Require plain legacy refs for one-to-one/endpoint-pair mappings, qualified refs only for declared groups, and `NEW <connection-id>` for unmapped rows. Assert mapped legacy endpoints, color, part/wire count, and note semantics against the reference artifact.

- [ ] **Step 4: Add action parity and visibility tests**

Require each row directive to equal the ordered normalized concatenation of its selected build actions. Require HTML and PDF Build tables to render the seven headers and every row value, including the complete directive in Note.

- [ ] **Step 5: Lock Cluster 11 ordering**

Require extracted HTML/PDF order `Step 1 → Independent isolated-map evidence → Step 2`.

- [ ] **Step 6: Run the focused tests and confirm RED**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py -k 'from_to or build_table'
```

Expected: FAIL because `build_rows` and rendered table headers do not exist.

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

- [ ] **Step 2: Verify electrical/action coverage**

Run the schema, consumption, legacy mapping, and action parity tests. Fix mappings until every existing wiring record and action is consumed exactly once without changing shared fields or electrical content.

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

Use `pdftotext -layout` to verify all table headers/rows/directives and Cluster 11 ordering. Use `pdfinfo` to require every page at 612 × 792 points.

- [ ] **Step 4: Inspect every rendered page**

Render with `pdftoppm`, make a contact sheet, and inspect all pages. Inspect the densest table pages full-size for clipped columns, unreadable Note text, split rows, or orphan spills. Adjust CSS and rerender until clean.

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
git pull --rebase
git push origin feat/esp32tap-devkit-bringup
git status --short --branch
```
