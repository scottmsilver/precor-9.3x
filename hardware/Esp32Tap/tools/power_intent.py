"""Exact emitted-copper intent for the redundant 2 A pass-through paths.

Rev E: one RJ45 per short edge, both centred on Y=37.0, with the mating
opening flush with its board edge.  The jack's pad row therefore sits
~17.8 mm INBOARD of each edge (J1 pads at X=17.8, J2 pads at X=77.2) and
the entry clusters live UNDER the jack bodies: the via arrays march from
each pad row toward its own board edge, in the otherwise-empty space
beneath the plastic shell (only the SMD mechanical-tab pads 9/10 are
there).  Both clusters share the same architecture: RAW via triplets per
contact plus a vertical B.Cu bus, and GND via triplets per contact tied
row-to-row by an In1 rowtie plus F.Cu branches meeting between the tab
bands.  The long haul uses:

* ``+8V_RAW``: a wide B.Cu trunk through the mid-board corridor between the
  supervisor-cluster escape rows (y~26.5) and the TP escape row (y=32.8),
  paralleled by 1.9 mm In2.Cu twins that terminate only on through-via
  centres (never dangling).
* ``GND``: five 1.98 mm In1.Cu strips.  In1 is the solid GND plane layer, so
  explicit GND tracks there are legal, merge with the plane, and cost no
  routing congestion.  The strips run endpoint-to-endpoint between the left
  and right GND via rows; the drop proof deliberately still ignores the
  plane itself and counts only this explicit copper (conservative).

All widths on In1/In2 stay below 2.0 mm so the locked wide-track layer
policy (wide +8V_RAW = B.Cu only, wide GND = F.Cu only) is preserved.
"""

from __future__ import annotations

from typing import Iterable

Point = tuple[float, float]

VIA_BARREL_ASSUMPTION = {
    "plating_thickness_um": 20.0,
    "class": "IPC-6012 Class 2",
    "evidence": (
        "https://jlcpcb.com/blog/pcb-pth",
        "https://www.ipc.org/TOC/IPC-6012F-TOC.pdf",
    ),
    "basis": (
        "JLCPCB states a 20 um minimum average PTH barrel copper thickness "
        "for IPC-6012 Class 2; IPC-6012F Table 3-10 is the governing hole "
        "copper plating requirement."
    ),
    "live_quote_dfm_confirmation_required": True,
}


def _path(
    identifier: str,
    net: str,
    layer: str,
    width_mm: float,
    points: Iterable[Point],
) -> dict[str, object]:
    return {
        "id": identifier,
        "net": net,
        "layer": layer,
        "width_mm": width_mm,
        "points": tuple(points),
    }


# J1 (left edge, rotation 270): pads 1-8 sit at board X=17.8, Y = 37.0 +
# pad local offset (pad 1 at the TOP of the row, Y=32.55).  J2 (right edge,
# rotation 90): pads 1-8 sit at board X=77.2, Y = 37.0 - pad local offset
# (pad 1 at the BOTTOM, Y=41.45).  The mating faces are flush with the
# board edges, so all entry copper lives beneath the jack shells.  Pads
# 9/10 are each jack's large SMD mechanical (NC) tab pads: J1's occupy
# X 3.395-8.605, Y 29.38-31.92 (pad 10) and Y 42.08-44.62 (pad 9); J2's
# are the x-mirror at X 86.395-91.605.  Contact stubs run at their own
# pad's exact Y; via arrays march from the pad row toward the board edge.
RAW_ARRAYS = {
    "J1.2": ((16.75, 33.82), (15.75, 33.82), (14.75, 33.82)),
    "J1.8": ((16.75, 41.45), (15.75, 41.45), (14.75, 41.45)),
    "J2.2": ((78.25, 40.18), (79.25, 40.18), (80.25, 40.18)),
    "J2.8": ((78.25, 32.55), (79.25, 32.55), (80.25, 32.55)),
}
RAW_CONTACTS = {
    "J1.2": (17.8, 33.82),
    "J1.8": (17.8, 41.45),
    "J2.2": (77.2, 40.18),
    "J2.8": (77.2, 32.55),
}
FUSE_ARRAY = ((17.4, 50.0), (18.4, 50.0), (19.4, 50.0))
FUSE_CONTACT = (18.3625, 52.5)

# GND via clusters: 1.4/1.0 mm vias 1.5 mm apart, boxed between the board
# edge/tab pads and the signal-escape via column on each side (exact
# x-mirror on the right: 95 - {9.9, 11.4, 12.9} = {85.1, 83.6, 82.1}).
# X >= 9.9 keeps the 1.4 mm barrels clear of the SMD tab-pad copper
# (X <= 8.555, 0.2 mm clearance needs X >= 9.455).
GROUND_ARRAYS = {
    "J1.1": ((12.9, 32.55), (11.4, 32.55), (9.9, 32.55)),
    "J1.7": ((12.9, 40.17), (11.4, 40.17), (9.9, 40.17)),
    "J2.1": ((82.1, 41.45), (83.6, 41.45), (85.1, 41.45)),
    "J2.7": ((82.1, 33.83), (83.6, 33.83), (85.1, 33.83)),
}
GROUND_CONTACTS = {
    "J1.1": (17.8, 32.55),
    "J1.7": (17.8, 40.17),
    "J2.1": (77.2, 41.45),
    "J2.7": (77.2, 33.83),
}
# Both connectors use the same branch scheme: each contact's F.Cu
# bus-branch runs from its OUTERMOST via straight to a per-connector
# meeting point between the two pad rows (clear of both mechanical-tab Y
# bands).  Using the outer column keeps the under-body escape boxes open:
# J2's four signal escapes need vias out to x~83.2, which a branch/rowtie
# column at x=83.6 would forbid.
GROUND_MEET = {
    "J1": (9.9, 37.0),
    "J2": (85.1, 37.0),
}

# +8V_RAW long-haul trunk (B.Cu primary).  Corridor: y=29.9, necked to
# 4.0 mm through the supervisor x 43-60 escape rows (4.4 mm east keeps the
# TP escape row at y=32.8 legal: via clearance 0.3+2.2+0.2=2.7 <= 2.9).
# The west end lands on the left vertical bus (x=15.45) and the east end
# on the right vertical bus (x=79.55), both under the jack bodies.
TRUNK_WEST = ((15.45, 29.9), (43.0, 29.9))
TRUNK_NECK = ((43.0, 29.9), (60.0, 29.9))
TRUNK_EAST = ((60.0, 29.9), (79.55, 29.9))
TRUNK_CONN = ((79.55, 29.9), (79.55, 32.55))
# In2 twins terminate ON via centres: the left twin ties J1.8's centre via
# (15.75, 41.45) through J1.2's centre via (15.75, 33.82) to a dedicated
# tie via at the west end of the neck; the right twin ties the J2.2 and
# J2.8 centre vias to a tie via that sits mid-track on the trunk
# connector at (79.55, 31.0) (mid-track, not on a segment endpoint, so
# KiCad's endpoint-on-via centering check stays quiet).
# The left twin deliberately stops at the west end of the neck: the neck
# region (x 43-60) is the router's main north-south crossing window for the
# supervisor/TP nets, so In2 must stay open there.
TWIN_LEFT_POINTS = (
    (15.75, 41.45),
    (15.75, 33.82),
    (15.75, 29.9),
    (43.0, 29.9),
)
# Second west-section In2 twin: branches off the primary twin at same-layer
# Y-junctions (no extra vias), one lane north of the trunk centreline.
TWIN_WEST_AUX_POINTS = (
    (17.0, 29.9),
    (19.0, 28.1),
    (41.0, 28.1),
    (43.0, 29.9),
)
TWIN_RIGHT_POINTS = (
    (79.55, 31.0),
    (79.25, 32.55),
    (79.25, 40.18),
)
TRUNK_TIE_VIAS = ((43.0, 29.9), (79.55, 31.0))

# GND long-haul: five 1.98 mm In1.Cu strips plus one In1 rowtie per
# connector.  Endpoints are always GND via centres.  Lanes keep their
# qualified Rev E interiors (A/B ride the fixture corridor latitudes
# 34.4 / 36.4, D/F ride south lanes 40.8 / 43.0 with D dodging the locked
# R22.1 escape at (43.6, 40.8) and F dodging the U4.5 VIN escape, E rides
# the +8V trunk corridor with a jog around the (43.0, 29.9) tie via).
# The cluster approaches thread the under-jack geometry: on the left, E
# exits north past the tab-10 pad while the A/F pair (spine-af, y=43.2)
# and the B/D pair (spine-bd, y=45.4) exit south below the tab-9 pad
# before fanning to their lanes -- each shared exit is emitted ONCE as
# its own spine route (never as duplicated per-strip copper, which would
# both double-count in the drop proof and break the exact intent-id ->
# board-track mapping).  On the right, A enters around the tab-10 pad
# from the north-east; B terminates as a T-junction on A's tail at
# (79.4, 34.4) so the J2 signal-escape via column at x~81.2, y 36-39
# stays clear; E ducks under the tab at y=28.1; and D/F approach the
# J2.1 via row from the south-east.
GROUND_STRIP_WIDTH = 1.98
GROUND_STRIPS = {
    "gnd-spine-af": (
        (9.9, 40.17),
        (9.9, 43.2),
        (19.5, 43.2),
    ),
    "gnd-spine-bd": (
        (11.4, 40.17),
        (11.4, 45.4),
        (20.0, 45.4),
    ),
    "gnd-strip-a": (
        # The east tail rides y=35.6 (not 34.4): >=1.69 mm from the
        # J2.7 via row at y=33.83 so the same-net crossing stays either
        # clear of the barrels or exactly on their centreline.
        (19.5, 43.2),
        (24.0, 34.4),
        (75.8, 34.4),
        (77.0, 35.6),
        (87.6, 35.6),
        (87.6, 33.83),
        (82.1, 33.83),
    ),
    "gnd-strip-b": (
        (20.0, 45.4),
        (25.5, 36.4),
        (77.0, 36.4),
        (78.6, 35.6),
    ),
    "gnd-strip-d": (
        (20.0, 45.4),
        (23.0, 40.8),
        (40.0, 40.8),
        (41.6, 38.8),
        (45.6, 38.8),
        (47.2, 40.8),
        (75.5, 40.8),
        (77.5, 43.6),
        (82.1, 41.45),
    ),
    "gnd-strip-e": (
        (9.9, 32.55),
        (9.9, 29.9),
        (41.2, 29.9),
        (42.0, 31.5),
        (44.0, 31.5),
        (44.8, 29.9),
        (77.0, 29.9),
        (77.0, 28.1),
        (85.1, 28.1),
        (85.1, 33.83),
    ),
    "gnd-rowtie-left": (
        (9.9, 32.55),
        (9.9, 40.17),
    ),
    "gnd-rowtie-right": (
        (85.1, 33.83),
        (85.1, 41.45),
    ),
    "gnd-strip-f": (
        (19.5, 43.2),
        (21.0, 43.0),
        (32.6, 43.0),
        (33.4, 44.6),
        (38.6, 44.6),
        (39.4, 43.0),
        (80.0, 43.0),
        (82.1, 41.45),
        (85.1, 41.45),
    ),
}


def _routes() -> tuple[dict[str, object], ...]:
    routes: list[dict[str, object]] = []
    for contact, vias in RAW_ARRAYS.items():
        bus_x = 15.45 if contact.startswith("J1") else 79.55
        routes.extend(
            (
                _path(
                    f"raw-{contact}-contact",
                    "+8V_RAW",
                    "F.Cu",
                    1.0,
                    (RAW_CONTACTS[contact], vias[1]),
                ),
                _path(
                    f"raw-{contact}-front-array",
                    "+8V_RAW",
                    "F.Cu",
                    1.0,
                    vias,
                ),
                # 1.0 mm on B.Cu: the adjacent GND contact's via sits
                # 1.27/1.28 mm away in Y with a 0.8 mm diameter; 1.0 mm
                # keeps >=0.37 mm clearance from that via while still
                # giving >2 A/20C IPC-2152 headroom.
                _path(
                    f"raw-{contact}-bottom-array",
                    "+8V_RAW",
                    "B.Cu",
                    1.0,
                    vias,
                ),
                # Short jog from the centre via to the vertical bus at
                # X=15.45 (left) / X=79.55 (right).
                _path(
                    f"raw-{contact}-bus-branch",
                    "+8V_RAW",
                    "B.Cu",
                    1.0,
                    (vias[1], (bus_x, vias[1][1])),
                ),
            )
        )
    routes.extend(
        (
            _path(
                "raw-fuse-contact",
                "+8V_RAW",
                "F.Cu",
                1.2,
                (FUSE_CONTACT, FUSE_ARRAY[1]),
            ),
            _path(
                "raw-fuse-front-array",
                "+8V_RAW",
                "F.Cu",
                1.2,
                FUSE_ARRAY,
            ),
            _path(
                "raw-fuse-bottom-array",
                "+8V_RAW",
                "B.Cu",
                2.0,
                FUSE_ARRAY,
            ),
            # Left vertical bus: the trunk junction, J1's two contacts,
            # and the fuse.
            _path(
                "raw-main-bus",
                "+8V_RAW",
                "B.Cu",
                2.0,
                (
                    (15.45, 29.9),
                    (15.45, 33.82),
                    (15.45, 41.45),
                    (15.45, 50.0),
                    FUSE_ARRAY[1],
                ),
            ),
            # Right vertical bus: J2's two contacts (the trunk connector
            # lands on its north end at (79.55, 32.55)).
            _path(
                "raw-right-bus",
                "+8V_RAW",
                "B.Cu",
                2.0,
                (
                    (79.55, 32.55),
                    (79.55, 40.18),
                ),
            ),
            # Full-board trunk (B.Cu) with the supervisor-row neck.
            _path("raw-trunk-west", "+8V_RAW", "B.Cu", 4.5, TRUNK_WEST),
            _path("raw-trunk-neck", "+8V_RAW", "B.Cu", 4.0, TRUNK_NECK),
            _path("raw-trunk-east", "+8V_RAW", "B.Cu", 4.4, TRUNK_EAST),
            _path("raw-trunk-conn", "+8V_RAW", "B.Cu", 2.0, TRUNK_CONN),
            # In2 twins (sub-2.0 mm to preserve the wide-track layer
            # policy), terminating on via centres at both ends.
            _path("raw-twin-left", "+8V_RAW", "In2.Cu", 1.9, TWIN_LEFT_POINTS),
            _path("raw-twin-right", "+8V_RAW", "In2.Cu", 1.9, TWIN_RIGHT_POINTS),
            _path("raw-twin-west-aux", "+8V_RAW", "In2.Cu", 1.9, TWIN_WEST_AUX_POINTS),
        )
    )
    for contact, vias in GROUND_ARRAYS.items():
        # The "contact" stub runs in the 1.27 mm pad-pitch lane shared with
        # the adjacent +8V_RAW contact; 1.0 mm keeps >=0.2 mm from the
        # neighbouring pad copper on both sides.  It runs to the OUTERMOST
        # via, crossing the inner two exactly on their centrelines.
        routes.extend(
            (
                _path(
                    f"gnd-{contact}-contact",
                    "GND",
                    "F.Cu",
                    1.0,
                    (GROUND_CONTACTS[contact], vias[-1]),
                ),
                _path(
                    f"gnd-{contact}-narrow-array",
                    "GND",
                    "F.Cu",
                    1.0,
                    vias,
                ),
                _path(
                    f"gnd-{contact}-wide-array",
                    "GND",
                    "F.Cu",
                    1.4,
                    vias,
                ),
            )
        )
        # Both branches jog from the outermost via to the per-connector
        # meeting point between the tab bands (a pure vertical: the meet
        # shares the outer vias' X).
        branch = (
            vias[-1],
            GROUND_MEET[contact.split(".")[0]],
        )
        routes.append(
            _path(
                f"gnd-{contact}-bus-branch",
                "GND",
                "F.Cu",
                2.0,
                branch,
            )
        )
    for identifier, points in GROUND_STRIPS.items():
        routes.append(
            _path(
                identifier,
                "GND",
                "In1.Cu",
                GROUND_STRIP_WIDTH,
                points,
            )
        )
    return tuple(routes)


ROUTES = _routes()
# Via sizing: +8V_RAW's vias sit 1.27 mm from the adjacent GND contact's
# pad row -- 0.8/0.4 mm keeps clearance there.  GND's vias sit in their own
# clusters and use the full 1.4/1.0 mm size that the conservative
# single-via-carries-2A IPC-2152 envelope needs.
_VIA_SIZE_MM = {"+8V_RAW": 0.8, "GND": 1.4}
_VIA_DRILL_MM = {"+8V_RAW": 0.4, "GND": 1.0}
VIAS = (
    tuple(
        {
            "id": f"{net.lower()}-{contact}-via-{index}",
            "net": net,
            "at": point,
            "size_mm": _VIA_SIZE_MM[net],
            "drill_mm": _VIA_DRILL_MM[net],
        }
        for net, arrays in (("+8V_RAW", RAW_ARRAYS), ("GND", GROUND_ARRAYS))
        for contact, points in arrays.items()
        for index, point in enumerate(points)
    )
    + tuple(
        {
            "id": f"raw-fuse-via-{index}",
            "net": "+8V_RAW",
            "at": point,
            "size_mm": 0.8,
            "drill_mm": 0.4,
        }
        for index, point in enumerate(FUSE_ARRAY)
    )
    + tuple(
        {
            "id": f"raw-trunk-tie-via-{index}",
            "net": "+8V_RAW",
            "at": point,
            "size_mm": 0.8,
            "drill_mm": 0.4,
        }
        for index, point in enumerate(TRUNK_TIE_VIAS)
    )
)


def track_segments(origin: Point = (0.0, 0.0)) -> tuple[dict[str, object], ...]:
    """Expand paths into exact segment signatures in the requested datum."""
    segments: list[dict[str, object]] = []
    for route in ROUTES:
        points = route["points"]
        assert isinstance(points, tuple)
        for index, (start, end) in enumerate(zip(points, points[1:])):
            segments.append(
                {
                    "intent_id": f"{route['id']}:{index}",
                    "net": route["net"],
                    "layer": route["layer"],
                    "width_mm": route["width_mm"],
                    "start": (
                        round(start[0] + origin[0], 6),
                        round(start[1] + origin[1], 6),
                    ),
                    "end": (
                        round(end[0] + origin[0], 6),
                        round(end[1] + origin[1], 6),
                    ),
                }
            )
    return tuple(segments)


def via_signatures(origin: Point = (0.0, 0.0)) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            **via,
            "at": (
                round(via["at"][0] + origin[0], 6),
                round(via["at"][1] + origin[1], 6),
            ),
        }
        for via in VIAS
    )
