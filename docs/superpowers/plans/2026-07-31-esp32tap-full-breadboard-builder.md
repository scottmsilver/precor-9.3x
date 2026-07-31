# Esp32Tap Full-Breadboard Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the obsolete jumper simulator with a tested, mobile-friendly browser builder for the exact full-breadboard relay interface, including all eight RJ45 conductors, atomic construction steps, numerical evidence gates, and locked firmware/treadmill phases.

**Architecture:** Keep `breadboard-wizard.html` as the portable artifact, but generate it from a small Python renderer, a focused HTML template, and one canonical JSON circuit model. Operator-supplied RJ45, adapter, and rail-break mappings are validated as bijections and resolved into physical endpoints at runtime; no guessed orientation enters the static artifact. Python tests validate electrical topology and ordering, Node tests validate the pure controller/resolver, and a dependency-free Chrome DevTools Protocol test exercises the generated file in real headless Chrome. The builder never controls hardware—it presents wiring, records local evidence, and locks later phases until their prerequisites are satisfied.

**Tech Stack:** Python 3 standard library, JSON, self-contained HTML/CSS/SVG, browser JavaScript, Node `node:test`, pytest.

---

## File structure

- Create `hardware/Esp32Tap/bringup/full-breadboard-model.json`: sole machine-readable source for parts, logical endpoints, mapping contracts, nets, items, structured power states, phases, steps, limits, firmware roles, and manufacturer references.
- Create `hardware/Esp32Tap/bringup/breadboard-controller.js`: DOM-free mapping resolver, evidence validator, phase lock, and persistence controller; embedded unchanged into the generated HTML.
- Create `hardware/Esp32Tap/bringup/breadboard-wizard.template.html`: presentation shell, inline SVG containers, evidence forms, and controller/view code with a placeholder for canonical JSON.
- Create `hardware/Esp32Tap/bringup/render_breadboard_wizard.py`: deterministic standard-library renderer that validates basic schema and embeds the model into the template.
- Replace generated `hardware/Esp32Tap/bringup/breadboard-wizard.html`: committed, self-contained browser artifact.
- Rewrite `hardware/Esp32Tap/tests/test_breadboard_wizard.py`: electrical, schema, sequence, content, generated-parity, and safety-limit tests.
- Rewrite `hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`: controller persistence, typed evidence, phase locks, firmware-role locks, reset, and zoom tests.
- Create `hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js`: minimal fake-DOM rendering checks for highlighted items, measurements, external references, and status messaging.
- Create `hardware/Esp32Tap/tests/breadboard_wizard_browser.test.js`: real headless-Chrome interaction tests using Node built-ins and Chrome DevTools Protocol.
- Modify `docs/superpowers/specs/2026-07-31-esp32tap-full-breadboard-builder-design.md` only if implementation uncovers an actual reviewed-design defect; do not silently change electrical intent.

### Task 1: Replace the old contract with the canonical full-circuit schema

**Files:**
- Create: `hardware/Esp32Tap/bringup/full-breadboard-model.json`
- Rewrite: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Write a failing loader/schema test**

Define the top-level contract before creating the model:

```python
MODEL_PATH = Path(__file__).parents[1] / "bringup" / "full-breadboard-model.json"
TOP_LEVEL_KEYS = {
    "schema_version", "storage_key", "identity", "references", "tools",
    "parts", "nodes", "mapping_contracts", "nets", "items",
    "temporary_configurations", "limits", "power_states", "firmware_roles",
    "phases", "steps",
}

def load_model():
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert set(model) == TOP_LEVEL_KEYS
    return model

def test_model_identity_and_exact_owned_parts():
    model = load_model()
    assert model["schema_version"] == 2
    assert model["storage_key"] == "esp32tap-full-breadboard-builder-v2"
    mpns = {part["mpn"] for part in model["parts"] if part["source"] == "purchased"}
    assert {
        "ESP32-S3-DEVKITC-1-N8R8", "TPS3700DDCR", "TPS70950DBVR",
        "LCQT-SOT23-6", "SN74AHC08N", "SN74AHC126N", "G5V-2 DC5",
        "BC337-40", "2N7000", "1N5822-TP", "P6KE6.8CA", "RXEF075",
        "P6KE12A-TP", "TSR 1-2433E",
    } <= mpns
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: FAIL because `full-breadboard-model.json` does not exist.

- [ ] **Step 3: Create the smallest valid model skeleton**

Create all required top-level keys. Represent user-supplied breakouts and breadboard with `source: "operator_mapped"`, never a guessed pinout. Seed these exact authoritative references (record retrieval date `2026-07-31`; keep a document revision only when the document states one):

```json
{
  "g5v2": "https://components.omron.com/sites/default/files/datasheet_pdf/K046-E1.pdf",
  "tps3700": "https://www.ti.com/lit/ds/symlink/tps3700.pdf",
  "tps709": "https://www.ti.com/lit/ds/symlink/tps709.pdf",
  "sn74ahc08": "https://www.ti.com/lit/ds/symlink/sn74ahc08.pdf",
  "sn74ahc126": "https://www.ti.com/lit/ds/symlink/sn74ahc126.pdf",
  "bc337": "https://diotec.com/tl_files/diotec/files/pdf/datasheets/bc337.pdf",
  "2n7000": "https://diotec.com/tl_files/diotec/files/pdf/datasheets/2n7000.pdf",
  "tsr1": "https://www.tracopower.com/model/tsr-1-2433e",
  "p6ke": "https://www.littelfuse.com/products/tvs-diodes/high-power-tvs-diodes/p6ke",
  "rxef": "https://www.littelfuse.com/products/polyswitch-resettable-pptcs/radial-leaded/rxef"
}
```

Use stable IDs (`k1`, `u_tps3700`, `u_tps709`, `u_ahc08`, `u_ahc126`, `q_relay`, `q_vbus`, `j_console`, `j_motor`) because tests and SVG elements will reference them.

- [ ] **Step 4: Run the schema test and verify it passes**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/bringup/full-breadboard-model.json hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "test(Esp32Tap): define full breadboard model contract"
```

### Task 2: Encode and prove the exact electrical topology

**Files:**
- Modify: `hardware/Esp32Tap/bringup/full-breadboard-model.json`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Add failing topology tests**

Add helpers that convert every item's terminal map into `net -> {(item, pin)}` sets. Assert the reviewed mappings, including:

```python
assert pins(model, "CONS6") == {("j_console", "6"), ("k1", "4"), ("r_cons_rx", "1")}
assert pins(model, "MOT6") == {("j_motor", "6"), ("k1", "6")}
assert pins(model, "TX_DRV") == {("k1", "8"), ("r_tx", "2")}
assert pins(model, "K1_NC_FB") == {("k1", "13"), ("devkit", "GPIO4"), ("r_fb_nc", "2")}
assert pins(model, "K1_NO_FB") == {("k1", "9"), ("devkit", "GPIO5"), ("r_fb_no", "2")}
assert net_for(model, "k1", "11") == "GND"
assert net_for(model, "k1", "1") == "RELAY_COIL+"
assert net_for(model, "k1", "16") == "RELAY_COIL-"
```

Also assert AHC pin allocation, TPS pin maps, `TREAD_OK_MCU` series isolation, `VBUS_PRESENT_N` discharge, GPIO38 status LED, all unused input terminations, and all eight RJ45 contacts.

Add a graph check proving:

- unpowered `CONSOLE.6 -> K1.NC -> K1.COM -> MOTOR.6` exists;
- unpowered TX is disconnected from `MOTOR.6`;
- `RELAY_GATE` is the AHC08 output of `GPIO21 AND TREAD_OK` and reaches both TPS709 EN and the BC337 base resistor;
- `TX_GATE` is the AHC08 output of `GPIO15 AND TREAD_OK` and reaches AHC126 OE;
- independent `GND_1/GND_7/P8_2/P8_8` construction nets receive explicit final links to canonical `GND/+8V_RAW`.

- [ ] **Step 2: Run tests and verify topology failures**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: FAIL listing missing nets/items.

- [ ] **Step 3: Add the RJ45 pass-through slice**

Add `j_console`, `j_motor`, contacts 1–8, logical pass-through nets, four separately testable physical paths, and the four final common-net links. Run the topology test filtered with `-k rj45` and make it pass.

- [ ] **Step 4: Add the G5V-2 slice**

Add K1 coil/contact pins, NC serial bypass, NO TX transfer, and dry feedback pole. For each item store:

```json
{
  "id": "k1",
  "kind": "relay",
  "value": "G5V-2 DC5",
  "orientation": "top view; case mark identifies pin 1; meter-verify bottom-view reversal",
  "terminals": {
    "1": "RELAY_COIL+", "16": "RELAY_COIL-",
    "4": "CONS6", "6": "MOT6", "8": "TX_DRV",
    "13": "K1_NC_FB", "11": "GND", "9": "K1_NO_FB"
  },
  "placement": {"type": "breadboard", "pins": {"1": "e3", "4": "e6", "6": "e8", "8": "e11", "16": "f3", "13": "f6", "11": "f8", "9": "f11"}}
}
```

Run the relay-focused test and make it pass.

- [ ] **Step 5: Add input-protection and TSR slices**

Add RXEF075 and its two nets; run `-k input_protection`. Add 1N5822/P6KE12A and rerun. Add TSR1 plus its input/output capacitors and rerun. Each red/green cycle changes only that group.

- [ ] **Step 6: Add TPS709 and TPS3700 adapter slices**

Add each adapter with logical IC pins but mapping-derived physical DIP pins. Add TPS709 and its capacitors; run `-k tps709`. Add TPS3700/dividers/filters; run `-k tps3700`.

- [ ] **Step 7: Add permission and relay-drive slices**

Add `TREAD_OK`, `TREAD_OK_MCU`, AHC08 gate 1, `RELAY_CMD/GATE`, BC337, coil link, and coil TVS one net at a time. Run `-k 'permission or relay_drive'` after each net.

- [ ] **Step 8: Add transmit isolation and UART taps**

Add AHC08 gate 2 and AHC126 gate 1; run `-k tx_gate`. Add the 100 ohm TX path, 10 kOhm console tap, and 10 kOhm pin-3 tap separately, rerunning `-k uart` each time.

- [ ] **Step 9: Add feedback, VBUS, and LED slices**

Add GPIO4/5 feedback and run `-k feedback`. Add 2N7000, VBUS discharge, GPIO7 pull-up and run `-k vbus`. Add GPIO38/status LED, power LED, and every unused-input termination separately, running `-k 'led or unused_inputs'` after each addition.

Use the model's `nets` section as a redundant expected-membership record generated manually from the reviewed design. Tests compare it to membership derived from `items`, making omissions visible.

- [ ] **Step 10: Add BOM accounting tests**

Assert exact counts: one each of the named active/protection parts; 1×100 ohm, 1×330 ohm, 1×560 ohm, 1×1 kOhm, 1×4.7 kOhm, 12×10 kOhm, 1×100 kOhm, 1×150 kOhm, 1×255 kOhm; 4×100 nF, 1×1 uF, 2×1 nF, 1×22 uF, 2×10 uF, 1×4.7 uF; two LEDs. Assert every counted part has terminals, placement, and exactly one construction step.

- [ ] **Step 11: Write failing physical-connectivity tests**

Implement a test-only union-find that expands every a–e row, every f–j row, and each rail segment after the operator's measured breaks. Occupying a hole connects a component terminal to its tie group; only declared wire/link items union different groups. Resistors, capacitors, diodes, transistors, ICs, and relay contacts do not union their pins. Assert:

- each connected physical component contains exactly one canonical net;
- every canonical net's placed terminals form one connected physical component in the completed configuration;
- no hole contains more than one inserted lead;
- DIP bodies straddle e/f, the fixed K1 pin map above fits, and package rectangles do not overlap;
- all holes are within a–j/1–63 or a mapped rail segment;
- forward/reversed adapter mappings still connect each logical IC pin to the correct physical DIP hole;
- the four power paths are distinct before final links and become the canonical `GND/+8V_RAW` nets afterward.

Run `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py -k physical`; expect FAIL before complete placements exist.

- [ ] **Step 12: Place fixed package footprints**

Lock K1 pins as shown and run `-k physical_k1`; AHC08 pins 1–7 at e13–e19 and 14–8 at f13–f19 and run `-k physical_ahc08`; AHC126 pins 1–7 at e22–e28 and 14–8 at f22–f28 and run `-k physical_ahc126`; TPS3700 adapter DIP pins across e/f rows 31–33 and TPS709 across e/f rows 35–37, running the adapter case after each; console anchors e54–e61 and motor anchors f54–f61 for mapped contacts 1–8 and run `-k physical_rj45`.

- [ ] **Step 13: Place passive/TO-92/SIP groups through red-green cycles**

Place and test four bounded groups in order: protection/TSR, TPS passives, logic/UART/TO-92, then feedback/LED/DevKit jumpers. After each group run `-k physical`; a collision changes only that incomplete group and never weakens the solver.

- [ ] **Step 14: Add failing temporary-current-harness topology tests**

Model `temporary_configurations.normal` and `temporary_configurations.current_measure`. In `current_measure`, normal console +8 entry links are explicitly removed; temporary wires join both mapped console +8 contacts to fused-DMM `A` input, DMM `COM` output splits to both independent `P8_2/P8_8` paths, and a required evidence item confirms the lead is in the fused current jack before power. Assert no path bypasses the meter. In `normal`, assert all temporary wires are absent and both direct links are restored.

- [ ] **Step 15: Add and physically place the temporary harness items**

Add off-board DMM `A`/`COM` anchors, four temporary wires, two removable normal links, and configuration-specific drawing metadata. Run logical and physical graph tests for both configurations until PASS.

- [ ] **Step 16: Run tests and verify all topology/BOM/physical checks pass**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: PASS.

- [ ] **Step 17: Commit**

```bash
git add hardware/Esp32Tap/bringup/full-breadboard-model.json hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "feat(Esp32Tap): encode relay breadboard netlist"
```

### Task 3: Capture, validate, and resolve operator mappings

**Files:**
- Modify: `hardware/Esp32Tap/bringup/full-breadboard-model.json`
- Create: `hardware/Esp32Tap/bringup/breadboard-controller.js`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`
- Replace: `hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`

- [ ] **Step 1: Add failing mapping-schema tests**

Require four contracts:

```python
assert contracts["console_rj45"]["kind"] == "bijection"
assert contracts["console_rj45"]["logical_values"] == list(range(1, 9))
assert contracts["motor_rj45"]["logical_values"] == list(range(1, 9))
assert contracts["tps3700_adapter"]["logical_values"] == list(range(1, 7))
assert contracts["tps709_adapter"]["logical_values"] == list(range(1, 6))
assert contracts["breadboard_rails"]["kind"] == "measured_breaks"
```

RJ45 mapping state contains eight unique operator-entered physical terminal labels and a bijection from contacts 1–8 to those labels, plus a no-shorts confirmation. Adapter state maps each IC land to one unique DIP pin. Each of the four breadboard rails records `continuous` or one or more measured `break_after_column` values.

- [ ] **Step 2: Run the mapping-schema test and verify failure**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py -k mapping`

Expected: FAIL for missing mapping contracts.

- [ ] **Step 3: Add mapping contracts and logical endpoint syntax**

Add logical endpoints such as `breakout:console:contact:6`, `adapter:tps709:ic_pin:1`, `rail:top_positive:column:40`, and ordinary `hole:e:12`. Physical placement data describes off-board terminal anchor order and each adapter's DIP-pin coordinates but never assigns logical contacts/IC pins.

- [ ] **Step 4: Make mapping-schema tests pass**

Run the filtered Python test; expect PASS.

- [ ] **Step 5: Write failing pure-JavaScript validator tests**

Test `validateMapping(contract, value)` rejects blank/duplicate terminal labels, missing/duplicate logical contacts, adapter maps with repeated DIP pins, out-of-range pins, and unsatisfied no-short confirmations. Test valid forward and reversed breakout/adapter maps.

- [ ] **Step 6: Implement and test `validateMapping`**

Export the DOM-free function as `window.BreadboardCore.validateMapping` from `hardware/Esp32Tap/bringup/breadboard-controller.js`. Node VM tests load that exact file; the renderer later embeds it unchanged as `script#wizard-controller`. Run the Node test after each validator case.

- [ ] **Step 7: Write failing endpoint-resolver tests**

Test `resolveEndpoint(logicalEndpoint, mappings, placements)` for two opposite RJ45 orientations and two opposite SOT-adapter orientations. Assert the same logical K1/TPS net produces different displayed terminal labels or holes when mapping changes. Test rail columns on either side of each measured break and require an explicit rail-link warning when a net crosses segments.

- [ ] **Step 8: Implement `resolveEndpoint` one endpoint kind at a time**

Implement and test `hole`, then `breakout`, then `adapter`, then `rail`. Unknown or incomplete mapping returns `{status: "locked", reason: ...}`; it never falls back to a default orientation.

- [ ] **Step 9: Add mapping-order tests**

Assert all mapping capture/continuity/no-short steps precede the first dependent construction step. A step with unresolved endpoints must be locked in controller tests even if localStorage is manually edited.

- [ ] **Step 10: Commit**

```bash
git add hardware/Esp32Tap/bringup/full-breadboard-model.json hardware/Esp32Tap/bringup/breadboard-controller.js hardware/Esp32Tap/tests/test_breadboard_wizard.py hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js
git commit -m "feat(Esp32Tap): validate physical breadboard mappings"
```

### Task 4: Encode phases, atomic construction steps, and numerical gates

**Files:**
- Modify: `hardware/Esp32Tap/bringup/full-breadboard-model.json`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Add failing step-order tests**

Require these ordered phase IDs:

```python
EXPECTED_PHASES = [
    "inventory", "map", "pass_through", "relay", "power", "logic",
    "unpowered_audit", "bench_power", "bench_relay", "observer_firmware",
    "treadmill_bypass", "functional_firmware",
]
```

Assert one item or wire per construction step; measurement steps may highlight one net. Assert that conductor 1/7/2/8 tests occur before four final common links, `COIL POWER` is open for disabled/enabled unloaded TPS709 checks, no step before `unpowered_audit` applies power, and treadmill attachment cannot occur before standalone observer identity.

- [ ] **Step 2: Add failing numerical-limit tests**

Require exact structured limits rather than prose matching:

```python
assert limits["treadmill_current_ma"]["max"] == 500
assert limits["path_drop_mv"]["max"] == 50
assert limits["contact_temp_c"]["max"] == 40
assert limits["temp_rise_c"]["max"] == 10
assert limits["coil_voltage_v"]["min"] == 4.50
assert limits["coil_current_ma"] == {"min": 90, "max": 110}
assert limits["bc337_vce_v"]["max"] == 0.30
assert limits["tps709_enabled_v"] == {"min": 4.75, "max": 5.25}
assert limits["protected_vin_v"] == {"min": 7.20, "max": 7.90}
assert limits["tsr_output_v"] == {"min": 3.20, "max": 3.40}
assert limits["tps709_disabled_v"]["max"] == 0.25
assert limits["coil_off_rail_current_ma"]["max"] == 50
assert limits["uv_boundary_v"] == {"min": 6.25, "max": 6.55}
assert limits["ov_boundary_v"] == {"min": 10.30, "max": 10.90}
assert limits["device_temp_c"]["max"] == 45
assert limits["nc_restore_ms"]["max"] == 100
assert limits["initial_supply_v"] == {"target": 8.00, "tolerance": 0.05}
assert limits["initial_current_limit_ma"]["max"] == 250
assert limits["relay_test_current_limit_ma"]["max"] == 500
assert limits["coil_dwell_s"]["min"] == 300
assert limits["bypass_dwell_s"]["min"] == 900
```

Also require explicit boolean evidence for `TREAD_OK` low below UV, high at 8.00 V, low above OV; energized feedback `(GPIO4,GPIO5) == (1,0)`; restored feedback `(0,1)`; explicit STOP for `(0,0)` and `(1,1)`; and measured UART idle levels on GPIO16 and GPIO18 before either UART is configured. Each evidence requirement names a type (`boolean`, `number`, `text`, `photo`, `mapping`, or `build_identity`), unit where applicable, comparison, and STOP message.

- [ ] **Step 3: Run tests and verify failures**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: FAIL for missing phases/steps/limits.

- [ ] **Step 4: Add structured power states and failing parity tests**

Add and test exact state objects:

```json
{
  "all_off": {"sources": [], "standalone_power": "removed", "coil_power": "removed", "firmware": null, "command": "none", "observe": "meter"},
  "bench_predevkit": {"sources": ["bench_vin"], "devkit": "disconnected", "standalone_power": "installed", "coil_power": "removed", "firmware": null, "command": "none", "observe": "meter"},
  "usb_diagnostic": {"sources": ["pi_uart_usb"], "standalone_power": "removed", "coil_power": "removed", "firmware": "diagnostic", "command": "none", "observe": "uart"},
  "bench_exerciser_coil_open": {"sources": ["pi_uart_usb", "bench_vin"], "standalone_power": "removed", "coil_power": "removed", "firmware": "relay_exerciser", "command": "uart", "observe": "uart_meter"},
  "bench_exerciser_coil_closed": {"sources": ["pi_uart_usb", "bench_vin"], "standalone_power": "removed", "coil_power": "installed", "firmware": "relay_exerciser", "command": "uart", "observe": "uart_meter"},
  "bench_observer_standalone": {"sources": ["bench_vin"], "devkit": "connected", "standalone_power": "installed", "coil_power": "installed", "firmware": "standalone_observer", "command": "none", "observe": "wifi_event_log"},
  "treadmill_observer": {"sources": ["treadmill_vin"], "standalone_power": "installed", "coil_power": "installed", "firmware": "standalone_observer", "command": "none", "observe": "wifi_event_log"},
  "treadmill_functional": {"sources": ["treadmill_vin"], "standalone_power": "installed", "coil_power": "installed", "firmware": "functional", "command": "wifi", "observe": "wifi_event_log"}
}
```

Schema tests reject any state containing both `pi_uart_usb` and an installed `standalone_power`, any treadmill state with USB, any pre-DevKit state with a connected DevKit, or any state whose firmware/transport conflicts with its role. Add an explicit phase-to-allowed-state table and assert every step's state is permitted for that phase.

- [ ] **Step 5: Add inventory atomic steps**

Add one step for each tool and counted part confirmation. Run `-k inventory_steps`; expect red before each batch and green afterward.

- [ ] **Step 6: Add console and motor mapping steps**

Add eight terminal-label and eight continuity-assignment steps plus no-shorts evidence for `console`; run `-k console_mapping_steps`. Repeat independently for `motor` and run `-k motor_mapping_steps`.

- [ ] **Step 7: Add adapter and rail mapping steps**

Add TPS3700 land-to-DIP assignments and run its case; repeat for TPS709. Add one continuity observation per rail segment and every measured break, then run `-k rail_mapping_steps`.

- [ ] **Step 8: Add pass-through and final-link steps**

Add one conductor, its isolated meter check, and adjacent-terminal no-short check per contact 1/2/3/4/5/7/8. Add each of four final common links only afterward. Run `-k pass_through_steps`.

- [ ] **Step 9: Add relay and protected-power steps**

Add relay placement, orientation, coil resistance, each NC/NO/COM meter observation, then one step per protection/power component. Run relay steps first, then power steps.

- [ ] **Step 10: Add logic/UART/feedback/LED steps**

Add one step per IC, adapter, termination, GPIO jumper, UART tap, feedback part, VBUS part, LED, and coil link. Run `-k logic_steps`, then `-k uart_steps`, then `-k feedback_steps`.

- [ ] **Step 11: Add unpowered-audit steps**

Add each rail-short, intended-continuity, unintended-open, polarity/orientation, relay-bypass, TX-isolation, and overhead-photo evidence item separately. Run `-k unpowered_audit`.

- [ ] **Step 12: Add pre-DevKit bench-power steps**

Using only `bench_predevkit`, add current-limit/setpoint, VIN, TSR, disabled TPS709, rail-current, UV/TREAD low, 8 V/TREAD high, and OV/TREAD low evidence one at a time. Run `-k bench_predevkit_steps` after each pair.

- [ ] **Step 13: Add bench-exerciser coil-open steps**

Require exerciser identity, `bench_exerciser_coil_open`, bounded relay-on command, enabled unloaded TPS709 reading, relay-off command, supplies removed, and <0.25 V discharge before permitting `COIL POWER`. Run `-k coil_open_steps`.

- [ ] **Step 14: Add bench-exerciser coil-closed steps**

Add the 500 mA ceiling, energized 90–110 mA/≥4.5 V/≤0.30 V checks, `(1,0)` feedback, invalid-pair STOP cases, five-minute dwell and two device temperatures, loss-source tests, ≤100 ms restoration, and `(0,1)` feedback. Run `-k coil_closed_steps`.

- [ ] **Step 15: Add standalone-observer bench qualification**

Require observer identity, remove USB, install STANDALONE POWER, select `bench_observer_standalone`, and prove DevKit boot, expected 3.3 V, Wi-Fi API reachability, bounded event-log retrieval, `(0,1)` relay feedback, and relay/TX disabled. A phase dependency keeps all treadmill steps locked until this record passes. Run `-k bench_observer_steps`.

- [ ] **Step 16: Add current-harness and treadmill-observer steps**

Require the passed bench-observer record and `treadmill_observer`. Add normal-links removed, temporary harness wires installed, fused-current-jack confirmation, ≤500 mA reading, power removed, temporary wires removed, direct links restored, voltage drops, UART idle observations, 15-minute dwell, and each temperature reading. Each harness step references the configuration modeled in Task 2. Run `-k treadmill_bypass_steps`.

- [ ] **Step 17: Add functional identity plus safety-artifact contract**

Add `safety_test_artifact` evidence with exact fields: functional recipe ID, Git commit, 64-hex test-manifest SHA-256, suite ID, `passed: true`, and required passed checks for `tread_ok`, `relay_feedback`, `tx_gate`, `zero_motion_default`, and `fault_release`. Controller validation requires the artifact recipe ID to equal the functional build identity. Add failing tests proving identity alone, artifact alone, mismatched identities, missing checks, or `passed: false` keep guarded transfer LOCKED.

- [ ] **Step 18: Add functional gate steps and common step schema**

Encode the 13 reviewed workflow stages as the 12 phase IDs above (the two bench steps share the bench phases). Each step has:

```json
{
  "id": "place_k1",
  "phase": "relay",
  "kind": "construction",
  "highlight": ["k1"],
  "instruction": "...",
  "purpose": "...",
  "warnings": [],
  "evidence": [{"id": "placed", "type": "boolean", "required": true}],
  "power_state": "all_off"
}
```

Add functional build identity, validated matching `safety_test_artifact`, and `treadmill_functional` state. Guarded transfer instructions appear only after both evidence objects pass. Validate every prior step against this schema and its structured `power_state`; no free-form power string is permitted.

- [ ] **Step 19: Add three explicit firmware roles**

Model `relay_exerciser`, `standalone_observer`, and `functional`. Each requires a build identity and declares allowed transports and outputs. Keep current `devkit-bringup` in a fourth `diagnostic` role with `relay: false`, `tx: false`, and no path that satisfies later roles.

- [ ] **Step 20: Run tests and verify pass**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py`

Expected: PASS.

- [ ] **Step 21: Commit**

```bash
git add hardware/Esp32Tap/bringup/full-breadboard-model.json hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "feat(Esp32Tap): define guarded construction workflow"
```

### Task 5: Build the deterministic self-contained HTML renderer

**Files:**
- Create: `hardware/Esp32Tap/bringup/render_breadboard_wizard.py`
- Create: `hardware/Esp32Tap/bringup/breadboard-wizard.template.html`
- Read/embed: `hardware/Esp32Tap/bringup/breadboard-controller.js`
- Replace: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Add failing render/parity tests**

Test that `render_breadboard_wizard.py --check` exits nonzero when generated HTML differs, that HTML contains exactly one `script#wiring-data`, and that embedded JSON equals `full-breadboard-model.json` exactly.

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py -k render`

Expected: FAIL because renderer/template do not exist.

- [ ] **Step 3: Implement the renderer**

Use only `argparse`, `html`, `json`, and `pathlib`. Serialize with `sort_keys=True`, escape `</script` as `<\/script`, replace one literal `{{WIRING_DATA}}` and one `{{CONTROLLER_JS}}`, and end output with one newline. A parity test extracts `script#wizard-controller` and requires byte equality with `breadboard-controller.js`. Support:

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py --check
```

The first writes the artifact; the second compares in memory and never writes.

- [ ] **Step 4: Create the template shell**

Preserve the existing mobile header, progress panel, Back/Next controls, reset confirmation, and zoom controls. Replace old simulator-specific content with containers for inventory, reference links, breadboard SVG, atomic instructions, evidence inputs, netlist, phase locks, and safety-state banner.

- [ ] **Step 5: Render and run parity tests**

Run:

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py --check
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py -k render
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/bringup/render_breadboard_wizard.py hardware/Esp32Tap/bringup/breadboard-controller.js hardware/Esp32Tap/bringup/breadboard-wizard.template.html hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py
git commit -m "feat(Esp32Tap): generate portable breadboard builder"
```

### Task 6: Implement the pure controller and evidence locks

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-controller.js`
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.template.html`
- Replace: `hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`
- Regenerate: `hardware/Esp32Tap/bringup/breadboard-wizard.html`

- [ ] **Step 1: Write failing controller tests**

Extract `script#wizard-controller` into a VM as existing tests do. Test:

- required booleans and text cannot advance empty;
- mapping evidence must pass `validateMapping`, and dependent endpoints resolve through `resolveEndpoint` before a step can unlock;
- numerical evidence accepts an in-range value and rejects boundaries outside the model comparison;
- a rejected value stores the reading but does not satisfy the step;
- build identity requires exact role and nonempty recipe ID;
- diagnostic identity cannot satisfy exerciser/observer/functional roles;
- functional identity cannot unlock transfer without a matching passed safety-test artifact containing every required check;
- phase prerequisites prevent index jumping through mutated local storage;
- current source/jumper/firmware/command/observation state must match the step's structured power-state object;
- USB plus installed `STANDALONE POWER`, treadmill plus USB, or wrong firmware transport always locks the step;
- stored state uses only the v2 key and schema/version mismatch resets safely;
- Back retains evidence, Reset removes only the v2 key, zoom clamps to 0.7–2.0.

- [ ] **Step 2: Run and verify failure**

Run: `node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`

Expected: FAIL against the old controller.

- [ ] **Step 3: Implement `createBreadboardController`**

Keep it DOM-free. Preserve Task 3's `validateMapping` and `resolveEndpoint`, and expose `currentStep`, `setEvidence`, `evidenceResult`, `resolvedEndpoints`, `powerStateResult`, `canNext`, `next`, `back`, `phaseStatus`, `index`, `zoomIn`, `zoomOut`, and `reset`. Recompute validity from the canonical model on every read; never trust a persisted `complete` flag.

- [ ] **Step 4: Run Node tests**

Run: `node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js`

Expected: PASS.

- [ ] **Step 5: Regenerate and run parity tests**

Run:

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py --check
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/bringup/breadboard-controller.js hardware/Esp32Tap/bringup/breadboard-wizard.template.html hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js
git commit -m "feat(Esp32Tap): enforce breadboard evidence gates"
```

### Task 7: Render the actual breadboard, netlist, and staged instructions

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.template.html`
- Create: `hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js`
- Regenerate: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`

- [ ] **Step 1: Write failing DOM/rendering tests**

Using a minimal fake document implementation, assert the view:

- draws a 63-column full-size breadboard with split rails and a center trench;
- draws DevKit and RJ45 breakouts off-board with printed signal labels;
- distinguishes top-view G5V-2 numbering from the datasheet bottom view;
- renders every placed item and wire with `data-item-id` matching the model;
- shows actual operator-entered breakout labels and adapter DIP holes returned by `resolveEndpoint`, never default pin labels;
- keeps every dependent instruction LOCKED until its required mapping is complete;
- shows only the active step as highlighted while completed items remain visible;
- renders exact endpoint coordinates and net name;
- renders numeric unit/range and immediate PASS/STOP result;
- renders source, STANDALONE POWER, COIL POWER, firmware, command, and observation fields from the exact structured power state;
- links references with `target="_blank" rel="noopener noreferrer"`;
- displays locked firmware phases without hidden bypass controls.

- [ ] **Step 2: Run and verify failure**

Run: `node --test hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js`

Expected: FAIL because the new view is not implemented.

For Steps 3–8, repeat the same red/run/green loop for each named function or panel: add one focused assertion to `breadboard_wizard_dom.test.js`, run that test to see it fail, implement only that function/panel, and rerun to PASS before moving to the next name.

- [ ] **Step 3: Draw the static board and off-board frames**

Implement `drawBreadboard`, `holePosition`, and `drawOffboardDevice`; make the 63 columns, rail breaks, center trench, DevKit frame, and two breakout frames pass focused DOM assertions.

- [ ] **Step 4: Draw orientation-sensitive packages**

Implement `drawDip`, `drawRelay`, and `drawAdapter`. Render the relay case mark/top-view pin numbers and DIP notches. Adapter labels come only from resolved operator mapping; test forward and reversed maps.

- [ ] **Step 5: Draw passive parts and wires**

Implement `drawTwoLeadPart`, `drawWire`, and `drawNetHighlight`. Resolve both endpoints first; a locked result draws no wire and displays the mapping reason. Use visible labels plus color; never encode net identity by color alone.

- [ ] **Step 6: Render mapping and evidence views**

Render terminal-label entry, bijection assignment, no-short confirmations, adapter land-to-DIP assignment, rail-break observations, numerical evidence, and immediate PASS/STOP status. Make mapping DOM tests pass before adding later panels.

- [ ] **Step 7: Render instruction and power-state views**

Render one instruction card, purpose, warnings, resolved endpoints, manufacturer link, applicable numerical limit, and all structured power-state fields. Add searchable BOM and netlist panels generated from the same model.

- [ ] **Step 8: Add legends and Python completeness checks**

Add a persistent legend for relay case mark, diode/LED/electrolytic polarity, DIP notch, and mapping-derived labels. Assert every item ID occurs in SVG-capable model placement, every reference URL appears in generated HTML, old terms (`DPDT switch simulator`, `GPIO6 switch`, `jumper-only`) are absent, and current diagnostic firmware is explicitly labeled no-control.

- [ ] **Step 9: Regenerate and run all focused tests**

Run:

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add hardware/Esp32Tap/bringup/breadboard-wizard.template.html hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js
git commit -m "feat(Esp32Tap): draw full relay breadboard builder"
```

### Task 8: Mobile/accessibility polish and live verification

**Files:**
- Modify: `hardware/Esp32Tap/bringup/breadboard-wizard.template.html`
- Regenerate: `hardware/Esp32Tap/bringup/breadboard-wizard.html`
- Modify: `hardware/Esp32Tap/tests/test_breadboard_wizard.py`
- Modify: `hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js`
- Create: `hardware/Esp32Tap/tests/breadboard_wizard_browser.test.js`

- [ ] **Step 1: Add failing accessibility and responsive tests**

Assert labels bind to inputs, status updates use `aria-live`, buttons have accessible names, reference links are keyboard reachable, focus moves to the new step heading, SVG has title/description, and CSS contains no fixed mobile minimum width.

- [ ] **Step 2: Run static tests and verify failure**

Run the Python and Node suites; expect at least one new assertion to fail.

- [ ] **Step 3: Implement accessible responsive behavior**

Use a single-column instruction-first phone layout and a two-column desktop layout. Keep Back/Next sticky without covering evidence fields. Make SVG horizontally pannable at phone width while zoom controls retain 0.7–2.0 limits. Use high-contrast PASS/STOP/LOCKED states with icons and text. Re-run static tests until PASS.

- [ ] **Step 4: Write a failing real-Chrome navigation test**

Create a Node helper using only `node:child_process`, `node:fs`, `node:http`, `node:os`, and Node 24's global `WebSocket`. Launch:

```text
/usr/bin/google-chrome --headless=new --no-sandbox --disable-gpu
  --remote-debugging-port=0 --user-data-dir=<mkdtemp>
  file://<absolute breadboard-wizard.html>
```

Read the numeric port from the first line of `DevToolsActivePort`. Use `node:http` to GET `http://127.0.0.1:<port>/json/list`, select the `type: "page"` entry whose URL ends in `breadboard-wizard.html`, and connect to that entry's `webSocketDebuggerUrl` (not the browser-level path on the second `DevToolsActivePort` line). Wrap request IDs for `Runtime.evaluate`, `Page.reload`, `Emulation.setDeviceMetricsOverride`, and `Page.captureScreenshot`; wait for `Page.loadEventFired` after navigation/reload. First test must fail until the view/controller integration is complete.

- [ ] **Step 5: Add real-browser mapping and persistence cases**

In Chrome, enter a reversed console mapping, advance, and assert a later endpoint displays the mapped physical terminal. Reload and verify mappings/evidence/current step persist in real localStorage. Corrupt the stored schema and verify safe reset. Attempt to skip with localStorage edits and verify the dependent phase remains LOCKED.

- [ ] **Step 6: Add real-browser interaction and power cases**

Exercise Back/Next, reset cancel/confirm, numeric PASS/STOP, mapping validation errors, firmware-role rejection, safety-artifact rejection/matching, and structured power-state display. Verify no control can create USB+TSR or treadmill+USB state and treadmill observer remains locked until bench observer qualification passes.

- [ ] **Step 7: Add real-browser responsive/focus cases**

Set 390×844 and 1440×900 metrics. Assert the phone layout has no document-level horizontal overflow, the SVG pan region remains scrollable, sticky controls do not cover the active evidence input, zoom clamps 0.7–2.0, and focus lands on the new step heading. Capture phone and desktop PNGs through `Page.captureScreenshot` into a temporary directory; tests assert nonempty output but do not commit screenshots.

- [ ] **Step 8: Regenerate and run complete focused verification**

Run:

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py --check
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js hardware/Esp32Tap/tests/breadboard_wizard_browser.test.js
git diff --check
```

Expected: all PASS and no whitespace errors.

- [ ] **Step 9: Serve the exact generated artifact with exact commands**

Run:

```bash
builder_server_json=$(/home/ssilver/.codex/superpowers/skills/brainstorming/scripts/start-server.sh --project-dir "$PWD" --host 0.0.0.0 --url-host 192.168.1.15)
builder_screen_dir=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["screen_dir"])' "$builder_server_json")
cp -f hardware/Esp32Tap/bringup/breadboard-wizard.html "$builder_screen_dir/breadboard-wizard-full-v2.html"
sha256sum hardware/Esp32Tap/bringup/breadboard-wizard.html "$builder_screen_dir/breadboard-wizard-full-v2.html"
python3 -c 'import json,sys; print(json.loads(sys.argv[1])["url"])' "$builder_server_json"
```

Expected: both hashes match and the printed LAN URL serves the full-v2 file. Open it and compare against the automated phone/desktop captures.

- [ ] **Step 10: Record issue evidence**

Run `bd update precor-9_3x-1y2.2 --notes="Builder tests passed; record artifact SHA-256 and live URL here. Exerciser, observer, and functional firmware remain external locked prerequisites."`, substituting the actual hash and URL.

- [ ] **Step 11: Commit**

```bash
git add hardware/Esp32Tap/bringup/breadboard-wizard.template.html hardware/Esp32Tap/bringup/breadboard-wizard.html hardware/Esp32Tap/tests/test_breadboard_wizard.py hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js hardware/Esp32Tap/tests/breadboard_wizard_browser.test.js
git commit -m "fix(Esp32Tap): finish mobile breadboard workflow"
```

### Task 9: Final repository verification and publication

**Files:**
- Verify only unless a test exposes an in-scope defect.

- [ ] **Step 1: Run the builder-specific quality gate from a clean shell**

```bash
python3 hardware/Esp32Tap/bringup/render_breadboard_wizard.py --check
python3 -m pytest -q hardware/Esp32Tap/tests/test_breadboard_wizard.py
node --test hardware/Esp32Tap/tests/breadboard_wizard_behavior.test.js hardware/Esp32Tap/tests/breadboard_wizard_dom.test.js hardware/Esp32Tap/tests/breadboard_wizard_browser.test.js
```

Expected: all PASS.

- [ ] **Step 2: Run adjacent Esp32Tap source-of-truth checks**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_design.py hardware/Esp32Tap/tests/test_generated_artifacts.py
python3 hardware/Esp32Tap/firmware/esp32/tools/check_pins.py
git diff --check
```

Expected: all PASS. If an adjacent test requires unavailable heavyweight tooling, record the exact command and blocker rather than weakening the builder tests.

- [ ] **Step 3: Confirm generated artifact and branch state**

```bash
git status --short
git log --oneline -8
sha256sum hardware/Esp32Tap/bringup/breadboard-wizard.html
```

Expected: no uncommitted implementation files and a recorded artifact hash.

- [ ] **Step 4: Close or update Beads accurately**

Search before filing to avoid duplicates:

```bash
bd search "relay exerciser firmware"
bd search "standalone observer firmware"
bd search "functional treadmill firmware"
```

For each genuinely absent item, run `bd create --title="..." --description="Implement the firmware prerequisite named by the full-breadboard builder; this is not supplied by the browser UI." --type=task --priority=0`, then add a dependency from the parent work as appropriate. Close `precor-9_3x-1y2.2` with `bd close precor-9_3x-1y2.2 --reason="Full-breadboard builder, tests, and live artifact verified"` only if the guide acceptance criteria are satisfied; otherwise use `bd update precor-9_3x-1y2.2 --notes="<exact remaining work>"` and leave it in progress.

- [ ] **Step 5: Rebase and push**

```bash
git pull --rebase
git push
bd dolt push
git status --short --branch
```

Expected: branch reports up to date with origin. `bd dolt push` succeeds; if this checkout still reports that no Dolt remote is configured, record that exact infrastructure blocker in the issue rather than claiming cross-machine Beads sync succeeded.
