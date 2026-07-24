#!/usr/bin/python3
"""Emit a deterministic, versioned physical audit of the Esp32Tap PCB.

This program deliberately runs in KiCad's system Python.  Consumers run in
ordinary Python and validate only this JSON boundary, so they never import
pcbnew (or test-only geometry packages).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import pcbnew
except Exception as error:  # pragma: no cover - fatal CLI boundary
    raise SystemExit(f"cannot import pcbnew: {error}") from error


SCHEMA_VERSION = 1
COPPER_LAYER_IDS = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)
USB_MINUS = {"USB_DN", "USB_DN_MCU", "USB_DN_R"}
USB_PLUS = {"USB_DP", "USB_DP_MCU", "USB_DP_R"}
USB_NETS = USB_MINUS | USB_PLUS


def mm(value: int) -> float:
    return round(pcbnew.ToMM(value), 6)


def xy(point: Any) -> list[float]:
    return [mm(point.x), mm(point.y)]


def normalized_degrees(angle: Any) -> float:
    """Return a deterministic board rotation in the half-open [0, 360) range."""
    degrees = round(float(angle.AsDegrees()) % 360.0, 6)
    return 0.0 if degrees == 360.0 or degrees == -0.0 else degrees


def item_uuid(item: Any) -> str:
    return item.m_Uuid.AsString()


def balanced_block(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"missing {marker}")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ValueError(f"unterminated {marker}")


def child_blocks(source: str, marker: str) -> list[str]:
    blocks: list[str] = []
    cursor = 0
    while True:
        start = source.find(marker, cursor)
        if start < 0:
            return blocks
        block = balanced_block(source[start:], marker)
        blocks.append(block)
        cursor = start + len(block)


def scalar(block: str, name: str) -> str | None:
    match = re.search(
        rf"\({re.escape(name)}\s+(?:\"([^\"]*)\"|([^\s\)]+))\)",
        block,
    )
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def parse_stackup(source: str, board: Any) -> dict[str, Any]:
    stackup = balanced_block(source, "(stackup")
    layers: list[dict[str, Any]] = []
    for block in child_blocks(stackup, "(layer "):
        name_match = re.match(r'\(layer\s+"([^"]+)"', block)
        if not name_match:
            continue
        thickness = scalar(block, "thickness")
        epsilon_r = scalar(block, "epsilon_r")
        layers.append(
            {
                "name": name_match.group(1),
                "type": scalar(block, "type") or "",
                "thickness_mm": float(thickness or 0.0),
                "epsilon_r": None if epsilon_r is None else float(epsilon_r),
            }
        )
    title = board.GetTitleBlock()
    comment = title.GetComment(0)
    name_match = re.search(r"STACKUP:\s*([A-Za-z0-9_-]+)", comment)
    if not name_match:
        raise ValueError("title-block comment 1 lacks STACKUP name")
    general = balanced_block(source, "(general")
    thickness = scalar(general, "thickness")
    if thickness is None:
        raise ValueError("board general block lacks thickness")
    return {
        "name": name_match.group(1),
        "finished_thickness_mm": float(thickness),
        "layers": [
            layer
            for layer in layers
            if layer["name"] in {
                "F.Cu",
                "In1.Cu",
                "In2.Cu",
                "B.Cu",
                "dielectric 1",
                "dielectric 2",
                "dielectric 3",
            }
        ],
    }


def polygon(zone: Any) -> list[list[float]]:
    outline = zone.Outline()
    if outline.OutlineCount() != 1:
        raise ValueError(
            f"zone {zone.GetZoneName()!r} must have exactly one outline"
        )
    chain = outline.COutline(0)
    return [xy(chain.CPoint(index)) for index in range(chain.PointCount())]


def bbox_dict(box: Any) -> dict[str, list[float]]:
    return {
        "min": [mm(box.GetLeft()), mm(box.GetTop())],
        "max": [mm(box.GetRight()), mm(box.GetBottom())],
    }


def point_in_polygon(point: list[float], outline: list[list[float]]) -> bool:
    x, y = point
    inside = False
    previous = outline[-1]
    for current in outline:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def segment_length(track: dict[str, Any]) -> float:
    return math.hypot(
        track["end"][0] - track["start"][0],
        track["end"][1] - track["start"][1],
    )


def point_segment_distance(
    point: list[float],
    start: list[float],
    end: list[float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / (dx * dx + dy * dy),
        ),
    )
    projection = [start[0] + fraction * dx, start[1] + fraction * dy]
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def parallel(a: dict[str, Any], b: dict[str, Any]) -> bool:
    adx = a["end"][0] - a["start"][0]
    ady = a["end"][1] - a["start"][1]
    bdx = b["end"][0] - b["start"][0]
    bdy = b["end"][1] - b["start"][1]
    return abs(adx * bdy - ady * bdx) <= 1e-6


def annotate_usb(
    tracks: list[dict[str, Any]],
    footprints: dict[str, Any],
) -> None:
    connector_pads = {
        tuple(footprints["J3"]["pads"][number]["at"])
        for number in ("A6", "B6", "A7", "B7")
        if "J3" in footprints and number in footprints["J3"]["pads"]
    }
    for track in tracks:
        if track["net"] not in {"USB_DN", "USB_DP"}:
            continue
        if any(
            math.hypot(endpoint[0] - pad[0], endpoint[1] - pad[1])
            <= 0.01
            for endpoint in (track["start"], track["end"])
            for pad in connector_pads
        ):
            track["role"] = "CONNECTOR_BREAKOUT"

    minus = [
        track
        for track in tracks
        if track["net"] in USB_MINUS and track["layer"] == "F.Cu"
    ]
    plus = [
        track
        for track in tracks
        if track["net"] in USB_PLUS and track["layer"] == "F.Cu"
    ]
    candidates: list[tuple[float, str, str, dict[str, Any], dict[str, Any]]] = []
    for negative in minus:
        for positive in plus:
            if (
                negative.get("role") == "CONNECTOR_BREAKOUT"
                or positive.get("role") == "CONNECTOR_BREAKOUT"
            ):
                continue
            if not parallel(negative, positive):
                continue
            if abs(segment_length(negative) - segment_length(positive)) > 0.01:
                continue
            distance = min(
                point_segment_distance(
                    negative["start"],
                    positive["start"],
                    positive["end"],
                ),
                point_segment_distance(
                    negative["end"],
                    positive["start"],
                    positive["end"],
                ),
            )
            if abs(distance - 0.4906) <= 0.01:
                candidates.append(
                    (
                        distance,
                        negative["id"],
                        positive["id"],
                        negative,
                        positive,
                    )
                )
    used: set[str] = set()
    section = 0
    for _, _, _, negative, positive in sorted(candidates):
        if negative["id"] in used or positive["id"] in used:
            continue
        label = f"USB_PAIR_{section:02d}"
        for track in (negative, positive):
            track["pair_section"] = label
            track["reference_plane"] = "In1.Cu:GND"
            used.add(track["id"])
        section += 1

    for net, ref in (("USB_DN_R", "C13"), ("USB_DP_R", "C14")):
        if ref not in footprints or "1" not in footprints[ref]["pads"]:
            continue
        pad = footprints[ref]["pads"]["1"]["at"]
        candidates = [
            track
            for track in tracks
            if track["net"] == net
            and min(
                math.hypot(endpoint[0] - pad[0], endpoint[1] - pad[1])
                for endpoint in (track["start"], track["end"])
            )
            <= 0.01
        ]
        if candidates:
            min(candidates, key=segment_length)["role"] = "DNP_STUB"

    for track in tracks:
        if track["net"] == "FB":
            track["role"] = "KELVIN_FB"


def enabled_copper_layers(board: Any) -> list[str]:
    return [
        board.GetLayerName(layer)
        for layer in COPPER_LAYER_IDS
        if board.IsLayerEnabled(layer)
    ]


def inspect(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"board does not exist: {path}")
    source = path.read_text(encoding="utf-8")
    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise ValueError(f"pcbnew failed to load {path}")
    board.BuildConnectivity()

    footprints: dict[str, Any] = {}
    pad_items: dict[str, tuple[str, str]] = {}
    all_items: dict[str, Any] = {}
    for footprint in sorted(
        board.GetFootprints(), key=lambda item: item.GetReference()
    ):
        reference = footprint.GetReference()
        if not reference:
            raise ValueError("footprint without reference")
        if reference in footprints:
            raise ValueError(f"duplicate footprint reference {reference}")
        pads: dict[str, Any] = {}
        for pad in sorted(
            footprint.Pads(),
            key=lambda item: (
                str(item.GetNumber()),
                item.GetPosition().x,
                item.GetPosition().y,
            ),
        ):
            number = str(pad.GetNumber())
            if not number:
                continue
            pads[number] = {
                "net": pad.GetNetname(),
                "at": xy(pad.GetPosition()),
                "layers": [
                    board.GetLayerName(layer)
                    for layer in pad.GetLayerSet().Seq()
                ],
            }
            pad_items[item_uuid(pad)] = (reference, number)
            all_items[item_uuid(pad)] = pad
        footprints[reference] = {
            "footprint": footprint.GetFPIDAsString(),
            "layer": board.GetLayerName(footprint.GetLayer()),
            "at": xy(footprint.GetPosition()),
            "rotation_deg": normalized_degrees(footprint.GetOrientation()),
            "dnp": footprint.IsDNP(),
            "excluded_from_bom": footprint.IsExcludedFromBOM(),
            "board_only": footprint.IsBoardOnly(),
            "bbox": bbox_dict(footprint.GetBoundingBox()),
            "pads": pads,
        }

    track_objects: list[Any] = []
    via_objects: list[Any] = []
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_VIA):
            via_objects.append(item)
        elif isinstance(item, pcbnew.PCB_TRACK):
            track_objects.append(item)

    track_objects.sort(
        key=lambda item: (
            item.GetNetname(),
            board.GetLayerName(item.GetLayer()),
            xy(item.GetStart()),
            xy(item.GetEnd()),
            mm(item.GetWidth()),
        )
    )
    tracks: list[dict[str, Any]] = []
    copper_ids: dict[str, str] = {}
    for index, item in enumerate(track_objects):
        identifier = f"track:{index:04d}"
        copper_ids[item_uuid(item)] = identifier
        all_items[item_uuid(item)] = item
        tracks.append(
            {
                "id": identifier,
                "net": item.GetNetname(),
                "layer": board.GetLayerName(item.GetLayer()),
                "width_mm": mm(item.GetWidth()),
                "start": xy(item.GetStart()),
                "end": xy(item.GetEnd()),
            }
        )

    via_objects.sort(
        key=lambda item: (
            item.GetNetname(),
            xy(item.GetPosition()),
            mm(item.GetWidth(pcbnew.F_Cu)),
            mm(item.GetDrillValue()),
        )
    )
    vias: list[dict[str, Any]] = []
    for index, item in enumerate(via_objects):
        identifier = f"via:{index:04d}"
        copper_ids[item_uuid(item)] = identifier
        all_items[item_uuid(item)] = item
        vias.append(
            {
                "id": identifier,
                "net": item.GetNetname(),
                "layers": [
                    board.GetLayerName(layer)
                    for layer in COPPER_LAYER_IDS
                    if board.IsLayerEnabled(layer) and item.IsOnLayer(layer)
                ],
                "at": xy(item.GetPosition()),
                "size_mm": mm(item.GetWidth(pcbnew.F_Cu)),
                "drill_mm": mm(item.GetDrillValue()),
            }
        )

    zones: list[dict[str, Any]] = []
    rule_areas: list[dict[str, Any]] = []
    zone_objects: list[Any] = []
    for zone in sorted(
        board.Zones(),
        key=lambda item: (
            item.GetIsRuleArea(),
            item.GetZoneName(),
            item.GetNetname(),
            board.GetLayerName(item.GetFirstLayer()),
        ),
    ):
        outline = polygon(zone)
        if zone.GetIsRuleArea():
            rule_areas.append(
                {
                    "name": zone.GetZoneName(),
                    "layers": [
                        board.GetLayerName(layer)
                        for layer in COPPER_LAYER_IDS
                        if zone.IsOnLayer(layer)
                    ],
                    "outline": outline,
                    "forbid_footprints": zone.GetDoNotAllowFootprints(),
                    "forbid_pads": zone.GetDoNotAllowPads(),
                    "forbid_tracks": zone.GetDoNotAllowTracks(),
                    "forbid_vias": zone.GetDoNotAllowVias(),
                    "forbid_zone_fills": zone.GetDoNotAllowZoneFills(),
                }
            )
            continue
        identifier = f"zone:{len(zones):04d}"
        copper_ids[item_uuid(zone)] = identifier
        all_items[item_uuid(zone)] = zone
        zone_objects.append(zone)
        zones.append(
            {
                "id": identifier,
                "net": zone.GetNetname(),
                "layer": board.GetLayerName(zone.GetFirstLayer()),
                "outline": outline,
            }
        )

    antenna_names = {
        area["name"]
        for area in rule_areas
        if area["name"] == "ESP32_ANTENNA_ALL_COPPER_KEEPOUT"
    }
    if antenna_names:
        for zone in zones:
            if zone["layer"] == "In1.Cu" and zone["net"] == "GND":
                zone["explicit_exceptions"] = sorted(antenna_names)

    fixture = next(
        (
            area
            for area in rule_areas
            if area["name"] == "TP5_TP13_BOTTOM_FIXTURE"
        ),
        None,
    )
    if fixture:
        for number in range(5, 14):
            ref = f"TP{number}"
            if ref in footprints:
                position = footprints[ref]["at"]
                footprints[ref]["fixture_accessible"] = point_in_polygon(
                    position,
                    fixture["outline"],
                )

    annotate_usb(tracks, footprints)

    texts: list[dict[str, Any]] = []
    edge_points: list[list[float]] = []
    for drawing in board.GetDrawings():
        if drawing.GetLayer() == pcbnew.Edge_Cuts and isinstance(
            drawing, pcbnew.PCB_SHAPE
        ):
            edge_points.extend((xy(drawing.GetStart()), xy(drawing.GetEnd())))
        if isinstance(drawing, pcbnew.PCB_TEXT):
            texts.append(
                {
                    "text": drawing.GetText(),
                    "layer": board.GetLayerName(drawing.GetLayer()),
                    "stroke_width_mm": mm(drawing.GetTextThickness()),
                    "height_mm": mm(drawing.GetTextSize().y),
                    "at": xy(drawing.GetPosition()),
                }
            )
    texts.sort(key=lambda item: (item["layer"], item["text"], item["at"]))
    if not edge_points:
        raise ValueError("board has no Edge.Cuts geometry")
    min_x = min(point[0] for point in edge_points)
    max_x = max(point[0] for point in edge_points)
    min_y = min(point[1] for point in edge_points)
    max_y = max(point[1] for point in edge_points)

    connectivity_data = board.GetConnectivity()
    connectivity: dict[str, Any] = {}
    net_seeds: dict[str, list[Any]] = {}
    for uid, (ref, pad) in pad_items.items():
        net = footprints[ref]["pads"][pad]["net"]
        if net:
            net_seeds.setdefault(net, []).append(all_items[uid])
    for uid, identifier in copper_ids.items():
        item = all_items[uid]
        net = item.GetNetname()
        if net:
            net_seeds.setdefault(net, []).append(item)

    for net, seeds in sorted(net_seeds.items()):
        remaining = {item_uuid(seed): seed for seed in seeds}
        components: list[dict[str, Any]] = []
        while remaining:
            seed_uid = sorted(remaining)[0]
            seed = remaining[seed_uid]
            connected = list(connectivity_data.GetConnectedItems(seed))
            connected.append(seed)
            connected_uids = {item_uuid(item) for item in connected}
            component_pads = sorted(
                {
                    pad_items[uid]
                    for uid in connected_uids
                    if uid in pad_items
                    and footprints[pad_items[uid][0]]["pads"][
                        pad_items[uid][1]
                    ]["net"]
                    == net
                }
            )
            component_copper = sorted(
                {
                    copper_ids[uid]
                    for uid in connected_uids
                    if uid in copper_ids
                }
            )
            if not component_pads and not component_copper:
                raise ValueError(f"empty connectivity component on {net}")
            components.append(
                {
                    "pads": [list(node) for node in component_pads],
                    "copper_ids": component_copper,
                }
            )
            removed = connected_uids & remaining.keys()
            if not removed:
                removed = {seed_uid}
            for uid in removed:
                remaining.pop(uid, None)
        components.sort(
            key=lambda item: (item["pads"], item["copper_ids"])
        )
        connectivity[net] = {"components": components}

    title = board.GetTitleBlock()
    u1 = next(
        (
            footprint
            for footprint in board.GetFootprints()
            if footprint.GetReference() == "U1"
        ),
        None,
    )
    if u1 is None:
        raise ValueError("board has no U1 antenna module")
    fab_points: list[list[float]] = []
    for graphic in u1.GraphicalItems():
        if graphic.GetLayer() != pcbnew.F_Fab:
            continue
        if isinstance(graphic, pcbnew.PCB_SHAPE):
            fab_points.extend((xy(graphic.GetStart()), xy(graphic.GetEnd())))
    if not fab_points:
        raise ValueError("U1 has no physical F.Fab outline")
    physical_edge_y = min(point[1] for point in fab_points)
    physical_span_x = [
        min(point[0] for point in fab_points),
        max(point[0] for point in fab_points),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "board": {
            "title": title.GetTitle(),
            "revision": title.GetRevision(),
            "copper_layers": enabled_copper_layers(board),
            "stackup": parse_stackup(source, board),
            "outline": {
                "min": [min_x, min_y],
                "max": [max_x, max_y],
                "width_mm": round(max_x - min_x, 6),
                "height_mm": round(max_y - min_y, 6),
            },
            "footprints": footprints,
            "tracks": tracks,
            "vias": vias,
            "zones": zones,
            "rule_areas": rule_areas,
            "texts": texts,
            "connectivity": connectivity,
            "antenna": {
                "reference": "U1",
                "physical_edge_y_mm": physical_edge_y,
                "span_x_mm": physical_span_x,
            },
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "kicad"
        / "Esp32Tap.kicad_pcb",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.board.resolve())
    except Exception as error:
        print(f"inspect_kicad: {error}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(
            f"schema={report['schema_version']} "
            f"layers={','.join(report['board']['copper_layers'])} "
            f"footprints={len(report['board']['footprints'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
