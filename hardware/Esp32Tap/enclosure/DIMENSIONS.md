# Esp32Tap Rev E enclosure dimensions and modeled validation

**Status: HOLD.** The CAD and checked-in meshes pass deterministic model
validation. Actual board/connector fit, delivered plug/latch/extraction
clearance, installed bend clearance, RF performance, material acceptance,
and enclosure supply remain physical or vendor gates.

The source is `esp32tap_case.scad`. Both meshes are rendered with hard
warnings using the immutable image:

```text
openscad/openscad@sha256:147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37
```

Run `python3 validate_enclosure.py` from this directory to inspect the live
KiCad board, render both parts again, compare canonical triangle hashes, and
run the mesh and functional probes.

## Inspector-bound geometry

Coordinates use the PCB outline minimum as `(0, 0)`.

| Item | Inspector-derived value |
|---|---|
| Board | 95.0 × 58.0 × 1.6 mm |
| J1 footprint anchor / body width | (11.9, 40.0) / 15.48 mm |
| J2 footprint anchor / body width | (83.1, 40.0) / 15.48 mm |
| J3 USB-C center | (83.6, 54.2) mm |
| SW1 / SW2 centers | (42.0, 7.0) / (91.0, 20.0) mm |
| U1 physical antenna edge | 3.3 mm inside board Y=0 |
| U1 antenna span | X=69.0…87.0 mm |
| Mounting centers | (20.0, 6.0), (48.0, 6.0), (92.0, 55.0) mm |

J1 and J2 are the identical Molex 0441440003 right-angle SMD 8P8C RJ45 jack
(LCSC C585890), so their body widths (and every RJ45 dimension below) are
the same value; Rev E places one jack per short edge (J1 left, J2 right),
both centred on the same Y=40 axis with each mating face FLUSH with its
board edge (the body extends inboard from the edge). The mounting locations
reproduce the versioned inspection report exactly; this is modeled
geometry, not delivered fit evidence.

## Shell and access geometry

| Item | Modeled value |
|---|---|
| Interior cavity | 99.0 × 78.7 × 21.1 mm |
| Outer shell | 104.0 × 83.7 × 23.6 mm |
| Lid | 104.0 × 83.7 × 3.0 mm, plus 1.2 mm registration lip |
| Wall / floor | 2.5 mm |
| PCB under-clearance / headroom | 3.0 / 16.5 mm |
| Antenna void | 15.0 mm plastic/air from the physical antenna edge |
| USB overmold aperture | 13.0 × 8.0 mm |
| Switch access | Ø2.5 mm tool openings at exact SW1/SW2 centers |
| Connector latch clearance | 6.0 mm modeled straight-access depth |
| Exterior cable bend service envelope | 18.0 mm minimum radius |
| Closure | Tool-less snap latches; optional supplied M3 fasteners/posts |

The enclosure contains no metal in the antenna void. The external 18 mm
bend envelope is a production routing requirement; it is not represented as
proof that a delivered cable fits an installed treadmill.

## RJ45 wall apertures

Both jacks are the identical, unshielded 8P8C part, edge-mounted with the
mating opening facing off a short board edge and the mating face flush
with it, wall + clr = 4.5 mm behind the exterior wall face (Rev E: J1
opens off X=0, J2 off X=95 — see `gen_pcb.py` `PLACE`).
There is no mechanical keying between console and motor any more: both
apertures are identical, straight 16.0 × 14.0 mm openings that clear the
15.48 × 13.4 mm jack body by 0.26 mm on width. CONSOLE/MOTOR silkscreen is
the only differentiator; a mis-plugged cable is a labeling/procedure risk,
not something this CAD rejects.

These apertures are modeled fit only. Delivered plug seating, latch
engagement, and wrong-cable attempts remain open physical evidence.

## RJ45 plug service envelope

The jack (Molex 0441440003 / LCSC C585890) mechanical envelope:

| Item | Value |
|---|---|
| Body width | 15.48 mm |
| Body depth (mating face at the board edge to the inboard rear mechanical-tab cap) | 17.17 mm |
| Body height above board | 13.4 mm |
| Aperture (width × height) | 16.0 × 14.0 mm |
| Latch/extraction clearance | 6.0 mm |
| Cable exit direction | J1: enclosure X-min; J2: enclosure X-max |

Each jack's service volume points outward through its own wall (J1
toward enclosure X-min, J2 toward X-max): each treadmill RJ45 cable exits
outward, then retains an 18 mm minimum bend radius before reaching the
enclosure wall. The service-volume geometry is model
evidence only; delivered plug seating, latch, and extraction checks remain
physical gates.

## Checked mesh evidence

| Mesh | Byte SHA-256 | Canonical geometry SHA-256 | Volume | Bounds |
|---|---|---|---:|---|
| `esp32tap_base.stl` | `1396a586083f3b1b823eb8254922180e8e63e05e15c8c02b79f6e50b3b2ed718` | `1e109aedba97785dc65e21c9f52fb7e7914b50d702fcc56d099f05c5f2c05bc1` | 43,414.399 mm³ | −6…112 × −1.2…83.7 × 0…23.6 mm |
| `esp32tap_lid.stl` | `8b8c0d8ccc043cdd017fe8340ea51cf5c1c8178e053da94f9642c11eed06a26f` | `39d5f1f75a25ca9b5d6e3328556357d34c5ae0d24bd0a58713c86effb5d900da` | 27,060.858 mm³ | 0…104 × 0…83.7 × 0…4.2 mm |

Both meshes are single positive-volume, watertight, winding-consistent
manifolds with exactly two faces per welded edge. Eighty-five occupancy and
boundary probes cover the cavity, RJ45 apertures, USB, switches, mounting
posts, lid posts, snap receivers, and antenna void.
