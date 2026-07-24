#!/usr/bin/env python3
"""Build or validate an Esp32Tap production safety-bundle manifest.

The bundle binds the exact application, bootloader, partition table, sdkconfig,
and hashed machine-readable safety contract.  It intentionally has no flashing
or deployment behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from safety_model import Controller


FIRMWARE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = FIRMWARE_DIR / "safety_manifest.schema.json"
GENERATED_BY = "hardware/Esp32Tap/firmware/build_safety_manifest.py"
CONTRACT_ARTIFACTS = (
    "safety_model",
    "safety_builder",
    "safety_schema",
    "firmware_plan",
)
BUNDLE_ARTIFACTS = (
    "application",
    "bootloader",
    "partition_table",
    "sdkconfig",
)
BROWNOUT_THRESHOLDS = {
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_1": 3.30,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_2": 3.19,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_3": 2.98,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_4": 2.84,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_5": 2.67,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_6": 2.56,
    "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_7": 2.44,
}
REQUIRED_SDKCONFIG = {
    "CONFIG_ESP_TASK_WDT_EN": "y",
    "CONFIG_ESP_TASK_WDT_INIT": "y",
    "CONFIG_ESP_TASK_WDT_TIMEOUT_S": "2",
    "CONFIG_ESP_TASK_WDT_PANIC": "y",
    "CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT": "y",
    "CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS": "0",
    "CONFIG_ESP_BROWNOUT_DET": "y",
}
FORBIDDEN_ENABLED_SDKCONFIG = (
    "CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT",
    "CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT",
    "CONFIG_ESP_SYSTEM_PANIC_GDBSTUB",
    "CONFIG_ESP_SYSTEM_GDBSTUB_RUNTIME",
    "CONFIG_ESP_DEBUG_OCDAWARE",
    "CONFIG_ESP_DEBUG_STUBS_ENABLE",
)


class ManifestError(RuntimeError):
    pass


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_nonempty(path: Path, label: str) -> bytes:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ManifestError(f"cannot read {label} {path}: {error}") from error
    if not payload:
        raise ManifestError(f"{label} is empty: {path}")
    return payload


def _artifact(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "filename": path.name,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _parse_sdkconfig(payload: bytes) -> dict[str, str]:
    text = payload.decode("utf-8", errors="strict")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ManifestError(
                f"sdkconfig line {line_number} is not KEY=VALUE"
            )
        key, value = line.split("=", 1)
        if key in values:
            raise ManifestError(f"duplicate sdkconfig key: {key}")
        values[key] = value
    return values


def _validate_power_evidence(
    *,
    measured_min_3v3: float,
    brownout_threshold: float,
    selector: str,
) -> None:
    if (
        not math.isfinite(measured_min_3v3)
        or not math.isfinite(brownout_threshold)
        or measured_min_3v3 <= 0
        or brownout_threshold <= 0
    ):
        raise ManifestError("brownout evidence must be positive and finite")
    if brownout_threshold >= measured_min_3v3:
        raise ManifestError(
            "brownout threshold must be below measured minimum +3V3"
        )
    try:
        selected_voltage = BROWNOUT_THRESHOLDS[selector]
    except KeyError as error:
        raise ManifestError(
            f"unsupported brownout selector: {selector}"
        ) from error
    if not math.isclose(
        selected_voltage,
        brownout_threshold,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ManifestError(
            f"{selector} is approximately {selected_voltage:.2f} V, "
            f"not {brownout_threshold:.2f} V"
        )
    candidates = [
        voltage
        for voltage in BROWNOUT_THRESHOLDS.values()
        if voltage < measured_min_3v3
    ]
    if not candidates:
        raise ManifestError(
            "measured minimum +3V3 is below every supported threshold"
        )
    highest_safe = max(candidates)
    if not math.isclose(
        selected_voltage,
        highest_safe,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ManifestError(
            f"{selector} is not the highest supported threshold below "
            f"{measured_min_3v3:.3f} V; expected {highest_safe:.2f} V"
        )


def _validate_sdkconfig(
    payload: bytes,
    *,
    measured_min_3v3: float,
    brownout_threshold: float,
) -> str:
    values = _parse_sdkconfig(payload)
    for key, expected in REQUIRED_SDKCONFIG.items():
        if values.get(key) != expected:
            raise ManifestError(
                f"unsafe sdkconfig: require {key}={expected}"
            )
    for key in FORBIDDEN_ENABLED_SDKCONFIG:
        if values.get(key) == "y":
            raise ManifestError(f"unsafe sdkconfig: {key}=y is forbidden")

    enabled_selectors = [
        key for key in BROWNOUT_THRESHOLDS if values.get(key) == "y"
    ]
    if len(enabled_selectors) != 1:
        raise ManifestError(
            "sdkconfig must enable exactly one supported brownout selector"
        )
    selector = enabled_selectors[0]
    selector_number = selector.rsplit("_", 1)[1]
    if values.get("CONFIG_ESP_BROWNOUT_DET_LVL") != selector_number:
        raise ManifestError(
            "unsafe sdkconfig: CONFIG_ESP_BROWNOUT_DET_LVL must match "
            f"{selector}"
        )
    _validate_power_evidence(
        measured_min_3v3=measured_min_3v3,
        brownout_threshold=brownout_threshold,
        selector=selector,
    )
    return selector


def _contract_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts = manifest["artifacts"]
    return {
        "schema_version": manifest["schema_version"],
        "generated_by": manifest["generated_by"],
        "safety_contract": manifest["safety_contract"],
        "power_evidence": manifest["power_evidence"],
        "contract_artifacts": {
            label: artifacts[label] for label in CONTRACT_ARTIFACTS
        },
    }


def _bundle_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts = manifest["artifacts"]
    return {
        **{label: artifacts[label] for label in BUNDLE_ARTIFACTS},
        "safety_manifest": manifest["contract_manifest_sha256"],
    }


def _load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(
            SCHEMA_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_json_no_duplicates,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot load safety schema: {error}") from error
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def validate_manifest(manifest: dict[str, Any]) -> None:
    try:
        jsonschema.validate(
            instance=manifest,
            schema=_load_schema(),
            cls=jsonschema.Draft202012Validator,
        )
    except jsonschema.ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ManifestError(f"schema validation failed at {path}: {error.message}") from error
    power = manifest["power_evidence"]
    _validate_power_evidence(
        measured_min_3v3=power["measured_min_3v3_volts"],
        brownout_threshold=power["brownout_threshold_volts"],
        selector=power["brownout_selector"],
    )
    expected_contract = _digest(_contract_payload(manifest))
    if manifest["contract_manifest_sha256"] != expected_contract:
        raise ManifestError("contract_manifest_sha256 does not match content")
    expected_bundle = _digest(_bundle_payload(manifest))
    if manifest["bundle_sha256"] != expected_bundle:
        raise ManifestError("bundle_sha256 does not match content")


def build_manifest(
    *,
    application: Path,
    bootloader: Path,
    partition_table: Path,
    sdkconfig: Path,
    measured_min_3v3: float,
    brownout_threshold: float,
) -> dict[str, Any]:
    paths = {
        "application": application,
        "bootloader": bootloader,
        "partition_table": partition_table,
        "sdkconfig": sdkconfig,
        "safety_model": FIRMWARE_DIR / "safety_model.py",
        "safety_builder": FIRMWARE_DIR / "build_safety_manifest.py",
        "safety_schema": SCHEMA_PATH,
        "firmware_plan": FIRMWARE_DIR / "PLAN.md",
    }
    payloads = {
        label: _read_nonempty(path, label)
        for label, path in paths.items()
    }
    selector = _validate_sdkconfig(
        payloads["sdkconfig"],
        measured_min_3v3=measured_min_3v3,
        brownout_threshold=brownout_threshold,
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": GENERATED_BY,
        "safety_contract": {
            "manual_lease_seconds": Controller.MANUAL_LEASE_SECONDS,
            "console_fresh_seconds": Controller.CONSOLE_FRESH_SECONDS,
            "transfer_gap_seconds": (
                Controller.TRANSFER_GAP_DEADLINE_SECONDS
            ),
            "relay_feedback_seconds": (
                Controller.RELAY_FEEDBACK_DEADLINE_SECONDS
            ),
            "relay_feedback_stable_seconds": (
                Controller.RELAY_FEEDBACK_STABLE_SECONDS
            ),
            "watchdog_seconds": Controller.WDT_SECONDS,
            "tread_ok_to_nc_max_seconds": (
                Controller.TREAD_OK_TO_NC_MAX_SECONDS
            ),
            "software_to_nc_max_seconds": (
                Controller.SOFTWARE_TO_NC_MAX_SECONDS
            ),
            "watchdog_to_nc_max_seconds": (
                Controller.WDT_TO_NC_MAX_SECONDS
            ),
            "normal_transition_acceptance_cycles": (
                Controller.NORMAL_TRANSITION_ACCEPTANCE_CYCLES
            ),
        },
        "power_evidence": {
            "measured_min_3v3_volts": measured_min_3v3,
            "brownout_threshold_volts": brownout_threshold,
            "brownout_selector": selector,
        },
        "artifacts": {
            label: _artifact(path, payloads[label])
            for label, path in paths.items()
        },
    }
    manifest["contract_manifest_sha256"] = _digest(
        _contract_payload(manifest)
    )
    manifest["bundle_sha256"] = _digest(_bundle_payload(manifest))
    validate_manifest(manifest)
    return manifest


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_no_duplicates,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot load manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError("manifest root must be an object")
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _reject_output_alias(
    output: Path,
    explicit_inputs: Iterable[Path],
) -> None:
    output_target = output.expanduser().resolve(strict=False)
    implicit_inputs = (
        FIRMWARE_DIR / "safety_model.py",
        FIRMWARE_DIR / "build_safety_manifest.py",
        SCHEMA_PATH,
        FIRMWARE_DIR / "PLAN.md",
    )
    for input_path in (*explicit_inputs, *implicit_inputs):
        if input_path.expanduser().resolve(strict=False) == output_target:
            raise ManifestError(
                f"output aliases hashed input artifact: {input_path}"
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--application", type=Path)
    parser.add_argument("--bootloader", type=Path)
    parser.add_argument("--partition-table", type=Path)
    parser.add_argument("--sdkconfig", type=Path)
    parser.add_argument("--measured-min-3v3", type=float)
    parser.add_argument("--brownout-threshold", type=float)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.validate is not None:
            supplied = (
                args.application,
                args.bootloader,
                args.partition_table,
                args.sdkconfig,
                args.measured_min_3v3,
                args.brownout_threshold,
                args.output,
            )
            if any(value is not None for value in supplied):
                parser.error("--validate cannot be combined with build options")
            manifest = _load_manifest(args.validate)
            validate_manifest(manifest)
            print(
                f"VALID bundle_sha256={manifest['bundle_sha256']}"
            )
            return 0

        required = {
            "--application": args.application,
            "--bootloader": args.bootloader,
            "--partition-table": args.partition_table,
            "--sdkconfig": args.sdkconfig,
            "--measured-min-3v3": args.measured_min_3v3,
            "--brownout-threshold": args.brownout_threshold,
            "--output": args.output,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"missing build options: {', '.join(missing)}")
        _reject_output_alias(
            args.output,
            (
                args.application,
                args.bootloader,
                args.partition_table,
                args.sdkconfig,
            ),
        )
        manifest = build_manifest(
            application=args.application,
            bootloader=args.bootloader,
            partition_table=args.partition_table,
            sdkconfig=args.sdkconfig,
            measured_min_3v3=args.measured_min_3v3,
            brownout_threshold=args.brownout_threshold,
        )
        output = args.output
        _atomic_write(
            output,
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n",
        )
        print(f"WROTE {output} bundle_sha256={manifest['bundle_sha256']}")
        return 0
    except (
        ManifestError,
        UnicodeDecodeError,
        jsonschema.SchemaError,
    ) as error:
        print(f"build_safety_manifest: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
