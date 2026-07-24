from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def test_harness_requirements_remain_unselected_while_current_is_unmeasured(
    esp32tap_dir: Path,
) -> None:
    requirements = json.loads(
        (esp32tap_dir / "harness" / "requirements.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(requirements) == {
        "revision",
        "status",
        "release_action",
        "interfaces",
        "owner_fabrication_allowed",
    }
    assert requirements["revision"] == "C"
    assert requirements["status"] == "HOLD_NOT_MEASURED"
    assert requirements["release_action"] == "connector_selection"
    assert requirements["interfaces"] == []
    assert requirements["owner_fabrication_allowed"] is False


def test_harness_validator_audits_hold_but_blocks_release(
    esp32tap_dir: Path,
) -> None:
    audit = subprocess.run(
        [sys.executable, "harness/validate_harnesses.py"],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    release = subprocess.run(
        [sys.executable, "harness/validate_harnesses.py", "--release"],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert audit.returncode == 0, audit.stderr
    assert "HOLD_NOT_MEASURED" in audit.stdout
    assert release.returncode != 0
    assert "NOT_MEASURED" in release.stderr


def _load_validator(esp32tap_dir: Path):
    path = esp32tap_dir / "harness" / "validate_harnesses.py"
    spec = importlib.util.spec_from_file_location("rev_c_harness_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_matrix_is_complete(esp32tap_dir: Path) -> None:
    matrix = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )

    assert matrix["schema_version"] == 1
    assert matrix["retrieved_at"].endswith("Z")
    connectors = matrix["connector_candidates"]
    viable = [
        item
        for item in connectors
        if item["kind"] == "locking_wire_to_board"
        and item["board_mount"] == "SMT"
        and item["packaging"] == "tape_and_reel"
        and item["viability"] == "VIABLE"
    ]
    assert len({item["family"] for item in viable}) >= 3
    assert any(item["kind"] == "smt_rj45_baseline" for item in connectors)
    assert {item["mpn"] for item in matrix["modules"]} == {
        "ESP32-S3-WROOM-1-N8",
        "ESP32-S3-MINI-1-N8",
    }
    assert len({item["mpn"] for item in matrix["switches"]}) >= 2

    required_evidence = {
        "manufacturer",
        "mpn",
        "lcsc_code",
        "official_product_url",
        "official_datasheet_url",
        "retrieved_at",
        "stock",
        "assembly_class",
        "placement_status",
        "source_sha256",
    }
    required_geometry = {
        "pcb_width_mm",
        "pcb_length_mm",
        "pcb_height_mm",
        "antenna_volume_mm3",
        "enclosure_width_mm",
        "enclosure_length_mm",
        "enclosure_height_mm",
        "minimum_bend_radius_mm",
        "service_clearance_mm",
        "assembly_support",
        "installed_bounding_volume_mm3",
        "rejection_constraints",
    }
    for item in connectors + matrix["modules"] + matrix["switches"]:
        assert required_evidence <= set(item)
    for connector in connectors:
        for combination in connector["module_combinations"]:
            assert required_geometry <= set(combination)


def test_selected_parts_and_module_policy_are_exact(esp32tap_dir: Path) -> None:
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(
            encoding="utf-8"
        )
    )
    interfaces = selection["interfaces"]
    assert interfaces["console"]["header"]["mpn"] != interfaces["motor"]["header"]["mpn"]
    assert interfaces["console"]["housing"]["positions"] == 8
    assert interfaces["motor"]["housing"]["positions"] == 10
    assert interfaces["console"]["terminal"]["mpn"] == interfaces["motor"]["terminal"]["mpn"]
    assert selection["module"]["mpn"] == "ESP32-S3-WROOM-1-N8"
    assert selection["module"]["lcsc_code"] == "C2913198"
    assert selection["mini_decision"] == "REJECTED_UNQUALIFIED"
    assert selection["switches"]["reset"]["mpn"] == "SKRPACE010"
    assert selection["switches"]["boot"]["mpn"] == "SKRPACE010"
    assert selection["switches"]["reset"]["lcsc_code"] == "C139797"


def test_module_and_switch_audits_are_exact(esp32tap_dir: Path) -> None:
    matrix = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    modules = {item["mpn"]: item for item in matrix["modules"]}
    wroom = modules["ESP32-S3-WROOM-1-N8"]
    mini = modules["ESP32-S3-MINI-1-N8"]

    assert wroom["packaging"] == "650/full reel"
    assert wroom["used_gpio_audit"] == {
        "GPIO0": "BOOT",
        "GPIO4": "K1_NC_FB",
        "GPIO5": "K1_NO_FB",
        "GPIO6": "TREAD_OK",
        "GPIO7": "VBUS_PRESENT_N",
        "GPIO15": "TX_ENABLE",
        "GPIO16": "PIN3_RX",
        "GPIO17": "ESP_TX",
        "GPIO18": "CONS_RX",
        "GPIO19": "USB_D-",
        "GPIO20": "USB_D+",
        "GPIO21": "RELAY_CMD",
        "GPIO38": "STATUS_LED",
        "GPIO43": "U0TXD",
        "GPIO44": "U0RXD",
        "EN": "RESET",
    }
    assert mini["pad_map"] != wroom["pad_map"]
    assert mini["used_gpio_audit"]["status"] == "INCOMPLETE_FOR_REV_B_SIGNAL_MATRIX"
    assert mini["decision"] == "REJECTED_UNQUALIFIED"
    for switch in matrix["switches"]:
        assert switch["packaging"] == "tape_and_reel"
        assert switch["footprint_provenance"].startswith("ALPSALPINE")


def test_selected_official_part_evidence_is_traceable(esp32tap_dir: Path) -> None:
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(
            encoding="utf-8"
        )
    )
    evidence = selection["official_part_evidence"]

    assert {
        "C240838",
        "C563827",
        "C127351",
        "C259745",
        "C259786",
        "C139797",
        "C2913198",
    } <= set(evidence)
    for code, record in evidence.items():
        assert record["lcsc_code"] == code
        assert record["mpn"]
        assert record["official_manufacturer_url"].startswith("https://")
        assert record["official_lcsc_url"].startswith("https://www.lcsc.com/")
        assert record["retrieved_at"].endswith("Z")
        assert record["packaging"]
        assert record["assembly_class"]
        assert record["stock"]["status"]
        assert len(record["lcsc_html_sha256"]) == 64
        assert record["placement_status"] == "REQUIRES_LIVE_BOM_CPL_PROOF"


def test_selected_complete_mating_system_and_open_contacts(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(
            encoding="utf-8"
        )
    )

    validator.validate_selection(selection)
    for interface_name in ("console", "motor"):
        interface = selection["interfaces"][interface_name]
        assert interface["wire"]["power_ground_awg"] <= 22
        for element in (
            "header",
            "housing",
            "terminal",
            "wire",
            "rj45_termination",
        ):
            rating = interface[element]["rating"]
            assert rating["voltage_v"] >= 24
            assert rating["temperature_min_c"] <= -20
            assert rating["temperature_max_c"] >= 85
        assert interface["terminal"]["derated_current_a"] >= 2.0
        assert interface["strain_relief"]["environment"] == {
            "temperature_min_c": -40,
            "temperature_max_c": 85,
        }
        for net in ("+8V_A", "+8V_B", "GND_A", "GND_B"):
            assert validator.remaining_contact_current_a(
                interface, open_net=net, total_current_a=2.0
            ) == pytest.approx(2.0)
            validator.validate_single_open(interface, open_net=net)


def test_unequal_two_amp_case_does_not_take_sharing_credit(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    case = {"total_current_a": 2.0, "branch_current_a": [1.35, 0.65]}

    validator.validate_unequal_case(case, per_contact_derated_rating_a=2.0)
    with pytest.raises(Exception, match="individual"):
        validator.validate_unequal_case(case, per_contact_derated_rating_a=1.99)


def test_rj45_normal_unequal_case_supports_two_amps_but_single_open_stays_open(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(
            encoding="utf-8"
        )
    )

    for interface in selection["interfaces"].values():
        rj45 = interface["rj45_termination"]
        validator.validate_rj45_normal_case(rj45)
        assert rj45["normal_total_current_a"] == 2.0
        assert rj45["normal_unequal_branch_current_a"] == [1.35, 0.65]
        assert rj45["single_open_2a_status"] == "UNSUPPORTED_OPEN_PHYSICAL_GATE"


@pytest.mark.parametrize("filename", ["console-harness.csv", "motor-harness.csv"])
def test_harness_csv_maps_all_rj45_pins_one_to_one(
    esp32tap_dir: Path, filename: str
) -> None:
    import csv

    rows = list(
        csv.DictReader(
            (esp32tap_dir / "harness" / filename).read_text(encoding="utf-8").splitlines()
        )
    )
    assert [int(row["rj45_pin"]) for row in rows] == list(range(1, 9))
    assert {
        int(row["rj45_pin"]): row["net"] for row in rows
    } == {
        1: "GND_A",
        2: "+8V_A",
        3: "DATA_A",
        4: "DATA_B",
        5: "DATA_C",
        6: "DATA_D",
        7: "GND_B",
        8: "+8V_B",
    }
    assert len({row["board_position"] for row in rows}) == 8
    assert all(row["continuity_test"] == "<=100mOhm,end-to-end" for row in rows)
    assert all(row["wire_awg"] == "22" for row in rows if row["net"].startswith(("+8V", "GND")))
