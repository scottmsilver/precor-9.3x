from __future__ import annotations

import csv
import hashlib
import itertools
import subprocess
import sys
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

OFFICIAL_JLC_ASSEMBLY_CLASSES = {
    # Verified against the current JLCPCB/LCSC part pages for Rev B.
    "C14860": "Extended",
    "C23354": "Extended",
    "C342541": "Extended",
    "C354262": "Extended",
    "C39148": "Extended",
    "C106858": "Extended",
}

EXPECTED_ACTIVE_PIN_TYPES = {
    ("U1", "1"): "power_in",
    ("U1", "2"): "power_in",
    ("U1", "3"): "input",
    ("U1", "4"): "input",
    ("U1", "5"): "input",
    ("U1", "6"): "input",
    ("U1", "7"): "input",
    ("U1", "8"): "output",
    ("U1", "9"): "input",
    ("U1", "10"): "output",
    ("U1", "11"): "input",
    ("U1", "12"): "no_connect",
    ("U1", "13"): "bidirectional",
    ("U1", "14"): "bidirectional",
    ("U1", "15"): "no_connect",
    ("U1", "16"): "no_connect",
    ("U1", "17"): "no_connect",
    ("U1", "18"): "no_connect",
    ("U1", "19"): "no_connect",
    ("U1", "20"): "no_connect",
    ("U1", "21"): "no_connect",
    ("U1", "22"): "no_connect",
    ("U1", "23"): "output",
    ("U1", "24"): "no_connect",
    ("U1", "25"): "no_connect",
    ("U1", "26"): "no_connect",
    ("U1", "27"): "input",
    ("U1", "28"): "no_connect",
    ("U1", "29"): "no_connect",
    ("U1", "30"): "no_connect",
    ("U1", "31"): "output",
    ("U1", "32"): "no_connect",
    ("U1", "33"): "no_connect",
    ("U1", "34"): "no_connect",
    ("U1", "35"): "no_connect",
    ("U1", "36"): "input",
    ("U1", "37"): "output",
    ("U1", "38"): "no_connect",
    ("U1", "39"): "no_connect",
    ("U1", "40"): "power_in",
    ("U1", "41"): "power_in",
    ("U2", "1"): "power_in",
    ("U2", "2"): "power_out",
    ("U2", "3"): "power_in",
    ("U2", "4"): "input",
    ("U2", "5"): "input",
    ("U3", "1"): "bidirectional",
    ("U3", "2"): "power_in",
    ("U3", "3"): "bidirectional",
    ("U3", "4"): "bidirectional",
    ("U3", "5"): "power_in",
    ("U3", "6"): "bidirectional",
    ("U4", "1"): "open_collector",
    ("U4", "2"): "power_in",
    ("U4", "3"): "input",
    ("U4", "4"): "input",
    ("U4", "5"): "power_in",
    ("U4", "6"): "open_collector",
    ("U5", "1"): "power_in",
    ("U5", "2"): "power_in",
    ("U5", "3"): "input",
    ("U5", "4"): "no_connect",
    ("U5", "5"): "power_out",
    ("U6", "1"): "input",
    ("U6", "2"): "input",
    ("U6", "3"): "output",
    ("U6", "4"): "power_in",
    ("U6", "5"): "input",
    ("U6", "6"): "input",
    ("U6", "7"): "output",
    ("U6", "8"): "power_in",
    ("U7", "1"): "input",
    ("U7", "2"): "input",
    ("U7", "3"): "power_in",
    ("U7", "4"): "tri_state",
    ("U7", "5"): "power_in",
    ("Q1", "1"): "input",
    ("Q1", "3"): "open_collector",
    ("Q2", "1"): "input",
    ("Q2", "3"): "open_collector",
    ("J3", "A8"): "no_connect",
    ("J3", "B8"): "no_connect",
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


def _move_pin(
    design: SimpleNamespace,
    pin: tuple[str, str],
    target_net: str,
) -> None:
    source_nets = [
        net
        for net, pins in design.NETS.items()
        if pin in pins
    ]
    assert len(source_nets) == 1, pin
    design.NETS[source_nets[0]].remove(pin)
    design.NETS[target_net].append(pin)


def _swap_pin_nets(
    design: SimpleNamespace,
    first: tuple[str, str],
    second: tuple[str, str],
) -> None:
    first_net = _net_for(design, *first)
    second_net = _net_for(design, *second)
    assert first_net != second_net
    design.NETS[first_net].remove(first)
    design.NETS[second_net].remove(second)
    design.NETS[first_net].append(second)
    design.NETS[second_net].append(first)


def _add_two_pin_passive(
    design: SimpleNamespace,
    ref: str,
    first_net: str,
    second_net: str,
) -> None:
    design.COMPONENTS[ref] = (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Test-only injected bridge",
        {"1": "1", "2": "2"},
    )
    design.PIN_TYPES[(ref, "1")] = "passive"
    design.PIN_TYPES[(ref, "2")] = "passive"
    design.NETS[first_net].append((ref, "1"))
    design.NETS[second_net].append((ref, "2"))


def _assert_design_invalid(
    design: SimpleNamespace,
    match: str,
) -> pytest.ExceptionInfo[ValueError]:
    return pytest.raises(ValueError, match=match)


def test_populated_parts_use_current_official_jlc_assembly_classes(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    design = load_design()
    matches = {
        ref: (component[3], component[4])
        for ref, component in design.COMPONENTS.items()
        if component[3] in OFFICIAL_JLC_ASSEMBLY_CLASSES
    }
    assert matches, "JLC class lock must exercise populated Rev B parts"
    for ref, (lcsc, actual_class) in matches.items():
        assert actual_class == OFFICIAL_JLC_ASSEMBLY_CLASSES[lcsc], (
            f"{ref} ({lcsc}) is currently {actual_class}; "
            f"official assembly class is {OFFICIAL_JLC_ASSEMBLY_CLASSES[lcsc]}"
        )


def _u6_equations(
    design: SimpleNamespace,
) -> dict[str, frozenset[str]]:
    assert _net_for(design, "U6", "8") == "+3V3"
    assert _net_for(design, "U6", "4") == "GND"

    equations: dict[str, frozenset[str]] = {}
    for input_a, input_b, output in (("1", "2", "7"), ("5", "6", "3")):
        output_net = _net_for(design, "U6", output)
        assert output_net not in equations, (
            f"both U6 channels cannot drive {output_net}"
        )
        equations[output_net] = frozenset(
            {
                _net_for(design, "U6", input_a),
                _net_for(design, "U6", input_b),
            }
        )
    return equations


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
    assert _pins(design, "+8V_RAW") == {
        ("J1", "2"),
        ("J1", "8"),
        ("J2", "2"),
        ("J2", "8"),
        ("F1", "1"),
    }


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


def test_relay_base_drive_has_conservative_forced_beta_margin(
    design: SimpleNamespace,
) -> None:
    assert _component(design, "R9")[0] == "560R"
    assert _component(design, "R10")[0] == "10k"


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
    assert _u6_equations(design) == {
        "RELAY_GATE": frozenset({"RELAY_CMD", "TREAD_OK"}),
        "TX_GATE": frozenset({"TX_ENABLE", "TREAD_OK"}),
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
    assert _pins(design, "CONS6") == {
        ("J1", "6"),
        ("K1", "2"),
        ("R7", "1"),
        ("D5", "1"),
    }
    assert _pins(design, "MOT6") == {
        ("J2", "6"),
        ("K1", "3"),
        ("D6", "1"),
    }
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
    design: SimpleNamespace,
    rail_3v3: bool,
    tread_ok: bool,
    relay_cmd: bool,
    tx_enable: bool,
) -> None:
    equations = _u6_equations(design)
    assert equations == {
        "RELAY_GATE": frozenset({"RELAY_CMD", "TREAD_OK"}),
        "TX_GATE": frozenset({"TX_ENABLE", "TREAD_OK"}),
    }
    inputs = {
        "RELAY_CMD": relay_cmd,
        "TREAD_OK": tread_ok,
        "TX_ENABLE": tx_enable,
    }

    def evaluate(output: str) -> bool:
        channel_inputs = equations[output]
        assert channel_inputs <= inputs.keys()
        return rail_3v3 and all(inputs[net] for net in channel_inputs)

    relay_gate = evaluate("RELAY_GATE")
    tx_gate = evaluate("TX_GATE")

    assert relay_gate == (rail_3v3 and tread_ok and relay_cmd)
    assert tx_gate == (rail_3v3 and tread_ok and tx_enable)


def test_validation_uses_explicit_value_error(
    design: SimpleNamespace,
) -> None:
    assert issubclass(design.DesignValidationError, ValueError)


def test_validate_rejects_incomplete_pin_types(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    mutated.PIN_TYPES.pop(("U6", "7"))

    with _assert_design_invalid(
        mutated,
        r"PIN_TYPES.*missing.*U6.*7.*extra",
    ):
        mutated.validate()


def test_validate_rejects_unknown_pin_type(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    mutated.PIN_TYPES[("U6", "7")] = "totem_pole"

    with _assert_design_invalid(
        mutated,
        r"PIN_TYPES.*unknown.*totem_pole",
    ):
        mutated.validate()


def test_all_non_passive_pin_types_match_independent_contract(
    design: SimpleNamespace,
) -> None:
    actual = {
        pin: pin_type
        for pin, pin_type in design.PIN_TYPES.items()
        if pin_type != "passive"
    }

    assert len(EXPECTED_ACTIVE_PIN_TYPES) == 82
    assert actual == EXPECTED_ACTIVE_PIN_TYPES


def test_u2_bootstrap_pin_is_passive(
    design: SimpleNamespace,
) -> None:
    assert design.PIN_TYPES[("U2", "6")] == "passive"


def test_validate_locks_u2_bootstrap_pin_as_passive(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    pin = ("U2", "6")
    mutated.PIN_TYPES[pin] = "power_out"

    derived_overrides = dict(mutated._PIN_TYPE_OVERRIDES)
    derived_overrides[pin] = "power_out"
    mutated.validate.__globals__["_PIN_TYPE_OVERRIDES"] = (
        derived_overrides
    )

    with _assert_design_invalid(
        mutated,
        r"PIN_TYPES active lock U2\.6.*"
        r"actual=power_out.*expected=passive",
    ):
        mutated.validate()


@pytest.mark.parametrize(
    ("pin", "expected_type"),
    [
        (("U6", "7"), "output"),
        (("U4", "1"), "open_collector"),
        (("U7", "4"), "tri_state"),
        (("U6", "8"), "power_in"),
    ],
    ids=("safety-output", "open-collector", "tri-state", "power-pin"),
)
def test_validate_uses_independent_active_pin_type_oracle(
    load_design: Callable[[], SimpleNamespace],
    pin: tuple[str, str],
    expected_type: str,
) -> None:
    mutated = load_design()
    mutated.PIN_TYPES[pin] = "passive"

    derived_overrides = dict(mutated._PIN_TYPE_OVERRIDES)
    derived_overrides[pin] = "passive"
    mutated.validate.__globals__["_PIN_TYPE_OVERRIDES"] = (
        derived_overrides
    )

    with _assert_design_invalid(
        mutated,
        rf"PIN_TYPES active lock.*{pin[0]}.*{pin[1]}.*"
        rf"actual=passive.*expected={expected_type}",
    ):
        mutated.validate()


def test_validate_rejects_populated_dnp(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    component = list(mutated.COMPONENTS["C13"])
    component[3] = "C14663"
    component[4] = "Basic"
    component[5] = 0.004
    mutated.COMPONENTS["C13"] = tuple(component)

    with _assert_design_invalid(
        mutated,
        r"DNP.*C13.*populated.*LCSC.*C14663",
    ):
        mutated.validate()


def test_validate_rejects_missing_dnp_declaration(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    mutated.DNP.remove("C13")

    with _assert_design_invalid(
        mutated,
        r"DNP set mismatch.*missing=.*C13.*extra=",
    ):
        mutated.validate()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (0, "wrong-value"),
        (2, "wrong-footprint"),
        (7, {"1": "2", "2": "1"}),
    ],
    ids=("part", "package", "pad-map"),
)
def test_validate_rejects_part_package_and_pad_lock_changes(
    load_design: Callable[[], SimpleNamespace],
    field: int,
    replacement: object,
) -> None:
    mutated = load_design()
    component = list(mutated.COMPONENTS["F1"])
    component[field] = replacement
    mutated.COMPONENTS["F1"] = tuple(component)

    with _assert_design_invalid(
        mutated,
        r"F1 part/package/pad lock mismatch.*actual=.*expected=",
    ):
        mutated.validate()


def test_validate_rejects_direct_vbus_power_bridge(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    _move_pin(mutated, ("C11", "2"), "+3V3")

    with _assert_design_invalid(
        mutated,
        r"VBUS isolation lock C11\.2.*"
        r"actual=\+3V3.*expected=GND",
    ):
        mutated.validate()


def test_validate_rejects_two_hop_vbus_power_bridge(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    mutated.NETS["VBUS_BRIDGE"] = [("C11", "2")]
    mutated.NETS["GND"].remove(("C11", "2"))
    _add_two_pin_passive(
        mutated,
        "R99",
        "VBUS_BRIDGE",
        "+3V3",
    )

    with _assert_design_invalid(
        mutated,
        r"VBUS isolation lock C11\.2.*"
        r"actual=VBUS_BRIDGE.*expected=GND",
    ):
        mutated.validate()


def test_validate_requires_vbus_lock_for_every_adjacent_terminal(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    component = list(mutated.COMPONENTS["C11"])
    pad_map = dict(component[7])
    pad_map["3"] = "unexpected"
    component[7] = pad_map
    mutated.COMPONENTS["C11"] = tuple(component)
    mutated.PIN_TYPES[("C11", "3")] = "passive"
    mutated.NETS["GND"].append(("C11", "3"))

    with _assert_design_invalid(
        mutated,
        r"VBUS isolation lock coverage.*"
        r"missing=.*C11.*3.*extra=",
    ):
        mutated.validate()


@pytest.mark.parametrize(
    ("first", "second", "diagnostic"),
    [
        (("U1", "2"), ("U1", "1"), r"U1\.1.*actual=\+3V3.*expected=GND"),
        (("U6", "8"), ("U6", "4"), r"U6\.4.*actual=\+3V3.*expected=GND"),
    ],
    ids=("esp32-supply", "and-gate-supply"),
)
def test_validate_rejects_exact_power_pad_swaps(
    load_design: Callable[[], SimpleNamespace],
    first: tuple[str, str],
    second: tuple[str, str],
    diagnostic: str,
) -> None:
    mutated = load_design()
    _swap_pin_nets(mutated, first, second)

    with _assert_design_invalid(
        mutated,
        rf"power-pad lock.*{diagnostic}",
    ):
        mutated.validate()


@pytest.mark.parametrize(
    ("first", "second", "diagnostic"),
    [
        (("J3", "A6"), ("J3", "A7"), r"J3\.A6"),
        (("J3", "B6"), ("J3", "B7"), r"J3\.B6"),
        (("U3", "1"), ("U3", "3"), r"U3\.1"),
        (("U3", "4"), ("U3", "6"), r"U3\.4"),
    ],
    ids=(
        "connector-a",
        "connector-b",
        "protector-connector-side",
        "protector-mcu-side",
    ),
)
def test_validate_rejects_usb_polarity_swaps(
    load_design: Callable[[], SimpleNamespace],
    first: tuple[str, str],
    second: tuple[str, str],
    diagnostic: str,
) -> None:
    mutated = load_design()
    expected_net = _net_for(mutated, *first)
    _swap_pin_nets(mutated, first, second)
    actual_net = _net_for(mutated, *first)

    with _assert_design_invalid(
        mutated,
        rf"USB polarity lock {diagnostic}.*"
        rf"actual={actual_net}.*expected={expected_net}",
    ):
        mutated.validate()


def test_validate_rejects_relay_gate_bypass(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    _move_pin(mutated, ("U5", "3"), "RELAY_CMD")

    with _assert_design_invalid(
        mutated,
        r"relay safety lock U5\.3.*"
        r"actual=RELAY_CMD.*expected=RELAY_GATE",
    ):
        mutated.validate()


def test_validate_rejects_tx_gate_bypass(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    _move_pin(mutated, ("U7", "1"), "TX_ENABLE")

    with _assert_design_invalid(
        mutated,
        r"TX gate safety lock U7\.1.*"
        r"actual=TX_ENABLE.*expected=TX_GATE",
    ):
        mutated.validate()


def test_validate_rejects_tx_buffer_bypass(
    load_design: Callable[[], SimpleNamespace],
) -> None:
    mutated = load_design()
    _add_two_pin_passive(mutated, "R99", "TX_BUF", "GND")
    _move_pin(mutated, ("R6", "1"), "ESP_TX")

    with _assert_design_invalid(
        mutated,
        r"TX buffer safety lock R6\.1.*"
        r"actual=ESP_TX.*expected=TX_BUF",
    ):
        mutated.validate()


def test_validation_lock_oracles_are_immutable(
    design: SimpleNamespace,
) -> None:
    for oracle_name in (
        "_PART_LOCKS",
        "_ACTIVE_PIN_TYPE_LOCKS",
        "_VBUS_ADJACENT_PIN_NET_LOCKS",
        "_POWER_PIN_NET_LOCKS",
        "_USB_PIN_NET_LOCKS",
        "_SAFETY_PIN_NET_LOCKS",
    ):
        oracle = getattr(design, oracle_name)
        with pytest.raises(TypeError):
            oracle[("__test__", "1")] = "unsafe"

    assert isinstance(design._PART_LOCKS["C2"][4], tuple)
    assert isinstance(design._PART_LOCKS["C3"][4], tuple)
    assert design._PART_LOCKS["C2"][4] is not (
        design._PART_LOCKS["C3"][4]
    )


def test_optimized_python_still_rejects_invalid_design(
    design_path: Path,
) -> None:
    script = (
        "import runpy\n"
        f"design = runpy.run_path({str(design_path)!r}, "
        "run_name='optimized_validation_test')\n"
        "design['PIN_TYPES'].pop(('U6', '7'))\n"
        "try:\n"
        "    design['validate']()\n"
        "except ValueError as exc:\n"
        "    print(exc)\n"
        "else:\n"
        "    raise SystemExit('optimized validation accepted an "
        "incomplete PIN_TYPES table')\n"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "PIN_TYPES" in result.stdout
    assert "missing" in result.stdout
