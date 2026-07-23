# Ordering Esp32Tap from JLCPCB (+ enclosure from JLC3DP)

Hard constraint honored: **100% fab assembly** — every BOM line is either a
JLC-library SMD part (reflow) or the two THT RJ45 jacks, which go through
JLC's hand-solder service. The owner plugs the board in and flashes over
USB-C. Zero soldering.

All figures are July-2026 snapshots. JLC churns stock, Basic/Extended
classification, and fees — **re-quote every C-number in the JLC BOM tool in
the cart before paying.** Budget policy from the design review: the $200
envelope allows exactly **one respin**, so treat the first order as the
verification build (assemble 2 of 5).

**Verification status (honest scope).** The C-numbers in `bom/BOM.csv` were
checked in July 2026 against **live LCSC product pages** (part exists, is
the named part, in stock at LCSC) after a review found 8 of ~30 original
lines dead or wrong-part. That is *not* the same as a JLC **BOM-tool**
check: LCSC stock ≠ JLC assembly stock, and Basic/Extended classification
was estimated, not confirmed. Before paying, upload `bom/BOM.csv` to the
JLC BOM tool and confirm **every** line resolves to the intended part with
assembly stock; treat any "Extended" fee estimate below as provisional.

## What to upload

1. **Gerbers**: `kicad/Esp32Tap-gerbers.zip` (already exported; or re-export
   from `kicad/Esp32Tap.kicad_pcb`).
2. **BOM**: `bom/BOM.csv` (JLC-compatible headers: Comment / Designator /
   Footprint / LCSC Part #).
3. **CPL**: `bom/CPL-positions.csv` (Top side only; origin = board
   bottom-left corner, +Y up, already in JLC convention). Expect to nudge a
   few rotations in JLC's placement preview (relay, USB-C, module are the
   ones to eyeball against the 3D render).

## Order options

* PCB: 2 layers, 100 × 55 mm, 1.6 mm, HASL or ENIG (HASL fine), min via
  drill used = **0.3 mm** (JLC's 2-layer minimum drill is 0.3 mm — 0.2 mm
  drills are a 4+-layer capability; the module thermal vias and all signal
  vias on this board are 0.3 mm. Leave "via covering" default).
* PCBA: **Economic**, top side only, **assemble 2 of 5**.
* Confirm "Do Not Place" list is empty (TP1–TP4 and MH1–MH3 are already
  excluded from the BOM/CPL).

## Expected cost lines (5 PCB / 2 assembled, USD)

| Line | Est. |
|---|---|
| 2-layer PCB 100×55, qty 5 | $4–8 |
| Economic PCBA setup | $8.00 |
| Stencil | $1.50 |
| SMT joints (~185 joints × 2 boards × $0.0017) | ~$0.65 |
| Parts, 2 × ~$7.35 (see `bom/BOM.csv`) | ~$14.70 |
| Extended-part loading fees, $3 per unique line. Realistically-Extended lines: U1 module, K1 relay, J1/J2 RJ45 (Extended-THT), U2 TPS54202, D3 TVS, D5–D7 PESD, SW1/SW2 KMR2, F1 polyfuse, C1 electrolytic = up to 9 lines (+L1 if it flips); some may land Basic/Preferred (fee waived) | $18–27 |
| THT hand-solder service (RJ45 × 2): flat fee | $3.50 |
| THT joints (16 pins + 4 locks ≈ 20 × ~$0.017 × 2 boards) | ~$0.70 |
| **JLCPCB subtotal** | **~$52–64** |
| Shipping (economy line, ~8–15 days) | $8–15 |
| Enclosure, JLC3DP resin two-part shell (`enclosure/`) | $6–14 |
| Combined-shipment saving if enclosure rides along | −$5–10 |
| **All-in total** | **~$65–95** |

Still inside the $200 budget, but the respin reserve is thinner than the
earlier estimate — the old numbers assumed only 2–3 Extended lines and
undercounted the loading fees. The per-board parts total (~$7.35) is
deliberately NOT a row in `bom/BOM.csv` (a trailing total row breaks the
JLC BOM-tool upload); this table is where it lives.

## Part-specific notes (verify at order time)

* **U1 ESP32-S3-WROOM-1-N8 (C2913198)** — Extended; if a
  *Preferred-Extended* variant is in stock the $3 fee is waived. The PSRAM
  variant **N8R2 (C2913204)** is footprint-identical: if the firmware RAM
  budget (see `firmware/PLAN.md` M5 gate) demands PSRAM, swap this line
  only.
* **K1 Omron G6K-2F-Y-TR DC3 (C2153097)** — tape/reel, SMD, 3 VDC coil.
  Verification level: LCSC product-page check only (see the verification
  note above), **not** a JLC BOM-tool assembly-stock check. Acceptable
  substitute: any G6K-2F-Y DC3 stock (C93168 is tube packaging — Economic
  PCBA needs reel, so prefer C2153097).
* **U2 TI TPS54202DDCR (C191884)** — the genuine TI part (the previously
  listed C60063 was not the TPS54202). If the EN divider values are ever
  touched, re-check the EN abs-max (7 V) and enable threshold (~1.21 V)
  against the exact datasheet: R3 100k / R14 47k gives EN ≈2.6 V at 7.6 V
  VIN and ≈1.6 V at 4.7 V USB.
* **J1/J2 Amphenol 54602-908LF RJ45 (C2847314, ~$0.38)** — THT, unshielded,
  **no magnetics** (magjacks like HR911105A would break the DC-coupled
  serial — do not substitute one). Any pin-compatible 54602 clone is fine;
  the footprint is the proven PiZeroHat one. These two THT lines also incur
  the Extended loading fee (counted in the fee row above).
* **J3 HRO TYPE-C-31-M-12 (C165948)** — Basic; the KiCad footprint is for
  this exact part.
* **D3 Littelfuse SMBJ12A (C151251)** — must be **unidirectional, 12 V
  standoff** (the previously listed C113996 was a 26 V bidirectional
  SMBJ26CA — do not resurrect it).
* **D5–D7 Nexperia PESD3V3L1BA,115 (C51450)** — 3.3 V bidirectional
  GND-referenced clamp, SOD-323.
* **D4 JSCJ 1N4148WS (C2128)** — SOD-323, matches the D_SOD-323 footprint.
* **L1 Sunlord SWPA4030S100MT (C38117)** — 10 µH 4030 shielded, Isat 2.4 A.
* **C1 ROQANG RVT1E101M0607 (C72477)** — 100 µF **25 V**, 6.3×7.7 mm SMD
  electrolytic (matches the CP_Elec_6.3x7.7 footprint).
* **F1 Littelfuse 1206L075/16WR (C371166)** — 0.75 A hold, **16 V max**
  (meets the ≥16 V rule below; the old 0.5 A/13.2 V part did not). Load is
  ~0.25 A at 8 V, trip ~1.5 A — still protects the harness.
* **Small R-C** — commodity lines; if any exact C-number is out of stock
  pick the same value/size/rating from Basic stock in the BOM tool. Keep:
  input caps ≥25 V, L1 ≥1.6 A Isat shielded 4030, polyfuse ≥16 V rating.

## What arrives

* 5 bare PCBs, 2 fully assembled (all SMD reflowed, RJ45s hand-soldered by
  JLC). No headers, nothing for the owner to solder.
* Enclosure halves (if ordered): base + lid, resin, with light-pipe holes
  and zip-tie ears (`enclosure/DIMENSIONS.md`).

## First-power checklist on arrival

Follow README "Bring-up" — USB-only power first, relay continuity test
unpowered, then bench 8 V. Do not connect a treadmill until the
**treadmill-contact gate checklist** in `firmware/PLAN.md` is fully
checked (M1–M3 green on the bench rig).
