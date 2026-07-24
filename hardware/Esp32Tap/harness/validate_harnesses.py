#!/usr/bin/env python3
"""Audit Rev C harness requirements and optionally enforce their release gate."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from evidence.schemas import EvidenceError, load_all, require_release  # noqa: E402


REQUIREMENT_FIELDS = {
    "revision",
    "status",
    "release_action",
    "interfaces",
    "owner_fabrication_allowed",
}
POWER_GROUND_NETS = {"+8V_A", "+8V_B", "GND_A", "GND_B"}
RJ45_NET_BY_PIN = {
    1: "GND_A",
    2: "+8V_A",
    3: "DATA_A",
    4: "DATA_B",
    5: "DATA_C",
    6: "DATA_D",
    7: "GND_B",
    8: "+8V_B",
}
SYSTEM_ELEMENTS = {
    "header",
    "housing",
    "terminal",
    "wire",
    "rj45_termination",
}
EXPECTED_INTERFACE_IDENTITIES = {
    "console": {
        "header": ("Molex", "430450809", "C240838"),
        "housing": ("Molex", "430250800", "C127351"),
    },
    "motor": {
        "header": ("Molex", "430451010", "C563827"),
        "housing": ("Molex", "430251000", "C259745"),
    },
}
COMMON_HARNESS_IDENTITIES = {
    "terminal": ("Molex", "430300001", "C259786"),
    "wire": ("Alpha Wire", "3051", None),
    "strain_relief": ("HellermannTyton", "151-00745", None),
    "rj45_termination": ("TE Connectivity", "1932219-1", None),
}
EXPECTED_MICROFIT_DERATING_ROWS = [
    {
        "wire_awg": 22,
        "circuit_count": 8,
        "ambient_c": 85,
        "current_per_contact_a": 4.0,
    },
    {
        "wire_awg": 22,
        "circuit_count": 10,
        "ambient_c": 85,
        "current_per_contact_a": 4.0,
    },
]


def validate_requirements(record: object) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != REQUIREMENT_FIELDS:
        raise EvidenceError("harness requirement fields are not exact")
    if record["revision"] != "C":
        raise EvidenceError("harness revision must be C")
    if record["status"] not in {"HOLD_NOT_MEASURED", "SELECTED"}:
        raise EvidenceError("harness status is invalid")
    if record["release_action"] != "connector_selection":
        raise EvidenceError("harness release action is invalid")
    if not isinstance(record["interfaces"], list):
        raise EvidenceError("harness interfaces must be a list")
    if record["owner_fabrication_allowed"] is not False:
        raise EvidenceError("owner harness fabrication must remain forbidden")
    if record["status"] == "HOLD_NOT_MEASURED" and record["interfaces"]:
        raise EvidenceError("HOLD harness requirements cannot select interfaces")
    return record


def _require_rating(name: str, element: object) -> None:
    if not isinstance(element, dict) or not isinstance(element.get("rating"), dict):
        raise EvidenceError(f"{name} must define a rating")
    rating = element["rating"]
    if rating.get("voltage_v", 0) < 24:
        raise EvidenceError(f"{name} voltage rating is below 24 V")
    if rating.get("temperature_min_c", 999) > -20:
        raise EvidenceError(f"{name} does not cover -20 C")
    if rating.get("temperature_max_c", -999) < 85:
        raise EvidenceError(f"{name} does not cover +85 C")


def _matching_evidence(
    element: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    matches = [
        item
        for item in evidence.values()
        if item.get("manufacturer") == element.get("manufacturer")
        and item.get("mpn") == element.get("mpn")
        and (
            "lcsc_code" not in element
            or item.get("lcsc_code") == element.get("lcsc_code")
        )
    ]
    if len(matches) != 1:
        raise EvidenceError(
            f"selected identity lacks exact evidence: "
            f"{element.get('manufacturer')} {element.get('mpn')}"
        )
    return matches[0]


def _identity(element: dict[str, Any]) -> tuple[object, object, object]:
    return (
        element.get("manufacturer"),
        element.get("mpn"),
        element.get("lcsc_code"),
    )


def derive_selected_contact_current_a(
    interface: dict[str, Any], evidence: dict[str, Any]
) -> float:
    qualification = interface.get("current_qualification")
    if not isinstance(qualification, dict):
        raise EvidenceError("current derating qualification is absent")
    evidence_id = qualification.get("evidence_id")
    source = evidence.get(evidence_id)
    if not isinstance(source, dict):
        raise EvidenceError("current derating evidence is absent")
    if (
        source.get("manufacturer"),
        source.get("mpn"),
        source.get("derating_table"),
    ) != ("Molex", "PS-43045", EXPECTED_MICROFIT_DERATING_ROWS):
        raise EvidenceError("official Micro-Fit derating evidence was altered")
    if qualification.get("circuit_count") != interface["header"].get("positions"):
        raise EvidenceError("current derating circuit count does not match header")
    if qualification.get("circuit_count") != interface["housing"].get("positions"):
        raise EvidenceError("current derating circuit count does not match housing")
    if qualification.get("wire_awg") != interface["wire"].get("power_ground_awg"):
        raise EvidenceError("current derating wire gauge does not match selected wire")
    lookup_fields = ("wire_awg", "circuit_count", "ambient_c")
    rows = [
        row
        for row in source.get("derating_table", [])
        if all(row.get(field) == qualification.get(field) for field in lookup_fields)
    ]
    if len(rows) != 1:
        raise EvidenceError("exact circuit-count/+85 C derating row is absent")
    derived = float(rows[0]["current_per_contact_a"])
    if qualification.get("ambient_c") != 85:
        raise EvidenceError("current derating must use +85 C")
    if qualification.get("derived_current_per_contact_a") != derived:
        raise EvidenceError("selected derived current does not match derating evidence")
    return derived


def validate_reversal_geometry(concept: object) -> None:
    if not isinstance(concept, dict):
        raise EvidenceError("modeled reversal-prevention geometry is absent")
    if concept.get("status") != "MODELED_FOR_TASK6_IMPLEMENTATION":
        raise EvidenceError("reversal-prevention status is not modeled for Task 6")
    if concept.get("concept") != "DISTINCT_KEYED_RJ45_COLLARS_AND_APERTURES":
        raise EvidenceError("reversal-prevention concept is not exact")
    common = concept.get("common_geometry", {})
    if (
        common.get("collar_body_width_mm", 999) >= common.get("aperture_width_mm", 0)
        or common.get("collar_body_height_mm", 999)
        >= common.get("aperture_height_mm", 0)
        or common.get("key_rib_width_mm", 999)
        >= common.get("key_slot_width_mm", 0)
        or common.get("key_rib_depth_mm", 999)
        >= common.get("key_slot_depth_mm", 0)
    ):
        raise EvidenceError("matching keyed collar lacks insertion clearance")
    console = concept.get("console", {})
    motor = concept.get("motor", {})
    if console.get("key_code") == motor.get("key_code"):
        raise EvidenceError("reversal keys are not distinct")
    separation = abs(
        float(console.get("key_offset_x_mm", 0))
        - float(motor.get("key_offset_x_mm", 0))
    )
    if separation <= common.get("key_slot_width_mm", 0):
        raise EvidenceError("wrong-mating key offsets are not distinct")
    modeled = concept.get("wrong_mating_geometry", {})
    collision_margin = separation - float(common["key_slot_width_mm"])
    if (
        modeled.get("key_offset_separation_mm") != separation
        or abs(modeled.get("minimum_collision_margin_mm", -1) - collision_margin)
        > 1e-9
    ):
        raise EvidenceError("wrong-mating collision geometry is inconsistent")
    if min(
        console.get("harness_length_mm", 0),
        motor.get("harness_length_mm", 0),
        console.get("minimum_service_slack_mm", 0),
        motor.get("minimum_service_slack_mm", 0),
    ) <= 0:
        raise EvidenceError("end-to-end harness reach dimensions are absent")
    if concept.get("physical_proof_status") != "OPEN_PENDING_DELIVERED_HARNESS":
        raise EvidenceError("physical wrong-mating proof must remain open")


def validate_module_audits(
    candidates: dict[str, Any], selection: dict[str, Any]
) -> None:
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
    modules = {
        item.get("mpn"): item
        for item in candidates.get("modules", [])
        if isinstance(item, dict)
    }
    if set(modules) != {
        "ESP32-S3-WROOM-1-N8",
        "ESP32-S3-MINI-1-N8",
    }:
        raise EvidenceError("WROOM/MINI module candidates are not exact")
    wroom = modules["ESP32-S3-WROOM-1-N8"]
    mini = modules["ESP32-S3-MINI-1-N8"]
    for module in modules.values():
        if not required <= set(module):
            raise EvidenceError(f"{module.get('mpn')} module audit is incomplete")
        if set(module["strapping_pins"]) != {
            "GPIO0",
            "GPIO3",
            "GPIO45",
            "GPIO46",
        }:
            raise EvidenceError(f"{module['mpn']} strapping-pin audit is incomplete")
        area = (
            float(module["package_size_mm"]["width"])
            * float(module["package_size_mm"]["length"])
        )
        if abs(float(module["footprint_area_mm2"]) - area) > 1e-9:
            raise EvidenceError(f"{module['mpn']} footprint area is inconsistent")
        if set(module["used_signal_safe_boot_states"]) != set(
            wroom["used_gpio_audit"]
        ):
            raise EvidenceError(f"{module['mpn']} safe-boot signal audit is incomplete")
    selected_module = selection.get("module", {})
    if (
        selected_module.get("mpn"),
        selected_module.get("lcsc_code"),
    ) != (wroom.get("mpn"), wroom.get("lcsc_code")):
        raise EvidenceError("selected WROOM identity does not match module candidate")
    if mini.get("decision") != "REJECTED_UNQUALIFIED" or any(
        status != "ABSENT" for status in mini["production_evidence"].values()
    ):
        raise EvidenceError("MINI must remain rejected with absent production evidence")


def validate_selection(
    record: object, candidates: object | None = None
) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("revision") != "C":
        raise EvidenceError("Rev C part selection is required")
    if record.get("status") != "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF":
        raise EvidenceError("selection must remain provisional")
    if (
        record.get("placement_status")
        != "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF"
    ):
        raise EvidenceError("public stock must not be promoted to placement proof")
    evidence = record.get("official_part_evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise EvidenceError("official part evidence is absent")
    if not isinstance(candidates, dict):
        raise EvidenceError("candidate matrix is absent")
    connector_identities = {
        (item.get("manufacturer"), item.get("mpn"), item.get("lcsc_code"))
        for item in candidates.get("connector_candidates", [])
    }
    connector_identities.update(
        (variant.get("manufacturer"), variant.get("mpn"), variant.get("lcsc_code"))
        for item in candidates.get("connector_candidates", [])
        for variant in item.get("selected_variants", [])
    )
    interfaces = record.get("interfaces")
    if not isinstance(interfaces, dict) or set(interfaces) != {"console", "motor"}:
        raise EvidenceError("exact console and motor interfaces are required")
    console = interfaces["console"]
    motor = interfaces["motor"]
    if console["header"]["mpn"] == motor["header"]["mpn"]:
        raise EvidenceError("console and motor headers must be physically incompatible")
    if (console["housing"]["positions"], motor["housing"]["positions"]) != (8, 10):
        raise EvidenceError("console/motor physical keying must be 8 versus 10 positions")
    for interface_name, interface in interfaces.items():
        header = interface["header"]
        expected_identities = {
            **COMMON_HARNESS_IDENTITIES,
            **EXPECTED_INTERFACE_IDENTITIES[interface_name],
        }
        for element_name, expected in expected_identities.items():
            if _identity(interface[element_name]) != expected:
                raise EvidenceError(
                    f"{interface_name}.{element_name} identity is not the exact selection"
                )
        if (
            header.get("manufacturer"),
            header.get("mpn"),
            header.get("lcsc_code"),
        ) not in connector_identities:
            raise EvidenceError(f"{interface_name} header identity is not a candidate")
        for element_name in (
            "header",
            "housing",
            "terminal",
            "wire",
            "strain_relief",
            "rj45_termination",
        ):
            _matching_evidence(interface[element_name], evidence)
        if set(interface["contact_nets"]) != {
            "+8V_A",
            "+8V_B",
            "DATA_A",
            "DATA_B",
            "DATA_C",
            "DATA_D",
            "GND_A",
            "GND_B",
        }:
            raise EvidenceError(f"{interface_name} contact nets are incomplete")
        if interface["header"].get("packaging") != "tape_and_reel":
            raise EvidenceError(f"{interface_name} board header is not tape-and-reel")
        derived_current = derive_selected_contact_current_a(interface, evidence)
        if derived_current < 2.0:
            raise EvidenceError(
                f"{interface_name} exact Micro-Fit path is below 2.0 A"
            )
        system_rating = evidence["MOLEX-MICROFIT-PS-43045"][
            "electrical_rating"
        ]
        for element_name in ("header", "housing", "terminal"):
            if interface[element_name].get("rating") != system_rating:
                raise EvidenceError(
                    f"{interface_name}.{element_name} rating is not evidence-bound"
                )
        if interface["wire"].get("power_ground_awg", 999) > 22:
            raise EvidenceError(f"{interface_name} power/ground wire is smaller than 22 AWG")
        wire_evidence = _matching_evidence(interface["wire"], evidence)
        if (
            interface["wire"].get("power_ground_awg")
            != wire_evidence.get("wire_awg")
            or interface["wire"].get("rating")
            != wire_evidence.get("electrical_rating")
            or set(interface["wire"].get("exact_color_mpns", {}).values())
            != set(wire_evidence.get("exact_color_mpns", []))
        ):
            raise EvidenceError(
                f"{interface_name} wire identity/rating/colors are not evidence-bound"
            )
        for element_name in SYSTEM_ELEMENTS:
            _require_rating(f"{interface_name}.{element_name}", interface[element_name])
        strain_environment = interface["strain_relief"].get("environment", {})
        strain_evidence = _matching_evidence(interface["strain_relief"], evidence)
        if (
            strain_environment.get("temperature_min_c", 999) > -20
            or strain_environment.get("temperature_max_c", -999) < 85
            or strain_environment != strain_evidence.get("environment")
        ):
            raise EvidenceError(
                f"{interface_name}.strain_relief does not cover -20..+85 C"
            )
        rj45 = interface["rj45_termination"]
        if rj45.get("single_open_2a_status") != "UNSUPPORTED_OPEN_PHYSICAL_GATE":
            raise EvidenceError("RJ45 single-open limitation must remain explicit")
        validate_rj45_normal_case(rj45, _matching_evidence(rj45, evidence))
        if interface["factory_assembly"].get("owner_crimping") is not False:
            raise EvidenceError("owner crimping is forbidden")
        for open_net in POWER_GROUND_NETS:
            validate_single_open(interface, evidence=evidence, open_net=open_net)
    switch_identities = {
        (item.get("manufacturer"), item.get("mpn"), item.get("lcsc_code"))
        for item in candidates.get("switches", [])
    }
    for name, switch in record.get("switches", {}).items():
        if _identity(switch) != ("ALPSALPINE", "SKRPACE010", "C139797"):
            raise EvidenceError(f"{name} switch identity is not the exact selection")
        _matching_evidence(switch, evidence)
        if (
            switch.get("manufacturer"),
            switch.get("mpn"),
            switch.get("lcsc_code"),
        ) not in switch_identities:
            raise EvidenceError(f"{name} switch identity is not a candidate")
    if record.get("mini_decision") != "REJECTED_UNQUALIFIED":
        raise EvidenceError("MINI must remain rejected without production evidence")
    _matching_evidence(record.get("module", {}), evidence)
    validate_module_audits(candidates, record)
    validate_reversal_geometry(record.get("reversal_prevention"))
    return record


def remaining_contact_current_a(
    interface: dict[str, Any], *, open_net: str, total_current_a: float
) -> float:
    if open_net not in POWER_GROUND_NETS:
        raise EvidenceError(f"{open_net} is not an individual power/ground contact")
    if open_net not in interface["contact_nets"]:
        raise EvidenceError(f"{open_net} is absent")
    # No sharing credit: after one nominally parallel board contact opens, the
    # remaining new-header/crimp/wire path is assigned the complete load.
    return float(total_current_a)


def validate_single_open(
    interface: dict[str, Any],
    *,
    evidence: dict[str, Any],
    open_net: str,
    total_current_a: float = 2.0,
) -> None:
    assigned = remaining_contact_current_a(
        interface, open_net=open_net, total_current_a=total_current_a
    )
    if derive_selected_contact_current_a(interface, evidence) < assigned:
        raise EvidenceError(
            f"individual {open_net} Micro-Fit path cannot carry {assigned:.1f} A"
        )
    if interface["wire"]["power_ground_awg"] > 22:
        raise EvidenceError(f"individual {open_net} wire is smaller than 22 AWG")


def validate_unequal_case(
    case: object, *, per_contact_derated_rating_a: float
) -> None:
    if not isinstance(case, dict) or case.get("total_current_a") != 2.0:
        raise EvidenceError("unequal case must model exactly 2.0 A total")
    branches = case.get("branch_current_a")
    if (
        not isinstance(branches, list)
        or len(branches) != 2
        or branches[0] == branches[1]
        or abs(sum(branches) - 2.0) > 1e-9
    ):
        raise EvidenceError("unequal case branches must be unequal and sum to 2.0 A")
    if per_contact_derated_rating_a < 2.0:
        raise EvidenceError("individual contact rating must be at least 2.0 A")


def validate_rj45_normal_case(
    rj45: object, evidence: dict[str, Any] | None = None
) -> None:
    if not isinstance(rj45, dict):
        raise EvidenceError("RJ45 termination record is absent")
    branches = rj45.get("normal_unequal_branch_current_a")
    if (
        rj45.get("normal_total_current_a") != 2.0
        or not isinstance(branches, list)
        or len(branches) != 2
        or branches[0] == branches[1]
        or abs(sum(branches) - 2.0) > 1e-9
    ):
        raise EvidenceError("RJ45 normal case must be unequal and total 2.0 A")
    if not isinstance(evidence, dict):
        raise EvidenceError("RJ45 official evidence is absent")
    official_rating = evidence.get("electrical_rating", {})
    published = official_rating.get("published_max_current_per_contact_a", 0)
    if rj45.get("published_max_current_per_contact_a") != published:
        raise EvidenceError("RJ45 selected current does not match official evidence")
    if rj45.get("rating") != {
        key: official_rating[key]
        for key in ("voltage_v", "temperature_min_c", "temperature_max_c")
    }:
        raise EvidenceError("RJ45 selected rating does not match official evidence")
    if any(branch > published for branch in branches):
        raise EvidenceError("RJ45 normal branch exceeds official per-contact rating")
    if official_rating.get("temperature_max_c", -999) < 85:
        raise EvidenceError("RJ45 official rating does not cover +85 C")
    if rj45.get("single_open_2a_status") != "UNSUPPORTED_OPEN_PHYSICAL_GATE":
        raise EvidenceError("RJ45 single-open 2 A must remain unsupported")


def validate_harness_csv(path: Path, interface: dict[str, Any]) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if [int(row["rj45_pin"]) for row in rows] != list(range(1, 9)):
        raise EvidenceError(f"{path.name} must map RJ45 pins 1 through 8 exactly once")
    if len({row["board_position"] for row in rows}) != 8:
        raise EvidenceError(f"{path.name} board positions are not one-to-one")
    if {row["net"] for row in rows} != set(interface["contact_nets"]):
        raise EvidenceError(f"{path.name} nets do not match the selected interface")
    for row in rows:
        pin = int(row["rj45_pin"])
        if row["net"] != RJ45_NET_BY_PIN[pin]:
            raise EvidenceError(
                f"{path.name} pin {pin} must be {RJ45_NET_BY_PIN[pin]}"
            )
        if row["net"] in POWER_GROUND_NETS and int(row["wire_awg"]) > 22:
            raise EvidenceError(f"{path.name} has undersized power/ground wire")
        if row["continuity_test"] != "<=100mOhm,end-to-end":
            raise EvidenceError(f"{path.name} continuity limit is not exact")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    parser.add_argument(
        "--action",
        choices=(
            "connector_selection",
            "layout",
            "verification_fabrication",
            "no_purchase_quote",
            "production_release",
            "deployment",
            "turnkey_status",
        ),
    )
    parser.add_argument(
        "--basis",
        choices=("conservative-predecessor",),
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(arguments)
    if args.action and not args.release:
        parser.error("--action requires --release")
    if args.basis and not args.release:
        parser.error("--basis requires --release")
    try:
        requirements = validate_requirements(
            json.loads((HERE / "requirements.json").read_text(encoding="utf-8"))
        )
        evidence = load_all(ROOT / "evidence")
        selection_path = ROOT / "bom" / "REV-C-PART-SELECTION.json"
        if selection_path.exists():
            candidates = json.loads(
                (HERE / "candidates.json").read_text(encoding="utf-8")
            )
            selection = validate_selection(
                json.loads(selection_path.read_text(encoding="utf-8")),
                candidates,
            )
            validate_harness_csv(
                HERE / "console-harness.csv", selection["interfaces"]["console"]
            )
            validate_harness_csv(
                HERE / "motor-harness.csv", selection["interfaces"]["motor"]
            )
            validate_unequal_case(
                {"total_current_a": 2.0, "branch_current_a": [1.35, 0.65]},
                per_contact_derated_rating_a=min(
                    derive_selected_contact_current_a(
                        interface, selection["official_part_evidence"]
                    )
                    for interface in selection["interfaces"].values()
                ),
            )
        if args.release:
            action = args.action or requirements["release_action"]
            if (
                selection_path.exists()
                and action in {"production_release", "deployment", "turnkey_status"}
            ):
                raise EvidenceError(
                    f"provisional selection cannot release {action}"
                )
            require_release(evidence, action, basis=args.basis)
    except (EvidenceError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VALID harness requirements status={requirements['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
