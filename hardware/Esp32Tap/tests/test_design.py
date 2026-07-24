from __future__ import annotations

import csv
import hashlib
import itertools
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest


@dataclass(frozen=True)
class PartLock:
    value: str
    library: str
    footprint: str
    lcsc: str
    pins: dict[str, str]


TWO_PIN_PASSIVE = {"1": "1", "2": "2"}
EXPECTED_PARTS = {
    "F1": PartLock(
        "1812L075/24DR",
        "Fuse",
        "Fuse_1812_4532Metric",
        "C207065",
        TWO_PIN_PASSIVE,
    ),
    "D3": PartLock(
        "SMBJ10A",
        "Diode_SMD",
        "D_SMB",
        "C151250",
        {"1": "K", "2": "A"},
    ),
    "D4": PartLock(
        "SMAJ6.0CA",
        "Diode_SMD",
        "D_SMA",
        "C80275",
        {"1": "K", "2": "A"},
    ),
    "K1": PartLock(
        "G6K-2F-Y-TR DC5",
        "Relay_SMD",
        "Relay_DPDT_Omron_G6K-2F-Y",
        "C47190",
        {
            "1": "COIL+",
            "2": "NC_A",
            "3": "COM_A",
            "4": "NO_A",
            "5": "NO_B",
            "6": "COM_B",
            "7": "NC_B",
            "8": "COIL-",
        },
    ),
    "Q1": PartLock(
        "BC817-40,215",
        "Package_TO_SOT_SMD",
        "SOT-23",
        "C52801",
        {"1": "B", "2": "E", "3": "C"},
    ),
    "U4": PartLock(
        "TPS3700DDCR",
        "Package_TO_SOT_SMD",
        "SOT-23-6",
        "C33002",
        {
            "1": "OUTA",
            "2": "GND",
            "3": "INA+",
            "4": "INB-",
            "5": "VDD",
            "6": "OUTB",
        },
    ),
    "U5": PartLock(
        "TPS70950DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C96028",
        {"1": "IN", "2": "GND", "3": "EN", "4": "NC", "5": "OUT"},
    ),
    "U6": PartLock(
        "SN74LVC2G08DCTR",
        "Package_SO",
        "SSOP-8_2.95x2.8mm_P0.65mm",
        "C352973",
        {
            "1": "1A",
            "2": "1B",
            "3": "2Y",
            "4": "GND",
            "5": "2A",
            "6": "2B",
            "7": "1Y",
            "8": "VCC",
        },
    ),
    "U7": PartLock(
        "SN74LVC1G126DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C7834",
        {"1": "OE", "2": "A", "3": "GND", "4": "Y", "5": "VCC"},
    ),
    "Q2": PartLock(
        "2N7002",
        "Package_TO_SOT_SMD",
        "SOT-23",
        "C8545",
        {"1": "G", "2": "S", "3": "D"},
    ),
    "C2": PartLock(
        "10uF/25V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C14860",
        TWO_PIN_PASSIVE,
    ),
    "C3": PartLock(
        "10uF/25V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C14860",
        TWO_PIN_PASSIVE,
    ),
    "C6": PartLock(
        "22uF/25V X7R 1210",
        "Capacitor_SMD",
        "C_1210_3225Metric",
        "C2918511",
        TWO_PIN_PASSIVE,
    ),
    "C7": PartLock(
        "22uF/25V X7R 1210",
        "Capacitor_SMD",
        "C_1210_3225Metric",
        "C2918511",
        TWO_PIN_PASSIVE,
    ),
    "C12": PartLock(
        "56pF C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C39148",
        TWO_PIN_PASSIVE,
    ),
    "C13": PartLock(
        "DNP",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "",
        TWO_PIN_PASSIVE,
    ),
    "C14": PartLock(
        "DNP",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "",
        TWO_PIN_PASSIVE,
    ),
    "C15": PartLock(
        "1uF/25V X7R 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C106858",
        TWO_PIN_PASSIVE,
    ),
    "C16": PartLock(
        "4.7uF/25V X7R 0805",
        "Capacitor_SMD",
        "C_0805_2012Metric",
        "C354262",
        TWO_PIN_PASSIVE,
    ),
    "C17": PartLock(
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        TWO_PIN_PASSIVE,
    ),
    "C18": PartLock(
        "1nF/50V C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C342541",
        TWO_PIN_PASSIVE,
    ),
    "C19": PartLock(
        "1nF/50V C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C342541",
        TWO_PIN_PASSIVE,
    ),
    "C20": PartLock(
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        TWO_PIN_PASSIVE,
    ),
    "C21": PartLock(
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        TWO_PIN_PASSIVE,
    ),
}


def _component(design: SimpleNamespace, ref: str) -> tuple:
    assert ref in design.COMPONENTS, f"Rev B component {ref} is missing"
    component = design.COMPONENTS[ref]
    assert len(component) == 8, f"{ref} must retain the component tuple schema"
    return component


def _pins(design: SimpleNamespace, net: str) -> set[tuple[str, str]]:
    assert net in design.NETS, f"Rev B net {net} is missing"
    return set(design.NETS[net])


def _net_for(design: SimpleNamespace, ref: str, pad: str) -> str:
    matches = [
        net
        for net, pins in design.NETS.items()
        if (ref, pad) in pins
    ]
    assert len(matches) == 1, (
        f"{ref}.{pad} must belong to exactly one net; found {matches}"
    )
    return matches[0]


def _terminal_nets(design: SimpleNamespace, ref: str) -> set[str]:
    component = _component(design, ref)
    return {_net_for(design, ref, pad) for pad in component[7]}


def _file_state(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return stat.st_mtime_ns, stat.st_size, digest


def _csv_refs(path: Path, column: str) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return {
            ref.strip()
            for row in rows
            for ref in row[column].split(",")
            if ref.strip()
        }


def test_design_import_does_not_mutate_generated_artifacts(
    esp32tap_dir: Path,
    load_design: Callable[[], SimpleNamespace],
) -> None:
    generated = [
        esp32tap_dir / "NETLIST.md",
        esp32tap_dir / "bom" / "BOM.csv",
        esp32tap_dir / "bom" / "CPL-positions.csv",
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_sch",
        esp32tap_dir / "kicad" / "Esp32Tap.kicad_pcb",
        esp32tap_dir / "kicad" / "esp32tap.kicad_sym",
    ]
    before = {path: _file_state(path) for path in generated}

    imported = load_design()

    assert imported.validate() is True
    assert {path: _file_state(path) for path in generated} == before


def test_usb_is_data_only_and_d2_is_absent(design: SimpleNamespace) -> None:
    assert "D2" not in design.COMPONENTS, (
        "Rev B deletes the USB-to-VIN ORing diode D2"
    )

    assert _pins(design, "VBUS") == {
        ("J3", "A4"),
        ("J3", "A9"),
        ("J3", "B4"),
        ("J3", "B9"),
        ("U3", "5"),
        ("C11", "1"),
        ("R29", "1"),
        ("Q2", "1"),
    }


def test_all_local_input_power_is_downstream_of_d1(
    design: SimpleNamespace,
) -> None:
    assert _pins(design, "+8V_F") == {("F1", "2"), ("D1", "2")}
    assert _pins(design, "VIN") == {
        ("D1", "1"),
        ("D3", "1"),
        ("U2", "3"),
        ("U4", "5"),
        ("U5", "1"),
        ("C1", "1"),
        ("C2", "1"),
        ("C3", "1"),
        ("C4", "1"),
        ("C15", "1"),
        ("C17", "1"),
        ("R3", "1"),
        ("R17", "1"),
        ("R19", "1"),
        ("TP5", "1"),
    }

    for ref, pad in {("U2", "3"), ("U4", "5"), ("U5", "1")}:
        assert _net_for(design, ref, pad) == "VIN"


def test_rj45_power_and_ground_pass_throughs_remain_direct(
    design: SimpleNamespace,
) -> None:
    assert {
        _net_for(design, connector, pad)
        for connector in ("J1", "J2")
        for pad in ("2", "8")
    } == {"+8V_RAW"}
    assert {
        _net_for(design, connector, pad)
        for connector in ("J1", "J2")
        for pad in ("1", "7")
    } == {"GND"}
    assert {
        ("J1", "2"),
        ("J1", "8"),
        ("J2", "2"),
        ("J2", "8"),
        ("F1", "1"),
    } <= _pins(design, "+8V_RAW")


@pytest.mark.parametrize(
    ("ref", "expected"),
    EXPECTED_PARTS.items(),
    ids=EXPECTED_PARTS,
)
def test_exact_rev_b_part_footprint_and_pad_locks(
    design: SimpleNamespace,
    ref: str,
    expected: PartLock,
) -> None:
    component = _component(design, ref)

    actual = PartLock(
        value=component[0],
        library=component[1],
        footprint=component[2],
        lcsc=component[3],
        pins=component[7],
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("ref", "value", "lcsc"),
    [
        ("R7", "10k", "C25804"),
        ("R8", "10k", "C25804"),
        ("C11", "100nF", "C14663"),
    ],
)
def test_corrected_tap_and_vbus_passives(
    design: SimpleNamespace,
    ref: str,
    value: str,
    lcsc: str,
) -> None:
    component = _component(design, ref)
    assert (component[0], component[3]) == (value, lcsc)


def test_new_resistors_have_the_approved_values_and_roles(
    design: SimpleNamespace,
) -> None:
    expected = {
        "R15": ("22R", {"USB_DN_MCU", "USB_DN_R"}),
        "R16": ("22R", {"USB_DP_MCU", "USB_DP_R"}),
        "R17": ("150k", {"VIN", "UV_SENSE"}),
        "R18": ("10k", {"UV_SENSE", "GND"}),
        "R19": ("255k", {"VIN", "OV_SENSE"}),
        "R20": ("10k", {"OV_SENSE", "GND"}),
        "R21": ("10k", {"+3V3", "TREAD_OK"}),
        "R22": ("100k", {"TREAD_OK", "GND"}),
        "R23": ("10k", {"RELAY_CMD", "GND"}),
        "R24": ("10k", {"RELAY_GATE", "GND"}),
        "R25": ("10k", {"+3V3", "K1_NC_FB"}),
        "R26": ("10k", {"+3V3", "K1_NO_FB"}),
        "R27": ("10k", {"TX_ENABLE", "GND"}),
        "R28": ("10k", {"TX_GATE", "GND"}),
        "R29": ("10k", {"VBUS", "GND"}),
        "R30": ("10k", {"+3V3", "VBUS_PRESENT_N"}),
        "R31": ("10k", {"+3V3", "IO0"}),
    }

    for ref, (value, nets) in expected.items():
        component = _component(design, ref)
        assert component[0] == value, ref
        assert component[1:3] == (
            "Resistor_SMD",
            "R_0603_1608Metric",
        ), ref
        assert component[7] == TWO_PIN_PASSIVE, ref
        assert _terminal_nets(design, ref) == nets, ref


def test_new_capacitors_have_the_approved_circuit_roles(
    design: SimpleNamespace,
) -> None:
    assert _terminal_nets(design, "C12") == {
        _net_for(design, "R1", "1"),
        _net_for(design, "R1", "2"),
    }
    assert _terminal_nets(design, "C15") == {"VIN", "GND"}
    assert _terminal_nets(design, "C16") == {"+5V_RLY", "GND"}
    assert _terminal_nets(design, "C17") == {"VIN", "GND"}
    assert _terminal_nets(design, "C18") == {"UV_SENSE", "GND"}
    assert _terminal_nets(design, "C19") == {"OV_SENSE", "GND"}
    assert _terminal_nets(design, "C20") == {"+3V3", "GND"}
    assert _terminal_nets(design, "C21") == {"+3V3", "GND"}


def test_usb_tuning_capacitors_are_dnp_and_not_assembled(
    design: SimpleNamespace,
    esp32tap_dir: Path,
) -> None:
    assert hasattr(design, "DNP"), "design.py must declare an explicit DNP set"
    assert set(design.DNP) == {"C13", "C14"}
    assert all(_component(design, ref)[3] == "" for ref in design.DNP)

    assembled = _csv_refs(
        esp32tap_dir / "bom" / "BOM.csv",
        "Designator",
    )
    assembled |= _csv_refs(
        esp32tap_dir / "bom" / "CPL-positions.csv",
        "Designator",
    )
    assert set(design.DNP).isdisjoint(assembled)


def test_supervisor_and_both_hardware_and_gate_equations(
    design: SimpleNamespace,
) -> None:
    assert _pins(design, "UV_SENSE") == {
        ("U4", "3"),
        ("R17", "2"),
        ("R18", "1"),
        ("C18", "1"),
    }
    assert _pins(design, "OV_SENSE") == {
        ("U4", "4"),
        ("R19", "2"),
        ("R20", "1"),
        ("C19", "1"),
    }
    assert {
        pin for pin in _pins(design, "RELAY_CMD") if pin[0] != "U6"
    } == {
        ("U1", "23"),
        ("R23", "1"),
    }
    assert {
        pin for pin in _pins(design, "TREAD_OK") if pin[0] != "U6"
    } == {
        ("U1", "6"),
        ("U4", "1"),
        ("U4", "6"),
        ("R21", "2"),
        ("R22", "1"),
        ("TP7", "1"),
    }
    assert {
        pin for pin in _pins(design, "RELAY_GATE") if pin[0] != "U6"
    } == {
        ("U5", "3"),
        ("R9", "1"),
        ("R24", "1"),
        ("TP8", "1"),
    }
    assert {
        pin for pin in _pins(design, "TX_ENABLE") if pin[0] != "U6"
    } == {
        ("U1", "8"),
        ("R27", "1"),
    }
    assert {
        pin for pin in _pins(design, "TX_GATE") if pin[0] != "U6"
    } == {
        ("U7", "1"),
        ("R28", "1"),
        ("TP10", "1"),
    }
    equations = {
        (
            frozenset(
                {
                    _net_for(design, "U6", input_a),
                    _net_for(design, "U6", input_b),
                }
            ),
            _net_for(design, "U6", output),
        )
        for input_a, input_b, output in (("1", "2", "7"), ("5", "6", "3"))
    }
    assert equations == {
        (frozenset({"RELAY_CMD", "TREAD_OK"}), "RELAY_GATE"),
        (frozenset({"TX_ENABLE", "TREAD_OK"}), "TX_GATE"),
    }


def test_u5_and_q1_are_series_relay_controls(design: SimpleNamespace) -> None:
    assert _pins(design, "+5V_RLY") - {("D4", "1"), ("D4", "2")} == {
        ("U5", "5"),
        ("C16", "1"),
        ("K1", "1"),
        ("TP6", "1"),
    }
    assert _pins(design, "RELAY_SW") - {("D4", "1"), ("D4", "2")} == {
        ("K1", "8"),
        ("Q1", "3"),
        ("TP9", "1"),
    }
    assert _pins(design, "Q1_B") == {
        ("Q1", "1"),
        ("R9", "2"),
        ("R10", "1"),
    }
    assert _net_for(design, "U5", "3") == "RELAY_GATE"
    assert _net_for(design, "Q1", "1") == "Q1_B"
    assert _net_for(design, "Q1", "2") == "GND"


def test_d4_is_connected_directly_across_the_relay_coil(
    design: SimpleNamespace,
) -> None:
    coil_nets = {
        _net_for(design, "K1", "1"),
        _net_for(design, "K1", "8"),
    }
    clamp_nets = {
        _net_for(design, "D4", "1"),
        _net_for(design, "D4", "2"),
    }

    assert coil_nets == {"+5V_RLY", "RELAY_SW"}
    assert clamp_nets == coil_nets


def test_k1_uses_one_transfer_pole_and_one_dry_feedback_pole(
    design: SimpleNamespace,
) -> None:
    assert {
        pad: _net_for(design, "K1", pad)
        for pad in ("2", "3", "4")
    } == {"2": "CONS6", "3": "MOT6", "4": "TX_DRV"}
    assert {
        pad: _net_for(design, "K1", pad)
        for pad in ("5", "6", "7")
    } == {
        "5": "K1_NO_FB",
        "6": "GND",
        "7": "K1_NC_FB",
    }
    assert _pins(design, "K1_NC_FB") == {
        ("K1", "7"),
        ("R25", "2"),
        ("U1", "4"),
        ("TP12", "1"),
    }
    assert _pins(design, "K1_NO_FB") == {
        ("K1", "5"),
        ("R26", "2"),
        ("U1", "5"),
        ("TP13", "1"),
    }
    assert _net_for(design, "R25", "1") == "+3V3"
    assert _net_for(design, "R26", "1") == "+3V3"


def test_u7_isolates_esp_tx_before_the_relay(design: SimpleNamespace) -> None:
    assert {
        pad: _net_for(design, "U7", pad)
        for pad in ("1", "2", "3", "4", "5")
    } == {
        "1": "TX_GATE",
        "2": "ESP_TX",
        "3": "GND",
        "4": "TX_BUF",
        "5": "+3V3",
    }
    assert _pins(design, "TX_BUF") == {("U7", "4"), ("R6", "1")}
    assert _pins(design, "TX_DRV") == {
        ("R6", "2"),
        ("K1", "4"),
        ("TP11", "1"),
    }


def test_q2_reports_vbus_without_powering_an_esp_gpio(
    design: SimpleNamespace,
) -> None:
    assert {
        pad: _net_for(design, "Q2", pad)
        for pad in ("1", "2", "3")
    } == {"1": "VBUS", "2": "GND", "3": "VBUS_PRESENT_N"}
    assert _pins(design, "VBUS_PRESENT_N") == {
        ("Q2", "3"),
        ("R30", "2"),
        ("U1", "7"),
    }
    assert _net_for(design, "R30", "1") == "+3V3"


def test_usb_series_and_dnp_shunt_topology(design: SimpleNamespace) -> None:
    assert _pins(design, "USB_DN_MCU") == {("U3", "6"), ("R15", "1")}
    assert _pins(design, "USB_DP_MCU") == {("U3", "4"), ("R16", "1")}
    assert _pins(design, "USB_DN_R") == {
        ("R15", "2"),
        ("C13", "1"),
        ("U1", "13"),
    }
    assert _pins(design, "USB_DP_R") == {
        ("R16", "2"),
        ("C14", "1"),
        ("U1", "14"),
    }
    assert _net_for(design, "C13", "2") == "GND"
    assert _net_for(design, "C14", "2") == "GND"


def test_rev_b_gpio_assignments(design: SimpleNamespace) -> None:
    expected = {
        "4": "K1_NC_FB",
        "5": "K1_NO_FB",
        "6": "TREAD_OK",
        "7": "VBUS_PRESENT_N",
        "8": "TX_ENABLE",
        "9": "PIN3_RX",
        "10": "ESP_TX",
        "11": "CONS_RX",
        "23": "RELAY_CMD",
        "27": "IO0",
        "31": "STATUS_LED",
    }

    assert {
        pad: _net_for(design, "U1", pad)
        for pad in expected
    } == expected
    assert _net_for(design, "R31", "1") == "+3V3"
    assert _net_for(design, "R31", "2") == "IO0"


@pytest.mark.parametrize(
    ("ref", "net"),
    [
        ("TP5", "VIN"),
        ("TP6", "+5V_RLY"),
        ("TP7", "TREAD_OK"),
        ("TP8", "RELAY_GATE"),
        ("TP9", "RELAY_SW"),
        ("TP10", "TX_GATE"),
        ("TP11", "TX_DRV"),
        ("TP12", "K1_NC_FB"),
        ("TP13", "K1_NO_FB"),
    ],
)
def test_rev_b_validation_test_pads(
    design: SimpleNamespace,
    ref: str,
    net: str,
) -> None:
    component = _component(design, ref)
    assert component[0] == net
    assert component[1:3] == (
        "TestPoint",
        "TestPoint_Pad_1.5x1.5mm",
    )
    assert component[7] == {"1": "1"}
    assert _net_for(design, ref, "1") == net


@pytest.mark.parametrize(
    ("rail_3v3", "tread_ok", "relay_cmd", "tx_enable"),
    itertools.product((False, True), repeat=4),
)
def test_required_hardware_gate_truth_table(
    rail_3v3: bool,
    tread_ok: bool,
    relay_cmd: bool,
    tx_enable: bool,
) -> None:
    relay_gate = all((rail_3v3, tread_ok, relay_cmd))
    tx_gate = all((rail_3v3, tread_ok, tx_enable))

    assert relay_gate == (rail_3v3 and tread_ok and relay_cmd)
    assert tx_gate == (rail_3v3 and tread_ok and tx_enable)
