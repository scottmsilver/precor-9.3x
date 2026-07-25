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
FULLY_ARCHIVAL_DOCUMENTS = {"PREFAB-ADVICE-FOR-CLAUDE.md"}
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
SUPERSEDED_BLOCK_BEGIN = re.compile(
    r"<!--\s*BEGIN SUPERSEDED REV A\s*-->",
    re.IGNORECASE,
)
SUPERSEDED_BLOCK_END = re.compile(
    r"<!--\s*END SUPERSEDED REV A\s*-->",
    re.IGNORECASE,
)
SUPERSEDED_LINE = re.compile(
    r"(?ix)"
    r"(?:^(?:\*\*)?status(?:\*\*)?\s*:"
    r"[^\n]*\bsuperseded\b[^\n]*\brev(?:ision)?\s+A\b)"
    r"|(?:<!--\s*SUPERSEDED REV A LINE\s*-->)"
)


def _active_lines(text: str) -> list[tuple[int, str]]:
    active: list[tuple[int, str]] = []
    suppressed_heading_level: int | None = None
    in_superseded_block = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        begins_block = bool(SUPERSEDED_BLOCK_BEGIN.search(line))
        ends_block = bool(SUPERSEDED_BLOCK_END.search(line))
        if begins_block:
            assert not in_superseded_block, f"nested superseded block at source line {line_number}"
            in_superseded_block = not ends_block
            continue
        if in_superseded_block:
            if ends_block:
                in_superseded_block = False
            continue
        assert not ends_block, f"superseded block ends without a begin at source line {line_number}"

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            if re.search(r"\bsuperseded\b", title, re.IGNORECASE) and re.search(
                r"\brev(?:ision)?\s+A\b",
                title,
                re.IGNORECASE,
            ):
                suppressed_heading_level = level
                continue
            if suppressed_heading_level is not None and level <= suppressed_heading_level:
                suppressed_heading_level = None
        if SUPERSEDED_LINE.search(line):
            continue
        if suppressed_heading_level is None:
            active.append((line_number, line))
    assert not in_superseded_block, "unterminated superseded Rev A block"
    return active


def _active_text(text: str) -> str:
    return "\n".join(line for _, line in _active_lines(text))


def _line_hits(
    lines: list[tuple[int, str]],
    pattern: re.Pattern[str],
) -> list[tuple[int, str]]:
    return [(line_number, line.strip()) for line_number, line in lines if pattern.search(line)]


def _hold_violations(filename: str, text: str) -> list[str]:
    active_lines = _active_lines(text)
    if not active_lines:
        if filename in FULLY_ARCHIVAL_DOCUMENTS:
            return []
        return [f"{filename} is a current status document and cannot be " "entirely suppressed"]

    active = "\n".join(line for _, line in active_lines)
    if re.search(r"\bHOLD\b", active):
        return []
    return [f"{filename} must state HOLD until every repository-closeable " "Rev B gate passes"]


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


def test_early_superseded_status_does_not_hide_later_active_claims() -> None:
    sample = """\
# Audit retained for traceability
**Status:** Superseded Rev A audit

## Current ordering status
GO to order.
"""
    active = _active_text(sample)

    assert "Superseded Rev A audit" not in active
    assert "GO to order." in active


def test_only_archival_document_may_be_wholly_suppressed() -> None:
    sample = """\
# Superseded Rev A history
The old package said GO to order.
"""

    assert _active_lines(sample) == []
    assert _hold_violations("README.md", sample) == [
        "README.md is a current status document and cannot be entirely " "suppressed"
    ]
    assert not _hold_violations("PREFAB-ADVICE-FOR-CLAUDE.md", sample)


def test_filtered_claims_keep_original_source_line_numbers() -> None:
    sample = """\
# Current status
HOLD
## Superseded Rev A history
GO to order.
## Current evidence
Still gathering evidence.
GO to order.
"""

    assert _line_hits(
        _active_lines(sample),
        FORBIDDEN_ACTIVE_CLAIMS["GO to order"],
    ) == [(7, "GO to order.")]


@pytest.mark.parametrize("filename", STATUS_DOCUMENTS)
def test_active_status_documents_remain_on_hold(
    esp32tap_dir: Path,
    filename: str,
) -> None:
    text = (esp32tap_dir / filename).read_text(encoding="utf-8")
    assert not _hold_violations(filename, text)


def test_no_active_rev_a_or_release_ready_claims(
    esp32tap_dir: Path,
) -> None:
    violations: list[str] = []
    for filename in STATUS_DOCUMENTS:
        path = esp32tap_dir / filename
        active_lines = _active_lines(path.read_text(encoding="utf-8"))
        for label, pattern in FORBIDDEN_ACTIVE_CLAIMS.items():
            for line_number, line in _line_hits(active_lines, pattern):
                violations.append(f"{filename}:{line_number}: {label}: {line}")

    assert not violations, (
        "Active Esp32Tap documents still contain Rev A/release-ready claims; "
        "move historical text into an explicitly superseded Rev A section:\n" + "\n".join(violations)
    )


def test_rev_d_handoff_states_power_firmware_and_release_boundaries(
    esp32tap_dir: Path,
) -> None:
    handoff = (esp32tap_dir / "AI-HANDOFF.md").read_text(encoding="utf-8")
    required = (
        "Status: HOLD",
        "current-limited +8 V bench power",
        "USB alone cannot power or program Rev D",
        "executable **host reference**, not production",
        "one 4 s manual total-silence lease",
        "complete valid parsed frame",
        "1.5 s",
        "GPIO7 is `VBUS_PRESENT_N`",
        "stock self-powered TinyUSB VBUS-monitor input is active-high",
        "encoded value was already zero",
        "Do not submit an order",
    )
    missing = [phrase for phrase in required if phrase not in handoff]
    assert not missing
    assert re.search(r"\bpay\b", handoff, re.IGNORECASE)


def test_firmware_plan_preserves_exact_safety_gates(
    esp32tap_dir: Path,
) -> None:
    plan = (esp32tap_dir / "firmware" / "PLAN.md").read_text(encoding="utf-8")
    required = (
        "CONFIG_ESP_TASK_WDT_EN=y",
        "CONFIG_ESP_TASK_WDT_INIT=y",
        "CONFIG_ESP_TASK_WDT_TIMEOUT_S=2",
        "CONFIG_ESP_TASK_WDT_PANIC=y",
        "CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y",
        "CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS",
        "may omit that hidden/default key",
        "continuously for at least 1 ms",
        "no 10 s reconnect grace",
        "younger than\n1.5 s",
        "at most 1 s",
        "before the 10 ms deadline",
        "at most 2.25 s",
        "`bundle_sha256`",
        "USB alone cannot power or program Rev B",
    )
    missing = [phrase for phrase in required if phrase not in plan]
    assert not missing


def test_prefab_advice_is_wholly_archival(
    esp32tap_dir: Path,
) -> None:
    text = (esp32tap_dir / "PREFAB-ADVICE-FOR-CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Superseded Rev A")
    assert _active_lines(text) == []
