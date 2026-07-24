from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


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


@dataclass(frozen=True)
class MakeRule:
    targets: tuple[str, ...]
    prerequisites: tuple[str, ...]
    recipe: tuple[str, ...]


def _makefile_text() -> str:
    assert MAKEFILE.is_file(), "hardware/Esp32Tap/Makefile is required"
    return MAKEFILE.read_text(encoding="utf-8")


def _rules(text: str) -> list[MakeRule]:
    lines = text.splitlines()
    rules: list[MakeRule] = []
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z0-9_. -]+):\s*([^#;]*)$", line)
        if not match:
            continue

        recipe: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.startswith("\t"):
                recipe.append(candidate.removeprefix("\t"))
                continue
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            break
        rules.append(
            MakeRule(
                targets=tuple(match.group(1).split()),
                prerequisites=tuple(match.group(2).split()),
                recipe=tuple(recipe),
            )
        )
    return rules


def _rule(text: str, target: str) -> MakeRule:
    matches = [rule for rule in _rules(text) if target in rule.targets]
    assert len(matches) == 1, (
        f"{target} must have exactly one explicit rule; found {len(matches)}"
    )
    return matches[0]


def _declared_targets(text: str) -> set[str]:
    return {
        target
        for rule in _rules(text)
        for target in rule.targets
        if not target.startswith(".")
    }


def _assert_no_canonical_prerequisites(
    text: str,
    targets: set[str],
) -> None:
    for target in targets:
        assert not _rule(text, target).prerequisites, (
            f"{target} must not delegate through prerequisites"
        )


def _dry_run(makefile: Path, target: str) -> list[str]:
    completed = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            "--file",
            str(makefile),
            target,
        ],
        cwd=makefile.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0, (
        f"make -n {target} failed\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def test_rule_parser_retains_prerequisites() -> None:
    text = """\
test: prep generated
\tpython3 -m pytest -q tests
"""

    assert _rule(text, "test") == MakeRule(
        targets=("test",),
        prerequisites=("prep", "generated"),
        recipe=("python3 -m pytest -q tests",),
    )
    with pytest.raises(AssertionError, match="prerequisites"):
        _assert_no_canonical_prerequisites(text, {"test"})


def test_make_dry_run_exposes_prerequisite_delegation(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        """\
.PHONY: test prep
test: prep
\tpython3 -m pytest -q tests
prep:
\techo delegated
""",
        encoding="utf-8",
    )

    assert _dry_run(makefile, "test") == [
        "echo delegated",
        "python3 -m pytest -q tests",
    ]


def test_makefile_declares_canonical_phony_targets() -> None:
    text = _makefile_text()

    assert REQUIRED_TARGETS <= _declared_targets(text)
    _assert_no_canonical_prerequisites(text, REQUIRED_TARGETS)
    phony = next(
        (line for line in text.splitlines() if line.startswith(".PHONY:")),
        "",
    )
    assert REQUIRED_TARGETS <= set(phony.removeprefix(".PHONY:").split())


def test_test_target_runs_only_local_pytest_suite() -> None:
    assert _rule(_makefile_text(), "test").recipe == (
        "python3 -m pytest -q tests",
    )
    assert _dry_run(MAKEFILE, "test") == ["python3 -m pytest -q tests"]


def test_deferred_targets_fail_explicitly_without_delegating() -> None:
    text = _makefile_text()

    for target in DEFERRED_TARGETS:
        recipe = "\n".join(_rule(text, target).recipe)
        assert "source not implemented yet" in recipe, target
        assert re.search(r"\bexit\s+[1-9][0-9]*\b", recipe), target
        assert "$(MAKE)" not in recipe, target
        assert _dry_run(MAKEFILE, target) == [
            f'echo "{target}: source not implemented yet" >&2; exit 2'
        ]


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
