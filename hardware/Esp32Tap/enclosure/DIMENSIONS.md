# Esp32Tap enclosure — dimension drawing (text form)

The `openscad` CLI was not available in the design environment, so no STL is
checked in. `esp32tap_case.scad` is fully parametric; export with:

```bash
openscad -D 'part="base"' -o esp32tap_base.stl esp32tap_case.scad
openscad -D 'part="lid"'  -o esp32tap_lid.stl  esp32tap_case.scad
```

Every cutout below is also derivable from the parameters at the top of the
.scad — this file is the human-checkable drawing.

## Coordinate convention

Same frame as the PCB (`kicad/Esp32Tap.kicad_pcb`): origin = **board
top-left corner**, +X right (toward USB), +Y toward the board bottom edge.
The board *top* edge (Y=0) is the antenna end. Enclosure interior origin
offsets: board corner sits at interior (2.0, 9.3); interior Z of board
underside = 5.2 (2.2 floor + 3.0 standoffs).

## Overall

| Item | Value |
|---|---|
| Board | 100.0 × 55.0 × 1.6 mm |
| Interior cavity | 104.0 × 73.3 × 21.1 mm |
| Outer shell | 108.4 × 77.7 mm; base height 23.3, lid 2.2 (+1.6 lip ring) |
| Wall / floor / lid | 2.2 mm |
| Under-board clearance | 3.0 mm (THT RJ45 pins ~2 mm) |
| Above-board headroom | 16.5 mm (tallest part: RJ45 13.4 mm → 1.5 mm clear even under the 1.6 mm lid lip ring) |
| Bottom-edge clearance | 9.0 mm (board bottom edge to interior wall) so the two Ø7 bottom lid screw posts clear the PCB corners by 2.0 mm |
| Antenna end | module overhangs board edge 6.3 mm; enclosure leaves a further 3.0 mm air gap; lid thinned to 1.4 mm over the antenna span (X 51–73). Plastic only — no conductive finish, antenna end away from the treadmill frame |

## Wall cutouts (board coordinates)

| Cutout | Wall | Center | Aperture (W × H) | Bottom of aperture |
|---|---|---|---|---|
| J1 RJ45 (CONSOLE) | X = 0 (left) | Y = 12.25 | 17.7 × 14.4 | board top surface −0.3 |
| J2 RJ45 (MOTOR) | X = 0 (left) | Y = 41.25 | 17.7 × 14.4 | board top surface −0.3 |
| J3 USB-C | X = 100 (right) | Y = 36.5 | 13.0 × 8.0 (overmold-sized) | connector mid-height − 4.0 |
| Side vents | both long walls | X = 30…66, five 4 mm slots at 9 mm pitch | 4 × 6 | 8 mm below base rim |

RJ45 jack faces sit ~2 mm proud of the board edge, so those apertures are
through the 2.2 mm wall plus the 2.0 mm interior clearance — cables plug
straight in; the snug aperture doubles as strain relief, and the two
exterior ears beside each wall accept a cable zip-tie for additional strain
relief. The **USB-C receptacle does NOT overhang** — its face is recessed
~4.2 mm behind the exterior wall face, so its aperture is sized for the
**cable overmold** (typical ≤12 × 6.5 mm), not the plug shell; the overmold
enters the wall and the shell reaches the receptacle at the board edge.

## Lid features (board coordinates)

| Feature | Position | Size |
|---|---|---|
| Light pipe, status LED (green, LED1) | (79.0, 12.97) | Ø3.2 |
| Light pipe, power LED (red, LED2) | (32.5, 44.5) | Ø3.2 |
| EN/reset tool hole (SW1) | (36.0, 5.0) | Ø2.5 |
| BOOT tool hole (SW2) | (78.0, 17.4) | Ø2.5 |
| Lid vents | (40…65, 48) | four 4 × 3 slots |
| Lid screws | 4 corners, (3.5, 3.5) from each outer corner | M3 self-tap, Ø3.4 clearance + countersink, into Ø7 posts with Ø2.5 pilot |
| Registration lip | perimeter ring, 2.0 mm wide × 1.6 mm deep | ring only — interior open (clears the 13.4 mm RJ45s by 1.5 mm); Ø7.6 cutouts where the ring meets the four screw posts |

## Board mounting (base)

| Post | Board hole | Size |
|---|---|---|
| MH1 | (2.9, 26.5) | Ø6 post, Ø2.0 pilot, M2.5 self-tap |
| MH2 | (97.0, 3.0) | same |
| MH3 | (97.0, 52.0) | same |
| Edge ledges | (50, 0), (20, 53.1), (70, 53.1) | 8 × 2 × 3 support ribs |

## Mounting ears

Four exterior ears (two per long side) at 25% / 75% of the enclosure
length: 6 × 12 × 4 mm with a 3 × 5 mm slot — zip-tie to the treadmill frame
near the lower board, or #6 self-tap screws. Keep the **antenna end
pointing away from the metal motor hood**, with a few mm of air behind the
wall (site-survey the BLE RSSI before final placement — see
`firmware/PLAN.md` carried-forward unknowns).

## JLC3DP ordering

Upload both STLs; material **LEDO 6060 resin** (or 8001; PA12 MJF if you
want more toughness), no post-finish. Estimated $6–14 for the pair at 2026
prices; combine shipping with the PCBA order (JLC3DP supports combined
parcels — the parcel ships when the slowest item finishes).
