# Esp32Tap — Works & Fits Check

**Final verification gate — 2026-07-23.** Every load-bearing check below was
*re-run fresh this session* against the live files (not trusted from prior
report prose). Tools: `kicad-cli` 10.0.1, host `g++` 12.4.0, `espressif/idf:latest`
(ESP-IDF 6.2 / xtensa GCC 16.1.0), the JLC live-catalog CLI, and
`openscad/openscad:latest`.

---

## Verdicts

| Question | Verdict |
|---|---|
| **WORKS** (electrical / firmware / parts) | **YES** — GO to order the PCB |
| **FITS — board dimension** | **YES** — delivered usable board = 100 × 55 mm |
| **FITS — enclosure ↔ board** | **NO (one blocker)** — RJ45 wall cutouts misplaced 8.695 mm |
| **FITS — treadmill install** | **PENDING MEASUREMENT** — no site dimensions in repo |

**Bottom line:** The board is order-ready today. The 3D-printed *enclosure* has a
single, fully-characterized blocker (two RJ45 apertures) that is free to fix now —
no STL has been printed, no PCB ordered. Physical install fit cannot be closed by
any tool; it needs three measurements at the treadmill (listed in §4).

---

## 1. DOES IT WORK — scoreboard (all re-run this session)

| # | Check | Method (fresh run) | Result |
|---|---|---|---|
| 1 | Schematic **ERC** | `kicad-cli sch erc --exit-code-violations` | **0 violations, exit 0** |
| 2 | Board **DRC** | `kicad-cli pcb drc --exit-code-violations` | **0 violations / 0 unconnected / 0 footprint errors, exit 0** |
| 3 | **Netlist parity** | fresh `sch export netlist` → sexpdata parse → exact pad-set diff vs NETLIST.md | **EXACT: 35/35 nets, 149/149 pads, 0 mismatch** |
| 4 | Unconnected-pin parity | export unconnected-* single-pad nets vs NETLIST.md list | **EXACT: 28 == 28, zero drift** |
| 5 | **5 part swaps** footprint/pin | netlist footprint vs placed footprint, per changed C-number | **UNCHANGED — all drop-in** (see below) |
| 6 | Component-set parity | 50 netlist comps vs PCB footprints | **all 50 placed**; only extras = MH1-3 mount holes (correctly not in netlist) |
| 7 | **BOM** LCSC cross-check | BOM.csv rows vs changed refs | **match**: D4=C2128, D5-7=C316020, LED1=C965804, R11=330R/C23138, R12=1k/C21190, C1=C72477 |
| 8 | **Host test suite** | built 9 doctest binaries directly, ran all | **132/132 cases, 684/684 assertions, 0 failed** |
| 9 | **Real-capture decode** | 5 logic-analyzer CSVs → inverted-RS485 UART decode | 9 streams, 71,883 bytes, **99.99% good stop bits** (71,874/71,883) |
| 10 | **Production `kv_parse` replay** | every real capture streamed through `cpp/protocol/kv_protocol.cpp` | **8,816 KV pairs, 675/675 hmph, 0 DESYNCS**, harness exit 0 |
| 11 | **ESP32-S3 build** of the parser | xtensa-esp32s3 `-std=c++20 -fno-exceptions -fno-rtti` | `kv_protocol.cpp` **clean, exit 0** (3352 B .text); `mode_state.cpp` clean (1610 B) |
| 12 | **Live JLC stock** | `jlc search <C#> --json` for all 31 unique parts | **31/31 ≥ 3× qty-2 need, 0 shortage/obsolete flags** |

**The 5 part swaps are true drop-ins** (footprint + 2-pad assignment identical
before/after — that is *why* DRC stays at 0):

- **R11** 1k→330Ω (C21190→C23138) — `R_0603_1608Metric`, still STATUS_LED.1 + LED1_A.2. The one *required* round-1 fix (F1: status LED was sub-visible).
- **R12** 2k→1k (C22975→C21190) — `R_0603`, power-LED brightness; optional F1 fix, cost-neutral, now shares R9's 1k line.
- **D5-D7** ESD clamps — `D_SOD-323`, C316020 (replaces 0-stock C51450).
- **LED1** status LED — `LED_0603_1608Metric`, C965804 (replaces low-stock C72043).
- **C1** bulk cap — `CP_Elec_6.3x7.7`, C72477 (100µF/25V, in stock).
- **D4** flyback — `D_SOD-323`, C2128 (genuine 1N4148WS).

**Live-stock margins (qty-2 build, threshold = 6):** every part clears comfortably.
Lowest is the RJ45 jack **C2847314 @ 3,484**; U1 module **C2913198 @ 6,104**;
bulk cap **C72477 @ 130,912**; ESD **C316020 @ 140,452**; status LED **C965804 @ 5.2M**.

### Confidence statement

The electrical and firmware verification is **solid and reproducible**. Every claim
in VALIDATION.md / ORDER-READY.md's §5a table was re-derived from the raw files this
session and matched to the digit (ERC/DRC 0/0, 132/684 tests, 8816/675/0 replay,
99.99% stop bits). The R11/R12 resistor edits touch LED drive current only — no
signal, no firmware, no parser path — so they are inert with respect to every proof
above. **Nothing machine-verifiable blocks ordering the PCB.**

**One honest, non-blocking caveat:** `cpp/protocol/ipc_protocol.cpp` no longer
compiles under the *current* `espressif/idf:latest` (GCC 16.1.0) with default flags —
but this is **toolchain drift, not a code regression**. The error is entirely inside
vendored `third_party/rapidjson/document.h:319` (`-Wtemplate-body`, which GCC 16
promoted to a hard error). Proof it is not our code: host GCC 12.4.0 accepts it, and
`-Wno-template-body` makes the same xtensa GCC 16 compile it to exit 0. It does **not**
touch the wire parser (`kv_protocol.cpp` compiles clean for esp32s3) — `ipc_protocol`
is the Unix-socket/JSON layer, not the bus-decode path proven in the replay.

---

## 2. WILL IT FIT

### 2a. Delivered board dimension — **100 × 55 mm** (confirmed)

The KiCad `Edge.Cuts` outline, re-parsed from `Esp32Tap.kicad_pcb` this session, is a
clean closed rectangle: **X 100→200 = 100 mm, Y 100→155 = 55 mm**, 2-layer, 1.6 mm.
This matches README, `enclosure/DIMENSIONS.md` ("Board 100.0 × 55.0 × 1.6 mm"), and
the captured JLC cart line ("2-layer **55×100**").

**On the "100 × 71 mm panel with 5 mm rails" premise:** that figure appears **nowhere**
in this repo — a case-insensitive grep for `rail|panel|breakaway|depanel|71mm|v-cut|
mouse-bite` across ORDER-READY / VALIDATION / REPORT / ORDERING / README / *.pcb
returns zero hits. The **designed and delivered *usable* board is unambiguously
100 × 55 mm.** If JLC's Standard PCBA process panelizes it with breakaway edge rails
(a fab-side manufacturing aid), those rails **ship attached** and the owner snaps/dresses
them off — after depanel the board is 100 × 55 mm, which is what seats in the enclosure.
The 100 × 71 figure, if it exists at all, is a transient *assembly panel*, never the
delivered product. **Action:** confirm the panelization/rail step in the live JLC cart
before treating anything but 100 × 55 mm as as-ordered (see §4).

### 2b. Enclosure ↔ board fit — numbers

The board-relative position + rotation of every connector, LED, switch, mount hole, and
tall part was extracted from the PCB and compared numerically against
`esp32tap_case.scad`. **Everything matches to 0.000 mm except the two RJ45 apertures.**

| Check | Result |
|---|---|
| **(a) Cavity fit** | Board 100×55 in interior 104.0×73.3 → 2.0 mm each X side, 9.3 mm antenna end, 9.0 mm bottom. Module body overhangs top edge only 5.95 mm (scad assumed 6.3) → antenna air gap **3.35 mm, 0.35 mm safer** than designed. Tightest real clearance = J3 USB-C shell **0.53 mm** from the right wall (intentional — reaches the aperture). **No collisions.** |
| **(b) Connector cutouts** | J3 USB-C: real Y-center 36.500 == scad 36.500 (**0.000 mm**). **J1/J2 RJ45: FAIL — 8.695 mm off in Y** (see §3). |
| **(c) Height clearance** | Above-board headroom 16.5 mm clears tallest part (RJ45 13.4 mm) by **3.1 mm**; C1 electrolytic 7.7 mm → 8.8 mm clear; USB-C / WROOM / relay all lower. **All positive.** |
| **(d) Mounting bosses** | scad posts MH1(2.9,26.5), MH2(97.0,3.0), MH3(97.0,52.0) match real hole centers **exactly, dX=dY=0.000 mm** all three. |
| **Lid features** | LED1(79.0,12.97), LED2(32.5,44.5), SW1(36.0,5.0), SW2(78.0,17.4) light-pipe/tool-hole positions all **0.000 mm**. |
| **STL render** | Docker openscad re-render **byte-identical** to checked-in STLs. Base 108.4×77.7×23.3 mm (120.4 across incl. zip-tie ears); Lid 108.4×77.7×3.8 mm. Matches parametric dims. |

### 2c. Treadmill install — **PENDING MEASUREMENT**

The install-fit question **cannot be closed from documentation alone.** What *is*
established: the tap splices inline on the console↔motor RJ45 cable near the Lower PCA;
it reuses the same treadmill **+8 V rail (RJ45 pins 2/8)** the Pi hat already draws from
(no new power-sourcing constraint); the RJ45 footprint (Amphenol 54602-x08) matches the
proven PiZeroHat design; and the enclosure is internally self-consistent. What is
**missing**: no cavity/space measurement, no cable-slack measurement, and no baseline of
the current Pi+PiZeroHat footprint exists anywhere in the repo. See §4.

---

## 3. BLOCKERS

### BLOCKER-1 (enclosure only — does **not** block ordering the PCB)

**Both RJ45 wall cutouts in `esp32tap_case.scad` are misplaced 8.695 mm in Y.** A case
printed as-is would not let the console/motor RJ45 plugs seat — roughly half of each
aperture opens over solid wall, the other half over empty cavity.

- **J1 (CONSOLE):** real jack centerline (F.Fab, F.CrtYd, and both latch-peg holes all
  agree) = board-rel **Y 3.555**. `scad j1_yc = 12.25` → aperture spans Y 3.40–21.10;
  jack body spans Y −4.60…11.71 → top ~8 mm of the jack is behind solid wall.
- **J2 (MOTOR):** real centerline **Y 32.555**; `scad j2_yc = 41.25` → same error.
- **Root cause:** the scad set `j_yc = pin-1 'at' Y + 4.25`, but the jack centerline is
  pin-1 Y **minus** the 4.445 mm body/peg offset. Sign flip → 4.25 + 4.445 = **8.695 mm**.
  Every other placement reads the component `at` correctly and matches to 0.000 mm; only
  the two RJ45s got this bad manual offset.
- **Fix:** set `j1_yc ≈ 3.55`, `j2_yc ≈ 32.55` (or derive from `at.Y − 4.445`), re-render,
  re-run the fit check. **17× the 0.5 mm tolerance** — must be fixed **before printing**.
  Free to fix now (no STL printed).

*(Verdict independently reproduced this gate from the raw PCB: J1 F.Fab center
Y = (−4.095 + 11.205)/2 = 3.555; scad j1_yc = 12.25; Δ = 8.695 mm.)*

### Non-blocking / advisory (order does not wait on these)

- **BOM label gap:** the grouped R9+R12 line in `bom/BOM.csv` is described "Relay driver
  base resistor" even though R12 is the power-LED resistor. Cosmetic `gen_docs.py` labeling
  bug; NETLIST.md is correct.
- **Stale cost docs:** REPORT.md still shows the Economic-PCBA model (9 Extended lines,
  ~$65–95) — superseded by the live finding that **U1 forces Standard PCBA** (+$25.56 setup,
  +$45.90 feeders → ~$156.33 / ~$170–185 all-in for qty-5). Doc pass pending; not fab-blocking.
- **Cart quantity unresolved:** saved cart is Qty=5; ORDER-READY recommends dropping to
  Qty=2 (~$125–140, more respin headroom). Not yet applied in the saved cart.
- **Minor artifact slip (does not change any verdict):** the stock track's "smallest margin
  C371166 @ 7,753" is mis-identified; the true lowest is RJ45 **C2847314 @ 3,484** — still
  far above the 6-unit threshold.

---

## 4. Physical measurements the owner MUST take (only tools can't close these)

Take these at the treadmill, at the existing Pi/PiZeroHat splice near the Lower PCA,
**before/at install**:

1. **Install cavity** — measure available **W × D × H** at the mount spot and clearance to
   adjacent metal / motor / belt. Nothing anywhere in the repo records this space. The box is
   **108.4 × 77.7 × ~25.5 mm** plus four protruding 6×12×4 mm zip-tie ears (120.4 mm across).
2. **Cable slack** — on **both** cut segments (console→splice and splice→motor). Confirm there
   is enough harness slack to route through/around the box without straining the RJ45 plugs.
3. **+8 V rail sourcing capacity** — measure the treadmill's +8 V rail (RJ45 pins 2/8) can
   source the ESP32Tap load under real belt-running noise (per PiZeroHat WIRING-CHECKLIST).
   SPICE modeled the EN divider + droop, not the SMPS against a 20-yr-old noisy rail.
4. **Baseline** — record the current Pi+PiZeroHat envelope at that spot ("is the new box
   smaller than what's there now?").
5. **BLE/RSSI site survey** — at final placement inside the metal motor hood (flagged in
   `firmware/PLAN.md` / VALIDATION.md; still open).
6. **JLC cart panelization step** — re-open the saved cart, explicitly check whether Standard
   PCBA adds breakaway rails (they ship **attached** — snap/dress off before the board fits the
   100×55 interior), and set **PCBA Qty = 2**.

---

## 5. Final verdicts

- **WORKS: YES.** PCB is ERC/DRC-clean and dimensionally correct; all 5 part swaps are true
  footprint/pin drop-ins; host tests 132/132; production `kv_parse` replay 8,816 pairs /
  675 hmph / 0 desync; esp32-s3 parser build clean; all 31 parts in live stock. **GO to order
  the PCB.** (One vendored-lib toolchain caveat, non-blocking, does not touch the wire parser.)
- **FITS: PENDING-MEASUREMENT**, with one **enclosure blocker to fix first**. The delivered
  board is 100 × 55 mm and drops into the case on cavity/height/mounting/USB-C/LED checks —
  but the two RJ45 apertures must be corrected (8.695 mm) before any case is printed, and the
  three treadmill measurements in §4 must be taken before mounting.

---

*Generated 2026-07-23 as the final Works & Fits gate. All checks re-run this session;
numbers reproduce VALIDATION.md / ORDER-READY.md §5a. No files fabricated, no PCB ordered,
no STL printed, nothing paid.*
