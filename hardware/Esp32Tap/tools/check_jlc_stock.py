#!/usr/bin/env python3
"""Fetch or validate a fail-closed JLC assembly-stock snapshot.

The authenticated JLC OpenAPI is not required.  Refresh reads only official,
anonymous ``jlcpcb.com/partdetail/C...`` pages, extracts the exact part identity,
library class, and public assembly stock, and atomically writes a sanitized
snapshot.  ``canPresaleNumber`` is retained as audit evidence but is not the
public PCBA inventory: Basic parts can report zero there while their official
page reports millions in stock.  No order, cart, quote, or payment endpoint
exists in this tool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOM = ROOT / "bom" / "BOM.csv"
EXPECTATIONS = ROOT / "bom" / "JLC-PART-EXPECTATIONS.json"
SNAPSHOT = ROOT / "bom" / "JLC-STOCK-SNAPSHOT.json"
BUILD_QUANTITY = 2
SOURCE_TEMPLATE = "https://jlcpcb.com/partdetail/{code}"
SOURCE_DESCRIPTION = "official anonymous JLCPCB part-detail pages"
STOCK_FIELD_DESCRIPTION = (
    "overseasStockCount (public JLC parts-library assembly stock); "
    "canPresaleNumber retained as non-gating pre-order evidence"
)
CATALOG_ACCESS = {
    "mode": "anonymous-read-only",
    "authenticated_api": "NOT_USED",
}
DEFAULT_MAX_AGE_HOURS = 24.0
FUTURE_TOLERANCE = timedelta(minutes=5)
BOM_FIELDS = [
    "Comment",
    "Designator",
    "Footprint",
    "LCSC Part #",
    "JLC class",
    "Qty",
    "Unit cost (USD)",
    "Ext cost (USD)",
    "Description",
]
LIBRARY_CLASSES = {
    "base": "Basic",
    "expand": "Extended",
}


class StockError(RuntimeError):
    """Catalog identity or availability is missing, ambiguous, or unsafe."""


def _balanced_object(source: str, opening: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise StockError("componentInfo object is truncated")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StockError(f"componentInfo has duplicate JSON key {key}")
        value[key] = item
    return value


def _string_field(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise StockError(
            f"componentInfo direct field {field} is missing/invalid"
        )
    return value


def _integer_field(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StockError(
            f"componentInfo direct field {field} is missing/invalid"
        )
    return value


def parse_catalog_page(source: str, expected_code: str) -> dict[str, Any]:
    """Parse one exact target record from Next.js page data."""
    decoded = source.replace('\\"', '"')
    marker = '"componentInfo":'
    candidates: list[dict[str, Any]] = []
    candidate_errors: list[StockError] = []
    cursor = 0
    while True:
        position = decoded.find(marker, cursor)
        if position < 0:
            break
        opening = decoded.find("{", position + len(marker))
        if opening < 0:
            raise StockError("componentInfo has no object")
        block = _balanced_object(decoded, opening)
        cursor = opening + len(block)
        try:
            record = json.loads(
                block,
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, StockError) as error:
            candidate_errors.append(
                StockError(f"componentInfo JSON is malformed: {error}")
            )
            continue
        if not isinstance(record, dict):
            candidate_errors.append(
                StockError("componentInfo must be a JSON object")
            )
            continue
        try:
            code = _string_field(record, "componentCode")
        except StockError:
            continue
        if code != expected_code:
            continue
        try:
            library_type = _string_field(record, "componentLibraryType")
            jlc_class = LIBRARY_CLASSES[library_type]
            candidates.append(
                {
                    "lcsc": code,
                    "model": _string_field(record, "componentModelEn"),
                    "package": _string_field(
                        record,
                        "componentSpecificationEn",
                    ),
                    "library_type": library_type,
                    "jlc_class": jlc_class,
                    "overseas_stock_count": _integer_field(
                        record,
                        "overseasStockCount",
                    ),
                    "can_presale_number": _integer_field(
                        record,
                        "canPresaleNumber",
                    ),
                }
            )
        except KeyError:
            candidate_errors.append(
                StockError(
                    f"{expected_code}: unsupported library type "
                    f"{library_type!r}"
                )
            )
        except StockError as error:
            candidate_errors.append(error)
    if not candidates:
        if candidate_errors:
            raise candidate_errors[-1]
        raise StockError(
            f"official catalog page did not yield exact identity {expected_code}"
        )
    unique = {
        json.dumps(candidate, sort_keys=True)
        for candidate in candidates
    }
    if len(unique) != 1:
        raise StockError(
            f"{expected_code}: ambiguous componentInfo records disagree"
        )
    return json.loads(next(iter(unique)))


def _load_design(root: Path) -> Any:
    path = root / "tools" / "design.py"
    spec = importlib.util.spec_from_file_location(
        "esp32tap_stock_design",
        path,
    )
    if spec is None or spec.loader is None:
        raise StockError(f"cannot load design source: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        module.validate()
    except Exception as error:
        raise StockError(f"design validation failed: {error}") from error
    return module


def build_requirements(
    root: Path = ROOT,
) -> dict[str, dict[str, object]]:
    design = _load_design(root)
    requirements: dict[str, dict[str, object]] = {}
    for reference, component in design.COMPONENTS.items():
        lcsc = component[3]
        raw_class = component[4]
        if reference in design.DNP or raw_class == "none":
            continue
        if not re.fullmatch(r"C\d+", lcsc):
            raise StockError(f"{reference}: invalid LCSC/JLC code {lcsc!r}")
        jlc_class = raw_class.removesuffix("-THT")
        requirement = requirements.setdefault(
            lcsc,
            {
                "references": [],
                "jlc_class": jlc_class,
                "required_qty": 0,
            },
        )
        if requirement["jlc_class"] != jlc_class:
            raise StockError(f"{lcsc}: design has conflicting JLC classes")
        requirement["references"].append(reference)
        requirement["required_qty"] += BUILD_QUANTITY
    for requirement in requirements.values():
        requirement["references"].sort()
    requirements = dict(sorted(requirements.items()))

    bom_path = root / "bom" / "BOM.csv"
    try:
        with bom_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != BOM_FIELDS:
                raise StockError(
                    "BOM headings differ from the exact assembly schema"
                )
            bom_rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise StockError(f"cannot parse BOM {bom_path}: {error}") from error

    bom_requirements: dict[str, dict[str, object]] = {}
    seen_references: set[str] = set()
    for row in bom_rows:
        if set(row) != set(BOM_FIELDS) or any(
            not isinstance(row[field], str) for field in BOM_FIELDS
        ):
            raise StockError("BOM row has missing/extra/malformed columns")
        references = [
            reference.strip()
            for reference in row["Designator"].split(",")
            if reference.strip()
        ]
        try:
            quantity = int(row["Qty"])
        except ValueError as error:
            raise StockError("BOM Qty must be an integer") from error
        if not references or quantity != len(references):
            raise StockError("BOM Qty does not match its designators")
        duplicates = seen_references.intersection(references)
        if duplicates:
            raise StockError(
                f"BOM repeats references: {sorted(duplicates)}"
            )
        seen_references.update(references)
        code = row["LCSC Part #"]
        if re.fullmatch(r"C\d+", code) is None:
            raise StockError(f"BOM has invalid LCSC/JLC code {code!r}")
        jlc_class = row["JLC class"].removesuffix("-THT")
        if jlc_class not in {"Basic", "Extended"}:
            raise StockError(f"BOM has unsupported JLC class {jlc_class!r}")
        requirement = bom_requirements.setdefault(
            code,
            {
                "references": [],
                "jlc_class": jlc_class,
                "required_qty": 0,
            },
        )
        if requirement["jlc_class"] != jlc_class:
            raise StockError(f"{code}: BOM has conflicting JLC classes")
        requirement["references"].extend(references)
        requirement["required_qty"] += quantity * BUILD_QUANTITY
    for requirement in bom_requirements.values():
        requirement["references"].sort()
    bom_requirements = dict(sorted(bom_requirements.items()))
    if bom_requirements != requirements:
        raise StockError(
            "BOM requirements differ from design requirements: "
            f"design={requirements}, BOM={bom_requirements}"
        )
    return requirements


def _load_expected_parts(path: Path) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StockError(f"cannot load part expectations {path}: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("parts"), dict)
    ):
        raise StockError("part expectations must contain schema_version 1/parts")
    parts = value["parts"]
    for code, record in parts.items():
        if (
            re.fullmatch(r"C\d+", code) is None
            or not isinstance(record, dict)
            or set(record) != {"model", "package"}
            or not all(
                isinstance(record[field], str) and record[field]
                for field in ("model", "package")
            )
        ):
            raise StockError(
                "part expectations contain an invalid code/model/package"
            )
    return parts


def validate_records(
    *,
    records: list[dict[str, Any]],
    requirements: dict[str, dict[str, object]],
    expected_parts: dict[str, dict[str, str]],
) -> None:
    by_code: dict[str, dict[str, Any]] = {}
    for record in records:
        code = record.get("lcsc")
        if not isinstance(code, str) or code in by_code:
            raise StockError(f"snapshot repeats or omits code {code!r}")
        by_code[code] = record
    if set(by_code) != set(requirements):
        raise StockError(
            "snapshot records differ from BOM requirements: "
            f"missing={sorted(set(requirements) - set(by_code))}, "
            f"extra={sorted(set(by_code) - set(requirements))}"
        )
    if set(expected_parts) != set(requirements):
        raise StockError(
            "expected-part records differ from BOM requirements: "
            f"missing={sorted(set(requirements) - set(expected_parts))}, "
            f"extra={sorted(set(expected_parts) - set(requirements))}"
        )

    for code, requirement in requirements.items():
        record = by_code[code]
        expectation = expected_parts[code]
        if record.get("model") != expectation["model"]:
            raise StockError(
                f"{code}: model differs; expected {expectation['model']!r}, "
                f"actual {record.get('model')!r}"
            )
        if record.get("package") != expectation["package"]:
            raise StockError(
                f"{code}: package differs; expected "
                f"{expectation['package']!r}, actual {record.get('package')!r}"
            )
        expected_class = requirement["jlc_class"]
        if record.get("jlc_class") != expected_class:
            raise StockError(
                f"{code}: class differs; expected {expected_class}, "
                f"actual {record.get('jlc_class')}"
            )
        expected_library_type = {
            "Basic": "base",
            "Extended": "expand",
        }[expected_class]
        if record.get("library_type") != expected_library_type:
            raise StockError(
                f"{code}: library type differs; expected "
                f"{expected_library_type}, actual={record.get('library_type')}"
            )
        if record.get("references") != requirement["references"]:
            raise StockError(
                f"{code}: references differ; "
                f"expected={requirement['references']}, "
                f"actual={record.get('references')}"
            )
        if record.get("required_qty") != requirement["required_qty"]:
            raise StockError(
                f"{code}: required quantity differs from two-board BOM"
            )
        status = record.get("status")
        if status != "IN_STOCK":
            raise StockError(f"{code}: stock status is {status or 'UNKNOWN'}")
        presale = record.get("can_presale_number")
        assembly_stock = record.get("overseas_stock_count")
        if (
            isinstance(presale, bool)
            or not isinstance(presale, int)
            or isinstance(assembly_stock, bool)
            or not isinstance(assembly_stock, int)
        ):
            raise StockError(f"{code}: stock quantities are UNKNOWN")
        if assembly_stock < requirement["required_qty"]:
            raise StockError(
                f"{code}: public assembly stock is insufficient "
                f"(required={requirement['required_qty']}, "
                f"assembly_stock={assembly_stock}, presale={presale})"
            )
        if record.get("source_url") != SOURCE_TEMPLATE.format(code=code):
            raise StockError(f"{code}: source URL is not the official exact page")


def _fetch(code: str) -> str:
    url = SOURCE_TEMPLATE.format(code=code)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "Esp32Tap-stock-audit/1 (read-only)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise StockError(f"{code}: catalog HTTP {response.status}")
            return response.read().decode("utf-8", errors="strict")
    except (
        OSError,
        UnicodeDecodeError,
        urllib.error.URLError,
    ) as error:
        raise StockError(f"{code}: catalog request failed: {error}") from error


def _bom_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise StockError(f"cannot hash BOM {path}: {error}") from error


def validate_snapshot_metadata(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> datetime:
    """Require exact official provenance and a recent canonical UTC timestamp."""
    if (
        isinstance(max_age_hours, bool)
        or not isinstance(max_age_hours, (int, float))
        or max_age_hours <= 0
    ):
        raise StockError("maximum snapshot age must be positive")
    if (
        snapshot.get("source") != SOURCE_DESCRIPTION
        or snapshot.get("source_template") != SOURCE_TEMPLATE
        or snapshot.get("stock_field") != STOCK_FIELD_DESCRIPTION
        or snapshot.get("catalog_access") != CATALOG_ACCESS
    ):
        raise StockError("stock snapshot provenance is not exact/official")
    raw_timestamp = snapshot.get("checked_at_utc")
    if not isinstance(raw_timestamp, str):
        raise StockError("stock snapshot timestamp is missing")
    try:
        checked_at = datetime.strptime(
            raw_timestamp,
            "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise StockError(
            "stock snapshot timestamp must be canonical UTC"
        ) from error
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise StockError("current time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    if checked_at > current + FUTURE_TOLERANCE:
        raise StockError("stock snapshot timestamp is implausibly in the future")
    if current - checked_at > timedelta(hours=float(max_age_hours)):
        raise StockError(
            f"stock snapshot is stale (maximum age {max_age_hours:g} hours)"
        )
    return checked_at


def refresh_snapshot(
    *,
    root: Path = ROOT,
    expectations_path: Path = EXPECTATIONS,
    snapshot_path: Path = SNAPSHOT,
) -> dict[str, Any]:
    requirements = build_requirements(root)
    expected_parts = _load_expected_parts(expectations_path)
    records: list[dict[str, Any]] = []
    for code, requirement in requirements.items():
        record = parse_catalog_page(_fetch(code), code)
        assembly_stock = record["overseas_stock_count"]
        record.update(
            {
                "source_url": SOURCE_TEMPLATE.format(code=code),
                "references": requirement["references"],
                "required_qty": requirement["required_qty"],
                "status": (
                    "IN_STOCK"
                    if assembly_stock >= requirement["required_qty"]
                    else "OUT_OF_STOCK"
                ),
            }
        )
        records.append(record)
    validate_records(
        records=records,
        requirements=requirements,
        expected_parts=expected_parts,
    )
    snapshot = {
        "schema_version": 1,
        "catalog_status": "PASS",
        "checked_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": SOURCE_DESCRIPTION,
        "stock_field": STOCK_FIELD_DESCRIPTION,
        "source_template": SOURCE_TEMPLATE,
        "catalog_access": CATALOG_ACCESS,
        "build_quantity": BUILD_QUANTITY,
        "bom_sha256": _bom_digest(root / "bom" / "BOM.csv"),
        "parts": records,
    }
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot_path.name}.",
        dir=snapshot_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(snapshot, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, snapshot_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return snapshot


def validate_snapshot(
    *,
    root: Path = ROOT,
    expectations_path: Path = EXPECTATIONS,
    snapshot_path: Path = SNAPSHOT,
    now: datetime | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StockError(f"cannot load stock snapshot {snapshot_path}: {error}") from error
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("catalog_status") != "PASS"
        or snapshot.get("source_template") != SOURCE_TEMPLATE
        or snapshot.get("build_quantity") != BUILD_QUANTITY
        or not isinstance(snapshot.get("parts"), list)
    ):
        raise StockError("stock snapshot schema/status is invalid or UNKNOWN")
    validate_snapshot_metadata(
        snapshot,
        now=now,
        max_age_hours=max_age_hours,
    )
    expected_digest = _bom_digest(root / "bom" / "BOM.csv")
    if snapshot.get("bom_sha256") != expected_digest:
        raise StockError("stock snapshot does not bind the current BOM bytes")
    validate_records(
        records=snapshot["parts"],
        requirements=build_requirements(root),
        expected_parts=_load_expected_parts(expectations_path),
    )
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--expectations", type=Path, default=EXPECTATIONS)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="perform read-only official catalog requests and replace snapshot",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help="reject a saved snapshot older than this many hours (default: 24)",
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.refresh:
            snapshot = refresh_snapshot(
                root=args.root.resolve(),
                expectations_path=args.expectations.resolve(),
                snapshot_path=args.snapshot.resolve(),
            )
            action = "WROTE"
        else:
            snapshot = validate_snapshot(
                root=args.root.resolve(),
                expectations_path=args.expectations.resolve(),
                snapshot_path=args.snapshot.resolve(),
                max_age_hours=args.max_age_hours,
            )
            action = "PASS"
        print(
            f"{action}: {len(snapshot['parts'])} exact JLC parts "
            f"catalog_status={snapshot['catalog_status']} "
            f"checked_at={snapshot['checked_at_utc']}"
        )
        return 0
    except StockError as error:
        print(f"check_jlc_stock: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
