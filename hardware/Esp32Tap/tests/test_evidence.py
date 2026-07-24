from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from hardware.Esp32Tap.evidence.schemas import (
    EvidenceError,
    load_all,
    release_allowed,
    validate_record,
)

PHYSICAL_VALUES = {
    "source_voltage_minimum_volts": (7.7, "V"),
    "source_voltage_maximum_volts": (8.3, "V"),
    "source_impedance_ohms": (0.4, "ohm"),
    "maximum_continuous_current_amps": (0.5, "A"),
    "transient_peak_amps": (0.9, "A"),
    "transient_duration_ms": (12.0, "ms"),
    "installed_ambient_celsius": (48.0, "degC"),
    "installed_airflow": (0.1, "m/s"),
    "installed_conductor_bundling": ("two 24 AWG pairs in loom", "description"),
    "usb_ground_potential_volts": (0.02, "V"),
    "usb_ground_connection_current_amps": (0.001, "A"),
}


@pytest.fixture()
def evidence(esp32tap_dir: Path) -> dict[str, object]:
    return load_all(esp32tap_dir / "evidence")


def _measured_physical_record(
    evidence: dict[str, object],
    evidence_root: Path,
    *,
    relative_path: str = "raw/treadmill-current-envelope.csv",
    payload: str | None = None,
    bound_fields: list[str] | None = None,
) -> dict[str, object]:
    measured = copy.deepcopy(evidence["physical"])
    measured["status"] = "PHYSICALLY_VALIDATED"
    envelope = measured["treadmill_current_envelope"]
    envelope.update(
        {
            "status": "MEASURED",
            **{field: value for field, (value, _) in PHYSICAL_VALUES.items()},
            "missing_fields": [],
        }
    )
    measured["open_items"] = []
    raw = evidence_root / relative_path
    if payload is None:
        rows = [
            "timestamp_utc,instrument_id,fixture,field,unit,value",
            *[
                (
                    "2026-07-24T12:00:00Z,scope-S1,installed-treadmill-F1,"
                    f"{field},{unit},{value}"
                )
                for field, (value, unit) in PHYSICAL_VALUES.items()
            ],
        ]
        payload = "\n".join(rows) + "\n"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(payload, encoding="utf-8")
    envelope["raw_records"] = [
        {
            "path": relative_path,
            "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "instrument": "scope-S1",
            "fixture": "installed-treadmill-F1",
            "captured_at_utc": "2026-07-24T12:00:00Z",
            "fields": bound_fields or list(PHYSICAL_VALUES),
        }
    ]
    return measured


def test_committed_evidence_classes_have_disjoint_status_namespaces(
    evidence: dict[str, object],
) -> None:
    assert evidence["model"]["status"] in {"MODELED", "UNSUPPORTED"}
    assert evidence["vendor"]["status"] in {
        "NOT_REVIEWED",
        "PARTIAL_VENDOR_REVIEW",
        "VENDOR_ACCEPTED",
    }
    assert evidence["physical"]["status"] in {
        "NOT_MEASURED",
        "PARTIAL_PHYSICAL",
        "PHYSICALLY_VALIDATED",
    }


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(EvidenceError, match="unknown fields"):
        validate_record(
            "model",
            {
                "evidence_class": "MODEL",
                "status": "MODELED",
                "operator_observed_browser_fact": "not model evidence",
            },
        )


def test_wrong_evidence_class_is_rejected() -> None:
    with pytest.raises(EvidenceError, match="wrong evidence_class"):
        validate_record(
            "physical",
            {
                "evidence_class": "MODEL",
                "status": "NOT_MEASURED",
                "treadmill_current_envelope": {},
                "open_items": [],
            },
        )


def test_unbound_physical_raw_record_is_rejected() -> None:
    with pytest.raises(EvidenceError, match="sha256"):
        validate_record(
            "physical",
            {
                "evidence_class": "PHYSICAL",
                "status": "PARTIAL_PHYSICAL",
                "treadmill_current_envelope": {
                    "status": "NOT_MEASURED",
                    "source_voltage_minimum_volts": None,
                    "source_voltage_maximum_volts": None,
                    "source_impedance_ohms": None,
                    "maximum_continuous_current_amps": None,
                    "transient_peak_amps": None,
                    "transient_duration_ms": None,
                    "installed_ambient_celsius": None,
                    "installed_airflow": None,
                    "installed_conductor_bundling": None,
                    "usb_ground_potential_volts": None,
                    "usb_ground_connection_current_amps": None,
                    "raw_records": [
                        {
                            "path": "bench/current.csv",
                            "instrument": "scope asset 123",
                            "fixture": "installed treadmill",
                            "captured_at_utc": "2026-07-24T12:00:00Z",
                        }
                    ],
                    "missing_fields": [],
                },
                "open_items": [],
            },
        )


def test_numeric_physical_measurements_reject_descriptive_text(
    evidence: dict[str, object],
) -> None:
    malformed = copy.deepcopy(evidence["physical"])
    malformed["treadmill_current_envelope"][
        "source_voltage_minimum_volts"
    ] = "about eight"

    with pytest.raises(EvidenceError, match="finite number"):
        validate_record("physical", malformed)


def test_every_missing_physical_field_requires_an_instrumented_open_item(
    evidence: dict[str, object],
) -> None:
    malformed = copy.deepcopy(evidence["physical"])
    malformed["open_items"].pop()

    with pytest.raises(EvidenceError, match="open_items must exactly match"):
        validate_record("physical", malformed)


def test_turnkey_quoted_without_vendor_and_cost_prerequisites_is_rejected() -> None:
    with pytest.raises(EvidenceError, match="TURNKEY_QUOTED prerequisites"):
        validate_record(
            "vendor",
            {
                "evidence_class": "VENDOR",
                "status": "NOT_REVIEWED",
                "observations": [],
                "turnkey_quote": {
                    "status": "TURNKEY_QUOTED",
                    "pcb_fabrication_usd": None,
                    "pcba_usd": None,
                    "harnesses_usd": None,
                    "enclosures_usd": None,
                    "shipping_tax_usd": None,
                    "total_usd": None,
                    "artifact_path": None,
                    "artifact_sha256": None,
                },
            },
        )


@pytest.mark.parametrize(
    ("artifact_path", "artifact_sha256", "message"),
    [
        ("vendor/missing-quote.json", "0" * 64, "does not exist"),
        ("vendor/quote.json", "f" * 64, "SHA-256 mismatch"),
    ],
)
def test_turnkey_quote_requires_matching_artifact_in_vendor_directory(
    evidence: dict[str, object],
    tmp_path: Path,
    artifact_path: str,
    artifact_sha256: str,
    message: str,
) -> None:
    evidence_root = tmp_path / "evidence"
    quote_path = evidence_root / "vendor" / "quote.json"
    quote_path.parent.mkdir(parents=True)
    quote_path.write_text('{"quote": "saved"}\n', encoding="utf-8")
    vendor = copy.deepcopy(evidence["vendor"])
    vendor["status"] = "VENDOR_ACCEPTED"
    vendor["turnkey_quote"] = {
        "status": "TURNKEY_QUOTED",
        "pcb_fabrication_usd": 10.0,
        "pcba_usd": 20.0,
        "harnesses_usd": 30.0,
        "enclosures_usd": 40.0,
        "shipping_tax_usd": 5.0,
        "total_usd": 105.0,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha256,
    }

    with pytest.raises(EvidenceError, match=message):
        validate_record("vendor", vendor, evidence_root=evidence_root)


def test_matching_turnkey_quote_artifact_is_accepted(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    quote_path = evidence_root / "vendor" / "quote.json"
    quote_path.parent.mkdir(parents=True)
    quote_path.write_text('{"quote": "saved"}\n', encoding="utf-8")
    vendor = copy.deepcopy(evidence["vendor"])
    vendor["status"] = "VENDOR_ACCEPTED"
    vendor["turnkey_quote"] = {
        "status": "TURNKEY_QUOTED",
        "pcb_fabrication_usd": 10.0,
        "pcba_usd": 20.0,
        "harnesses_usd": 30.0,
        "enclosures_usd": 40.0,
        "shipping_tax_usd": 5.0,
        "total_usd": 105.0,
        "artifact_path": "vendor/quote.json",
        "artifact_sha256": hashlib.sha256(quote_path.read_bytes()).hexdigest(),
    }

    assert validate_record(
        "vendor",
        vendor,
        evidence_root=evidence_root,
    ) == vendor


@pytest.mark.parametrize(
    "action",
    ["connector_selection", "fabrication_release", "turnkey_status"],
)
def test_not_measured_blocks_every_rev_c_release(
    evidence: dict[str, object],
    action: str,
) -> None:
    assert evidence["physical"]["treadmill_current_envelope"]["status"] == (
        "NOT_MEASURED"
    )
    assert not release_allowed(evidence, action)


def test_hash_bound_complete_measurement_allows_connector_selection(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    measured = copy.deepcopy(evidence)
    evidence_root = tmp_path / "evidence"
    measured["physical"] = _measured_physical_record(
        evidence,
        evidence_root,
    )

    assert release_allowed(
        measured,
        "connector_selection",
        evidence_root=evidence_root,
    )


def test_physical_binding_rejects_missing_raw_path(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    physical = _measured_physical_record(evidence, evidence_root)
    raw = evidence_root / physical["treadmill_current_envelope"]["raw_records"][0][
        "path"
    ]
    raw.unlink()

    with pytest.raises(EvidenceError, match="does not exist"):
        validate_record("physical", physical, evidence_root=evidence_root)


def test_physical_binding_rejects_non_raw_source_file(
    evidence: dict[str, object],
    esp32tap_dir: Path,
) -> None:
    physical = copy.deepcopy(evidence["physical"])
    physical["treadmill_current_envelope"]["raw_records"] = [
        {
            "path": "schemas.py",
            "sha256": hashlib.sha256(
                (esp32tap_dir / "evidence" / "schemas.py").read_bytes()
            ).hexdigest(),
            "instrument": "scope-S1",
            "fixture": "installed-treadmill-F1",
            "captured_at_utc": "2026-07-24T12:00:00Z",
            "fields": list(PHYSICAL_VALUES),
        }
    ]

    with pytest.raises(EvidenceError, match="approved raw directory"):
        validate_record(
            "physical",
            physical,
            evidence_root=esp32tap_dir / "evidence",
        )


def test_physical_binding_rejects_hash_only_content(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    physical = _measured_physical_record(
        evidence,
        evidence_root,
        payload="sha256\n" + "0" * 64 + "\n",
    )

    with pytest.raises(EvidenceError, match="measurement columns"):
        validate_record("physical", physical, evidence_root=evidence_root)


def test_physical_binding_rejects_mismatched_raw_hash(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    physical = _measured_physical_record(evidence, evidence_root)
    physical["treadmill_current_envelope"]["raw_records"][0]["sha256"] = "0" * 64

    with pytest.raises(EvidenceError, match="SHA-256 mismatch"):
        validate_record("physical", physical, evidence_root=evidence_root)


def test_current_voltage_csv_cannot_claim_unrelated_physical_fields(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    payload = "\n".join(
        [
            "timestamp_utc,instrument_id,fixture,field,unit,value",
            (
                "2026-07-24T12:00:00Z,scope-S1,installed-treadmill-F1,"
                "source_voltage_minimum_volts,V,7.7"
            ),
            (
                "2026-07-24T12:00:00Z,scope-S1,installed-treadmill-F1,"
                "maximum_continuous_current_amps,A,0.5"
            ),
            "",
        ]
    )
    physical = _measured_physical_record(
        evidence,
        evidence_root,
        payload=payload,
        bound_fields=list(PHYSICAL_VALUES),
    )

    with pytest.raises(EvidenceError, match="claimed field has no raw row"):
        validate_record("physical", physical, evidence_root=evidence_root)


def test_verified_raw_bindings_must_cover_every_measured_datum(
    evidence: dict[str, object],
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    bound_fields = [
        "source_voltage_minimum_volts",
        "maximum_continuous_current_amps",
    ]
    payload = "\n".join(
        [
            "timestamp_utc,instrument_id,fixture,field,unit,value",
            (
                "2026-07-24T12:00:00Z,scope-S1,installed-treadmill-F1,"
                "source_voltage_minimum_volts,V,7.7"
            ),
            (
                "2026-07-24T12:00:00Z,scope-S1,installed-treadmill-F1,"
                "maximum_continuous_current_amps,A,0.5"
            ),
            "",
        ]
    )
    physical = _measured_physical_record(
        evidence,
        evidence_root,
        payload=payload,
        bound_fields=bound_fields,
    )

    with pytest.raises(EvidenceError, match="cover every measured datum"):
        validate_record("physical", physical, evidence_root=evidence_root)


def test_schema_cli_reports_hold_reason(esp32tap_dir: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "evidence/schemas.py",
            "--require",
            "connector-selection",
        ],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "NOT_MEASURED" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        ["harness/validate_harnesses.py", "--release"],
        [
            "tools/export_fab.py",
            "--audit-only",
            "--require-rev-c-release",
        ],
    ],
)
def test_not_measured_blocks_both_release_entry_points(
    esp32tap_dir: Path,
    command: list[str],
) -> None:
    result = subprocess.run(
        [sys.executable, *command],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "NOT_MEASURED" in result.stderr


def test_rev_b_exporter_remains_importable_without_rev_c_evidence_copy(
    esp32tap_dir: Path,
    tmp_path: Path,
) -> None:
    tools = tmp_path / "Esp32Tap" / "tools"
    tools.mkdir(parents=True)
    shutil.copyfile(
        esp32tap_dir / "tools" / "export_fab.py",
        tools / "export_fab.py",
    )

    result = subprocess.run(
        [sys.executable, str(tools / "export_fab.py"), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
