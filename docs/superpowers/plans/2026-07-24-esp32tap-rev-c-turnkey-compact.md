# Esp32Tap Rev C Turnkey Compact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the smallest validated Esp32Tap revision that JLCPCB can
return as a completely assembled SMT PCBA, using factory-made plug-in RJ45
harnesses and requiring no owner soldering or crimping.

**Architecture:** Preserve Rev B's treadmill-only power, normally closed
bypass, hardware `TREAD_OK` gates, data-only USB, and four-layer impedance
stack. Replace the two through-hole RJ45 jacks with physically distinct,
locking SMT harness connectors; choose only live JLC-placeable parts; relayout
the generated PCB and enclosure around the compact interfaces and a fully
on-board ESP antenna; then bind reproducible electrical, mechanical, ngspice,
DFM, placement, harness, and delivered-cost evidence to the exact artifacts.

**Tech Stack:** Python 3, pytest, KiCad 9 Python/CLI, pcbnew, OpenSCAD,
trimesh, ngspice 42 plus pinned Docker ngspice 39, JLCPCB/LCSC official
catalog and authenticated quote workflow.

**Design specification:**
`docs/superpowers/specs/2026-07-24-esp32tap-rev-c-turnkey-compact-design.md`

**Issue:** `precor-9_3x-1dj`

---

### Task 0: Isolate execution from the user's working tree

**Files:**

- Create worktree: `.worktrees/esp32tap-rev-c`

- [ ] **Step 1: Create and verify an isolated worktree**

Use `superpowers:using-git-worktrees`. Confirm `.worktrees/` is ignored, fetch
the pushed `main`, and create branch `feat/esp32tap-rev-c` from the exact
approved plan commit. Do not stash, stage, or copy the user's modified
`cpp/captures/RS485_DISCOVERY.md` or untracked `static/`.

- [ ] **Step 2: Confirm a clean execution baseline**

Run:

```bash
git status --short
make -C hardware/Esp32Tap clean-check
```

Expected: empty worktree status and the Rev B reproducibility gate passes.

### Task 1: Create fail-closed evidence and current-envelope gates

**Files:**

- Create: `hardware/Esp32Tap/evidence/schemas.py`
- Create: `hardware/Esp32Tap/evidence/model.json`
- Create: `hardware/Esp32Tap/evidence/vendor.json`
- Create: `hardware/Esp32Tap/evidence/physical.json`
- Create: `hardware/Esp32Tap/evidence/predecessor.json`
- Create: `hardware/Esp32Tap/tests/test_evidence.py`
- Create: `hardware/Esp32Tap/tests/test_harnesses.py`
- Create: `hardware/Esp32Tap/harness/requirements.json`
- Create: `hardware/Esp32Tap/harness/validate_harnesses.py`
- Modify: `hardware/Esp32Tap/tools/export_fab.py`

- [ ] **Step 1: Write failing evidence-schema tests**

Add tests requiring three disjoint namespaces and allowed statuses:

```python
def test_evidence_classes_cannot_be_promoted(tmp_path):
    evidence = load_all(tmp_path)
    assert evidence["model"]["status"] in {"MODELED", "UNSUPPORTED"}
    assert evidence["vendor"]["status"] in {
        "NOT_REVIEWED", "PARTIAL_VENDOR_REVIEW", "VENDOR_ACCEPTED"
    }
    assert evidence["physical"]["status"] in {
        "NOT_MEASURED", "PARTIAL_PHYSICAL", "PHYSICALLY_VALIDATED"
    }


def test_unmeasured_current_envelope_blocks_part_release(evidence):
    envelope = evidence["physical"]["treadmill_current_envelope"]
    if envelope["status"] != "MEASURED":
        assert not release_allowed(evidence, "connector_selection")
```

The validator must reject a model assertion in `physical.json`, an
operator-observed browser fact in `model.json`, any unbound raw record, and
`TURNKEY_QUOTED` while a required vendor/cost field is absent.
`predecessor.json` records and byte-hashes these exact artifacts:

- `hardware/PiZeroHat/README.md`
- `hardware/PiZeroHat/kicad/WIRING.md`
- `hardware/PiZeroHat/kicad/PiZeroHat.kicad_sch`
- `hardware/PiZeroHat/kicad/PiZeroHat.kicad_pcb`
- the approved Rev C specification containing the owner's authorization

Add an action-matrix test proving that this verified predecessor basis permits
only `connector_selection`, `layout`, `verification_fabrication`, and
`no_purchase_quote`. It must keep `physical.json` literally `NOT_MEASURED`
and reject `production_release`, `deployment`, and `TURNKEY_QUOTED`.

- [ ] **Step 2: Run tests and confirm the evidence layer is absent**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_evidence.py
```

Expected: fail because `evidence.schemas` and evidence files do not exist.

- [ ] **Step 3: Implement the minimal schema validator and HOLD records**

Create strict JSON records with `additionalProperties: false` behavior in
Python. Initial records say `MODELED`, `NOT_REVIEWED`, and `NOT_MEASURED`;
each open physical item names the instrument/fixture/data required. Implement
`release_allowed()` so connector selection, fabrication export, and
`TURNKEY_QUOTED` all fail closed until their prerequisites are satisfied.
Wire the gate into `validate_harnesses.py --release` and
`export_fab.py --require-rev-c-release`; ordinary Rev B audit mode remains
available while Rev C is unselected.
Conservative verification uses a separate, hash-bound predecessor action
matrix; it must never satisfy the physical or turnkey predicates.

- [ ] **Step 4: Run schema tests green**

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_evidence.py
```

Expected: all schema tests pass while the project status remains HOLD.

- [ ] **Step 5: Prove both release entry points fail closed**

Add subprocess tests:

```python
@pytest.mark.parametrize("command", [
    ["python3", "harness/validate_harnesses.py", "--release"],
    ["python3", "tools/export_fab.py", "--audit-only",
     "--require-rev-c-release"],
])
def test_not_measured_blocks_release(esp32tap_dir, command):
    result = subprocess.run(command, cwd=esp32tap_dir, text=True,
                            capture_output=True)
    assert result.returncode != 0
    assert "NOT_MEASURED" in result.stderr
```

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_evidence.py
```

Expected: PASS, proving both release entry points reject the committed HOLD
state.

- [ ] **Step 6: Locate or acquire the treadmill current envelope**

Search repository measurement records for raw, instrument-bound treadmill
source voltage, source impedance, maximum continuous pass-through current,
startup/transient waveform and duration, installed ambient/airflow/bundling,
and USB-ground potential/current. Validate hashes and provenance. If the
complete measured envelope is absent, record exactly what is missing. The
owner-authorized PiZeroHat predecessor basis may then release only conservative
verification connector selection, layout, fabrication, and quotation using
the fixed 2.0 A/22 AWG requirements in the specification. Simulation must not
fill physical fields, `physical.json` remains `NOT_MEASURED`, and no turnkey
or deployment claim is permitted.

- [ ] **Step 7: Validate the physical release gate**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_evidence.py
python3 hardware/Esp32Tap/evidence/schemas.py \
  --require connector-selection --basis conservative-predecessor
```

Expected: pytest passes in both HOLD and measured states; the explicit
connector-selection command passes with either bound measured evidence or the
repository-bound conservative predecessor evidence and fixed 2.0 A/22 AWG
limits. Fabrication uses a separate `verification_fabrication` action;
production/deployment and `TURNKEY_QUOTED` remain blocked while
`NOT_MEASURED`. If the conservative gate fails, stop safely.

- [ ] **Step 8: Commit the green evidence framework**

```bash
git add hardware/Esp32Tap/evidence hardware/Esp32Tap/tests/test_evidence.py \
  hardware/Esp32Tap/tests/test_harnesses.py \
  hardware/Esp32Tap/harness hardware/Esp32Tap/tools/export_fab.py
git commit -m "test: add Rev C fail-closed evidence gates"
```

### Task 2: Select exact compact connectors, harness parts, switches, and module

**Files:**

- Create: `hardware/Esp32Tap/harness/candidates.json`
- Create: `hardware/Esp32Tap/harness/CONNECTOR-SELECTION.md`
- Create: `hardware/Esp32Tap/harness/console-harness.csv`
- Create: `hardware/Esp32Tap/harness/motor-harness.csv`
- Create: `hardware/Esp32Tap/harness/validate_harnesses.py`
- Create: `hardware/Esp32Tap/bom/REV-C-PART-SELECTION.json`
- Modify: `hardware/Esp32Tap/tests/test_harnesses.py`
- Modify: `hardware/Esp32Tap/tests/test_jlc_stock.py`

- [ ] **Step 1: Add failing evidence-schema tests**

Require at least three locking SMT wire-to-board candidates, an SMT-RJ45
baseline, exact manufacturer/JLC codes, current/voltage/contact-resistance
ratings, mating housings and terminals, packaging, official datasheet URLs,
STEP/footprint provenance, JLC assembly class, and current stock timestamp.
For every connector/module combination require PCB width, length and height,
antenna volume, enclosure width/length/height, minimum bend radius, service
clearance, assembly support, installed bounding volume, and explicit rejection
constraints. Require both WROOM and MINI module comparisons and exact switch
alternatives. Run the focused test and observe failure before writing the
candidate records:

Add executable validator tests requiring every individual new SMT-header,
housing-terminal, and harness-wire power/ground path to retain at least 2.0 A
rating after circuit-count and +85 °C derating; 22 AWG or larger power/ground
wire; and at least 24 V and -20 °C through +85 °C ratings for the complete
mating system's electrical elements. Require strain-relief material,
mechanical-retention, and -20 °C through +85 °C environmental evidence without
inventing a voltage rating for a nonconductive part. Require an unequal-contact
2.0 A modeled case and separate
single-open cases for the new board-connector paths. The standard RJ45 end
retains dual parallel contacts from PiZeroHat; if its official rating does not
support a 2.0 A single-contact case, require that case to remain explicitly
`UNSUPPORTED` and physically open. Require a worst-case unequal-sharing
calculation proving both RJ45 power contacts and both ground contacts stay
within their official circuit-count and +85 °C derated ratings at 2.0 A total
normal load.

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_harnesses.py::test_candidate_matrix_is_complete
```

Expected: FAIL on missing candidate fields.

- [ ] **Step 2: Collect only official current evidence**

Use manufacturer datasheets and official JLCPCB/LCSC pages. Reject any part
whose board connector is not tape-and-reel SMT, whose housing/terminals cannot
be bought as a finished harness. Before the live workflow exists, mark the
exact selection `PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF`; do not infer
placement support from public stock. Task 10 rejects any part whose exact row
cannot be selected and must either close that gate or force
reselection/regeneration before order readiness.
Reject any new board connector/terminal below 2.0 A per individual
power/ground contact after circuit-count and +85 °C derating. Reject any
conductive/insulating electrical mating-system element below 24 V or without
-20 °C to +85 °C coverage. Require mechanical strain-relief material,
retention, and the same temperature range without a fictional voltage field.
Require 22 AWG or larger power/ground conductors and verify each single-open
new board-connector case leaves the remaining contact within rating at 2.0 A.
For the standard RJ45 termination, select the highest-current officially rated
non-magnetic part and preserve the dual power/ground contacts; require the
documented 2.0 A unequal-sharing normal-load calculation and never convert an
unpublished single-contact rating into a pass.

- [ ] **Step 3: Choose physically incompatible Console and Motor interfaces**

Prefer two key codes; otherwise select different circuit counts/families.
Document installed RJ45-end reversal prevention using measured routing length
or keyed enclosure/shroud geometry. Record this as modeled geometry only;
Task 2 must select and dimension the prevention concept; Task 6 closes its CAD
geometry. Actual delivered-harness wrong-connection attempts remain OPEN in
`evidence/physical.json`.

- [ ] **Step 4: Complete the ESP module pin audit**

For each exact module MPN, tabulate native USB pins, all used GPIOs, strapping
and reserved pins, reset/ROM/brownout defaults, pulls, ADC/drive capability,
flash, decoupling, RF keepout, footprint area, and safe boot states. Select
MINI only if it reduces installed volume and passes every Rev B safety signal.

Rev C defaults to retaining exact WROOM `C2913198` fully inside the outline.
MINI migration is forbidden unless the repository contains a production
ESP-IDF application, exact target/sdkconfig, successful build artifacts,
GPIO/package audit, ROM-download/reset/brownout boot logs, flash/boot results,
and the complete Rev B firmware safety matrix bound to the exact module MPN.
Run:

```bash
test -f hardware/Esp32Tap/firmware/CMakeLists.txt
idf.py -C hardware/Esp32Tap/firmware set-target esp32s3
idf.py -C hardware/Esp32Tap/firmware build
python3 -m pytest -q hardware/Esp32Tap/tests/test_firmware_safety_model.py
```

If any command, production source, hardware flash/boot record, or safety row is
absent, mark MINI `REJECTED_UNQUALIFIED` and retain WROOM. The host-only
`safety_model.py` is not production firmware evidence.

- [ ] **Step 5: Validate the selection evidence**

Run:

```bash
python3 hardware/Esp32Tap/harness/validate_harnesses.py
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_harnesses.py \
  hardware/Esp32Tap/tests/test_jlc_stock.py
```

Expected: all selection and evidence-schema tests pass.

- [ ] **Step 6: Commit exact selections**

```bash
git add hardware/Esp32Tap/harness hardware/Esp32Tap/bom \
  hardware/Esp32Tap/tests/test_harnesses.py \
  hardware/Esp32Tap/tests/test_jlc_stock.py
git commit -m "hardware: select Rev C compact interconnects"
```

### Task 3: Migrate the generated schematic and BOM to Rev C

**Files:**

- Modify: `hardware/Esp32Tap/tools/design.py`
- Modify: `hardware/Esp32Tap/tools/gen_sch.py`
- Modify: `hardware/Esp32Tap/tools/gen_docs.py`
- Modify: `hardware/Esp32Tap/tests/test_design.py`
- Modify: `hardware/Esp32Tap/tests/test_schematic_docs.py`
- Regenerate: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch`
- Regenerate: `hardware/Esp32Tap/kicad/esp32tap.kicad_sym`
- Regenerate: `hardware/Esp32Tap/bom/BOM.csv`
- Regenerate: `hardware/Esp32Tap/NETLIST.md`

- [ ] **Step 1: Write and run failing Rev C design tests**

Add:

```python
def test_every_populated_part_is_automated_smt(design):
    populated = {
        ref: part for ref, part in design.COMPONENTS.items()
        if ref not in design.DNP_REFS
    }
    assert all(part[4] in {"Basic", "Extended"} for part in populated.values())
    assert all("THT" not in part[4] for part in populated.values())


def test_console_and_motor_connectors_are_physically_distinct(design):
    assert design.COMPONENTS["J1"][2] != design.COMPONENTS["J2"][2]
    assert design.COMPONENTS["J1"][3] != design.COMPONENTS["J2"][3]
```

Also lock the selected WROOM decision, switch MPNs, all eight harness pins,
direct power/ground paths, and existing safety nets.

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_design.py
```

Expected: FAIL specifically on Rev B THT connectors and old switch selection.

- [ ] **Step 2: Update exact component locks**

Replace J1/J2, SW1/SW2, and U1 only with the Task 2 selections. Preserve all
eight treadmill conductors, both parallel +8 V paths, both grounds, CONS6/
MOT6 differentiation, the fused local branch, and every relay/TX safety net.
Keep all footprint, JLC code, class, and pin maps immutable in validation.

- [ ] **Step 3: Regenerate the schematic and documents**

Run:

```bash
make -C hardware/Esp32Tap generate
```

Expected: generator exits zero and emits Rev C titles and exact parts.

- [ ] **Step 4: Run schematic and design tests**

Run:

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_design.py \
  hardware/Esp32Tap/tests/test_schematic_docs.py
python3 hardware/Esp32Tap/tools/repro_check.py --write-report erc
```

Expected: tests pass and ERC reports zero unexplained violations.

- [ ] **Step 5: Commit the green schematic migration**

```bash
git add hardware/Esp32Tap/tools hardware/Esp32Tap/tests \
  hardware/Esp32Tap/kicad hardware/Esp32Tap/bom/BOM.csv \
  hardware/Esp32Tap/NETLIST.md
git commit -m "hardware: migrate Esp32Tap schematic to Rev C"
```

### Task 4: Relayout the compact four-layer PCB

**Files:**

- Modify: `hardware/Esp32Tap/tools/gen_pcb.py`
- Modify: `hardware/Esp32Tap/tools/inspect_kicad.py`
- Modify: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_dru`
- Modify: `hardware/Esp32Tap/tests/test_generated_artifacts.py`
- Regenerate: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_pcb`
- Regenerate: `hardware/Esp32Tap/kicad/drc.rpt`

- [ ] **Step 1: Write and run failing Rev C board tests**

Require a Rev C title, the exact selected outline and area below Rev B's
5,500 mm², U1 body and antenna fully inside `Edge.Cuts`, complete manufacturer
keepout, fixed `JLC04161H-7628` stack, distinct J1/J2 footprints, and no
through-hole pads on populated components.

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_generated_artifacts.py \
  -k 'rev_c or outline or antenna or stackup or footprint'
```

Expected: FAIL on Rev B outline, RJ45 footprints, title, and antenna overhang.

- [ ] **Step 2: Lock candidate comparison and target outline**

Record direct SMT RJ45 and selected harness-connector layouts, including PCB
and installed bend volume. Choose the smallest outline that retains antenna,
assembly, hot-loop, USB, relay, test-point, latch, and enclosure clearance.
Add exact outline and placement assertions before routing.

- [ ] **Step 3: Place safety-critical blocks**

Place input protection, TPS54202 hot loop/feedback, relay/LDO/TVS, `TREAD_OK`
gates, USB-C/ESD/pair, fully contained ESP module, connectors, switches,
fiducials, and test points. Preserve the 15 mm axial antenna air/plastic
clearance contract without board overhang.

- [ ] **Step 4: Route power and safety nets explicitly**

Route both +8 V and ground pass-through paths on the 2.0 A conservative
single-contact basis, including every individual open-contact case. Keep the
local fuse branch separate. Preserve direct relay NC bypass, feedback
separation, short TVS returns, and converter geometry.

- [ ] **Step 5: Route USB on the locked four-layer stack**

Retain `JLC04161H-7628`, 1 oz outer/0.5 oz inner copper, In1 solid ground,
90 Ω geometry, zero signal vias, short connector breakouts, and no antenna
keepout violation.

- [ ] **Step 6: Regenerate and iterate DRC**

Run:

```bash
/usr/bin/python3 hardware/Esp32Tap/tools/gen_pcb.py
python3 hardware/Esp32Tap/tools/repro_check.py --write-report drc
python3 -m pytest -q hardware/Esp32Tap/tests/test_generated_artifacts.py
```

Expected: DRC and connectivity zero; all Rev C geometry, stack, copper,
antenna, and deterministic-generation tests pass.

- [ ] **Step 7: Inspect top/bottom renders and commit green**

Verify connector latch access, pin 1, polarized parts, module keepout, USB,
silkscreen Console/Motor identity, and no body collisions.

```bash
git add hardware/Esp32Tap/tools hardware/Esp32Tap/tests \
  hardware/Esp32Tap/kicad
git commit -m "hardware: lay out compact Esp32Tap Rev C PCB"
```

### Task 5: Model harness voltage-drop and degraded-contact corners

**Files:**

- Create: `hardware/Esp32Tap/sim/decks/harness_supply_drop.cir`
- Modify: `hardware/Esp32Tap/sim/assertions.json`
- Modify: `hardware/Esp32Tap/sim/README.md`
- Modify: `hardware/Esp32Tap/tests/test_sim_runner.py`
- Create: `hardware/Esp32Tap/harness/electrical_limits.json`
- Modify: `hardware/Esp32Tap/harness/validate_harnesses.py`

- [ ] **Step 1: Add failing simulation-manifest tests**

Require numeric assertions supported by the predecessor basis for 2.0 A normal
imbalance, each individual open new-board-connector +8 V contact, each
individual open new-board-connector ground contact, and doubled contact
resistance. Keep any unsupported RJ45 single-open 2.0 A case, minimum VIN,
source impedance, ambient/thermal behavior, transient response, complete
installed-path drop, and USB return current as `UNSUPPORTED` until physical
inputs exist.

- [ ] **Step 2: Implement the ngspice deck**

Model both production harnesses, four RJ45 terminations, both board connectors,
PCB copper/vias, unequal parallel contacts, source impedance, local load, and
USB ground path. If measured physical evidence exists, bind its current,
transient, source and ambient values. Otherwise use the explicit 2.0 A
conservative verification envelope bound to the predecessor design and owner
authorization, while leaving source/ambient/USB-ground physical fields open.
Mark thermal, ESD, RF, and switching-loop phenomena `UNSUPPORTED` rather than
converting them into modeled passes.

- [ ] **Step 3: Run host and pinned Docker simulations**

Run:

```bash
python3 hardware/Esp32Tap/sim/run_simulations.py \
  --host-ngspice /usr/bin/ngspice \
  --docker-image ngspice-cached:latest
```

Expected: every predecessor-supported numeric assertion passes identically
across three host and three Docker repetitions. The manifest explicitly
reports physical-dependent outcomes as `UNSUPPORTED`; it does not manufacture
default source, ambient, transient, thermal, or USB-ground values.

- [ ] **Step 4: Validate electrical-limit calculations and commit**

```bash
python3 hardware/Esp32Tap/harness/validate_harnesses.py
python3 -m pytest -q hardware/Esp32Tap/tests/test_sim_runner.py \
  hardware/Esp32Tap/tests/test_harnesses.py
git add hardware/Esp32Tap/sim hardware/Esp32Tap/harness \
  hardware/Esp32Tap/tests
git commit -m "test: model Rev C harness electrical corners"
```

### Task 6: Regenerate the compact enclosure

**Files:**

- Modify: `hardware/Esp32Tap/enclosure/esp32tap_case.scad`
- Modify: `hardware/Esp32Tap/enclosure/validate_enclosure.py`
- Modify: `hardware/Esp32Tap/enclosure/DIMENSIONS.md`
- Modify: `hardware/Esp32Tap/tests/test_enclosure.py`
- Regenerate: `hardware/Esp32Tap/enclosure/esp32tap_base.stl`
- Regenerate: `hardware/Esp32Tap/enclosure/esp32tap_lid.stl`

- [ ] **Step 1: Write failing Rev C enclosure tests**

Require board-derived compact dimensions, complete internal fit, 15 mm antenna
void, USB/switch access, distinct harness apertures, connector latch clearance,
production cable bend radii, supplied fasteners/tool-less closure, strain
relief, and geometric wrong-harness rejection. These tests establish CAD fit,
not delivered-harness or installed physical evidence.

- [ ] **Step 2: Regenerate enclosure source and meshes**

Derive all connector, board, mounting, and antenna geometry from the inspection
report. Preserve positive post overlap, printable walls, and manifold meshes.

- [ ] **Step 3: Run deterministic fit and mesh checks**

Run:

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_enclosure.py
python3 hardware/Esp32Tap/enclosure/validate_enclosure.py
```

Expected: all functional probes pass; base and lid are watertight,
winding-consistent single volumes and match pinned geometry hashes.
Update only `evidence/model.json`; leave actual enclosure fit, strain relief,
wrong-mating attempts, and installed bend clearance OPEN in
`evidence/physical.json`.

- [ ] **Step 4: Commit enclosure**

```bash
git add hardware/Esp32Tap/enclosure hardware/Esp32Tap/tests/test_enclosure.py
git commit -m "hardware: fit compact keyed Rev C enclosure"
```

### Task 7: Regenerate and bind the manufacturing package

**Files:**

- Modify: `hardware/Esp32Tap/tools/export_fab.py`
- Modify: `hardware/Esp32Tap/tools/validate_artifacts.py`
- Modify: `hardware/Esp32Tap/tools/repro_check.py`
- Modify: `hardware/Esp32Tap/tests/test_fab_export.py`
- Modify: `hardware/Esp32Tap/tests/test_repro_check.py`
- Regenerate: `hardware/Esp32Tap/bom/CPL-positions.csv`
- Regenerate: `hardware/Esp32Tap/kicad/gerbers/*`
- Regenerate: `hardware/Esp32Tap/kicad/Esp32Tap-gerbers.zip`

- [ ] **Step 1: Replace Rev B identity gates with Rev C gates**

Require exact archive membership, normalized timestamps, four copper layers,
named stack metadata, all-SMT BOM/CPL parity, no unselected/DNP leakage, exact
harness connector footprints, and fully on-board antenna geometry.

- [ ] **Step 2: Regenerate and audit artifacts**

Run:

```bash
make -C hardware/Esp32Tap generate
make -C hardware/Esp32Tap fab
python3 hardware/Esp32Tap/tools/validate_artifacts.py
python3 hardware/Esp32Tap/tools/repro_check.py
```

Expected: deterministic PCB, schematic, BOM, CPL, Gerbers, ZIP, ERC, and DRC.

- [ ] **Step 3: Commit the exact package**

```bash
git add hardware/Esp32Tap
git commit -m "hardware: publish Esp32Tap Rev C fabrication package"
```

### Task 8: Update validation, ordering, and handoff documents

**Files:**

- Modify: `hardware/Esp32Tap/README.md`
- Modify: `hardware/Esp32Tap/ORDERING.md`
- Modify: `hardware/Esp32Tap/ORDER-READY.md`
- Modify: `hardware/Esp32Tap/VALIDATION.md`
- Modify: `hardware/Esp32Tap/REPORT.md`
- Modify: `hardware/Esp32Tap/AI-HANDOFF.md`
- Modify: `hardware/Esp32Tap/WORKS-AND-FITS.md`
- Create: `hardware/Esp32Tap/REV-C-COMPACTION.md`

- [ ] **Step 1: Document evidence without overstating it**

Record exact hashes, dimensions, module/connector decisions, all-SMT status,
harness scope, ngspice outcomes, enclosure fit, and remaining installed
current/thermal/RF/USB/treadmill physical gates. Preserve `HOLD — READY FOR
VENDOR AND BENCH GATES` and Proxy-only first contact.

- [ ] **Step 2: Run document consistency tests**

```bash
python3 -m pytest -q \
  hardware/Esp32Tap/tests/test_document_status.py \
  hardware/Esp32Tap/tests/test_schematic_docs.py
git diff --check
```

Expected: documentation tests and whitespace check pass.

- [ ] **Step 3: Commit documentation**

```bash
git add hardware/Esp32Tap/*.md
git commit -m "docs: hand off Esp32Tap Rev C validation"
```

### Task 9: Run the complete repository validation and independent review

**Files:**

- Modify only files implicated by fresh failures.

- [ ] **Step 1: Run clean and full quality gates**

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
```

Expected: all pytest, ERC, DRC, reproducibility, ngspice, enclosure, fab audit,
and stock gates pass.

- [ ] **Step 2: Independently review electrical and manufacturing deltas**

Review exact diffs and regenerated artifacts for pin maps, NC bypass,
TREAD_OK/TX gates, power-only-from-treadmill, USB isolation limitations,
connector current paths, antenna keepout, part orientation, CPL rotation,
stack, Gerbers, and harness reversal prevention.

- [ ] **Step 3: Fix and rerun until approved**

Every correction receives a focused failing test when mechanically possible.
Rerun the full Task 9 command after the last change.

- [ ] **Step 4: Commit verified corrections**

```bash
git add hardware/Esp32Tap
git commit -m "fix: close Esp32Tap Rev C release review"
```

### Task 10: Obtain a no-purchase consolidated cost status

**Files:**

- Create: `hardware/Esp32Tap/vendor/JLC-PCBA-REV-C-QUOTE.json`
- Create: `hardware/Esp32Tap/vendor/HARNESS-REV-C-QUOTE.json`
- Create: `hardware/Esp32Tap/vendor/ENCLOSURE-REV-C-QUOTE.json`
- Create: `hardware/Esp32Tap/vendor/REV-C-COST-STATUS.json`
- Create: `hardware/Esp32Tap/vendor/REV-C-DELIVERED-COST.md`
- Create: `hardware/Esp32Tap/tools/validate_vendor_evidence.py`
- Create: `hardware/Esp32Tap/tests/test_vendor_evidence.py`
- Modify: `hardware/Esp32Tap/ORDER-READY.md`

- [ ] **Step 1: Write and run failing quote-evidence tests**

Require each JSON record to contain supplier identity, quote date/expiry,
currency, exact artifact/spec SHA-256, quantities, every required operation
and line item, setup/tooling/NRE, unit/subtotal, shipping, tax, total,
acceptance status, sanitization metadata, and source-screen/archive hash.
Require exactly five PCBs, two PCBAs, four harnesses, and two enclosures.
Require JLC cart count zero plus `order_created=false`,
`checkout_started=false`, and `payment_authorized=false`.

```bash
python3 -m pytest -q hardware/Esp32Tap/tests/test_vendor_evidence.py
```

Expected: FAIL because the structured quote records do not exist.

- [ ] **Step 2: Implement the strict quote validator**

`validate_vendor_evidence.py` rejects unknown fields, arithmetic mismatch,
expired/non-firm supplier quotes, missing operations, unhashed evidence,
unsanitized account data, wrong quantities, or a purchase-state flag.
It emits `INCOMPLETE_COST` whenever any required item, vendor acceptance,
shipping, or tax is unavailable. It permits `TURNKEY_QUOTED` only when all
four structured records are complete and mutually hash-bound **and**
`physical.json` is literally `MEASURED` with every required installed test
closed. Under conservative predecessor authorization, a fully priced
no-purchase quote still uses a non-turnkey verification status.
Add a document-status test that rejects “turnkey complete,” “complete delivered
cost,” or `TURNKEY_QUOTED` in `ORDER-READY.md` and
`REV-C-DELIVERED-COST.md` whenever `REV-C-COST-STATUS.json` is
`INCOMPLETE_COST`.

- [ ] **Step 3: Refresh official part evidence**

```bash
python3 hardware/Esp32Tap/tools/check_jlc_stock.py --refresh
make -C hardware/Esp32Tap check
```

Expected: exact current catalog identities and stock evidence pass.

- [ ] **Step 4: Upload the exact archive, BOM, and CPL**

Use the authenticated JLCPCB workflow with five PCBs and two fully assembled
top-side Standard PCBAs. Lock four layers, `JLC04161H-7628`, 1.6 mm, 1 oz
outer/0.5 oz inner, ENIG, green/white, controlled impedance, production-file
and placement confirmation, no substitutions, no vendor realignment, and no
unsolicited via filling.

- [ ] **Step 5: Prove every populated designator is placed**

Archive the sanitized review state showing exact MPN/JLC code, quantity, side,
rotation, placement charge, and selected status for every populated part.
Obtain vendor acceptance for the exact side-entry connectors, edge clearance,
panelization, and depanelization. Stop on any unselected, customer-supplied,
manual, wave, fixture, or substituted part.

- [ ] **Step 6: Obtain separate firm harness and enclosure quotes**

Populate the harness and enclosure JSON files only from exact supplier quote
or checkout-review evidence for four finished, tested harnesses and two
finished enclosures. Record tooling/NRE, test, strain-relief/material/finish,
lead time, shipping, tax, and quote expiry. Do not substitute catalog part
prices for a finished assembly quote.

- [ ] **Step 7: Build the consolidated delivered-cost package**

Record the archive-bound JLC PCB/PCBA subtotal plus firm four-harness and
two-enclosure costs, tooling/NRE, shipping, and tax. Label any checkout-only
shipping/tax as unknown rather than estimating and force status
`INCOMPLETE_COST`. Do not press Save to Cart, submit, checkout, or pay.

- [ ] **Step 8: Verify account state and structured evidence**

Confirm project saved, cart count zero, only the final save/cart action remains,
and no order/payment exists. Remove temporary authenticated browser profiles.

```bash
python3 hardware/Esp32Tap/tools/validate_vendor_evidence.py
python3 -m pytest -q hardware/Esp32Tap/tests/test_vendor_evidence.py
make -C hardware/Esp32Tap check
```

Expected: evidence tests pass. Status is `TURNKEY_QUOTED` only if all delivered
costs are firm and physical evidence is `MEASURED`; a firm conservative
verification quote uses `VERIFICATION_QUOTED`. Missing costs remain explicitly
`INCOMPLETE_COST` and HOLD.

- [ ] **Step 9: Commit sanitized evidence**

```bash
git add hardware/Esp32Tap/vendor hardware/Esp32Tap/ORDER-READY.md \
  hardware/Esp32Tap/bom/JLC-STOCK-SNAPSHOT.json \
  hardware/Esp32Tap/tools/validate_vendor_evidence.py \
  hardware/Esp32Tap/tests/test_vendor_evidence.py
git commit -m "docs: record Esp32Tap Rev C quote status"
```

### Task 11: Close the implementation session

**Files:**

- Update Beads issue `precor-9_3x-1dj`

- [ ] **Step 1: Re-run completion verification**

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
git diff --check
git status --short
```

Expected: all gates pass; only explicitly preserved unrelated user changes
remain.

- [ ] **Step 2: Update issue status honestly**

Close `precor-9_3x-1dj` only if repository, vendor-placement, harness
procurement, delivered-harness wrong-mating tests, enclosure fit, installed
current/thermal/RF/USB-ground tests, treadmill staging, and complete quote
acceptance criteria are satisfied. Actual results must be present in
`evidence/physical.json`; modeled or vendor evidence cannot substitute.
Otherwise record exact remaining external/physical gates, keep the issue open,
and do not call the board turnkey-complete or treadmill-ready.

- [ ] **Step 3: Push issue and code state**

```bash
bd dolt push
git fetch origin main
git rebase origin/main
git push
git status --short --branch
```

Expected: local branch is up to date with origin; unrelated user changes are
untouched.
