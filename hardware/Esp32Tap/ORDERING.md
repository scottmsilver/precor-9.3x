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

- quantity 5 PCBs, with only 2 units assembled for the verification build;
- finished board 100.0 × 55.0 mm;
- four copper layers and exactly the vendor's 1.6 mm thickness selection
  (the generated stack metadata totals 1.59 mm);
- `JLC04161H-7628`: 0.035 mm outer copper, 0.2104 mm 7628 prepreg,
  0.0152 mm inner copper, 1.065 mm NP-155F core, then the symmetric return;
- 1 oz outer / 0.5 oz inner copper, green solder mask, white silkscreen,
  ENIG board finish, and lead-free assembly;
- controlled impedance enabled for the 90 Ω D+/D- pair on L1 referenced to
  L2, using 0.2906 mm traces and a 0.2000 mm edge gap on the controlled runs;
  retain the four short 0.20 mm connector-breakout segments;
- top-side Standard PCBA. U1/C2913198 is an Extended-library part whose
  catalog service restriction requires Standard PCBA; these are different
  classifications;
- explicit support for both J1/J2 C2847314 54602-908LF through-hole jacks.
  Their catalog process is Wave Soldering and states that an assembly fixture
  is required. Require JLC to identify the actual manual/wave/pallet process,
  seating requirement, fixture, and quote line; do not assume hand soldering;
- tented 0.30 mm vias matching the Gerber mask, with no unsolicited
  plugging, epoxy fill, or copper fill;
- "Confirm production files" enabled;
- "Remove Order Number" selected, with no vendor-added legend marking;
- no panel rails or break-tabs left on the delivered functional outline unless
  their removal is included and the antenna carrier is approved;
- no unreviewed substitutions.

Ask JLC to confirm the declared 90 Ω native-USB differential geometry on the
selected production stack. If their calculator or stack differs, stop and
reroute/revalidate rather than accepting an automatic adjustment.

The ESP32 module extends 6.3 mm beyond the finished board edge. Obtain a
carrier/panel drawing showing how the module is supported through assembly and
depanelization while keeping copper, tooling, and metal clear of its antenna.
This is a mandatory vendor gate. Standard PCBA publishes a 70 × 70 mm minimum
single-board assembly size; this board is 100 × 55 mm, so default 5 mm rails
would reach only 65 mm on the short axis. Require the actual carrier dimensions,
tooling holes, fiducials, routed antenna relief, support method, tab locations,
depanel method, delivered outline, and rail-removal/edge-dressing plan.

The operator-recorded JLCDFM evidence in `vendor/JLC-DFM-REVIEW.json` is bound
to the exact local fabrication ZIP. It is not a vendor-signed result or
independent proof of upload provenance. Its accepted raw findings and
dispositions do not replace production CAM, carrier, stackup, or PCBA
engineering approval.

Official references to reopen during review:

- PCB capability limits:
  <https://jlcpcb.com/capabilities/pcb-capabilities/>
- JLCDFM count interpretation:
  <https://jlcpcb.com/help/article/dfm-analysis-result-view-help>
- controlled-impedance calculator:
  <https://jlcpcb.com/pcb-impedance-calculator/>
- standard laminated structures:
  <https://jlcpcb.com/help/article/multi-layer-pcb-standard-laminated-structures>
- PCBA rails and fiducials:
  <https://jlcpcb.com/help/article/how-to-add-edge-rails-fiducials-for-pcb-assembly-order>
- Standard PCBA capability and 70 × 70 mm minimum:
  <https://jlcpcb.com/capabilities/pcb-assembly-capabilities>
- assembly carrier and wave-fixture guidance:
  <https://jlcpcb.com/help/article/pcb-assembly-fixtures>
- via-covering definitions:
  <https://jlcpcb.com/help/article/pcb-via-covering>
- ESP32 module catalog entry:
  <https://jlcpcb.com/partdetail/ESP32-S3-WROOM-1-N8/C2913198>
- RJ45 catalog entry:
  <https://jlcpcb.com/partdetail/AmphenolICC-54602908LF/C2847314>

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
interpretation. The fabrication legend intentionally omits footprint outlines
and reference designators; use the PCB source, BOM/CPL, preview, and retained
`+ C1`, diode/LED cathode, and `K1 P1` labels together. Inspect every
polarized or asymmetric part, especially:

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
