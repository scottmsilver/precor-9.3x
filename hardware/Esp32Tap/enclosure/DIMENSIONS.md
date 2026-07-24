# Esp32Tap Rev C enclosure dimensions and modeled validation

**Status: HOLD.** The CAD and checked-in meshes pass deterministic model
validation. Actual board/connector fit, delivered-harness strain relief,
wrong-mating attempts, installed bend clearance, RF performance, material
acceptance, and enclosure supply remain physical or vendor gates.

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
| J1 footprint anchor / body width | (12.5, 14.0) / 15.75 mm |
| J2 footprint anchor / body width | (12.5, 40.0) / 18.75 mm |
| J3 USB-C center | (91.2, 39.5) mm |
| SW1 / SW2 centers | (42.0, 7.0) / (91.0, 20.0) mm |
| U1 physical antenna edge | 3.3 mm inside board Y=0 |
| U1 antenna span | X=69.0…87.0 mm |
| Mounting centers | (20.0, 6.0), (48.0, 6.0), (92.0, 55.0) mm |

The mounting locations reproduce the versioned inspection report exactly;
this is modeled geometry, not delivered fit evidence.

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
| Strain relief | Integrated zip-tie bridges for 5.0 mm jackets |
| Closure | Tool-less snap latches; optional supplied M3 fasteners/posts |

The enclosure contains no metal in the antenna void. The external 18 mm
bend envelope is a production routing requirement; it is not represented as
proof that a delivered harness fits an installed treadmill.

## Keyed harness apertures

The selected collar body is 15.6 × 13.6 mm. Each opening is 16.6 × 14.0 mm,
giving 0.5 mm modeled collar clearance per side. A 3.0 mm rib enters a 3.4 × 2.2 mm
slot. Console and Motor slots are offset −5.0 mm and +5.0 mm respectively.
The 10.0 mm separation leaves a 6.6 mm modeled wrong-mating collision margin.

These dimensions encode geometric rejection in CAD. Delivered wrong-harness
attempts remain open physical evidence.

## Mated connector and pigtail service envelopes

The receptacle envelopes are taken from the official Molex
`430250000-SD` Rev A customer drawing for the selected `43025-0800`
(`430250800`) and `43025-1000` (`430251000`) housings:

| Item | 8-circuit J1 | 10-circuit J2 |
|---|---:|---:|
| Nominal housing width A | 12.85 mm | 15.85 mm |
| Maximum modeled width (including 0.25 mm drawing tolerance) | 13.10 mm | 16.10 mm |
| Maximum modeled height | 11.06 mm | 11.06 mm |
| Maximum modeled housing depth | 17.81 mm | 17.81 mm |
| Maximum modeled mated 43025/43020 depth | 25.02 mm | 25.02 mm |
| Straight latch/extraction clearance beyond mated envelope | 6.0 mm | 6.0 mm |

The shared 16.6 mm aperture leaves 0.25 mm on each side of the maximum J2
housing envelope. Both service volumes point toward enclosure X-min:
the factory harness pigtails exit outward, then retain an 18 mm minimum bend
radius for 22 AWG conductors before the integrated 5 mm jacket strain relief.
The service-volume geometry is model evidence only; delivered housing,
latch, extraction, and installed pigtail checks remain physical gates.

Official drawing:
<https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/en-us/salesdrawingpdf/430/43025/430251600_sd.pdf>

## Checked mesh evidence

| Mesh | Byte SHA-256 | Canonical geometry SHA-256 | Volume | Bounds |
|---|---|---|---:|---|
| `esp32tap_base.stl` | `097558cfea5cbc591866f01aae818b1784ca18bfc82a1d21fced37697424d9aa` | `590c8b310cf560c0b337cc2d632e28c96e0bff94f7ae37a5c448508331119b45` | 43,792.682 mm³ | −6…112 × −1.2…83.7 × 0…23.6 mm |
| `esp32tap_lid.stl` | `ccf89dc86528bf850dc80a7aceadf61193741d3e6fecf37d9ca385cc4c513e73` | `cd9d13dd46ca09035dad29a626d7c18d2688f8a468ebfe559381c19ba7149160` | 27,060.858 mm³ | 0…104 × 0…83.7 × 0…4.2 mm |

Both meshes are single positive-volume, watertight, winding-consistent
manifolds with exactly two faces per welded edge. Ninety-three occupancy and
boundary probes cover the cavity, keyed apertures, strain relief, USB,
switches, mounting posts, lid posts, snap receivers, and antenna void.
