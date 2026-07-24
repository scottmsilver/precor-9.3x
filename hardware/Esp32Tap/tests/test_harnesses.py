from __future__ import annotations

import copy
import csv
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
        (esp32tap_dir / "harness" / "requirements.json").read_text(encoding="utf-8")
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
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    interfaces = selection["interfaces"]
    assert (
        interfaces["console"]["header"]["mpn"] != interfaces["motor"]["header"]["mpn"]
    )
    assert interfaces["console"]["housing"]["positions"] == 8
    assert interfaces["motor"]["housing"]["positions"] == 10
    assert (
        interfaces["console"]["terminal"]["mpn"]
        == interfaces["motor"]["terminal"]["mpn"]
    )
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
    assert mini["used_gpio_audit"] == wroom["used_gpio_audit"]
    assert mini["decision"] == "REJECTED_UNQUALIFIED"
    for switch in matrix["switches"]:
        assert switch["packaging"] == "tape_and_reel"
        assert switch["footprint_provenance"].startswith("ALPSALPINE")


def test_selected_official_part_evidence_is_traceable(esp32tap_dir: Path) -> None:
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
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
        "MOLEX-MICROFIT-PS-43045",
        "ALPHA-3051",
        "HT-151-00745",
        "TE-1932219-1",
    } <= set(evidence)
    for code, record in evidence.items():
        assert record["mpn"]
        assert record["official_manufacturer_url"].startswith("https://")
        assert record["retrieved_at"].endswith("Z")
        if code.startswith("C"):
            assert record["lcsc_code"] == code
            assert record["official_lcsc_url"].startswith("https://www.lcsc.com/")
            assert record["packaging"]
            assert record["assembly_class"]
            assert record["stock"]["status"]
            assert len(record["lcsc_html_sha256"]) == 64
            assert (
                record["placement_status"] == "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF"
            )
        elif "source_sha256" in record:
            assert len(record["source_sha256"]) == 64


def test_selected_complete_mating_system_and_open_contacts(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )

    validator.validate_selection(selection, candidates)
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
        assert (
            validator.derive_selected_contact_current_a(
                interface, selection["official_part_evidence"]
            )
            >= 2.0
        )
        assert interface["strain_relief"]["environment"] == {
            "temperature_min_c": -40,
            "temperature_max_c": 85,
        }
        for net in ("+8V_A", "+8V_B", "GND_A", "GND_B"):
            assert validator.remaining_contact_current_a(
                interface, open_net=net, total_current_a=2.0
            ) == pytest.approx(2.0)
            validator.validate_single_open(
                interface,
                evidence=selection["official_part_evidence"],
                open_net=net,
            )


def test_unequal_two_amp_case_does_not_take_sharing_credit(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    case = {"total_current_a": 2.0, "branch_current_a": [1.35, 0.65]}

    validator.validate_unequal_case(case, per_contact_derated_rating_a=2.0)
    with pytest.raises(validator.EvidenceError, match="individual"):
        validator.validate_unequal_case(case, per_contact_derated_rating_a=1.99)


def test_electrical_limits_bind_only_supported_two_amp_calculations(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    limits = json.loads(
        (esp32tap_dir / "harness" / "electrical_limits.json").read_text(
            encoding="utf-8"
        )
    )

    validated = validator.validate_electrical_limits(limits)
    assert validated["basis"] == "CONSERVATIVE_PREDECESSOR"
    assert validated["total_current_a"] == 2.0
    assert validated["normal_unequal_branch_current_a"] == [1.35, 0.65]
    assert validated["new_board_connectors"] == {
        "console": {
            "mpn": "430450809",
            "contact_resistance_ohm": 0.01,
            "doubled_contact_resistance_ohm": 0.02,
        },
        "motor": {
            "mpn": "430451010",
            "contact_resistance_ohm": 0.01,
            "doubled_contact_resistance_ohm": 0.02,
        },
    }
    assert validated["rj45"]["termination_count"] == 4
    assert validated["rj45"]["single_open_2a_status"] == (
        "UNSUPPORTED_OPEN_PHYSICAL_GATE"
    )
    assert validated["wire"]["dc_resistance_ohm_per_1000ft_at_20c"] == 16.2
    assert validated["wire"]["harness_length_mm"] == {
        "console": 180,
        "motor": 240,
    }
    assert validated["wire"]["calculated_conductor_resistance_ohm"] == (
        pytest.approx(
            {
                "console": 16.2 * 180 / 304800,
                "motor": 16.2 * 240 / 304800,
            }
        )
    )
    assert validated["pcb"]["copper_via_resistance_ohm"] is None
    assert validated["source"]["minimum_vin_v"] is None
    assert validated["source"]["impedance_ohm"] is None
    assert validated["local_load"]["current_a"] is None
    assert validated["usb_ground"]["return_current_a"] is None
    unsupported = set(validated["unsupported"])
    assert {
        "RJ45_SINGLE_OPEN_2A",
        "MINIMUM_VIN",
        "SOURCE_IMPEDANCE",
        "AMBIENT_THERMAL",
        "TRANSIENT_RESPONSE",
        "COMPLETE_INSTALLED_DROP",
        "USB_RETURN_CURRENT",
    } <= unsupported


def test_electrical_limits_reject_numeric_physical_defaults(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    path = esp32tap_dir / "harness" / "electrical_limits.json"
    original = json.loads(path.read_text(encoding="utf-8"))

    for section, field in (
        ("pcb", "copper_via_resistance_ohm"),
        ("source", "minimum_vin_v"),
        ("source", "impedance_ohm"),
        ("local_load", "current_a"),
        ("usb_ground", "return_current_a"),
    ):
        changed = copy.deepcopy(original)
        changed[section][field] = 0.0
        with pytest.raises(
            validator.EvidenceError,
            match="UNSUPPORTED|physical|must remain null",
        ):
            validator.validate_electrical_limits(changed)


def test_rj45_normal_unequal_case_supports_two_amps_but_single_open_stays_open(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )

    for interface in selection["interfaces"].values():
        rj45 = interface["rj45_termination"]
        validator.validate_rj45_normal_case(
            rj45, selection["official_part_evidence"]["TE-1932219-1"]
        )
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
            (esp32tap_dir / "harness" / filename)
            .read_text(encoding="utf-8")
            .splitlines()
        )
    )
    assert [int(row["rj45_pin"]) for row in rows] == list(range(1, 9))
    assert {int(row["rj45_pin"]): row["net"] for row in rows} == {
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
    assert all(
        row["wire_awg"] == "22" for row in rows if row["net"].startswith(("+8V", "GND"))
    )


def test_provisional_selection_binds_every_identity_to_official_evidence(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )

    assert selection["status"] == "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF"
    assert selection["placement_status"] == "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF"
    evidence = selection["official_part_evidence"]
    assert {
        "ALPHA-3051",
        "HT-151-00745",
        "TE-1932219-1",
    } <= set(evidence)
    validator.validate_selection(selection, candidates)

    empty_evidence = copy.deepcopy(selection)
    empty_evidence["official_part_evidence"] = {}
    with pytest.raises(validator.EvidenceError, match="evidence"):
        validator.validate_selection(empty_evidence, candidates)

    invented_header = copy.deepcopy(selection)
    invented_header["interfaces"]["console"]["header"]["mpn"] = "INVENTED"
    invented_header["interfaces"]["console"]["header"]["lcsc_code"] = "C0"
    with pytest.raises(validator.EvidenceError, match="identity|candidate|evidence"):
        validator.validate_selection(invented_header, candidates)

    invented_terminal = copy.deepcopy(selection)
    invented_terminal["interfaces"]["motor"]["terminal"]["mpn"] = "INVENTED"
    with pytest.raises(validator.EvidenceError, match="identity|evidence"):
        validator.validate_selection(invented_terminal, candidates)


def test_selected_two_amp_ratings_are_derived_from_exact_evidence(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )

    validator.validate_selection(selection, candidates)
    expected_derived = 4.0 * (20.0 / 30.0) ** 0.5 * 0.75
    for circuit_count, interface_name in ((8, "console"), (10, "motor")):
        interface = selection["interfaces"][interface_name]
        qualification = interface["current_qualification"]
        assert qualification == {
            "evidence_id": "MOLEX-MICROFIT-PS-43045",
            "wire_awg": 22,
            "selected_circuit_count": circuit_count,
            "conservative_reference_circuit_count": 12,
            "ambient_c": 85,
            "connector_maximum_c": 105,
            "official_temperature_rise_limit_c": 30,
            "base_current_per_contact_a": 4.0,
            "allowed_temperature_rise_c": 20,
            "engineering_safety_factor": 0.75,
            "formula": "I_base*sqrt(allowed_rise/official_rise)*safety_factor",
            "derived_current_per_contact_a": expected_derived,
            "expected_temperature_rise_at_2a_c": 7.5,
            "thermal_margin_at_2a_c": 12.5,
            "basis_class": "CONSERVATIVE_ENGINEERING_DERIVATION",
        }
        assert validator.derive_selected_contact_current_a(
            interface, selection["official_part_evidence"]
        ) == pytest.approx(expected_derived)
        assert expected_derived > 2.0

    changed = copy.deepcopy(selection)
    changed["interfaces"]["console"]["current_qualification"][
        "derived_current_per_contact_a"
    ] = 2.5
    with pytest.raises(validator.EvidenceError, match="derating|derived"):
        validator.validate_selection(changed, candidates)

    changed = copy.deepcopy(selection)
    changed["interfaces"]["motor"]["current_qualification"][
        "selected_circuit_count"
    ] = 8
    with pytest.raises(validator.EvidenceError, match="circuit"):
        validator.validate_selection(changed, candidates)

    changed = copy.deepcopy(selection)
    changed["official_part_evidence"]["MOLEX-MICROFIT-PS-43045"]["official_table"][
        "rows"
    ][0]["current_per_contact_a"] = 4.6
    with pytest.raises(validator.EvidenceError, match="evidence was altered"):
        validator.validate_selection(changed, candidates)

    changed = copy.deepcopy(selection)
    changed["official_part_evidence"]["MOLEX-MICROFIT-PS-43045"]["official_table"][
        "temperature_rise_limit_c"
    ] = 29
    with pytest.raises(validator.EvidenceError, match="evidence was altered"):
        validator.validate_selection(changed, candidates)

    changed = copy.deepcopy(selection)
    changed["official_part_evidence"]["MOLEX-MICROFIT-PS-43045"]["electrical_rating"][
        "temperature_max_c"
    ] = 104
    with pytest.raises(validator.EvidenceError, match="rating|evidence|derivation"):
        validator.validate_selection(changed, candidates)

    mutations = (
        ("connector_maximum_c", 104),
        ("official_temperature_rise_limit_c", 29),
        ("engineering_safety_factor", 0.74),
        ("formula", "unsupported fabricated formula"),
        ("expected_temperature_rise_at_2a_c", 7.4),
        ("thermal_margin_at_2a_c", 12.4),
    )
    for field, value in mutations:
        changed = copy.deepcopy(selection)
        changed["interfaces"]["console"]["current_qualification"][field] = value
        with pytest.raises(validator.EvidenceError, match="derivation|margin|evidence"):
            validator.validate_selection(changed, candidates)


def test_module_audits_cover_every_required_safety_field(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    matrix = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    validator.validate_module_audits(matrix, selection)
    modules = {item["mpn"]: item for item in matrix["modules"]}
    required = {
        "pad_map",
        "strapping_pins",
        "reserved_pins",
        "pulls",
        "adc_drive_capability",
        "decoupling",
        "footprint_area_mm2",
        "reset_rom_brownout_defaults",
        "used_signal_safe_boot_states",
        "rf",
        "flash",
        "native_usb",
        "production_evidence",
    }
    expected_used = set(modules["ESP32-S3-WROOM-1-N8"]["used_gpio_audit"])
    for module in modules.values():
        assert required <= set(module)
        assert set(module["strapping_pins"]) == {
            "GPIO0",
            "GPIO3",
            "GPIO45",
            "GPIO46",
        }
        assert module["footprint_area_mm2"] == pytest.approx(
            module["package_size_mm"]["width"] * module["package_size_mm"]["length"]
        )
        assert set(module["used_signal_safe_boot_states"]) == expected_used

    mini = modules["ESP32-S3-MINI-1-N8"]
    assert mini["decision"] == "REJECTED_UNQUALIFIED"
    assert mini["production_evidence"] == {
        "firmware_build": "ABSENT",
        "flash_log": "ABSENT",
        "boot_log": "ABSENT",
        "brownout_log": "ABSENT",
        "safety_matrix": "ABSENT",
    }
    incomplete = copy.deepcopy(matrix)
    del incomplete["modules"][0]["used_signal_safe_boot_states"]["GPIO21"]
    with pytest.raises(validator.EvidenceError, match="safe-boot"):
        validator.validate_module_audits(incomplete, selection)


def test_modeled_reversal_prevention_geometry_rejects_wrong_interface(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    concept = selection["reversal_prevention"]

    assert concept["status"] == "MODELED_FOR_TASK6_IMPLEMENTATION"
    assert concept["physical_proof_status"] == "OPEN_PENDING_DELIVERED_HARNESS"
    assert concept["concept"] == "DISTINCT_KEYED_RJ45_COLLARS_AND_APERTURES"
    assert concept["console"]["harness_length_mm"] == 180
    assert concept["motor"]["harness_length_mm"] == 240
    validator.validate_reversal_geometry(concept)

    wrong = copy.deepcopy(concept)
    wrong["motor"]["key_offset_x_mm"] = wrong["console"]["key_offset_x_mm"]
    with pytest.raises(validator.EvidenceError, match="wrong-mating|distinct"):
        validator.validate_reversal_geometry(wrong)


def test_provisional_harness_release_is_layout_only(esp32tap_dir: Path) -> None:
    validator = esp32tap_dir / "harness" / "validate_harnesses.py"
    layout = subprocess.run(
        [
            sys.executable,
            str(validator),
            "--release",
            "--action",
            "layout",
            "--basis",
            "conservative-predecessor",
        ],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    assert layout.returncode == 0, layout.stderr

    turnkey = subprocess.run(
        [
            sys.executable,
            str(validator),
            "--release",
            "--action",
            "turnkey_status",
            "--basis",
            "conservative-predecessor",
        ],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    assert turnkey.returncode != 0
    assert "provisional selection cannot release turnkey_status" in turnkey.stderr


CSV_FIELDS = [
    "rj45_pin",
    "board_position",
    "net",
    "wire_mpn",
    "wire_awg",
    "color",
    "label",
    "board_header",
    "board_housing",
    "terminal",
    "rj45_assembly",
    "strain_relief",
    "continuity_test",
]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def _write_csv(
    path: Path, rows: list[dict[str, str]], fieldnames: list[str] | None = None
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames or CSV_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize(
    ("interface_name", "filename"),
    (("console", "console-harness.csv"), ("motor", "motor-harness.csv")),
)
@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("rj45_pin", "8"),
        ("board_position", "2"),
        ("net", "+8V_B"),
        ("wire_mpn", "INVENTED-WIRE"),
        ("wire_awg", "24"),
        ("color", "violet"),
        ("label", "WRONG-P1"),
        ("board_header", "INVENTED-HEADER"),
        ("board_housing", "INVENTED-HOUSING"),
        ("terminal", "INVENTED-TERMINAL"),
        ("rj45_assembly", "INVENTED-RJ45"),
        ("strain_relief", "INVENTED-RELIEF"),
        ("continuity_test", "<=999mOhm,end-to-end"),
    ),
)
def test_harness_csv_binds_every_fabrication_column(
    esp32tap_dir: Path,
    tmp_path: Path,
    interface_name: str,
    filename: str,
    field: str,
    bad_value: str,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    rows = _csv_rows(esp32tap_dir / "harness" / filename)
    path = tmp_path / filename
    _write_csv(path, rows)
    validator.validate_harness_csv(
        path, interface_name, selection["interfaces"][interface_name]
    )

    rows[0][field] = bad_value
    _write_csv(path, rows)
    with pytest.raises(validator.EvidenceError, match="CSV|csv|column|row|exact"):
        validator.validate_harness_csv(
            path, interface_name, selection["interfaces"][interface_name]
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "swap_board_positions",
        "duplicate_row",
        "missing_row",
        "extra_row",
        "missing_header",
        "extra_header",
    ),
)
def test_harness_csv_rejects_structural_mutations(
    esp32tap_dir: Path, tmp_path: Path, mutation: str
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    rows = _csv_rows(esp32tap_dir / "harness" / "motor-harness.csv")
    fieldnames = list(CSV_FIELDS)
    if mutation == "swap_board_positions":
        rows[0]["board_position"], rows[1]["board_position"] = (
            rows[1]["board_position"],
            rows[0]["board_position"],
        )
    elif mutation == "duplicate_row":
        rows[1] = dict(rows[0])
    elif mutation == "missing_row":
        rows.pop()
    elif mutation == "extra_row":
        rows.append(dict(rows[-1]))
    elif mutation == "missing_header":
        fieldnames.remove("terminal")
    elif mutation == "extra_header":
        fieldnames.append("unexpected")
        for row in rows:
            row["unexpected"] = "bad"
    path = tmp_path / "motor.csv"
    _write_csv(path, rows, fieldnames)
    with pytest.raises(validator.EvidenceError, match="CSV|csv|column|row|exact"):
        validator.validate_harness_csv(path, "motor", selection["interfaces"]["motor"])


def test_wire_ampacity_remains_an_explicit_physical_gate(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )

    for interface in selection["interfaces"].values():
        wire_gate = interface["wire_current_qualification"]
        assert wire_gate["status"] == "OPEN_PHYSICAL_WIRE_AMPACITY"
        assert wire_gate["alpha_3051_dcr_ohm_per_1000ft_at_20c"] == 16.2
        assert wire_gate["microfit_test_conductor"] == "22 AWG tinned stranded copper"
        assert wire_gate["claim"] == "NOT_A_COMPLETE_PATH_CURRENT_QUALIFICATION"
    assert "OPEN_PHYSICAL_WIRE_AMPACITY" in selection["open_gates"]
    validator.validate_selection(selection, candidates)

    changed = copy.deepcopy(selection)
    changed["interfaces"]["console"]["wire_current_qualification"][
        "status"
    ] = "QUALIFIED"
    with pytest.raises(validator.EvidenceError, match="wire.*ampacity|wire.*open"):
        validator.validate_selection(changed, candidates)


def test_official_provenance_is_exact_and_mutation_resistant(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    expected_url = (
        "https://www.molex.com/content/dam/molex/molex-dot-com/products/"
        "automated/en-us/productspecificationpdf/430/43045/PS-43045-001.pdf"
    )
    selected = candidates["connector_candidates"][0]
    evidence = selection["official_part_evidence"]["MOLEX-MICROFIT-PS-43045"]
    assert selected["official_datasheet_url"] == expected_url
    assert evidence["official_manufacturer_url"] == expected_url
    assert evidence["document_revision"] == "R"
    assert evidence["document_date"] == "2025-11-14"
    assert evidence["table_locator"] == "Section 4.3, sheet 8 of 24"
    assert evidence["source_sha256"] == (
        "b5f03865599a0576c43ab82828960d874a12bcc9564eda619750ff9e26a81204"
    )
    validator.validate_selection(selection, candidates)

    for field, value in (
        ("official_manufacturer_url", "https://example.invalid/fake.pdf"),
        ("source_sha256", "0" * 64),
        ("document_revision", "FAKE"),
        ("table_locator", "nowhere"),
    ):
        changed = copy.deepcopy(selection)
        changed["official_part_evidence"]["MOLEX-MICROFIT-PS-43045"][field] = value
        with pytest.raises(
            validator.EvidenceError, match="official|provenance|evidence"
        ):
            validator.validate_selection(changed, candidates)


@pytest.mark.parametrize(
    ("branches", "rating"),
    (
        ([3.0, -1.0], 3.0),
        ([float("nan"), 0.65], 3.0),
        ([float("inf"), 0.0], float("inf")),
        ([1.6, 0.4], 1.5),
    ),
)
def test_unequal_current_rejects_nonfinite_negative_and_overrating(
    esp32tap_dir: Path, branches: list[float], rating: float
) -> None:
    validator = _load_validator(esp32tap_dir)
    with pytest.raises(validator.EvidenceError, match="finite|nonnegative|rating"):
        validator.validate_unequal_case(
            {"total_current_a": 2.0, "branch_current_a": branches},
            per_contact_derated_rating_a=rating,
        )


def test_selection_schema_is_exact_and_malformed_shapes_are_normalized(
    esp32tap_dir: Path,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    validator.validate_selection(selection, candidates)

    mutations = []
    for key in selection:
        changed = copy.deepcopy(selection)
        del changed[key]
        mutations.append(changed)
    extra = copy.deepcopy(selection)
    extra["unexpected"] = True
    mutations.append(extra)
    empty_switches = copy.deepcopy(selection)
    empty_switches["switches"] = {}
    mutations.append(empty_switches)
    malformed_interface = copy.deepcopy(selection)
    malformed_interface["interfaces"]["console"] = []
    mutations.append(malformed_interface)
    for changed in mutations:
        with pytest.raises(
            validator.EvidenceError, match="schema|fields|switch|interface"
        ):
            validator.validate_selection(changed, candidates)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("pad_map", "invented pad map"),
        ("native_usb", {"dm": "GPIO18", "dp": "GPIO20"}),
        ("strapping_pins", {"GPIO0": "wrong"}),
        ("reserved_pins", {"GPIO26-GPIO32": "available"}),
        ("pulls", {"GPIO0": "none"}),
        ("adc_drive_capability", {"GPIO4": "output only"}),
        ("decoupling", {"3v3_bulk": "none"}),
        ("used_signal_safe_boot_states", {"GPIO21": "relay on"}),
        ("flash", {"capacity_mb": 4}),
        ("rf", {"antenna": "external"}),
    ),
)
def test_module_audit_value_mutations_are_rejected(
    esp32tap_dir: Path, field: str, value: object
) -> None:
    validator = _load_validator(esp32tap_dir)
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    changed = copy.deepcopy(candidates)
    changed["modules"][0][field] = value
    with pytest.raises(validator.EvidenceError, match="module.*audit|module.*evidence"):
        validator.validate_module_audits(changed, selection)


@pytest.mark.parametrize(
    "mutation",
    (
        "negative_dimension",
        "bad_volume",
        "duplicate_candidate",
        "bad_url",
        "bad_packaging",
        "bad_status",
        "empty_rejection",
        "missing_module_combination",
    ),
)
def test_candidate_schema_rejects_corruption(esp32tap_dir: Path, mutation: str) -> None:
    validator = _load_validator(esp32tap_dir)
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    changed = copy.deepcopy(candidates)
    candidate = changed["connector_candidates"][0]
    combination = candidate["module_combinations"][0]
    if mutation == "negative_dimension":
        combination["pcb_width_mm"] = -1
    elif mutation == "bad_volume":
        combination["installed_bounding_volume_mm3"] += 1
    elif mutation == "duplicate_candidate":
        changed["connector_candidates"].append(copy.deepcopy(candidate))
    elif mutation == "bad_url":
        candidate["official_product_url"] = "https://example.invalid/fake"
    elif mutation == "bad_packaging":
        candidate["packaging"] = "loose"
    elif mutation == "bad_status":
        candidate["placement_status"] = "PLACED"
    elif mutation == "empty_rejection":
        combination["rejection_constraints"] = []
    elif mutation == "missing_module_combination":
        candidate["module_combinations"].pop()
    with pytest.raises(validator.EvidenceError, match="candidate|geometry|schema"):
        validator.validate_candidates(changed)


@pytest.mark.parametrize(
    ("evidence_id", "url_field", "hash_field"),
    (
        ("C240838", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C563827", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C127351", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C259745", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C259786", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C139797", "official_manufacturer_url", "lcsc_html_sha256"),
        ("C2913198", "official_manufacturer_url", "lcsc_html_sha256"),
        ("ALPHA-3051", "official_manufacturer_url", "source_sha256"),
        ("HT-151-00745", "official_manufacturer_url", "source_sha256"),
        ("TE-1932219-1", "official_manufacturer_url", "source_sha256"),
    ),
)
def test_every_selected_evidence_provenance_is_exact(
    esp32tap_dir: Path,
    evidence_id: str,
    url_field: str,
    hash_field: str,
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    changed_url = copy.deepcopy(selection)
    original_url = changed_url["official_part_evidence"][evidence_id][url_field]
    changed_url["official_part_evidence"][evidence_id][url_field] = (
        original_url.rsplit("/", 1)[0] + "/alternate"
    )
    with pytest.raises(validator.EvidenceError, match="provenance|official evidence"):
        validator.validate_selection(changed_url, candidates)

    changed_hash = copy.deepcopy(selection)
    changed_hash["official_part_evidence"][evidence_id][hash_field] = "a" * 64
    with pytest.raises(validator.EvidenceError, match="provenance|official evidence"):
        validator.validate_selection(changed_hash, candidates)


@pytest.mark.parametrize(
    "mutation",
    (
        "switch_status",
        "switch_packaging",
        "switch_extra",
        "module_decision",
        "module_extra",
        "gates_mapping",
        "gates_empty",
        "missing_wire_gate",
        "missing_live_jlc",
        "missing_firm_harness",
        "missing_rj45_open",
        "missing_reversal",
        "missing_thermal_drop",
    ),
)
def test_nested_selection_schema_is_exact(esp32tap_dir: Path, mutation: str) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    changed = copy.deepcopy(selection)
    if mutation == "switch_status":
        changed["switches"]["reset"]["placement_status"] = "PLACED"
    elif mutation == "switch_packaging":
        changed["switches"]["reset"]["packaging"] = "loose"
    elif mutation == "switch_extra":
        changed["switches"]["reset"]["unexpected"] = True
    elif mutation == "module_decision":
        changed["module"]["decision"] = "MIGRATE_TO_MINI"
    elif mutation == "module_extra":
        changed["module"]["unexpected"] = True
    elif mutation == "gates_mapping":
        changed["open_gates"] = {"OPEN_PHYSICAL_WIRE_AMPACITY": True}
    elif mutation == "gates_empty":
        changed["open_gates"] = []
    else:
        gate_fragments = {
            "missing_wire_gate": "OPEN_PHYSICAL_WIRE_AMPACITY",
            "missing_live_jlc": "live JLC",
            "missing_firm_harness": "firm quote",
            "missing_rj45_open": "RJ45 single-open",
            "missing_reversal": "reversal physical",
            "missing_thermal_drop": "thermal/drop",
        }
        fragment = gate_fragments[mutation]
        changed["open_gates"] = [
            gate for gate in changed["open_gates"] if fragment not in gate
        ]
    with pytest.raises(validator.EvidenceError, match="switch|module|gate|schema"):
        validator.validate_selection(changed, candidates)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("voltage_v", "600"),
        ("temperature_min_c", None),
        ("temperature_max_c", float("nan")),
    ),
)
def test_selected_rating_malformed_values_are_evidence_errors(
    esp32tap_dir: Path, field: str, value: object
) -> None:
    validator = _load_validator(esp32tap_dir)
    selection = json.loads(
        (esp32tap_dir / "bom" / "REV-C-PART-SELECTION.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (esp32tap_dir / "harness" / "candidates.json").read_text(encoding="utf-8")
    )
    changed = copy.deepcopy(selection)
    changed["interfaces"]["console"]["header"]["rating"][field] = value
    with pytest.raises(validator.EvidenceError, match="rating|finite"):
        validator.validate_selection(changed, candidates)
