# Esp32Tap Rev C vendor procedure

**Status: HOLD. Do not submit, add to a production cart, authorize a
substitution, or pay.** This is a future review procedure, not purchase
authorization.

## Exact review inputs

| Vendor input | File |
|---|---|
| PCB fabrication | `kicad/Esp32Tap-gerbers.zip` |
| Assembly BOM | `bom/BOM.csv` |
| Placement | `bom/CPL-positions.csv` |
| Enclosure | Current approved base/lid files only after enclosure review |
| Pigtails | Approved dimensioned console/motor production drawings |

Regenerate and run:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
python3 hardware/Esp32Tap/tools/check_jlc_stock.py --refresh
git diff --check
```

Record hashes for the current ZIP, BOM, CPL, stock snapshot, approved enclosure
files, and pigtail drawings. Never mix generations or edit generated CSVs.

## PCB/PCBA preview

Require the preview and quote to show:

- 95.0 × 58.0 mm, four layers, vendor 1.6 mm selection;
- `JLC04161H-7628`, 1 oz outer / 0.5 oz inner, green mask, white legend,
  ENIG, lead-free top-side Standard PCBA;
- controlled impedance for 90 Ω USB on L1/L2, using the generated
  0.2906/0.2000 mm controlled geometry;
- J1 `430450809` and J2 `430451010` as right-angle **SMT Micro-Fit** headers,
  with exact package, orientation, and latch direction;
- J3 under standard reflow. Its four S1 plated mechanical stakes are part of
  the stock footprint; do not request or quote manual/wave soldering;
- the exact DNP/populated set and no unreviewed substitutions;
- "Confirm production files" enabled and no vendor-added order legend;
- an IPC-6012 Class 2 commitment to at least **20 µm average PTH/via barrel
  copper**, confirmed in the live quote/DFM response;
- confirmation that the generated 1.4/1.0 mm GND vias, plated J3 slots, and
  stock U1 keepout are accepted unchanged.

U1 is fully on-board with 3.25/3.30 mm margins. The 58 mm axis remains a
carrier/rail question. Require a dimensioned carrier/panel drawing with
tooling, fiducials, tabs, support, depanel method, delivered outline, and edge
dressing. Keep metal and tooling out of the antenna keepout.

The operator-observed `vendor/JLC-DFM-REVIEW.json` is bound to an older exact
archive. Preserve it; do not treat it as approval of current Rev C bytes.
Upload the current exact ZIP for a new live review, then verify the same BOM,
CPL, and placement preview.

Useful live references:

- <https://jlcpcb.com/capabilities/pcb-capabilities/>
- <https://jlcpcb.com/pcb-impedance-calculator/>
- <https://jlcpcb.com/help/article/multi-layer-pcb-standard-laminated-structures>
- <https://jlcpcb.com/capabilities/pcb-assembly-capabilities>
- <https://jlcpcb.com/help/article/how-to-add-edge-rails-fiducials-for-pcb-assembly-order>
- <https://jlcpcb.com/blog/pcb-pth>
- <https://www.ipc.org/TOC/IPC-6012F-TOC.pdf>

## BOM and placement review

Confirm every populated designator's exact code, model, package, class,
quantity, side, position, and rotation. In particular:

- J1/J2 are the two distinct Micro-Fit headers, not RJ45 jacks or magjacks;
- J1 mates only with `430250800`, J2 only with `430251000`, using
  `430300001` terminals;
- K1 is `G6K-2F-Y-TR DC5`;
- U1 is `ESP32-S3-WROOM-1-N8`, fully inside the finished edge;
- C13/C14 and mechanical/test items remain excluded as generated;
- J3's USB opening direction and S1 stake geometry are unchanged.

Any vendor rotation, footprint, or substitution proposal stops the review and
returns to source/regeneration.

## Pigtail quote

The board order does not include the external RJ45 interfaces. Obtain separate
turnkey quotes for two keyed pigtails:

- console: `430250800` housing, `430300001` terminals, mapped to RJ45;
- motor: `430251000` housing, `430300001` terminals, mapped to RJ45.

The quote must bind exact drawings, one-to-one pin mapping, wire type/gauge,
length, strain relief, labels/keying, crimp tooling, pull test, continuity
test, wrong-mating prevention, and RJ45 source. RJ45 single-open 2 A remains
unsupported; do not call the system turnkey until physical qualification and
installed loop testing close.

## Enclosure quote

Keep enclosure purchasing open until the current 95 × 58 board-derived
geometry is approved. Quote nonconductive material, no scaling/hollowing,
critical apertures and posts, shrink/warp, support removal, screw fit, and
installed RF clearance. Local mesh checks are not vendor acceptance.

## Release conditions

Before purchase, obtain and archive:

1. live current-archive CAM/DFM and stack/impedance acceptance;
2. exact BOM/CPL placement acceptance;
3. 20 µm Class 2 barrel-plating confirmation;
4. carrier/rail and J3 standard-reflow confirmation;
5. exact turnkey pigtail and enclosure quotes/drawings;
6. owner authorization for the specified verification quantity.

After arrival, follow `README.md`, `VALIDATION.md`, and `firmware/PLAN.md`.
USB alone cannot power Rev C. First treadmill contact is Proxy-only.
