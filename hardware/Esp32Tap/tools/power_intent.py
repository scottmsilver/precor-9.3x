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


RAW_ARRAYS = {
    "J1.2": ((7.4, 12.5), (8.4, 12.5), (9.4, 12.5)),
    "J1.8": ((15.6, 6.5), (16.6, 6.5), (17.6, 6.5)),
    "J2.2": ((7.4, 40.0), (8.4, 40.0), (9.4, 40.0)),
    "J2.8": ((15.6, 37.0), (16.6, 37.0), (17.6, 37.0)),
}
RAW_CONTACTS = {
    "J1.2": (10.185, 12.5),
    "J1.8": (14.815, 6.5),
    "J2.2": (10.185, 40.0),
    "J2.8": (14.815, 37.0),
}
FUSE_ARRAY = ((17.4, 50.0), (18.4, 50.0), (19.4, 50.0))
FUSE_CONTACT = (18.3625, 52.5)

GROUND_ARRAYS = {
    "J1.1": ((8.5, 15.5), (10.0, 15.5), (11.5, 15.5)),
    "J1.7": ((15.1, 9.5), (16.6, 9.5), (18.1, 9.5)),
    "J2.1": ((8.5, 43.0), (10.0, 43.0), (11.5, 43.0)),
    "J2.7": ((15.1, 40.0), (16.6, 40.0), (18.1, 40.0)),
}
GROUND_CONTACTS = {
    "J1.1": (10.185, 15.5),
    "J1.7": (14.815, 9.5),
    "J2.1": (10.185, 43.0),
    "J2.7": (14.815, 40.0),
}


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
                _path(
                    f"raw-{contact}-bottom-array",
                    "+8V_RAW",
                    "B.Cu",
                    2.0,
                    vias,
                ),
                _path(
                    f"raw-{contact}-bus-branch",
                    "+8V_RAW",
                    "B.Cu",
                    2.0,
                    (vias[1], (6.4, vias[1][1])),
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
                    (6.4, 6.5),
                    (6.4, 12.5),
                    (6.4, 37.0),
                    (6.4, 40.0),
                    (6.4, 50.0),
                    FUSE_ARRAY[1],
                ),
            ),
        )
    )
    for contact, vias in GROUND_ARRAYS.items():
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
                    2.0,
                    vias,
                ),
            )
        )
        if contact == "J1.1":
            branch = (vias[1], (11.0, 18.0), (20.8, 18.0))
        elif contact == "J2.1":
            branch = (vias[1], (11.0, 48.0), (20.8, 48.0))
        else:
            branch = (vias[1], (20.8, vias[1][1]))
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
                (20.8, 9.5),
                (20.8, 18.0),
                (20.8, 40.0),
                (20.8, 48.0),
            ),
        )
    )
    return tuple(routes)


ROUTES = _routes()
VIAS = tuple(
    {
        "id": f"{net.lower()}-{contact}-via-{index}",
        "net": net,
        "at": point,
        "size_mm": 1.4 if net == "GND" else 0.8,
        "drill_mm": 1.0 if net == "GND" else 0.4,
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
