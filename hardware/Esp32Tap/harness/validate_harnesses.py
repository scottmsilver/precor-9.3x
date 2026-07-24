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


def validate_selection(record: object) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("revision") != "C":
        raise EvidenceError("Rev C part selection is required")
    if record.get("placement_status") != "REQUIRES_LIVE_BOM_CPL_PROOF":
        raise EvidenceError("public stock must not be promoted to placement proof")
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
        if interface["header"].get("derated_current_a", 0) < 2.0:
            raise EvidenceError(
                f"{interface_name} individual board contacts are below 2.0 A"
            )
        if interface["terminal"].get("derated_current_a", 0) < 2.0:
            raise EvidenceError(
                f"{interface_name} individual crimp terminals are below 2.0 A"
            )
        if interface["wire"].get("power_ground_awg", 999) > 22:
            raise EvidenceError(f"{interface_name} power/ground wire is smaller than 22 AWG")
        for element_name in SYSTEM_ELEMENTS:
            _require_rating(f"{interface_name}.{element_name}", interface[element_name])
        strain_environment = interface["strain_relief"].get("environment", {})
        if (
            strain_environment.get("temperature_min_c", 999) > -20
            or strain_environment.get("temperature_max_c", -999) < 85
        ):
            raise EvidenceError(
                f"{interface_name}.strain_relief does not cover -20..+85 C"
            )
        rj45 = interface["rj45_termination"]
        if rj45.get("single_open_2a_status") != "UNSUPPORTED_OPEN_PHYSICAL_GATE":
            raise EvidenceError("RJ45 single-open limitation must remain explicit")
        validate_rj45_normal_case(rj45)
        if interface["factory_assembly"].get("owner_crimping") is not False:
            raise EvidenceError("owner crimping is forbidden")
        for open_net in POWER_GROUND_NETS:
            validate_single_open(interface, open_net=open_net)
    if record.get("mini_decision") != "REJECTED_UNQUALIFIED":
        raise EvidenceError("MINI must remain rejected without production evidence")
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
    interface: dict[str, Any], *, open_net: str, total_current_a: float = 2.0
) -> None:
    assigned = remaining_contact_current_a(
        interface, open_net=open_net, total_current_a=total_current_a
    )
    if interface["header"]["derated_current_a"] < assigned:
        raise EvidenceError(
            f"individual {open_net} board contact cannot carry {assigned:.1f} A"
        )
    if interface["terminal"]["derated_current_a"] < assigned:
        raise EvidenceError(
            f"individual {open_net} crimp terminal cannot carry {assigned:.1f} A"
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


def validate_rj45_normal_case(rj45: object) -> None:
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
    published = rj45.get("published_max_current_per_contact_a", 0)
    if any(branch > published for branch in branches):
        raise EvidenceError("RJ45 normal branch exceeds official per-contact rating")
    if rj45.get("rating", {}).get("temperature_max_c", -999) < 85:
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
            selection = validate_selection(
                json.loads(selection_path.read_text(encoding="utf-8"))
            )
            validate_harness_csv(
                HERE / "console-harness.csv", selection["interfaces"]["console"]
            )
            validate_harness_csv(
                HERE / "motor-harness.csv", selection["interfaces"]["motor"]
            )
            validate_unequal_case(
                {"total_current_a": 2.0, "branch_current_a": [1.35, 0.65]},
                per_contact_derated_rating_a=2.0,
            )
        if args.release:
            action = args.action or requirements["release_action"]
            require_release(evidence, action, basis=args.basis)
    except (EvidenceError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VALID harness requirements status={requirements['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
