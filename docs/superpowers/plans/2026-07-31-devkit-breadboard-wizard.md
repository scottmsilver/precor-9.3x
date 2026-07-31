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

Every two-terminal `items` entry is `{id,type,value,from,to,step}`. The DPDT is
the sole exception: it is one physical BOM item with an explicit six-terminal
shape and two independent poles:

```json
{"id":"dpdt","type":"switch","value":"DPDT","step":"dpdt_identify",
 "terminals":{
   "common_a":"dpdt_common_a","grounded_a":"dpdt_ground_a","unused_a":"dpdt_unused_a",
   "common_b":"dpdt_common_b","grounded_b":"dpdt_ground_b","unused_b":"dpdt_unused_b"
 },
 "contacts":[
   {"common":"dpdt_common_a","grounded":"dpdt_ground_a","unused":"dpdt_unused_a"},
   {"common":"dpdt_common_b","grounded":"dpdt_ground_b","unused":"dpdt_unused_b"}
 ]}
```

No DPDT common is electrically connected to the other common. Each pole may
connect only its own common to its own selected throw. The exact two-terminal
item tuples, including their construction step, are:

```text
r4_pullup,resistor,10k,gpio4,3v3,r4_pullup
r5_pullup,resistor,10k,gpio5,3v3,r5_pullup
w_dpdt_a,wire,,gpio4,dpdt_common_a,w_dpdt_a
w_dpdt_b,wire,,gpio5,dpdt_common_b,w_dpdt_b
w_dpdt_ga,wire,,dpdt_ground_a,gnd,w_dpdt_ga
w_dpdt_gb,wire,,dpdt_ground_b,gnd,w_dpdt_gb
r6_pulldown,resistor,47k,gpio6,gnd,r6_pulldown
sw6,switch,SPST,gpio6,gpio6_sw,sw6
r6_series,resistor,1k,gpio6_sw,3v3,r6_series
r7_pullup,resistor,10k,gpio7,3v3,r7_pullup
sw7,switch,SPST,gpio7,gnd,sw7
r15_pulldown,resistor,47k,gpio15,gnd,r15_pulldown
r15_series,resistor,1k,gpio15,led15_a,r15_series
led15,led,red,led15_a,led15_k,led15
w_led15_gnd,wire,,led15_k,gnd,w_led15_gnd
r21_pulldown,resistor,47k,gpio21,gnd,r21_pulldown
r21_series,resistor,1k,gpio21,led21_a,r21_series
led21,led,yellow,led21_a,led21_k,led21
w_led21_gnd,wire,,led21_k,gnd,w_led21_gnd
j_gnd,jumper,,devkit_gnd,gnd,j_gnd
j_3v3,jumper,,devkit_3v3,3v3,j_3v3
j_gpio4,jumper,,devkit_gpio4,gpio4,j_gpio4
j_gpio5,jumper,,devkit_gpio5,gpio5,j_gpio5
j_gpio6,jumper,,devkit_gpio6,gpio6,j_gpio6
j_gpio7,jumper,,devkit_gpio7,gpio7,j_gpio7
j_gpio15,jumper,,devkit_gpio15,gpio15,j_gpio15
j_gpio21,jumper,,devkit_gpio21,gpio21,j_gpio21
```

No item endpoint references `dpdt_unused_a` or `dpdt_unused_b`; the UI labels
both insulated. No node or item contains `5V`, native USB, or treadmill wiring.

`baseline` is exactly `{"dpdt":"ungrounded","gpio6":"open","gpio7":"open","levels":[1,1,0,1]}`.
`truth_table` is exactly these complete rows in display order:

```json
[
 {"dpdt":"ungrounded","gpio6":"open","gpio7":"open","levels":[1,1,0,1]},
 {"dpdt":"ungrounded","gpio6":"open","gpio7":"closed","levels":[1,1,0,0]},
 {"dpdt":"ungrounded","gpio6":"closed","gpio7":"open","levels":[1,1,1,1]},
 {"dpdt":"ungrounded","gpio6":"closed","gpio7":"closed","levels":[1,1,1,0]},
 {"dpdt":"grounded","gpio6":"open","gpio7":"open","levels":[0,0,0,1]},
 {"dpdt":"grounded","gpio6":"open","gpio7":"closed","levels":[0,0,0,0]},
 {"dpdt":"grounded","gpio6":"closed","gpio7":"open","levels":[0,0,1,1]},
 {"dpdt":"grounded","gpio6":"closed","gpio7":"closed","levels":[0,0,1,0]}
]
```

Atomic step IDs are exactly:

```text
safety,bom,rails,r4_pullup,r5_pullup,dpdt_identify,w_dpdt_a,w_dpdt_b,
w_dpdt_ga,w_dpdt_gb,dpdt_insulate,r6_pulldown,sw6,r6_series,r7_pullup,sw7,
r15_pulldown,r15_series,led15,w_led15_gnd,r21_pulldown,r21_series,
led21,w_led21_gnd,precheck,j_gnd,j_3v3,j_gpio4,j_gpio5,j_gpio6,j_gpio7,
j_gpio15,j_gpio21,check_3v3_gnd,check_devkit_gnd,check_devkit_3v3,
check_gpio4,check_gpio5,check_gpio6,check_gpio7,check_gpio15,check_gpio21,
check_empty16,check_empty17,check_empty18,check_empty38,photo
```

Every step object has exactly `id`, `phase`, `highlight`, `instruction`,
`purpose`, `confirmation_ids`, and `applies_power`. `highlight` is the matching
item ID for item-placement steps, `dpdt` for `dpdt_identify` and
`dpdt_insulate`, and `null` for safety/check/photo steps. `phase` is `prepare`
through `rails`, `inputs` through `w_led21_gnd`, `attach` from `precheck`
through `j_gpio21`, and `verify` thereafter. `instruction` is an imperative
placement or exact measurement; `purpose` explains the electrical function.
Neither field may be empty. Every ordinary placement, precheck, and `check_*`
step has one result ID in `confirmation_ids`. `dpdt_identify` alone has exactly
two: `placed` and `both_pole_pairs_meter_identified`.

The controller gates Next on every ID in the active step's `confirmation_ids`.
`j_gnd`, then `j_3v3`, then the six signal jumpers occur only after `precheck`.
Every individual `check_*` precedes and independently gates `photo`. Every
step has `applies_power=false`.

`meter_checks` has `semantics`, `pre`, and `post`. `semantics` is exactly
`{"continuity_pass":"<2 Ohm","short_fail":"<100 Ohm after waiting five seconds"}`.
`pre` requires UART USB disconnected, no power, continuous GND rail, continuous
3V3 rail, and 3V3-to-ground not below the short-fail threshold. It also requires
both LED cathodes to GND at `<2 Ohm`; meter-confirmed DPDT ungrounded/grounded
behavior on each pole independently; GPIO6 SPST open/closed behavior; and GPIO7
SPST open/closed behavior. `post` has one entry matching every `check_*` step:
3V3-to-ground not shorted; DevKit GND to
GND rail `<2 Ohm`; DevKit 3V3 to 3V3 rail `<2 Ohm`; each of GPIO4, GPIO5,
GPIO6, GPIO7, GPIO15, and GPIO21 to its named breadboard node `<2 Ohm`; and
GPIO16, GPIO17, GPIO18, and GPIO38 visibly empty/disconnected. Every result is
an explicit boolean confirmation stored independently by the controller.

The safety and final-inspection UI explicitly states all of the following:

- DPDT lug arrangements vary; meter-identify both independent common/throw
  pairs and never infer them from physical position.
- If either rail is split or fails continuity, replace it with one verified
  continuous rail segment; do not assume it is continuous and do not bridge it.
- Confirm no 5V, native USB, or treadmill connection; GPIO16/17/18/38 empty;
  every resistor value; both LED A/K polarities; and baseline DPDT ungrounded,
  GPIO6 open, GPIO7 open.
- `Powered testing is deferred until the overhead photo is reviewed.`
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
step IDs/order and every required step field. Assert the DPDT has exactly six
distinct terminals and two per-pole contact objects, and reject common-to-common
or cross-pole contacts. Assert the split rail is deliberately limited to columns
1-27, LED anode/cathode nodes and labels are distinct, baseline/truth rows are
exact, every meter result is independent, and powered testing is explicitly
deferred. Assert final instruction/checkbox exactly.

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
- DPDT identify requires both `placed` and
  `both_pole_pairs_meter_identified`; every other step requires its one exact
  result confirmation;
- Back retains confirmations;
- photo is unreachable until all earlier steps and every individual post-check
  result are confirmed (loop over every `check_*` ID, omit one at a time, and
  prove the photo remains unreachable);
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

Require DOM IDs `board-svg`, `step-title`, `step-copy`, `confirmations`,
`previous-step`, `next-step`, `zoom-in`, `zoom-out`,
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
controller. `Next` uses controller gating and renders one checkbox per active
step `confirmation_ids` entry; only `dpdt_identify` renders two. Photo requires
all earlier confirmations. Reset uses a native
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

Copy the absolute `screen_dir` value from the server-started JSON into the
following assignment, then verify and publish using that same variable:

```bash
SCREEN_DIR='/absolute/screen_dir/from/server-started-json'
test -f "$SCREEN_DIR/.server-info"
test ! -e "$SCREEN_DIR/.server-stopped"
cp -f hardware/Esp32Tap/bringup/breadboard-wizard.html \
  "$SCREEN_DIR/breadboard-wizard-live.html"
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
