#!/usr/bin/env python3
"""Audit Rev C harness requirements and optionally enforce their release gate."""

from __future__ import annotations

import argparse
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
