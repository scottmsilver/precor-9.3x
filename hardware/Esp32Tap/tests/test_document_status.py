from __future__ import annotations

import re
from pathlib import Path

import pytest


STATUS_DOCUMENTS = (
    "README.md",
    "REPORT.md",
    "VALIDATION.md",
    "WORKS-AND-FITS.md",
    "ORDERING.md",
    "ORDER-READY.md",
    "AI-HANDOFF.md",
    "PREFAB-ADVICE-FOR-CLAUDE.md",
)
FORBIDDEN_ACTIVE_CLAIMS = {
    "GO to order": re.compile(r"\bGO\s+to\s+order\b", re.IGNORECASE),
    "order-ready": re.compile(r"\border[- ]ready\b", re.IGNORECASE),
    "print-ready": re.compile(r"\bprint[- ]ready\b", re.IGNORECASE),
    "two-layer design": re.compile(
        r"\b(?:two|2)[ -]?layer\b",
        re.IGNORECASE,
    ),
    "Rev A designation": re.compile(
        r"\brev(?:ision)?\s+A\b",
        re.IGNORECASE,
    ),
    "Rev A USB power diode": re.compile(r"\bD2\b"),
}
WHOLE_DOCUMENT_SUPERSEDED = re.compile(
    r"(?im)^(?:\*\*)?status(?:\*\*)?\s*:\s*"
    r"[^\n]*\bsuperseded\b[^\n]*\brev(?:ision)?\s+A\b"
)
SUPERSEDED_BLOCK = re.compile(
    r"<!--\s*BEGIN SUPERSEDED REV A\s*-->.*?"
    r"<!--\s*END SUPERSEDED REV A\s*-->",
    re.IGNORECASE | re.DOTALL,
)


def _active_text(text: str) -> str:
    if WHOLE_DOCUMENT_SUPERSEDED.search(text[:3000]):
        return ""

    text = SUPERSEDED_BLOCK.sub("", text)
    active: list[str] = []
    suppressed_heading_level: int | None = None
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            if (
                re.search(r"\bsuperseded\b", title, re.IGNORECASE)
                and re.search(
                    r"\brev(?:ision)?\s+A\b",
                    title,
                    re.IGNORECASE,
                )
            ):
                suppressed_heading_level = level
                continue
            if (
                suppressed_heading_level is not None
                and level <= suppressed_heading_level
            ):
                suppressed_heading_level = None
        if suppressed_heading_level is None:
            active.append(line)
    return "\n".join(active)


def _line_hits(
    text: str,
    pattern: re.Pattern[str],
) -> list[tuple[int, str]]:
    return [
        (line_number, line.strip())
        for line_number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def test_historical_rev_a_is_allowed_only_when_explicitly_superseded() -> None:
    sample = """\
# Current Rev B
**Status:** HOLD

## Superseded Rev A history
The old board was two-layer and order-ready.

## Current constraints
Rev B remains on HOLD.
"""
    active = _active_text(sample)

    assert "two-layer" not in active
    assert "order-ready" not in active
    assert "Rev B remains on HOLD." in active


@pytest.mark.parametrize("filename", STATUS_DOCUMENTS)
def test_active_status_documents_remain_on_hold(
    esp32tap_dir: Path,
    filename: str,
) -> None:
    text = (esp32tap_dir / filename).read_text(encoding="utf-8")
    active = _active_text(text)

    if not active.strip():
        return
    assert re.search(r"\bHOLD\b", active), (
        f"{filename} must state HOLD until every repository-closeable "
        "Rev B gate passes"
    )


def test_no_active_rev_a_or_release_ready_claims(
    esp32tap_dir: Path,
) -> None:
    violations: list[str] = []
    for filename in STATUS_DOCUMENTS:
        path = esp32tap_dir / filename
        active = _active_text(path.read_text(encoding="utf-8"))
        for label, pattern in FORBIDDEN_ACTIVE_CLAIMS.items():
            for line_number, line in _line_hits(active, pattern):
                violations.append(
                    f"{filename}:{line_number}: {label}: {line}"
                )

    assert not violations, (
        "Active Esp32Tap documents still contain Rev A/release-ready claims; "
        "move historical text into an explicitly superseded Rev A section:\n"
        + "\n".join(violations)
    )
