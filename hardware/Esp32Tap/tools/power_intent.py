"""Exact emitted-copper intent for the redundant 2 A pass-through paths.

Rev E: J2 (MOTOR) moved to the right short edge (opening +X), so the +8V_RAW
and GND pass-through now span the full board.  The left entry cluster keeps
its Rev D geometry; the right entry cluster is its exact x-mirror (x -> 95-x)
with the pad-row top/bottom flip that rotation 270 produces.  The long haul
uses:

* ``+8V_RAW``: a wide B.Cu trunk through the mid-board corridor between the
  supervisor-cluster escape rows (y~26.5) and the TP escape row (y=32.8),
  paralleled by 1.8 mm In2.Cu twins that terminate only on through-via
  centres (never dangling).
* ``GND``: five 1.9 mm In1.Cu strips.  In1 is the solid GND plane layer, so
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


# J1 (left edge, rotation 90): pads 1-8 sit at board X=2.1, Y = 15.0 + pad
# local offset.  J2 (right edge, rotation 270): pads 1-8 sit at board
# X=92.9, Y = 37.0 - pad local offset -- note the top/bottom flip versus the
# old left-edge J2 (pad 1 is now the TOP of its row at Y=32.55).  Pads 9/10
# are each jack's large mechanical (NC) tab pads; J2's occupy X 78.5-83.7,
# Y 29.38-31.92 (pad 10) and Y 42.08-44.62 (pad 9).  Contact stubs run at
# their own pad's exact Y; the GND bus-branches jog to Y=37.0 (J2) which
# clears both tab bands.
RAW_ARRAYS = {
    "J1.2": ((4.0, 18.18), (5.0, 18.18), (6.0, 18.18)),
    "J1.8": ((4.0, 10.55), (5.0, 10.55), (6.0, 10.55)),
    "J2.2": ((91.0, 33.82), (90.0, 33.82), (89.0, 33.82)),
    "J2.8": ((91.0, 41.45), (90.0, 41.45), (89.0, 41.45)),
}
RAW_CONTACTS = {
    "J1.2": (2.1, 18.18),
    "J1.8": (2.1, 10.55),
    "J2.2": (92.9, 33.82),
    "J2.8": (92.9, 41.45),
}
FUSE_ARRAY = ((17.4, 50.0), (18.4, 50.0), (19.4, 50.0))
FUSE_CONTACT = (18.3625, 52.5)

# GND via clusters: 1.4/1.0 mm vias 1.5 mm apart, boxed between the +8V_RAW
# via cluster and the mechanical-tab band on each side (exact x-mirror on
# the right: 95 - {7.35, 8.85, 10.35} = {87.65, 86.15, 84.65}).
GROUND_ARRAYS = {
    "J1.1": ((7.35, 19.45), (8.85, 19.45), (10.35, 19.45)),
    "J1.7": ((7.35, 11.83), (8.85, 11.83), (10.35, 11.83)),
    "J2.1": ((87.65, 32.55), (86.15, 32.55), (84.65, 32.55)),
    "J2.7": ((87.65, 40.17), (86.15, 40.17), (84.65, 40.17)),
}
GROUND_CONTACTS = {
    "J1.1": (2.1, 19.45),
    "J1.7": (2.1, 11.83),
    "J2.1": (92.9, 32.55),
    "J2.7": (92.9, 40.17),
}
# Safe crossing Y for each connector's GND bus-branch: clear of both the
# pad-9 and pad-10 mechanical-tab Y bands.
GROUND_SAFE_Y = {
    "J1.1": 15.0,
    "J1.7": 15.0,
    "J2.1": 37.0,
    "J2.7": 37.0,
}
# Left side only: the contact that carries the shared bus-branch tail past
# its paired contact's identical-safe_y meeting point.  On the right the two
# branches simply meet at RIGHT_GROUND_MEET (no onward bus exists there).
TRUNK_GROUND_CONTACTS = {"J1.1"}
RIGHT_GROUND_CONTACTS = {"J2.1", "J2.7"}
RIGHT_GROUND_MEET = (84.0, 37.0)

# +8V_RAW long-haul trunk (B.Cu primary).  Corridor: y=28.5 west of the
# supervisor cluster (necked to 3.2 mm through its x 43-60 escape rows),
# then y=29.9 east of it (4.0 mm keeps the TP escape row at y=32.8 legal:
# via clearance 0.3+2.0+0.2=2.5 <= 2.9).
TRUNK_WEST = ((5.3, 29.9), (43.0, 29.9))
TRUNK_NECK = ((43.0, 29.9), (60.0, 29.9))
TRUNK_EAST = ((60.0, 29.9), (82.8, 29.9))
TRUNK_CONN = ((82.8, 29.9), (89.7, 29.9), (89.7, 33.82))
# In2 twins terminate ON via centres: J1.8's centre via (5.0, 10.55) through
# J1.2's centre via (5.0, 18.18) to a dedicated tie via at the east end of
# the neck; the right twin ties the J2.2 and J2.8 centre vias to a tie via
# at the west end of the trunk connector.
# The left twin deliberately stops at the west end of the neck: the neck
# region (x 43-60) is the router's main north-south crossing window for the
# supervisor/TP nets, so In2 must stay open there.
TWIN_LEFT_POINTS = (
    (5.0, 10.55),
    (5.0, 18.18),
    (5.0, 29.9),
    (43.0, 29.9),
)
# Second west-section In2 twin: branches off the primary twin at same-layer
# Y-junctions (no extra vias), one lane north of the trunk centreline.
TWIN_WEST_AUX_POINTS = (
    (12.0, 29.9),
    (14.0, 28.1),
    (41.0, 28.1),
    (43.0, 29.9),
)
TWIN_RIGHT_POINTS = (
    (84.4, 29.9),
    (89.7, 29.9),
    (89.7, 33.82),
    (90.0, 33.82),
    (90.0, 41.45),
)
TRUNK_TIE_VIAS = ((43.0, 29.9), (84.4, 29.9))

# GND long-haul: five 1.9 mm In1.Cu strips.  Endpoints are always GND via
# centres.  Lanes were chosen against the router's fixed obstacles: A/B ride
# the fixture corridor latitudes (34.4 / 36.4), D/F ride south lanes (40.8 /
# 43.0, with D dodging the locked R22.1 escape at (43.6, 40.8)), and E rides
# the +8V trunk corridor itself on In1 (the trunk's own via exclusion zone
# shelters it; 28.3 east of x=58 clears the twin tie via at (61.4, 29.9)).
GROUND_STRIP_WIDTH = 1.98
GROUND_STRIPS = {
    "gnd-strip-a": (
        (7.35, 19.45),
        (14.0, 34.4),
        (77.0, 34.4),
        (80.9, 32.55),
        (84.65, 32.55),
    ),
    "gnd-strip-b": (
        (10.35, 19.45),
        (17.0, 36.4),
        (77.0, 36.4),
        (82.6, 36.4),
        (86.15, 32.55),
    ),
    "gnd-strip-d": (
        (10.35, 11.83),
        (19.6, 39.2),
        (21.0, 40.8),
        (40.0, 40.8),
        (41.6, 38.8),
        (45.6, 38.8),
        (47.2, 40.8),
        (80.0, 40.8),
        (82.5, 40.17),
        (84.65, 40.17),
    ),
    "gnd-strip-e": (
        (10.35, 11.83),
        (10.35, 20.0),
        (16.4, 29.9),
        (41.2, 29.9),
        (42.0, 31.5),
        (44.0, 31.5),
        (44.8, 29.9),
        (80.0, 29.9),
        (83.4, 32.55),
        (84.65, 32.55),
    ),
    "gnd-rowtie-right": (
        (86.15, 32.55),
        (86.15, 40.17),
    ),
    "gnd-strip-f": (
        (8.85, 11.83),
        (8.85, 19.45),
        (20.5, 43.0),
        (32.6, 43.0),
        (33.4, 44.6),
        (38.6, 44.6),
        (39.4, 43.0),
        (80.0, 43.0),
        (86.15, 41.9),
        (86.15, 40.17),
    ),
}
# The left gnd-main-bus keeps its Rev D role for J1 and now ends on a
# dedicated stitch via that sits on strip B's centreline, so the wide F.Cu
# GND track terminates on copper instead of dangling where J2 used to be.
GND_BUS_END = (20.8, 36.4)


def _routes() -> tuple[dict[str, object], ...]:
    routes: list[dict[str, object]] = []
    for contact, vias in RAW_ARRAYS.items():
        bus_x = 5.3 if contact.startswith("J1") else 89.7
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
                # X=5.3 (left) / X=89.7 (right).
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
            # Left vertical bus: J1's two contacts and the fuse.
            _path(
                "raw-main-bus",
                "+8V_RAW",
                "B.Cu",
                2.0,
                (
                    (5.3, 10.55),
                    (5.3, 18.18),
                    (5.3, 50.0),
                    FUSE_ARRAY[1],
                ),
            ),
            # Right vertical bus: J2's two contacts.
            _path(
                "raw-right-bus",
                "+8V_RAW",
                "B.Cu",
                2.0,
                (
                    (89.7, 33.82),
                    (89.7, 41.45),
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
        # neighbouring pad copper on both sides.
        routes.extend(
            (
                _path(
                    f"gnd-{contact}-contact",
                    "GND",
                    "F.Cu",
                    1.0,
                    (GROUND_CONTACTS[contact], vias[1]),
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
        safe_y = GROUND_SAFE_Y[contact]
        if contact in RIGHT_GROUND_CONTACTS:
            # Right side: both branches jog from the centre via to the
            # shared meeting point between the tab bands.
            branch = (
                vias[1],
                RIGHT_GROUND_MEET,
            )
        elif contact in TRUNK_GROUND_CONTACTS:
            branch = (
                vias[1],
                (11.0, safe_y),
                (16.0, safe_y),
                (20.8, safe_y),
            )
        else:
            branch = (
                vias[1],
                (11.0, safe_y),
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
    routes.append(
        _path(
            "gnd-main-bus",
            "GND",
            "F.Cu",
            2.0,
            (
                (20.8, 15.0),
                GND_BUS_END,
            ),
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
    + (
        {
            "id": "gnd-bus-stitch-via",
            "net": "GND",
            "at": GND_BUS_END,
            "size_mm": 1.4,
            "drill_mm": 1.0,
        },
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
