from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _walk(value: Any) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []

    def visit(candidate: Any, path: str) -> None:
        if isinstance(candidate, dict):
            for key, nested in candidate.items():
                nested_path = f"{path}.{key}" if path else key
                items.append((nested_path, nested))
                visit(nested, nested_path)
        elif isinstance(candidate, list):
            for index, nested in enumerate(candidate):
                visit(nested, f"{path}[{index}]")

    visit(value, "")
    return items


def test_jlc_dfm_review_is_bound_to_the_exact_archive(
    esp32tap_dir: Path,
) -> None:
    review_path = esp32tap_dir / "vendor" / "JLC-DFM-REVIEW.json"
    assert review_path.is_file()
    review = json.loads(review_path.read_text(encoding="utf-8"))

    assert review["schema_version"] == 1
    assert review["status"] == "HOLD"
    assert (
        review["scope_status"]
        == "OPERATOR_OBSERVED_EXACT_ARCHIVE_ONLINE_DFM"
    )
    assert "operator observed" in review["evidence_scope"].lower()
    assert "not a vendor-signed result" in review["evidence_scope"]
    assert review["archive"]["path"] == "kicad/Esp32Tap-gerbers.zip"
    archive = esp32tap_dir / review["archive"]["path"]
    assert review["archive"]["sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()

    checked_at = datetime.fromisoformat(
        review["checked_at_utc"].replace("Z", "+00:00")
    )
    assert checked_at.tzinfo is not None
    assert checked_at <= datetime.now(timezone.utc)

    parsed = review["parsed_board"]
    assert parsed == {
        "copper_layers": 4,
        "height_mm": 55.0,
        "width_mm": 100.0,
    }

    actions = review["external_actions"]
    assert actions == {
        "added_to_cart": False,
        "order_submitted": False,
        "payment_authorized": False,
        "production_files_approved": False,
        "uploaded_for_analysis": True,
    }

    analysis = review["analysis"]
    assert analysis["actionable_danger_count"] == 0
    categories = analysis["categories"]
    assert categories
    assert len({item["name"] for item in categories}) == len(categories)
    calculated_actionable_dangers = 0
    accepted_danger_dispositions = {
        "ACCEPTED_DEFAULT_RULE_FALSE_POSITIVE",
        "ACCEPTED_TENTED_VIA_LEGEND_CLIP",
    }
    for item in categories:
        assert set(item["raw_counts"]) == {
            "danger",
            "informational",
            "warning",
        }
        assert all(
            isinstance(count, int) and count >= 0
            for count in item["raw_counts"].values()
        )
        if item["raw_counts"]["danger"]:
            assert item["disposition"] in accepted_danger_dispositions
            assert item["basis"]
        if item["disposition"] not in accepted_danger_dispositions:
            calculated_actionable_dangers += item["raw_counts"]["danger"]
        if item["raw_counts"]["warning"]:
            assert item["disposition"] == "WARNING_RETAINED"
    assert (
        analysis["actionable_danger_count"]
        == calculated_actionable_dangers
    )

    assert {
        "antenna_overhang_carrier",
        "controlled_impedance_production_confirmation",
        "mixed_smt_tht_fixture_and_process",
        "pcba_bom_cpl_placement_preview",
    } <= set(review["open_vendor_gates"])
    assert len(review["official_sources"]) >= 2
    assert all(
        source.startswith("https://jlcpcb.com/")
        for source in review["official_sources"]
    )

    forbidden = re.compile(
        r"(?:^|\.)(?:access|auth|cookie|customer|session|token|"
        r"upload\.file\.id)(?:$|\.)",
        re.IGNORECASE,
    )
    assert not [
        path
        for path, _value in _walk(review)
        if forbidden.search(path)
    ]
