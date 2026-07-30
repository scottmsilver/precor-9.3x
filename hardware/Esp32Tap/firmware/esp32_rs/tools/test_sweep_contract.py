#!/usr/bin/env python3
"""Freeze the ordered release-sweep commands without executing the sweep."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
SWEEP_PATH = TOOLS_DIR / "sweep.sh"
FIXTURE_PATH = TOOLS_DIR / "fixtures" / "sweep_contract_base.json"


def read_sweep_source() -> str:
    return SWEEP_PATH.read_bytes().decode("utf-8")


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


def aggregate_contract_bytes(source: str) -> bytes:
    lines = source.splitlines(keepends=True)
    run_function_start = next(
        i for i, line in enumerate(lines) if line.startswith("run() {")
    )
    run_function_end = next(
        i
        for i in range(run_function_start, len(lines))
        if re.search(r"\bfi;\s*}\s*$", lines[i].rstrip("\r\n"))
    )
    deep_start = next(
        i
        for i, line in enumerate(lines)
        if line.rstrip("\r\n") == 'if [ -n "${DEEP:-}" ]; then'
    )
    deep_end = next(
        i
        for i in range(deep_start + 1, len(lines))
        if lines[i].rstrip("\r\n") == "fi"
    )

    run_function = "".join(lines[run_function_start : run_function_end + 1])
    post_deep_tail = "".join(lines[deep_end + 1 :])
    return (
        b"run_function\0"
        + run_function.encode("utf-8")
        + b"\0post_deep_tail\0"
        + post_deep_tail.encode("utf-8")
    )


def aggregate_contract_sha256(source: str) -> str:
    return hashlib.sha256(aggregate_contract_bytes(source)).hexdigest()


def sweep_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def matches_base_sweep_contract(source: str) -> bool:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return (
        fixture["aggregates_failures"] is True
        and aggregate_contract_sha256(source)
        == fixture["aggregate_contract_sha256"]
        and sweep_sha256(source) == fixture["sweep_sha256"]
    )


def test_release_sweep_contract() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    source = read_sweep_source()
    normal, deep = parse_contract(source)

    assert normal == fixture["normal"]
    assert deep == fixture["deep"]
    assert fixture["aggregates_failures"] is True
    assert (
        aggregate_contract_sha256(source)
        == fixture["aggregate_contract_sha256"]
    )
    assert sweep_sha256(source) == fixture["sweep_sha256"]


def test_aggregation_rejects_failure_reset_before_summary() -> None:
    source = read_sweep_source()
    mutated = source.replace(
        '\necho "SWEEP:',
        '\nfail=0\necho "SWEEP:',
        1,
    )

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_summary_before_run_commands() -> None:
    lines = read_sweep_source().splitlines(keepends=True)
    summary_index = next(
        i for i, line in enumerate(lines) if line.startswith('echo "SWEEP:')
    )
    final_lines = lines[summary_index : summary_index + 2]
    del lines[summary_index : summary_index + 2]
    first_run_index = next(
        i for i, line in enumerate(lines) if line.startswith("run ")
    )
    lines[first_run_index:first_run_index] = final_lines

    assert not matches_base_sweep_contract("".join(lines))


def test_aggregation_rejects_run_wrapper_that_ignores_argv() -> None:
    source = read_sweep_source()
    mutated = source.replace(
        'if "$@" >/tmp/sweep_$n.log 2>&1; then',
        "if true; then",
        1,
    )

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_early_return_from_run_wrapper() -> None:
    source = read_sweep_source()
    mutated = source.replace(
        'run() { local n="$1"; shift\n',
        'run() { local n="$1"; shift\n  return 0\n',
        1,
    )

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_early_exit_before_summary() -> None:
    source = read_sweep_source()
    mutated = source.replace(
        '\necho "SWEEP:',
        '\nexit 0\necho "SWEEP:',
        1,
    )

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_changed_accumulator_initialization() -> None:
    source = read_sweep_source()
    mutated = source.replace("\nfail=0\n", "\nfail=7\n", 1)

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_removed_accumulator_initialization() -> None:
    source = read_sweep_source()
    mutated = source.replace("\nfail=0\n", "\n", 1)

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)


def test_aggregation_rejects_early_exit_before_deep_block() -> None:
    source = read_sweep_source()
    mutated = source.replace(
        '\nif [ -n "${DEEP:-}" ]; then',
        '\nexit 0\nif [ -n "${DEEP:-}" ]; then',
        1,
    )

    assert mutated != source
    assert not matches_base_sweep_contract(mutated)
