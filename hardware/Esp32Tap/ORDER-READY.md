# Esp32Tap — ORDER-READY

## VERDICT: ✅ GO

The Esp32Tap fab package is order-ready. Round-2 validation (board-level SPICE +
PCB checks + firmware build + real-capture replay + enclosure render) passed every
machine-verifiable track, and this final gate **independently re-ran** the load-bearing
checks (ERC, DRC, full 31-part live stock, gerber content-compare) rather than trusting
the track prose. All green. The one blocking design change from round 1 (R11 status-LED
resistor 1 k → 330 R, finding F1) has been applied and re-validated.

**Nothing machine-verifiable blocks the order.** The residual risk is entirely
bench-only (rail noise, RF range, relay lifetime, motor byte-pacing tolerance) — none
of which any off-treadmill tool can close, and all of which are gated behind the
firmware/PLAN.md treadmill-contact checklist *after* the boards arrive.

Gate performed 2026-07-23 with `kicad-cli` 10.0.1 and the JLC CLI live catalog.

---

## 1. What you are ordering

- **5 bare PCBs**, 2 of them fully assembled (verification build — the $200 budget
  allows exactly one respin, so treat the first order as the verification build).
- **2 enclosure halves** (base + lid), resin, from JLC3DP.

---

## 2. JLCPCB PCB + PCBA order — click by click

Go to jlcpcb.com → **Order Now** (PCB), then add assembly.

**A. Upload gerbers**
1. Click **Add gerber file** and upload `hardware/Esp32Tap/kicad/Esp32Tap-gerbers.zip`.
   (11-file curated Protel set — verified this gate to be byte-identical to a fresh
   `kicad-cli pcb export`.) The preview should render a 100 × 55 mm 2-layer board.

**B. PCB options**
2. **Base Material:** FR-4. **Layers:** `2`.
3. **Dimensions:** auto-detected `100 mm × 55 mm` — confirm it reads that.
4. **PCB Qty:** `5`.
5. **Thickness:** `1.6 mm`. **Surface finish:** HASL (lead-free fine) or ENIG — HASL is fine.
6. **Via covering:** leave default. **Min via drill:** board uses **0.3 mm** (2-layer
   minimum; do not let a template push 0.2 mm — that is a 4-layer capability).
7. Leave remaining PCB options at default.

**C. Turn on assembly**
8. Toggle **PCB Assembly → ON**.
9. **Assembly Side:** `Top Side`. **PCBA Type:** `Economic`.
10. **PCBA Qty:** `2` (assemble **2 of 5**). Click **Confirm**.

**D. Upload BOM + CPL**
11. **Add BOM file:** upload `hardware/Esp32Tap/bom/BOM.csv`
    (headers `Comment, Designator, Footprint, LCSC Part #` — JLC-native; no trailing
    total row; test points TP1–TP4 and mount holes already excluded).
12. **Add CPL / placement file:** upload `hardware/Esp32Tap/bom/CPL-positions.csv`
    (headers `Designator, Mid X, Mid Y, Rotation, Layer`; origin = board bottom-left,
    +Y up, JLC convention).
13. Click **Process BOM & CPL**. Expect **0 "Do Not Place"** entries and every line to
    resolve to its part. **Re-quote each C-number here** — LCSC stock ≠ JLC assembly
    stock, and Basic/Extended can flip at order time (fee waived on any line that lands
    Basic/Preferred).

**E. Placement review**
14. In the 3D placement preview, eyeball rotations on **K1 (relay), J3 (USB-C), U1
    (module)** against the render — these are the three worth a second look. Nudge any
    obviously wrong rotation in the preview.

**F. THT hand-solder**
15. J1/J2 (RJ45) are THT and route through JLC's hand-solder service automatically —
    confirm they appear under the hand-soldered / THT section, not flagged as
    unplaceable. **Do not** substitute a magjack (magnetics break the DC-coupled serial).

16. Add to cart. Choose the economy shipping line. **Do not pay yet** if also ordering
    the enclosure — combine the shipment (step 4) to save $5–10.

---

## 3. JLC3DP enclosure order — click by click

1. Go to jlc3dp.com → **3D Printing**.
2. **Upload** both STLs:
   - `scratchpad/validation2/enclosure2/esp32tap_base.stl`
   - `scratchpad/validation2/enclosure2/esp32tap_lid.stl`

   ⚠️ **The committed `hardware/Esp32Tap/enclosure/` directory contains only the
   `.scad` source and `DIMENSIONS.md` — no STL.** Upload the rendered STLs from the
   validation run above, or re-render locally with `openscad -o base.stl
   enclosure/esp32tap_case.scad` (both were confirmed 2-manifold this gate: CGAL
   `Simple: yes`, base 2524 facets, lid 2140 facets).
3. **Material:** Resin (e.g. 9000 Resin / white). **Technology:** SLA.
4. **Qty:** `1` each (or 2 for a spare shell).
5. Confirm auto-detected bounding boxes: base ≈ 120.4 × 77.7 × 23.3 mm (incl. zip-tie
   ears), lid ≈ 108.4 × 77.7 × 3.8 mm.
6. Add to cart; **combine with the PCB shipment** to save on freight.

---

## 4. Cost table (sanity-rechecked this gate)

Parts/board recomputed from `bom/BOM.csv` = **$7.21** (matches ORDERING.md and REPORT.md).
Extended-fee count recomputed from the **live JLC catalog** = **13 unique Extended
lines** (see §5) — the figure in `ORDERING.md` is the accurate one.

| Line | Est. (USD) |
|---|---|
| 2-layer PCB 100×55, qty 5 | $4–8 |
| Economic PCBA setup | $8.00 |
| Stencil | $1.50 |
| SMT joints (~185 × 2 boards) | ~$0.65 |
| Parts, 2 × $7.21 | ~$14.42 |
| Extended-part loading fees ($3 × 13 live-Extended lines; some may flip Basic/Preferred → waived) | $30–39 |
| THT hand-solder service (RJ45 × 2) | $3.50 |
| THT joints (~20 × 2 boards) | ~$0.70 |
| **JLCPCB subtotal** | **~$64–76** |
| Shipping (economy, ~8–15 days) | $8–15 |
| Enclosure (JLC3DP resin, base + lid) | $6–14 |
| Combined-shipment saving | −$5–10 |
| **All-in total** | **~$77–107** |

Inside the $200 budget with a respin reserve, but the reserve is thinner than the
early estimate. **Doc note (not an order blocker):** `REPORT.md`'s cost table still
lists 9 Extended lines / $18–27 fees / ~$65–95 all-in — that is stale; the live catalog
says 13 lines. Follow the numbers above / in `ORDERING.md`. (`REPORT.md`'s component
table also still labels J3/U3/L1 "Basic" vs the BOM's "Extended" — same pre-existing
drift, flagged for a follow-up doc pass; it does not affect the fab data you upload.)

---

## 5. Residual-risk register

### 5a. Machine-verified — closed, reproducible (this gate re-ran the load-bearing ones)

| # | Item | Result | How verified this gate |
|---|---|---|---|
| 1 | Schematic ERC | **0 errors / 0 warnings** | `kicad-cli sch erc --exit-code-violations` re-run → exit 0 |
| 2 | Board DRC | **0 violations / 0 unconnected / 0 footprint errors** | `kicad-cli pcb drc --exit-code-violations` re-run → exit 0 |
| 3 | Netlist parity (NETLIST↔sch↔PCB) | **exact, 35/35 nets, 149 pads** | `verify-results.json` (11 passes / 0 fails) |
| 4 | Gerber bundle current | **11/11 files byte-identical** to fresh export | independent `kicad-cli pcb export gerbers` + content diff (only CreationDate lines differ); zip = dir |
| 5 | Upload bundle lint | BOM headers JLC-native, no total row, no DNP; CPL headers correct; zip has all copper/mask/paste/silk/edge/drill/job | direct header + `grep` DNP/TP/MH inspection |
| 6 | Live parts stock | **31/31 parts, 0 flags**, all ≥3× needed qty, classes + IDs match | re-ran `jlc search --json` on **every** unique C-number myself |
| 7 | Power SPICE margins | back-feed 0.57 mA (<1 mA); EN safe to 9 V; 3V3 droop 27.8 mV | ngspice measure output in `sim-power/*.log` |
| 8 | Relay-driver SPICE | Vce(on) 68.6 mV, Icoil 49.1 mA, β_forced ≈19.5 (deep saturation), flyback clamped | `sim-relay/sim1_basedrive.log` |
| 9 | Signal-integrity SPICE | UART rise 65 ns vs ~104 µs bit (3 orders faster) | `sim-signal/1_console_to_esp_rx.log` |
| 10 | Host protocol tests | **132/132 cases pass** (9 binaries) | `firmware2/host_tests.log` |
| 11 | Real-capture replay | **8,816 KV pairs across 9 streams, 0 desyncs** | `firmware2/replay_results.log` |
| 12 | ESP-IDF S3 build | clean `esp32tap_poc.bin` (0x32e30 ≈ 208 KB), build complete | `firmware2/idf_build.log` |
| 13 | QEMU S3 boot | self-test parsed 67 KV pairs from 512 real bus bytes; emulate-entry zeroed speed | `firmware2/qemu_boot_full.log` |
| 14 | Enclosure manifold render | both STLs 2-manifold (CGAL `Simple: yes`), fit vs PCB PASS | `enclosure2/fit_check.md` (openscad Docker) |

### 5b. Bench-only — CANNOT be simulated; gated behind firmware/PLAN.md before treadmill contact

| Item | Why un-simulatable | Gated by (firmware/PLAN.md) |
|---|---|---|
| Buck under real treadmill rail noise | SPICE modeled the EN divider + droop, not the SMPS switching against a 20-yr-old belt's noisy 8 V rail | Treadmill-contact gate step 4 (**+8 V rail sourcing capacity measured** per PiZeroHat WIRING-CHECKLIST) + README bench-8 V bring-up |
| RF / BLE range at install point | RF inside a metal motor hood is not machine-modelable | **M4** (NimBLE FTMS + HRM, 24 h coex soak) + "BLE RSSI inside the metal motor hood — site-survey" carried-forward unknown |
| Relay mechanical lifetime | Contact wear is physical, long-term | **M3** watchdog-matrix bench proof (relay release per cell) + **TC2** first real-console switching |
| Motor tolerance to UART byte-pacing | Real motor's reaction to emulate-TX FIFO pacing is physical | **TC2 — first treadmill Emulate** ("Confirms motor tolerance of UART-FIFO pacing on the real motor"), only after TC1 clean + M3 re-run on the flashed build |

**Machine-verified: 14 items. Bench-only: 4 items.**

---

## 6. On arrival

Do **not** connect a treadmill until the **treadmill-contact gate checklist** in
`firmware/PLAN.md` is fully green (M1–M3 on the bench rig, WDT-releases-relay proof,
signal-integrity-while-dead test, +8 V rail measurement). USB-only power first →
unpowered relay continuity test → bench 8 V → then, and only then, TC1 in-line proxy
observation. First treadmill Emulate (TC2) is a separate gate after TC1 is clean.
