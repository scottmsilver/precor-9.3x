from __future__ import annotations

import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MAKEFILE = PROJECT_DIR / "Makefile"
REQUIRED_TARGETS = {
    "test",
    "generate",
    "erc",
    "drc",
    "sim",
    "enclosure",
    "fab",
    "check",
    "clean-check",
}
DEFERRED_TARGETS = REQUIRED_TARGETS - {"test"}


def _makefile_text() -> str:
    assert MAKEFILE.is_file(), "hardware/Esp32Tap/Makefile is required"
    return MAKEFILE.read_text(encoding="utf-8")


def _declared_targets(text: str) -> set[str]:
    targets: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_. -]+):(?:\s|$)", line)
        if match and not line.startswith("."):
            targets.update(match.group(1).split())
    return targets


def _recipe(text: str, target: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z0-9_. -]+):(?:\s|$)", line)
        if not match or target not in match.group(1).split():
            continue

        recipe: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.startswith("\t"):
                recipe.append(candidate.removeprefix("\t"))
                continue
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            break
        return recipe
    return []


def test_makefile_declares_canonical_phony_targets() -> None:
    text = _makefile_text()

    assert REQUIRED_TARGETS <= _declared_targets(text)
    phony = next(
        (line for line in text.splitlines() if line.startswith(".PHONY:")),
        "",
    )
    assert REQUIRED_TARGETS <= set(phony.removeprefix(".PHONY:").split())


def test_test_target_runs_only_local_pytest_suite() -> None:
    assert _recipe(_makefile_text(), "test") == [
        "python3 -m pytest -q tests"
    ]


def test_deferred_targets_fail_explicitly_without_delegating() -> None:
    text = _makefile_text()

    for target in DEFERRED_TARGETS:
        recipe = "\n".join(_recipe(text, target))
        assert "source not implemented yet" in recipe, target
        assert re.search(r"\bexit\s+[1-9][0-9]*\b", recipe), target
        assert "$(MAKE)" not in recipe, target


def test_makefile_never_reaches_unrelated_or_live_hardware_workflows() -> None:
    lowered = _makefile_text().lower()
    forbidden = {
        "test-pi": r"\btest-pi\b",
        "test-all": r"\btest-all\b",
        "ship-check": r"\bship-check\b",
        "SSH": r"\bssh\b",
        "SCP": r"\bscp\b",
        "rsync": r"\brsync\b",
        "live treadmill host": r"\b192\.168\.1\.206\b",
        "live treadmill mode": r"\btreadmill_mock\s*=\s*0\b",
        "live treadmill lock": r"\bpi-lock\b",
        "live treadmill command": (
            r"\b(?:run|start|control)[-_ ](?:the[-_ ])?treadmill\b"
        ),
    }

    assert not {
        label
        for label, pattern in forbidden.items()
        if re.search(pattern, lowered)
    }
