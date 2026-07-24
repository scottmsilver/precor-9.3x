# Esp32Tap Rev B enclosure — generated dimensions and validation

**Status: HOLD.** The checked-in meshes are repository-validated, but plug
fit, final material/DFM acceptance, RF performance, and installation clearance
remain physical or vendor gates.

`esp32tap_case.scad` is the parametric source. Both checked-in STLs were
rendered with hard warnings using this immutable image:

```bash
openscad/openscad@sha256:147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37
```

Reproduce and validate them from `hardware/Esp32Tap/enclosure/`:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  openscad/openscad@sha256:147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37 \
  openscad --hardwarnings -D 'part="base"' \
  -o esp32tap_base.stl esp32tap_case.scad
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  openscad/openscad@sha256:147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37 \
  openscad --hardwarnings -D 'part="lid"' \
  -o esp32tap_lid.stl esp32tap_case.scad
python3 validate_enclosure.py
```

## Coordinate convention

Same frame as the PCB (`kicad/Esp32Tap.kicad_pcb`): origin = **board
top-left corner**, +X right (toward USB), +Y toward the board bottom edge.
The board *top* edge (Y=0) is the antenna end. The board corner sits at
interior (2.0, 21.3), comprising the module's 6.3 mm overhang plus the
required 15.0 mm antenna void. Board-underside Z is 5.5 mm (2.5 mm floor +
3.0 mm standoffs).

The corrected RJ45 body centers are **12.445 mm** and **41.445 mm**. They are
not copied from an old drawing: `validate_enclosure.py` derives each from the
centroid of pads 1–8 in the versioned KiCad-inspector JSON. The historical
3.555/32.555 change used the body offset with the wrong sign and is rejected
by the tests.

> **Delivered board vs. assembly panel.** The board that seats in this enclosure is the
> KiCad `Edge.Cuts` outline = **100.0 × 55.0 mm** (verified X 100→200, Y 100→155). If
> JLC's Standard PCBA service panelizes the board with breakaway edge rails as a fab aid,
> those rails **ship attached** and must be snapped/dressed off first — after depanel the
> usable board is 100 × 55 mm, which is what the cavity below is sized for. No 100 × 71 mm
> ("board + rails") panel dimension is a delivered-product dimension.

## Overall

| Item | Value |
|---|---|
| Board (usable, post-depanel) | 100.0 × 55.0 × 1.6 mm |
| Interior cavity | 104.0 × 85.3 × 21.1 mm |
| Outer shell | 109.0 × 90.3 mm; base height 23.6, lid 3.0 (+1.2 lip ring) |
| Wall / floor | 2.5 mm; lid 3.0 mm (thickened for JLC3DP resin thin-wall/warp margin) |
| Under-board clearance | 3.0 mm (THT RJ45 pins ~2 mm) |
| Above-board headroom | 16.5 mm (tallest part: RJ45 13.4 mm → **1.9 mm** clear under the 1.2 mm lid lip ring) |
| Bottom-edge clearance | 9.0 mm (board bottom edge to interior wall) so the two Ø7 bottom lid screw posts clear the PCB corners by **2.25 mm** |
| Antenna end | U1 F.Fab span X=69.0…87.0 mm; module overhangs the board edge 6.3 mm; enclosure leaves a further **15.0 mm** plastic/air void. Lid stays full thickness. Plastic only—no conductive finish, metal-filled resin, or hardware in the RF keepout |
| Lid posts | Ø7.0 mm, centers derived from `post_inset=3.25`; each post overlaps the inner wall face by 0.25 mm; lid relief Ø7.6 mm |

## Wall cutouts (board coordinates)

| Cutout | Wall | Center | Aperture (W × H) | Bottom of aperture |
|---|---|---|---|---|
| J1 RJ45 (CONSOLE) | X = 0 (left) | Y = 12.445 | 17.7 × 14.4 | board top surface −0.3 |
| J2 RJ45 (MOTOR) | X = 0 (left) | Y = 41.445 | 17.7 × 14.4 | board top surface −0.3 |
| J3 USB-C | X = 100 (right) | Y = 36.5 | 13.0 × 8.0 (overmold-sized) | connector mid-height − 4.0 |
| Side vents | both long walls | X = 30…66, five 4 mm slots at 9 mm pitch | 4 × 6 | 8 mm below base rim |

RJ45 jack faces sit ~1.5 mm proud of the board edge (F.Fab-measured 1.53 mm
— the ~2 mm the walls were sized for was slightly optimistic), so the mating
face ends up ~0.5 mm inboard of the interior wall face and ~2.7 mm behind
the exterior face. The apertures are through the 2.5 mm wall plus the
2.0 mm interior clearance, and an 8P8C plug body is far longer than 2.7 mm,
so cables still plug straight in; the snug aperture doubles as strain
relief, and the two
exterior ears beside each wall accept a cable zip-tie for additional strain
relief. The **USB-C receptacle does NOT overhang** — its face is recessed
4.5 mm behind the exterior wall face, so its aperture is sized for the
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
| Lid screws | shell centers X=5.75/103.25, Y=5.75/84.55 mm | M3 self-tap, Ø3.4 clearance + countersink, into Ø7 posts with Ø2.5 pilot |
| Registration lip | perimeter ring, 4.0 mm wide × 1.2 mm deep | widened/shortened from 2.0×1.6 so it is no longer a thin standing ring; interior open (clears the 13.4 mm RJ45s); Ø7.6 cutouts where the ring meets the four screw posts |

## Board mounting (base)

| Post | Board hole | Size |
|---|---|---|
| MH1 | (2.9, 26.5) | Ø6 post, Ø2.0 pilot, M2.5 self-tap |
| MH2 | (97.0, 3.0) | same |
| MH3 | (97.0, 52.0) | same |
| Edge ledges | (50, 0), (20, 53.1), (70, 53.1) | 8 × 2 × 3 support ribs |

## Mounting ears

Four exterior ears (two per long side) at 25% / 75% of the enclosure
length: 8 × 12 × 4 mm with a 3 × 5 mm slot — zip-tie to the treadmill frame
near the lower board, or #6 self-tap screws. Keep the **antenna end
pointing away from the metal motor hood**, with a few mm of air behind the
wall (site-survey the BLE RSSI before final placement — see
`firmware/PLAN.md` carried-forward unknowns).

## Checked mesh evidence

| Mesh | SHA-256 | Welded bodies | Volume | Bounds |
|---|---|---:|---:|---|
| `esp32tap_base.stl` | `4ec0ed81e3127cb441fa7fde67e19e435497c6f442c73ff881d35fcdc3162b77` | 1 | 47,294.952 mm³ | −6…117 × 0…90.3 × 0…23.6 mm |
| `esp32tap_lid.stl` | `b61b33f5d91865cfa4b9e02049225de65f293b3328e116cb94a99d0aae9a3468` | 1 | 30,663.317 mm³ | 0…109 × 0…90.3 × 0…4.2 mm |

The SHA-256 values identify the checked-in ASCII STL bytes. OpenSCAD may
emit the same triangles in a different facet order on another render, so raw
byte hashes are not the reproducibility gate. The validator fresh-renders
both parts with the pinned image and compares canonical geometry digests:
coordinates are rounded to six decimals and facets are sorted independently
of emission order.

Both meshes have positive volume, consistent winding, watertightness, and
exactly two faces incident to every welded edge. The validator also checks
the cavity boundaries, two RJ45 apertures, USB aperture, board posts, lid
posts and reliefs, and the 15 mm antenna void against inspector JSON. These
checks do not authorize an order: obtain current JLC3DP material and DFM
acceptance, then physically verify both intended RJ45 plugs, USB-C overmold
access, RF range, and installed clearances.
