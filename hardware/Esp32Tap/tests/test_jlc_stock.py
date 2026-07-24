from __future__ import annotations

import copy
import json
import runpy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def stock_tool(esp32tap_dir: Path) -> SimpleNamespace:
    path = esp32tap_dir / "tools" / "check_jlc_stock.py"
    assert path.is_file(), "tools/check_jlc_stock.py is required"
    return SimpleNamespace(
        **runpy.run_path(str(path), run_name="esp32tap_stock_test")
    )


def _catalog_html(
    *,
    code: str = "C123",
    model: str = "EXACT-PART",
    library_type: str = "expand",
    stock: int = 42,
    available: int = 40,
) -> str:
    return (
        "<script>self.__next_f.push([1,\""
        "\\\"componentInfo\\\":{"
        f"\\\"componentCode\\\":\\\"{code}\\\","
        f"\\\"componentModelEn\\\":\\\"{model}\\\","
        "\\\"componentSpecificationEn\\\":\\\"SOT-23\\\","
        f"\\\"canPresaleNumber\\\":{available},"
        f"\\\"overseasStockCount\\\":{stock},"
        f"\\\"componentLibraryType\\\":\\\"{library_type}\\\""
        "}\"])</script>"
    )


def _requirements() -> dict[str, dict[str, object]]:
    return {
        "C123": {
            "references": ["U1", "U2"],
            "jlc_class": "Extended",
            "required_qty": 4,
            "footprint": "Lib:Footprint",
        }
    }


def _record() -> dict[str, object]:
    return {
        "lcsc": "C123",
        "model": "EXACT-PART",
        "package": "SOT-23",
        "footprint": "Lib:Footprint",
        "library_type": "expand",
        "jlc_class": "Extended",
        "overseas_stock_count": 42,
        "can_presale_number": 40,
        "source_url": "https://jlcpcb.com/partdetail/C123",
        "references": ["U1", "U2"],
        "required_qty": 4,
        "status": "IN_STOCK",
    }


def _expected_parts() -> dict[str, dict[str, str]]:
    return {
        "C123": {
            "model": "EXACT-PART",
            "package": "SOT-23",
            "footprint": "Lib:Footprint",
        }
    }


def _write_design_and_bom(root: Path, *, bom_code: str = "C123") -> None:
    tools = root / "tools"
    bom = root / "bom"
    tools.mkdir(parents=True)
    bom.mkdir(parents=True)
    (tools / "design.py").write_text(
        "COMPONENTS = {\n"
        "    'U1': ('PART', 'Lib', 'Footprint', 'C123', "
        "'Extended', 1.0, 'part', {'1': '1'}),\n"
        "}\n"
        "DNP = set()\n"
        "def validate():\n"
        "    return None\n",
        encoding="utf-8",
    )
    (bom / "BOM.csv").write_text(
        "Comment,Designator,Footprint,LCSC Part #,JLC class,Qty,"
        "Unit cost (USD),Ext cost (USD),Description\n"
        f"PART,U1,Footprint,{bom_code},Extended,1,"
        "1.000,1.000,part\n",
        encoding="utf-8",
    )


def test_requirements_are_derived_from_matching_design_and_bom(
    stock_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    _write_design_and_bom(tmp_path)

    assert stock_tool.build_requirements(tmp_path) == {
        "C123": {
            "references": ["U1"],
            "jlc_class": "Extended",
            "required_qty": 2,
            "footprint": "Lib:Footprint",
        }
    }


def test_requirements_reject_a_bom_that_differs_from_design(
    stock_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    _write_design_and_bom(tmp_path, bom_code="C999")

    with pytest.raises(stock_tool.StockError, match="BOM.*design"):
        stock_tool.build_requirements(tmp_path)


def test_catalog_parser_extracts_exact_identity_class_and_stock(
    stock_tool: SimpleNamespace,
) -> None:
    record = stock_tool.parse_catalog_page(_catalog_html(), "C123")

    assert record == {
        "lcsc": "C123",
        "model": "EXACT-PART",
        "package": "SOT-23",
        "library_type": "expand",
        "jlc_class": "Extended",
        "overseas_stock_count": 42,
        "can_presale_number": 40,
    }


def test_catalog_parser_accepts_consistent_official_summary_shapes(
    stock_tool: SimpleNamespace,
) -> None:
    identity_with_class = (
        '<script>{"componentInfo":{'
        '"componentCode":"C123",'
        '"componentModelEn":"EXACT-PART",'
        '"componentSpecificationEn":"SOT-23",'
        '"componentLibraryType":"expand",'
        '"assemblyMode":"SMT"}}</script>'
    )
    category_summary = (
        '<script>{"componentInfo":{'
        '"componentCode":"C123",'
        '"componentModelEn":"EXACT-PART",'
        '"componentSpecificationEn":"SOT-23",'
        '"firstTypeNameEn":"Transistors"}}</script>'
    )

    assert stock_tool.parse_catalog_page(
        identity_with_class + category_summary + _catalog_html(),
        "C123",
    ) == stock_tool.parse_catalog_page(_catalog_html(), "C123")


@pytest.mark.parametrize(
    ("html", "message"),
    [
        (_catalog_html(code="C999"), "C123"),
        (
            _catalog_html() + _catalog_html(model="OTHER"),
            "ambiguous",
        ),
        (
            _catalog_html().replace(
                '\\"overseasStockCount\\":42,',
                "",
            ),
            "overseasStockCount",
        ),
        (
            (
                '<script>{"componentInfo":{"componentCode":"C123",'
                '"unrelatedNestedRecord":{'
                '"componentModelEn":"WRONG-SCOPE",'
                '"componentSpecificationEn":"0201",'
                '"canPresaleNumber":999,'
                '"overseasStockCount":999,'
                '"componentLibraryType":"expand"}}}</script>'
                ),
                "direct field",
            ),
        (
            _catalog_html().replace(
                '\\"componentModelEn\\":\\"EXACT-PART\\",',
                '\\"componentModelEn\\":\\"EXACT-PART\\",'
                '\\"componentModelEn\\":\\"OTHER\\",',
            ),
            "duplicate",
        ),
        (
            _catalog_html().replace(
                '\\"overseasStockCount\\":42,',
                '\\"overseasStockCount\\":\\"42\\",',
            ),
            "overseasStockCount",
        ),
        (
            _catalog_html().replace(
                '\\"componentLibraryType\\":\\"expand\\"',
                '\\"malformed\\":NaN,'
                '\\"componentLibraryType\\":\\"expand\\"',
            ),
            "non-finite",
        ),
        (
            _catalog_html()
            + _catalog_html().replace(
                '\\"componentSpecificationEn\\":\\"SOT-23\\",',
                "",
            ),
            "componentSpecificationEn",
        ),
        (_catalog_html(library_type="mystery"), "library type"),
    ],
)
def test_catalog_parser_fails_closed(
    stock_tool: SimpleNamespace,
    html: str,
    message: str,
) -> None:
    with pytest.raises(stock_tool.StockError, match=message):
        stock_tool.parse_catalog_page(html, "C123")


def test_snapshot_records_bind_bom_identity_quantity_and_model(
    stock_tool: SimpleNamespace,
) -> None:
    stock_tool.validate_records(
        records=[_record()],
        requirements=_requirements(),
        expected_parts=_expected_parts(),
    )


def test_snapshot_records_reject_empty_success(
    stock_tool: SimpleNamespace,
) -> None:
    with pytest.raises(stock_tool.StockError, match="empty"):
        stock_tool.validate_records(
            records=[],
            requirements={},
            expected_parts={},
        )


def test_part_expectations_reject_nonfinite_json(
    stock_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    expectations = tmp_path / "expectations.json"
    expectations.write_text(
        '{"schema_version":1,"parts":'
        '{"C123":{"model":NaN,"package":"SOT-23",'
        '"footprint":"Lib:Footprint"}}}',
        encoding="utf-8",
    )

    with pytest.raises(stock_tool.StockError, match="non-finite"):
        stock_tool._load_expected_parts(expectations)


def test_public_assembly_stock_does_not_require_presale_inventory(
    stock_tool: SimpleNamespace,
) -> None:
    record = _record()
    record["can_presale_number"] = 0

    stock_tool.validate_records(
        records=[record],
        requirements=_requirements(),
        expected_parts=_expected_parts(),
    )


def _snapshot_metadata(
    stock_tool: SimpleNamespace,
    checked_at: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "catalog_status": "PASS",
        "checked_at_utc": checked_at,
        "source": stock_tool.SOURCE_DESCRIPTION,
        "source_template": stock_tool.SOURCE_TEMPLATE,
        "stock_field": stock_tool.STOCK_FIELD_DESCRIPTION,
        "catalog_access": stock_tool.CATALOG_ACCESS,
        "build_quantity": stock_tool.BUILD_QUANTITY,
        "parts": [],
    }


def test_snapshot_metadata_requires_fresh_exact_official_provenance(
    stock_tool: SimpleNamespace,
) -> None:
    now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
    stock_tool.validate_snapshot_metadata(
        _snapshot_metadata(stock_tool, "2026-07-24T07:00:00Z"),
        now=now,
        max_age_hours=24.0,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("stale", "stale"),
        ("future", "future"),
        ("source", "provenance"),
        ("stock-field", "provenance"),
        ("access", "provenance"),
        ("timestamp", "timestamp"),
        ("noncanonical", "canonical"),
    ],
)
def test_snapshot_metadata_fails_closed(
    stock_tool: SimpleNamespace,
    mutation: str,
    message: str,
) -> None:
    now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
    metadata = _snapshot_metadata(stock_tool, "2026-07-24T07:00:00Z")
    if mutation == "stale":
        metadata["checked_at_utc"] = "1900-01-01T00:00:00Z"
    elif mutation == "future":
        metadata["checked_at_utc"] = "2026-07-24T09:00:00Z"
    elif mutation == "source":
        metadata["source"] = "not JLC"
    elif mutation == "stock-field":
        metadata["stock_field"] = "invented"
    elif mutation == "access":
        metadata["catalog_access"] = {"authenticated_api": "pretended"}
    elif mutation == "noncanonical":
        metadata["checked_at_utc"] = "2026-7-24T7:00:00Z"
    else:
        metadata["checked_at_utc"] = "yesterday"

    with pytest.raises(stock_tool.StockError, match=message):
        stock_tool.validate_snapshot_metadata(
            metadata,
            now=now,
            max_age_hours=24.0,
        )


@pytest.mark.parametrize("max_age", [float("nan"), float("inf")])
def test_snapshot_metadata_rejects_nonfinite_max_age(
    stock_tool: SimpleNamespace,
    max_age: float,
) -> None:
    with pytest.raises(stock_tool.StockError, match="finite"):
        stock_tool.validate_snapshot_metadata(
            _snapshot_metadata(stock_tool, "2026-07-24T07:00:00Z"),
            now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
            max_age_hours=max_age,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "records differ"),
        ("wrong-model", "model"),
        ("wrong-package", "package"),
        ("wrong-footprint", "footprint"),
        ("wrong-class", "class"),
        ("wrong-library-type", "library type"),
        ("unknown", "UNKNOWN"),
        ("out-of-stock", "stock"),
        ("wrong-refs", "references"),
        ("insufficient", "stock"),
        ("negative-presale", "negative"),
        ("negative-assembly", "negative"),
        ("extra-field", "schema"),
    ],
)
def test_snapshot_records_fail_closed(
    stock_tool: SimpleNamespace,
    mutation: str,
    message: str,
) -> None:
    records = [_record()]
    requirements = _requirements()
    if mutation == "missing":
        records = []
    elif mutation == "wrong-model":
        records[0]["model"] = "WRONG"
    elif mutation == "wrong-package":
        records[0]["package"] = "WRONG"
    elif mutation == "wrong-footprint":
        records[0]["footprint"] = "Wrong:Footprint"
    elif mutation == "wrong-class":
        records[0]["jlc_class"] = "Basic"
    elif mutation == "wrong-library-type":
        records[0]["library_type"] = "base"
    elif mutation == "unknown":
        records[0]["status"] = "UNKNOWN"
    elif mutation == "out-of-stock":
        records[0]["status"] = "OUT_OF_STOCK"
        records[0]["overseas_stock_count"] = 0
    elif mutation == "wrong-refs":
        records[0]["references"] = ["U1"]
    elif mutation == "insufficient":
        records[0]["overseas_stock_count"] = 3
    elif mutation == "negative-presale":
        records[0]["can_presale_number"] = -1
    elif mutation == "negative-assembly":
        records[0]["overseas_stock_count"] = -1
    else:
        records[0]["authenticated_api_probe"] = "PASS"

    with pytest.raises(stock_tool.StockError, match=message):
        stock_tool.validate_records(
            records=copy.deepcopy(records),
            requirements=copy.deepcopy(requirements),
            expected_parts=_expected_parts(),
        )


def test_refresh_rejects_design_footprint_mismatch_before_fetch(
    stock_tool: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_design_and_bom(tmp_path)
    expectations = tmp_path / "bom" / "JLC-PART-EXPECTATIONS.json"
    expectations.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "parts": {
                    "C123": {
                        "model": "EXACT-PART",
                        "package": "SOT-23",
                        "footprint": "Wrong:QFN",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def unexpected_fetch(code: str) -> str:
        pytest.fail(f"network fetch happened before footprint check: {code}")

    monkeypatch.setitem(
        stock_tool.refresh_snapshot.__globals__,
        "_fetch",
        unexpected_fetch,
    )
    with pytest.raises(stock_tool.StockError, match="footprint"):
        stock_tool.refresh_snapshot(
            root=tmp_path,
            expectations_path=expectations,
            snapshot_path=tmp_path / "bom" / "snapshot.json",
        )


def test_saved_snapshot_rejects_undeclared_top_level_evidence(
    stock_tool: SimpleNamespace,
    tmp_path: Path,
) -> None:
    _write_design_and_bom(tmp_path)
    expectations = tmp_path / "bom" / "JLC-PART-EXPECTATIONS.json"
    expectations.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "parts": _expected_parts(),
            }
        ),
        encoding="utf-8",
    )
    record = _record()
    record["references"] = ["U1"]
    record["required_qty"] = 2
    snapshot = _snapshot_metadata(
        stock_tool,
        "2026-07-24T07:00:00Z",
    )
    snapshot["bom_sha256"] = stock_tool._bom_digest(
        tmp_path / "bom" / "BOM.csv"
    )
    snapshot["parts"] = [record]
    snapshot["authenticated_api_probe"] = "PASS"
    snapshot_path = tmp_path / "bom" / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(stock_tool.StockError, match="schema"):
        stock_tool.validate_snapshot(
            root=tmp_path,
            expectations_path=expectations,
            snapshot_path=snapshot_path,
            now=datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
        )
