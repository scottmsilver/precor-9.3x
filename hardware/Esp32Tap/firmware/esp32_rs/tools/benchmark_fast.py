#!/usr/bin/env python3
"""Validate deterministic, load-matched Esp32Tap fast-loop measurements."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import NoReturn


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_COMMAND_ITEMS = 128
MAX_COMMAND_ITEM_BYTES = 4096
MAX_TEXT_BYTES = 4096
EXPECTED_SAMPLE_COUNT = 46
LOAD_BAND = 0.20
EXACT_BROAD_COMMAND = (
    "env",
    "-C",
    "hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios",
    "python3",
    "-m",
    "pytest",
    "test_reviewer_attacks.py",
    "-q",
    "-n",
    "3",
)
TOP_LEVEL_KEYS = frozenset(
    {"baseline_command", "candidate_command", "samples", "version"}
)
SAMPLE_KEYS = frozenset(
    {
        "artifact_identity",
        "command",
        "dataset",
        "duration_seconds",
        "exit_status",
        "load_1",
        "load_5",
        "pair_index",
        "retry_count",
        "role",
        "sha",
        "target_cache",
        "worktree_path",
    }
)
DATASETS = frozenset(
    {"provenance", "host", "firmware_warm", "firmware_cold"}
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
ARTIFACT_ID = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")


class ContractError(ValueError):
    """The benchmark input cannot support the acceptance claim."""


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Return the exact nearest-rank percentile."""
    if not values:
        raise ContractError("statistics require at least one sample")
    if (
        isinstance(percentile, bool)
        or not isinstance(percentile, (int, float))
        or not math.isfinite(float(percentile))
        or not 0 < float(percentile) <= 1
    ):
        raise ContractError("percentile must be finite and in (0, 1]")
    ordered = sorted(float(value) for value in values)
    rank = math.ceil(float(percentile) * len(ordered))
    return ordered[rank - 1]


def sample_median(values: Sequence[float]) -> float:
    """Return the ordinary median, averaging the two center values."""
    if not values:
        raise ContractError("statistics require at least one sample")
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractError(
            f"{label} keys are not exact (missing={missing!r}, extra={extra!r})"
        )


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{label} must be an integer >= {minimum}")
    return value


def _finite_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ContractError(f"{label} must be a finite nonnegative number")
    return float(value)


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ContractError(f"{label} must be a nonempty NUL-free string")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ContractError(f"{label} is too large")
    return value


def _command(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_COMMAND_ITEMS
    ):
        raise ContractError(
            f"{label} must be a nonempty explicit command array "
            f"of at most {MAX_COMMAND_ITEMS} items"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, str)
            or not item
            or "\0" in item
            or len(item.encode("utf-8")) > MAX_COMMAND_ITEM_BYTES
        ):
            raise ContractError(f"{label}[{index}] is not a bounded command argument")
        result.append(item)
    return tuple(result)


def _path(value: object, label: str) -> str:
    text = _bounded_text(value, label)
    pure = PurePosixPath(text)
    if not pure.is_absolute() or ".." in pure.parts or text != pure.as_posix():
        raise ContractError(f"{label} must be a normalized absolute POSIX path")
    return text


def _load_matched(first: float, second: float) -> bool:
    scale = min(first, second)
    if scale == 0:
        return first == second
    return abs(first - second) <= LOAD_BAND * scale


def _sample(value: object, index: int) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ContractError(f"sample {index} must be a JSON object")
    _exact_keys(value, SAMPLE_KEYS, f"sample {index}")

    dataset = _bounded_text(value["dataset"], f"sample {index} dataset")
    role = _bounded_text(value["role"], f"sample {index} role")
    if dataset not in DATASETS:
        raise ContractError(f"sample {index} has unknown dataset")
    command = _command(value["command"], f"sample {index} command")
    duration = _finite_number(
        value["duration_seconds"], f"sample {index} duration_seconds"
    )
    load_1 = _finite_number(value["load_1"], f"sample {index} load_1")
    load_5 = _finite_number(value["load_5"], f"sample {index} load_5")
    exit_status = _strict_int(
        value["exit_status"], f"sample {index} exit status"
    )
    retry_count = _strict_int(
        value["retry_count"], f"sample {index} retry count"
    )
    no_artifact_expected = dataset == "host" or (
        dataset == "provenance" and role == "missing"
    )
    raw_artifact = value["artifact_identity"]
    if raw_artifact is None:
        if not no_artifact_expected:
            raise ContractError(
                f"sample {index} artifact_identity is required for an artifact sample"
            )
        artifact: str | None = None
    else:
        if no_artifact_expected:
            raise ContractError(
                f"sample {index} artifact_identity must be null when no artifact exists"
            )
        artifact = _bounded_text(
            raw_artifact, f"sample {index} artifact_identity"
        )
        if ARTIFACT_ID.fullmatch(artifact) is None:
            raise ContractError(
                f"sample {index} artifact_identity must be a SHA-256 identity"
            )
    sha = _bounded_text(value["sha"], f"sample {index} sha")
    if HEX40.fullmatch(sha) is None:
        raise ContractError(f"sample {index} sha must be 40 lowercase hex characters")
    worktree = _path(value["worktree_path"], f"sample {index} worktree_path")

    firmware = dataset in {"firmware_warm", "firmware_cold"}
    raw_pair = value["pair_index"]
    if firmware:
        pair_index: int | None = _strict_int(
            raw_pair, f"sample {index} pair_index"
        )
        if role not in {"baseline", "candidate"}:
            raise ContractError(f"sample {index} firmware role is invalid")
    else:
        if raw_pair is not None:
            raise ContractError(f"sample {index} pair_index must be null")
        pair_index = None

    raw_cache = value["target_cache"]
    if dataset == "firmware_cold":
        target_cache: str | None = _path(
            raw_cache, f"sample {index} target_cache"
        )
        if target_cache == "/tmp/rustcargo" or target_cache.startswith(
            "/tmp/rustcargo/"
        ):
            raise ContractError(
                "cold target_cache must never use or clear /tmp/rustcargo"
            )
        cache_path = PurePosixPath(target_cache)
        if (
            len(cache_path.parts) < 3
            or cache_path.parts[0:2] != ("/", "tmp")
            or not cache_path.parts[2].startswith("esp32tap-")
        ):
            raise ContractError(
                "cold target_cache must be a task-specific /tmp/esp32tap-* path"
            )
        if not worktree.startswith("/tmp/esp32tap-bench-cold."):
            raise ContractError(
                "cold worktree_path must be a task-specific "
                "/tmp/esp32tap-bench-cold.* path"
            )
    else:
        if raw_cache is not None:
            raise ContractError(
                f"sample {index} target_cache must be null outside cold data"
            )
        target_cache = None

    return {
        "artifact_identity": artifact,
        "command": command,
        "dataset": dataset,
        "duration_seconds": duration,
        "exit_status": exit_status,
        "load_1": load_1,
        "load_5": load_5,
        "pair_index": pair_index,
        "retry_count": retry_count,
        "role": role,
        "sha": sha,
        "target_cache": target_cache,
        "worktree_path": worktree,
    }


def _require_success(sample: Mapping[str, object], label: str) -> None:
    if sample["exit_status"] != 0:
        raise ContractError(f"{label} exit status must be zero")
    if sample["retry_count"] != 0:
        raise ContractError(f"{label} retry count must be zero")


def _indexed_pairs(
    samples: Sequence[Mapping[str, object]], dataset: str, count: int
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    selected = [sample for sample in samples if sample["dataset"] == dataset]
    if len(selected) != count * 2:
        raise ContractError(
            f"{dataset} requires exactly {count} baseline/candidate pairs"
        )
    pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for pair_index in range(count):
        matching = [
            sample for sample in selected if sample["pair_index"] == pair_index
        ]
        if len(matching) != 2 or {sample["role"] for sample in matching} != {
            "baseline",
            "candidate",
        }:
            raise ContractError(
                f"{dataset} pair indexes must be exact 0..{count - 1} "
                "with one baseline and one candidate"
            )
        baseline = next(sample for sample in matching if sample["role"] == "baseline")
        candidate = next(
            sample for sample in matching if sample["role"] == "candidate"
        )
        if not _load_matched(
            float(baseline["load_1"]), float(candidate["load_1"])
        ):
            raise ContractError(
                f"{dataset} pair {pair_index} is outside the 20% load band"
            )
        pairs.append((baseline, candidate))
    return pairs


def evaluate_document(document: object) -> dict[str, object]:
    """Validate all acceptance records and return the canonical summary."""
    if not isinstance(document, dict) or not all(
        isinstance(key, str) for key in document
    ):
        raise ContractError("top-level value must be a JSON object")
    _exact_keys(document, TOP_LEVEL_KEYS, "top-level")
    if _strict_int(document["version"], "version", minimum=1) != 1:
        raise ContractError("version must equal 1")

    baseline_command = _command(document["baseline_command"], "baseline_command")
    candidate_command = _command(document["candidate_command"], "candidate_command")
    if baseline_command != EXACT_BROAD_COMMAND:
        raise ContractError(
            "baseline_command must be the exact broad reviewer command from Task 0"
        )
    if candidate_command == baseline_command:
        raise ContractError("candidate_command must differ from baseline_command")

    raw_samples = document["samples"]
    if not isinstance(raw_samples, list) or len(raw_samples) != EXPECTED_SAMPLE_COUNT:
        raise ContractError(
            f"samples must contain exactly {EXPECTED_SAMPLE_COUNT} records"
        )
    samples = [_sample(value, index) for index, value in enumerate(raw_samples)]

    provenance = [sample for sample in samples if sample["dataset"] == "provenance"]
    if len(provenance) != 10:
        raise ContractError("requires exactly 10 provenance samples")
    if sum(sample["role"] == "missing" for sample in provenance) != 5:
        raise ContractError("provenance requires exactly five missing samples")
    if sum(sample["role"] == "stale" for sample in provenance) != 5:
        raise ContractError("provenance requires exactly five stale samples")
    for index, sample in enumerate(provenance):
        expected = 20 if sample["role"] == "missing" else 21
        label = "missing" if expected == 20 else "stale"
        command = sample["command"]
        assert isinstance(command, tuple)
        if (
            len(command) < 3
            or not PurePosixPath(command[0]).name.startswith("python3")
            or PurePosixPath(command[1]).name != "artifact_provenance.py"
            or "verify" not in command[2:]
        ):
            raise ContractError(
                f"provenance sample {index} must record the direct verifier command"
            )
        if sample["exit_status"] != expected:
            raise ContractError(
                f"provenance sample {index} must be recognized {label} exit {expected}"
            )
        if sample["retry_count"] != 0:
            raise ContractError(
                f"provenance sample {index} retry count must be zero"
            )
    provenance_p95 = nearest_rank(
        [float(sample["duration_seconds"]) for sample in provenance], 0.95
    )
    if provenance_p95 >= 1:
        raise ContractError("provenance p95 must be below 1 second")

    host = [sample for sample in samples if sample["dataset"] == "host"]
    if len(host) != 10 or any(sample["role"] != "candidate" for sample in host):
        raise ContractError("host dataset requires exactly 10 candidate samples")
    for index, sample in enumerate(host):
        command = sample["command"]
        assert isinstance(command, tuple)
        if (
            len(command) != 5
            or command[0:3] != ("cargo", "test", "--manifest-path")
            or not command[3].endswith("program_core/Cargo.toml")
            or command[4] != "-q"
        ):
            raise ContractError(
                f"host sample {index} must record the program_core host command"
            )
        _require_success(sample, f"host sample {index}")
    host_p95 = nearest_rank(
        [float(sample["duration_seconds"]) for sample in host], 0.95
    )
    if host_p95 >= 5:
        raise ContractError("host p95 must be below 5 seconds")

    warm_pairs = _indexed_pairs(samples, "firmware_warm", 10)
    for pair_index, (baseline, candidate) in enumerate(warm_pairs):
        _require_success(baseline, f"firmware warm baseline {pair_index}")
        _require_success(candidate, f"firmware warm candidate {pair_index}")
        if baseline["command"] != baseline_command:
            raise ContractError(
                f"firmware warm baseline {pair_index} command does not match "
                "the declared baseline"
            )
        if candidate["command"] != candidate_command:
            raise ContractError(
                f"firmware warm candidate {pair_index} command does not match "
                "the declared candidate"
            )
    for role in ("baseline", "candidate"):
        identities = {
            sample["artifact_identity"]
            for pair in warm_pairs
            for sample in pair
            if sample["role"] == role
        }
        if len(identities) != 1:
            raise ContractError(
                f"firmware warm {role} samples must use one artifact identity"
            )
    baseline_durations = [
        float(baseline["duration_seconds"]) for baseline, _candidate in warm_pairs
    ]
    candidate_durations = [
        float(candidate["duration_seconds"]) for _baseline, candidate in warm_pairs
    ]
    candidate_p95 = nearest_rank(candidate_durations, 0.95)
    baseline_median = sample_median(baseline_durations)
    candidate_median = sample_median(candidate_durations)
    if candidate_p95 >= 30:
        raise ContractError("firmware candidate p95 must be below 30 seconds")
    if candidate_median > baseline_median * 0.5:
        raise ContractError(
            "firmware candidate median must be at least 50% below baseline"
        )

    cold_pairs = _indexed_pairs(samples, "firmware_cold", 3)
    cold_samples = [
        sample for pair in cold_pairs for sample in pair
    ]
    caches = [str(sample["target_cache"]) for sample in cold_samples]
    worktrees = [str(sample["worktree_path"]) for sample in cold_samples]
    if len(set(caches)) != 6:
        raise ContractError("cold samples require six distinct target_cache paths")
    if len(set(worktrees)) != 6:
        raise ContractError("cold samples require six distinct worktree_path paths")
    for pair_index, (baseline, candidate) in enumerate(cold_pairs):
        _require_success(baseline, f"firmware cold baseline {pair_index}")
        _require_success(candidate, f"firmware cold candidate {pair_index}")
        if baseline["command"] != baseline_command:
            raise ContractError(
                f"firmware cold baseline {pair_index} command does not match "
                "the declared baseline"
            )
        if candidate["command"] != candidate_command:
            raise ContractError(
                f"firmware cold candidate {pair_index} command does not match "
                "the declared candidate"
            )
    cold_baseline_durations = [
        float(baseline["duration_seconds"]) for baseline, _candidate in cold_pairs
    ]
    cold_candidate_durations = [
        float(candidate["duration_seconds"]) for _baseline, candidate in cold_pairs
    ]

    return {
        "cold_firmware": {
            "baseline_median_seconds": sample_median(cold_baseline_durations),
            "baseline_p95_seconds": nearest_rank(cold_baseline_durations, 0.95),
            "candidate_median_seconds": sample_median(cold_candidate_durations),
            "candidate_p95_seconds": nearest_rank(cold_candidate_durations, 0.95),
        },
        "cold_pairs": 3,
        "firmware": {
            "baseline_median_seconds": baseline_median,
            "candidate_median_seconds": candidate_median,
            "candidate_p95_seconds": candidate_p95,
        },
        "host": {"p95_seconds": host_p95},
        "load_band_percent": 20,
        "provenance": {"p95_seconds": provenance_p95},
        "status": "PASS",
        "version": 1,
        "warm_pairs": 10,
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ContractError(f"non-finite JSON constant is forbidden: {value}")


def load_document(path: Path) -> object:
    """Load one regular, canonical, bounded JSON file without following links."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError(f"cannot open benchmark input: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ContractError("benchmark input must be a regular file")
        if info.st_size > MAX_INPUT_BYTES:
            raise ContractError("benchmark input is too large")
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_INPUT_BYTES:
            raise ContractError("benchmark input is too large")
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("benchmark input must be UTF-8") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ContractError(f"invalid benchmark JSON: {exc}") from exc
    try:
        canonical = (
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid benchmark JSON: {exc}") from exc
    if text != canonical:
        raise ContractError("benchmark input must be canonical JSON with one newline")
    return document


def _usage() -> NoReturn:
    print(
        "usage: benchmark_fast.py evaluate ACCEPTANCE.json",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "evaluate":
        _usage()
    try:
        document = load_document(Path(arguments[1]))
        result = evaluate_document(document)
    except (ContractError, OSError) as exc:
        print(f"benchmark-fast: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
