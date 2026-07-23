# ESP32 Treadmill Tap — Build Package

**Esp32Tap rev A** — single-board ESP32-S3 replacement for the Pi Zero 2 W + PiZeroHat
Precor 9.31 serial-bus interceptor. Design complete, ERC/DRC clean (kicad-cli 10.0.1,
`--severity-all`: **0 violations, 0 unconnected**), fab package exported, not yet
fabricated. All artifacts live in `hardware/Esp32Tap/`.

| | |
|---|---|
| Board | 100 × 55 mm, 2-layer, ESP32-S3-WROOM-1-N8 |
| Parts cost per board | **$7.34** — sub-$20 verdict: **PASS** (see BOM) |
| Full order, all-in | **~$65–95** (5 PCBs / 2 assembled + enclosure + shipping) |
| $200 budget | Fits, with **~$105–135 reserve** = exactly one respin |
| Owner assembly | **None.** 100% JLC fab assembly — plug in, flash over USB-C |
| Adversarial review | 4/4 lenses initially **FAILED**; all findings fixed, re-verified |

---

## 1. Architecture summary — and what changed vs the Pi

The board sits in the middle of the console↔motor RJ45 cable exactly where the Pi
did: pin 6 (console→motor) is cut through the device, pin 3 (motor→console) is
passively tapped, and pins 1/2/4/5/7/8 (GND, +8 V, unknown, safety interlock) are
pure copper pass-through that never touch silicon.

```
      antenna overhangs top edge (Espressif keep-out fully off-board)
   ┌──────────────────╨╨╨╨╨──────────────────┐
 ┌─┤ J1 RJ45           ESP32-S3-WROOM-1-N8   │
 │ │ CONSOLE   K1 relay      (U1)        SW2 │
 └─┤           G6K-2F-Y                LEDs  ├─┐
 ┌─┤ J2 RJ45   driver     SW1 EN       U3    │ │ J3 USB-C
 │ │ MOTOR                             ESD   ├─┘ (flash/JTAG/console)
 └─┤  8V→3.3V buck: F1 TVS bulk → TPS54202   │
   └─────────────────────────────────────────┘
```

### What changed vs the Pi version

| Aspect | Pi Zero 2 W + PiZeroHat | Esp32Tap |
|---|---|---|
| Boards | Pi + custom hat (2 boards, D24V10F5 buck module) | One 100×55 mm board, on-board TPS54202 buck |
| Serial I/O | pigpio bit-banged, hand-inverted DMA waveforms | Hardware UARTs with `uart_set_line_inverse` (native inverted 9600 8N1) |
| **Proxy mode** | **Software forwarding** — bytes cross through Linux userspace | **Normally-closed relay bridge** — console→motor through copper, zero latency, zero firmware dependence |
| Unpowered behavior | Depends on hat wiring | **Stock treadmill by construction**: relay de-energized bridges pin 6; RX taps behind 4.7 kΩ (≈0.3 mA worst-case back-feed); GND-referenced ESD clamps stay inert |
| Safety envelope | C++ `treadmill_io` under systemd | Ported to ESP-IDF **on-MCU**, survives total network loss; `esp_task_wdt` panic-reset releases the relay |
| OS / boot | Linux, systemd, SD card, DMA crash journal | No OS image, no SD card, no pigpio DMA-handle leak class at all |
| FTMS / HRM | Two Rust daemons over BlueZ | NimBLE dual-role on core 1 (FTMS peripheral + HRM central) |
| Control API | Unix socket → FastAPI on the same host | Authenticated **WSS/HTTPS directly on the MCU**; server.py becomes a remote WSS client |
| Power | 5 V USB or hat buck | Treadmill +8 V **or** USB VBUS, ORed with Schottkys — bench flashing needs no treadmill |

The fail-safe relay (Omron G6K-2F-Y DC3, both DPDT poles paralleled) is the design's
central safety claim: de-energized (unpowered, boot, crash, watchdog, Proxy) the
console is bridged to the motor and the ESP32 TX is physically open-circuit; it
energizes only in Emulate, after the UART is configured — boot-time TX glitches on
the motor line are impossible. The driver is an S8050 NPN with a 10 kΩ base
pull-down, so GPIO21 Hi-Z during boot/reset keeps the relay released.

## 2. System split — what runs where now

**ON-MCU (must survive total network loss):**
- KV streaming parser with all Postel tolerances (skip 0xFF/0x00, empty `[]`, partial frames, printable-ASCII guard, 64-byte caps)
- Proxy as boot/default mode (hardware bridge); emulate 14-key/5-burst cycle (100 ms gaps, `part=6`/`diag=0`/`loop=5550`); hmph/inc hex codecs with noise guards
- The **full safety envelope**: zero-on-emulate-entry, auto-proxy/auto-emulate, 3 h no-change timeout, session watchdogs, task-WDT relay release
- Interval executor (port of `ProgramState`'s 1 s tick loop) — a loaded workout survives RF stalls
- FTMS BLE peripheral wired directly to the local mode engine; HRM BLE central with NVS persistence
- HTTPS/WSS control API (existing newline-JSON vocabulary + HRM verbs), mDNS `_treadmill._tcp` with `scheme=https`
- Clamps now include application limits on-MCU: speed 0–120 tenths AND incline 0–30 half-pct (15%) — the remote box is no longer a trust boundary (0–198 stays as the absolute hardware guard)
- 30 s run-record checkpoints buffered in a RAM/NVS ring during server outages, replayed on reconnect

**OFF-DEVICE (server.py, graceful-degradation optional):**
- Gemini coach/chat/voice, program *generation*, workout_db, histories/saved workouts, GPX, profiles, web UI static + TLS, `/api/background/advise`
- server.py swaps its Unix-socket client for a WSS client (same JSON schema); programs push down to the MCU (push-down-then-mirror; MCU authoritative for the executing program)
- Client base-URL decision (recorded at M5): **server-proxies-belt** — server.py stays the single base URL for app clients and proxies belt commands to the MCU; FTMS/Zwift and the safety envelope never depend on it

## 3. Complete costed BOM

Source: `bom/BOM.csv` (generated from `tools/design.py`; JLC-compatible headers, no
trailing total row — that would break the JLC BOM-tool upload). C-numbers were
re-verified July 2026 against live LCSC product pages after review killed 8 of ~30
original lines.

| Ref(s) | Part | Footprint | LCSC | Class | Qty | Unit $ | Ext $ |
|---|---|---|---|---|---:|---:|---:|
| J1, J2 | RJ45 Amphenol 54602-908LF (unshielded, no magnetics) | 54602-x08 THT | C2847314 | Extended-THT | 2 | 0.380 | 0.760 |
| J3 | USB-C HRO TYPE-C-31-M-12 | 16-pin USB2.0 | C165948 | Basic | 1 | 0.160 | 0.160 |
| U1 | ESP32-S3-WROOM-1-N8 (N8R2 C2913204 = drop-in PSRAM upgrade) | module | C2913198 | Extended | 1 | 3.200 | 3.200 |
| U2 | TI TPS54202DDCR buck 4.5–28 V/2 A | SOT-23-6 | C191884 | Extended | 1 | 0.350 | 0.350 |
| U3 | USBLC6-2SC6 USB ESD array | SOT-23-6 | C7519 | Basic | 1 | 0.200 | 0.200 |
| K1 | Omron G6K-2F-Y-TR DC3 DPDT relay (~45 mA coil) | G6K-2F-Y | C2153097 | Extended | 1 | 1.500 | 1.500 |
| Q1 | S8050 NPN relay driver | SOT-23 | C2146 | Basic | 1 | 0.020 | 0.020 |
| D1, D2 | SS34 Schottky (VIN ORing: 8 V leg + USB leg) | SMA | C8678 | Basic | 2 | 0.050 | 0.100 |
| D3 | Littelfuse SMBJ12A TVS (unidirectional, 12 V standoff) | SMB | C151251 | Extended | 1 | 0.050 | 0.050 |
| D4 | 1N4148WS flyback (JSCJ, genuine SOD-323) | SOD-323 | C2128 | Basic | 1 | 0.010 | 0.010 |
| D5–D7 | BORN PESD3V3L1BA-N (GND-referenced, inert unpowered) | SOD-323 | C316020 | Extended | 3 | 0.061 | 0.183 |
| LED1, LED2 | Green status (GPIO38) + red power 0603 | 0603 | C965804 / C2286 | Extended/Basic | 2 | 0.005 | 0.012 |
| SW1, SW2 | C&K KMR2 tactile (EN + BOOT) | KMR2 | C72443 | Extended | 2 | 0.100 | 0.200 |
| F1 | Littelfuse 1206L075/16WR polyfuse 0.75 A hold / 16 V | 1206 | C371166 | Extended | 1 | 0.100 | 0.100 |
| L1 | Sunlord SWPA4030S100MT 10 µH shielded, Isat 2.4 A | 4030 | C38117 | Basic | 1 | 0.070 | 0.070 |
| R1, R3 | 100k (buck FB top; EN divider top) | 0603 | C25803 | Basic | 2 | 0.002 | 0.004 |
| R2 | 22k (buck FB bottom) | 0603 | C31850 | Basic | 1 | 0.002 | 0.002 |
| R14 | 47k (buck EN divider bottom — EN abs-max fix) | 0603 | C25819 | Basic | 1 | 0.002 | 0.002 |
| R4, R5 | 5.1k USB-C CC pull-downs | 0603 | C23186 | Basic | 2 | 0.002 | 0.004 |
| R6 | 100R motor-pin6 TX series | 0603 | C22775 | Basic | 1 | 0.002 | 0.002 |
| R7, R8 | 4.7k RX tap series (0.3 mA unpowered back-feed cap) | 0603 | C23162 | Basic | 2 | 0.002 | 0.004 |
| R9, R11 | 1k (relay base; status LED) | 0603 | C21190 | Basic | 2 | 0.002 | 0.004 |
| R10, R13 | 10k (relay base pull-down; EN pull-up) | 0603 | C25804 | Basic | 2 | 0.002 | 0.004 |
| R12 | 2k power-LED resistor | 0603 | C22975 | Basic | 1 | 0.002 | 0.002 |
| C1 | 100 µF/25 V electrolytic (ROQANG RVT1E101M0607, 6.3×7.7) | CP_Elec_6.3x7.7 | C72477 | Extended | 1 | 0.100 | 0.100 |
| C2, C3 | 4.7 µF/50 V X7R input/buck-VIN ceramic | 1206 | C29823 | Basic | 2 | 0.040 | 0.080 |
| C4, C5, C9 | 100 nF (buck VIN HF, BOOT cap, 3V3 HF) | 0603 | C14663 | Basic | 3 | 0.004 | 0.012 |
| C6, C7 | 22 µF/16 V buck output | 0805 | C45783 | Basic | 2 | 0.020 | 0.040 |
| C8 | 10 µF/16 V ESP32 3V3 bulk | 0805 | C15850 | Basic | 1 | 0.010 | 0.010 |
| C10, C11 | 1 µF (EN reset RC; VBUS bypass) | 0603 | C15849 | Basic | 2 | 0.006 | 0.012 |
| TP1–TP4, MH1–MH3 | Test pads + mounting holes | — | — | none | — | — | — |

**Parts total per board: $7.34.** The sub-$20 target is met with 63% margin —
**PASS** — and holds even if a few commodity lines get substituted at slightly
worse prices in the JLC BOM tool. (The single biggest lever is U1 at $3.20; the
relay is second at $1.50.)

## 4. JLCPCB ordering walkthrough + $200 budget ledger

Hard constraint honored: **100% fab assembly.** Every line is either JLC-library SMD
(reflow) or the two THT RJ45s via JLC's hand-solder service. Zero owner soldering.
All figures are July-2026 snapshots — re-quote every C-number in the JLC BOM tool in
the cart before paying.

### Step-by-step

1. **jlcpcb.com → Order now → Add gerber file.** Upload `kicad/Esp32Tap-gerbers.zip`.
2. **PCB options**: 2 layers, 100 × 55 mm (auto-detected), qty **5**, 1.6 mm, HASL
   (ENIG unnecessary), leave via covering default. The board's minimum drill is
   **0.3 mm** — inside JLC's 2-layer capability (0.2 mm drills are a 4+-layer
   capability; this board deliberately uses none).
3. **Enable PCB Assembly**: Economic, **top side only**, **assemble 2 of 5**.
4. **Upload BOM**: `bom/BOM.csv` (Comment / Designator / Footprint / LCSC Part #).
5. **Upload CPL**: `bom/CPL-positions.csv` (top side, bottom-left origin, +Y up —
   already JLC convention).
6. **BOM tool review — the critical human step**: confirm *every* line resolves to
   the intended part **with assembly stock** (LCSC stock ≠ JLC assembly stock).
   Part-specific rules: no magjack substitute for J1/J2 (magnetics break the
   DC-coupled serial); D3 must be unidirectional 12 V standoff; F1 ≥16 V; input
   caps ≥25 V; L1 ≥1.6 A Isat shielded 4030; U2 must be the genuine TI TPS54202.
7. **Placement preview**: nudge rotations if needed — eyeball the relay, USB-C, and
   ESP32 module against the 3D render.
8. **Confirm Do-Not-Place list is empty** (TP1–TP4/MH1–MH3 are already excluded).
9. **JLC3DP (same account)**: export the two STLs from
   `enclosure/esp32tap_case.scad` (`openscad -D 'part="base"' …`, `-D 'part="lid"'`),
   upload both, material **LEDO 6060 resin** (or PA12 MJF for toughness), no
   post-finish. **Combine shipping** with the PCBA parcel.
10. **Pay and set the delivery address.** Economy shipping, ~8–15 days.

### Budget ledger (5 PCB / 2 assembled, USD)

| Line | Est. |
|---|---:|
| 2-layer PCB 100×55, qty 5 | $4–8 |
| Economic PCBA setup | $8.00 |
| Stencil | $1.50 |
| SMT joints (~185 × 2 boards × $0.0017) | ~$0.65 |
| Parts, 2 × $7.34 | ~$14.70 |
| Extended-part loading fees ($3 × up to 9 realistically-Extended lines: U1, K1, J1/J2, U2, D3, D5–D7, SW1/SW2, F1, C1, +L1 hedge; some may land Basic/Preferred = waived) | $18–27 |
| THT hand-solder service flat fee (RJ45 × 2) | $3.50 |
| THT joints (≈20 × ~$0.017 × 2 boards) | ~$0.70 |
| **JLCPCB subtotal** | **~$52–64** |
| Shipping (economy) | $8–15 |
| JLC3DP enclosure (resin base + lid) | $6–14 |
| Combined-shipment saving | −$5–10 |
| **All-in total** | **~$65–95** |
| **$200 budget remaining** | **~$105–135 → exactly one respin** |

Budget policy from the design review: treat the first order as the verification
build. The respin reserve is thinner than earlier estimates assumed (they
undercounted Extended loading fees at 2–3 lines) — but one full respin still fits.

## 5. Adversarial review — findings and resolutions

Four independent adversarial lenses were run against the design. **All four
initially failed**: `electrical: FAIL`, `fab-dfm: FAIL`, `budget-and-order: FAIL`,
`firmware-safety: FAIL`. Every finding was fixed in-place (in `tools/design.py` and
regenerated downstream), then the full pipeline was re-verified: **ERC = 0
violations, DRC = 0 violations / 0 unconnected** (kicad-cli 10.0.1,
`--severity-all --exit-code-violations`; reports at `kicad/erc.rpt`,
`kicad/drc.rpt`), gerbers + Excellon re-exported, `Esp32Tap-gerbers.zip` rebuilt.
Drill census after fixes: 76× 0.3 mm vias, nothing below 0.3 mm.

### Electrical

- **TPS54202 EN absolute-maximum violation** — a bare pull-up would float EN to
  ~7.6 V against a 7 V abs-max. Added **R14 47k EN→GND** (C25819, Basic) forming a
  100k/47k divider from VIN: EN ≈2.6 V at 7.6 V treadmill VIN, ≈1.6 V at 4.7 V USB —
  above the ~1.21 V enable threshold on both sources, below abs-max. Wired in
  design.py, placed/routed with its own GND stitch via; ORDERING.md flags datasheet
  re-verification at order time. (One courtyard overlap + one clearance issue DRC
  found during placement were fixed before the final zero-violation run.)
- **Via drills below fab minimum** — min through-drill bumped to 0.3 mm; the 2×
  0.25 mm USB_DP vias enlarged; `gen_pcb.py` now enlarges the library ESP32-S3 EP
  thermal-via drills 0.2→0.3 mm (pads to 0.45 mm) at generation time. Verified zero
  drills <0.3 mm in the output board.

### Parts (8 of ~30 BOM lines were dead or the wrong part)

- **J1/J2**: C880323 → **C2847314** (Amphenol 54602-908LF), $0.85 → $0.38/unit.
- **U2**: C60063 → **C191884** — the previously listed number *was not the TPS54202*.
- **D3**: C113996 (a 26 V bidirectional SMBJ26CA) → **C151251** Littelfuse SMBJ12A,
  verified unidirectional 12 V standoff / 13.3 V breakdown.
- **D5–D7**: C456028 (404) → C51450 → **C316020** BORN PESD3V3L1BA-N (3.3 V bidirectional
  GND-referenced, SOD-323, in stock).
- **L1**: C38891 (404) → **C38117** Sunlord SWPA4030S100MT (Isat 2.4 A ≥ 1.6 A req).
- **D4**: C466653 (package mismatch) → **C2128** JSCJ 1N4148WS, genuine SOD-323.
- **C1**: C134722 (10 V, wrong can size) → **C72477** 100 µF **25 V**, 6.3×7.7 mm.
- **F1**: C369159 (13.2 V max) → **C371166** Littelfuse 1206L075/16WR (0.75 A hold,
  **16 V max** — meets the ≥16 V rule; ~0.25 A load, ~1.5 A trip).

All replacements verified against **live LCSC product pages** (July 2026).

### Fab / DFM (enclosure)

- **Lid lip collided with the RJ45 bodies** — solid slab replaced with a 2.0 mm
  perimeter ring (interior open), Ø7.6 cutouts where the ring meets the four screw
  posts; headroom raised 15.0→16.5 mm so the ring clears the 13.4 mm RJ45s by 1.5 mm.
- **Bottom lid-screw posts collided with PCB corners** — added 9.0 mm bottom-edge
  clearance (interior 66.3→73.3 mm, shell 70.7→77.7 mm); post centers moved into a
  shared `posts` list used by base, lid holes, and ring cutouts.
- **USB-C aperture couldn't mate a real cable** — the receptacle face is recessed
  ~4.2 mm behind the exterior wall, so the plug-shell-sized hole was replaced with a
  13.0 × 8.0 mm **overmold-sized** opening through the full wall+clearance; a
  standard cable's overmold passes through and the shell reaches the receptacle.

### Budget & ordering honesty

- **Extended-fee line recomputed**: $3 × up to **9** unique realistically-Extended
  lines = $18–27 (old estimate assumed 2–3 lines). Subtotal restated ~$52–64,
  all-in ~$65–95, with an explicit note that the respin reserve is thinner.
- **Trailing "TOTAL" row removed from BOM.csv** (it breaks the JLC BOM-tool
  upload); fixed in `gen_docs.py` so it stays removed on regeneration. The total
  lives in ORDERING.md's cost table.
- **Verification claim rewritten honestly**: a new "Verification status (honest
  scope)" section states exactly what was done — July-2026 live LCSC product-page
  checks, **not** a JLC BOM-tool assembly-stock check — and mandates a full JLC
  BOM-tool re-verification before payment. The K1 relay's "verified on JLC's part
  pages" claim was downgraded to its actual verification level.
- **JLC capability claim corrected**: 2-layer minimum drill is 0.3 mm (0.2 mm is a
  4+-layer capability).

### Firmware safety

- **Treadmill-contact gate was self-contradictory** (M2/M3 were defined on the
  treadmill while also being required *before* treadmill contact). Fixed: M2 and M3
  are now **bench-rig-only by definition** (M2 = serial engine + emulate timing on
  the loopback rig; M3 = full safety envelope + entire watchdog matrix on the rig),
  and a single authoritative **"Treadmill-contact gate" checklist** in
  `firmware/PLAN.md` (5 boxes, incl. archived matrix evidence and per-task WDT
  relay-release proof) gates TC1 (proxy-only first contact, Emulate compiled out)
  then TC2 (first emulate, after re-running the M3 matrix on the exact build).
  README step 7, ORDERING.md's first-power checklist, and PLAN's testing-discipline
  paragraph all point at this one checklist — bench and treadmill milestones are
  now disjoint and non-circular.
- **Task-WDT scope + action were insufficient**: PLAN.md now *normatively* requires
  `esp_task_wdt` subscription of **every task whose stall can hold the relay
  energized** (serial engine, emulate cycle task, interval executor), and
  **`CONFIG_ESP_TASK_WDT_PANIC=y`** is in the mandatory sdkconfig list — the IDF
  default only logs a warning, which would have left the "stall → relay released"
  watchdog-matrix column unimplemented. A stall now panics → reset → GPIO21 Hi-Z →
  10 k base pull-down → relay released. M3 requires stalling each supervised task
  one at a time.

Nothing was skipped; every finding above was fixed this session.

## 6. Firmware port plan + bench-first safety gate

ESP-IDF 5.x, C++20, `-fno-exceptions -fno-rtti` — `kv_protocol`, `mode_state`, and
`emulation_engine` port nearly verbatim from `cpp/`. Core 0 (pinned, high prio):
serial engine, mode state, emulate cycle, safety timers. Core 1: NimBLE (FTMS +
HRM), WiFi STA + `esp_https_server`, mDNS, checkpoint replay — with explicit
sdkconfig overrides (WiFi/BT default to core 0). The S3's 128-byte TX FIFO makes a
≤50-byte KV message hardware-contiguous, retiring most of the emulate-timing risk.

**Watchdog matrix**: sessions have exactly one controlling liveness source
(`NONE/WSS/BLE/EXECUTOR`). WSS manual sessions: 4 s heartbeat with a documented
**10 s bounded reconnect-grace** (frozen, then zero + Proxy). BLE FTMS sessions: the
BLE supervision timeout (~4 s) *is* the heartbeat; disconnect ⇒ zero + Proxy.
Executor sessions: network/BLE silence never touches the belt; MCU reboot boots to
Proxy with **no program resume**. Console buttons always work — the bridge is
hardware. Every matrix cell gets a regression test before M3 closes.

**Milestones (each gates the next):**

| Gate | Scope | Where |
|---|---|---|
| M1 | Host-parity build; existing doctest suite green on linux + ESP32; UART loopback proves inverted 9600 8N1 | Bench |
| M2 | Serial engine on the loopback rig; RX matches golden vectors; TX cycle timing vs Pi capture on a logic analyzer | Bench |
| M3 | Emulate + full safety envelope; entire watchdog matrix, one regression test per cell; per-task WDT relay-release proof | Bench |
| **Gate** | **Treadmill-contact checklist** (5 boxes: M1–M3 evidence archived, WDT column proven, dead-board signal-integrity test, +8 V sourcing measured, belt clear) | — |
| TC1 | Proxy-only observation, Emulate compiled out — first-ever treadmill contact | Treadmill |
| TC2 | First emulate, after re-running M3 matrix on the exact build | Treadmill |
| M4 | Radios; **24 h+ WiFi/BLE coex soak is a hard bench gate**; fallback (second radio/MCU) decided here | Bench |
| M5 | WSS + token + TOFU cert pinning, mDNS, server.py WSS adapter, on-MCU executor, measured RAM budget published | Bench |
| M6 | OTA (dual partitions, pull only at speed 0 + Proxy, rollback), NVS provisioning, ship-check acceptance script | Bench |

Security lands **before** the WSS port is ever enabled: per-device self-signed cert
+ per-device bearer token bound to the cert fingerprint, clients TOFU-pin, no
plaintext TCP, discovery via mDNS only. On-MCU clamps (12 mph / 15%) are the last
line of defense against a compromised app-plane host.

## 7. What Scott must do himself

Everything below is account/logistics/flashing — **explicitly NO soldering and NO
assembly**, ever. The board arrives fully assembled.

1. **Create/log in to a JLCPCB account** (JLC3DP rides the same account).
2. **Place the order** per the walkthrough in section 4 (or hand the four files to an
   agent session): `kicad/Esp32Tap-gerbers.zip`, `bom/BOM.csv`,
   `bom/CPL-positions.csv`, and the two STLs exported from
   `enclosure/esp32tap_case.scad`.
3. **Run the JLC BOM-tool check before paying** — the one human verification step
   the automation cannot do: every line resolves, assembly stock exists, Extended
   fees as quoted, placement preview rotations look right.
4. **Payment + delivery address.**
5. **On arrival**: follow README bring-up in order — visual/shorts check with a
   multimeter, **USB-C power only** (3.3 V on TP3/TP4, power LED, enumerate, flash
   hello-world), unpowered relay continuity (J1.6↔J2.6 < 1 Ω, TX open), bench 8 V
   from a current-limited supply, then the loopback rig. Flashing is a USB-C cable
   and `idf.py flash` — nothing more.
6. **Never connect the treadmill** until the treadmill-contact gate checklist in
   `firmware/PLAN.md` is fully checked.

## 8. Live-catalog validation (2026-07-23, post-review)

All 31 unique C-numbers were re-verified against JLCPCB's live component
catalog with `@jlcpcb/cli` (`jlc search <C#> --json`, anonymous read-only).
Every part resolved; two order-blockers were found and fixed in this tree:
D5–D7 ESD clamp C51450 showed **0 stock** (replaced with the
footprint-identical C316020) and green LED C72043 showed stock=6 (replaced
with C965804, 5.2M stock). Four lines were live-reclassified
Basic→Extended (J3, U3, L1, LED1) and the ORDERING.md fee ledger was
recomputed (+~$12; all-in now ~$77–107). This closes the "un-run BOM-tool
check" risk at the catalog level; the JLC **BOM-tool upload** check before
payment still stands, since assembly stock and classification churn daily.

## 9. Honest open risks

- **The JLC BOM-tool check has not been run.** C-numbers were verified on live LCSC
  product pages only; LCSC stock ≠ JLC assembly stock, and Basic/Extended classes
  (hence the $18–27 fee band) are estimates. This is the top pre-payment risk.
- **Nothing has been fabricated or powered.** ERC/DRC-clean is not
  bench-validated; rev A of a hand-generated board can still hide a footprint or
  courtyard subtlety JLC's preview will surface.
- **The enclosure has never been printed** — `openscad` wasn't available in the
  design environment, so no STL is checked in; `DIMENSIONS.md` is the
  human-checkable drawing, but fit is unproven until the first print.
- **Motor tolerance of pin-6 silence is uncharacterized** — there is deliberately
  no Idle mode; the Proxy bridge is the idle state until TC1/TC2 characterize it.
- **RJ45 pin 4 function unknown** — passed through untouched, never probed.
- **WiFi/BLE coex on one radio** is the biggest firmware risk; the 24 h M4 soak is
  a hard gate with a pre-agreed fallback (second radio/second MCU), decided at M4,
  not later.
- **RAM budget on the N8 (no PSRAM)** is a measured gate at M5; the N8R2 is a
  BOM-only escape hatch on the same footprint.
- **BLE RSSI inside the metal motor hood** — antenna overhang + plastic enclosure
  + air gap should help; site-survey before final placement.
- **+8 V rail sourcing capacity under worst-case motor load** — carried-forward
  unknown; measured per the PiZeroHat WIRING-CHECKLIST before first connect.
- **The 10 s WSS reconnect-grace window** is a documented safety-semantics choice
  (today's stack effectively never reverts on RF blips); it is flagged for the
  safety review rather than silently shipped.
- **Respin reserve is one respin, not two.** If the first order plus a respin both
  burn the high end of the estimate, the $200 envelope is consumed.

---

*Generated from the design package at `hardware/Esp32Tap/` (worktree
`wf_4b2fe7a1-b29-6`): README.md, NETLIST.md, ORDERING.md, firmware/PLAN.md,
enclosure/DIMENSIONS.md, bom/BOM.csv, kicad/erc.rpt, kicad/drc.rpt.*
