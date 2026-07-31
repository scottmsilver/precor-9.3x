from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parent
SCRIPT = TOOLS / "benchmark_fast.py"
SPEC = importlib.util.spec_from_file_location("benchmark_fast", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark_fast = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark_fast
SPEC.loader.exec_module(benchmark_fast)

BROAD = [
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
]
CANDIDATE = ["bash", "tools/fast.sh", "--base", "HEAD~1"]
HOST = [
    "cargo",
    "test",
    "--manifest-path",
    "hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml",
    "-q",
]
RS = "hardware/Esp32Tap/firmware/esp32_rs"
BASELINE_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40


def record(
    dataset: str,
    role: str,
    index: int,
    duration: float,
    *,
    load: float = 1.0,
) -> dict[str, object]:
    firmware = dataset.startswith("firmware_")
    cold = dataset == "firmware_cold"
    worktree = (
        f"/tmp/esp32tap-bench-cold.worktree-{role}-{index}"
        if cold
        else f"/tmp/esp32tap-bench-{dataset}-{role}"
    )
    if role == "missing":
        status = 20
        kind = "qemu-test" if index % 2 == 0 else "production"
        public = "build_qemu_test" if kind == "qemu-test" else "build"
        command = [
            "python3",
            "tools/artifact_provenance.py",
            "--repo-root",
            worktree,
            "verify",
            "--kind",
            kind,
            f"{worktree}/{RS}/{public}",
        ]
    elif role == "stale":
        status = 21
        kind = "qemu-test" if index % 2 == 0 else "production"
        public = "build_qemu_test" if kind == "qemu-test" else "build"
        command = [
            "python3",
            "tools/artifact_provenance.py",
            "--repo-root",
            worktree,
            "verify",
            "--kind",
            kind,
            f"{worktree}/{RS}/{public}",
        ]
    elif dataset == "host":
        status = 0
        command = HOST
    else:
        status = 0
        command = BROAD if role == "baseline" else CANDIDATE
    if role in {"baseline", "candidate"}:
        artifact_number = 1 if role == "baseline" else 2
    else:
        artifact_number = 3
    return {
        "artifact_identity": (
            None
            if dataset in {"provenance", "host"}
            else f"sha256:{artifact_number:064x}"
        ),
        "command": command,
        "dataset": dataset,
        "duration_seconds": duration,
        "exit_status": status,
        "load_1": load,
        "load_5": load + 0.1,
        "pair_index": index if firmware else None,
        "retry_count": 0,
        "role": role,
        "sha": BASELINE_SHA if role == "baseline" else CANDIDATE_SHA,
        "target_cache": (
            "/tmp/esp32tap-target-"
            + hashlib.sha256(worktree.encode()).hexdigest()[:12]
            + "/qemu"
            if cold
            else None
        ),
        "worktree_path": worktree,
    }


def passing_document() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    samples.extend(
        record("provenance", "missing", i, duration)
        for i, duration in enumerate([0.20, 0.21, 0.22, 0.23, 0.24])
    )
    samples.extend(
        record("provenance", "stale", i, duration)
        for i, duration in enumerate([0.30, 0.31, 0.32, 0.33, 0.34])
    )
    samples.extend(record("host", "candidate", i, 1.0 + i / 100) for i in range(10))
    for i in range(10):
        samples.append(record("firmware_warm", "baseline", i, 40.0 + i / 10))
        samples.append(record("firmware_warm", "candidate", i, 10.0 + i / 10))
    for i in range(3):
        samples.append(record("firmware_cold", "baseline", i, 100.0 + i))
        samples.append(record("firmware_cold", "candidate", i, 80.0 + i))
    return {
        "baseline_command": BROAD,
        "candidate_command": CANDIDATE,
        "samples": samples,
        "version": 1,
    }


def write_document(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="ascii",
    )


def evaluate(document: dict[str, object]) -> dict[str, object]:
    return benchmark_fast.evaluate_document(document)


def test_exact_nearest_rank_p95_and_median() -> None:
    values = [10.0, 1.0, 9.0, 2.0, 8.0, 3.0, 7.0, 4.0, 6.0, 5.0]
    assert benchmark_fast.nearest_rank(values, 0.95) == 10.0
    assert benchmark_fast.sample_median(values) == 5.5


def test_passing_contract_reports_deterministic_summary() -> None:
    result = evaluate(passing_document())
    assert result == {
        "cold_firmware": {
            "baseline_median_seconds": 101.0,
            "baseline_p95_seconds": 102.0,
            "candidate_median_seconds": 81.0,
            "candidate_p95_seconds": 82.0,
        },
        "cold_pairs": 3,
        "firmware": {
            "baseline_median_seconds": 40.45,
            "candidate_median_seconds": 10.45,
            "candidate_p95_seconds": 10.9,
        },
        "host": {"p95_seconds": 1.09},
        "load_band_percent": 20,
        "provenance": {"p95_seconds": 0.34},
        "status": "PASS",
        "version": 1,
        "warm_pairs": 10,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda doc: doc["samples"][0].update(dataset="host", role="candidate"),
            "exactly 10 provenance samples",
        ),
        (
            lambda doc: [
                sample.update(duration_seconds=1.0)
                for sample in doc["samples"]
                if sample["dataset"] == "provenance"
            ],
            "provenance p95",
        ),
        (
            lambda doc: [
                sample.update(duration_seconds=5.0)
                for sample in doc["samples"]
                if sample["dataset"] == "host"
            ],
            "host p95",
        ),
        (
            lambda doc: [
                sample.update(duration_seconds=30.0)
                for sample in doc["samples"]
                if sample["dataset"] == "firmware_warm"
                and sample["role"] == "candidate"
            ],
            "firmware candidate p95",
        ),
        (
            lambda doc: [
                sample.update(duration_seconds=30.0)
                for sample in doc["samples"]
                if sample["dataset"] == "firmware_warm"
                and sample["role"] == "candidate"
            ]
            + [
                sample.update(duration_seconds=40.0)
                for sample in doc["samples"]
                if sample["dataset"] == "firmware_warm"
                and sample["role"] == "baseline"
            ],
            "firmware candidate p95",
        ),
    ],
)
def test_threshold_or_count_failure(
    mutation: object, message: str
) -> None:
    document = passing_document()
    mutation(document)  # type: ignore[operator]
    with pytest.raises(benchmark_fast.ContractError, match=message):
        evaluate(document)


def test_median_must_be_at_least_fifty_percent_below_baseline() -> None:
    document = passing_document()
    for sample in document["samples"]:
        if sample["dataset"] == "firmware_warm" and sample["role"] == "candidate":
            sample["duration_seconds"] = 20.3
    with pytest.raises(benchmark_fast.ContractError, match="50% below"):
        evaluate(document)


def test_every_warm_and_cold_pair_must_be_load_matched() -> None:
    for dataset in ("firmware_warm", "firmware_cold"):
        document = passing_document()
        target = next(
            sample
            for sample in document["samples"]
            if sample["dataset"] == dataset
            and sample["role"] == "candidate"
            and sample["pair_index"] == 0
        )
        target["load_1"] = 1.26
        with pytest.raises(benchmark_fast.ContractError, match="20% load band"):
            evaluate(document)


def test_load_band_is_a_ratio_not_a_larger_run_denominator() -> None:
    document = passing_document()
    target = next(
        sample
        for sample in document["samples"]
        if sample["dataset"] == "firmware_warm"
        and sample["role"] == "candidate"
        and sample["pair_index"] == 0
    )
    target["load_1"] = 1.21
    with pytest.raises(benchmark_fast.ContractError, match="20% load band"):
        evaluate(document)


@pytest.mark.parametrize("field,value", [("exit_status", 7), ("retry_count", 1)])
def test_unexpected_nonzero_or_any_retry_fails(field: str, value: int) -> None:
    document = passing_document()
    sample = next(
        item
        for item in document["samples"]
        if item["dataset"] == "firmware_warm" and item["role"] == "candidate"
    )
    sample[field] = value
    with pytest.raises(benchmark_fast.ContractError, match=field.replace("_", " ")):
        evaluate(document)


def test_recognized_provenance_statuses_are_required() -> None:
    document = passing_document()
    stale = next(sample for sample in document["samples"] if sample["role"] == "stale")
    stale["exit_status"] = 22
    with pytest.raises(benchmark_fast.ContractError, match="recognized stale"):
        evaluate(document)


def test_provenance_and_host_samples_record_the_required_direct_commands() -> None:
    document = passing_document()
    document["samples"][0]["command"] = ["true"]
    with pytest.raises(benchmark_fast.ContractError, match="direct verifier"):
        evaluate(document)

    document = passing_document()
    host = next(sample for sample in document["samples"] if sample["dataset"] == "host")
    host["command"] = ["true"]
    with pytest.raises(benchmark_fast.ContractError, match="host command"):
        evaluate(document)


@pytest.mark.parametrize(
    "evil",
    [
        ["true"],
        [
            "python3",
            "tools/artifact_provenance.py.evil",
            "--repo-root",
            "/tmp/esp32tap-bench-provenance-missing",
            "verify",
            "--kind",
            "qemu-test",
            "/tmp/esp32tap-bench-provenance-missing/"
            + RS
            + "/build_qemu_test",
        ],
    ],
)
def test_provenance_command_rejects_fake_executables(evil: list[str]) -> None:
    document = passing_document()
    document["samples"][0]["command"] = evil
    with pytest.raises(benchmark_fast.ContractError, match="direct verifier"):
        evaluate(document)


def test_provenance_kind_maps_to_one_exact_public_bundle() -> None:
    document = passing_document()
    sample = document["samples"][0]
    sample["command"][-1] += "-evil"
    with pytest.raises(benchmark_fast.ContractError, match="public bundle"):
        evaluate(document)

    document = passing_document()
    sample = document["samples"][0]
    sample["command"][6] = "production"
    with pytest.raises(benchmark_fast.ContractError, match="public bundle"):
        evaluate(document)


def test_host_command_rejects_manifest_suffix_tricks() -> None:
    document = passing_document()
    host = next(sample for sample in document["samples"] if sample["dataset"] == "host")
    host["command"] = [*HOST]
    host["command"][3] += ".evil"
    with pytest.raises(benchmark_fast.ContractError, match="host command"):
        evaluate(document)


def test_commands_are_arrays_and_broad_command_is_exact() -> None:
    document = passing_document()
    document["baseline_command"] = ["bash", "-c", "pytest"]
    with pytest.raises(benchmark_fast.ContractError, match="exact broad reviewer"):
        evaluate(document)

    document = passing_document()
    document["candidate_command"] = "tools/fast.sh"  # type: ignore[assignment]
    with pytest.raises(benchmark_fast.ContractError, match="candidate_command"):
        evaluate(document)

    document = passing_document()
    document["candidate_command"] = ["tools/fast.sh", "--base", "HEAD~1"]
    with pytest.raises(benchmark_fast.ContractError, match="exact fast runner"):
        evaluate(document)


def test_record_commands_must_match_the_declared_firmware_commands() -> None:
    document = passing_document()
    candidate = next(
        sample
        for sample in document["samples"]
        if sample["dataset"] == "firmware_warm" and sample["role"] == "candidate"
    )
    candidate["command"] = ["different"]
    with pytest.raises(benchmark_fast.ContractError, match="declared candidate"):
        evaluate(document)


def test_cold_samples_use_six_distinct_isolated_paths() -> None:
    document = passing_document()
    cold = [
        sample for sample in document["samples"] if sample["dataset"] == "firmware_cold"
    ]
    cold[1]["target_cache"] = cold[0]["target_cache"]
    with pytest.raises(benchmark_fast.ContractError, match="distinct target_cache"):
        evaluate(document)

    document = passing_document()
    cold = [
        sample for sample in document["samples"] if sample["dataset"] == "firmware_cold"
    ]
    cold[1]["worktree_path"] = cold[0]["worktree_path"]
    with pytest.raises(benchmark_fast.ContractError, match="distinct worktree_path"):
        evaluate(document)

    document = passing_document()
    cold = [
        sample for sample in document["samples"] if sample["dataset"] == "firmware_cold"
    ]
    cold[0]["target_cache"] = "/tmp/rustcargo/target"
    with pytest.raises(benchmark_fast.ContractError, match="rustcargo"):
        evaluate(document)

    document = passing_document()
    cold = [
        sample for sample in document["samples"] if sample["dataset"] == "firmware_cold"
    ]
    cold[0]["target_cache"] += "-evil"
    with pytest.raises(benchmark_fast.ContractError, match="physical-worktree cache"):
        evaluate(document)


@pytest.mark.parametrize("dataset", ["firmware_warm", "firmware_cold"])
def test_firmware_records_must_alternate_baseline_candidate_in_pair_order(
    dataset: str,
) -> None:
    document = passing_document()
    indexes = [
        index
        for index, sample in enumerate(document["samples"])
        if sample["dataset"] == dataset
    ]
    document["samples"][indexes[0]], document["samples"][indexes[1]] = (
        document["samples"][indexes[1]],
        document["samples"][indexes[0]],
    )
    with pytest.raises(benchmark_fast.ContractError, match="ordered alternating"):
        evaluate(document)


def test_schema_is_exact_and_scalar_types_are_strict() -> None:
    document = passing_document()
    document["surprise"] = True
    with pytest.raises(benchmark_fast.ContractError, match="top-level keys"):
        evaluate(document)

    document = passing_document()
    document["samples"][0]["surprise"] = True
    with pytest.raises(benchmark_fast.ContractError, match=r"sample .* keys"):
        evaluate(document)

    document = passing_document()
    document["samples"][0]["duration_seconds"] = True
    with pytest.raises(benchmark_fast.ContractError, match="duration_seconds"):
        evaluate(document)


def test_provenance_and_host_artifact_identity_is_exactly_absent() -> None:
    document = passing_document()
    missing = next(sample for sample in document["samples"] if sample["role"] == "missing")
    assert missing["artifact_identity"] is None
    stale = next(sample for sample in document["samples"] if sample["role"] == "stale")
    assert stale["artifact_identity"] is None
    stale["artifact_identity"] = "sha256:" + "3" * 64
    with pytest.raises(benchmark_fast.ContractError, match="artifact_identity"):
        evaluate(document)


def test_each_firmware_role_keeps_one_artifact_identity() -> None:
    document = passing_document()
    candidate = next(
        sample
        for sample in document["samples"]
        if sample["dataset"] == "firmware_warm"
        and sample["role"] == "candidate"
        and sample["pair_index"] == 0
    )
    candidate["artifact_identity"] = "sha256:" + "f" * 64
    with pytest.raises(benchmark_fast.ContractError, match="one artifact identity"):
        evaluate(document)

    document = passing_document()
    cold_candidate = next(
        sample
        for sample in document["samples"]
        if sample["dataset"] == "firmware_cold"
        and sample["role"] == "candidate"
    )
    cold_candidate["artifact_identity"] = "sha256:" + "e" * 64
    with pytest.raises(benchmark_fast.ContractError, match="one artifact identity"):
        evaluate(document)


def test_baseline_and_candidate_sources_are_internally_consistent_and_distinct() -> None:
    document = passing_document()
    cold_baseline = next(
        sample
        for sample in document["samples"]
        if sample["dataset"] == "firmware_cold"
        and sample["role"] == "baseline"
    )
    cold_baseline["sha"] = "f" * 40
    with pytest.raises(benchmark_fast.ContractError, match="one commit SHA"):
        evaluate(document)

    document = passing_document()
    host = next(sample for sample in document["samples"] if sample["dataset"] == "host")
    host["sha"] = "f" * 40
    with pytest.raises(benchmark_fast.ContractError, match="candidate commit SHA"):
        evaluate(document)

    document = passing_document()
    candidate_identity = next(
        sample["artifact_identity"]
        for sample in document["samples"]
        if sample["dataset"] == "firmware_warm"
        and sample["role"] == "candidate"
    )
    for sample in document["samples"]:
        if sample["dataset"].startswith("firmware_") and sample["role"] == "baseline":
            sample["artifact_identity"] = candidate_identity
    with pytest.raises(benchmark_fast.ContractError, match="must differ"):
        evaluate(document)


def test_cli_requires_canonical_bounded_json_and_returns_canonical_summary(
    tmp_path: Path,
) -> None:
    data = tmp_path / "acceptance.json"
    write_document(data, passing_document())
    completed = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "evaluate", os.fspath(data)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    parsed = json.loads(completed.stdout)
    assert parsed["status"] == "PASS"
    assert completed.stdout == (
        json.dumps(parsed, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    )

    data.write_text(json.dumps(passing_document(), indent=2), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "evaluate", os.fspath(data)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "canonical JSON" in completed.stderr


def test_cli_rejects_duplicate_keys_and_oversized_input(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"version":1,"version":1}\n', encoding="ascii")
    completed = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "evaluate", os.fspath(duplicate)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "duplicate JSON key" in completed.stderr

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (benchmark_fast.MAX_INPUT_BYTES + 1))
    completed = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "evaluate", os.fspath(oversized)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "too large" in completed.stderr


def test_cli_rejects_pathological_json_without_a_traceback(tmp_path: Path) -> None:
    pathological = tmp_path / "pathological.json"
    pathological.write_text(
        '{"baseline_command":[],"candidate_command":[],"samples":[],"version":'
        + "9" * 5000
        + "}\n",
        encoding="ascii",
    )
    completed = subprocess.run(
        [sys.executable, os.fspath(SCRIPT), "evaluate", os.fspath(pathological)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "invalid benchmark JSON" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_tool_is_executable_and_never_invokes_a_shell_or_clears_shared_cache() -> None:
    assert stat.S_IMODE(SCRIPT.stat().st_mode) == 0o755
    source = SCRIPT.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "eval(" not in source
    assert "rmtree" not in source
    assert "subprocess" not in source
