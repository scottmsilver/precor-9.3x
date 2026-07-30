#!/usr/bin/env python3
"""Freeze the ordered release-sweep commands without executing the sweep."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
SWEEP_PATH = TOOLS_DIR / "sweep.sh"
FIXTURE_PATH = TOOLS_DIR / "fixtures" / "sweep_contract_base.json"


def parse_contract(source: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    normal: list[dict[str, object]] = []
    deep: list[dict[str, object]] = []
    in_deep_block = False

    for line in source.splitlines():
        if line == 'if [ -n "${DEEP:-}" ]; then':
            in_deep_block = True
            continue
        if in_deep_block and line == "fi":
            in_deep_block = False
            continue
        if not re.match(r"^\s*run\s+", line):
            continue

        words = shlex.split(line)
        entry = {"name": words[1], "argv": words[2:]}
        (deep if in_deep_block else normal).append(entry)

    return normal, deep


def aggregates_failures(source: str) -> bool:
    return (
        "fail=0" in source
        and re.search(r"\belse\b[^\n]*\bfail=1\b", source) is not None
        and "ALL GREEN || echo HAS FAILURES" in source
        and re.search(r"^exit \$fail$", source, re.MULTILINE) is not None
    )


def test_release_sweep_contract() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    source = SWEEP_PATH.read_text(encoding="utf-8")
    normal, deep = parse_contract(source)

    assert normal == fixture["normal"]
    assert deep == fixture["deep"]
    assert aggregates_failures(source) is fixture["aggregates_failures"]
