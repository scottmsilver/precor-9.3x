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
    lines = source.splitlines()
    executable = "\n".join(
        line for line in lines if not line.lstrip().startswith("#")
    )

    try:
        init_index = lines.index("fail=0")
        run_function_start = lines.index('run() { local n="$1"; shift')
        run_function_end = next(
            i
            for i in range(run_function_start, len(lines))
            if re.search(r"\bfi;\s*}\s*$", lines[i])
        )
        deep_start = lines.index('if [ -n "${DEEP:-}" ]; then')
        deep_end = next(
            i for i in range(deep_start + 1, len(lines)) if lines[i] == "fi"
        )
        summary_indices = [
            i for i, line in enumerate(lines) if line.startswith('echo "SWEEP:')
        ]
        exit_indices = [i for i, line in enumerate(lines) if line == "exit $fail"]
    except (ValueError, StopIteration):
        return False

    run_function = "\n".join(lines[run_function_start : run_function_end + 1])
    run_indices = [
        i for i, line in enumerate(lines) if re.match(r"^\s*run\s+", line)
    ]
    assignments = re.findall(
        r"(?<![$\w])fail\s*=\s*([^;\s}]+)",
        executable,
    )
    if len(summary_indices) != 1 or len(exit_indices) != 1 or not run_indices:
        return False

    summary_index = summary_indices[0]
    exit_index = exit_indices[0]
    trailing_code = [
        line
        for line in lines[exit_index + 1 :]
        if line.strip() and not line.lstrip().startswith("#")
    ]

    return (
        assignments == ["0", "1"]
        and init_index < run_function_start
        and 'if "$@" >/tmp/sweep_$n.log 2>&1; then' in run_function
        and re.search(
            r"\belse\b[^\n]*\bfail=1;\s*fi;\s*}\s*$",
            run_function,
        )
        is not None
        and summary_index > max(*run_indices, deep_end)
        and exit_index == summary_index + 1
        and "$([ $fail -eq 0 ] && echo ALL GREEN || echo HAS FAILURES)"
        in lines[summary_index]
        and not trailing_code
    )


def test_release_sweep_contract() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    source = SWEEP_PATH.read_text(encoding="utf-8")
    normal, deep = parse_contract(source)

    assert normal == fixture["normal"]
    assert deep == fixture["deep"]
    assert aggregates_failures(source) is fixture["aggregates_failures"]


def test_aggregation_rejects_failure_reset_before_summary() -> None:
    source = SWEEP_PATH.read_text(encoding="utf-8")
    mutated = source.replace(
        '\necho "SWEEP:',
        '\nfail=0\necho "SWEEP:',
        1,
    )

    assert mutated != source
    assert not aggregates_failures(mutated)


def test_aggregation_rejects_summary_before_run_commands() -> None:
    lines = SWEEP_PATH.read_text(encoding="utf-8").splitlines()
    summary_index = next(i for i, line in enumerate(lines) if line.startswith('echo "SWEEP:'))
    final_lines = lines[summary_index : summary_index + 2]
    del lines[summary_index : summary_index + 2]
    first_run_index = next(i for i, line in enumerate(lines) if line.startswith("run "))
    lines[first_run_index:first_run_index] = final_lines

    assert not aggregates_failures("\n".join(lines))


def test_aggregation_rejects_run_wrapper_that_ignores_argv() -> None:
    source = SWEEP_PATH.read_text(encoding="utf-8")
    mutated = source.replace(
        'if "$@" >/tmp/sweep_$n.log 2>&1; then',
        "if true; then",
        1,
    )

    assert mutated != source
    assert not aggregates_failures(mutated)
