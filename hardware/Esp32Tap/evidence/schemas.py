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
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

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
    "captured_at_utc",
    "fields",
}
RAW_MEASUREMENT_FIELDS = {
    "timestamp_utc",
    "instrument_id",
    "fixture",
    "field",
    "unit",
    "value",
}
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
    approved = (evidence_root / approved_directory).resolve()
    candidate = (evidence_root / path_value).resolve()
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
    fields = record["fields"]
    if (
        not isinstance(fields, list)
        or not fields
        or any(field not in ENVELOPE_VALUE_FIELDS for field in fields)
        or len(set(fields)) != len(fields)
    ):
        raise EvidenceError(f"{label}.fields must name unique physical fields")
    try:
        with path.open(encoding="utf-8", newline="") as raw_file:
            reader = csv.DictReader(raw_file)
            if reader.fieldnames is None or set(reader.fieldnames) != (
                RAW_MEASUREMENT_FIELDS
            ):
                raise EvidenceError(
                    f"{label} lacks exact raw measurement columns "
                    f"{sorted(RAW_MEASUREMENT_FIELDS)}"
                )
            rows = list(reader)
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} is not UTF-8 measurement data") from error
    if not rows:
        raise EvidenceError(f"{label} contains no raw measurement rows")

    rows_by_field: dict[str, list[dict[str, str]]] = {}
    for row_number, row in enumerate(rows, start=2):
        if (
            row["timestamp_utc"] != record["captured_at_utc"]
            or row["instrument_id"] != record["instrument"]
            or row["fixture"] != record["fixture"]
        ):
            raise EvidenceError(
                f"{label} row {row_number} provenance does not match its binding"
            )
        field = row["field"]
        if field not in fields:
            raise EvidenceError(f"{label} row {row_number} is not bound to its field")
        rows_by_field.setdefault(field, []).append(row)

    for field in fields:
        field_rows = rows_by_field.get(field, [])
        if not field_rows:
            raise EvidenceError(f"{label} claimed field has no raw row: {field}")
        for row in field_rows:
            if row["unit"] != PHYSICAL_UNITS[field]:
                raise EvidenceError(f"{label} {field} has the wrong unit")
            if field in ENVELOPE_NUMERIC_FIELDS:
                try:
                    value = float(row["value"])
                except ValueError as error:
                    raise EvidenceError(
                        f"{label} {field} value is not numeric"
                    ) from error
                if (
                    not math.isfinite(value)
                    or not math.isclose(
                        value,
                        float(envelope[field]),
                        rel_tol=1e-9,
                        abs_tol=1e-12,
                    )
                ):
                    raise EvidenceError(
                        f"{label} {field} does not match the envelope datum"
                    )
            elif row["value"] != envelope[field]:
                raise EvidenceError(
                    f"{label} {field} does not match the envelope datum"
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
            if field == "fields":
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
) -> None:
    if not isinstance(items, list):
        raise EvidenceError(f"{label} must be a list")
    for index, item in enumerate(items):
        bound = _exact_fields(f"{label}[{index}]", item, fields)
        if not isinstance(bound["artifact_sha256"], str) or not SHA256.fullmatch(
            bound["artifact_sha256"]
        ):
            raise EvidenceError(f"{label}[{index}].artifact_sha256 is not bound")
        for field in fields - {"artifact_sha256"}:
            if not isinstance(bound[field], str) or not bound[field].strip():
                raise EvidenceError(f"{label}[{index}].{field} is blank")


def _validate_model(record: dict[str, Any]) -> None:
    _validate_hash_bound_items(
        "assertions",
        record.get("assertions"),
        MODEL_ASSERTION_FIELDS,
    )


def _validate_vendor(record: dict[str, Any], evidence_root: Path) -> None:
    _validate_hash_bound_items(
        "observations",
        record.get("observations"),
        VENDOR_OBSERVATION_FIELDS,
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


def validate_record(
    kind: str,
    record: object,
    *,
    evidence_root: Path | str = HERE,
) -> dict[str, Any]:
    if kind not in STATUSES:
        raise EvidenceError(f"unknown evidence class {kind!r}")
    if not isinstance(record, dict):
        raise EvidenceError(f"{kind} evidence must be an object")
    if record.get("evidence_class") != kind.upper():
        raise EvidenceError(f"{kind} evidence has the wrong evidence_class")
    if record.get("status") not in STATUSES[kind]:
        raise EvidenceError(f"{kind} evidence has an invalid status")
    _exact_fields(f"{kind} evidence", record, FIELDS[kind])
    if kind == "model":
        _validate_model(record)
    elif kind == "vendor":
        _validate_vendor(record, Path(evidence_root))
    else:
        _validate_physical(record, Path(evidence_root))
    return record


def load_all(directory: Path | str = HERE) -> dict[str, dict[str, Any]]:
    root = Path(directory)
    return {
        kind: validate_record(
            kind,
            json.loads((root / f"{kind}.json").read_text(encoding="utf-8")),
            evidence_root=root,
        )
        for kind in STATUSES
    }


def release_allowed(
    evidence: dict[str, dict[str, Any]],
    action: str,
    *,
    evidence_root: Path | str = HERE,
) -> bool:
    for kind in STATUSES:
        validate_record(kind, evidence.get(kind), evidence_root=evidence_root)
    normalized = action.lower().replace("-", "_")
    aliases = {
        "connector_selection": "connector_selection",
        "fabrication_export": "fabrication_release",
        "fabrication_release": "fabrication_release",
        "turnkey_quoted": "turnkey_status",
        "turnkey_status": "turnkey_status",
    }
    if normalized not in aliases:
        raise EvidenceError(f"unknown release action {action!r}")

    physical = evidence["physical"]
    envelope = physical["treadmill_current_envelope"]
    physical_ready = (
        physical["status"] == "PHYSICALLY_VALIDATED"
        and envelope["status"] == "MEASURED"
        and not envelope["missing_fields"]
        and not physical["open_items"]
        and all(envelope[field] is not None for field in ENVELOPE_VALUE_FIELDS)
        and bool(envelope["raw_records"])
    )
    if not physical_ready:
        return False

    required = aliases[normalized]
    if required == "connector_selection":
        return True
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
) -> str:
    physical = evidence["physical"]
    envelope = physical["treadmill_current_envelope"]
    if not release_allowed(evidence, action, evidence_root=evidence_root):
        missing = ", ".join(envelope["missing_fields"]) or "downstream prerequisites"
        return (
            f"{action} blocked: physical={physical['status']}, "
            f"treadmill_current_envelope={envelope['status']}; missing={missing}"
        )
    return ""


def require_release(
    evidence: dict[str, dict[str, Any]],
    action: str,
    *,
    evidence_root: Path | str = HERE,
) -> None:
    reason = release_denial_reason(
        evidence,
        action,
        evidence_root=evidence_root,
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
            "turnkey-status",
        ),
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        evidence = load_all()
        if args.require:
            require_release(evidence, args.require)
    except (EvidenceError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("VALID Rev C evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
