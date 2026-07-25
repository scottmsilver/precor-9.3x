from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import power_intent  # noqa: E402

SYSTEM_PYTHON = Path("/usr/bin/python3")
INSPECTOR = Path("tools/inspect_kicad.py")
INSPECTOR_TIMEOUT_SECONDS = 30
EXPECTED_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
EXPECTED_STACKUP = [
    ("F.Cu", "copper", 0.035, None),
    ("dielectric 1", "prepreg", 0.2104, 4.4),
    ("In1.Cu", "copper", 0.0152, None),
    ("dielectric 2", "core", 1.065, 4.38),
    ("In2.Cu", "copper", 0.0152, None),
    ("dielectric 3", "prepreg", 0.2104, 4.4),
    ("B.Cu", "copper", 0.035, None),
]
USB_CONTROLLED_WIDTH_MM = 0.2906
USB_EDGE_GAP_MM = 0.2000
USB_CENTER_SPACING_MM = USB_CONTROLLED_WIDTH_MM + USB_EDGE_GAP_MM
USB_ROUTE_PATHS = {
    "D-": {"USB_DN", "USB_DN_MCU", "USB_DN_R"},
    "D+": {"USB_DP", "USB_DP_MCU", "USB_DP_R"},
}
USB_ROUTE_NETS = set().union(*USB_ROUTE_PATHS.values())
EXPECTED_USB_NET_PADS = {
    "USB_DN": {("J3", "A7"), ("J3", "B7"), ("U3", "1")},
    "USB_DP": {("J3", "A6"), ("J3", "B6"), ("U3", "3")},
    "USB_DN_MCU": {("U3", "6"), ("R15", "1")},
    "USB_DP_MCU": {("U3", "4"), ("R16", "1")},
    "USB_DN_R": {("R15", "2"), ("C13", "1"), ("U1", "13")},
    "USB_DP_R": {("R16", "2"), ("C14", "1"), ("U1", "14")},
}
EXPECTED_FOOTPRINTS = {
    "J1": "RJ45_SMD:RJ45-SMD_441440003",
    "J2": "RJ45_SMD:RJ45-SMD_441440003",
    "SW1": "Button_Switch_SMD:SW_SPST_SKRPACE010",
    "SW2": "Button_Switch_SMD:SW_SPST_SKRPACE010",
    "F1": "Fuse:Fuse_1812_4532Metric",
    "D3": "Diode_SMD:D_SMB",
    "D4": "Diode_SMD:D_SMA",
    "K1": "Relay_SMD:Relay_DPDT_Omron_G6K-2F-Y",
    "Q1": "Package_TO_SOT_SMD:SOT-23",
    "Q2": "Package_TO_SOT_SMD:SOT-23",
    "U4": "Package_TO_SOT_SMD:SOT-23-6",
    "U5": "Package_TO_SOT_SMD:SOT-23-5",
    "U6": "Package_SO:SSOP-8_2.95x2.8mm_P0.65mm",
    "U7": "Package_TO_SOT_SMD:SOT-23-5",
    "C2": "Capacitor_SMD:C_1206_3216Metric",
    "C3": "Capacitor_SMD:C_1206_3216Metric",
    "C6": "Capacitor_SMD:C_1210_3225Metric",
    "C7": "Capacitor_SMD:C_1210_3225Metric",
    "C12": "Capacitor_SMD:C_0603_1608Metric",
    "C13": "Capacitor_SMD:C_0603_1608Metric",
    "C14": "Capacitor_SMD:C_0603_1608Metric",
    "C15": "Capacitor_SMD:C_0603_1608Metric",
    "C16": "Capacitor_SMD:C_0805_2012Metric",
    "C17": "Capacitor_SMD:C_0603_1608Metric",
    "C18": "Capacitor_SMD:C_0603_1608Metric",
    "C19": "Capacitor_SMD:C_0603_1608Metric",
    "C20": "Capacitor_SMD:C_0603_1608Metric",
    "C21": "Capacitor_SMD:C_0603_1608Metric",
    **{f"TP{number}": "TestPoint:TestPoint_Pad_1.5x1.5mm" for number in range(5, 14)},
}


def _run_inspector(esp32tap_dir: Path) -> dict[str, Any]:
    script = esp32tap_dir / INSPECTOR
    assert script.is_file(), (
        "Rev B artifact inspector is missing: " "tools/inspect_kicad.py must run under /usr/bin/python3"
    )
    command = [str(SYSTEM_PYTHON), str(INSPECTOR), "--json"]
    try:
        completed = subprocess.run(
            command,
            cwd=esp32tap_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=INSPECTOR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("inspect_kicad.py --json timed out after " f"{INSPECTOR_TIMEOUT_SECONDS} seconds")
    assert completed.returncode == 0, (
        "inspect_kicad.py --json failed\n" f"stdout:\n{completed.stdout}\n" f"stderr:\n{completed.stderr}"
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        pytest.fail(f"inspector stdout is not JSON: {error}")
    _assert_report_schema(report)
    return report


def _assert_report_schema(report: Any) -> None:
    assert isinstance(report, dict)
    assert report.get("schema_version") == 1
    board = report.get("board")
    assert isinstance(board, dict)
    required = {
        "title",
        "revision",
        "copper_layers",
        "stackup",
        "outline",
        "footprints",
        "tracks",
        "vias",
        "zones",
        "rule_areas",
        "texts",
        "connectivity",
        "antenna",
    }
    assert required <= board.keys()
    assert isinstance(board["title"], str)
    assert isinstance(board["revision"], str)
    assert _is_string_list(board["copper_layers"])
    assert isinstance(board["stackup"], dict)
    assert {
        "name",
        "finished_thickness_mm",
        "layers",
    } <= board["stackup"].keys()
    assert isinstance(board["stackup"]["name"], str)
    assert _is_number(board["stackup"]["finished_thickness_mm"])
    assert isinstance(board["stackup"]["layers"], list)
    for layer in board["stackup"]["layers"]:
        assert isinstance(layer, dict)
        assert {"name", "type", "thickness_mm", "epsilon_r"} <= layer.keys()
        assert isinstance(layer["name"], str) and layer["name"]
        assert isinstance(layer["type"], str) and layer["type"]
        assert _is_number(layer["thickness_mm"])
        assert layer["epsilon_r"] is None or _is_number(layer["epsilon_r"])
    assert isinstance(board["outline"], dict)
    assert {
        "min",
        "max",
        "width_mm",
        "height_mm",
        "area_mm2",
    } <= board["outline"].keys()
    _assert_xy(board["outline"]["min"], "outline.min")
    _assert_xy(board["outline"]["max"], "outline.max")
    assert _is_number(board["outline"]["width_mm"])
    assert _is_number(board["outline"]["height_mm"])
    assert _is_number(board["outline"]["area_mm2"])
    assert isinstance(board["footprints"], dict)
    assert all(isinstance(reference, str) and reference for reference in board["footprints"])
    for footprint in board["footprints"].values():
        _assert_footprint_schema(footprint)

    validators = {
        "tracks": _assert_track_schema,
        "vias": _assert_via_schema,
        "zones": _assert_zone_schema,
        "texts": _assert_text_schema,
    }
    for key, validator in validators.items():
        assert isinstance(board[key], list), key
        for item in board[key]:
            validator(item)
    assert isinstance(board["rule_areas"], list)
    for item in board["rule_areas"]:
        _assert_rule_area_schema(item)

    copper = board["tracks"] + board["vias"] + board["zones"]
    copper_by_id = {item["id"]: item for item in copper}
    assert len(copper_by_id) == len(copper), "copper IDs must be unique"
    _assert_connectivity_schema(
        board["connectivity"],
        board["footprints"],
        copper_by_id,
    )
    antenna = board["antenna"]
    assert antenna["reference"] == "U1"
    assert _is_number(antenna["physical_edge_y_mm"])
    _assert_xy(antenna["span_x_mm"], "antenna.span_x_mm")
    assert antenna["span_x_mm"][1] > antenna["span_x_mm"][0]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _assert_xy(value: Any, label: str) -> None:
    assert isinstance(value, list) and len(value) == 2, label
    assert all(_is_number(coordinate) for coordinate in value), label


def _assert_footprint_schema(footprint: Any) -> None:
    assert isinstance(footprint, dict)
    assert {
        "footprint",
        "layer",
        "at",
        "rotation_deg",
        "dnp",
        "excluded_from_bom",
        "board_only",
        "bbox",
        "courtyard_bbox",
        "pads",
        "pad_occurrences",
        "manufacturer_keepouts",
    } <= footprint.keys()
    assert isinstance(footprint["footprint"], str) and footprint["footprint"]
    assert isinstance(footprint["layer"], str) and footprint["layer"]
    _assert_xy(footprint["at"], "footprint.at")
    assert _is_number(footprint["rotation_deg"])
    assert 0.0 <= footprint["rotation_deg"] < 360.0
    assert isinstance(footprint["dnp"], bool)
    assert isinstance(footprint["excluded_from_bom"], bool)
    assert isinstance(footprint["board_only"], bool)
    assert isinstance(footprint["bbox"], dict)
    _assert_xy(footprint["bbox"]["min"], "footprint.bbox.min")
    _assert_xy(footprint["bbox"]["max"], "footprint.bbox.max")
    courtyard = footprint["courtyard_bbox"]
    assert courtyard is None or isinstance(courtyard, dict)
    if courtyard is not None:
        _assert_xy(courtyard["min"], "footprint.courtyard_bbox.min")
        _assert_xy(courtyard["max"], "footprint.courtyard_bbox.max")
    assert isinstance(footprint["pads"], dict)
    assert isinstance(footprint["pad_occurrences"], list)
    assert isinstance(footprint["manufacturer_keepouts"], list)
    for number, pad in footprint["pads"].items():
        assert isinstance(number, str) and number
        assert isinstance(pad, dict)
        assert {
            "net",
            "at",
            "layers",
            "attribute",
            "shape",
            "drill_shape",
            "size_mm",
            "drill_mm",
        } <= pad.keys()
        assert isinstance(pad["net"], str)
        _assert_xy(pad["at"], "pad.at")
        assert _is_string_list(pad["layers"])
        assert pad["attribute"] in {"smd", "pth", "npth", "connector"}
        _assert_xy(pad["size_mm"], "pad.size_mm")
        _assert_xy(pad["drill_mm"], "pad.drill_mm")
    for pad in footprint["pad_occurrences"]:
        assert {"occurrence_id", "number"} <= pad.keys()
        assert isinstance(pad["occurrence_id"], str)
        assert isinstance(pad["number"], str)
        logical = footprint["pads"][pad["number"]]
        assert {
            "net",
            "at",
            "layers",
            "attribute",
            "shape",
            "drill_shape",
            "size_mm",
            "drill_mm",
        } <= pad.keys()
        assert logical["net"] == pad["net"]
    for keepout in footprint["manufacturer_keepouts"]:
        assert {
            "id",
            "source_file",
            "source_sha256",
            "layers",
            "outline",
            "forbid_footprints",
            "forbid_pads",
            "forbid_tracks",
            "forbid_vias",
            "forbid_zone_fills",
        } <= keepout.keys()


def _assert_track_schema(track: Any) -> None:
    assert isinstance(track, dict)
    assert {
        "id",
        "net",
        "layer",
        "width_mm",
        "start",
        "end",
    } <= track.keys()
    assert isinstance(track["id"], str) and track["id"]
    assert isinstance(track["net"], str) and track["net"]
    assert isinstance(track["layer"], str) and track["layer"]
    assert _is_number(track["width_mm"]) and track["width_mm"] > 0
    _assert_xy(track["start"], "track.start")
    _assert_xy(track["end"], "track.end")


def _assert_via_schema(via: Any) -> None:
    assert isinstance(via, dict)
    assert {
        "id",
        "net",
        "layers",
        "at",
        "size_mm",
        "drill_mm",
    } <= via.keys()
    assert isinstance(via["id"], str) and via["id"]
    assert isinstance(via["net"], str) and via["net"]
    assert _is_string_list(via["layers"])
    _assert_xy(via["at"], "via.at")
    assert _is_number(via["size_mm"]) and via["size_mm"] > 0
    assert _is_number(via["drill_mm"]) and via["drill_mm"] > 0


def _assert_zone_schema(zone: Any) -> None:
    assert isinstance(zone, dict)
    assert {"id", "net", "layer", "outline"} <= zone.keys()
    assert isinstance(zone["id"], str) and zone["id"]
    assert isinstance(zone["net"], str) and zone["net"]
    assert isinstance(zone["layer"], str) and zone["layer"]
    assert isinstance(zone["outline"], list) and len(zone["outline"]) >= 3
    for point in zone["outline"]:
        _assert_xy(point, "zone.outline")


def _assert_rule_area_schema(area: Any) -> None:
    assert isinstance(area, dict)
    assert {
        "name",
        "layers",
        "outline",
        "forbid_footprints",
        "forbid_pads",
        "forbid_tracks",
        "forbid_vias",
        "forbid_zone_fills",
    } <= area.keys()
    assert isinstance(area["name"], str) and area["name"]
    assert _is_string_list(area["layers"])
    assert isinstance(area["outline"], list) and len(area["outline"]) >= 3
    for point in area["outline"]:
        _assert_xy(point, "rule_area.outline")
    for key in (
        "forbid_footprints",
        "forbid_pads",
        "forbid_tracks",
        "forbid_vias",
        "forbid_zone_fills",
    ):
        assert isinstance(area[key], bool)


def _assert_text_schema(text: Any) -> None:
    assert isinstance(text, dict)
    assert {
        "text",
        "layer",
        "stroke_width_mm",
        "height_mm",
        "at",
    } <= text.keys()
    assert isinstance(text["text"], str)
    assert isinstance(text["layer"], str) and text["layer"]
    assert _is_number(text["stroke_width_mm"])
    assert text["stroke_width_mm"] > 0
    assert _is_number(text["height_mm"])
    assert text["height_mm"] > 0
    _assert_xy(text["at"], "text.at")


def _assert_connectivity_schema(
    connectivity: Any,
    footprints: dict[str, Any],
    copper_by_id: dict[str, Any],
) -> None:
    assert isinstance(connectivity, dict)
    for net, net_connectivity in connectivity.items():
        assert isinstance(net, str) and net
        assert isinstance(net_connectivity, dict)
        assert set(net_connectivity) == {"components"}
        components = net_connectivity["components"]
        assert isinstance(components, list) and components
        seen_pads: set[tuple[str, str]] = set()
        seen_copper: set[str] = set()
        for component in components:
            assert isinstance(component, dict)
            assert set(component) == {"pads", "copper_ids"}
            assert isinstance(component["pads"], list)
            assert isinstance(component["copper_ids"], list)
            assert component["pads"] or component["copper_ids"]
            for node in component["pads"]:
                assert (
                    isinstance(node, list)
                    and len(node) == 2
                    and all(isinstance(value, str) and value for value in node)
                )
                ref, pad = node
                assert ref in footprints
                assert pad in footprints[ref]["pads"]
                assert footprints[ref]["pads"][pad]["net"] == net
                assert (ref, pad) not in seen_pads
                seen_pads.add((ref, pad))
            for copper_id in component["copper_ids"]:
                assert isinstance(copper_id, str) and copper_id
                assert copper_id in copper_by_id
                assert copper_by_id[copper_id]["net"] == net
                assert copper_id not in seen_copper
                seen_copper.add(copper_id)


@pytest.fixture(scope="module")
def kicad_report(esp32tap_dir: Path) -> dict[str, Any]:
    if not (esp32tap_dir / INSPECTOR).is_file():
        pytest.skip("Rev B tools/inspect_kicad.py is not implemented yet")
    return _run_inspector(esp32tap_dir)


def _board(report: dict[str, Any]) -> dict[str, Any]:
    return report["board"]


def _pads(report: dict[str, Any], ref: str) -> dict[str, str]:
    footprints = _board(report)["footprints"]
    assert ref in footprints, f"PCB is missing Rev B footprint {ref}"
    pads = footprints[ref].get("pads")
    assert isinstance(pads, dict), f"{ref} pads must be a JSON object"
    return {number: pad["net"] for number, pad in pads.items()}


def _nodes_on_net(
    report: dict[str, Any],
    net: str,
) -> set[tuple[str, str]]:
    return {
        (ref, pad)
        for ref, footprint in _board(report)["footprints"].items()
        for pad, pad_data in footprint["pads"].items()
        if pad_data["net"] == net
    }


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_segment_distance(
    point: list[float],
    start: list[float],
    end: list[float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return _distance(point, start)
    fraction = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy),
        ),
    )
    projection = [start[0] + fraction * dx, start[1] + fraction * dy]
    return _distance(point, projection)


def _planar_copper_graph(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    label: str,
    bridges: tuple[
        tuple[tuple[float, float], tuple[float, float]],
        ...,
    ] = (),
) -> dict[
    tuple[float, float],
    list[tuple[float, tuple[float, float]]],
]:
    # 1e-4 mm (0.1 um), not 1e-7: pcbnew stores coordinates as integer
    # nanometres, so round-tripping a clean decimal mm value through
    # FromMM()/ToMM() can land ~1 nm (1e-6 mm) off the intended value --
    # 10x tighter than 1e-7 would tolerate, but a real routing distinction
    # is always at least tens of microns, so 1e-4 cannot create a false
    # intersection/shared-node match.
    epsilon = 1e-4

    def point(value: tuple[float, float]) -> tuple[float, float]:
        # Match `epsilon` above: coarse enough to merge two conceptually-
        # identical nodes whose pcbnew-derived coordinates differ by the
        # ~1 nm FromMM()/ToMM() round-trip artifact.
        return (round(value[0], 4), round(value[1], 4))

    normalized = [(point(start), point(end)) for start, end in segments]
    split_points = [{start, end} for start, end in normalized]

    def cross(
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        return left[0] * right[1] - left[1] * right[0]

    def subtract(
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> tuple[float, float]:
        return (left[0] - right[0], left[1] - right[1])

    def on_segment(
        candidate: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        if abs(cross(subtract(candidate, start), subtract(end, start))) > (epsilon):
            return False
        return (
            min(start[0], end[0]) - epsilon <= candidate[0] <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon <= candidate[1] <= max(start[1], end[1]) + epsilon
        )

    for left_index, (left_start, left_end) in enumerate(normalized):
        left_vector = subtract(left_end, left_start)
        for right_index in range(left_index + 1, len(normalized)):
            right_start, right_end = normalized[right_index]
            right_vector = subtract(right_end, right_start)
            denominator = cross(left_vector, right_vector)
            offset = subtract(right_start, left_start)
            if abs(denominator) <= epsilon:
                if abs(cross(offset, left_vector)) > epsilon:
                    continue
                for candidate in {
                    left_start,
                    left_end,
                    right_start,
                    right_end,
                }:
                    if on_segment(
                        candidate,
                        left_start,
                        left_end,
                    ) and on_segment(candidate, right_start, right_end):
                        split_points[left_index].add(candidate)
                        split_points[right_index].add(candidate)
                continue
            left_fraction = cross(offset, right_vector) / denominator
            right_fraction = cross(offset, left_vector) / denominator
            if -epsilon <= left_fraction <= 1 + epsilon and -epsilon <= right_fraction <= 1 + epsilon:
                intersection = point(
                    (
                        left_start[0] + left_fraction * left_vector[0],
                        left_start[1] + left_fraction * left_vector[1],
                    )
                )
                split_points[left_index].add(intersection)
                split_points[right_index].add(intersection)

    graph: dict[
        tuple[float, float],
        list[tuple[float, tuple[float, float]]],
    ] = {}
    parents: dict[tuple[float, float], tuple[float, float]] = {}

    def root(item: tuple[float, float]) -> tuple[float, float]:
        parents.setdefault(item, item)
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def edge(
        start: tuple[float, float],
        end: tuple[float, float],
        length: float,
    ) -> None:
        left, right = root(start), root(end)
        assert left != right, f"{label} contains a copper cycle between {start} and {end}"
        parents[left] = right
        graph.setdefault(start, []).append((length, end))
        graph.setdefault(end, []).append((length, start))

    for index, (start, end) in enumerate(normalized):
        vector = subtract(end, start)
        length_squared = vector[0] ** 2 + vector[1] ** 2
        assert length_squared > epsilon
        ordered = sorted(
            split_points[index],
            key=lambda candidate: ((candidate[0] - start[0]) * vector[0] + (candidate[1] - start[1]) * vector[1])
            / length_squared,
        )
        for first, second in zip(ordered, ordered[1:]):
            if _distance(first, second) > epsilon:
                edge(first, second, _distance(first, second))
    for start, end in bridges:
        edge(point(start), point(end), 0.0)
    return graph


def _track_length(track: dict[str, Any]) -> float:
    return _distance(track["start"], track["end"])


def _parse_profile_gerber(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    assert "%TF.FileFunction,Profile,NP*%" in source
    assert "%FSLAX46Y46*%" in source
    assert "%MOMM*%" in source
    current: tuple[float, float] | None = None
    points: list[tuple[float, float]] = []
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for match in re.finditer(
        r"^X(-?\d+)Y(-?\d+)D0([12])\*$",
        source,
        re.MULTILINE,
    ):
        point = (
            int(match.group(1)) / 1_000_000,
            int(match.group(2)) / 1_000_000,
        )
        points.append(point)
        if match.group(3) == "1":
            assert current is not None
            segments.append((current, point))
        current = point
    assert points
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    return {
        "bounds": bounds,
        "width_mm": bounds[2] - bounds[0],
        "height_mm": bounds[3] - bounds[1],
        "segments": segments,
    }


def _parse_excellon_drills(path: Path) -> list[dict[str, float]]:
    source = path.read_text(encoding="utf-8")
    assert re.search(r"^METRIC$", source, re.MULTILINE)
    tools = {
        match.group(1): float(match.group(2))
        for match in re.finditer(
            r"^T(\d+)C(\d+(?:\.\d+)?)$",
            source,
            re.MULTILINE,
        )
    }
    drills: list[dict[str, float]] = []
    selected: str | None = None
    for line in source.splitlines():
        tool = re.fullmatch(r"T(\d+)", line)
        if tool:
            selected = tool.group(1)
            assert selected in tools
            continue
        coordinate = re.fullmatch(
            r"(?:G0[01])?X(-?\d+(?:\.\d+)?)Y(-?\d+(?:\.\d+)?)",
            line,
        )
        if coordinate:
            assert selected is not None
            drills.append(
                {
                    "x_mm": float(coordinate.group(1)),
                    "y_mm": float(coordinate.group(2)),
                    "diameter_mm": tools[selected],
                }
            )
    return drills


def _footprint_at(report: dict[str, Any], ref: str) -> list[float]:
    return _board(report)["footprints"][ref]["at"]


def _tracks_on_net(
    report: dict[str, Any],
    net: str,
) -> list[dict[str, Any]]:
    return [track for track in _board(report)["tracks"] if track["net"] == net]


def _assert_usb_connectivity(report: dict[str, Any]) -> None:
    board = _board(report)
    connectivity = board["connectivity"]
    for net, expected_pads in EXPECTED_USB_NET_PADS.items():
        assert net in connectivity, f"{net} lacks connectivity graph data"
        components = connectivity[net]["components"]
        assert len(components) == 1, f"{net} must be one connected component from every endpoint"
        component = components[0]
        actual_pads = {tuple(node) for node in component["pads"]}
        assert actual_pads == expected_pads, (
            f"{net} connectivity endpoints differ: "
            f"missing={sorted(expected_pads - actual_pads)}, "
            f"extra={sorted(actual_pads - expected_pads)}"
        )

        actual_copper_ids = set(component["copper_ids"])
        expected_copper_ids = {
            item["id"] for collection in ("tracks", "vias", "zones") for item in board[collection] if item["net"] == net
        }
        assert actual_copper_ids
        assert actual_copper_ids == expected_copper_ids, f"{net} connectivity must account for every routed copper item"


def _minimal_inspector_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "board": {
            "title": "Schema fixture",
            "revision": "B",
            "copper_layers": ["F.Cu", "B.Cu"],
            "stackup": {
                "name": "fixture",
                "finished_thickness_mm": 1.6,
                "layers": [
                    {
                        "name": "F.Cu",
                        "type": "copper",
                        "thickness_mm": 0.035,
                        "epsilon_r": None,
                    }
                ],
            },
            "outline": {
                "min": [0.0, 0.0],
                "max": [1.0, 1.0],
                "width_mm": 1.0,
                "height_mm": 1.0,
                "area_mm2": 1.0,
            },
            "footprints": {
                "X1": {
                    "footprint": "Test:Connector",
                    "layer": "F.Cu",
                    "at": [0.0, 0.0],
                    "rotation_deg": 90.0,
                    "dnp": False,
                    "excluded_from_bom": False,
                    "board_only": False,
                    "bbox": {"min": [0.0, 0.0], "max": [1.0, 1.0]},
                    "courtyard_bbox": {
                        "min": [0.0, 0.0],
                        "max": [1.0, 1.0],
                    },
                    "pads": {
                        "1": {
                            "net": "N",
                            "at": [0.0, 0.0],
                            "layers": ["F.Cu"],
                            "attribute": "smd",
                            "shape": "rect",
                            "drill_shape": "none",
                            "size_mm": [1.0, 1.0],
                            "drill_mm": [0.0, 0.0],
                        }
                    },
                    "pad_occurrences": [
                        {
                            "occurrence_id": "X1:1:00",
                            "number": "1",
                            "net": "N",
                            "at": [0.0, 0.0],
                            "layers": ["F.Cu"],
                            "attribute": "smd",
                            "shape": "rect",
                            "drill_shape": "none",
                            "size_mm": [1.0, 1.0],
                            "drill_mm": [0.0, 0.0],
                        }
                    ],
                    "manufacturer_keepouts": [],
                }
            },
            "tracks": [
                {
                    "id": "track:1",
                    "net": "N",
                    "layer": "F.Cu",
                    "width_mm": USB_CONTROLLED_WIDTH_MM,
                    "start": [0.0, 0.0],
                    "end": [1.0, 0.0],
                }
            ],
            "vias": [
                {
                    "id": "via:1",
                    "net": "N",
                    "layers": ["F.Cu", "B.Cu"],
                    "at": [1.0, 0.0],
                    "size_mm": 0.6,
                    "drill_mm": 0.3,
                }
            ],
            "zones": [
                {
                    "id": "zone:1",
                    "net": "N",
                    "layer": "F.Cu",
                    "outline": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                }
            ],
            "rule_areas": [
                {
                    "name": "fixture",
                    "layers": ["B.Cu"],
                    "outline": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                    "forbid_footprints": True,
                    "forbid_pads": True,
                    "forbid_tracks": True,
                    "forbid_vias": True,
                    "forbid_zone_fills": True,
                }
            ],
            "texts": [
                {
                    "text": "Schema fixture",
                    "layer": "F.SilkS",
                    "stroke_width_mm": 0.15,
                    "height_mm": 1.0,
                    "at": [0.0, 0.0],
                }
            ],
            "connectivity": {
                "N": {
                    "components": [
                        {
                            "pads": [["X1", "1"]],
                            "copper_ids": [
                                "track:1",
                                "via:1",
                                "zone:1",
                            ],
                        }
                    ]
                }
            },
            "antenna": {
                "reference": "U1",
                "physical_edge_y_mm": -1.0,
                "span_x_mm": [0.0, 1.0],
            },
        },
    }


def _usb_connectivity_report() -> dict[str, Any]:
    report = _minimal_inspector_report()
    board = report["board"]
    board["footprints"] = {}
    board["tracks"] = []
    board["vias"] = []
    board["zones"] = []
    board["connectivity"] = {}

    for net, nodes in EXPECTED_USB_NET_PADS.items():
        for ref, pad in nodes:
            footprint = board["footprints"].setdefault(
                ref,
                {
                    "footprint": f"Test:{ref}",
                    "layer": "F.Cu",
                    "at": [0.0, 0.0],
                    "rotation_deg": 0.0,
                    "dnp": False,
                    "excluded_from_bom": False,
                    "board_only": False,
                    "bbox": {"min": [0.0, 0.0], "max": [1.0, 1.0]},
                    "courtyard_bbox": {
                        "min": [0.0, 0.0],
                        "max": [1.0, 1.0],
                    },
                    "pads": {},
                    "pad_occurrences": [],
                    "manufacturer_keepouts": [],
                },
            )
            pad_record = {
                "net": net,
                "at": [0.0, 0.0],
                "layers": ["F.Cu"],
                "attribute": "smd",
                "shape": "rect",
                "drill_shape": "none",
                "size_mm": [1.0, 1.0],
                "drill_mm": [0.0, 0.0],
            }
            footprint["pads"][pad] = pad_record
            footprint["pad_occurrences"].append(
                {
                    "occurrence_id": f"{ref}:{pad}:00",
                    "number": pad,
                    **pad_record,
                }
            )

        copper_id = f"track:{net}"
        board["tracks"].append(
            {
                "id": copper_id,
                "net": net,
                "layer": "F.Cu",
                "width_mm": USB_CONTROLLED_WIDTH_MM,
                "start": [0.0, 0.0],
                "end": [1.0, 0.0],
            }
        )
        board["connectivity"][net] = {
            "components": [
                {
                    "pads": [list(node) for node in sorted(nodes)],
                    "copper_ids": [copper_id],
                }
            ]
        }
    return report


def test_minimal_inspector_report_satisfies_nested_schema() -> None:
    _assert_report_schema(_minimal_inspector_report())


@pytest.mark.parametrize(
    "section",
    [
        "footprint",
        "pad",
        "track",
        "via",
        "zone",
        "text",
        "connectivity",
    ],
)
def test_inspector_schema_rejects_malformed_nested_data(section: str) -> None:
    report = copy.deepcopy(_minimal_inspector_report())
    board = report["board"]
    if section == "footprint":
        board["footprints"]["X1"].pop("layer")
    elif section == "pad":
        board["footprints"]["X1"]["pads"]["1"]["at"] = ["x", 0.0]
    elif section == "track":
        board["tracks"][0].pop("end")
    elif section == "via":
        board["vias"][0]["layers"] = "F.Cu"
    elif section == "zone":
        board["zones"][0]["outline"] = [[0.0, 0.0], [1.0, 0.0]]
    elif section == "text":
        board["texts"][0]["stroke_width_mm"] = "wide"
    else:
        board["connectivity"]["N"]["components"][0]["pads"] = [["X1"]]

    with pytest.raises(AssertionError):
        _assert_report_schema(report)


def test_usb_connectivity_rejects_a_disconnected_arbitrary_segment() -> None:
    report = _usb_connectivity_report()
    _assert_report_schema(report)
    _assert_usb_connectivity(report)

    disconnected = copy.deepcopy(report)
    board = disconnected["board"]
    board["tracks"].append(
        {
            "id": "track:USB_DP:orphan",
            "net": "USB_DP",
            "layer": "F.Cu",
            "width_mm": USB_CONTROLLED_WIDTH_MM,
            "start": [10.0, 10.0],
            "end": [11.0, 10.0],
        }
    )
    board["connectivity"]["USB_DP"]["components"].append(
        {
            "pads": [],
            "copper_ids": ["track:USB_DP:orphan"],
        }
    )
    _assert_report_schema(disconnected)

    with pytest.raises(AssertionError, match="one connected component"):
        _assert_usb_connectivity(disconnected)


def test_inspector_timeout_has_a_clear_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / INSPECTOR
    script.parent.mkdir(parents=True)
    script.write_text("# timeout fixture\n", encoding="utf-8")

    def raise_timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(
        pytest.fail.Exception,
        match=r"timed out after 30 seconds",
    ):
        _run_inspector(tmp_path)


def test_system_python_inspector_emits_versioned_json(
    esp32tap_dir: Path,
) -> None:
    assert SYSTEM_PYTHON.is_file()
    _run_inspector(esp32tap_dir)


def test_pcb_generator_is_byte_reproducible_and_leaves_no_sidecars(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "Esp32Tap.kicad_pcb"
    completed = subprocess.run(
        [
            str(SYSTEM_PYTHON),
            "tools/gen_pcb.py",
            "--output",
            str(output),
        ],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert output.read_bytes() == (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb").read_bytes()
    assert {path.name for path in tmp_path.iterdir()} == {"Esp32Tap.kicad_pcb"}


def test_footprint_generation_uses_only_pinned_project_sources(
    esp32tap_dir: Path,
) -> None:
    generator = (esp32tap_dir / "tools" / "gen_footprints.py").read_text(encoding="utf-8")
    assert "/usr/share/kicad/footprints" not in generator
    expected = {
        "RJ45-SMD_441440003.kicad_mod": "aa1fe4ddaf8087ef440e4d2f76aa3db133c3651048a906d319bdc70c4fac92af",
        "ESP32-S3-WROOM-1.kicad_mod": "b7f7c0eb5ecd56a08d127f464d0b0ffb5dc5e2b685bb493de1d731654e57bbd3",
    }
    for filename, digest in expected.items():
        source = esp32tap_dir / "tools" / "footprint_sources" / filename
        assert source.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        assert digest in generator


def test_compaction_locks_explicit_coupled_groups_and_neighbors(
    esp32tap_dir: Path,
) -> None:
    completed = subprocess.run(
        [
            str(SYSTEM_PYTHON),
            "-c",
            (
                "import json, sys; "
                "sys.path.insert(0, 'tools'); "
                "import gen_pcb; "
                "print(json.dumps({'positions': {ref: gen_pcb.PLACE[ref] "
                "for ref in ('J3', 'U3', 'Q2', 'R29', 'SW2', 'LED1', "
                "'R4', 'R5', 'R11', 'R31', 'C11', 'TP5', 'TP13', "
                "'J1', 'J2', 'K1', 'D4', 'D5', 'D6', 'D7')}, "
                "'deltas': gen_pcb.COMPACT_X_DELTAS, "
                "'edge_route_x': gen_pcb.usb_edge_x(93.4)}))"
            ),
        ],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    compact = json.loads(completed.stdout)
    positions = compact["positions"]
    assert positions == {
        "J3": [91.2, 36.5, 90],
        "U3": [82.0, 35.0, 180],
        "Q2": [84.0, 46.5, 0],
        "R29": [80.0, 48.0, 0],
        "SW2": [91.0, 17.0, 0],
        "LED1": [93.0, 10.0, 180],
        "R4": [94.0, 43.5, 270],
        "R5": [94.0, 29.0, 90],
        "R11": [90.0, 10.0, 0],
        "R31": [90.0, 13.0, 90],
        "C11": [87.0, 43.0, 0],
        "TP5": [49.0, 36.0, 0],
        "TP13": [74.6, 36.0, 0],
        "J1": [8.0, 15.0, 90],
        "J2": [8.0, 37.0, 90],
        "K1": [30.2, 23.0, 0],
        "D4": [30.0, 11.5, 0],
        "D5": [27.0, 15.0, 270],
        "D6": [29.0, 47.0, 270],
        "D7": [38.0, 38.0, 270],
    }
    assert compact["deltas"] == {
        "J3": -5.0,
        "U3": -5.0,
        "SW2": -3.0,
        **{f"TP{number}": -5.0 for number in range(5, 14)},
    }
    assert _distance(positions["Q2"], positions["R29"]) >= 4.0
    assert compact["edge_route_x"] == pytest.approx(88.4)


def test_u1_profile_correction_translates_its_coupled_cluster_together(
    esp32tap_dir: Path,
) -> None:
    completed = subprocess.run(
        [
            str(SYSTEM_PYTHON),
            "-c",
            (
                "import json, sys; sys.path.insert(0, 'tools'); "
                "import gen_pcb; print(json.dumps({"
                "'shift': gen_pcb.U1_PROFILE_SHIFT_Y, "
                "'usb_shift': gen_pcb.USB_ROUTE_SHIFT_Y, "
                "'positions': {ref: gen_pcb.PLACE[ref] for ref in "
                "gen_pcb.U1_COUPLED_REFS}, "
                "'escapes': {'.'.join(key): value for key, value in "
                "gen_pcb.LOCKED_DFM_ESCAPES.items() if key in "
                "gen_pcb.U1_COUPLED_ESCAPE_KEYS}}))"
            ),
        ],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["shift"] == pytest.approx(6.6)
    assert observed["usb_shift"] == pytest.approx(6.6)
    baselines = {
        "U1": (78.0, 6.45, 0),
        "R7": (64.0, 13.0, 0),
        "R8": (64.0, 10.5, 0),
        "R13": (60.0, 12.0, 90),
        "R15": (66.5, 15.5, 0),
        "R16": (66.5, 17.5, 0),
        "C8": (66.5, 6.5, 90),
        "C9": (66.5, 2.0, 90),
        "C10": (60.0, 8.0, 90),
        "C13": (67.3, 12.8, 90),
        "C14": (67.3, 20.0, 270),
    }
    for ref, baseline in baselines.items():
        assert observed["positions"][ref] == pytest.approx([baseline[0], baseline[1] + 6.6, baseline[2]])
    assert observed["escapes"] == {
        "C10.1": pytest.approx([60.0, 16.0]),
        "R13.2": pytest.approx([60.0, 16.0]),
        "C9.2": pytest.approx([65.2, 7.6]),
        "R8.2": pytest.approx([64.4, 14.8]),
    }


def test_power_corridors_preserve_a_locked_safety_route_priority(
    esp32tap_dir: Path,
    kicad_report: dict[str, Any],
) -> None:
    completed = subprocess.run(
        [
            str(SYSTEM_PYTHON),
            "-c",
            (
                "import json, sys; sys.path.insert(0, 'tools'); "
                "import gen_pcb; print(json.dumps({"
                "'priority': gen_pcb.SAFETY_RELAY_PRIORITY, "
                "'cluster': gen_pcb.U1_CLUSTER_PRIORITY, "
                "'tx_gate_escape': gen_pcb.LOCKED_DFM_ESCAPES[('TP10', '1')], "
                "'order': gen_pcb.slow_net_order()}))"
            ),
        ],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    observed = json.loads(completed.stdout)
    expected = [
        "CONS6",
        "MOT6",
        "TX_DRV",
        "TX_BUF",
        "TREAD_OK",
        "TREAD_OK_MCU",
        "RELAY_CMD",
        "RELAY_GATE",
        "RELAY_SW",
        "TX_ENABLE",
        "TX_GATE",
        "K1_NC_FB",
        "K1_NO_FB",
        "PIN3",
        "PIN3_RX",
        "PIN5_SAFETY",
        "PIN4_PASS",
        "CONS_RX",
    ]
    assert observed["priority"] == expected
    expected_cluster = [
        "ESP_TX",
        "STATUS_LED",
        "EN",
        "IO0",
        "U0TXD",
        "U0RXD",
    ]
    assert observed["cluster"] == expected_cluster
    assert observed["tx_gate_escape"] == [63.2, 39.2]
    routed_priority = [net for net in expected if net not in {"CONS6"}]
    assert observed["order"][: len(routed_priority)] == routed_priority
    assert observed["order"][len(routed_priority) : len(routed_priority) + len(expected_cluster)] == expected_cluster

    pass_through = [track for track in _board(kicad_report)["tracks"] if track.get("role") == "PASS_THROUGH_2A"]
    assert pass_through
    assert {track["layer"] for track in pass_through if track["net"] == "+8V_RAW" and track["width_mm"] >= 2.0} == {
        "B.Cu"
    }
    assert {track["layer"] for track in pass_through if track["net"] == "GND" and track["width_mm"] >= 2.0} == {"F.Cu"}


def test_checked_in_sources_identify_a_four_layer_rev_d_board(
    esp32tap_dir: Path,
) -> None:
    pcb = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb").read_text(encoding="utf-8")
    schematic = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_sch").read_text(encoding="utf-8")

    assert all(f'"{layer}"' in pcb for layer in EXPECTED_LAYERS)
    assert re.search(r'\(rev\s+"D"\)', schematic)
    assert re.search(r"Esp32Tap\s+rev\s+D", pcb, re.IGNORECASE)
    for marking in ("BYPASS", "EMULATE"):
        assert f'"{marking}"' in pcb


def test_fabrication_package_contains_both_inner_copper_gerbers(
    esp32tap_dir: Path,
) -> None:
    gerber_dir = esp32tap_dir / "kicad" / "gerbers"
    names = {path.name for path in gerber_dir.iterdir() if path.is_file()}
    assert any("In1_Cu" in name for name in names)
    assert any("In2_Cu" in name for name in names)

    archive = esp32tap_dir / "kicad" / "Esp32Tap-gerbers.zip"
    with zipfile.ZipFile(archive) as zipped:
        archived_names = {Path(name).name for name in zipped.namelist()}
    assert any("In1_Cu" in name for name in archived_names)
    assert any("In2_Cu" in name for name in archived_names)


def test_independent_fab_profile_drills_and_antenna_binding(
    esp32tap_dir: Path,
    kicad_report: dict[str, Any],
) -> None:
    gerber_dir = esp32tap_dir / "kicad" / "gerbers"
    profile = _parse_profile_gerber(gerber_dir / "Esp32Tap-Edge_Cuts.gm1")
    drills = _parse_excellon_drills(gerber_dir / "Esp32Tap.drl")

    assert profile["bounds"] == pytest.approx((100.0, -155.0, 195.0, -97.0))
    assert profile["width_mm"] == pytest.approx(95.0)
    assert profile["height_mm"] == pytest.approx(58.0)
    assert {frozenset(segment) for segment in profile["segments"]} == {
        frozenset(((100.0, -97.0), (195.0, -97.0))),
        frozenset(((195.0, -97.0), (195.0, -155.0))),
        frozenset(((195.0, -155.0), (100.0, -155.0))),
        frozenset(((100.0, -155.0), (100.0, -97.0))),
    }
    assert drills
    min_x, min_y, max_x, max_y = profile["bounds"]
    for drill in drills:
        radius = drill["diameter_mm"] / 2
        assert drill["x_mm"] - radius >= min_x
        assert drill["x_mm"] + radius <= max_x
        assert drill["y_mm"] - radius >= min_y
        assert drill["y_mm"] + radius <= max_y

    archive = esp32tap_dir / "kicad" / "Esp32Tap-gerbers.zip"
    binding = hashlib.sha256()
    pcb = esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    binding.update(b"PCB\0" + hashlib.sha256(pcb.read_bytes()).digest())
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {path.name for path in gerber_dir.iterdir() if path.is_file()}
        for name in sorted(zipped.namelist()):
            exported = (gerber_dir / name).read_bytes()
            archived = zipped.read(name)
            assert hashlib.sha256(archived).digest() == hashlib.sha256(exported).digest()
            binding.update(name.encode("utf-8") + b"\0" + hashlib.sha256(archived).digest())
        assert binding.hexdigest() == ("e4f5e16c4de8bf4eb481318ef51577fb26049ace955ddc246b7a7c0078e472fe")

    # Component bodies are not present in Gerber/Excellon. Bind the
    # inspector's antenna proof to this exact reviewed PCB and fab package.
    antenna = _board(kicad_report)["antenna"]
    assert antenna == {
        "reference": "U1",
        "physical_edge_y_mm": 100.3,
        "span_x_mm": [169.0, 187.0],
    }


def test_checked_in_board_contains_exact_rev_c_footprints(
    esp32tap_dir: Path,
) -> None:
    pcb = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb").read_text(encoding="utf-8")

    missing = {
        f"{ref}={footprint}" for ref, footprint in EXPECTED_FOOTPRINTS.items() if f'(footprint "{footprint}"' not in pcb
    }
    assert not missing


@pytest.mark.parametrize("ref", ["SW1", "SW2"])
def test_skrpace010_footprint_matches_the_official_body_and_land_envelope(
    kicad_report: dict[str, Any],
    ref: str,
) -> None:
    footprint = _board(kicad_report)["footprints"][ref]
    body = footprint["fabrication_body_bbox"]
    courtyard = footprint["courtyard_bbox"]
    assert body is not None
    assert courtyard is not None
    assert body["max"][0] - body["min"][0] == pytest.approx(4.2)
    assert body["max"][1] - body["min"][1] == pytest.approx(3.2)
    assert courtyard["max"][0] - courtyard["min"][0] == pytest.approx(5.65)
    assert courtyard["max"][1] - courtyard["min"][1] == pytest.approx(3.65)


def test_checked_in_board_has_no_d2_vbus_to_vin_bridge(
    esp32tap_dir: Path,
) -> None:
    pcb = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb").read_text(encoding="utf-8")

    assert not re.search(r'\(property "Reference"\s+"D2"', pcb)


def test_checked_in_usb_copper_has_no_vias_or_back_layer_segments(
    esp32tap_dir: Path,
) -> None:
    pcb = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb").read_text(encoding="utf-8")
    route_blocks = re.findall(
        r"\t\((?:segment|via)\n.*?\n\t\)",
        pcb,
        flags=re.DOTALL,
    )
    usb_blocks = [block for block in route_blocks if re.search(r'\(net "USB_D[NP](?:_MCU|_R)?"\)', block)]
    routed_nets = {
        match.group(1)
        for block in usb_blocks
        if block.startswith("\t(segment")
        if (match := re.search(r'\(net "(USB_D[NP](?:_MCU|_R)?)"\)', block))
    }

    for polarity, path_nets in USB_ROUTE_PATHS.items():
        assert path_nets <= routed_nets, f"{polarity} lacks routed copper on {sorted(path_nets - routed_nets)}"
    assert not [block for block in usb_blocks if block.startswith("\t(via")]
    assert all('(layer "F.Cu")' in block for block in usb_blocks)
    widths = [float(match.group(1)) for block in usb_blocks if (match := re.search(r"\(width ([0-9.]+)\)", block))]
    assert len(widths) == len(usb_blocks)
    assert all(width == pytest.approx(0.20) or width == pytest.approx(USB_CONTROLLED_WIDTH_MM) for width in widths)
    assert sum(width == pytest.approx(0.20) for width in widths) == 4


def test_inspected_board_has_ground_only_on_in1(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    assert board["copper_layers"] == EXPECTED_LAYERS

    in1_tracks = [item for item in board["tracks"] if item.get("layer") == "In1.Cu"]
    in1_zones = [item for item in board["zones"] if item.get("layer") == "In1.Cu"]
    assert in1_zones, "In1.Cu must contain the ground plane"
    assert all(item.get("net") == "GND" for item in in1_tracks + in1_zones)


def test_inspected_board_locks_rev_c_footprints_and_pad_nets(
    kicad_report: dict[str, Any],
) -> None:
    footprints = _board(kicad_report)["footprints"]
    for ref, footprint in EXPECTED_FOOTPRINTS.items():
        assert ref in footprints, ref
        assert footprints[ref].get("footprint") == footprint, ref

    assert _pads(kicad_report, "F1") == {"1": "+8V_RAW", "2": "+8V_F"}
    assert _pads(kicad_report, "D3") == {"1": "VIN", "2": "GND"}
    assert set(_pads(kicad_report, "D4").values()) == {
        "+5V_RLY",
        "RELAY_SW",
    }
    assert _pads(kicad_report, "K1") == {
        "1": "+5V_RLY",
        "2": "CONS6",
        "3": "MOT6",
        "4": "TX_DRV",
        "5": "K1_NO_FB",
        "6": "GND",
        "7": "K1_NC_FB",
        "8": "RELAY_SW",
    }
    assert _pads(kicad_report, "Q1") == {
        "1": "Q1_B",
        "2": "GND",
        "3": "RELAY_SW",
    }
    assert _pads(kicad_report, "U4") == {
        "1": "TREAD_OK",
        "2": "GND",
        "3": "UV_SENSE",
        "4": "OV_SENSE",
        "5": "VIN",
        "6": "TREAD_OK",
    }
    assert _pads(kicad_report, "U5") == {
        "1": "VIN",
        "2": "GND",
        "3": "RELAY_GATE",
        "4": "unconnected-(U5-NC-Pad4)",
        "5": "+5V_RLY",
    }
    assert _pads(kicad_report, "U7") == {
        "1": "TX_GATE",
        "2": "ESP_TX",
        "3": "GND",
        "4": "TX_BUF",
        "5": "+3V3",
    }


def test_inspected_board_preserves_schematic_dnp_and_nc_parity(
    kicad_report: dict[str, Any],
    design: Any,
) -> None:
    footprints = _board(kicad_report)["footprints"]
    for reference in design.DNP:
        assert footprints[reference]["dnp"] is True
        assert footprints[reference]["excluded_from_bom"] is True

    for reference, pad in design.NC:
        pin_name = design.COMPONENTS[reference][7][pad]
        expected = f"unconnected-({reference}-{pin_name}-Pad{pad})"
        assert footprints[reference]["pads"][pad]["net"] == expected
    assert _pads(kicad_report, "Q2") == {
        "1": "VBUS",
        "2": "GND",
        "3": "VBUS_PRESENT_N",
    }
    u6 = _pads(kicad_report, "U6")
    equations = {
        (
            frozenset({u6[input_a], u6[input_b]}),
            u6[output],
        )
        for input_a, input_b, output in (("1", "2", "7"), ("5", "6", "3"))
    }
    assert u6["4"] == "GND"
    assert u6["8"] == "+3V3"
    assert equations == {
        (frozenset({"RELAY_CMD", "TREAD_OK"}), "RELAY_GATE"),
        (frozenset({"TX_ENABLE", "TREAD_OK"}), "TX_GATE"),
    }

    passive_pad_locks = {
        "C2": {"1": "VIN", "2": "GND"},
        "C3": {"1": "VIN", "2": "GND"},
        "C6": {"1": "+3V3", "2": "GND"},
        "C7": {"1": "+3V3", "2": "GND"},
        "C12": {"1": "+3V3", "2": "FB"},
        "C13": {"1": "USB_DN_R", "2": "GND"},
        "C14": {"1": "USB_DP_R", "2": "GND"},
        "C15": {"1": "VIN", "2": "GND"},
        "C16": {"1": "+5V_RLY", "2": "GND"},
        "C17": {"1": "VIN", "2": "GND"},
        "C18": {"1": "UV_SENSE", "2": "GND"},
        "C19": {"1": "OV_SENSE", "2": "GND"},
        "C20": {"1": "+3V3", "2": "GND"},
        "C21": {"1": "+3V3", "2": "GND"},
    }
    for ref, pads in passive_pad_locks.items():
        assert _pads(kicad_report, ref) == pads, ref

    test_pad_nets = {
        "TP5": "VIN",
        "TP6": "+5V_RLY",
        "TP7": "TREAD_OK",
        "TP8": "RELAY_GATE",
        "TP9": "RELAY_SW",
        "TP10": "TX_GATE",
        "TP11": "TX_DRV",
        "TP12": "K1_NC_FB",
        "TP13": "K1_NO_FB",
    }
    for ref, net in test_pad_nets.items():
        assert _pads(kicad_report, ref) == {"1": net}, ref


def test_inspected_usb_pair_is_front_copper_only(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    _assert_usb_connectivity(kicad_report)
    tracks = [item for item in board["tracks"] if item.get("net") in USB_ROUTE_NETS]
    vias = [item for item in board["vias"] if item.get("net") in USB_ROUTE_NETS]

    routed_nets = {item.get("net") for item in tracks}
    for polarity, path_nets in USB_ROUTE_PATHS.items():
        assert path_nets <= routed_nets, f"{polarity} lacks routed copper on {sorted(path_nets - routed_nets)}"
    assert {item.get("layer") for item in tracks} == {"F.Cu"}
    breakout = [item for item in tracks if item.get("role") == "CONNECTOR_BREAKOUT"]
    assert len(breakout) == 4
    assert all(item.get("width_mm") == pytest.approx(0.20) and _track_length(item) <= 2.0 for item in breakout)
    assert all(
        item.get("width_mm") == pytest.approx(USB_CONTROLLED_WIDTH_MM)
        for item in tracks
        if item.get("role") != "CONNECTOR_BREAKOUT"
    )
    assert not vias
    j3 = _pads(kicad_report, "J3")
    assert {pad: j3[pad] for pad in ("A6", "B6", "A7", "B7")} == {
        "A6": "USB_DP",
        "B6": "USB_DP",
        "A7": "USB_DN",
        "B7": "USB_DN",
    }
    assert _pads(kicad_report, "U3") == {
        "1": "USB_DN",
        "2": "GND",
        "3": "USB_DP",
        "4": "USB_DP_MCU",
        "5": "VBUS",
        "6": "USB_DN_MCU",
    }
    assert _pads(kicad_report, "R15") == {
        "1": "USB_DN_MCU",
        "2": "USB_DN_R",
    }
    assert _pads(kicad_report, "R16") == {
        "1": "USB_DP_MCU",
        "2": "USB_DP_R",
    }
    u1 = _pads(kicad_report, "U1")
    assert u1["13"] == "USB_DN_R"
    assert u1["14"] == "USB_DP_R"


def test_inspected_vbus_cannot_reach_vin(
    kicad_report: dict[str, Any],
) -> None:
    footprints = _board(kicad_report)["footprints"]
    assert "D2" not in footprints
    assert _nodes_on_net(kicad_report, "VBUS") == {
        ("J3", "A4"),
        ("J3", "A9"),
        ("J3", "B4"),
        ("J3", "B9"),
        ("U3", "5"),
        ("C11", "1"),
        ("R29", "1"),
        ("Q2", "1"),
    }

    vbus_to_vin_bridges = {
        ref
        for ref, footprint in footprints.items()
        if {"VBUS", "VIN"} <= {pad["net"] for pad in footprint["pads"].values()}
    }
    assert not vbus_to_vin_bridges


def test_inspected_title_and_silkscreen_are_rev_d(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    assert board["title"] == "Esp32Tap - ESP32-S3 Precor serial-bus tap"
    assert board["revision"] == "D"

    front_silk = [item for item in board["texts"] if item.get("layer") in {"F.SilkS", "F.Silkscreen"}]
    rendered = "\n".join(str(item.get("text", "")) for item in front_silk)
    assert re.search(r"Esp32Tap\s+rev\s+D", rendered, re.IGNORECASE)
    assert "BYPASS" in rendered
    assert "EMULATE" in rendered


def test_board_outline_and_named_jlc_stackup_are_exact(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    outline = board["outline"]
    assert outline["width_mm"] == pytest.approx(95.0, abs=0.001)
    assert outline["height_mm"] == pytest.approx(58.0, abs=0.001)
    assert outline["area_mm2"] == pytest.approx(5510.0, abs=0.1)

    stackup = board["stackup"]
    assert stackup["name"] == "JLC04161H-7628"
    assert stackup["finished_thickness_mm"] == pytest.approx(1.59)
    observed = [
        (
            layer["name"],
            layer["type"],
            layer["thickness_mm"],
            layer["epsilon_r"],
        )
        for layer in stackup["layers"]
    ]
    assert len(observed) == len(EXPECTED_STACKUP)
    for actual, expected in zip(observed, EXPECTED_STACKUP):
        assert actual[:2] == expected[:2]
        assert actual[2] == pytest.approx(expected[2])
        if expected[3] is None:
            assert actual[3] is None
        else:
            assert actual[3] == pytest.approx(expected[3])


def test_every_footprint_courtyard_is_inside_the_board(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    outline = board["outline"]
    # U1's stock courtyard includes the 15 mm off-board axial antenna
    # clearance convention.  PCB containment is proved from the physical
    # fabrication body, antenna edge/span, and the named copper keepout.
    intentional_edge_features = {"J1", "J2", "J3", "U1"}
    for ref, footprint in board["footprints"].items():
        courtyard = footprint["courtyard_bbox"]
        if courtyard is None or ref in intentional_edge_features:
            continue
        assert courtyard["min"][0] >= outline["min"][0] - 0.001, ref
        assert courtyard["min"][1] >= outline["min"][1] - 0.001, ref
        assert courtyard["max"][0] <= outline["max"][0] + 0.001, ref
        assert courtyard["max"][1] <= outline["max"][1] + 0.001, ref

    sw2 = board["footprints"]["SW2"]["courtyard_bbox"]
    assert sw2 is not None
    assert outline["max"][0] - sw2["max"][0] >= 1.0
    for ref in ("J1", "J2", "J3"):
        body = board["footprints"][ref]["fabrication_body_bbox"]
        assert body is not None
        assert body["min"][0] >= outline["min"][0]
        assert body["min"][1] >= outline["min"][1]
        assert body["max"][0] <= outline["max"][0]
        assert body["max"][1] <= outline["max"][1]

    u1 = board["footprints"]["U1"]
    antenna = board["antenna"]
    body = u1["fabrication_body_bbox"]
    assert body is not None
    assert body["min"][0] >= outline["min"][0]
    assert body["min"][1] >= outline["min"][1]
    assert body["max"][0] <= outline["max"][0]
    assert body["max"][1] <= outline["max"][1]
    assert antenna["physical_edge_y_mm"] >= outline["min"][1]
    assert body["min"][1] - outline["min"][1] >= 2.5
    assert antenna["physical_edge_y_mm"] - outline["min"][1] >= 2.5
    assert antenna["span_x_mm"][0] >= outline["min"][0]
    assert antenna["span_x_mm"][1] <= outline["max"][0]


def test_mounting_hole_drills_and_courtyards_are_inside_profile(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    outline = board["outline"]
    for ref in ("MH1", "MH2", "MH3"):
        footprint = board["footprints"][ref]
        courtyard = footprint["courtyard_bbox"]
        assert courtyard is not None
        assert courtyard["min"][0] >= outline["min"][0], ref
        assert courtyard["min"][1] >= outline["min"][1], ref
        assert courtyard["max"][0] <= outline["max"][0], ref
        assert courtyard["max"][1] <= outline["max"][1], ref
        pad = footprint["pads"]["mount"]
        assert pad["attribute"] == "npth"
        drill_radius = max(pad["drill_mm"]) / 2
        assert pad["at"][0] - drill_radius >= outline["min"][0], ref
        assert pad["at"][1] - drill_radius >= outline["min"][1], ref
        assert pad["at"][0] + drill_radius <= outline["max"][0], ref
        assert pad["at"][1] + drill_radius <= outline["max"][1], ref


def test_enclosure_geometry_is_explicit_and_board_derived(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    origin = board["outline"]["min"]
    expected_mounting = {
        "MH1": [20.0, 6.0],
        "MH2": [48.0, 6.0],
        "MH3": [92.0, 55.0],
    }
    for reference, local_position in expected_mounting.items():
        observed = board["footprints"][reference]["at"]
        assert observed == pytest.approx(
            [
                origin[0] + local_position[0],
                origin[1] + local_position[1],
            ]
        )

    antenna = board["antenna"]
    assert antenna["reference"] == "U1"
    assert antenna["physical_edge_y_mm"] >= origin[1]
    assert antenna["span_x_mm"][1] <= board["outline"]["max"][0]


def test_rj45_jacks_face_the_left_board_edge(
    kicad_report: dict[str, Any],
) -> None:
    # Rev D: J1/J2 are the Molex 441440003 right-angle SMD RJ45 (LCSC
    # C585890). Its mating opening/pad row sits near the board edge; the
    # jack's rear mechanical-tab cap (the fabrication body's far X) extends
    # inward toward the board interior -- the opposite geometry from Rev
    # C's Micro-Fit housing, whose mating nose was the part closest to the
    # board edge.
    board = _board(kicad_report)
    outline = board["outline"]
    for ref in ("J1", "J2"):
        connector = board["footprints"][ref]
        body = connector["fabrication_body_bbox"]
        assert connector["rotation_deg"] == pytest.approx(90.0)
        assert body is not None
        assert body["min"][0] >= outline["min"][0]
        pad_row_x = connector["pads"]["1"]["at"][0]
        assert pad_row_x - outline["min"][0] == pytest.approx(2.1, abs=0.05)
        assert pad_row_x < body["max"][0]
        assert body["max"][0] - outline["min"][0] < 20.0


def _assert_exact_non_smt_pad_occurrences(board: dict[str, Any]) -> None:
    physical = {
        ref: [pad for pad in footprint["pad_occurrences"] if pad["attribute"] != "smd"]
        for ref, footprint in board["footprints"].items()
    }
    assert {ref for ref, pads in physical.items() if pads} == {
        "J3",
        "U1",
        "MH1",
        "MH2",
        "MH3",
    }
    for ref in ("MH1", "MH2", "MH3"):
        assert len(physical[ref]) == 1
        pad = physical[ref][0]
        assert pad["number"] == "mount"
        assert pad["attribute"] == "npth"
        assert pad["shape"] == pad["drill_shape"] == "circle"
        assert pad["size_mm"] == [2.7, 2.7]
        assert pad["drill_mm"] == [2.7, 2.7]

    u1 = physical["U1"]
    assert len(u1) == 12
    assert all(pad["number"] == "41" for pad in u1)
    assert all(pad["attribute"] == "pth" for pad in u1)
    assert all(pad["shape"] == pad["drill_shape"] == "circle" for pad in u1)
    assert all(pad["size_mm"] == [0.6, 0.6] for pad in u1)
    assert all(pad["drill_mm"] == [0.2, 0.2] for pad in u1)

    j3 = physical["J3"]
    assert len(j3) == 6
    npth = [pad for pad in j3 if pad["attribute"] == "npth"]
    slots = [pad for pad in j3 if pad["attribute"] == "pth"]
    assert len(npth) == 2
    assert all(pad["number"] == "mount" for pad in npth)
    assert all(pad["shape"] == pad["drill_shape"] == "circle" for pad in npth)
    assert all(pad["size_mm"] == [0.65, 0.65] for pad in npth)
    assert all(pad["drill_mm"] == [0.65, 0.65] for pad in npth)
    assert len(slots) == 4
    assert all(pad["number"] == "S1" for pad in slots)
    assert all(pad["shape"] == "oval" for pad in slots)
    assert all(pad["drill_shape"] == "oblong" for pad in slots)
    signatures = sorted((tuple(pad["size_mm"]), tuple(pad["drill_mm"])) for pad in slots)
    assert signatures == [
        ((1.0, 1.6), (0.6, 1.2)),
        ((1.0, 1.6), (0.6, 1.2)),
        ((1.0, 2.1), (0.6, 1.7)),
        ((1.0, 2.1), (0.6, 1.7)),
    ]


def test_only_exact_documented_physical_pads_may_be_non_smt(
    kicad_report: dict[str, Any],
) -> None:
    _assert_exact_non_smt_pad_occurrences(_board(kicad_report))


@pytest.mark.parametrize("mutation", ("loss", "drill-change"))
def test_physical_pad_gate_rejects_occurrence_mutations(
    kicad_report: dict[str, Any],
    mutation: str,
) -> None:
    board = copy.deepcopy(_board(kicad_report))
    pads = board["footprints"]["U1"]["pad_occurrences"]
    target = next(pad for pad in pads if pad["number"] == "41" and pad["attribute"] == "pth")
    if mutation == "loss":
        pads.remove(target)
    else:
        target["drill_mm"] = [0.25, 0.25]
    with pytest.raises(AssertionError):
        _assert_exact_non_smt_pad_occurrences(board)


def _assert_stock_u1_manufacturer_keepout(board: dict[str, Any]) -> None:
    keepouts = board["footprints"]["U1"]["manufacturer_keepouts"]
    assert len(keepouts) == 1
    keepout = keepouts[0]
    assert keepout["id"] == "U1:manufacturer_keepout:00"
    assert keepout["source_file"] == ("tools/footprint_sources/ESP32-S3-WROOM-1.kicad_mod")
    assert keepout["source_sha256"] == ("b7f7c0eb5ecd56a08d127f464d0b0ffb5dc5e2b685bb493de1d731654e57bbd3")
    assert keepout["layers"] == EXPECTED_LAYERS
    assert keepout["outline"] == [
        [154.0, 106.3],
        [202.0, 106.3],
        [202.0, 85.3],
        [154.0, 85.3],
    ]
    assert keepout["forbid_tracks"]
    assert keepout["forbid_vias"]
    assert keepout["forbid_pads"]
    assert keepout["forbid_footprints"]
    assert keepout["forbid_zone_fills"]


def test_stock_u1_manufacturer_keepout_is_exactly_source_bound(
    kicad_report: dict[str, Any],
) -> None:
    _assert_stock_u1_manufacturer_keepout(_board(kicad_report))


@pytest.mark.parametrize(
    "mutation",
    ("loss", "geometry", "source-hash", "layers", "behavior"),
)
def test_stock_u1_keepout_gate_rejects_mutations(
    kicad_report: dict[str, Any],
    mutation: str,
) -> None:
    board = copy.deepcopy(_board(kicad_report))
    keepouts = board["footprints"]["U1"]["manufacturer_keepouts"]
    if mutation == "loss":
        keepouts.clear()
    elif mutation == "geometry":
        keepouts[0]["outline"][0][0] += 0.1
    elif mutation == "source-hash":
        keepouts[0]["source_sha256"] = "0" * 64
    elif mutation == "layers":
        keepouts[0]["layers"].remove("B.Cu")
    else:
        keepouts[0]["forbid_pads"] = False
    with pytest.raises(AssertionError):
        _assert_stock_u1_manufacturer_keepout(board)


def test_named_antenna_keepout_is_all_copper_and_explicit_exception(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    keepouts = [area for area in board["rule_areas"] if area["name"] == "ESP32_ANTENNA_ALL_COPPER_KEEPOUT"]
    assert len(keepouts) == 1
    keepout = keepouts[0]
    assert keepout["layers"] == EXPECTED_LAYERS
    assert keepout["forbid_tracks"]
    assert keepout["forbid_vias"]
    assert keepout["forbid_zone_fills"]
    assert not keepout["forbid_footprints"]
    assert not keepout["forbid_pads"]
    assert len(keepout["outline"]) >= 4

    in1 = [zone for zone in board["zones"] if zone["layer"] == "In1.Cu" and zone["net"] == "GND"]
    assert len(in1) == 1
    assert in1[0].get("explicit_exceptions") == ["ESP32_ANTENNA_ALL_COPPER_KEEPOUT"]


def test_inner_and_bottom_layer_policy_is_locked(
    kicad_report: dict[str, Any],
) -> None:
    tracks = _board(kicad_report)["tracks"]
    assert not [track for track in tracks if track["layer"] == "In1.Cu" and track["net"] != "GND"]
    in2_forbidden = USB_ROUTE_NETS | {"SW_NODE", "BST", "FB"}
    assert not [track for track in tracks if track["layer"] == "In2.Cu" and track["net"] in in2_forbidden]
    bottom_forbidden = USB_ROUTE_NETS | {
        "SW_NODE",
        "BST",
        "FB",
        "UV_SENSE",
        "OV_SENSE",
    }
    assert not [track for track in tracks if track["layer"] == "B.Cu" and track["net"] in bottom_forbidden]


def _union_coincident_power_edges(
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse overlapping primitives into their physical copper union.

    Splitting has already reduced all collinear overlaps to identical
    subsegments.  A narrower primitive on the same centreline does not add
    copper in parallel with a wider primitive; the union has the widest
    occupied cross-section.  Distinct geometric branches retain distinct
    keys and therefore remain true parallel routes.
    """
    union: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for edge in edges:
        key = (
            edge["layer"],
            tuple(sorted((edge["a"], edge["b"]), key=repr)),
        )
        if key not in union:
            union[key] = edge
            order.append(key)
            continue
        if edge["width_mm"] > union[key]["width_mm"]:
            union[key] = edge
    return [union[key] for key in order]


def _power_proof(board: dict[str, Any]) -> dict[str, Any]:
    """Solve every single-open case on the exact emitted planar multigraph."""
    expected_tracks = {segment["intent_id"]: segment for segment in power_intent.track_segments((100.0, 100.0))}
    expected_vias = {via["id"]: via for via in power_intent.via_signatures((100.0, 100.0))}
    tracks = [track for track in board["tracks"] if track.get("role") == "PASS_THROUGH_2A"]
    vias = [via for via in board["vias"] if via.get("role") == "PASS_THROUGH_2A"]
    assert {track.get("power_intent_id") for track in tracks} == set(expected_tracks)
    assert {via.get("power_intent_id") for via in vias} == set(expected_vias)
    via_intent_ids = [via.get("power_intent_id") for via in vias]
    assert len(via_intent_ids) == len(expected_vias)
    assert len(set(via_intent_ids)) == len(via_intent_ids), "duplicate coincident intended power-via occurrence"
    # pcbnew stores coordinates as integer nanometres; round-tripping a
    # clean decimal mm value through FromMM()/ToMM() can land 1 nm off the
    # intended value (see inspect_kicad.py's _GEOMETRY_MATCH_DECIMALS
    # note), so endpoint/position comparisons use pytest.approx rather
    # than exact equality.
    for track in tracks:
        expected = expected_tracks[track["power_intent_id"]]
        assert track["net"] == expected["net"]
        assert track["layer"] == expected["layer"]
        assert track["width_mm"] == pytest.approx(expected["width_mm"])
        actual_endpoints = sorted([tuple(track["start"]), tuple(track["end"])])
        expected_endpoints = sorted([tuple(expected["start"]), tuple(expected["end"])])
        for actual_point, expected_point in zip(actual_endpoints, expected_endpoints, strict=True):
            for actual_value, expected_value in zip(actual_point, expected_point, strict=True):
                assert actual_value == pytest.approx(expected_value, abs=1e-4)
    for via in vias:
        expected = expected_vias[via["power_intent_id"]]
        assert via["net"] == expected["net"]
        for actual_value, expected_value in zip(via["at"], expected["at"], strict=True):
            assert actual_value == pytest.approx(expected_value, abs=1e-4)
        assert via["size_mm"] == pytest.approx(expected["size_mm"])
        assert via["drill_mm"] == pytest.approx(expected["drill_mm"])

    rho_105c = 1.724e-8 * (1 + 0.00393 * 85)
    copper_thickness_m = 35e-6
    assumption = power_intent.VIA_BARREL_ASSUMPTION
    assert assumption["plating_thickness_um"] == 20.0
    assert assumption["class"] == "IPC-6012 Class 2"
    assert assumption["evidence"] == (
        "https://jlcpcb.com/blog/pcb-pth",
        "https://www.ipc.org/TOC/IPC-6012F-TOC.pdf",
    )
    assert assumption["live_quote_dfm_confirmation_required"]
    via_plating_m = assumption["plating_thickness_um"] * 1e-6
    board_thickness_m = 1.59e-3
    # 1e-4 mm (0.1 um), not 1e-7: pcbnew stores coordinates as integer
    # nanometres, so round-tripping a clean decimal mm value through
    # FromMM()/ToMM() can land ~1 nm (1e-6 mm) off the intended value --
    # 10x tighter than 1e-7 would tolerate, but a real routing distinction
    # is always at least tens of microns, so 1e-4 cannot create a false
    # intersection/shared-node match.
    epsilon = 1e-4

    def point(layer: str, xy: Any) -> tuple[str, float, float]:
        # Match `epsilon` above (pcbnew ~1 nm round-trip tolerance).
        return (layer, round(xy[0], 4), round(xy[1], 4))

    def planar_edges(net: str) -> list[dict[str, Any]]:
        selected = [track for track in tracks if track["net"] == net]
        split = [{tuple(track["start"]), tuple(track["end"])} for track in selected]

        def cross(a: tuple[float, float], b: tuple[float, float]) -> float:
            return a[0] * b[1] - a[1] * b[0]

        def sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
            return (a[0] - b[0], a[1] - b[1])

        def on_segment(
            candidate: tuple[float, float],
            start: tuple[float, float],
            end: tuple[float, float],
        ) -> bool:
            if abs(cross(sub(candidate, start), sub(end, start))) > epsilon:
                return False
            return all(
                min(start[index], end[index]) - epsilon <= candidate[index] <= max(start[index], end[index]) + epsilon
                for index in (0, 1)
            )

        for left_index, left in enumerate(selected):
            left_start, left_end = tuple(left["start"]), tuple(left["end"])
            left_vector = sub(left_end, left_start)
            for right_index in range(left_index + 1, len(selected)):
                right = selected[right_index]
                if left["layer"] != right["layer"]:
                    continue
                right_start, right_end = (
                    tuple(right["start"]),
                    tuple(right["end"]),
                )
                right_vector = sub(right_end, right_start)
                denominator = cross(left_vector, right_vector)
                offset = sub(right_start, left_start)
                if abs(denominator) <= epsilon:
                    if abs(cross(offset, left_vector)) > epsilon:
                        continue
                    for candidate in {
                        left_start,
                        left_end,
                        right_start,
                        right_end,
                    }:
                        if on_segment(candidate, left_start, left_end) and on_segment(
                            candidate, right_start, right_end
                        ):
                            split[left_index].add(candidate)
                            split[right_index].add(candidate)
                    continue
                left_fraction = cross(offset, right_vector) / denominator
                right_fraction = cross(offset, left_vector) / denominator
                if -epsilon <= left_fraction <= 1 + epsilon and -epsilon <= right_fraction <= 1 + epsilon:
                    intersection = (
                        round(
                            left_start[0] + left_fraction * left_vector[0],
                            6,
                        ),
                        round(
                            left_start[1] + left_fraction * left_vector[1],
                            6,
                        ),
                    )
                    split[left_index].add(intersection)
                    split[right_index].add(intersection)

        edges: list[dict[str, Any]] = []
        for index, track in enumerate(selected):
            start, end = tuple(track["start"]), tuple(track["end"])
            vector = sub(end, start)
            length_squared = vector[0] ** 2 + vector[1] ** 2
            ordered = sorted(
                split[index],
                key=lambda candidate: ((candidate[0] - start[0]) * vector[0] + (candidate[1] - start[1]) * vector[1])
                / length_squared,
            )
            for split_index, (first, second) in enumerate(zip(ordered, ordered[1:])):
                length_mm = _distance(first, second)
                if length_mm <= epsilon:
                    continue
                resistance = rho_105c * (length_mm / 1000) / (track["width_mm"] / 1000 * copper_thickness_m)
                edges.append(
                    {
                        "id": (f"{track['power_intent_id']}#{split_index}"),
                        "kind": "track",
                        "layer": track["layer"],
                        "width_mm": track["width_mm"],
                        "a": point(track["layer"], first),
                        "b": point(track["layer"], second),
                        "resistance_ohm": resistance,
                    }
                )
        edges = _union_coincident_power_edges(edges)
        for via in vias:
            if via["net"] != net:
                continue
            barrel_area = math.pi * via["drill_mm"] / 1000 * via_plating_m
            edges.append(
                {
                    "id": via["power_intent_id"],
                    "kind": "via",
                    "drill_mm": via["drill_mm"],
                    "barrel_area_m2": barrel_area,
                    "a": point("F.Cu", via["at"]),
                    "b": point("B.Cu", via["at"]),
                    "resistance_ohm": (rho_105c * board_thickness_m / barrel_area),
                }
            )
        return edges

    def solve(
        edges: list[dict[str, Any]],
        source_contacts: list[Any],
        sink_contacts: list[Any],
    ) -> tuple[float, list[tuple[dict[str, Any], float]]]:
        def collapsed(node: Any) -> Any:
            if node in source_contacts:
                return ("SOURCE",)
            if node in sink_contacts:
                return ("SINK",)
            return node

        collapsed_edges = [
            {**edge, "a": collapsed(edge["a"]), "b": collapsed(edge["b"])}
            for edge in edges
            if collapsed(edge["a"]) != collapsed(edge["b"])
        ]
        nodes = sorted(
            {node for edge in collapsed_edges for node in (edge["a"], edge["b"]) if node != ("SINK",)},
            key=repr,
        )
        assert ("SOURCE",) in nodes
        index = {node: offset for offset, node in enumerate(nodes)}
        matrix = [[0.0] * len(nodes) for _ in nodes]
        vector = [0.0] * len(nodes)
        vector[index[("SOURCE",)]] = 2.0
        for edge in collapsed_edges:
            conductance = 1.0 / edge["resistance_ohm"]
            a, b = edge["a"], edge["b"]
            if a != ("SINK",):
                matrix[index[a]][index[a]] += conductance
            if b != ("SINK",):
                matrix[index[b]][index[b]] += conductance
            if a != ("SINK",) and b != ("SINK",):
                matrix[index[a]][index[b]] -= conductance
                matrix[index[b]][index[a]] -= conductance

        # Partial-pivot Gaussian elimination keeps this dependency-free.
        for column in range(len(nodes)):
            pivot = max(
                range(column, len(nodes)),
                key=lambda row: abs(matrix[row][column]),
            )
            assert abs(matrix[pivot][column]) > 1e-12, "open power network"
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
            vector[column], vector[pivot] = vector[pivot], vector[column]
            divisor = matrix[column][column]
            for item in range(column, len(nodes)):
                matrix[column][item] /= divisor
            vector[column] /= divisor
            for row in range(len(nodes)):
                if row == column:
                    continue
                factor = matrix[row][column]
                if factor == 0:
                    continue
                for item in range(column, len(nodes)):
                    matrix[row][item] -= factor * matrix[column][item]
                vector[row] -= factor * vector[column]
        voltages = {node: vector[offset] for node, offset in index.items()}
        voltages[("SINK",)] = 0.0
        currents = [
            (
                edge,
                abs((voltages[edge["a"]] - voltages[edge["b"]]) / edge["resistance_ohm"]),
            )
            for edge in collapsed_edges
        ]
        return voltages[("SOURCE",)] / 2.0, currents

    results: dict[str, Any] = {"nets": {}}
    for net in ("+8V_RAW", "GND"):
        edges = planar_edges(net)
        contacts = {
            ref: {
                number: point("F.Cu", pad["at"])
                for number, pad in board["footprints"][ref]["pads"].items()
                if pad["net"] == net
            }
            for ref in ("J1", "J2")
        }
        cases = []
        # Degraded redundant-contact mode: each external connector has one
        # surviving contact, and that survivor is the ideal source/sink
        # supernode.  Exercise all four combinations rather than choosing a
        # convenient contact pair.
        for open_j1 in contacts["J1"]:
            for open_j2 in contacts["J2"]:
                opened = (("J1", open_j1), ("J2", open_j2))
                source_contacts = [node for number, node in contacts["J1"].items() if number != open_j1]
                sink_contacts = [node for number, node in contacts["J2"].items() if number != open_j2]
                resistance, currents = solve(edges, source_contacts, sink_contacts)
                max_track_rise = 0.0
                max_via_rise = 0.0
                max_track_current = 0.0
                max_via_current = 0.0
                max_via_i2r = 0.0
                for edge, current in currents:
                    if edge["kind"] == "track":
                        area_mil2 = edge["width_mm"] / 0.0254 * 1.378
                        rise = (current / (0.048 * area_mil2**0.725)) ** (1 / 0.44)
                        max_track_rise = max(max_track_rise, rise)
                        max_track_current = max(max_track_current, current)
                    else:
                        area_mil2 = edge["barrel_area_m2"] / (25.4e-6) ** 2
                        rise = (current / (0.024 * area_mil2**0.725)) ** (1 / 0.44)
                        max_via_rise = max(max_via_rise, rise)
                        max_via_current = max(max_via_current, current)
                        max_via_i2r = max(
                            max_via_i2r,
                            current**2 * edge["resistance_ohm"],
                        )
                assert max_track_rise <= 20.0
                assert max_via_rise <= 20.0
                cases.append(
                    {
                        "open": opened,
                        "resistance_ohm": resistance,
                        "max_track_current_a": max_track_current,
                        "max_via_current_a": max_via_current,
                        "max_track_rise_c": max_track_rise,
                        "max_via_rise_c": max_via_rise,
                        "max_via_i2r_w": max_via_i2r,
                    }
                )
        worst = max(cases, key=lambda case: case["resistance_ohm"])
        for metric in (
            "max_track_current_a",
            "max_via_current_a",
            "max_track_rise_c",
            "max_via_rise_c",
            "max_via_i2r_w",
        ):
            worst[metric] = max(case[metric] for case in cases)
        results["nets"][net] = worst
    results["combined_drop_v"] = 2.0 * sum(result["resistance_ohm"] for result in results["nets"].values())
    # The trace-union GND solve deliberately omits the In1 plane and is
    # conservative for end-to-end drop.  Plane current sharing is not
    # uniquely knowable from this model, so qualify every intended GND
    # stitching via independently for the passive-network upper bound: the
    # entire 2 A load through one barrel.
    gnd_via_edges = [edge for edge in planar_edges("GND") if edge["kind"] == "via"]
    assert gnd_via_edges
    envelope_current_a = 2.0
    envelope_rises = []
    envelope_i2r = []
    for edge in gnd_via_edges:
        area_mil2 = edge["barrel_area_m2"] / (25.4e-6) ** 2
        rise = (envelope_current_a / (0.024 * area_mil2**0.725)) ** (1 / 0.44)
        i2r = envelope_current_a**2 * edge["resistance_ohm"]
        assert rise <= 20.0
        envelope_rises.append(rise)
        envelope_i2r.append(i2r)
    results["gnd_via_conservative_envelope"] = {
        "current_a": envelope_current_a,
        "max_rise_c": max(envelope_rises),
        "max_i2r_w": max(envelope_i2r),
    }
    assert results["combined_drop_v"] <= 0.1
    return results


def test_single_open_connector_power_paths_are_sized_for_two_amps(
    kicad_report: dict[str, Any],
) -> None:
    # Rev D: the harness/ pigtail subsystem (and its electrical_limits.json
    # supply-drop model, which combined PCB contact + wire + RJ45
    # termination resistance for a discrete factory harness) is retired
    # along with the Micro-Fit connectors -- the RJ45 jack is now the
    # board-mounted termination directly. `_power_proof` below still
    # solves the actual emitted copper (both nets' resistance, current
    # distribution, and IPC-2152 thermal rise) directly from the
    # generated PCB, independent of any harness-specific evidence file.
    result = _power_proof(_board(kicad_report))
    assert result["nets"]["+8V_RAW"]["resistance_ohm"] == pytest.approx(0.014279, abs=0.000005)
    assert result["nets"]["GND"]["resistance_ohm"] == pytest.approx(0.034911, abs=0.000005)
    assert result["combined_drop_v"] == pytest.approx(0.098380, abs=0.000010)
    assert result["combined_drop_v"] <= 0.1
    assert result["nets"]["+8V_RAW"]["max_via_current_a"] == pytest.approx(0.828913, abs=0.000005)
    assert max(net["max_track_current_a"] for net in result["nets"].values()) == pytest.approx(2.0)
    assert result["gnd_via_conservative_envelope"]["current_a"] == 2.0
    assert result["gnd_via_conservative_envelope"]["max_rise_c"] <= 20.0


def test_power_union_rejects_overlapping_primitive_false_parallelism() -> None:
    narrow = {
        "id": "narrow",
        "kind": "track",
        "layer": "F.Cu",
        "width_mm": 1.0,
        "a": ("F.Cu", 0.0, 0.0),
        "b": ("F.Cu", 1.0, 0.0),
        "resistance_ohm": 0.002,
    }
    wide = {
        **narrow,
        "id": "wide-reversed",
        "width_mm": 2.0,
        "a": narrow["b"],
        "b": narrow["a"],
        "resistance_ohm": 0.001,
    }
    distinct_parallel = {
        **wide,
        "id": "separate-geometry",
        "a": ("F.Cu", 0.0, 1.0),
        "b": ("F.Cu", 1.0, 1.0),
    }
    union = _union_coincident_power_edges([narrow, wide, copy.deepcopy(wide), distinct_parallel])
    assert len(union) == 2
    assert {edge["id"] for edge in union} == {
        "wide-reversed",
        "separate-geometry",
    }
    assert sum(
        1 / edge["resistance_ohm"] for edge in union if {edge["a"], edge["b"]} == {narrow["a"], narrow["b"]}
    ) == pytest.approx(1000.0)


@pytest.mark.parametrize(
    "mutation",
    ("remove-track", "neck-track", "remove-via", "duplicate-via"),
)
def test_power_proof_rejects_intent_mutations(
    kicad_report: dict[str, Any],
    mutation: str,
) -> None:
    board = copy.deepcopy(_board(kicad_report))
    if mutation in {"remove-via", "duplicate-via"}:
        items = board["vias"]
    else:
        items = board["tracks"]
    target = next(item for item in items if item.get("role") == "PASS_THROUGH_2A")
    if mutation == "duplicate-via":
        items.append(copy.deepcopy(target))
    elif mutation.startswith("remove"):
        items.remove(target)
    else:
        target["width_mm"] = 0.25
    with pytest.raises(AssertionError):
        _power_proof(board)


def test_usb_pair_has_exact_gap_match_and_reference_plane(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    polarity_tracks = {
        polarity: [track for track in board["tracks"] if track["net"] in route_nets]
        for polarity, route_nets in USB_ROUTE_PATHS.items()
    }

    def shortest_path_lengths(polarity: str) -> dict[str, float]:
        bridges = (
            ((("U3", "1"), ("U3", "6")), (("R15", "1"), ("R15", "2")))
            if polarity == "D-"
            else ((("U3", "3"), ("U3", "4")), (("R16", "1"), ("R16", "2")))
        )
        graph = _planar_copper_graph(
            [
                (tuple(track["start"]), tuple(track["end"]))
                for track in polarity_tracks[polarity]
                if track.get("role") != "DNP_STUB"
            ],
            polarity,
            tuple(
                (
                    tuple(board["footprints"][left[0]]["pads"][left[1]]["at"]),
                    tuple(board["footprints"][right[0]]["pads"][right[1]]["at"]),
                )
                for left, right in bridges
            ),
        )

        def node(point: list[float]) -> tuple[float, float]:
            return (round(point[0], 6), round(point[1], 6))

        connector_numbers = ("A7", "B7") if polarity == "D-" else ("A6", "B6")
        destination = node(board["footprints"]["U1"]["pads"]["13" if polarity == "D-" else "14"]["at"])
        lengths: dict[str, float] = {}
        for side, number in zip(("A", "B"), connector_numbers):
            start = node(board["footprints"]["J3"]["pads"][number]["at"])
            queue = [(0.0, start)]
            best = {start: 0.0}
            while queue:
                distance, current = heapq.heappop(queue)
                if current == destination:
                    lengths[side] = distance
                    break
                if distance != best[current]:
                    continue
                for segment_length, adjacent in graph.get(current, []):
                    candidate = distance + segment_length
                    if candidate < best.get(adjacent, math.inf):
                        best[adjacent] = candidate
                        heapq.heappush(queue, (candidate, adjacent))
            else:
                pytest.fail(f"{polarity} has no J3 {side}-side-to-U1 copper path")
        return lengths

    lengths = {polarity: shortest_path_lengths(polarity) for polarity in USB_ROUTE_PATHS}
    for side in ("A", "B"):
        assert abs(lengths["D+"][side] - lengths["D-"][side]) <= 0.5, f"{side}-side USB lengths differ: {lengths}"

    coupled = [
        (minus, plus)
        for minus in polarity_tracks["D-"]
        for plus in polarity_tracks["D+"]
        if minus.get("pair_section") and plus.get("pair_section")
        if minus["pair_section"] == plus["pair_section"]
    ]
    assert coupled
    for minus, plus in coupled:
        center_gap = min(
            _point_segment_distance(minus["start"], plus["start"], plus["end"]),
            _point_segment_distance(minus["end"], plus["start"], plus["end"]),
        )
        assert center_gap == pytest.approx(
            USB_CENTER_SPACING_MM,
            abs=0.002,
        )
        edge_gap = center_gap - USB_CONTROLLED_WIDTH_MM
        assert edge_gap == pytest.approx(USB_EDGE_GAP_MM, abs=0.002)
        assert minus.get("reference_plane") == "In1.Cu:GND"
        assert plus.get("reference_plane") == "In1.Cu:GND"


@pytest.mark.parametrize(
    "segments",
    [
        [
            ((0.0, 0.0), (2.0, 0.0)),
            ((2.0, 0.0), (2.0, 1.0)),
            ((2.0, 1.0), (0.0, 1.0)),
            ((0.0, 1.0), (1.0, 0.0)),
        ],
        [
            ((0.0, 0.0), (2.0, 0.0)),
            ((1.0, 0.0), (3.0, 0.0)),
        ],
        [
            ((0.0, 0.0), (2.0, 0.0)),
            ((2.0, 0.0), (2.0, 2.0)),
            ((2.0, 2.0), (0.0, 2.0)),
            ((0.0, 2.0), (1.0, -1.0)),
        ],
    ],
    ids=("endpoint-on-interior", "collinear-overlap", "crossing"),
)
def test_usb_planar_graph_rejects_geometric_rejoins(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> None:
    with pytest.raises(AssertionError, match="copper cycle"):
        _planar_copper_graph(segments, "mutation")


def test_usb_terminations_and_dnp_stubs_are_local(
    kicad_report: dict[str, Any],
) -> None:
    u1_pads = _board(kicad_report)["footprints"]["U1"]["pads"]
    for ref, pad in (("R15", "13"), ("R16", "14")):
        assert (
            _distance(
                _footprint_at(kicad_report, ref),
                u1_pads[pad]["at"],
            )
            <= 3.0
        )
    for net in ("USB_DN_R", "USB_DP_R"):
        dnp_stubs = [track for track in _tracks_on_net(kicad_report, net) if track.get("role") == "DNP_STUB"]
        assert len(dnp_stubs) == 1
        assert _track_length(dnp_stubs[0]) <= 2.0


def test_usb_has_clearance_from_unrelated_front_copper(
    kicad_report: dict[str, Any],
) -> None:
    front = [track for track in _board(kicad_report)["tracks"] if track["layer"] == "F.Cu"]
    usb = [
        track
        for track in front
        if track["net"] in USB_ROUTE_NETS
        and min(track["start"][0], track["end"][0]) <= 178.5
        and max(track["start"][0], track["end"][0]) >= 169.5
        and min(track["start"][1], track["end"][1]) <= 131.8
        and max(track["start"][1], track["end"][1]) >= 129.9
    ]
    unrelated = [track for track in front if track["net"] not in USB_ROUTE_NETS | {"GND"}]
    assert usb
    for usb_track in usb:
        for other in unrelated:
            endpoint_distance = min(
                _point_segment_distance(
                    endpoint,
                    other["start"],
                    other["end"],
                )
                for endpoint in (usb_track["start"], usb_track["end"])
            )
            copper_clearance = endpoint_distance - usb_track["width_mm"] / 2 - other["width_mm"] / 2
            assert copper_clearance >= 0.8 - 0.002, (
                f"{usb_track['net']} too close to {other['net']}: " f"{copper_clearance:.3f} mm"
            )


def test_unrelated_route_vias_clear_controlled_usb_pair(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    usb = [
        track
        for track in board["tracks"]
        if track["net"] in USB_ROUTE_NETS
        and min(track["start"][0], track["end"][0]) <= 178.5
        and max(track["start"][0], track["end"][0]) >= 169.5
        and min(track["start"][1], track["end"][1]) <= 131.8
        and max(track["start"][1], track["end"][1]) >= 129.9
    ]
    vias = [via for via in board["vias"] if via["net"] not in USB_ROUTE_NETS | {"GND"}]
    k1_no_vias = [via for via in vias if via["net"] == "K1_NO_FB"]
    assert usb
    assert k1_no_vias

    # Check the electrical property for every unrelated via.  Locking one
    # historical A* transition allowed a different feedback net to regress.
    for via in vias:
        for usb_track in usb:
            copper_clearance = (
                _point_segment_distance(
                    via["at"],
                    usb_track["start"],
                    usb_track["end"],
                )
                - via["size_mm"] / 2
                - usb_track["width_mm"] / 2
            )
            assert copper_clearance >= 0.8 - 0.002, (
                f"{via['id']} is too close to {usb_track['net']}: " f"{copper_clearance:.3f} mm"
            )


def test_same_net_tracks_cross_vias_on_their_centerline(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    for via in board["vias"]:
        for track in board["tracks"]:
            if track["net"] != via["net"]:
                continue
            centerline_distance = _point_segment_distance(
                via["at"],
                track["start"],
                track["end"],
            )
            if centerline_distance >= (via["size_mm"] / 2 + track["width_mm"] / 2):
                continue
            if (
                min(
                    _distance(via["at"], track["start"]),
                    _distance(via["at"], track["end"]),
                )
                <= via["size_mm"] / 2 + track["width_mm"] / 2
            ):
                continue
            assert centerline_distance <= 0.002, (
                f"{track['id']} crosses {via['id']} off-center by " f"{centerline_distance:.3f} mm"
            )


@pytest.mark.parametrize(
    ("a", "b", "maximum_mm"),
    [
        ("F1", "D1", 10.0),
        ("D1", "D3", 8.0),
        ("U2", "C4", 4.0),
        ("U2", "C5", 3.0),
        ("U2", "L1", 5.0),
        ("L1", "C6", 6.0),
        ("L1", "C7", 7.0),
        ("R1", "C12", 2.0),
        ("U4", "C17", 3.5),
        ("U5", "C15", 3.5),
        ("U5", "C16", 4.0),
        ("U6", "C20", 3.0),
        ("U7", "C21", 3.0),
    ],
)
def test_critical_parts_are_compact(
    kicad_report: dict[str, Any],
    a: str,
    b: str,
    maximum_mm: float,
) -> None:
    assert (
        _distance(
            _footprint_at(kicad_report, a),
            _footprint_at(kicad_report, b),
        )
        <= maximum_mm
    )


def test_buck_hf_bypass_has_a_short_wide_via_free_vin_route(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    u2_vin = board["footprints"]["U2"]["pads"]["3"]["at"]
    c4_vin = board["footprints"]["C4"]["pads"]["1"]["at"]
    matching_tracks = [
        track
        for track in board["tracks"]
        if track["net"] == "VIN"
        and track["layer"] == "F.Cu"
        and track["width_mm"] >= 0.60
        and (
            (_distance(track["start"], u2_vin) <= 0.002 and _distance(track["end"], c4_vin) <= 0.002)
            or (_distance(track["end"], u2_vin) <= 0.002 and _distance(track["start"], c4_vin) <= 0.002)
        )
    ]
    assert len(matching_tracks) == 1
    assert _track_length(matching_tracks[0]) <= 2.5


def test_buck_required_input_cap_has_a_short_wide_via_free_vin_route(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    c4_vin = board["footprints"]["C4"]["pads"]["1"]["at"]
    c3_vin = board["footprints"]["C3"]["pads"]["1"]["at"]
    matching_tracks = [
        track
        for track in board["tracks"]
        if track["net"] == "VIN"
        and track["layer"] == "F.Cu"
        and track["width_mm"] >= 0.60
        and (
            (_distance(track["start"], c4_vin) <= 0.002 and _distance(track["end"], c3_vin) <= 0.002)
            or (_distance(track["end"], c4_vin) <= 0.002 and _distance(track["start"], c3_vin) <= 0.002)
        )
    ]
    assert len(matching_tracks) == 1
    u2_to_c4 = _distance(
        board["footprints"]["U2"]["pads"]["3"]["at"],
        c4_vin,
    )
    assert u2_to_c4 + _track_length(matching_tracks[0]) <= 4.5


def test_buck_bootstrap_cap_has_a_short_direct_boot_route(
    kicad_report: dict[str, Any],
) -> None:
    tracks = _tracks_on_net(kicad_report, "BST")
    assert tracks
    assert all(track["layer"] == "F.Cu" for track in tracks)
    assert sum(_track_length(track) for track in tracks) <= 2.3


def test_fb_and_c12_are_kelvin_routed_away_from_switch_node(
    kicad_report: dict[str, Any],
) -> None:
    fb_tracks = _tracks_on_net(kicad_report, "FB")
    sw_tracks = _tracks_on_net(kicad_report, "SW_NODE")
    assert fb_tracks and sw_tracks
    assert all(track["layer"] == "F.Cu" for track in fb_tracks + sw_tracks)
    assert all(track.get("role") == "KELVIN_FB" for track in fb_tracks)
    for fb in fb_tracks:
        for switch in sw_tracks:
            assert (
                min(
                    _point_segment_distance(
                        endpoint,
                        switch["start"],
                        switch["end"],
                    )
                    for endpoint in (fb["start"], fb["end"])
                )
                >= 0.8
            )


def test_every_decoupler_has_an_adjacent_ground_via(
    kicad_report: dict[str, Any],
) -> None:
    decouplers = {
        "C2",
        "C3",
        "C4",
        "C6",
        "C7",
        "C8",
        "C9",
        "C11",
        "C15",
        "C16",
        "C17",
        "C20",
        "C21",
    }
    board = _board(kicad_report)
    ground_vias = [via for via in board["vias"] if via["net"] == "GND"]
    assert ground_vias
    for ref in decouplers:
        ground_pad = board["footprints"][ref]["pads"]["2"]["at"]
        assert min(_distance(ground_pad, via["at"]) for via in ground_vias) <= 1.5


def _circle_to_axis_aligned_pad_clearance(
    center: list[float],
    radius: float,
    pad_center: list[float],
    half_width: float,
    half_height: float,
) -> float:
    dx = max(abs(center[0] - pad_center[0]) - half_width, 0.0)
    dy = max(abs(center[1] - pad_center[1]) - half_height, 0.0)
    return math.hypot(dx, dy) - radius


@pytest.mark.parametrize(
    (
        "escape_ref",
        "escape_pad_number",
        "clearance_ref",
        "clearance_pad_number",
        "half_width",
        "half_height",
    ),
    [
        ("R13", "2", "R13", "2", 0.475, 0.400),
        ("C9", "2", "C9", "2", 0.475, 0.450),
        ("R12", "2", "R12", "2", 0.400, 0.475),
        ("R22", "1", "R22", "1", 0.400, 0.475),
        ("R8", "2", "R8", "2", 0.400, 0.475),
        ("L1", "2", "C6", "1", 0.575, 1.350),
    ],
)
def test_jlc_flagged_smd_pads_keep_clear_of_same_net_vias(
    kicad_report: dict[str, Any],
    escape_ref: str,
    escape_pad_number: str,
    clearance_ref: str,
    clearance_pad_number: str,
    half_width: float,
    half_height: float,
) -> None:
    board = _board(kicad_report)
    escape_pad = board["footprints"][escape_ref]["pads"][escape_pad_number]
    clearance_pad = board["footprints"][clearance_ref]["pads"][clearance_pad_number]
    same_net_vias = [via for via in board["vias"] if via["net"] == escape_pad["net"]]
    assert same_net_vias, f"{escape_ref}.{escape_pad_number} net lacks any via"
    for via in same_net_vias:
        clearance = _circle_to_axis_aligned_pad_clearance(
            via["at"],
            via["size_mm"] / 2,
            clearance_pad["at"],
            half_width,
            half_height,
        )
        assert clearance >= 0.15 - 0.002, (
            f"{via['id']} is only {clearance:.3f} mm from " f"{clearance_ref}.{clearance_pad_number}"
        )


@pytest.mark.parametrize(
    ("pad_number", "expected_net"),
    [
        ("3", "PIN3"),
        ("4", "PIN4_PASS"),
    ],
)
def test_jlc_flagged_j1_pad_via_escapes_have_vendor_clearance(
    kicad_report: dict[str, Any],
    pad_number: str,
    expected_net: str,
) -> None:
    board = _board(kicad_report)
    pad = board["footprints"]["J1"]["pads"][pad_number]
    assert pad["net"] == expected_net
    same_net_vias = [via for via in board["vias"] if via["net"] == expected_net]
    assert same_net_vias
    for via in same_net_vias:
        clearance = _distance(via["at"], pad["at"]) - 0.750 - via["size_mm"] / 2
        assert clearance >= 0.20 - 0.002, f"{via['id']} is only {clearance:.3f} mm from " f"J1.{pad_number}"


@pytest.mark.parametrize(
    ("ref", "pad_number", "axis", "direction", "pad_half_span"),
    [
        ("R30", "2", 0, 1, 0.400),
        ("R26", "2", 1, -1, 0.400),
    ],
)
def test_jlc_flagged_pad_escapes_do_not_turn_inside_mask_opening(
    kicad_report: dict[str, Any],
    ref: str,
    pad_number: str,
    axis: int,
    direction: int,
    pad_half_span: float,
) -> None:
    board = _board(kicad_report)
    pad = board["footprints"][ref]["pads"][pad_number]
    incident = [
        track
        for track in board["tracks"]
        if track["net"] == pad["net"]
        and track["layer"] == "F.Cu"
        and (_distance(track["start"], pad["at"]) <= 0.002 or _distance(track["end"], pad["at"]) <= 0.002)
    ]
    assert len(incident) == 1
    track = incident[0]
    other = track["end"] if _distance(track["start"], pad["at"]) <= 0.002 else track["start"]
    outward_span = direction * (other[axis] - pad["at"][axis])
    assert outward_span >= pad_half_span + 0.15
    assert any(
        via["net"] == pad["net"] and _distance(via["at"], other) <= 0.002 for via in board["vias"]
    ), f"{ref}.{pad_number} escape must run directly to its via"


def test_fixture_test_pads_have_bottom_no_copper_access(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    test_refs = {f"TP{number}" for number in range(5, 14)}
    area = [candidate for candidate in board["rule_areas"] if candidate["name"] == "TP5_TP13_BOTTOM_FIXTURE"]
    assert len(area) == 1
    fixture = area[0]
    assert "B.Cu" in fixture["layers"]
    assert fixture["forbid_footprints"]
    assert fixture["forbid_pads"]
    assert fixture["forbid_tracks"]
    assert fixture["forbid_vias"]
    assert fixture["forbid_zone_fills"]
    assert all(board["footprints"][ref]["layer"] in {"F.Cu", "F.Courtyard"} for ref in test_refs)
    for ref in test_refs:
        assert board["footprints"][ref].get("fixture_accessible") is True


def test_silkscreen_minimums_and_required_markings(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    # Rev D: 307, not 295 -- the RJ45-SMD_441440003 footprint's silkscreen
    # (front-mating-face marks, courtyard-adjacent outline) has a
    # different graphic count than Rev C's Micro-Fit headers.
    assert board["footprint_silkscreen_graphic_count"] == 307
    front = [text for text in board["texts"] if text["layer"] in {"F.SilkS", "F.Silkscreen"}]
    expected_labels = {
        "ESP32TAP REV D",
        "BYPASS",
        "CONSOLE",
        "D1 K",
        "K D3",
        "EMULATE",
        "K1 P1",
        "LED1 K",
        "K LED2",
        "MOTOR",
        "NC",
        "NO",
        "PIN 1",
        "USB DATA ONLY",
    }
    labels = {text["text"].upper(): text for text in front}
    assert set(labels) == expected_labels
    # Lock the body-clearance-reviewed placements as well as marker direction.
    expected_placements = {
        "BYPASS": ([142.0, 110.0], 0.0),
        "D1 K": ([126.5, 148.0], 90.0),
        "EMULATE": ([129.5, 137.0], 0.0),
        "ESP32TAP REV D": ([158.0, 103.0], 0.0),
        "K1 P1": ([123.0, 119.2], 0.0),
        "LED1 K": ([191.0, 121.0], 0.0),
        "K LED2": ([177.0, 153.0], 0.0),
        "NO": ([142.0, 135.0], 0.0),
        # Rev D: rotated vertical (90 deg) to fit the narrow corridor
        # between the RJ45 jacks' deeper courtyard and D4/D5 (see
        # gen_pcb.py add_silkscreen).
        "MOTOR": ([123.0, 137.0], 90.0),
        "PIN 1": ([123.0, 108.0], 90.0),
        "USB DATA ONLY": ([150.0, 108.0], 0.0),
    }
    for label, (position, rotation) in expected_placements.items():
        assert labels[label]["at"] == position
        assert labels[label]["rotation_deg"] == rotation
    assert board["footprints"]["D3"]["pads"]["1"]["at"][0] < (board["footprints"]["D3"]["pads"]["2"]["at"][0])
    assert board["footprints"]["LED2"]["pads"]["1"]["at"][0] < (board["footprints"]["LED2"]["pads"]["2"]["at"][0])
    assert board["footprints"]["D1"]["pads"]["1"]["at"][0] > (board["footprints"]["D1"]["pads"]["2"]["at"][0])
    assert board["footprints"]["LED1"]["pads"]["1"]["at"][0] > (board["footprints"]["LED1"]["pads"]["2"]["at"][0])
    relay_pad_1 = board["footprints"]["K1"]["pads"]["1"]["at"]
    relay_pad_8 = board["footprints"]["K1"]["pads"]["8"]["at"]
    assert relay_pad_1[0] < relay_pad_8[0]
    assert labels["K1 P1"]["at"][0] < relay_pad_1[0]
    assert labels["K1 P1"]["at"][1] == relay_pad_1[1]
    outline = board["outline"]
    for label, text in labels.items():
        assert text["stroke_width_mm"] >= 0.20, label
        assert text["height_mm"] >= 1.0, label
        assert (
            min(
                text["bbox"]["min"][0] - outline["min"][0],
                text["bbox"]["min"][1] - outline["min"][1],
                outline["max"][0] - text["bbox"]["max"][0],
                outline["max"][1] - text["bbox"]["max"][1],
            )
            >= 0.50
        ), label
        assert text["min_fabrication_clearance_mm"] >= 0.25, (
            f"{label} is only "
            f"{text['min_fabrication_clearance_mm']:.3f} mm from "
            f"{text['nearest_fabrication_obstacle']}"
        )
        assert text["min_component_body_clearance_mm"] >= 0.50, (
            f"{label} is only "
            f"{text['min_component_body_clearance_mm']:.3f} mm from "
            f"{text['nearest_component_body']} nominal body"
        )


def test_silkscreen_gerber_strokes_meet_jlc_minimum(
    esp32tap_dir: Path,
) -> None:
    payloads = [
        (esp32tap_dir / "kicad" / "gerbers" / filename).read_text(encoding="utf-8")
        for filename in (
            "Esp32Tap-F_Silkscreen.gto",
            "Esp32Tap-B_Silkscreen.gbo",
        )
    ]
    aperture_widths = [
        float(width)
        for payload in payloads
        for width in re.findall(
            r"%ADD\d+C,([0-9.]+)\*%",
            payload,
        )
    ]
    assert set(aperture_widths) == {0.20}
    assert all("%TO.C," not in payload for payload in payloads)
    assert re.search(
        r"(?m)^X[-0-9]+Y[-0-9]+D01\*$",
        payloads[0],
    )


def test_project_and_dru_lock_named_usb_geometry(
    esp32tap_dir: Path,
) -> None:
    project = json.loads((esp32tap_dir / "kicad" / "Esp32Tap.kicad_pro").read_text(encoding="utf-8"))
    classes = project["net_settings"]["classes"]
    usb = [item for item in classes if item["name"] == "USB_90R_JLC04161H"]
    assert len(usb) == 1
    assert usb[0]["track_width"] == pytest.approx(USB_CONTROLLED_WIDTH_MM)
    assert usb[0]["diff_pair_width"] == pytest.approx(USB_CONTROLLED_WIDTH_MM)
    assert usb[0]["diff_pair_gap"] == pytest.approx(USB_EDGE_GAP_MM)
    assignments = project["net_settings"]["netclass_assignments"]
    assert assignments
    for net in USB_ROUTE_NETS:
        assert assignments.get(net) in (
            "USB_90R_JLC04161H",
            ["USB_90R_JLC04161H"],
        )

    dru = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_dru").read_text(encoding="utf-8")
    for token in (
        "USB_90R_JLC04161H",
        "0.2906mm",
        "0.200mm",
        "0.8mm",
        "F.Cu",
    ):
        assert token in dru


def test_combined_drc_and_schematic_parity_report_is_clean(
    esp32tap_dir: Path,
) -> None:
    report = (esp32tap_dir / "kicad" / "drc.rpt").read_text(encoding="utf-8")
    assert re.search(r"Found 0 DRC violations", report)
    assert re.search(r"Found 0 unconnected pads", report)
    assert re.search(r"Found 0 Footprint errors", report)
    assert not (esp32tap_dir / "kicad" / "drc-parity.rpt").exists()


def test_default_python_validator_passes(
    esp32tap_dir: Path,
) -> None:
    validator = esp32tap_dir / "tools" / "validate_artifacts.py"
    assert validator.is_file()
    completed = subprocess.run(
        ["python3", str(validator)],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
