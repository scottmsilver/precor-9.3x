#!/usr/bin/env python3
"""Audit Rev C harness requirements and optionally enforce their release gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable
from urllib.parse import urlparse


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
ELECTRICAL_LIMIT_FIELDS = {
    "schema_version",
    "basis",
    "total_current_a",
    "normal_unequal_branch_current_a",
    "new_board_connectors",
    "wire",
    "rj45",
    "pcb",
    "source",
    "local_load",
    "usb_ground",
    "unsupported",
}
ELECTRICAL_UNSUPPORTED = {
    "RJ45_SINGLE_OPEN_2A",
    "MINIMUM_VIN",
    "SOURCE_IMPEDANCE",
    "AMBIENT_THERMAL",
    "TRANSIENT_RESPONSE",
    "COMPLETE_INSTALLED_DROP",
    "USB_RETURN_CURRENT",
    "ESD",
    "RF",
    "SWITCHING_LOOP",
}
EXPECTED_MICROFIT_OFFICIAL_TABLE = {
    "configuration": "wire_to_board",
    "wire_awg": 22,
    "temperature_rise_limit_c": 30,
    "ambient_derating_required": True,
    "manufacturer_published_85c_rating": False,
    "rows": [
        {"circuit_count": 6, "current_per_contact_a": 4.5},
        {"circuit_count": 12, "current_per_contact_a": 4.0},
    ],
}
MICROFIT_DERIVATION_FORMULA = "I_base*sqrt(allowed_rise/official_rise)*safety_factor"
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
SELECTION_FIELDS = {
    "revision",
    "status",
    "retrieved_at",
    "placement_status",
    "official_part_evidence",
    "interfaces",
    "reversal_prevention",
    "switches",
    "module",
    "mini_decision",
    "open_gates",
}
COMMON_INTERFACE_FIELDS = {
    "header",
    "housing",
    "terminal",
    "wire",
    "strain_relief",
    "rj45_termination",
    "current_qualification",
    "wire_current_qualification",
    "contact_nets",
    "factory_assembly",
    "routing_gate",
}
INTERFACE_ELEMENT_FIELDS = {
    "header": {
        "manufacturer",
        "mpn",
        "lcsc_code",
        "positions",
        "packaging",
        "rating",
    },
    "housing": {"manufacturer", "mpn", "lcsc_code", "positions", "rating"},
    "terminal": {
        "manufacturer",
        "mpn",
        "lcsc_code",
        "packaging",
        "wire_awg",
        "rating",
    },
    "wire": {
        "manufacturer",
        "mpn",
        "exact_color_mpns",
        "power_ground_awg",
        "signal_awg",
        "colors",
        "rating",
    },
    "strain_relief": {
        "manufacturer",
        "mpn",
        "description",
        "environment",
    },
    "rj45_termination": {
        "manufacturer",
        "mpn",
        "type",
        "rating",
        "published_max_current_per_contact_a",
        "rating_application",
        "normal_total_current_a",
        "normal_unequal_branch_current_a",
        "single_open_2a_status",
        "predecessor_exception",
    },
}
CURRENT_QUALIFICATION_FIELDS = {
    "evidence_id",
    "wire_awg",
    "selected_circuit_count",
    "conservative_reference_circuit_count",
    "ambient_c",
    "connector_maximum_c",
    "official_temperature_rise_limit_c",
    "base_current_per_contact_a",
    "allowed_temperature_rise_c",
    "engineering_safety_factor",
    "formula",
    "derived_current_per_contact_a",
    "expected_temperature_rise_at_2a_c",
    "thermal_margin_at_2a_c",
    "basis_class",
}
MODULE_AUDIT_FIELDS = {
    "pad_map",
    "strapping_pins",
    "reserved_pins",
    "pulls",
    "adc_drive_capability",
    "decoupling",
    "footprint_area_mm2",
    "reset_rom_brownout_defaults",
    "used_gpio_audit",
    "used_signal_safe_boot_states",
    "rf",
    "flash",
    "native_usb",
    "production_evidence",
}
MODULE_AUDIT_SHA256 = {
    "ESP32-S3-WROOM-1-N8": "6252aaa46c189e853e096b0b227bbf25ffbdcbfa8703ccb7a59f40506a704035",
    "ESP32-S3-MINI-1-N8": "825d36cc81a51bb3ea2dc64a67e6f812d45c6068cbaaca786c6124a751730812",
}
ALLOWED_OFFICIAL_DOMAINS = {
    "www.molex.com",
    "www.alphawire.com",
    "www.hellermanntyton.com",
    "www.te.com",
    "www.espressif.com",
    "tech.alpsalpine.com",
}
CONNECTOR_CANDIDATE_FIELDS = {
    "kind",
    "family",
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
    "board_mount",
    "packaging",
    "viability",
    "positions",
    "locking",
    "rating",
    "mating_housing",
    "terminal",
    "footprint_provenance",
    "step_provenance",
    "rejection_constraints",
    "module_combinations",
}
MODULE_COMBINATION_FIELDS = {
    "module",
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
MODULE_FIELDS = {
    "manufacturer",
    "mpn",
    "lcsc_code",
    "official_product_url",
    "official_datasheet_url",
    "retrieved_at",
    "stock",
    "packaging",
    "assembly_class",
    "placement_status",
    "source_sha256",
    "package_size_mm",
    "footprint_area_mm2",
    "pad_map",
    "strapping_pins",
    "reserved_pins",
    "pulls",
    "adc_drive_capability",
    "decoupling",
    "reset_rom_brownout_defaults",
    "native_usb",
    "used_gpio_audit",
    "used_signal_safe_boot_states",
    "rf",
    "flash",
    "production_evidence",
    "boot_rf_safety",
    "decision",
}
SWITCH_CANDIDATE_FIELDS = {
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
    "packaging",
    "footprint_provenance",
    "module_combinations",
}
EXPECTED_SELECTED_PROVENANCE = {
    "C240838": {
        "official_manufacturer_url": "https://www.molex.com/en-us/products/part-detail/430450809",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C240838.html",
        "lcsc_html_sha256": "f1dddbc882ac2866762d8578c1f6ecaf856522cf971f59b7588bb4ef5855a54a",
    },
    "C563827": {
        "official_manufacturer_url": "https://www.molex.com/en-us/products/part-detail/430451010",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C563827.html",
        "lcsc_html_sha256": "d2a821250f3f4c104759a0d59209c56be773d21f5e1d1ec6ee3984ecdebaca73",
    },
    "C127351": {
        "official_manufacturer_url": "https://www.molex.com/en-us/products/part-detail/430250800",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C127351.html",
        "lcsc_html_sha256": "12ac1b20343f69ad14da389c8ea145d75c74e7264483190f0ebfcd89a7b6bee3",
    },
    "C259745": {
        "official_manufacturer_url": "https://www.molex.com/en-us/products/part-detail/430251000",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C259745.html",
        "lcsc_html_sha256": "e9f8f9a9be55eea92905bc41c944b7ea3aece99875e8fa8b82fb8980482c9fc7",
    },
    "C259786": {
        "official_manufacturer_url": "https://www.molex.com/en-us/products/part-detail/430300001",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C259786.html",
        "lcsc_html_sha256": "ed573c87246eda0f07e9db7c49fe03bcf1838d779e555746a74ff68fdcbcdcf4",
    },
    "C139797": {
        "official_manufacturer_url": "https://tech.alpsalpine.com/e/products/detail/SKRPACE010/",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C139797.html",
        "lcsc_html_sha256": "076e68be6ba84e6dd9406d498e94dacb1f159b452949f3145cf202243a54713b",
    },
    "C2913198": {
        "official_manufacturer_url": "https://www.espressif.com/en/products/modules/esp32-s3-wroom-1",
        "official_lcsc_url": "https://www.lcsc.com/product-detail/C2913198.html",
        "lcsc_html_sha256": "513c845fdb44ef34c6948a39a2da52d688369978be002e2ff3a2faea5b88b50f",
    },
    "MOLEX-MICROFIT-PS-43045": {
        "official_manufacturer_url": (
            "https://www.molex.com/content/dam/molex/molex-dot-com/products/"
            "automated/en-us/productspecificationpdf/430/43045/PS-43045-001.pdf"
        ),
        "source_sha256": "b5f03865599a0576c43ab82828960d874a12bcc9564eda619750ff9e26a81204",
        "document_revision": "R",
        "document_date": "2025-11-14",
        "table_locator": "Section 4.3, sheet 8 of 24",
    },
    "ALPHA-3051": {
        "official_manufacturer_url": "https://www.alphawire.com/products/wire/hook-up-wire/premium/3051",
        "official_specification_url": (
            "https://www.alphawire.com/disteAPI/SpecPDF/"
            "DownloadProductSpecPdf?productPartNumber=3051"
        ),
        "source_sha256": "40c4a1d9f755eba448c5bf344d2bb71be75da4fac14b48edf8e4eadb1ad0ecc9",
    },
    "HT-151-00745": {
        "official_manufacturer_url": (
            "https://www.hellermanntyton.com/products/"
            "clips-clamps-and-plugs/pc5.0/151-00745"
        ),
        "source_sha256": "20a091f111ee6fe51fa33b26c80a22d5de722b6599cb939e143cf54f4495e607",
    },
    "TE-1932219-1": {
        "official_manufacturer_url": "https://www.te.com/en/product-1932219-1.html",
        "official_drawing_url": (
            "https://www.te.com/commerce/DocumentDelivery/DDEController?"
            "Action=srchrtrv&DocFormat=pdf&DocLang=English&DocNm=1932219"
            "&DocType=Customer+Drawing&PartCntxt=1932219-1"
        ),
        "source_sha256": "974c87a05725834b9754e2458adcf6b1f9462f5f8c9d567dbd79524aca694201",
    },
}
EXPECTED_SWITCH_SELECTION = {
    "manufacturer": "ALPSALPINE",
    "mpn": "SKRPACE010",
    "lcsc_code": "C139797",
    "packaging": "4000/full reel",
    "footprint": "Button_Switch_SMD:SW_SPST_SKRPACE010",
    "placement_status": "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF",
}
EXPECTED_MODULE_SELECTION = {
    "manufacturer": "Espressif Systems",
    "mpn": "ESP32-S3-WROOM-1-N8",
    "lcsc_code": "C2913198",
    "decision": "RETAIN_EXISTING",
}
MANDATORY_OPEN_GATES = {
    "OPEN_PHYSICAL_WIRE_AMPACITY",
    "live JLC BOM/CPL placement acceptance for every selected SMT row",
    "factory harness firm quote/orderable assembly number",
    "exact strain-relief and RJ45 pigtail production drawing",
    (
        "RJ45 single-open 2 A is UNSUPPORTED and requires physical "
        "qualification or measured-envelope redesign"
    ),
    "RJ45-side reversal physical test",
    "treadmill current envelope and complete path thermal/drop test",
    "deployment, production release, and TURNKEY_QUOTED remain blocked",
}


def _exact_fields(value: object, expected: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError(f"{name} schema fields are not exact")
    return value


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"{name} must be a finite nonnegative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EvidenceError(f"{name} must be a finite nonnegative number")
    return number


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceError(f"{name} must be a finite number")
    return number


def _official_url(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{name} official URL is absent")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_OFFICIAL_DOMAINS:
        raise EvidenceError(f"{name} official URL domain is invalid")
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise EvidenceError(f"{name} SHA-256 provenance is invalid")
    return value


def _audit_sha256(module: dict[str, Any]) -> str:
    payload = {field: module[field] for field in sorted(MODULE_AUDIT_FIELDS)}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_candidates(record: object) -> dict[str, Any]:
    candidate_matrix = _exact_fields(
        record,
        {
            "schema_version",
            "retrieved_at",
            "evidence_policy",
            "connector_candidates",
            "modules",
            "switches",
        },
        "candidate matrix",
    )
    connectors = candidate_matrix["connector_candidates"]
    if not isinstance(connectors, list) or not connectors:
        raise EvidenceError("candidate connector list is absent")
    identities: set[tuple[object, object, object]] = set()
    for candidate in connectors:
        expected = set(CONNECTOR_CANDIDATE_FIELDS)
        if isinstance(candidate, dict) and "selected_variants" in candidate:
            expected.add("selected_variants")
        candidate = _exact_fields(candidate, expected, "candidate connector")
        identity = _identity(candidate)
        if identity in identities:
            raise EvidenceError("candidate identity is duplicated")
        identities.add(identity)
        _official_url(candidate.get("official_product_url"), "candidate")
        _official_url(candidate.get("official_datasheet_url"), "candidate")
        _sha256(candidate.get("source_sha256"), "candidate")
        if candidate.get("packaging") != "tape_and_reel":
            raise EvidenceError("candidate packaging is not tape-and-reel")
        if candidate.get("placement_status") != "REQUIRES_LIVE_BOM_CPL_PROOF":
            raise EvidenceError("candidate placement status is invalid")
        if (
            not isinstance(candidate.get("rejection_constraints"), list)
            or not candidate["rejection_constraints"]
        ):
            raise EvidenceError("candidate rejection constraints are absent")
        _exact_fields(
            candidate.get("stock"),
            {"public_catalog_quantity", "status"},
            "candidate stock",
        )
        if (
            not isinstance(candidate.get("positions"), int)
            or candidate["positions"] <= 0
        ):
            raise EvidenceError("candidate positions are invalid")
        if not isinstance(candidate.get("locking"), bool):
            raise EvidenceError("candidate locking value is invalid")
        rating = candidate.get("rating")
        if not isinstance(rating, dict):
            raise EvidenceError("candidate rating schema is invalid")
        for field in (
            "voltage_v",
            "temperature_min_c",
            "temperature_max_c",
            "contact_resistance_mohm",
            "nominal_current_a",
        ):
            value = rating.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise EvidenceError(f"candidate rating {field} is not finite")
        if (
            rating["voltage_v"] < 0
            or rating["contact_resistance_mohm"] < 0
            or rating["nominal_current_a"] < 0
        ):
            raise EvidenceError("candidate rating values must be nonnegative")
        if "selected_variants" in candidate:
            variants = candidate["selected_variants"]
            if not isinstance(variants, list) or len(variants) != 2:
                raise EvidenceError("candidate selected variants are not exact")
            for variant in variants:
                _exact_fields(
                    variant,
                    {"manufacturer", "mpn", "lcsc_code", "positions"},
                    "candidate selected variant",
                )
        combinations = candidate.get("module_combinations")
        if not isinstance(combinations, list) or {
            item.get("module") for item in combinations if isinstance(item, dict)
        } != {"ESP32-S3-WROOM-1-N8", "ESP32-S3-MINI-1-N8"}:
            raise EvidenceError("candidate module combinations are not exact")
        for combination in combinations:
            combination = _exact_fields(
                combination, MODULE_COMBINATION_FIELDS, "candidate geometry"
            )
            dimensions = (
                "pcb_width_mm",
                "pcb_length_mm",
                "pcb_height_mm",
                "antenna_volume_mm3",
                "enclosure_width_mm",
                "enclosure_length_mm",
                "enclosure_height_mm",
                "minimum_bend_radius_mm",
                "service_clearance_mm",
                "installed_bounding_volume_mm3",
            )
            values = {
                field: _finite_nonnegative(
                    combination.get(field), f"candidate geometry {field}"
                )
                for field in dimensions
            }
            if any(values[field] <= 0 for field in dimensions):
                raise EvidenceError("candidate geometry dimensions must be positive")
            volume = (
                values["enclosure_width_mm"]
                * values["enclosure_length_mm"]
                * values["enclosure_height_mm"]
            )
            if not math.isclose(
                values["installed_bounding_volume_mm3"], volume, abs_tol=1e-9
            ):
                raise EvidenceError("candidate geometry volume is inconsistent")
            if not isinstance(combination.get("rejection_constraints"), list) or not (
                combination["rejection_constraints"]
            ):
                raise EvidenceError(
                    "candidate geometry rejection constraints are absent"
                )
    modules = candidate_matrix["modules"]
    if not isinstance(modules, list) or len(modules) != 2:
        raise EvidenceError("candidate module schema is not exact")
    for module in modules:
        _exact_fields(module, MODULE_FIELDS, "candidate module")
        _official_url(module.get("official_product_url"), "candidate module")
        _official_url(module.get("official_datasheet_url"), "candidate module")
        _sha256(module.get("source_sha256"), "candidate module")
    switches = candidate_matrix["switches"]
    if not isinstance(switches, list) or not switches:
        raise EvidenceError("candidate switch schema is absent")
    switch_identities = set()
    for switch in switches:
        switch = _exact_fields(switch, SWITCH_CANDIDATE_FIELDS, "candidate switch")
        identity = _identity(switch)
        if identity in switch_identities:
            raise EvidenceError("candidate switch identity is duplicated")
        switch_identities.add(identity)
        _official_url(switch.get("official_product_url"), "candidate switch")
        _official_url(switch.get("official_datasheet_url"), "candidate switch")
        _sha256(switch.get("source_sha256"), "candidate switch")
        if switch.get("packaging") != "tape_and_reel":
            raise EvidenceError("candidate switch packaging is invalid")
        if switch.get("module_combinations") != []:
            raise EvidenceError("candidate switch module combinations must be empty")
    return candidate_matrix


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


def validate_electrical_limits(record: object) -> dict[str, Any]:
    limits = _exact_fields(
        record,
        ELECTRICAL_LIMIT_FIELDS,
        "harness electrical limits",
    )
    if limits["schema_version"] != 1:
        raise EvidenceError("harness electrical limits schema version is invalid")
    if limits["basis"] != "CONSERVATIVE_PREDECESSOR":
        raise EvidenceError("harness electrical limits basis is not predecessor-bound")
    total = _finite_nonnegative(limits["total_current_a"], "total current")
    branches = limits["normal_unequal_branch_current_a"]
    if (
        total != 2.0
        or not isinstance(branches, list)
        or len(branches) != 2
        or [
            _finite_nonnegative(value, "normal unequal branch current")
            for value in branches
        ]
        != [1.35, 0.65]
        or not math.isclose(sum(branches), total, abs_tol=1e-12)
    ):
        raise EvidenceError("normal unequal predecessor current case is invalid")

    connectors = _exact_fields(
        limits["new_board_connectors"],
        {"console", "motor"},
        "new board connectors",
    )
    for name, expected_mpn in (
        ("console", "430450809"),
        ("motor", "430451010"),
    ):
        connector = _exact_fields(
            connectors[name],
            {
                "mpn",
                "contact_resistance_ohm",
                "doubled_contact_resistance_ohm",
            },
            f"{name} board connector",
        )
        nominal = _finite_nonnegative(
            connector["contact_resistance_ohm"],
            f"{name} connector contact resistance",
        )
        doubled = _finite_nonnegative(
            connector["doubled_contact_resistance_ohm"],
            f"{name} doubled contact resistance",
        )
        if (
            connector["mpn"] != expected_mpn
            or nominal != 0.01
            or doubled != 2 * nominal
        ):
            raise EvidenceError(
                f"{name} exact connector or doubled contact resistance is invalid"
            )

    wire = _exact_fields(
        limits["wire"],
        {
            "mpn",
            "awg",
            "dc_resistance_ohm_per_1000ft_at_20c",
            "harness_length_mm",
            "calculated_conductor_resistance_ohm",
        },
        "wire electrical limits",
    )
    if wire["mpn"] != "Alpha Wire 3051" or wire["awg"] != 22:
        raise EvidenceError("harness wire identity is invalid")
    dcr = _finite_nonnegative(
        wire["dc_resistance_ohm_per_1000ft_at_20c"],
        "wire DCR",
    )
    if dcr != 16.2:
        raise EvidenceError("Alpha Wire 3051 official DCR is invalid")
    lengths = _exact_fields(
        wire["harness_length_mm"],
        {"console", "motor"},
        "harness lengths",
    )
    resistances = _exact_fields(
        wire["calculated_conductor_resistance_ohm"],
        {"console", "motor"},
        "calculated conductor resistances",
    )
    for name, expected_length in (("console", 180), ("motor", 240)):
        length = _finite_nonnegative(lengths[name], f"{name} harness length")
        resistance = _finite_nonnegative(
            resistances[name],
            f"{name} conductor resistance",
        )
        expected = dcr * length / 304800.0
        if length != expected_length or not math.isclose(
            resistance,
            expected,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise EvidenceError(f"{name} wire resistance calculation is invalid")

    rj45 = _exact_fields(
        limits["rj45"],
        {
            "mpn",
            "termination_count",
            "published_max_current_per_contact_a",
            "contact_resistance_ohm",
            "single_open_2a_status",
        },
        "RJ45 electrical limits",
    )
    if (
        rj45["mpn"] != "TE 1932219-1"
        or rj45["termination_count"] != 4
        or rj45["published_max_current_per_contact_a"] != 1.5
        or rj45["contact_resistance_ohm"] is not None
        or rj45["single_open_2a_status"]
        != "UNSUPPORTED_OPEN_PHYSICAL_GATE"
        or max(branches) > rj45["published_max_current_per_contact_a"]
    ):
        raise EvidenceError("RJ45 normal or unsupported single-open limits are invalid")

    null_fields = (
        ("pcb", "copper_via_resistance_ohm"),
        ("source", "minimum_vin_v"),
        ("source", "impedance_ohm"),
        ("local_load", "current_a"),
        ("usb_ground", "return_current_a"),
    )
    expected_section_fields = {
        "pcb": {"copper_via_resistance_ohm"},
        "source": {"minimum_vin_v", "impedance_ohm"},
        "local_load": {"current_a"},
        "usb_ground": {"return_current_a"},
    }
    for section, fields in expected_section_fields.items():
        _exact_fields(
            limits[section],
            fields,
            f"{section} physical limits",
        )
    for section, field in null_fields:
        if limits[section][field] is not None:
            raise EvidenceError(
                f"{section}.{field} must remain null while physical evidence "
                "is UNSUPPORTED"
            )
    unsupported = limits["unsupported"]
    if (
        not isinstance(unsupported, list)
        or len(unsupported) != len(set(unsupported))
        or set(unsupported) != ELECTRICAL_UNSUPPORTED
    ):
        raise EvidenceError("harness electrical UNSUPPORTED claims are not exact")
    return limits


def _require_rating(name: str, element: object) -> None:
    if not isinstance(element, dict) or not isinstance(element.get("rating"), dict):
        raise EvidenceError(f"{name} must define a rating")
    rating = element["rating"]
    voltage = _finite_nonnegative(rating.get("voltage_v"), f"{name} voltage rating")
    temperature_min = _finite_number(
        rating.get("temperature_min_c"), f"{name} minimum temperature rating"
    )
    temperature_max = _finite_number(
        rating.get("temperature_max_c"), f"{name} maximum temperature rating"
    )
    if voltage < 24:
        raise EvidenceError(f"{name} voltage rating is below 24 V")
    if temperature_min > -20:
        raise EvidenceError(f"{name} does not cover -20 C")
    if temperature_max < 85:
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
        source.get("official_table"),
    ) != ("Molex", "PS-43045", EXPECTED_MICROFIT_OFFICIAL_TABLE):
        raise EvidenceError("official Micro-Fit derating evidence was altered")
    expected_provenance = {
        "official_manufacturer_url": (
            "https://www.molex.com/content/dam/molex/molex-dot-com/products/"
            "automated/en-us/productspecificationpdf/430/43045/PS-43045-001.pdf"
        ),
        "document_revision": "R",
        "document_date": "2025-11-14",
        "table_locator": "Section 4.3, sheet 8 of 24",
        "source_sha256": (
            "b5f03865599a0576c43ab82828960d874a12bcc9564eda619750ff9e26a81204"
        ),
    }
    if any(source.get(key) != value for key, value in expected_provenance.items()):
        raise EvidenceError("official Micro-Fit provenance was altered")
    _official_url(source["official_manufacturer_url"], "Micro-Fit evidence")
    if qualification.get("selected_circuit_count") != interface["header"].get(
        "positions"
    ):
        raise EvidenceError("current derating circuit count does not match header")
    if qualification.get("selected_circuit_count") != interface["housing"].get(
        "positions"
    ):
        raise EvidenceError("current derating circuit count does not match housing")
    if qualification.get("wire_awg") != interface["wire"].get("power_ground_awg"):
        raise EvidenceError("current derating wire gauge does not match selected wire")
    reference_circuits = qualification.get("conservative_reference_circuit_count")
    rows = [
        row
        for row in source["official_table"]["rows"]
        if row.get("circuit_count") == reference_circuits
    ]
    if len(rows) != 1:
        raise EvidenceError("conservative official circuit-count row is absent")
    base_current = float(rows[0]["current_per_contact_a"])
    official_rise = float(source["official_table"]["temperature_rise_limit_c"])
    connector_maximum = float(source["electrical_rating"]["temperature_max_c"])
    ambient = float(qualification.get("ambient_c", -999))
    allowed_rise = connector_maximum - ambient
    safety_factor = float(qualification.get("engineering_safety_factor", 0))
    if official_rise <= 0 or allowed_rise <= 0:
        raise EvidenceError("Micro-Fit temperature-rise derivation is not positive")
    if not 0 < safety_factor <= 0.75:
        raise EvidenceError("engineering safety factor must be within 0..0.75")
    derived = base_current * math.sqrt(allowed_rise / official_rise) * safety_factor
    expected_rise = official_rise * (2.0 / base_current) ** 2
    thermal_margin = allowed_rise - expected_rise
    if qualification.get("ambient_c") != 85:
        raise EvidenceError("engineering derivation must use +85 C ambient")
    exact_fields = {
        "wire_awg": 22,
        "conservative_reference_circuit_count": 12,
        "connector_maximum_c": connector_maximum,
        "official_temperature_rise_limit_c": official_rise,
        "base_current_per_contact_a": base_current,
        "allowed_temperature_rise_c": allowed_rise,
        "engineering_safety_factor": 0.75,
        "formula": MICROFIT_DERIVATION_FORMULA,
        "basis_class": "CONSERVATIVE_ENGINEERING_DERIVATION",
    }
    for field, expected in exact_fields.items():
        if qualification.get(field) != expected:
            raise EvidenceError(
                f"Micro-Fit engineering derivation field changed: {field}"
            )
    numeric_results = {
        "derived_current_per_contact_a": derived,
        "expected_temperature_rise_at_2a_c": expected_rise,
        "thermal_margin_at_2a_c": thermal_margin,
    }
    for field, expected in numeric_results.items():
        if not math.isclose(
            float(qualification.get(field, math.nan)),
            expected,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise EvidenceError(
                f"Micro-Fit engineering derivation result changed: {field}"
            )
    if thermal_margin <= 0:
        raise EvidenceError("Micro-Fit thermal margin is not positive")
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
        or common.get("key_rib_width_mm", 999) >= common.get("key_slot_width_mm", 0)
        or common.get("key_rib_depth_mm", 999) >= common.get("key_slot_depth_mm", 0)
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
        or abs(modeled.get("minimum_collision_margin_mm", -1) - collision_margin) > 1e-9
    ):
        raise EvidenceError("wrong-mating collision geometry is inconsistent")
    if (
        min(
            console.get("harness_length_mm", 0),
            motor.get("harness_length_mm", 0),
            console.get("minimum_service_slack_mm", 0),
            motor.get("minimum_service_slack_mm", 0),
        )
        <= 0
    ):
        raise EvidenceError("end-to-end harness reach dimensions are absent")
    if concept.get("physical_proof_status") != "OPEN_PENDING_DELIVERED_HARNESS":
        raise EvidenceError("physical wrong-mating proof must remain open")


def validate_module_audits(
    candidates: dict[str, Any], selection: dict[str, Any]
) -> None:
    if not isinstance(candidates, dict) or not isinstance(selection, dict):
        raise EvidenceError("module audit inputs must be objects")
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
        if not MODULE_AUDIT_FIELDS <= set(module):
            raise EvidenceError(f"{module.get('mpn')} module audit is incomplete")
        if set(module["strapping_pins"]) != {
            "GPIO0",
            "GPIO3",
            "GPIO45",
            "GPIO46",
        }:
            raise EvidenceError(
                f"{module['mpn']} module strapping-pin audit is incomplete"
            )
        area = float(module["package_size_mm"]["width"]) * float(
            module["package_size_mm"]["length"]
        )
        if abs(float(module["footprint_area_mm2"]) - area) > 1e-9:
            raise EvidenceError(f"{module['mpn']} footprint area is inconsistent")
        if set(module["used_signal_safe_boot_states"]) != set(wroom["used_gpio_audit"]):
            raise EvidenceError(
                f"{module['mpn']} module safe-boot signal audit is incomplete"
            )
        if _audit_sha256(module) != MODULE_AUDIT_SHA256[module["mpn"]]:
            raise EvidenceError(f"{module['mpn']} module audit values were altered")
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
    record = _exact_fields(record, SELECTION_FIELDS, "selection")
    if record.get("revision") != "C":
        raise EvidenceError("Rev C part selection is required")
    if record.get("status") != "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF":
        raise EvidenceError("selection must remain provisional")
    if record.get("placement_status") != "PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF":
        raise EvidenceError("public stock must not be promoted to placement proof")
    evidence = record.get("official_part_evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise EvidenceError("official part evidence is absent")
    if set(evidence) != set(EXPECTED_SELECTED_PROVENANCE):
        raise EvidenceError("official evidence provenance identities are not exact")
    for evidence_id, item in evidence.items():
        if not isinstance(item, dict):
            raise EvidenceError(f"official evidence {evidence_id} is malformed")
        for key, value in item.items():
            if key.startswith("official_") and key.endswith("_url"):
                if key == "official_lcsc_url":
                    parsed = urlparse(value) if isinstance(value, str) else None
                    if (
                        parsed is None
                        or parsed.scheme != "https"
                        or parsed.hostname != "www.lcsc.com"
                    ):
                        raise EvidenceError(
                            f"official evidence {evidence_id} LCSC URL is invalid"
                        )
                else:
                    _official_url(value, f"official evidence {evidence_id}")
            if key.endswith("_sha256"):
                _sha256(value, f"official evidence {evidence_id}")
        expected_provenance = EXPECTED_SELECTED_PROVENANCE[evidence_id]
        if any(item.get(key) != value for key, value in expected_provenance.items()):
            raise EvidenceError(
                f"official evidence provenance was altered: {evidence_id}"
            )
    candidates = validate_candidates(candidates)
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
    for interface_name, interface in interfaces.items():
        expected_fields = set(COMMON_INTERFACE_FIELDS)
        if interface_name == "motor":
            expected_fields |= {"unused_positions", "unused_position_treatment"}
        _exact_fields(interface, expected_fields, f"{interface_name} interface")
        for element_name, element_fields in INTERFACE_ELEMENT_FIELDS.items():
            _exact_fields(
                interface.get(element_name),
                element_fields,
                f"{interface_name}.{element_name}",
            )
        _exact_fields(
            interface.get("current_qualification"),
            CURRENT_QUALIFICATION_FIELDS,
            f"{interface_name}.current_qualification",
        )
        _exact_fields(
            interface.get("factory_assembly"),
            {
                "supplier",
                "orderable_part_number",
                "owner_crimping",
                "continuity_test",
            },
            f"{interface_name}.factory_assembly",
        )
    if console["header"]["mpn"] == motor["header"]["mpn"]:
        raise EvidenceError("console and motor headers must be physically incompatible")
    if (console["housing"]["positions"], motor["housing"]["positions"]) != (8, 10):
        raise EvidenceError(
            "console/motor physical keying must be 8 versus 10 positions"
        )
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
                f"{interface_name} Micro-Fit connector/terminal basis is below 2.0 A"
            )
        wire_qualification = _exact_fields(
            interface.get("wire_current_qualification"),
            {
                "status",
                "alpha_3051_dcr_ohm_per_1000ft_at_20c",
                "microfit_test_conductor",
                "claim",
            },
            f"{interface_name} wire current qualification",
        )
        if wire_qualification != {
            "status": "OPEN_PHYSICAL_WIRE_AMPACITY",
            "alpha_3051_dcr_ohm_per_1000ft_at_20c": 16.2,
            "microfit_test_conductor": "22 AWG tinned stranded copper",
            "claim": "NOT_A_COMPLETE_PATH_CURRENT_QUALIFICATION",
        }:
            raise EvidenceError(
                f"{interface_name} wire current qualification must remain open"
            )
        system_rating = evidence["MOLEX-MICROFIT-PS-43045"]["electrical_rating"]
        for element_name in ("header", "housing", "terminal"):
            if interface[element_name].get("rating") != system_rating:
                raise EvidenceError(
                    f"{interface_name}.{element_name} rating is not evidence-bound"
                )
        if interface["wire"].get("power_ground_awg", 999) > 22:
            raise EvidenceError(
                f"{interface_name} power/ground wire is smaller than 22 AWG"
            )
        wire_evidence = _matching_evidence(interface["wire"], evidence)
        if (
            interface["wire"].get("power_ground_awg") != wire_evidence.get("wire_awg")
            or interface["wire"].get("rating") != wire_evidence.get("electrical_rating")
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
    switches = record.get("switches")
    if not isinstance(switches, dict) or set(switches) != {"reset", "boot"}:
        raise EvidenceError(
            "switch schema must contain exact reset and boot selections"
        )
    for name, switch in switches.items():
        if switch != EXPECTED_SWITCH_SELECTION:
            raise EvidenceError(f"{name} switch selection schema or values changed")
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
    module = record.get("module")
    if module != EXPECTED_MODULE_SELECTION:
        raise EvidenceError("selected module schema or decision is not exact")
    open_gates = record.get("open_gates")
    if (
        not isinstance(open_gates, list)
        or not open_gates
        or any(not isinstance(gate, str) or not gate for gate in open_gates)
        or len(set(open_gates)) != len(open_gates)
        or not MANDATORY_OPEN_GATES <= set(open_gates)
    ):
        raise EvidenceError("mandatory open gate schema or entries are incomplete")
    _matching_evidence(module, evidence)
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
    # remaining connector/terminal contact is assigned the complete load.
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
            f"individual {open_net} Micro-Fit connector/terminal basis "
            f"cannot carry {assigned:.1f} A"
        )


def validate_unequal_case(case: object, *, per_contact_derated_rating_a: float) -> None:
    if not isinstance(case, dict):
        raise EvidenceError("unequal case schema is invalid")
    total = _finite_nonnegative(case.get("total_current_a"), "total current")
    rating = _finite_nonnegative(per_contact_derated_rating_a, "per-contact rating")
    if total != 2.0:
        raise EvidenceError("unequal case must model exactly 2.0 A total")
    branches = case.get("branch_current_a")
    if not isinstance(branches, list) or len(branches) != 2:
        raise EvidenceError("unequal case branches must contain two finite values")
    branch_values = [
        _finite_nonnegative(branch, f"branch {index} current")
        for index, branch in enumerate(branches)
    ]
    if branch_values[0] == branch_values[1] or abs(sum(branch_values) - 2.0) > 1e-9:
        raise EvidenceError("unequal case branches must be unequal and sum to 2.0 A")
    if rating < 2.0 or any(branch > rating for branch in branch_values):
        raise EvidenceError("individual contact rating must be at least 2.0 A")


def validate_rj45_normal_case(
    rj45: object, evidence: dict[str, Any] | None = None
) -> None:
    if not isinstance(rj45, dict):
        raise EvidenceError("RJ45 termination record is absent")
    total = _finite_nonnegative(
        rj45.get("normal_total_current_a"), "RJ45 normal total current"
    )
    branches = rj45.get("normal_unequal_branch_current_a")
    if not isinstance(branches, list) or len(branches) != 2:
        raise EvidenceError("RJ45 normal branches must contain two finite values")
    branch_values = [
        _finite_nonnegative(branch, f"RJ45 branch {index} current")
        for index, branch in enumerate(branches)
    ]
    if (
        total != 2.0
        or branch_values[0] == branch_values[1]
        or abs(sum(branch_values) - 2.0) > 1e-9
    ):
        raise EvidenceError("RJ45 normal case must be unequal and total 2.0 A")
    if not isinstance(evidence, dict):
        raise EvidenceError("RJ45 official evidence is absent")
    official_rating = evidence.get("electrical_rating", {})
    published = _finite_nonnegative(
        official_rating.get("published_max_current_per_contact_a"),
        "RJ45 published rating",
    )
    if rj45.get("published_max_current_per_contact_a") != published:
        raise EvidenceError("RJ45 selected current does not match official evidence")
    if rj45.get("rating") != {
        key: official_rating[key]
        for key in ("voltage_v", "temperature_min_c", "temperature_max_c")
    }:
        raise EvidenceError("RJ45 selected rating does not match official evidence")
    if any(branch > published for branch in branch_values):
        raise EvidenceError("RJ45 normal branch exceeds official per-contact rating")
    if official_rating.get("temperature_max_c", -999) < 85:
        raise EvidenceError("RJ45 official rating does not cover +85 C")
    if rj45.get("single_open_2a_status") != "UNSUPPORTED_OPEN_PHYSICAL_GATE":
        raise EvidenceError("RJ45 single-open 2 A must remain unsupported")


def validate_harness_csv(
    path: Path, interface_name: str, interface: dict[str, Any]
) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_FIELDS:
            raise EvidenceError(f"{path.name} CSV schema fields are not exact")
        rows = list(reader)
    colors = interface["wire"]["colors"]
    exact_mpns = interface["wire"]["exact_color_mpns"]
    expected_rows = []
    for pin in range(1, 9):
        color = colors[str(pin)]
        expected_rows.append(
            {
                "rj45_pin": str(pin),
                "board_position": str(pin),
                "net": RJ45_NET_BY_PIN[pin],
                "wire_mpn": f"Alpha-Wire-{exact_mpns[color].replace(' ', '-')}",
                "wire_awg": "22",
                "color": color,
                "label": f"{'C' if interface_name == 'console' else 'M'}-P{pin}",
                "board_header": interface["header"]["mpn"],
                "board_housing": interface["housing"]["mpn"],
                "terminal": interface["terminal"]["mpn"],
                "rj45_assembly": "TE-1932219-1-carrier-PENDING-FIRM-QUOTE",
                "strain_relief": "HellermannTyton-151-00745",
                "continuity_test": interface["factory_assembly"]["continuity_test"],
            }
        )
    if rows != expected_rows:
        raise EvidenceError(f"{path.name} CSV fabrication tuples are not exact")


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
        validate_electrical_limits(
            json.loads(
                (HERE / "electrical_limits.json").read_text(encoding="utf-8")
            )
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
                HERE / "console-harness.csv",
                "console",
                selection["interfaces"]["console"],
            )
            validate_harness_csv(
                HERE / "motor-harness.csv",
                "motor",
                selection["interfaces"]["motor"],
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
            if selection_path.exists() and action in {
                "production_release",
                "deployment",
                "turnkey_status",
            }:
                raise EvidenceError(f"provisional selection cannot release {action}")
            require_release(evidence, action, basis=args.basis)
    except (
        EvidenceError,
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"VALID harness requirements status={requirements['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
