"""Exact emitted-copper intent for the redundant 2 A pass-through paths."""

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


# Rev D: J1/J2 are now the Molex 441440003 right-angle SMD RJ45 (LCSC
# C585890), an 8-in-a-row, 1.27 mm pitch pad field (pads 1-8 all sit at
# local X=2.1 mm, board Y = footprint Y +/- the pin's row offset) instead
# of Rev C's two-row Micro-Fit header.  Pads 9/10 are the jack's large
# mechanical (NC) ground-tab pads at local X=13.9 mm; J1's occupy
# Y 6.05-11.25 (pad 9) and Y 18.75-23.95 (pad 10), J2's the same offsets
# from its own center.  Every contact stub below runs at its own pad's
# exact Y out to X=6.0 (never entering the X=12.6-15.2 tab band), so nine
# same-pitch neighbours can fan out without a clearance violation; the
# GND bus-branch then jogs to a Y that clears both tabs (J1: Y=15.0 sits
# between the two bands; J2: Y=37.0 sits between its own two bands)
# before crossing the tab-width X window on its way to the shared bus.
RAW_ARRAYS = {
    "J1.2": ((4.0, 18.18), (5.0, 18.18), (6.0, 18.18)),
    "J1.8": ((4.0, 10.55), (5.0, 10.55), (6.0, 10.55)),
    "J2.2": ((4.0, 40.18), (5.0, 40.18), (6.0, 40.18)),
    "J2.8": ((4.0, 32.55), (5.0, 32.55), (6.0, 32.55)),
}
RAW_CONTACTS = {
    "J1.2": (2.1, 18.18),
    "J1.8": (2.1, 10.55),
    "J2.2": (2.1, 40.18),
    "J2.8": (2.1, 32.55),
}
FUSE_ARRAY = ((17.4, 50.0), (18.4, 50.0), (19.4, 50.0))
FUSE_CONTACT = (18.3625, 52.5)

# Rev D: GND's vias sit at X=7.35-10.35, not X=4-6 like +8V_RAW's (below)
# -- see the VIAS note on via size. The mechanical-tab pads' real
# fabrication bbox starts at X=11.295 (not the courtyard's X=12.6 the
# module note above still uses for the *trace* crossing further out), so
# this cluster is boxed into the 4.895 mm window between +8V_RAW's via
# edge (X=6.4) and the tab edge: 1.5 mm pitch (matching Rev C's original
# spacing) x3 vias plus the 1.4 mm via diameter needs 4.4 mm of that,
# leaving ~0.25 mm clearance on each side. This lets GND use the full
# 1.4/1.0 mm via (matching Rev C's IPC-2152 margin) instead of a
# pitch-constrained smaller one. The connecting F.Cu contact/array traces
# are still only 0.6/0.8 mm through the tight pad lane (X=2.1-7.35ish);
# only the via itself needed the extra room.
GROUND_ARRAYS = {
    "J1.1": ((7.35, 19.45), (8.85, 19.45), (10.35, 19.45)),
    "J1.7": ((7.35, 11.83), (8.85, 11.83), (10.35, 11.83)),
    "J2.1": ((7.35, 41.45), (8.85, 41.45), (10.35, 41.45)),
    "J2.7": ((7.35, 33.83), (8.85, 33.83), (10.35, 33.83)),
}
GROUND_CONTACTS = {
    "J1.1": (2.1, 19.45),
    "J1.7": (2.1, 11.83),
    "J2.1": (2.1, 41.45),
    "J2.7": (2.1, 33.83),
}
# Safe crossing Y for each connector's GND bus-branch: clear of both the
# pad-9 and pad-10 mechanical-tab Y bands (see module note above).
GROUND_SAFE_Y = {
    "J1.1": 15.0,
    "J1.7": 15.0,
    "J2.1": 37.0,
    "J2.7": 37.0,
}
# The contact that carries the shared bus-branch tail past its paired
# contact's identical-safe_y meeting point (see the bus-branch note below).
TRUNK_GROUND_CONTACTS = {"J1.1", "J2.1"}


def _routes() -> tuple[dict[str, object], ...]:
    routes: list[dict[str, object]] = []
    for contact, vias in RAW_ARRAYS.items():
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
                # Rev D: 1.0 mm on B.Cu — the adjacent GND contact's via
                # sits 1.27/1.28 mm away in Y with a 0.8 mm diameter (see
                # VIAS below); 1.0 mm keeps >=0.37 mm clearance from that
                # via while still giving >2 A/20C IPC-2152 headroom (a
                # 2.0 mm Rev C trace fit the old, much wider Micro-Fit
                # pitch but would violate clearance against this via).
                _path(
                    f"raw-{contact}-bottom-array",
                    "+8V_RAW",
                    "B.Cu",
                    1.0,
                    vias,
                ),
                # X=5.3, inside +8V_RAW's own via cluster (X=4-6): GND's
                # via cluster now sits at X=7.35-10.35 (see module note),
                # only 1.35 mm from the nearest edge of this cluster at
                # X=6 -- a 2.0 mm-wide B.Cu main bus needs to stay close
                # to X=4-6 (its own vias, no clearance concern) rather
                # than reaching toward X=6.5+, which eats into the 0.7 mm
                # GND via radius' clearance budget from only 1.35 mm away.
                _path(
                    f"raw-{contact}-bus-branch",
                    "+8V_RAW",
                    "B.Cu",
                    1.0,
                    (vias[1], (5.3, vias[1][1])),
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
            _path(
                "raw-main-bus",
                "+8V_RAW",
                "B.Cu",
                2.0,
                (
                    (5.3, 10.55),
                    (5.3, 18.18),
                    (5.3, 32.55),
                    (5.3, 40.18),
                    (5.3, 50.0),
                    FUSE_ARRAY[1],
                ),
            ),
        )
    )
    for contact, vias in GROUND_ARRAYS.items():
        # Rev D: the "contact" stub runs in the 1.27 mm pad-pitch lane
        # shared with the immediately adjacent +8V_RAW contact (whose own
        # F.Cu contact/front-array stay at their original 1.0 mm) and
        # with the jack's own body pad 0.32 mm to either side of the pad
        # center (0.64 mm pad height / 2), leaving 0.95 mm before the
        # next pad's own copper -- 1.0 mm keeps >=0.2 mm from the
        # adjacent pad and, combined with +8V_RAW's own 1.0 mm, exactly
        # the 1.27 mm pitch's clearance budget. The narrow/wide-array
        # (which only span the relocated via cluster at X=7.35-10.35, see
        # GROUND_ARRAYS -- clear of +8V_RAW's F.Cu entirely) aren't pitch
        # -constrained, so both use 1.0 mm too for lower resistance
        # (single-open combined-drop budget), each still leaving >=0.2 mm
        # to a same-cluster via at the 1.5 mm via pitch.
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
        # The via cluster (X=9-11, see module note) already sits short of
        # the mechanical-tab band's X=12.6 start, so the branch can jog
        # straight from via[1] to the safe crossing Y (clear of both the
        # pad-9 and pad-10 mechanical-tab bands) without an intermediate
        # hold. The bus is reached at X=20.8, past the tab band's X=15.2
        # end.
        #
        # J1.1/J1.7 (and J2.1/J2.7) share the same safe_y, so their tails
        # from X=11.0 onward would otherwise be byte-identical copper --
        # not a second parallel path, just the same track drawn twice
        # (which also makes the two routes' intent_ids geometrically
        # indistinguishable). Only the "trunk" contact (J1.1/J2.1) carries
        # the tail to the shared bus; the paired contact (J1.7/J2.7) stops
        # at the X=11.0 meeting point, electrically joined there (same
        # net, same coordinates) plus backed by the full GND plane.
        safe_y = GROUND_SAFE_Y[contact]
        if contact in TRUNK_GROUND_CONTACTS:
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
                1.0,
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
                (20.8, 37.0),
            ),
        )
    )
    return tuple(routes)


ROUTES = _routes()
# Rev D via sizing: +8V_RAW's vias stay at X=4-6, 1.27 mm from the
# adjacent GND contact's own pad row -- 0.8/0.4 mm keeps clearance there.
# GND's vias moved out to X=9-11 (see module note on GROUND_ARRAYS)
# specifically so they could go back to Rev C's original 1.4/1.0 mm
# size, which the conservative single-via-carries-2A IPC-2152 envelope
# needs (a 0.4 mm drill barrel alone cannot clear 20 C at 2 A).
_VIA_SIZE_MM = {"+8V_RAW": 0.8, "GND": 1.4}
_VIA_DRILL_MM = {"+8V_RAW": 0.4, "GND": 1.0}
VIAS = tuple(
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
) + tuple(
    {
        "id": f"raw-fuse-via-{index}",
        "net": "+8V_RAW",
        "at": point,
        "size_mm": 0.8,
        "drill_mm": 0.4,
    }
    for index, point in enumerate(FUSE_ARRAY)
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
