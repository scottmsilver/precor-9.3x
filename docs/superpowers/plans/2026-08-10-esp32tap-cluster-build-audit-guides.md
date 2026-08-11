# Esp32Tap Cluster Build and Audit Guides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the whole-board checklist with two tested printable guides: an empty-board depth-first cluster build/test guide and a separately assembled-board cluster audit/test guide.

**Architecture:** Keep each guide as a self-contained static HTML source rendered to a committed US Letter PDF. Embed matching machine-readable cluster metadata in both sources so tests can enforce identical numbering, nets, limits, source-state language, pass gates, and ordering while allowing the build and audit instructions to differ.

**Tech Stack:** HTML/CSS, embedded JSON metadata, Python/pytest, Poppler (`pdftotext`, `pdfinfo`), headless Google Chrome.

---

## File Structure

- Create `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`: structural, parity, safety-contract, supersession, and rendered-PDF tests.
- Create `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html`: empty-board construction source.
- Create `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf`: printable build artifact.
- Create `hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.html`: assembled-board isolation/diagnostic source.
- Create `hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.pdf`: printable audit artifact.
- Delete `hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.html` and `.pdf`: misleading superseded workflow.
- Modify any repository navigation/documentation file found by exact reference search so it no longer links to the deleted artifacts.

### Task 1: Lock the two-guide contract with failing tests

**Files:**
- Create: `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`
- Reference: `docs/superpowers/specs/2026-08-10-esp32tap-cluster-build-audit-guides-design.md`

- [ ] **Step 1: Define paths and shared cluster contract**

Add constants for the two HTML/PDF pairs, the superseded pair, and:

```python
EXPECTED_CLUSTERS = [
    (1, "Raw protection"),
    (2, "TSR supply"),
    (3, "DevKit and logic supply"),
    (4, "TPS3700 voltage monitor"),
    (5, "AHC08 permission logic"),
    (6, "TPS709 and BC337 driver"),
    (7, "Relay coil, local contacts, and feedback"),
    (8, "AHC126 and UART taps"),
    (9, "Indicators and VBUS sensing"),
    (10, "Whole-device standalone bench test"),
    (11, "RJ45 pass-through and treadmill bypass"),
]
```

- [ ] **Step 2: Add metadata parsing and parity tests**

Parse `<script id="guide-metadata" type="application/json">`. Require:

```python
assert build["mode"] == "empty_board_build"
assert audit["mode"] == "assembled_board_audit"
shared = ("number", "name", "inputs", "outputs", "dependencies",
          "source_state", "stop_gate", "pass_gate")
assert [{k: c[k] for k in shared} for c in build["clusters"]] == [
    {k: c[k] for k in shared} for c in audit["clusters"]
]
assert [(c["number"], c["name"]) for c in build["clusters"]] == EXPECTED_CLUSTERS
assert all(c["inputs"] and c["outputs"] for c in build["clusters"])
assert all(c["stop_gate"] and c["pass_gate"] for c in build["clusters"])
```

Require each audit cluster to name `isolate`, `measure`, and `restore` actions.
Reject fields or prose that prescribe holes such as `a29`, `f36`, or `-52`.

- [ ] **Step 3: Add safety and content tests**

Require both sources to contain exact source rules and shared numerical limits:

```python
REQUIRED_LIMITS = {
    "protected_vin": "7.20–7.90 V",
    "initial_source": "8.00 V",
    "initial_current_limit": "250 mA",
    "coil_open_current": "50 mA",
    "logic_3v3": "3.20–3.40 V",
    "uv_boundary": "6.25–6.55 V",
    "ov_falling_boundary": "10.30–10.90 V",
    "tps709_enabled": "4.75–5.25 V",
    "tps709_disabled": "0.25 V",
    "coil_current": "90–110 mA",
    "coil_voltage": "4.50 V",
    "bc337_vce": "0.30 V",
    "release_time": "100 ms",
    "treadmill_current": "500 mA",
    "path_drop": "50 mV",
    "device_temperature": "45°C",
    "temperature_rise": "10°C over ambient",
    "coil_hold": "five-minute",
    "bypass_temperature": "40°C",
    "bypass_hold": "fifteen minutes",
}
```

Assert both guides state:

- USB and installed `STANDALONE POWER` are mutually exclusive;
- `COIL POWER` stays removed through unloaded TPS709 testing;
- any unexpected reset is a STOP;
- treadmill relay transfer and ESP transmit are prohibited;
- all power sources are off for RJ45/harness/direct-path changes;
- cluster 7 proves local relay endpoints only and cluster 11 proves end-to-end `CONSOLE.6 ↔ MOTOR.6`.
- relay feedback is `(1,0)` energized and `(0,1)` in bypass, while `00` and
  `11` are explicit faults;
- TPS3700/TREAD_OK is low below UV, high at 8.00 V, and low above OV;
- the loaded relay test and treadmill-current test each independently state a
  no-more-than-500 mA limit;
- VBUS sensing is active-low and never joins VBUS to a local power rail.

Require build-specific phrases for `Parts`, `Build`, `Unpowered test`, `Powered test`, `PASS — continue`, and exact pin/net/color wiring. Require audit-specific phrases for `Isolate`, `Inspect`, `Measure`, `Likely causes`, `Restore`, and `PASS — continue`.
Require every cluster card in both modes to contain writable operator/date and
signed pass evidence fields.

- [ ] **Step 4: Add supersession and PDF tests**

Assert the old HTML/PDF do not exist and no non-plan/spec file references them. Extract both PDFs with `pdftotext -layout`; require all cluster headings in order, the guide-specific card labels, numerical limits, STOP/PASS gates, build identity fields, and no `file:///` or local filesystem path. Use `pdfinfo` to require `612 x 792 pts (letter)`.

- [ ] **Step 5: Run the test and confirm red**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py
```

Expected: FAIL because the new HTML/PDF pairs do not exist and the superseded artifacts still exist.

### Task 2: Implement the empty-board build-and-test guide

**Files:**
- Create: `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html`
- Create: `hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf`
- Test: `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`

- [ ] **Step 1: Add print shell and metadata**

Use US Letter portrait `@page`, page-contained cluster cards, writable evidence
cells, STOP/PASS callouts, and no browser-dependent assets. Embed all 11
clusters under `guide-metadata`, with shared fields `number`, `name`, `inputs`,
`outputs`, `dependencies`, `source_state`, `stop_gate`, and `pass_gate`.

- [ ] **Step 2: Write clusters 1–3**

For raw protection, TSR, and DevKit/logic supply, give exact parts/pins/nets,
wire colors, one-at-a-time connection order, polarity, unpowered checks, bench
source settings, expected measurements, and source-link rules. Do not create a
global rail before its producing cluster passes.

- [ ] **Step 3: Write clusters 4–6**

For TPS3700, AHC08, and TPS709/BC337, include adapter continuity mapping,
divider formulas, complete gate truth tables, safe GPIO-jumper removal for
manual input injection, unloaded regulator tests, and the rule that COIL POWER
cannot be installed yet.

- [ ] **Step 4: Write clusters 7–9**

For relay, AHC126/UART, and indicators/VBUS, include local relay endpoint tests,
bounded exerciser identity, fail-release timing, thermal evidence, TX
high-impedance behavior, receive-tap checks, LED polarity/resistors, and proof
that USB VBUS never joins VIN, +5V_RLY, or LOGIC_3V3.

- [ ] **Step 5: Write clusters 10–11**

For standalone and RJ45/treadmill bypass, include observer manifest evidence,
Wi-Fi/event-log observations, UART idle values, independent conductor mapping,
all-sources-off transition rules, the dual-pin fused-DMM harness sequence,
direct-path restoration before voltage-drop testing, per-terminal thermal
fields, and an explicit final prohibition on relay transfer/TX.

- [ ] **Step 6: Render and run the build-guide subset**

Run:

```bash
google-chrome --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$PWD/hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf" \
  "file://$PWD/hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html"
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py -k build
```

Expected: build-source/PDF tests PASS; audit and supersession tests remain red.

- [ ] **Step 7: Commit the build guide**

```bash
git add hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.html \
  hardware/Esp32Tap/bringup/esp32tap-cluster-build-and-test.pdf
git commit -m "docs(Esp32Tap): add depth-first cluster build guide"
```

### Task 3: Implement the assembled-board audit-and-test guide

**Files:**
- Create: `hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.html`
- Create: `hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.pdf`
- Test: `hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py`

- [ ] **Step 1: Add matching print shell and metadata**

Reuse the build guide's visual language and identical cluster metadata while
setting mode to `assembled_board_audit`. Each cluster names its isolation and
restoration boundary explicitly.

- [ ] **Step 2: Write power-chain audit clusters 1–3**

Start each audit at its upstream measurement point, open downstream power links
where possible, prove unpowered rail isolation, then apply the smallest safe
source and measure forward to the output. Provide ordered likely causes for
missing, low, or current-limited outputs.

- [ ] **Step 3: Write supervisor/control audit clusters 4–6**

Audit divider ratios and thresholds, AHC08 truth behavior, TPS709 disabled and
enabled output, BC337 pin mapping/base drive, and every opened GPIO or coil link
with a recorded restoration step.

- [ ] **Step 4: Write relay/UART/indicator audit clusters 7–9**

Audit local relay endpoints and feedback before end-to-end RJ45 claims, bounded
exerciser identity, AHC126 isolation, receive tap inputs, LED polarity, and
VBUS active-low sensing. Likely-cause trees proceed input → component → output.

- [ ] **Step 5: Write integration audit clusters 10–11**

Audit observer identity/manifest, standalone source handoff, Wi-Fi observation,
all eight independent RJ45 conductors, fused-DMM harness transitions, restored
direct paths, voltage drops, and individual thermal endpoints. End with the
same bypass-only boundary and transfer prohibition as the build guide.

- [ ] **Step 6: Render and run all new-guide tests except supersession**

Run Chrome with the audit HTML/PDF paths, then:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py -k 'not superseded'
```

Expected: PASS.

- [ ] **Step 7: Commit the audit guide**

```bash
git add hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py \
  hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.html \
  hardware/Esp32Tap/bringup/esp32tap-cluster-audit-and-test.pdf
git commit -m "docs(Esp32Tap): add assembled-board cluster audit guide"
```

### Task 4: Remove the misleading checklist and stale links

**Files:**
- Delete: `hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.html`
- Delete: `hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.pdf`
- Historical references only: the exact search currently returns the current
  plan, approved design, and prior implementation plan; no operational or
  navigation file needs modification unless a fresh search proves otherwise.

- [ ] **Step 1: Enumerate references before deletion**

Run:

```bash
rg -l 'esp32tap-module-test-checklist' . --glob '!**/.git/**'
```

Allowlist only the current plan, approved design, and prior implementation
plan. Verify the two superseded artifact paths exist separately with `test -f`.
If a new operational/navigation reference appears, stop and update this plan
before changing that file.

- [ ] **Step 2: Delete the superseded artifacts**

Use `apply_patch` for the HTML deletion and `rm -f` for the generated binary
PDF after verifying the exact two paths.

- [ ] **Step 3: Run the full guide test**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py
```

Expected: PASS.

- [ ] **Step 4: Commit supersession**

```bash
git add \
  hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.html \
  hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.pdf
git commit -m "docs(Esp32Tap): retire whole-board test checklist"
```

### Task 5: Print, visual, and repository verification

**Files:**
- Verify all files above; do not alter unrelated dirty files.

- [ ] **Step 1: Verify PDF integrity and extracted content**

Run `pdfinfo` and `pdftotext -layout` for both PDFs. Confirm US Letter size,
all 11 cluster headings in order, guide-specific verbs, all numerical limits,
STOP/PASS gates, identity/manifest fields, and absence of local-path footers.

- [ ] **Step 2: Inspect every rendered page**

Render both PDFs with `pdftoppm`, create contact sheets with `montage`, and
inspect every page for clipping, spill-only pages, tiny text, broken tables,
and adequate writable evidence space. Fix HTML and re-render until clean.

- [ ] **Step 3: Run focused and neighboring tests**

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_cluster_build_audit_guides.py \
  hardware/Esp32Tap/tests/test_point_to_point_clustered_pdf.py
```

Expected: PASS.

- [ ] **Step 4: Verify scope and push**

Confirm only the new guides/tests, superseded artifact deletions, approved
spec/plan are staged. Explicitly stage
`docs/superpowers/plans/2026-08-10-esp32tap-cluster-build-audit-guides.md` in
the final commit. Preserve
`full-breadboard-model.json` and `test_breadboard_wizard.py` modifications.
Close bead `precor-9_3x-vgc`, commit remaining verification adjustments, run
`git pull --rebase --autostash`, push, and verify local HEAD equals the remote
branch HEAD.
