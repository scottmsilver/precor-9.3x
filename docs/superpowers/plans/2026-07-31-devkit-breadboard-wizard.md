# DevKit Breadboard Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and launch a precise interactive browser wizard for assembling the approved unpowered ESP32-S3 DevKit sidecar breadboard.

**Architecture:** A single tracked, self-contained HTML file owns a machine-readable circuit model, inline SVG rendering, navigation, safety gating, and local progress. A dependency-free Python test reads that model and checks the electrical contract, construction order, and required controls. The visual companion serves the completed HTML without a separate frontend toolchain.

**Tech Stack:** HTML5, inline SVG, vanilla CSS/JavaScript, Python stdlib + pytest.

---

### Task 1: Lock the breadboard circuit contract

**Files:**
- Create: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`
- Create: `hardware/Esp32Tap/bringup/breadboard-wizard.html`

- [ ] **Step 1: Write the failing contract test**

Read the HTML and extract one `<script id="wiring-data" type="application/json">`
object. Require:

```python
assert data["bom"] == {"10k": 3, "47k": 3, "1k": 3, "led": 2, "dpdt": 1, "spst": 2}
assert data["disconnected_pins"] == [16, 17, 18, 38]
assert data["baseline"] == {"dpdt": "ungrounded", "gpio6": "open", "gpio7": "open", "levels": [1, 1, 0, 1]}
assert len(data["truth_table"]) == 8
```

Also assert every component/wire has explicit endpoints, all circuit nodes from
the approved spec exist, the six DevKit signal jumpers are ordered last, no
construction step applies power, and both pre/post meter-check groups and photo
handoff are represented.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
```

Expected: fail because the HTML/data model does not exist.

- [ ] **Step 3: Add the minimal embedded circuit model**

Create the HTML and canonical JSON model with the exact BOM, coordinates,
netlist, atomic steps, meter checks, baseline, and eight-state table from the
approved design. Do not add external assets or dependencies.

- [ ] **Step 4: Run GREEN and commit**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
git add hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "test(Esp32Tap): lock breadboard wizard circuit"
```

### Task 2: Render the interactive SVG wizard

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Extend tests for UI and safety controls**

Require stable DOM IDs for `board-svg`, `step-title`, `step-copy`, `confirm-step`,
`previous-step`, `next-step`, `zoom-in`, `zoom-out`, `reset-progress`,
`netlist-panel`, `truth-table`, and `photo-handoff`. Require a versioned local
storage key, confirmation-gated Next logic, confirmed reset logic, active-step
SVG classes, and no network URLs.

- [ ] **Step 2: Run RED**

Expected: contract test fails on missing controls/rendering markers.

- [ ] **Step 3: Implement the page**

Render:

- a conventional a-e/f-j breadboard with columns 1-30 and labeled rails;
- the DevKit beside it with printed-name connection pads;
- resistors with readable value bands, polarized LEDs, switches, and
  color-coded wires generated from the embedded model;
- one highlighted atomic step at a time, with completed work muted and future
  work ghosted;
- Back/Next, confirmation gating, zoom, reset, progress, BOM, netlist, truth
  table, meter expectations, disconnected-pin warning, and final chat-photo
  instruction.

Keep the page responsive for phone and desktop and usable without JavaScript
downloads.

- [ ] **Step 4: Run GREEN, inspect, and commit**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
python3 -m pytest -q hardware/Esp32Tap/tests
git diff --check
git add hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "feat(Esp32Tap): add interactive breadboard wizard"
```

### Task 3: Launch and verify the browser guide

**Files:**
- No tracked file changes expected.

- [ ] **Step 1: Start the visual companion**

Start it with this worktree as `--project-dir`, bind to `0.0.0.0`, and publish a
reachable LAN URL.

- [ ] **Step 2: Publish the tracked HTML as a fresh companion screen**

Copy the exact tracked file to a new semantic filename in the returned screen
directory. Do not maintain a second edited copy.

- [ ] **Step 3: Verify visually and functionally**

Open the page at desktop and phone widths. Check SVG labels/endpoints, atomic
navigation, disabled Next, Back behavior, zoom, persistent progress, confirmed
reset, truth-table values, and final photo instruction.

- [ ] **Step 4: Push and hand off**

```bash
git pull --rebase
git push
git status --short --branch
```

Give the user the live browser URL and tell them to stop before connecting USB
and attach an overhead photo in this chat.
