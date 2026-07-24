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
        "footprints",
        "tracks",
        "vias",
        "zones",
        "texts",
        "connectivity",
    }
    assert required <= board.keys()
    assert isinstance(board["title"], str)
    assert isinstance(board["revision"], str)
    assert _is_string_list(board["copper_layers"])
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

    copper = board["tracks"] + board["vias"] + board["zones"]
    copper_by_id = {item["id"]: item for item in copper}
    assert len(copper_by_id) == len(copper), "copper IDs must be unique"
    _assert_connectivity_schema(
        board["connectivity"],
        board["footprints"],
        copper_by_id,
    )


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
    assert {"footprint", "layer", "at", "pads"} <= footprint.keys()
    assert isinstance(footprint["footprint"], str) and footprint["footprint"]
    assert isinstance(footprint["layer"], str) and footprint["layer"]
    _assert_xy(footprint["at"], "footprint.at")
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


def _assert_text_schema(text: Any) -> None:
    assert isinstance(text, dict)
    assert {"text", "layer", "stroke_width_mm", "at"} <= text.keys()
    assert isinstance(text["text"], str)
    assert isinstance(text["layer"], str) and text["layer"]
    assert _is_number(text["stroke_width_mm"])
    assert text["stroke_width_mm"] > 0
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
            "footprints": {
                "X1": {
                    "footprint": "Test:Connector",
                    "layer": "F.Cu",
                    "at": [0.0, 0.0],
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
                    "width_mm": 0.285,
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
            "texts": [
                {
                    "text": "Schema fixture",
                    "layer": "F.SilkS",
                    "stroke_width_mm": 0.15,
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
                "width_mm": 0.285,
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
            "width_mm": 0.285,
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
    assert all(re.search(r"\(width 0\.285(?:0*)?\)", block) for block in usb_blocks)


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
        "4": "",
        "5": "+5V_RLY",
    }
    assert _pads(kicad_report, "U7") == {
        "1": "TX_GATE",
        "2": "ESP_TX",
        "3": "GND",
        "4": "TX_BUF",
        "5": "+3V3",
    }
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
    assert all(item.get("width_mm") == pytest.approx(0.285) for item in tracks)
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
