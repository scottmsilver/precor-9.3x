from __future__ import annotations

import copy
import json
import math
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest


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
    **{
        f"TP{number}": "TestPoint:TestPoint_Pad_1.5x1.5mm"
        for number in range(5, 14)
    },
}


def _run_inspector(esp32tap_dir: Path) -> dict[str, Any]:
    script = esp32tap_dir / INSPECTOR
    assert script.is_file(), (
        "Rev B artifact inspector is missing: "
        "tools/inspect_kicad.py must run under /usr/bin/python3"
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
        pytest.fail(
            "inspect_kicad.py --json timed out after "
            f"{INSPECTOR_TIMEOUT_SECONDS} seconds"
        )
    assert completed.returncode == 0, (
        "inspect_kicad.py --json failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
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
    assert {"min", "max", "width_mm", "height_mm"} <= board["outline"].keys()
    _assert_xy(board["outline"]["min"], "outline.min")
    _assert_xy(board["outline"]["max"], "outline.max")
    assert _is_number(board["outline"]["width_mm"])
    assert _is_number(board["outline"]["height_mm"])
    assert isinstance(board["footprints"], dict)
    assert all(
        isinstance(reference, str) and reference
        for reference in board["footprints"]
    )
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
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item for item in value)
    )


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
        "pads",
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
    assert isinstance(footprint["pads"], dict)
    for number, pad in footprint["pads"].items():
        assert isinstance(number, str) and number
        assert isinstance(pad, dict)
        assert {"net", "at", "layers"} <= pad.keys()
        assert isinstance(pad["net"], str)
        _assert_xy(pad["at"], "pad.at")
        assert _is_string_list(pad["layers"])


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
            (
                (point[0] - start[0]) * dx
                + (point[1] - start[1]) * dy
            )
            / (dx * dx + dy * dy),
        ),
    )
    projection = [start[0] + fraction * dx, start[1] + fraction * dy]
    return _distance(point, projection)


def _track_length(track: dict[str, Any]) -> float:
    return _distance(track["start"], track["end"])


def _footprint_at(report: dict[str, Any], ref: str) -> list[float]:
    return _board(report)["footprints"][ref]["at"]


def _tracks_on_net(
    report: dict[str, Any],
    net: str,
) -> list[dict[str, Any]]:
    return [
        track for track in _board(report)["tracks"] if track["net"] == net
    ]


def _assert_usb_connectivity(report: dict[str, Any]) -> None:
    board = _board(report)
    connectivity = board["connectivity"]
    for net, expected_pads in EXPECTED_USB_NET_PADS.items():
        assert net in connectivity, f"{net} lacks connectivity graph data"
        components = connectivity[net]["components"]
        assert len(components) == 1, (
            f"{net} must be one connected component from every endpoint"
        )
        component = components[0]
        actual_pads = {tuple(node) for node in component["pads"]}
        assert actual_pads == expected_pads, (
            f"{net} connectivity endpoints differ: "
            f"missing={sorted(expected_pads - actual_pads)}, "
            f"extra={sorted(actual_pads - expected_pads)}"
        )

        actual_copper_ids = set(component["copper_ids"])
        expected_copper_ids = {
            item["id"]
            for collection in ("tracks", "vias", "zones")
            for item in board[collection]
            if item["net"] == net
        }
        assert actual_copper_ids
        assert actual_copper_ids == expected_copper_ids, (
            f"{net} connectivity must account for every routed copper item"
        )


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
                    "pads": {
                        "1": {
                            "net": "N",
                            "at": [0.0, 0.0],
                            "layers": ["F.Cu"],
                        }
                    },
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
                    "pads": {},
                },
            )
            footprint["pads"][pad] = {
                "net": net,
                "at": [0.0, 0.0],
                "layers": ["F.Cu"],
            }

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
    assert output.read_bytes() == (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    ).read_bytes()
    assert {path.name for path in tmp_path.iterdir()} == {
        "Esp32Tap.kicad_pcb"
    }


def test_checked_in_sources_identify_a_four_layer_rev_b_board(
    esp32tap_dir: Path,
) -> None:
    pcb = (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    ).read_text(encoding="utf-8")
    schematic = (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_sch"
    ).read_text(encoding="utf-8")

    assert all(f'"{layer}"' in pcb for layer in EXPECTED_LAYERS)
    assert re.search(r'\(rev\s+"B"\)', schematic)
    assert re.search(r'Esp32Tap\s+rev\s+B', pcb, re.IGNORECASE)
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


def test_checked_in_board_contains_exact_rev_b_footprints(
    esp32tap_dir: Path,
) -> None:
    pcb = (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    ).read_text(encoding="utf-8")

    missing = {
        f"{ref}={footprint}"
        for ref, footprint in EXPECTED_FOOTPRINTS.items()
        if f'(footprint "{footprint}"' not in pcb
    }
    assert not missing


def test_checked_in_board_has_no_d2_vbus_to_vin_bridge(
    esp32tap_dir: Path,
) -> None:
    pcb = (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    ).read_text(encoding="utf-8")

    assert not re.search(r'\(property "Reference"\s+"D2"', pcb)


def test_checked_in_usb_copper_has_no_vias_or_back_layer_segments(
    esp32tap_dir: Path,
) -> None:
    pcb = (
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb"
    ).read_text(encoding="utf-8")
    route_blocks = re.findall(
        r"\t\((?:segment|via)\n.*?\n\t\)",
        pcb,
        flags=re.DOTALL,
    )
    usb_blocks = [
        block
        for block in route_blocks
        if re.search(r'\(net "USB_D[NP](?:_MCU|_R)?"\)', block)
    ]
    routed_nets = {
        match.group(1)
        for block in usb_blocks
        if block.startswith("\t(segment")
        if (match := re.search(r'\(net "(USB_D[NP](?:_MCU|_R)?)"\)', block))
    }

    for polarity, path_nets in USB_ROUTE_PATHS.items():
        assert path_nets <= routed_nets, (
            f"{polarity} lacks routed copper on {sorted(path_nets - routed_nets)}"
        )
    assert not [block for block in usb_blocks if block.startswith("\t(via")]
    assert all('(layer "F.Cu")' in block for block in usb_blocks)
    widths = [
        float(match.group(1))
        for block in usb_blocks
        if (match := re.search(r"\(width ([0-9.]+)\)", block))
    ]
    assert len(widths) == len(usb_blocks)
    assert all(
        width == pytest.approx(0.20)
        or width == pytest.approx(USB_CONTROLLED_WIDTH_MM)
        for width in widths
    )
    assert sum(width == pytest.approx(0.20) for width in widths) == 4


def test_inspected_board_has_ground_only_on_in1(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    assert board["copper_layers"] == EXPECTED_LAYERS

    in1_tracks = [
        item for item in board["tracks"] if item.get("layer") == "In1.Cu"
    ]
    in1_zones = [
        item for item in board["zones"] if item.get("layer") == "In1.Cu"
    ]
    assert in1_zones, "In1.Cu must contain the ground plane"
    assert all(item.get("net") == "GND" for item in in1_tracks + in1_zones)


def test_inspected_board_locks_rev_b_footprints_and_pad_nets(
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
    tracks = [
        item for item in board["tracks"] if item.get("net") in USB_ROUTE_NETS
    ]
    vias = [
        item for item in board["vias"] if item.get("net") in USB_ROUTE_NETS
    ]

    routed_nets = {item.get("net") for item in tracks}
    for polarity, path_nets in USB_ROUTE_PATHS.items():
        assert path_nets <= routed_nets, (
            f"{polarity} lacks routed copper on {sorted(path_nets - routed_nets)}"
        )
    assert {item.get("layer") for item in tracks} == {"F.Cu"}
    breakout = [
        item for item in tracks if item.get("role") == "CONNECTOR_BREAKOUT"
    ]
    assert len(breakout) == 4
    assert all(
        item.get("width_mm") == pytest.approx(0.20)
        and _track_length(item) <= 2.0
        for item in breakout
    )
    assert all(
        item.get("width_mm") == pytest.approx(USB_CONTROLLED_WIDTH_MM)
        for item in tracks
        if item.get("role") != "CONNECTOR_BREAKOUT"
    )
    assert not vias
    j3 = _pads(kicad_report, "J3")
    assert {
        pad: j3[pad]
        for pad in ("A6", "B6", "A7", "B7")
    } == {
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
        if {"VBUS", "VIN"}
        <= {pad["net"] for pad in footprint["pads"].values()}
    }
    assert not vbus_to_vin_bridges


def test_inspected_title_and_silkscreen_are_rev_b(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    assert board["title"] == "Esp32Tap - ESP32-S3 Precor serial-bus tap"
    assert board["revision"] == "B"

    front_silk = [
        item
        for item in board["texts"]
        if item.get("layer") in {"F.SilkS", "F.Silkscreen"}
    ]
    rendered = "\n".join(str(item.get("text", "")) for item in front_silk)
    assert re.search(r"Esp32Tap\s+rev\s+B", rendered, re.IGNORECASE)
    assert "BYPASS" in rendered
    assert "EMULATE" in rendered


def test_board_outline_and_named_jlc_stackup_are_exact(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    outline = board["outline"]
    assert outline["width_mm"] == pytest.approx(100.0, abs=0.001)
    assert outline["height_mm"] == pytest.approx(55.0, abs=0.001)

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


def test_enclosure_geometry_is_explicit_and_board_derived(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    origin = board["outline"]["min"]
    expected_mounting = {
        "MH1": [2.9, 26.5],
        "MH2": [97.0, 3.0],
        "MH3": [97.0, 52.0],
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
    assert origin[1] - antenna["physical_edge_y_mm"] == pytest.approx(6.3)
    assert antenna["span_x_mm"] == pytest.approx([169.0, 187.0])


def test_named_antenna_keepout_is_all_copper_and_explicit_exception(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    keepouts = [
        area
        for area in board["rule_areas"]
        if area["name"] == "ESP32_ANTENNA_ALL_COPPER_KEEPOUT"
    ]
    assert len(keepouts) == 1
    keepout = keepouts[0]
    assert keepout["layers"] == EXPECTED_LAYERS
    assert keepout["forbid_tracks"]
    assert keepout["forbid_vias"]
    assert keepout["forbid_zone_fills"]
    assert not keepout["forbid_footprints"]
    assert not keepout["forbid_pads"]
    assert len(keepout["outline"]) >= 4

    in1 = [
        zone
        for zone in board["zones"]
        if zone["layer"] == "In1.Cu" and zone["net"] == "GND"
    ]
    assert len(in1) == 1
    assert in1[0].get("explicit_exceptions") == [
        "ESP32_ANTENNA_ALL_COPPER_KEEPOUT"
    ]


def test_inner_and_bottom_layer_policy_is_locked(
    kicad_report: dict[str, Any],
) -> None:
    tracks = _board(kicad_report)["tracks"]
    assert not [
        track
        for track in tracks
        if track["layer"] == "In1.Cu" and track["net"] != "GND"
    ]
    in2_forbidden = USB_ROUTE_NETS | {"SW_NODE", "BST", "FB"}
    assert not [
        track
        for track in tracks
        if track["layer"] == "In2.Cu" and track["net"] in in2_forbidden
    ]
    bottom_forbidden = USB_ROUTE_NETS | {
        "SW_NODE",
        "BST",
        "FB",
        "UV_SENSE",
        "OV_SENSE",
    }
    assert not [
        track
        for track in tracks
        if track["layer"] == "B.Cu" and track["net"] in bottom_forbidden
    ]


def test_usb_pair_has_exact_gap_match_and_reference_plane(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    polarity_tracks = {
        polarity: [
            track
            for track in board["tracks"]
            if track["net"] in route_nets
        ]
        for polarity, route_nets in USB_ROUTE_PATHS.items()
    }
    lengths = {
        polarity: sum(_track_length(track) for track in tracks)
        for polarity, tracks in polarity_tracks.items()
    }
    assert abs(lengths["D+"] - lengths["D-"]) <= 0.5

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


def test_usb_terminations_and_dnp_stubs_are_local(
    kicad_report: dict[str, Any],
) -> None:
    u1_pads = _board(kicad_report)["footprints"]["U1"]["pads"]
    for ref, pad in (("R15", "13"), ("R16", "14")):
        assert _distance(
            _footprint_at(kicad_report, ref),
            u1_pads[pad]["at"],
        ) <= 3.0
    for net in ("USB_DN_R", "USB_DP_R"):
        dnp_stubs = [
            track
            for track in _tracks_on_net(kicad_report, net)
            if track.get("role") == "DNP_STUB"
        ]
        assert len(dnp_stubs) == 1
        assert _track_length(dnp_stubs[0]) <= 2.0


def test_usb_has_clearance_from_unrelated_front_copper(
    kicad_report: dict[str, Any],
) -> None:
    front = [
        track
        for track in _board(kicad_report)["tracks"]
        if track["layer"] == "F.Cu"
    ]
    usb = [
        track
        for track in front
        if track["net"] in USB_ROUTE_NETS and track.get("pair_section")
    ]
    unrelated = [
        track
        for track in front
        if track["net"] not in USB_ROUTE_NETS | {"GND"}
    ]
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
            copper_clearance = (
                endpoint_distance
                - usb_track["width_mm"] / 2
                - other["width_mm"] / 2
            )
            assert copper_clearance >= 0.8 - 0.002, (
                f"{usb_track['net']} too close to {other['net']}: "
                f"{copper_clearance:.3f} mm"
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
    assert _distance(
        _footprint_at(kicad_report, a),
        _footprint_at(kicad_report, b),
    ) <= maximum_mm


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
            (
                _distance(track["start"], u2_vin) <= 0.002
                and _distance(track["end"], c4_vin) <= 0.002
            )
            or (
                _distance(track["end"], u2_vin) <= 0.002
                and _distance(track["start"], c4_vin) <= 0.002
            )
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
            (
                _distance(track["start"], c4_vin) <= 0.002
                and _distance(track["end"], c3_vin) <= 0.002
            )
            or (
                _distance(track["end"], c4_vin) <= 0.002
                and _distance(track["start"], c3_vin) <= 0.002
            )
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
            assert min(
                _point_segment_distance(
                    endpoint,
                    switch["start"],
                    switch["end"],
                )
                for endpoint in (fb["start"], fb["end"])
            ) >= 0.8


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
    clearance_pad = board["footprints"][clearance_ref]["pads"][
        clearance_pad_number
    ]
    same_net_vias = [
        via for via in board["vias"] if via["net"] == escape_pad["net"]
    ]
    assert same_net_vias, (
        f"{escape_ref}.{escape_pad_number} net lacks any via"
    )
    for via in same_net_vias:
        clearance = _circle_to_axis_aligned_pad_clearance(
            via["at"],
            via["size_mm"] / 2,
            clearance_pad["at"],
            half_width,
            half_height,
        )
        assert clearance >= 0.15 - 0.002, (
            f"{via['id']} is only {clearance:.3f} mm from "
            f"{clearance_ref}.{clearance_pad_number}"
        )


@pytest.mark.parametrize(
    ("pad_number", "expected_net"),
    [
        ("3", "PIN3"),
        ("4", "PIN4_PASS"),
        ("8", "+8V_RAW"),
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
    same_net_vias = [
        via for via in board["vias"] if via["net"] == expected_net
    ]
    assert same_net_vias
    for via in same_net_vias:
        clearance = (
            _distance(via["at"], pad["at"])
            - 0.750
            - via["size_mm"] / 2
        )
        assert clearance >= 0.20 - 0.002, (
            f"{via['id']} is only {clearance:.3f} mm from "
            f"J1.{pad_number}"
        )


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
        and (
            _distance(track["start"], pad["at"]) <= 0.002
            or _distance(track["end"], pad["at"]) <= 0.002
        )
    ]
    assert len(incident) == 1
    track = incident[0]
    other = (
        track["end"]
        if _distance(track["start"], pad["at"]) <= 0.002
        else track["start"]
    )
    outward_span = direction * (other[axis] - pad["at"][axis])
    assert outward_span >= pad_half_span + 0.15
    assert any(
        via["net"] == pad["net"]
        and _distance(via["at"], other) <= 0.002
        for via in board["vias"]
    ), f"{ref}.{pad_number} escape must run directly to its via"


def test_fixture_test_pads_have_bottom_no_copper_access(
    kicad_report: dict[str, Any],
) -> None:
    board = _board(kicad_report)
    test_refs = {f"TP{number}" for number in range(5, 14)}
    area = [
        candidate
        for candidate in board["rule_areas"]
        if candidate["name"] == "TP5_TP13_BOTTOM_FIXTURE"
    ]
    assert len(area) == 1
    fixture = area[0]
    assert "B.Cu" in fixture["layers"]
    assert fixture["forbid_footprints"]
    assert fixture["forbid_pads"]
    assert fixture["forbid_tracks"]
    assert fixture["forbid_vias"]
    assert fixture["forbid_zone_fills"]
    assert all(
        board["footprints"][ref]["layer"] in {"F.Cu", "F.Courtyard"}
        for ref in test_refs
    )
    for ref in test_refs:
        assert board["footprints"][ref].get("fixture_accessible") is True


def test_silkscreen_minimums_and_required_markings(
    kicad_report: dict[str, Any],
) -> None:
    front = [
        text
        for text in _board(kicad_report)["texts"]
        if text["layer"] in {"F.SilkS", "F.Silkscreen"}
    ]
    assert front
    assert min(text["stroke_width_mm"] for text in front) >= 0.20
    rendered = " ".join(text["text"] for text in front).upper()
    for required in (
        "REV B",
        "BYPASS",
        "EMULATE",
        "CONSOLE",
        "MOTOR",
        "USB DATA ONLY",
        "PIN 1",
        "D3 K",
        "D1 K",
    ):
        assert required in rendered
    critical_labels = {
        text["text"].upper(): text
        for text in front
        if text["text"].upper()
        in {"USB DATA ONLY", "PIN 1", "D1 K", "D3 K"}
    }
    assert set(critical_labels) == {
        "USB DATA ONLY",
        "PIN 1",
        "D1 K",
        "D3 K",
    }
    assert all(
        text.get("height_mm", 0.0) >= 1.0
        for text in critical_labels.values()
    )


def test_silkscreen_gerber_strokes_meet_jlc_minimum(
    esp32tap_dir: Path,
) -> None:
    aperture_widths = [
        float(width)
        for filename in (
            "Esp32Tap-F_Silkscreen.gto",
            "Esp32Tap-B_Silkscreen.gbo",
        )
        for width in re.findall(
            r"%ADD\d+C,([0-9.]+)\*%",
            (
                esp32tap_dir / "kicad" / "gerbers" / filename
            ).read_text(encoding="utf-8"),
        )
    ]
    assert aperture_widths
    assert min(aperture_widths) >= 0.16


def test_project_and_dru_lock_named_usb_geometry(
    esp32tap_dir: Path,
) -> None:
    project = json.loads(
        (esp32tap_dir / "kicad" / "Esp32Tap.kicad_pro").read_text(
            encoding="utf-8"
        )
    )
    classes = project["net_settings"]["classes"]
    usb = [item for item in classes if item["name"] == "USB_90R_JLC04161H"]
    assert len(usb) == 1
    assert usb[0]["track_width"] == pytest.approx(
        USB_CONTROLLED_WIDTH_MM
    )
    assert usb[0]["diff_pair_width"] == pytest.approx(
        USB_CONTROLLED_WIDTH_MM
    )
    assert usb[0]["diff_pair_gap"] == pytest.approx(USB_EDGE_GAP_MM)
    assignments = project["net_settings"]["netclass_assignments"]
    assert assignments
    for net in USB_ROUTE_NETS:
        assert assignments.get(net) in (
            "USB_90R_JLC04161H",
            ["USB_90R_JLC04161H"],
        )

    dru = (esp32tap_dir / "kicad" / "Esp32Tap.kicad_dru").read_text(
        encoding="utf-8"
    )
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
    report = (esp32tap_dir / "kicad" / "drc.rpt").read_text(
        encoding="utf-8"
    )
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
