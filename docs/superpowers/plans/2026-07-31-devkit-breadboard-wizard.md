# DevKit Breadboard Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and launch a precise interactive browser wizard for assembling the approved unpowered ESP32-S3 DevKit sidecar breadboard.

**Architecture:** One self-contained HTML file owns a canonical JSON circuit model, inline SVG, and a pure JavaScript state controller. Python validates the electrical model; Node's built-in test runner validates controller behavior without browser dependencies. The visual companion serves the tracked HTML directly from a fresh screen copy.

**Tech Stack:** HTML5, inline SVG, vanilla CSS/JavaScript, Python stdlib + pytest, Node `node:test`.

---

## Canonical model to implement

The single `<script id="wiring-data" type="application/json">` object is the
only runtime wiring source. It has these exact top-level keys:

```json
{
  "schema_version": 1,
  "storage_key": "esp32tap-breadboard-wizard-v1",
  "bom": {"10k": 3, "47k": 3, "1k": 3, "led": 2, "dpdt": 1, "spst": 2},
  "disconnected_pins": [16, 17, 18, 38],
  "nodes": {},
  "items": [],
  "baseline": {},
  "truth_table": [],
  "steps": [],
  "meter_checks": {},
  "photo_instruction": ""
}
```

`nodes` is exactly:

```json
{
  "3v3": "red rail columns 1-27",
  "gnd": "blue rail columns 1-27",
  "gpio4": "a4-e4",
  "gpio5": "a6-e6",
  "gpio6": "a10-e10",
  "gpio6_sw": "a12-e12",
  "gpio7": "a15-e15",
  "gpio15": "a20-e20",
  "led15_a": "f20-j20",
  "led15_k": "f22-j22",
  "gpio21": "a25-e25",
  "led21_a": "f25-j25",
  "led21_k": "f27-j27",
  "dpdt_common_a": "off-board common A",
  "dpdt_common_b": "off-board common B",
  "dpdt_ground_a": "off-board grounded throw A",
  "dpdt_ground_b": "off-board grounded throw B",
  "dpdt_unused_a": "off-board insulated unused throw A",
  "dpdt_unused_b": "off-board insulated unused throw B",
  "devkit_3v3": "DevKit printed 3V3",
  "devkit_gnd": "DevKit printed GND",
  "devkit_gpio4": "DevKit printed GPIO4",
  "devkit_gpio5": "DevKit printed GPIO5",
  "devkit_gpio6": "DevKit printed GPIO6",
  "devkit_gpio7": "DevKit printed GPIO7",
  "devkit_gpio15": "DevKit printed GPIO15",
  "devkit_gpio21": "DevKit printed GPIO21"
}
```

Each `items` entry is `{id,type,value,from,to,step}`. The exact item endpoint
tuples are:

```text
r4_pullup,resistor,10k,gpio4,3v3
r5_pullup,resistor,10k,gpio5,3v3
dpdt,switch,DPDT,dpdt_common_a,dpdt_common_b
w_dpdt_a,wire,,gpio4,dpdt_common_a
w_dpdt_b,wire,,gpio5,dpdt_common_b
w_dpdt_ga,wire,,dpdt_ground_a,gnd
w_dpdt_gb,wire,,dpdt_ground_b,gnd
r6_pulldown,resistor,47k,gpio6,gnd
sw6,switch,SPST,gpio6,gpio6_sw
r6_series,resistor,1k,gpio6_sw,3v3
r7_pullup,resistor,10k,gpio7,3v3
sw7,switch,SPST,gpio7,gnd
r15_pulldown,resistor,47k,gpio15,gnd
r15_series,resistor,1k,gpio15,led15_a
led15,led,red,led15_a,led15_k
w_led15_gnd,wire,,led15_k,gnd
r21_pulldown,resistor,47k,gpio21,gnd
r21_series,resistor,1k,gpio21,led21_a
led21,led,yellow,led21_a,led21_k
w_led21_gnd,wire,,led21_k,gnd
j_gnd,jumper,,devkit_gnd,gnd
j_3v3,jumper,,devkit_3v3,3v3
j_gpio4,jumper,,devkit_gpio4,gpio4
j_gpio5,jumper,,devkit_gpio5,gpio5
j_gpio6,jumper,,devkit_gpio6,gpio6
j_gpio7,jumper,,devkit_gpio7,gpio7
j_gpio15,jumper,,devkit_gpio15,gpio15
j_gpio21,jumper,,devkit_gpio21,gpio21
```

No item endpoint references `dpdt_unused_a` or `dpdt_unused_b`; the UI labels
both insulated. No node or item contains `5V`, native USB, or treadmill wiring.

`baseline` is exactly `{"dpdt":"ungrounded","gpio6":"open","gpio7":"open","levels":[1,1,0,1]}`.
The eight truth-table level arrays in display order are exactly:
`[1,1,0,1]`, `[1,1,0,0]`, `[1,1,1,1]`, `[1,1,1,0]`,
`[0,0,0,1]`, `[0,0,0,0]`, `[0,0,1,1]`, `[0,0,1,0]`.

Atomic step IDs are exactly:

```text
safety,bom,rails,r4_pullup,r5_pullup,dpdt_identify,w_dpdt_a,w_dpdt_b,
w_dpdt_ga,w_dpdt_gb,r6_pulldown,sw6,r6_series,r7_pullup,sw7,
r15_pulldown,r15_series,led15,w_led15_gnd,r21_pulldown,r21_series,
led21,w_led21_gnd,precheck,j_gnd,j_3v3,j_gpio4,j_gpio5,j_gpio6,j_gpio7,
j_gpio15,j_gpio21,postcheck,photo
```

`dpdt_identify` has `requires_meter=true` and therefore two confirmations.
`j_gnd`, then `j_3v3`, then the six signal jumpers occur only after `precheck`.
`postcheck` precedes `photo`. Every step has `applies_power=false`.

Meter-check text includes these exact normative tokens: `<2 Ohm`, `<100 Ohm`,
`wait five seconds`, `UART USB disconnected`, `3V3-to-ground`,
`GPIO16`, `GPIO17`, `GPIO18`, and `GPIO38`.
The final instruction is exactly:

```text
Keep UART USB unplugged. Take one sharp photo directly overhead with every rail, resistor band, switch lug, LED lead, and DevKit label visible. Attach that photo in this chat and wait for approval before applying power.
```

The final checkbox text is exactly `I attached the overhead photo in this chat`.

---

### Task 1: Lock the electrical model

**Files:**
- Create: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`
- Create: `hardware/Esp32Tap/bringup/breadboard-wizard.html`

- [ ] **Step 1: Write the failing Python model test**

Implement `load_model()` with `html.parser.HTMLParser`; reject missing/duplicate
model scripts, non-JSON, or extra top-level keys. Assert the complete canonical
model above, exact BOM counts, endpoint-node membership, value counts, item IDs,
step IDs/order, unused DPDT nodes, rail polarity, no forbidden power strings,
truth-table rows, meter tokens, and final instruction/checkbox.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
```

Expected: `FileNotFoundError` for `breadboard-wizard.html`.

- [ ] **Step 3: Add the minimum HTML and exact JSON model**

Create a valid self-contained HTML shell and the canonical model. Include no
other circuit data in JavaScript or HTML attributes.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: all model tests pass.

- [ ] **Step 5: Check and commit**

```bash
git diff --check
git add hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "test(Esp32Tap): lock breadboard wizard circuit"
```

### Task 2: Implement and test the state controller

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Create: `hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Write the failing Node behavior test**

Extract `<script id="wizard-controller">`, evaluate it with `node:vm`, and call
`window.createBreadboardController({model,storage,confirmReset})`. Test:

- Next disabled until the active step is confirmed;
- DPDT identify requires both placement and meter confirmations;
- Back retains confirmations;
- photo is unreachable until all earlier steps are confirmed;
- state persists only under `esp32tap-breadboard-wizard-v1`;
- reset calls `confirmReset`, removes only that key, returns to index 0;
- zoom clamps to 0.7 through 2.0.

- [ ] **Step 2: Run RED**

```bash
node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js
```

Expected: fail because `wizard-controller`/factory is absent.

- [ ] **Step 3: Implement the pure controller**

Add only state/navigation/persistence/zoom logic to `wizard-controller`; it may
read the already-parsed model but contains no nodes, nets, endpoints, or BOM.

- [ ] **Step 4: Run controller GREEN**

Run the same Node command. Expected: all controller tests pass.

- [ ] **Step 5: Extend Python static safety assertions**

Require DOM IDs `board-svg`, `step-title`, `step-copy`, `confirm-step`,
`confirm-meter`, `previous-step`, `next-step`, `zoom-in`, `zoom-out`,
`reset-progress`, `netlist-panel`, `truth-table`, and `photo-handoff`. Require
CSS classes `.item-complete`, `.item-active`, `.item-future`; exact warnings
`NO 5V`, `UART USB UNPLUGGED`, `NO NATIVE USB`, `NO TREADMILL`; LED `A`/`K`
labels; rail `3V3`/`GND`; and no `http://` or `https://`.

- [ ] **Step 6: Run Python RED**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
```

Expected: fail on missing DOM/rendering markers.

### Task 3: Render the responsive SVG wizard

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Implement the view**

Render from `wiring-data`: columns 1-30, a-e/f-j holes, center trench, selected
rail segment, DevKit printed-name pads, all items/endpoints, resistor values,
LED polarity, DPDT unused insulated throws, active/completed/future states,
phase/progress, atomic instruction, persistent BOM/netlist/truth table, meter
checks, disconnected pins, and photo handoff. Wire DOM controls to the pure
controller. `Next` uses controller gating; the meter checkbox appears only on
`dpdt_identify`; photo requires all earlier confirmations. Reset uses a native
confirmation dialog and clears only the versioned key.

- [ ] **Step 2: Run Python GREEN**

Run the Python test. Expected: all tests pass.

- [ ] **Step 3: Run Node GREEN**

Run the Node test. Expected: all tests pass.

- [ ] **Step 4: Run the hardware test directory**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests
```

Expected: all existing and wizard tests pass.

- [ ] **Step 5: Check and commit**

```bash
git diff --check
git add hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js
git commit -m "feat(Esp32Tap): add interactive breadboard wizard"
```

### Task 4: Launch and inspect the browser guide

**Files:**
- No tracked changes expected.

- [ ] **Step 1: Start the visual companion**

```bash
/home/ssilver/.codex/superpowers/skills/brainstorming/scripts/start-server.sh \
  --project-dir /home/ssilver/development/precor-9.3x/.worktrees/esp32tap-devkit-bringup \
  --host 0.0.0.0 \
  --url-host 192.168.1.15
```

Expected JSON contains `"type":"server-started"`, a port/URL, and an absolute
`screen_dir` under `.superpowers/brainstorm/`.

- [ ] **Step 2: Publish the exact tracked file**

After checking `$screen_dir/.server-info` exists and `.server-stopped` does not:

```bash
cp -f hardware/Esp32Tap/bringup/breadboard-wizard.html \
  "$screen_dir/breadboard-wizard-live.html"
```

- [ ] **Step 3: Inspect desktop and phone views**

At 1440x900 and 390x844, inspect every labeled endpoint, rail polarity, LED
orientation, DPDT unused throws, BOM/netlist/truth table, phase/atomic order,
ordinary and DPDT dual gating, Back retention, local persistence, scoped reset,
zoom, and the exact final unpowered photo instruction. Confirm no instruction
applies power.

- [ ] **Step 4: Push and hand off**

```bash
git pull --rebase
git push
git status --short --branch
```

Give the user the live URL. Tell them to keep UART USB unplugged, complete the
wizard, and attach the overhead photo in this chat before applying power.
