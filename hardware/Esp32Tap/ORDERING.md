# Esp32Tap Rev B vendor procedure

**Status: HOLD. Do not submit, add to a production cart, authorize a
substitution, or pay.** This procedure identifies the exact files and review
points for a future verification build; it is not permission to place one.

## Exact upload inputs

Use only artifacts regenerated and verified by the canonical gate:

| Vendor input | File |
|---|---|
| PCB fabrication | `kicad/Esp32Tap-gerbers.zip` |
| Assembly BOM | `bom/BOM.csv` |
| Component placement | `bom/CPL-positions.csv` |
| Enclosure base | `enclosure/esp32tap_base.stl` |
| Enclosure lid | `enclosure/esp32tap_lid.stl` |

Do not zip the KiCad source, mix in an older fabrication directory, edit a
generated CSV, or upload loose Gerbers from another run. The fabrication ZIP
must contain exactly 13 members: four copper, two mask, two paste, two
silkscreen, Edge.Cuts, Excellon drill, and Gerber job.

## Before opening a quote

From the repository root:

```bash
make -C hardware/Esp32Tap clean-check
make -C hardware/Esp32Tap check
python3 hardware/Esp32Tap/tools/check_jlc_stock.py --refresh
git diff --check
```

The stock refresh is read-only and replaces the sanitized snapshot only after
all 43 exact pages validate. Review any changed model, package, library class,
or quantity. A part mismatch or substitution stops the process and requires a
new engineering review.

Record hashes for the ZIP, BOM, CPL, both STLs, and stock snapshot. The vendor
preview must refer to those same bytes.

## PCB/PCBA preview requirements

Enter or confirm:

- finished board 100.0 × 55.0 mm;
- four copper layers and approximately 1.6 mm finished thickness;
- `JLC04161H-7628` stack geometry, 1 oz outer / 0.5 oz inner copper;
- green solder mask and ordinary lead-free surface finish unless the owner
  chooses another reviewed finish;
- top-side SMT assembly plus explicit support for J1/J2
  54602-908LF through-hole hand assembly;
- no panel rails or break-tabs left on the delivered functional outline unless
  their removal is included and the antenna carrier is approved;
- no unreviewed substitutions.

Ask JLC to confirm the declared 90 Ω native-USB differential geometry on the
selected production stack. If their calculator or stack differs, stop and
reroute/revalidate rather than accepting an automatic adjustment.

The ESP32 module extends 6.3 mm beyond the finished board edge. Obtain a
carrier/panel drawing showing how the module is supported through assembly and
depanelization while keeping copper, tooling, and metal clear of its antenna.
This is a mandatory vendor gate.

## BOM review

The BOM contains 43 unique populated JLC/LCSC codes. C13/C14, TP1–TP13, and
MH1–MH3 are deliberately absent from assembly purchasing. Confirm:

- every populated designator resolves to the exact code, model, package, and
  Basic/Extended class recorded by the snapshot;
- J1/J2 are unshielded 54602-908LF jacks, not Ethernet magjacks;
- K1 is the 5 V `G6K-2F-Y-TR DC5`;
- U1 is `ESP32-S3-WROOM-1-N8`;
- F1 is `1812L075/24DR`;
- D3 is `SMBJ10A`, D4 is `SMAJ6.0CA`, and Q1 is `BC817-40,215`;
- R9 is 560 Ω / C23204;
- C13/C14 remain unpopulated.

Reject a vendor-selected “equivalent” until its electrical ratings, pinout,
footprint, package, lifecycle, and safety effect are re-reviewed.

## Placement preview

The automated audit checks coordinates, top/bottom side, and rotations against
the generated PCB. The vendor preview is still authoritative for machine
interpretation. Inspect every polarized or asymmetric part, especially:

- U1 antenna orientation and overhang;
- J1/J2 latch and cable-facing direction;
- J3 USB-C opening direction;
- K1 pin 1 and contact orientation;
- U2/U4/U5/U6/U7 and Q1/Q2 pin 1;
- D1, D3, D4, D5–D7, LED1/LED2, C1, and L1.

Any rotation correction belongs in the generator/CPL source, followed by full
regeneration. Do not make an undocumented one-off vendor edit.

## Enclosure preview

Upload base and lid as separate parts. Use nonconductive resin without metal
fill or conductive coating. Ask JLC3DP to confirm:

- watertight acceptance and retained dimensions;
- 2.5 mm walls, 3.0 mm lid, posts, pilot holes, and countersinks;
- connector apertures and recessed USB access;
- material shrink/warp expectations for the 109.0 × 90.3 mm shell;
- no automatic scaling, hollowing, supports retained in functional openings,
  or orientation change that alters critical faces.

Local mesh validation does not close these vendor questions.

## After an authorized verification build arrives

Do not start with USB alone; it cannot power Rev B. Begin unpowered, then use
current-limited +8 V bench power. Follow the complete sequence in `README.md`,
`VALIDATION.md`, and `firmware/PLAN.md`. First treadmill contact is Proxy-only
with relay energization compiled out.

Stop immediately for a mismatched part, unexpected rail, heating, missing NC
continuity, TX leakage, wrong feedback state, USB attach anomaly, enclosure
interference, or waveform distortion.
