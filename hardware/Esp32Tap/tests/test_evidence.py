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


@pytest.fixture()
def evidence(esp32tap_dir: Path) -> dict[str, object]:
    return load_all(esp32tap_dir / "evidence")


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
    raw = tmp_path / "treadmill-current-envelope.csv"
    raw.write_text("time_s,current_a,voltage_v\n0,0.4,8.0\n", encoding="utf-8")
    physical = measured["physical"]
    physical["status"] = "PHYSICALLY_VALIDATED"
    envelope = physical["treadmill_current_envelope"]
    envelope.update(
        {
            "status": "MEASURED",
            "source_voltage_minimum_volts": 7.7,
            "source_voltage_maximum_volts": 8.3,
            "source_impedance_ohms": 0.4,
            "maximum_continuous_current_amps": 0.5,
            "transient_peak_amps": 0.9,
            "transient_duration_ms": 12.0,
            "installed_ambient_celsius": 48.0,
            "installed_airflow": "0.1 m/s toward rear",
            "installed_conductor_bundling": "two 24 AWG pairs in loom",
            "usb_ground_potential_volts": 0.02,
            "usb_ground_connection_current_amps": 0.001,
            "raw_records": [
                {
                    "path": str(raw),
                    "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "instrument": "calibrated scope asset S1",
                    "fixture": "installed treadmill fixture F1",
                    "captured_at_utc": "2026-07-24T12:00:00Z",
                }
            ],
            "missing_fields": [],
        }
    )
    physical["open_items"] = []

    assert release_allowed(measured, "connector_selection")


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
