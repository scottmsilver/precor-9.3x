# Esp32Tap — AI Handoff Brief

*Hand this to another AI (or engineer) to continue the ESP32 treadmill-tap project. Written 2026-07-23. Repo: `precor-9.3x`, hardware lives in `hardware/Esp32Tap/`.*

---

## 1. Mission

Replace the Raspberry Pi that currently sits inline on a **Precor 9.31 treadmill**'s
serial cable (between the console / "Upper PCA" and the motor controller / "Lower
PCA") with a single custom **ESP32-S3-WROOM-1** board. The board intercepts the
bus, can proxy or emulate it, runs the belt **safety** logic locally, and exposes
the treadmill over BLE (FTMS to Zwift etc., HRM heart-rate) and WiFi.

**The bus (ground truth — do not re-litigate):** single-ended, **inverted-polarity
(idle-LOW) UART**, 9600 8N1, TTL/3.3 V. It is *called* "RS-485" in old docs but is
**not differential and has no transceiver** — the working Pi reads it with
`bb_serial_invert=1` on one GPIO and drives cable **pin 6** single-ended, and the
motor responds. Cable **pin 6** is CUT and routed through the board (console→RX,
TX→motor) for proxy/emulate; **pin 3** is a passive tap. Both carry `[key:value]`
ASCII. See `CLAUDE.md`, `HARDWARE.md`, `RS485_DISCOVERY.md`.

---

## 2. Current state — what is DONE

- **Board design complete and machine-verified.** KiCad project ERC/DRC clean
  (0/0), pin-accurate `NETLIST.md`, gerbers, costed BOM (LCSC part numbers, all
  live-in-stock as of 2026-07-23), CPL. Board is 100×55 mm, 2-layer.
- **Firmware is a PORT, not a rewrite.** The proven Pi C++ (`cpp/protocol/` KV
  parser, `cpp/engine/` mode-state + emulation + safety) already works on real
  hardware and is being ported to ESP-IDF. Host test suite 132/132; the real
  logic-analyzer captures in `cpp/captures/` replay through the parser with 0
  desyncs; the parser compiles for esp32s3. Plan: `firmware/PLAN.md`.
- **Enclosure fixed and print-ready.** Two-part JLC3DP case (`enclosure/`), RJ45
  cutout blocker corrected (was 8.695 mm off in Y). Base prints clean; lid has an
  accepted flat-plate warp advisory.
- **Both JLC orders are STAGED under the owner's account, nothing paid:**
  - **PCBA:** qty 2 (verification build), Standard PCBA (the ESP32 module *forces*
    Standard — see gotchas), ~**$123** + shipping. `cart.jlcpcb.com`, saved SMT quote.
  - **Enclosure:** base + lid on JLC3DP, ~**$9.62** with shipping.
- **Reports:** `REPORT.md` (design + BOM), `VALIDATION.md` (what was verified),
  `WORKS-AND-FITS.md` (fit check), `ORDER-READY.md` (order walkthrough + real quote).

---

## 3. The critical open question — WILL IT WORK?

An independent adversarial review (Codex, 2026-07-23) verdict:
**UNKNOWABLE-WITHOUT-BENCH** — the design has no gross errors and is orderable as a
2-board verification build, but **one load-bearing thing cannot be proven except on
the real motor.** The three findings, in priority:

1. **⚠️ THE most dangerous assumption: does the real motor accept the ESP32's UART
   timing?** The Pi sent each message as one DMA-contiguous waveform. ESP-IDF UART
   TX can insert inter-byte gaps under scheduler load. **No capture in the repo
   proves the motor tolerates gapped bytes.** If it doesn't, the belt simply won't
   respond to emulate. This is gated as bench test **TC2** and is the reason not to
   mass-produce before the bench proves it.
2. **Dead-board back-feed — mitigated, not measured.** With the board unpowered,
   the NC relay (K1) bridges pin 6 and physically isolates the TX driver; RX taps
   are 4.7 kΩ (R7/R8) and ESD clamps go to GND, so injection is designed to be
   <0.3 mA. That is a design assertion — needs a scope test on a live bus.
3. **Belt safety with WiFi dead — safe but not *universally* stop-safe by design.**
   An on-MCU program deliberately keeps running through network loss; manual
   sessions have a 10 s grace. Critically, the fail-safe relay only releases on a
   stalled task if **`CONFIG_ESP_TASK_WDT_PANIC=y`** is set in the flashed build —
   the IDF default only logs. This is now a hard config gate in `PLAN.md`.

---

## 4. What to do next — in order

### Step A — Place the two orders (owner's money, owner clicks pay)
The carts are staged. The owner logs into their JLC account and finishes:
- PCBA: open the saved SMT quote, pick shipping, Save to Cart, pay. (Before paying,
  re-run JLC's BOM tool for live assembly stock — it churns.)
- Enclosure: JLC3DP, both STLs, lid "accept risk" already ticked, pay.
- **Do not place orders or enter payment yourself. That is always the owner's action.**

### Step B — On arrival, run the treadmill-contact gate BEFORE the belt
Follow `firmware/PLAN.md` → "Treadmill-contact gate" exactly. Do NOT connect a
treadmill until every box is green. Highest-value bench checks (Codex's top 3):
1. **Dead board inline:** scope `CONS6 / MOT6 / PIN3`, verify no level/edge
   degradation, measure clamp current. (Closes finding #2.)
2. **Logic-analyzer compare Pi vs ESP32 at the motor pin** — every byte, message
   boundary, the 100 ms cycle gap, under worst-case RF/CPU load. (De-risks #1
   before belt contact.)
3. **Prove relay release on each stalled task with the exact production
   `sdkconfig`** (`grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig` must pass). (Closes #3.)

### Step C — First treadmill contact, still gated (two steps)
- **TC1 — Proxy only** (Emulate compiled out). First-ever treadmill contact; verify
  stock behavior + parsed telemetry vs the Pi.
- **TC2 — first Emulate**, only after TC1 is clean and the M3 watchdog matrix was
  re-run on the exact flashed build. **This is the test that finally answers "will
  it work" (motor byte-pacing).**

### Step D — Finish the firmware
Port `cpp/protocol` + `cpp/engine` to ESP-IDF per `PLAN.md`; NimBLE FTMS peripheral
+ HRM central; WSS API; OTA. Watch for latent porting bugs like the KvPair[16]
stack-overflow QEMU found — size FreeRTOS task stacks for parser buffers.

---

## 5. HARD RULES (safety — non-negotiable)

- **Never drive the real treadmill belt** (the physical machine, Pi at
  `192.168.1.206`) without the owner's explicit, in-the-moment consent. Belt-moving
  tests happen against the mock dev server (`TREADMILL_MOCK=1`) only.
- **Do not place orders, enter payment, or submit any cart.** Stage only; the owner
  pays.
- **Do not commit to git** until the owner gives the password. **Never push without
  asking.** **Never hard-code a server URL.** (These are standing repo rules in
  the owner's `CLAUDE.md`.)
- After a work session that changes code, run the two-track security audit
  (dependency CVE scan + a `codex exec --sandbox read-only` code review) before
  claiming done.
- The belt-safety envelope is safety-critical: 3-hour timeout, zero-speed on
  emulate entry, auto-proxy on console button, watchdog → relay release. Treat any
  change to it as needing its own review + the full watchdog test matrix.

---

## 6. File map (in `hardware/Esp32Tap/`)

| File | What it is |
|---|---|
| `REPORT.md` | Full design rationale, BOM table, adversarial history |
| `NETLIST.md` | Pin-accurate netlist — **source of truth** for the electrical design |
| `firmware/PLAN.md` | C++→ESP-IDF port plan, safety envelope, BLE, the bench gate |
| `VALIDATION.md` | What was machine-verified (SPICE, DRC, host tests, replay) |
| `WORKS-AND-FITS.md` | Works + fits verdict (board-to-case fit, install measurements) |
| `ORDER-READY.md` | Order walkthrough + the real live JLC quote + Standard-PCBA finding |
| `bom/BOM.csv`, `bom/CPL-positions.csv` | JLC assembly files (LCSC part #s) |
| `kicad/` | KiCad project + gerbers + ERC/DRC reports |
| `enclosure/` | `esp32tap_case.scad` (parametric), STLs, `DIMENSIONS.md` |
| `tools/jlc_api.py` | Read-only JLCPCB OpenAPI client (auth works; account lacks endpoint perms) |
| `tools/design.py`, `gen_*.py` | Generators — the .scad/kicad/BOM regenerate from `design.py` |

Reference (the proven Pi implementation being ported): `cpp/protocol/`,
`cpp/engine/`, `cpp/gpio/`, and the real captures in `cpp/captures/`.

---

## 7. Gotchas / hard-won facts

- **The ESP32-S3 module forces JLC "Standard PCBA," not Economic** — a hard JLC
  gate (tested: WROOM-1 *and* MINI-1 both trip it; it's the shielded castellated
  package class, and the label drifts over time). Standard adds a ~$25 setup + a
  ~$46 per-unique-part feeders-loading fee. There is no Economic-assemblable S3
  module; the only Economic path is a chip-down QFN redesign (not worth it at this
  volume). Re-check the exact part's classification at order time — it can change.
- **JLC's minimum assembly quantity is 2** (for a 5-board PCB order you assemble
  2–5, not 1).
- **Panel rails:** if JLC adds breakaway edge rails for Standard PCBA (making a
  100×71 mm panel), they ship attached and depanel to the real 100×55 mm board —
  the enclosure is sized for 100×55.
- **RJ45 must have NO magnetics** — a magjack breaks the DC-coupled serial. Use a
  plain 8P8C (Amphenol 54602-908LF / C2847314).
- **C1 electrolytic:** several 100 µF/25 V options show JLC inventory shortage;
  C72477 (RVT1E101M0607) was the in-stock one.
- **jlc3dp.com is a separate domain** from jlcpcb.com but shares the JLC passport
  SSO login.
- Board-level SPICE, DRC, host tests, capture replay, ESP-IDF build, and live stock
  are all *machine-verified*. The 4 bench-only unknowns (motor byte-pacing, buck
  under real rail noise, BLE range in the metal motor hood, relay lifetime) are the
  only things a tool can't close.

---

## 8. Environment / tooling notes (this machine)

- KiCad `kicad-cli` v10 present. OpenSCAD + ngspice available via Docker only.
  `espressif/idf` Docker image present for ESP-IDF builds.
- `codex exec --sandbox read-only "<prompt>"` for independent reviews — keep prompts
  **focused** (few files) or it gets killed before concluding.
- Android app testing: prefer the real tablet over the emulator (dynamic
  wireless-debugging endpoint via `adb mdns services`); emulators must NOT run on
  DISPLAY `:1`.
- Issue tracking is `bd` (beads), not TODO lists. Deliverables → claude.ai Artifacts
  per the owner's global preference.
