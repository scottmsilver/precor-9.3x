# Esp32Tap — Validation Run

> **Round 2 (2026-07-23) — board-level sims added + F1 fix applied + final order gate.**
> See [§6 Round 2](#6-round-2--post-fix-board-level-revalidation--order-gate) at the
> bottom. Bottom line: the round-1 blocking change (**R11 1 k → 330 R**, F1) is applied
> and re-validated; a second SPICE round now covers board-level power/signal/relay
> behavior; an adversarial final gate independently re-ran ERC, DRC, the full 31-part
> live stock check, and a gerber content-compare. **Verdict: GO to order** — see
> `ORDER-READY.md`. Round-1 content below is preserved as-is.

---

Machine-only validation pass over the full Esp32Tap design: analog behavior
(ngspice), PCB fabrication data (KiCad 10 CLI + independent scripts), parts
catalog (live JLCPCB queries), firmware feasibility (host tests + real-capture
replay + ESP-IDF build + QEMU boot), and enclosure geometry (pcbnew
introspection). Five parallel tracks, each independently audited afterward;
the audit found **zero credibility problems** with any substantive result
(two tracks had cosmetic prose miscounts in their own summaries, corrected
below).

Validation artifacts (sim decks, logs, check scripts, diffs) live in the
session scratchpad under `validation/{spice,pcb,catalog,firmware,enclosure}/`;
file names are cited per row so any check can be re-run.

---

## 1. Scoreboard

| Track | What actually ran | Verdict | Key artifacts |
|---|---|---|---|
| **SPICE / analog** | ngspice-42 (Docker, alpine 3.20): 7 sim decks — dead-board back-feed DC+tran, TPS54202 EN divider DC sweep 2–10 V, UART rise-time tran, S8050 relay driver tran (both coil variants), LED currents + LED fix; independent Python analytic cross-check (matches SPICE to 3–4 sig figs); LED1 Vf confirmed against the LCSC C965804 datasheet | **4 / 5 PASS** — LED1 fails the 1–8 mA visibility criterion (fix simulated and verified) | `sim1*–sim5b*.cir/.log`, `crosscheck.py/.log` |
| **PCB / fab data** | Fresh `kicad-cli` ERC + DRC + schematic-parity DRC; netlist exported both formats and diffed three-way (NETLIST.md ↔ schematic ↔ PCB pad-nets, all 35 nets / 149 pads / 28 NC pads); BOM↔CPL↔PCB↔NETLIST cross-check (46/46 designators, one consistent JLC origin transform, ≤0.01 mm); shapely courtyard overlap + outline containment; all 11 fab files regenerated and byte-diffed vs checked-in gerbers + zip | **PASS, clean sweep** — 0 ERC, 0 DRC, exact netlist parity, gerbers fully current (only timestamp lines differ) | `erc.rpt`, `drc.rpt`, `drc-parity.rpt`, `netlist-check.log`, `bom-cpl-check.log`, `geometry-check.log`, `gerber-diff-summary.log` |
| **Catalog / parts** | Live `jlc search --json` for all 31 unique LCSC parts; independent CDFER basic/preferred mirror CSV downloaded and matched; Octopart/Nexar, DigiKey, Mouser live-probed (all auth-walled — no anonymous second source exists) | **PASS** — 0 not-found, every part ≥3× BOM-qty in stock (worst: C371166, 7,758 vs 3 needed), all class labels match live library_type, all 18 unique Basic parts corroborated basic=1 by the independent mirror | `catalog_report.md`, `parsed.json`, `cdfer-basic-preferred.csv`, `probe.txt` |
| **Firmware feasibility** | Clean rebuild + run of all 9 C++ host test binaries; decoded all 5 logic-analyzer captures (9 channels, 71,883 bytes, ≥99.9 % good stop bits); production `kv_parse` replayed over every real stream in 64-byte chunks; ESP-IDF (espressif/idf Docker) esp32s3 build of `kv_protocol.cpp` + `ipc_protocol.cpp` + `mode_state.cpp` **verbatim**; booted in bundled qemu-system-xtensa | **PASS** — 132/132 tests (684 assertions); 8,816 KV pairs from real bus data, 0 desyncs, 675/675 hmph decodes; clean 208 KB esp32s3 image; stable 90 s QEMU boot with self-test parsing real bus bytes and emulate-entry zeroing confirmed | `host_tests.log`, `decode_all.log`, `replay_results.log`, `idf_build2.log`, `qemu_boot2.log` |
| **Enclosure geometry** | pcbnew Python bindings (LoadBoard) + custom s-expr parser over the real `.kicad_pcb`: board outline, thickness, all mount holes, LEDs, switches, connector F.Fab body boxes vs `DIMENSIONS.md` and `esp32tap_case.scad` parameters; derived cavity/shell dims recomputed from scad source | **PASS with 2 doc-drift + 2 borderline findings** (all documentation-level; no physical clearance violation) | `pcbnew_dump*.py/.json`, `parse_pcb.py`, `fp_data.json` |

Post-audit note: the catalog track's own prose said "20 Basic parts / 31 BOM
rows" — actual is 18 unique Basic parts and 33 rows (31 unique LCSC parts;
the two duplicate pairs are intentional). The substantive checks were re-run
and hold. The firmware track's "340 reboots" is 339 in the log, and the
quoted binary size matches the build log (the on-disk file was regenerated
96 B smaller during the QEMU run). Neither affects any conclusion.

---

## 2. Real failures, with concrete fixes

### F1 — LED1 status LED too dim as drawn (medium) — **design change, flagged, NOT applied**

`XL-1608UGC-04` (C965804) is an **InGaN emerald** LED, Vf 2.7–3.3 V per the
LCSC datasheet — not the ~2 V GaP part a 1 k resistor assumes. From a 3.3 V
GPIO through R11 = 1 k, ngspice gives **0.60 mA typical / 0.43 mA at
worst-case Vf** — below the 1–8 mA visibility band; the status LED may be
invisible in room light.

**Fix (simulated and verified): R11 1 k → 330 R** → 1.51 mA typ / 1.04 mA
worst-case, inside the band. Requires edits to `tools/design.py` (R11 value
+ LCSC part, C21190 → a 330 R Basic part, e.g. C23138), then regenerating
schematic/PCB/NETLIST.md/BOM/CPL/gerbers. This changes the board, so it was
**not** applied in this docs-only pass — do it before ordering.

Secondary (optional): LED2 (red rail LED, R12 = 2 k) runs 0.78 mA —
functional but dim. If the power indicator should be clearly visible, take
R12 2 k → 1 k in the same regeneration.

### F2 — ESP32 port stack-size constraint (medium) — **documented in `firmware/PLAN.md`**

`KvPair` is 128 bytes; two on-stack `KvPair[16]` arrays (2 KB each) overflow
ESP-IDF's default 3.5 KB main-task stack — observed as a hard crash-loop
(~340 consecutive stack-overflow reboots) in QEMU until the buffers were
made static. Latent bricking bug for a naive port. **Fix applied**: task
stack sizing requirement added to the PLAN.md sdkconfig section (static/heap
parser buffers, or `CONFIG_ESP_MAIN_TASK_STACK_SIZE` / `xTaskCreate` depth
sized for them).

### F3 — Enclosure doc drift (low) — **fixed in `enclosure/DIMENSIONS.md`**

- Antenna thinned-lid span: doc said "X 51–73", scad uses 52–72 (both clear
  the real module at X 52.95–71.05). Doc reconciled to the scad numbers.
- Lid screw posts: doc said "(3.5, 3.5) from each outer corner"; actual scad
  geometry is wall+3.5 = (5.7, 5.7) from the true exterior corner. Doc
  reworded to "in from each interior cavity corner" with both numbers.

### F4 — RJ45 jacks not literally flush/proud (low) — **documented in `enclosure/DIMENSIONS.md`**

J1/J2 bodies protrude **1.53 mm** past the board edge (F.Fab-measured), not
the ~2.0 mm the wall stack assumed, so the mating face sits ~0.5 mm inboard
of the interior wall face and ~2.7 mm behind the exterior face. An 8P8C plug
body is far longer than 2.7 mm and the 17.7 × 14.4 mm aperture passes it, so
**insertion works** — but the "flush-or-proud" intent is not literally met.
Doc corrected to the measured numbers. Optional physical tweak if desired:
thin the RJ45 wall pocket ~0.5 mm in the scad before printing.

Non-failures worth knowing: the initial ESP-IDF build break was in the
vendored rapidjson (too old for GCC ≥ 14; fixed with the known upstream
one-liner — not project code), and MH1's courtyard circle extends 0.03 mm²
past the board edge (hole edge 1.55 mm inside; cosmetic, DRC-clean).

---

## 3. Not validated on this machine (and what it would take)

| Item | Why not | What it takes |
|---|---|---|
| OpenSCAD render / STL manifold check | `openscad` not installed; geometry verified arithmetically only | `apt install openscad` (or Docker), render base+lid, check for CGAL errors — 10 min, do before paying for a print |
| Real RS-485 electrical behavior (drive strength, reflections, actual dead-board back-feed) | SPICE models the design, not the physical treadmill bus | Hardware bench: assembled board + scope on a live Precor bus; verify the 4.7 k tap injects <1 mA with the board unpowered |
| TPS54202 buck regulation, ripple, thermal | Only the EN divider was simulated; SMPS switching behavior needs the real part | Bench: load-step 3V3 at ≥600 mA, scope ripple, thermal camera |
| Relay switching on the real console line | Driver sim passes; contact behavior/timing is physical | Bench: verify NO-path isolation dead and closed-path timing on real pin 6 |
| BLE antenna performance in the enclosure near the motor hood | RF is un-simulatable here | Site-survey RSSI as already called out in `firmware/PLAN.md` |
| JLCPCB order acceptance (DFM, final part classing) | Their DFM engine and Basic/Extended flips are order-time | Upload the gerber zip + BOM + CPL to a real (unpaid) order and review DFM output |
| Full firmware (UART timing, WDT-releases-relay matrix, dual-radio coexistence) | Only the parser/mode-state PoC was built and booted | The M1–M3 plan in `firmware/PLAN.md`, on a real ESP32-S3 devkit with two UARTs looped |

## 4. Confidence statement

**We now KNOW (machine-verified, reproducible):**

- The fab package is internally consistent end-to-end: schematic, PCB,
  NETLIST.md, BOM, CPL, and gerbers all agree exactly, with 0 ERC/DRC
  issues, and the checked-in gerbers are byte-current with the layout.
- Every BOM part exists, is correctly classed, and is in stock ≥3× at JLC
  as of this run (stock is perishable — recheck at order time).
- The circuit-level safety story holds in simulation *and* an independent
  analytic cross-check: dead-board back-feed 0.57 mA (<1 mA), EN divider
  safe to 9 V with 4.1 V abs-max margin, UART edges 3 orders of magnitude
  faster than a bit, relay driver ≥5.8× overdrive with flyback clamped.
- The existing C++ protocol/mode-state code compiles **verbatim** for
  esp32s3 and correctly parses real captured treadmill bus traffic (8,816
  KV pairs, zero desyncs) — the core porting bet is de-risked.
- The board physically fits its enclosure math: outline, holes, LEDs,
  switches, USB-C all match the scad to ≤0.2 mm.

**We still only BELIEVE (needs hardware or a service to confirm):**

- The physical bus tolerates the tap (SPICE ≠ a 20-year-old treadmill's
  actual line impedance).
- The buck converter's dynamic behavior, the relay's real-world switching,
  and BLE range from inside the case near the motor.
- The scad renders to a manifold STL and JLCPCB accepts the order without
  DFM pushback.
- The full firmware (beyond the parser PoC) meets the PLAN.md timing and
  watchdog matrix.

**Bottom line:** one real design change is required before ordering
(**R11 1 k → 330 R**, F1); everything else found was documentation drift,
now fixed in-tree, or hardware-bench work that no machine-only pass can
close.

---

## 5. Changes applied in this pass (uncommitted, docs only)

1. `enclosure/DIMENSIONS.md` — antenna span reconciled to scad (X 52–72);
   lid-screw position wording corrected (interior-corner reference, both
   numbers); RJ45 proud/recess claim corrected to F.Fab-measured values.
2. `firmware/PLAN.md` — added the QEMU-validated task-stack-sizing
   constraint for KvPair buffers.
3. This file (`VALIDATION.md`).

**Flagged, not applied (design changes):** R11 1 k → 330 R (required);
R12 2 k → 1 k (optional). Both go through `tools/design.py` + full
regeneration of the fab package.

---

## 6. Round 2 — post-fix board-level revalidation + order gate

**Date:** 2026-07-23. **What changed since round 1:** the two flagged LED-resistor
edits were applied in `tools/design.py` (the single source of truth) and the entire
fab chain was regenerated:

- **R11 1 k → 330 R** (C21190 → **C23138**, Basic): status-LED current 0.60 mA → **1.51 mA
  typ / 1.04 mA worst-case Vf**, now inside the 1–8 mA visibility band (closes F1).
- **R12 2 k → 1 k** (→ **C21190**, reuses the R9 line, Basic): power LED 0.78 mA → clearly
  visible (closes the optional secondary of F1). Parts-cost delta of both edits = **$0.00**.

Source-of-truth reconciliation folded prior "live-validated" values back into
`design.py` (J3/U3/L1/LED1 Basic→Extended, LED1/D5–D7 cost + description) so
regeneration no longer regresses the ordering data; the only *semantic* BOM change vs
the prior HEAD is the intended R11/R12 regroup. `tools/gen_sch.py`'s hard-coded worktree
output path was replaced with a `__file__`-relative path.

### 6.1 Round-2 tracks (all green)

| Track | What ran (round 2) | Verdict |
|---|---|---|
| **SPICE — power** | ngspice board-level: inrush, EN-divider ripple, 3V3 step-load droop, VIN ORing | PASS — droop 27.8 mV; back-feed <1 mA; EN within abs-max |
| **SPICE — signal** | console→ESP RX, ESP TX→motor, bypass-leakage attenuation, relay-switchover transient | PASS — UART edges ~65 ns vs 104 µs bit |
| **SPICE — relay** | S8050 base drive, flyback (with/without diode), step-load, brownout stuck-high | PASS — Vce(on) 68.6 mV, β_forced ≈19.5, flyback clamped |
| **PCB / fab** | fresh ERC + DRC; three-way netlist parity; BOM↔CPL↔PCB↔NETLIST; shapely geometry; 11-file gerber regen + byte-diff | PASS — 0 ERC, 0 DRC, 35/35 nets, gerbers current |
| **Catalog** | live `jlc search --json` on all 31 unique parts incl. new C23138; CDFER Basic/Preferred mirror cross-check | PASS — 0 flags, all ≥3× qty, 18/18 Basic corroborated |
| **Firmware** | 132/132 host tests; 8,816-pair real-capture replay (9 streams); ESP-IDF S3 build; QEMU boot | PASS |
| **Enclosure** | openscad (Docker) render of base+lid → 2-manifold STL; fit vs PCB | PASS — CGAL `Simple: yes`, all positions within tol |

### 6.2 Adversarial final gate (independent re-runs, not trusting track prose)

The order-readiness gate **re-ran the load-bearing checks itself** on
`hardware/Esp32Tap/kicad/` and `bom/BOM.csv`:

- `kicad-cli sch erc --exit-code-violations` → **exit 0** (0/0).
- `kicad-cli pcb drc --exit-code-violations` → **exit 0** (0 violations, 0 unconnected,
  0 footprint errors). *(The track's `drc.rpt` shows 135 `footprint_symbol_mismatch`
  lines — those come from a `--schematic-parity` run and are project-overridden warnings
  from the custom footprint library naming + missing LCSC symbol field; they are not
  fab-blocking and do not affect the exit code.)*
- Fresh `kicad-cli pcb export gerbers` + content-diff vs the committed
  `Esp32Tap-gerbers.zip` → **11/11 files byte-identical** except CreationDate lines; zip
  unpacks byte-identical to `kicad/gerbers/`.
- Upload-bundle lint: BOM headers JLC-native (`Comment/Designator/Footprint/LCSC Part #`),
  **no trailing total row**, **no DNP/TP/MH** parts; CPL headers
  `Designator/Mid X/Mid Y/Rotation/Layer`.
- **Live stock re-run on every unique C-number:** 31/31 parts, **0 flags**, all stock
  ≥3× needed qty (tightest: C2847314 RJ45 at 3,504 vs 6), all live `library_type` matches
  the BOM class, all first-result `id == query`. **13 unique Extended lines** confirmed
  live (drives the $30–39 loading-fee row — `ORDERING.md` is accurate; `REPORT.md`'s
  cost table still says 9 lines and is stale, flagged for a doc pass, not order-blocking).

### 6.3 Verdict

**GO to order.** Full click-by-click JLCPCB PCBA + JLC3DP order steps, the rechecked
cost table, and the machine-verified (14) vs bench-only (4) residual-risk split are in
[`ORDER-READY.md`](ORDER-READY.md). No machine-verifiable blocker remains; all residual
risk is bench-only (buck rail noise, BLE range, relay lifetime, motor byte-pacing) and
gated behind the `firmware/PLAN.md` treadmill-contact checklist after the boards arrive.

### 6.4 Round-2 changes applied (uncommitted)

1. `tools/design.py` — R11 330 R/C23138, R12 1 k/C21190 + source-of-truth reconciliation.
2. Regenerated: `kicad/*` (sch/pcb/sym), `NETLIST.md`, `bom/BOM.csv`, `bom/CPL-positions.csv`,
   `kicad/gerbers/*`, `kicad/Esp32Tap-gerbers.zip`.
3. `ORDERING.md` / `REPORT.md` — parts total $7.35 → $7.21 (R11/R12 cost-neutral).
4. `ORDER-READY.md` — new (GO verdict + order checklist + risk split).
5. This §6.
