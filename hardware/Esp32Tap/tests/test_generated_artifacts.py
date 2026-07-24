from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest


SYSTEM_PYTHON = Path("/usr/bin/python3")
INSPECTOR = Path("tools/inspect_kicad.py")
EXPECTED_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
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
    completed = subprocess.run(
        [str(SYSTEM_PYTHON), str(INSPECTOR), "--json"],
        cwd=esp32tap_dir,
        check=False,
        capture_output=True,
        text=True,
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
    }
    assert required <= board.keys()
    assert isinstance(board["copper_layers"], list)
    assert isinstance(board["footprints"], dict)
    for key in ("tracks", "vias", "zones", "texts"):
        assert isinstance(board[key], list), key


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
    return pads


def _nodes_on_net(
    report: dict[str, Any],
    net: str,
) -> set[tuple[str, str]]:
    return {
        (ref, pad)
        for ref, footprint in _board(report)["footprints"].items()
        for pad, pad_net in footprint["pads"].items()
        if pad_net == net
    }


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

    assert usb_blocks, "checked-in PCB must route the native USB nets"
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
    usb_nets = {
        "USB_DN",
        "USB_DP",
        "USB_DN_MCU",
        "USB_DP_MCU",
        "USB_DN_R",
        "USB_DP_R",
    }
    tracks = [
        item for item in board["tracks"] if item.get("net") in usb_nets
    ]
    vias = [item for item in board["vias"] if item.get("net") in usb_nets]

    assert tracks, "inspector must report the routed USB pair"
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
        if {"VBUS", "VIN"} <= set(footprint["pads"].values())
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
