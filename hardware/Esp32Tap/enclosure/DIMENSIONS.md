# Esp32Tap Rev D enclosure dimensions and modeled validation

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
| J1 footprint anchor / body width | (8.0, 18.0) / 15.48 mm |
| J2 footprint anchor / body width | (8.0, 40.0) / 15.48 mm |
| J3 USB-C center | (91.2, 39.5) mm |
| SW1 / SW2 centers | (42.0, 7.0) / (91.0, 20.0) mm |
| U1 physical antenna edge | 3.3 mm inside board Y=0 |
| U1 antenna span | X=69.0…87.0 mm |
| Mounting centers | (20.0, 6.0), (48.0, 6.0), (92.0, 55.0) mm |

J1 and J2 are the identical Molex 0441440003 right-angle SMD 8P8C RJ45 jack
(LCSC C585890), so their body widths (and every RJ45 dimension below) are
the same value; only the board-Y anchor differs. The mounting locations
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
mating opening facing off the board's X=0 edge (see `gen_pcb.py` `PLACE`).
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
| Body depth (board edge to rear mechanical-tab cap) | 17.17 mm |
| Body height above board | 13.4 mm |
| Aperture (width × height) | 16.0 × 14.0 mm |
| Latch/extraction clearance | 6.0 mm |
| Cable exit direction | Enclosure X-min (both jacks) |

Both jacks' service volumes point toward enclosure X-min: the treadmill's
own RJ45 cable exits outward, then retains an 18 mm minimum bend radius
before reaching the enclosure wall. The service-volume geometry is model
evidence only; delivered plug seating, latch, and extraction checks remain
physical gates.

## Checked mesh evidence

| Mesh | Byte SHA-256 | Canonical geometry SHA-256 | Volume | Bounds |
|---|---|---|---:|---|
| `esp32tap_base.stl` | `424f54d01e6b05ee66f717ebece51dd360dee2268af46a4e0fbe6b8bfd00bc32` | `817efa8de7af36e9306a4a15bfe3b69a1c1e73fc47d7b54a40f1105aa22760c9` | 43,414.399 mm³ | −6…112 × −1.2…83.7 × 0…23.6 mm |
| `esp32tap_lid.stl` | `dceda9ed05d40ae223d6dfe68f464d909a5982b2dfad88c50bbf7296226239c7` | `cd9d13dd46ca09035dad29a626d7c18d2688f8a468ebfe559381c19ba7149160` | 27,060.858 mm³ | 0…104 × 0…83.7 × 0…4.2 mm |

Both meshes are single positive-volume, watertight, winding-consistent
manifolds with exactly two faces per welded edge. Eighty-five occupancy and
boundary probes cover the cavity, RJ45 apertures, USB, switches, mounting
posts, lid posts, snap receivers, and antenna void.
