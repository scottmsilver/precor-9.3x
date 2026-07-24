#!/usr/bin/env python3
"""Validate the three disjoint Rev C evidence classes and release gates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]

STATUSES = {
    "model": {"MODELED", "UNSUPPORTED"},
    "vendor": {
        "NOT_REVIEWED",
        "PARTIAL_VENDOR_REVIEW",
        "VENDOR_ACCEPTED",
    },
    "physical": {
        "NOT_MEASURED",
        "PARTIAL_PHYSICAL",
        "PHYSICALLY_VALIDATED",
    },
    "predecessor": {"OWNER_AUTHORIZED"},
}
FIELDS = {
    "model": {"evidence_class", "status", "assertions"},
    "vendor": {
        "evidence_class",
        "status",
        "observations",
        "turnkey_quote",
    },
    "physical": {
        "evidence_class",
        "status",
        "treadmill_current_envelope",
        "open_items",
    },
    "predecessor": {
        "evidence_class",
        "status",
        "basis",
        "owner_authorization_revision",
        "artifacts",
        "constraints",
        "allowed_actions",
    },
}
ENVELOPE_VALUE_FIELDS = {
    "source_voltage_minimum_volts",
    "source_voltage_maximum_volts",
    "source_impedance_ohms",
    "maximum_continuous_current_amps",
    "transient_peak_amps",
    "transient_duration_ms",
    "installed_ambient_celsius",
    "installed_airflow",
    "installed_conductor_bundling",
    "usb_ground_potential_volts",
    "usb_ground_connection_current_amps",
}
ENVELOPE_TEXT_FIELDS = {
    "installed_conductor_bundling",
}
ENVELOPE_NUMERIC_FIELDS = ENVELOPE_VALUE_FIELDS - ENVELOPE_TEXT_FIELDS
ENVELOPE_FIELDS = ENVELOPE_VALUE_FIELDS | {
    "status",
    "raw_records",
    "missing_fields",
}
RAW_RECORD_FIELDS = {
    "path",
    "sha256",
    "instrument",
    "fixture",
    "capture_started_at_utc",
    "capture_ended_at_utc",
    "bindings",
}
RAW_MEASUREMENT_FIELDS = (
    "timestamp_utc",
    "measurement",
    "unit",
    "value",
)
BINDING_FIELDS = {"field", "measurement", "unit", "aggregation"}
AGGREGATIONS = {"min", "max", "max_abs", "measurement"}
PHYSICAL_UNITS = {
    "source_voltage_minimum_volts": "V",
    "source_voltage_maximum_volts": "V",
    "source_impedance_ohms": "ohm",
    "maximum_continuous_current_amps": "A",
    "transient_peak_amps": "A",
    "transient_duration_ms": "ms",
    "installed_ambient_celsius": "degC",
    "installed_airflow": "m/s",
    "installed_conductor_bundling": "description",
    "usb_ground_potential_volts": "V",
    "usb_ground_connection_current_amps": "A",
}
OPEN_ITEM_FIELDS = {"field", "instrument", "fixture", "data_required"}
MODEL_ASSERTION_FIELDS = {
    "claim",
    "method",
    "artifact_path",
    "artifact_sha256",
}
VENDOR_OBSERVATION_FIELDS = {
    "claim",
    "source_url",
    "operator",
    "observed_at_utc",
    "artifact_path",
    "artifact_sha256",
    "supports",
}
QUOTE_COST_FIELDS = {
    "pcb_fabrication_usd",
    "pcba_usd",
    "harnesses_usd",
    "enclosures_usd",
    "shipping_tax_usd",
    "total_usd",
}
QUOTE_FIELDS = QUOTE_COST_FIELDS | {
    "status",
    "artifact_path",
    "artifact_sha256",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
RELEASE_ALIASES = {
    "connector_selection": "connector_selection",
    "fabrication_export": "fabrication_release",
    "fabrication_release": "fabrication_release",
    "turnkey_quoted": "turnkey_status",
    "turnkey_status": "turnkey_status",
    "layout": "layout",
    "verification_fabrication": "verification_fabrication",
    "no_purchase_quote": "no_purchase_quote",
    "production_release": "production_release",
    "deployment": "deployment",
    "physical_promotion": "physical_promotion",
}
PREDECESSOR_PATHS = {
    "hardware/PiZeroHat/README.md",
    "hardware/PiZeroHat/kicad/WIRING.md",
    "hardware/PiZeroHat/kicad/PiZeroHat.kicad_sch",
    "hardware/PiZeroHat/kicad/PiZeroHat.kicad_pcb",
    (
        "docs/superpowers/specs/"
        "2026-07-24-esp32tap-rev-c-turnkey-compact-design.md"
    ),
}
PREDECESSOR_SHA256 = {
    "hardware/PiZeroHat/README.md": (
        "2a27c38153ea1c30c4d428423cefc3eb0261f6927ced2253702c05341e4ec87f"
    ),
    "hardware/PiZeroHat/kicad/WIRING.md": (
        "7330fa8acadb0b213628712519662774bba5740f9152b6b73c076713b352b937"
    ),
    "hardware/PiZeroHat/kicad/PiZeroHat.kicad_sch": (
        "5f9215e9a8becf99ab6e9cd0827a73de582fe68fcb7c7bbf8e625728149ec7a9"
    ),
    "hardware/PiZeroHat/kicad/PiZeroHat.kicad_pcb": (
        "121264ead80bcea009798fed1b17e449ab4cd8a89abd14e9b5937779a7282709"
    ),
    (
        "docs/superpowers/specs/"
        "2026-07-24-esp32tap-rev-c-turnkey-compact-design.md"
    ): "1fef24b6aadec676cd131d895d118750fb8e4a0b35f234ced423d36e24b12b5b",
}
PREDECESSOR_ACTIONS = {
    "connector_selection",
    "layout",
    "verification_fabrication",
    "no_purchase_quote",
}
PREDECESSOR_CONSTRAINTS = {
    "total_continuous_current_amps": 2.0,
    "individual_power_contact_min_amps": 2.0,
    "individual_ground_contact_min_amps": 2.0,
    "parallel_sharing_credit": False,
    "power_ground_wire_awg": 22,
    "mating_system_min_voltage_volts": 24,
    "mating_system_min_ambient_celsius": -20,
    "mating_system_max_ambient_celsius": 85,
}
PREDECESSOR_ARTIFACT_FIELDS = {"path", "sha256"}
OWNER_AUTHORIZATION_REVISION = "e4f8ae6294d58cedf0572d123c3a8c88f64cc8f8"
CLASS_NAMES = {
    "model": "MODEL",
    "vendor": "VENDOR",
    "physical": "PHYSICAL",
    "predecessor": "CONSERVATIVE_PREDECESSOR",
}


class EvidenceError(ValueError):
    """Raised when an evidence record violates its class contract."""


def _exact_fields(label: str, value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing or unknown:
        raise EvidenceError(
            f"{label} fields differ: missing={sorted(missing)}, "
            f"unknown fields={sorted(unknown)}"
        )
    return value


def _verified_artifact(
    *,
    evidence_root: Path,
    relative_path: object,
    approved_directory: str,
    expected_sha256: object,
    label: str,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise EvidenceError(f"{label} path is blank")
    path_value = Path(relative_path)
    resolved_root = evidence_root.resolve()
    approved = (resolved_root / approved_directory).resolve()
    if not approved.is_relative_to(resolved_root):
        raise EvidenceError(
            f"approved {approved_directory} directory escapes evidence root"
        )
    candidate = (resolved_root / path_value).resolve()
    if path_value.is_absolute() or not candidate.is_relative_to(approved):
        raise EvidenceError(
            f"{label} is outside the approved {approved_directory} directory"
        )
    if not candidate.is_file():
        raise EvidenceError(f"{label} does not exist: {relative_path}")
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise EvidenceError(f"{label} SHA-256 mismatch")
    return candidate


def _utc_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceError(f"{label} must be an ISO-8601 UTC timestamp") from error
    return parsed


def _aggregate(
    *,
    aggregation: str,
    values: list[float | str],
    numeric: bool,
    label: str,
) -> float | str:
    if aggregation == "measurement":
        if len(values) != 1:
            raise EvidenceError(f"{label} measurement aggregation requires one row")
        return values[0]
    if not numeric:
        raise EvidenceError(f"{label} text data requires measurement aggregation")
    numeric_values = [float(value) for value in values]
    if aggregation == "min":
        return min(numeric_values)
    if aggregation == "max":
        return max(numeric_values)
    if aggregation == "max_abs":
        return max(abs(value) for value in numeric_values)
    raise EvidenceError(f"{label} has an invalid aggregation")


def _validate_raw_measurement(
    *,
    record: dict[str, Any],
    envelope: dict[str, Any],
    evidence_root: Path,
    index: int,
) -> set[str]:
    label = f"raw_records[{index}]"
    path = _verified_artifact(
        evidence_root=evidence_root,
        relative_path=record["path"],
        approved_directory="raw",
        expected_sha256=record["sha256"],
        label=label,
    )
    started = _utc_timestamp(
        record["capture_started_at_utc"],
        f"{label}.capture_started_at_utc",
    )
    ended = _utc_timestamp(
        record["capture_ended_at_utc"],
        f"{label}.capture_ended_at_utc",
    )
    if ended < started:
        raise EvidenceError(f"{label} capture end precedes capture start")

    bindings_value = record["bindings"]
    if (
        not isinstance(bindings_value, list)
        or not bindings_value
    ):
        raise EvidenceError(f"{label}.bindings must be a nonempty list")
    bindings: list[dict[str, Any]] = []
    for binding_index, value in enumerate(bindings_value):
        binding = _exact_fields(
            f"{label}.bindings[{binding_index}]",
            value,
            BINDING_FIELDS,
        )
        field = binding["field"]
        if field not in ENVELOPE_VALUE_FIELDS:
            raise EvidenceError(f"{label}.bindings[{binding_index}] field is unknown")
        if envelope[field] is None:
            raise EvidenceError(
                f"{label} binding references null physical datum: {field}"
            )
        if (
            not isinstance(binding["measurement"], str)
            or not binding["measurement"].strip()
        ):
            raise EvidenceError(
                f"{label}.bindings[{binding_index}] measurement is blank"
            )
        if binding["unit"] != PHYSICAL_UNITS[field]:
            raise EvidenceError(f"{label}.bindings[{binding_index}] has the wrong unit")
        if binding["aggregation"] not in AGGREGATIONS:
            raise EvidenceError(
                f"{label}.bindings[{binding_index}] aggregation is invalid"
            )
        bindings.append(binding)
    fields = [binding["field"] for binding in bindings]
    if len(set(fields)) != len(fields):
        raise EvidenceError(f"{label}.bindings repeat a physical field")

    try:
        with path.open(encoding="utf-8", newline="") as raw_file:
            reader = csv.DictReader(raw_file)
            if reader.fieldnames is None:
                raise EvidenceError(
                    f"{label} lacks exact raw measurement columns "
                    f"{list(RAW_MEASUREMENT_FIELDS)}"
                )
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise EvidenceError(f"{label} has duplicate CSV headers")
            if reader.fieldnames != list(RAW_MEASUREMENT_FIELDS):
                raise EvidenceError(
                    f"{label} lacks exact raw measurement columns "
                    f"{list(RAW_MEASUREMENT_FIELDS)}"
                )
            rows = list(reader)
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} is not UTF-8 measurement data") from error
    if not rows:
        raise EvidenceError(f"{label} contains no raw measurement rows")

    measurements = {binding["measurement"] for binding in bindings}
    rows_by_measurement: dict[str, list[float | str]] = {}
    for row_number, row in enumerate(rows, start=2):
        if set(row) != set(RAW_MEASUREMENT_FIELDS) or None in row:
            raise EvidenceError(f"{label} row {row_number} has extra CSV columns")
        if any(value is None for value in row.values()):
            raise EvidenceError(f"{label} row {row_number} has missing CSV columns")
        timestamp = _utc_timestamp(
            row["timestamp_utc"],
            f"{label} row {row_number} timestamp_utc",
        )
        if timestamp < started or timestamp > ended:
            raise EvidenceError(f"{label} row {row_number} is outside capture time")
        measurement = row["measurement"]
        if measurement not in measurements:
            raise EvidenceError(
                f"{label} row {row_number} is not bound to its measurement"
            )
        matching = [
            binding
            for binding in bindings
            if binding["measurement"] == measurement
        ]
        expected_units = {binding["unit"] for binding in matching}
        if row["unit"] not in expected_units or len(expected_units) != 1:
            raise EvidenceError(f"{label} row {row_number} has the wrong unit")
        numeric = all(
            binding["field"] in ENVELOPE_NUMERIC_FIELDS
            for binding in matching
        )
        value: float | str
        if numeric:
            try:
                value = float(row["value"])
            except ValueError as error:
                raise EvidenceError(
                    f"{label} row {row_number} value is not numeric"
                ) from error
            if not math.isfinite(value):
                raise EvidenceError(f"{label} row {row_number} value is not finite")
        else:
            if not row["value"].strip():
                raise EvidenceError(f"{label} row {row_number} value is blank")
            value = row["value"]
        rows_by_measurement.setdefault(measurement, []).append(value)

    for binding in bindings:
        field = binding["field"]
        values = rows_by_measurement.get(binding["measurement"], [])
        if not values:
            raise EvidenceError(f"{label} binding has no raw rows: {field}")
        summary = _aggregate(
            aggregation=binding["aggregation"],
            values=values,
            numeric=field in ENVELOPE_NUMERIC_FIELDS,
            label=f"{label} {field}",
        )
        expected = envelope[field]
        matches = (
            math.isclose(
                float(summary),
                float(expected),
                rel_tol=1e-9,
                abs_tol=1e-12,
            )
            if field in ENVELOPE_NUMERIC_FIELDS
            else summary == expected
        )
        if not matches:
            raise EvidenceError(
                f"{label} {field} aggregate does not match the envelope datum"
            )
    return set(fields)


def _validate_physical(
    record: dict[str, Any],
    evidence_root: Path,
) -> None:
    envelope = _exact_fields(
        "treadmill_current_envelope",
        record.get("treadmill_current_envelope"),
        ENVELOPE_FIELDS,
    )
    if envelope["status"] not in {"NOT_MEASURED", "MEASURED"}:
        raise EvidenceError("treadmill_current_envelope has an invalid status")
    for field in ENVELOPE_NUMERIC_FIELDS:
        value = envelope[field]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise EvidenceError(f"{field} must be a finite number or null")
    for field in ENVELOPE_TEXT_FIELDS:
        value = envelope[field]
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise EvidenceError(f"{field} must be nonblank text or null")
    if not isinstance(envelope["raw_records"], list):
        raise EvidenceError("raw_records must be a list")
    bound_fields: set[str] = set()
    for index, raw in enumerate(envelope["raw_records"]):
        bound = _exact_fields(f"raw_records[{index}]", raw, RAW_RECORD_FIELDS)
        if not isinstance(bound["sha256"], str) or not SHA256.fullmatch(
            bound["sha256"]
        ):
            raise EvidenceError(f"raw_records[{index}].sha256 is not bound")
        for field in RAW_RECORD_FIELDS - {"sha256"}:
            if field == "bindings":
                continue
            if not isinstance(bound[field], str) or not bound[field].strip():
                raise EvidenceError(f"raw_records[{index}].{field} is blank")
        bound_fields.update(
            _validate_raw_measurement(
                record=bound,
                envelope=envelope,
                evidence_root=evidence_root,
                index=index,
            )
        )
    missing_fields = envelope["missing_fields"]
    if (
        not isinstance(missing_fields, list)
        or any(field not in ENVELOPE_VALUE_FIELDS for field in missing_fields)
        or len(set(missing_fields)) != len(missing_fields)
    ):
        raise EvidenceError("missing_fields must name unique envelope fields")
    if not isinstance(record.get("open_items"), list):
        raise EvidenceError("open_items must be a list")
    open_fields: list[str] = []
    for index, item in enumerate(record["open_items"]):
        open_item = _exact_fields(f"open_items[{index}]", item, OPEN_ITEM_FIELDS)
        if open_item["field"] not in ENVELOPE_VALUE_FIELDS:
            raise EvidenceError(f"open_items[{index}].field is unknown")
        open_fields.append(open_item["field"])
        for field in OPEN_ITEM_FIELDS - {"field"}:
            if not isinstance(open_item[field], str) or not open_item[field].strip():
                raise EvidenceError(f"open_items[{index}].{field} is blank")
    if (
        len(set(open_fields)) != len(open_fields)
        or set(open_fields) != set(missing_fields)
    ):
        raise EvidenceError("open_items must exactly match missing_fields")
    if any(
        (field in missing_fields) != (envelope[field] is None)
        for field in ENVELOPE_VALUE_FIELDS
    ):
        raise EvidenceError("missing_fields must exactly identify null measurements")
    measured_fields = ENVELOPE_VALUE_FIELDS - set(missing_fields)
    if bound_fields != measured_fields:
        raise EvidenceError(
            "verified raw-record field bindings must cover every measured datum"
        )


def _validate_hash_bound_items(
    label: str,
    items: object,
    fields: set[str],
    *,
    evidence_root: Path,
    approved_directory: str,
) -> None:
    if not isinstance(items, list):
        raise EvidenceError(f"{label} must be a list")
    for index, item in enumerate(items):
        bound = _exact_fields(f"{label}[{index}]", item, fields)
        if not isinstance(bound["artifact_sha256"], str) or not SHA256.fullmatch(
            bound["artifact_sha256"]
        ):
            raise EvidenceError(f"{label}[{index}].artifact_sha256 is not bound")
        for field in fields - {"artifact_sha256", "supports"}:
            if not isinstance(bound[field], str) or not bound[field].strip():
                raise EvidenceError(f"{label}[{index}].{field} is blank")
        if "supports" in fields:
            supports = bound["supports"]
            if (
                not isinstance(supports, list)
                or not supports
                or any(
                    action not in {"fabrication_release", "turnkey_status"}
                    for action in supports
                )
                or len(set(supports)) != len(supports)
            ):
                raise EvidenceError(f"{label}[{index}].supports is invalid")
        _verified_artifact(
            evidence_root=evidence_root,
            relative_path=bound["artifact_path"],
            approved_directory=approved_directory,
            expected_sha256=bound["artifact_sha256"],
            label=f"{label}[{index}] artifact",
        )


def _validate_model(record: dict[str, Any], evidence_root: Path) -> None:
    _validate_hash_bound_items(
        "assertions",
        record.get("assertions"),
        MODEL_ASSERTION_FIELDS,
        evidence_root=evidence_root,
        approved_directory="model",
    )


def _validate_vendor(record: dict[str, Any], evidence_root: Path) -> None:
    _validate_hash_bound_items(
        "observations",
        record.get("observations"),
        VENDOR_OBSERVATION_FIELDS,
        evidence_root=evidence_root,
        approved_directory="vendor",
    )
    if record["status"] == "VENDOR_ACCEPTED" and not record["observations"]:
        raise EvidenceError("VENDOR_ACCEPTED requires a verified observation")
    if record["status"] == "VENDOR_ACCEPTED" and not any(
        "fabrication_release" in observation["supports"]
        for observation in record["observations"]
    ):
        raise EvidenceError(
            "VENDOR_ACCEPTED requires a fabrication_release observation"
        )
    quote = _exact_fields(
        "turnkey_quote",
        record.get("turnkey_quote"),
        QUOTE_FIELDS,
    )
    if quote["status"] not in {"NOT_QUOTED", "PARTIAL_QUOTE", "TURNKEY_QUOTED"}:
        raise EvidenceError("turnkey_quote has an invalid status")
    for field in QUOTE_COST_FIELDS:
        value = quote[field]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise EvidenceError(f"turnkey_quote.{field} must be nonnegative or null")
    if quote["status"] == "TURNKEY_QUOTED":
        prerequisites = (
            record["status"] == "VENDOR_ACCEPTED"
            and all(quote[field] is not None for field in QUOTE_COST_FIELDS)
            and isinstance(quote["artifact_path"], str)
            and bool(quote["artifact_path"].strip())
            and isinstance(quote["artifact_sha256"], str)
            and bool(SHA256.fullmatch(quote["artifact_sha256"]))
        )
        if not prerequisites:
            raise EvidenceError("TURNKEY_QUOTED prerequisites are incomplete")
        _verified_artifact(
            evidence_root=evidence_root,
            relative_path=quote["artifact_path"],
            approved_directory="vendor",
            expected_sha256=quote["artifact_sha256"],
            label="TURNKEY_QUOTED artifact",
        )


def _validate_predecessor(
    record: dict[str, Any],
    repository_root: Path,
) -> None:
    if record["basis"] != "conservative-predecessor":
        raise EvidenceError("predecessor basis is invalid")
    if record["owner_authorization_revision"] != OWNER_AUTHORIZATION_REVISION:
        raise EvidenceError("owner authorization revision is invalid")
    constraints = record["constraints"]
    if (
        not isinstance(constraints, dict)
        or constraints != PREDECESSOR_CONSTRAINTS
        or type(constraints["parallel_sharing_credit"]) is not bool
        or type(constraints["power_ground_wire_awg"]) is not int
    ):
        raise EvidenceError("predecessor constraints differ from the fixed basis")
    actions = record["allowed_actions"]
    if (
        not isinstance(actions, list)
        or set(actions) != PREDECESSOR_ACTIONS
        or len(actions) != len(PREDECESSOR_ACTIONS)
    ):
        raise EvidenceError("predecessor allowed_actions differ from the fixed matrix")
    artifacts = record["artifacts"]
    if not isinstance(artifacts, list):
        raise EvidenceError("predecessor artifacts must be a list")
    by_path: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(artifacts):
        artifact = _exact_fields(
            f"predecessor artifacts[{index}]",
            value,
            PREDECESSOR_ARTIFACT_FIELDS,
        )
        path = artifact["path"]
        if not isinstance(path, str) or path in by_path:
            raise EvidenceError("predecessor artifact path is blank or repeated")
        if (
            not isinstance(artifact["sha256"], str)
            or not SHA256.fullmatch(artifact["sha256"])
        ):
            raise EvidenceError("predecessor artifact sha256 is invalid")
        by_path[path] = artifact
    if set(by_path) != PREDECESSOR_PATHS:
        raise EvidenceError("predecessor artifact paths differ from the exact set")
    if any(
        artifact["sha256"] != PREDECESSOR_SHA256[path]
        for path, artifact in by_path.items()
    ):
        raise EvidenceError("predecessor artifact hash differs from the approved set")

    resolved_root = repository_root.resolve()
    for path, artifact in by_path.items():
        candidate = (resolved_root / path).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise EvidenceError("predecessor artifact escapes repository root")
        if not candidate.is_file():
            raise EvidenceError(f"predecessor artifact does not exist: {path}")
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != artifact["sha256"]:
            raise EvidenceError(f"predecessor artifact SHA-256 mismatch: {path}")


def validate_record(
    kind: str,
    record: object,
    *,
    evidence_root: Path | str = HERE,
    repository_root: Path | str = REPOSITORY_ROOT,
) -> dict[str, Any]:
    if kind not in STATUSES:
        raise EvidenceError(f"unknown evidence class {kind!r}")
    if not isinstance(record, dict):
        raise EvidenceError(f"{kind} evidence must be an object")
    if record.get("evidence_class") != CLASS_NAMES[kind]:
        raise EvidenceError(f"{kind} evidence has the wrong evidence_class")
    if record.get("status") not in STATUSES[kind]:
        raise EvidenceError(f"{kind} evidence has an invalid status")
    _exact_fields(f"{kind} evidence", record, FIELDS[kind])
    if kind == "model":
        _validate_model(record, Path(evidence_root))
    elif kind == "vendor":
        _validate_vendor(record, Path(evidence_root))
    elif kind == "physical":
        _validate_physical(record, Path(evidence_root))
    else:
        _validate_predecessor(record, Path(repository_root))
    return record


def load_all(
    directory: Path | str = HERE,
    *,
    repository_root: Path | str = REPOSITORY_ROOT,
) -> dict[str, dict[str, Any]]:
    root = Path(directory)
    return {
        kind: validate_record(
            kind,
            json.loads((root / f"{kind}.json").read_text(encoding="utf-8")),
            evidence_root=root,
            repository_root=repository_root,
        )
        for kind in STATUSES
    }


def _release_action(action: str) -> str:
    normalized = action.lower().replace("-", "_")
    if normalized not in RELEASE_ALIASES:
        raise EvidenceError(f"unknown release action {action!r}")
    return RELEASE_ALIASES[normalized]


def _physical_ready(evidence: dict[str, dict[str, Any]]) -> bool:
    physical = evidence["physical"]
    envelope = physical["treadmill_current_envelope"]
    return (
        physical["status"] == "PHYSICALLY_VALIDATED"
        and envelope["status"] == "MEASURED"
        and not envelope["missing_fields"]
        and not physical["open_items"]
        and all(envelope[field] is not None for field in ENVELOPE_VALUE_FIELDS)
        and bool(envelope["raw_records"])
    )


def release_allowed(
    evidence: dict[str, dict[str, Any]],
    action: str,
    *,
    evidence_root: Path | str = HERE,
    repository_root: Path | str = REPOSITORY_ROOT,
    basis: str | None = None,
) -> bool:
    for kind in STATUSES:
        validate_record(
            kind,
            evidence.get(kind),
            evidence_root=evidence_root,
            repository_root=repository_root,
        )
    required = _release_action(action)
    if basis is not None:
        if basis != "conservative-predecessor":
            raise EvidenceError(f"unknown release basis {basis!r}")
        physical = evidence["physical"]
        envelope = physical["treadmill_current_envelope"]
        predecessor_ready = (
            physical["status"] == "NOT_MEASURED"
            and envelope["status"] == "NOT_MEASURED"
            and set(envelope["missing_fields"]) == ENVELOPE_VALUE_FIELDS
            and not envelope["raw_records"]
            and evidence["predecessor"]["status"] == "OWNER_AUTHORIZED"
        )
        return predecessor_ready and required in PREDECESSOR_ACTIONS

    if not _physical_ready(evidence):
        return False

    if required in {"connector_selection", "layout"}:
        return True
    if required in {
        "verification_fabrication",
        "no_purchase_quote",
        "physical_promotion",
        "production_release",
        "deployment",
    }:
        return False
    vendor = evidence["vendor"]
    if vendor["status"] != "VENDOR_ACCEPTED":
        return False
    if required == "fabrication_release":
        return True
    return vendor["turnkey_quote"]["status"] == "TURNKEY_QUOTED"


def release_denial_reason(
    evidence: dict[str, dict[str, Any]],
    action: str,
    *,
    evidence_root: Path | str = HERE,
    repository_root: Path | str = REPOSITORY_ROOT,
    basis: str | None = None,
) -> str:
    required = _release_action(action)
    physical = evidence["physical"]
    envelope = physical["treadmill_current_envelope"]
    if release_allowed(
        evidence,
        action,
        evidence_root=evidence_root,
        repository_root=repository_root,
        basis=basis,
    ):
        return ""
    if basis == "conservative-predecessor":
        return (
            f"{action} blocked: conservative-predecessor permits only "
            f"{sorted(PREDECESSOR_ACTIONS)} and requires physical=NOT_MEASURED"
        )
    if not _physical_ready(evidence):
        missing = ", ".join(envelope["missing_fields"]) or "downstream prerequisites"
        return (
            f"{action} blocked: physical={physical['status']}, "
            f"treadmill_current_envelope={envelope['status']}; missing={missing}"
        )
    vendor = evidence["vendor"]
    if required in {
        "fabrication_release",
        "turnkey_status",
    } and (
        vendor["status"] != "VENDOR_ACCEPTED"
    ):
        return (
            f"{action} blocked: requires vendor.status=VENDOR_ACCEPTED; "
            f"actual={vendor['status']}"
        )
    quote_status = vendor["turnkey_quote"]["status"]
    if required == "turnkey_status" and quote_status != "TURNKEY_QUOTED":
        return (
            f"{action} blocked: requires "
            f"turnkey_quote.status=TURNKEY_QUOTED; actual={quote_status}"
        )
    return f"{action} blocked: unmet release prerequisite"


def require_release(
    evidence: dict[str, dict[str, Any]],
    action: str,
    *,
    evidence_root: Path | str = HERE,
    repository_root: Path | str = REPOSITORY_ROOT,
    basis: str | None = None,
) -> None:
    reason = release_denial_reason(
        evidence,
        action,
        evidence_root=evidence_root,
        repository_root=repository_root,
        basis=basis,
    )
    if reason:
        raise EvidenceError(reason)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require",
        choices=(
            "connector-selection",
            "fabrication-release",
            "layout",
            "verification-fabrication",
            "no-purchase-quote",
            "production-release",
            "deployment",
            "physical-promotion",
            "turnkey-status",
        ),
    )
    parser.add_argument(
        "--basis",
        choices=("conservative-predecessor",),
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        evidence = load_all()
        if args.require:
            require_release(evidence, args.require, basis=args.basis)
    except (EvidenceError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("VALID Rev C evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
