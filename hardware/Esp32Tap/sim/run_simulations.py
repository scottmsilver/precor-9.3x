#!/usr/bin/env python3
"""Run and cross-check the Esp32Tap Rev B behavioral SPICE evidence.

The numeric limits live in assertions.json.  This runner deliberately treats
ngspice output as hostile input: every expected measurement must occur once,
must be finite, and must satisfy both its scenario limit and its declared
cross-engine tolerance.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SIM_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SIM_DIR / "assertions.json"
_ASSIGNMENT_START = re.compile(
    r"^[ \t]*([A-Za-z][A-Za-z0-9_]*)[ \t]*=",
)
_MEASURE_ASSIGNMENT_LINE = re.compile(
    r"^[ \t]*([A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*(\S+)(.*)$",
)
_MEASURE_METADATA = re.compile(
    r"[ \t]+([A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*(\S+)",
)
_ALLOWED_MEASURE_METADATA = {"at", "from", "targ", "to", "trig"}
_SPICE_NUMBER = re.compile(
    r"^(?P<number>[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))"
    r"(?:[eE][-+]?\d+)?)"
    r"(?P<suffix>meg|[tgkmunpf])?$",
    re.IGNORECASE,
)
_SPICE_SCALE = {
    "": 1.0,
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}
_LIMIT_KEYS = {
    "min",
    "max",
    "min_exclusive",
    "max_exclusive",
    "expected",
}


class SimulationError(RuntimeError):
    """A reproducibility, parsing, or numeric assertion failure."""


def _json_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SimulationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _as_finite_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationError(f"{description} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SimulationError(f"{description} must be finite")
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and structurally validate the machine-readable assertion file."""

    try:
        manifest = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_without_duplicate_keys,
        )
    except OSError as exc:
        raise SimulationError(f"cannot read assertion manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SimulationError(f"malformed assertion manifest {path}: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise SimulationError("assertion manifest schema_version must be 1")
    if not isinstance(manifest.get("repeat_count"), int) or (
        manifest["repeat_count"] < 1
    ):
        raise SimulationError("repeat_count must be a positive integer")
    engines = manifest.get("engines")
    if not isinstance(engines, dict) or set(engines) != {"host", "docker"}:
        raise SimulationError("engines must define exactly host and docker")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise SimulationError("scenarios must be a non-empty object")

    for scenario_name, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            raise SimulationError(f"{scenario_name}: scenario must be an object")
        deck = scenario.get("deck")
        if not isinstance(deck, str) or not deck.endswith(".cir"):
            raise SimulationError(f"{scenario_name}: deck must name a .cir file")
        assertions = scenario.get("assertions")
        if not isinstance(assertions, dict) or not assertions:
            raise SimulationError(
                f"{scenario_name}: assertions must be a non-empty object"
            )
        for measure_name, limits in assertions.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", measure_name):
                raise SimulationError(
                    f"{scenario_name}: invalid measure name {measure_name!r}"
                )
            if not isinstance(limits, dict):
                raise SimulationError(
                    f"{scenario_name}/{measure_name}: limits must be an object"
                )
            if not isinstance(limits.get("unit"), str) or not limits["unit"]:
                raise SimulationError(
                    f"{scenario_name}/{measure_name}: unit is required"
                )
            for tolerance in ("abs_tolerance", "rel_tolerance"):
                numeric = _as_finite_number(
                    limits.get(tolerance),
                    f"{scenario_name}/{measure_name} {tolerance}",
                )
                if numeric < 0:
                    raise SimulationError(
                        f"{scenario_name}/{measure_name}: "
                        f"{tolerance} cannot be negative"
                    )
            present_limits = _LIMIT_KEYS.intersection(limits)
            if not present_limits:
                raise SimulationError(
                    f"{scenario_name}/{measure_name}: numeric limit is required"
                )
            if "expected" in limits and len(present_limits) != 1:
                raise SimulationError(
                    f"{scenario_name}/{measure_name}: expected cannot be "
                    "combined with range bounds"
                )
            for limit_name in present_limits:
                _as_finite_number(
                    limits[limit_name],
                    f"{scenario_name}/{measure_name} {limit_name}",
                )
            if "expected" in limits:
                tolerance = _as_finite_number(
                    limits.get("assert_tolerance", 0.0),
                    f"{scenario_name}/{measure_name} assert_tolerance",
                )
                if tolerance < 0:
                    raise SimulationError(
                        f"{scenario_name}/{measure_name}: "
                        "assert_tolerance cannot be negative"
                    )

        unsupported = scenario.get("unsupported")
        if not isinstance(unsupported, list) or not unsupported:
            raise SimulationError(
                f"{scenario_name}: unsupported must be a non-empty list"
            )
        claims: set[str] = set()
        for entry in unsupported:
            if not isinstance(entry, dict):
                raise SimulationError(
                    f"{scenario_name}: unsupported entry must be an object"
                )
            claim = entry.get("claim")
            reason = entry.get("reason")
            if not isinstance(claim, str) or not claim:
                raise SimulationError(
                    f"{scenario_name}: unsupported claim is required"
                )
            if not isinstance(reason, str) or not reason:
                raise SimulationError(
                    f"{scenario_name}/{claim}: unsupported reason is required"
                )
            if claim in claims:
                raise SimulationError(
                    f"{scenario_name}: duplicate unsupported claim {claim!r}"
                )
            claims.add(claim)
            if claim in assertions:
                raise SimulationError(
                    f"{scenario_name}: assertion/unsupported overlap for {claim}"
                )
            measure = entry.get("measure")
            if measure is not None:
                if not isinstance(measure, str) or not re.fullmatch(
                    r"[a-z][a-z0-9_]*",
                    measure,
                ):
                    raise SimulationError(
                        f"{scenario_name}/{claim}: invalid unsupported measure"
                    )
                if measure in assertions:
                    raise SimulationError(
                        f"{scenario_name}: assertion/unsupported overlap "
                        f"for {measure}"
                    )

    rows = manifest.get("truth_table")
    if not isinstance(rows, list) or len(rows) != 16:
        raise SimulationError("truth table must contain exactly 16 rows")
    safety = scenarios.get("safety_truth_table", {}).get("assertions", {})
    combinations: set[tuple[int, int, int, int]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SimulationError(f"truth table row {index} must be an object")
        bits: list[int] = []
        for field in ("rail_3v3", "tread_ok", "relay_cmd", "tx_enable"):
            value = row.get(field)
            if type(value) is not int or value not in (0, 1):
                raise SimulationError(
                    f"truth table row {index}: {field} must be 0 or 1"
                )
            bits.append(value)
        combination = tuple(bits)
        if combination in combinations:
            raise SimulationError(
                f"truth table has duplicate input row {combination}"
            )
        combinations.add(combination)
        rail, tread, relay_cmd, tx_enable = map(bool, combination)
        expected_outputs = {
            "relay_gate": rail and tread and relay_cmd,
            "tx_gate": rail and tread and tx_enable,
        }
        for output, expected_on in expected_outputs.items():
            output_value = row.get(output)
            if type(output_value) is not int or output_value not in (0, 1):
                raise SimulationError(
                    f"truth table row {index}: {output} must be 0 or 1"
                )
            if bool(output_value) != expected_on:
                raise SimulationError(
                    f"truth table row {index}: {output} violates gate equation"
                )
            measure_field = (
                "relay_measure" if output == "relay_gate" else "tx_measure"
            )
            measure_name = row.get(measure_field)
            if not isinstance(measure_name, str) or measure_name not in safety:
                raise SimulationError(
                    f"truth table row {index}: missing safety assertion "
                    f"for {measure_field}"
                )
            limits = safety[measure_name]
            expected_voltage = 3.3 if expected_on else 0.0
            if limits.get("expected") != expected_voltage:
                raise SimulationError(
                    f"truth table assertion drift for {measure_name}: "
                    f"expected {expected_voltage} V"
                )

    return manifest


def _parse_spice_number(token: str, measure_name: str) -> float:
    lowered = token.lower()
    if lowered in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
        raise SimulationError(f"non-finite measure {measure_name}: {token}")
    match = _SPICE_NUMBER.fullmatch(token)
    if match is None:
        raise SimulationError(f"malformed measure {measure_name}: {token}")
    suffix = (match.group("suffix") or "").lower()
    value = float(match.group("number")) * _SPICE_SCALE[suffix]
    if not math.isfinite(value):
        raise SimulationError(f"non-finite measure {measure_name}: {token}")
    return value


def parse_measure_log(
    log: str,
    expected_names: Iterable[str],
) -> dict[str, float]:
    """Extract exactly one finite assignment for every expected measure."""

    expected = set(expected_names)
    found: dict[str, float] = {}
    for line in log.splitlines():
        start = _ASSIGNMENT_START.match(line)
        if start is None:
            continue
        name = start.group(1)
        if name not in expected:
            continue
        if name in found:
            raise SimulationError(f"duplicate measure {name}")
        match = _MEASURE_ASSIGNMENT_LINE.fullmatch(line)
        if match is None:
            raise SimulationError(f"malformed measure {name}: {line.strip()}")
        parsed_name, token, trailing = match.groups()
        if parsed_name != name:
            raise SimulationError(f"malformed measure {name}: {line.strip()}")
        found[name] = _parse_spice_number(token, name)

        position = 0
        for metadata in _MEASURE_METADATA.finditer(trailing):
            if metadata.start() != position:
                raise SimulationError(
                    f"malformed measure {name}: {line.strip()}"
                )
            metadata_name, metadata_token = metadata.groups()
            if metadata_name.lower() not in _ALLOWED_MEASURE_METADATA:
                raise SimulationError(
                    f"malformed measure {name}: {line.strip()}"
                )
            _parse_spice_number(
                metadata_token,
                f"{name} metadata {metadata_name}",
            )
            position = metadata.end()
        if trailing[position:].strip():
            raise SimulationError(
                f"malformed measure {name}: {line.strip()}"
            )

    missing = sorted(expected - found.keys())
    if missing:
        raise SimulationError(f"missing measure(s): {', '.join(missing)}")
    return found


def parse_engine_major(output: str) -> int:
    """Return the unique ngspice major from a version banner."""

    matches = {
        int(value)
        for value in re.findall(
            r"(?:ngspice\s*-\s*|ngspice[^\n]*?\brelease\s+)(\d+)",
            output,
            flags=re.IGNORECASE,
        )
    }
    if not matches:
        raise SimulationError("ngspice version is missing from engine banner")
    if len(matches) != 1:
        raise SimulationError(
            "ambiguous ngspice version banner: "
            + ", ".join(str(value) for value in sorted(matches))
        )
    return next(iter(matches))


def require_engine_major(label: str, output: str, expected: int) -> int:
    actual = parse_engine_major(output)
    if actual != expected:
        raise SimulationError(
            f"{label}: expected ngspice major {expected}, got {actual}"
        )
    return actual


def assert_measure_in_range(
    name: str,
    value: float,
    limits: Mapping[str, Any],
) -> None:
    if not math.isfinite(value):
        raise SimulationError(f"{name}: non-finite measured value {value}")

    comparisons = (
        ("min", lambda measured, bound: measured >= bound, ">="),
        ("max", lambda measured, bound: measured <= bound, "<="),
        ("min_exclusive", lambda measured, bound: measured > bound, ">"),
        ("max_exclusive", lambda measured, bound: measured < bound, "<"),
    )
    for key, predicate, operator in comparisons:
        if key not in limits:
            continue
        bound = _as_finite_number(limits[key], f"{name} {key}")
        if not predicate(value, bound):
            raise SimulationError(
                f"{name}: {value:.12g} does not satisfy {operator} "
                f"{bound:.12g} {limits.get('unit', '')}".rstrip()
            )

    if "expected" in limits:
        expected = _as_finite_number(limits["expected"], f"{name} expected")
        tolerance = _as_finite_number(
            limits.get("assert_tolerance", 0.0),
            f"{name} assert_tolerance",
        )
        if abs(value - expected) > tolerance:
            raise SimulationError(
                f"{name}: {value:.12g} differs from expected "
                f"{expected:.12g} by more than {tolerance:.12g} "
                f"{limits.get('unit', '')}".rstrip()
            )


def compare_engine_measures(
    scenario: str,
    host: Mapping[str, float],
    docker: Mapping[str, float],
    specs: Mapping[str, Mapping[str, Any]],
) -> None:
    if set(host) != set(docker) or set(host) != set(specs):
        raise SimulationError(
            f"{scenario}: cross-engine measure set mismatch "
            f"(host={sorted(host)}, docker={sorted(docker)}, "
            f"manifest={sorted(specs)})"
        )
    for name, limits in specs.items():
        host_value = host[name]
        docker_value = docker[name]
        if not math.isfinite(host_value) or not math.isfinite(docker_value):
            raise SimulationError(
                f"{scenario}: cross-engine non-finite measure {name}"
            )
        absolute = _as_finite_number(
            limits["abs_tolerance"],
            f"{scenario}/{name} abs_tolerance",
        )
        relative = _as_finite_number(
            limits["rel_tolerance"],
            f"{scenario}/{name} rel_tolerance",
        )
        if not math.isclose(
            host_value,
            docker_value,
            rel_tol=relative,
            abs_tol=absolute,
        ):
            raise SimulationError(
                f"{scenario}: cross-engine {name} differs: "
                f"host={host_value:.12g}, docker={docker_value:.12g}, "
                f"abs_tol={absolute:.12g}, rel_tol={relative:.12g}"
            )


def render_unsupported(
    scenario: str,
    entries: Sequence[Mapping[str, str]],
) -> list[str]:
    return [
        f"{scenario}: {entry['claim']}: UNSUPPORTED ({entry['reason']})"
        for entry in entries
    ]


def build_engine_commands(
    sim_dir: Path,
    host_ngspice: Path,
    docker_image: str,
    deck: str,
) -> tuple[list[str], list[str]]:
    host = [str(host_ngspice), "-n", "-b", deck]
    docker = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--mount",
        f"type=bind,src={sim_dir.resolve()},dst=/sim,readonly",
        "-w",
        "/sim",
        docker_image,
        "ngspice",
        "-n",
        "-b",
        deck,
    ]
    return host, docker


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    description: str,
    timeout_s: float = 45.0,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SimulationError(f"{description} failed to execute: {exc}") from exc
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        tail = "\n".join(output.splitlines()[-30:])
        raise SimulationError(
            f"{description} exited {completed.returncode}\n{tail}"
        )
    return output


def _version_commands(
    sim_dir: Path,
    host_ngspice: Path,
    docker_image: str,
) -> tuple[list[str], list[str]]:
    return (
        [str(host_ngspice), "--version"],
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            docker_image,
            "ngspice",
            "--version",
        ],
    )


def _require_docker_image_id(
    image: str,
    expected_id: str,
    sim_dir: Path,
) -> None:
    output = _run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        cwd=sim_dir,
        description=f"inspect Docker image {image}",
    ).strip()
    if output != expected_id:
        raise SimulationError(
            f"Docker image {image}: expected ID {expected_id}, got {output}"
        )


def _unsupported_measure_names(
    scenario: Mapping[str, Any],
) -> set[str]:
    return {
        entry["measure"]
        for entry in scenario["unsupported"]
        if "measure" in entry
    }


def _simulate_once(
    command: Sequence[str],
    *,
    sim_dir: Path,
    scenario_name: str,
    engine_name: str,
    repeat: int,
    expected_names: set[str],
) -> dict[str, float]:
    output = _run(
        command,
        cwd=sim_dir,
        description=f"{engine_name}/{scenario_name}/run-{repeat}",
    )
    return parse_measure_log(output, expected_names)


def run_all(
    manifest: Mapping[str, Any],
    *,
    sim_dir: Path,
    host_ngspice: Path,
    docker_image: str,
    repeat_count: int,
) -> dict[str, dict[str, dict[str, float]]]:
    engines = manifest["engines"]
    host_version_command, docker_version_command = _version_commands(
        sim_dir,
        host_ngspice,
        engines["docker"]["image_id"],
    )
    host_banner = _run(
        host_version_command,
        cwd=sim_dir,
        description="host ngspice version",
    )
    _require_docker_image_id(
        docker_image,
        engines["docker"]["image_id"],
        sim_dir,
    )
    docker_banner = _run(
        docker_version_command,
        cwd=sim_dir,
        description="Docker ngspice version",
    )
    host_major = require_engine_major(
        "host",
        host_banner,
        engines["host"]["major"],
    )
    docker_major = require_engine_major(
        "docker",
        docker_banner,
        engines["docker"]["major"],
    )
    print(f"host engine: ngspice-{host_major}")
    print(
        f"docker engine: ngspice-{docker_major} "
        f"({engines['docker']['image_id']})"
    )

    results: dict[str, dict[str, dict[str, float]]] = {}
    deterministic_reference: dict[
        tuple[str, str], dict[str, float]
    ] = {}
    for repeat in range(1, repeat_count + 1):
        print(f"repeat {repeat}/{repeat_count}")
        for scenario_name, scenario in manifest["scenarios"].items():
            deck = scenario["deck"]
            deck_path = sim_dir / deck
            if not deck_path.is_file():
                raise SimulationError(
                    f"{scenario_name}: deck does not exist: {deck_path}"
                )
            assertions = scenario["assertions"]
            supported_names = set(assertions)
            expected_names = supported_names | _unsupported_measure_names(
                scenario
            )
            host_command, docker_command = build_engine_commands(
                sim_dir,
                host_ngspice,
                engines["docker"]["image_id"],
                deck,
            )
            host_all = _simulate_once(
                host_command,
                sim_dir=sim_dir,
                scenario_name=scenario_name,
                engine_name="host",
                repeat=repeat,
                expected_names=expected_names,
            )
            docker_all = _simulate_once(
                docker_command,
                sim_dir=sim_dir,
                scenario_name=scenario_name,
                engine_name="docker",
                repeat=repeat,
                expected_names=expected_names,
            )
            host = {name: host_all[name] for name in supported_names}
            docker = {name: docker_all[name] for name in supported_names}
            for name, limits in assertions.items():
                assert_measure_in_range(
                    f"{scenario_name}/{name}/host",
                    host[name],
                    limits,
                )
                assert_measure_in_range(
                    f"{scenario_name}/{name}/docker",
                    docker[name],
                    limits,
                )
            compare_engine_measures(
                scenario_name,
                host,
                docker,
                assertions,
            )

            for engine_name, measured in (("host", host), ("docker", docker)):
                key = (scenario_name, engine_name)
                previous = deterministic_reference.get(key)
                if previous is None:
                    deterministic_reference[key] = measured
                else:
                    compare_engine_measures(
                        f"{scenario_name}/{engine_name}/repeat",
                        previous,
                        measured,
                        assertions,
                    )
            results[scenario_name] = {"host": host, "docker": docker}
            values = ", ".join(
                f"{name}={host[name]:.7g} {assertions[name]['unit']}"
                for name in sorted(host)
            )
            print(f"{scenario_name}: PASS ({values})")

    for scenario_name, scenario in manifest["scenarios"].items():
        for line in render_unsupported(
            scenario_name,
            scenario["unsupported"],
        ):
            print(line)
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Esp32Tap Rev B behavioral decks on pinned host and Docker "
            "ngspice engines"
        )
    )
    parser.add_argument(
        "--host-ngspice",
        type=Path,
        default=Path("/usr/bin/ngspice"),
    )
    parser.add_argument(
        "--docker-image",
        default="ngspice-cached:latest",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
        help="override the manifest repeat count",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        repeat_count = (
            manifest["repeat_count"] if args.repeat is None else args.repeat
        )
        if repeat_count < 1:
            raise SimulationError("--repeat must be a positive integer")
        if str(args.host_ngspice) != manifest["engines"]["host"]["executable"]:
            raise SimulationError(
                "host executable differs from assertions.json: "
                f"{args.host_ngspice}"
            )
        if args.docker_image != manifest["engines"]["docker"]["image"]:
            raise SimulationError(
                "Docker image differs from assertions.json: "
                f"{args.docker_image}"
            )
        run_all(
            manifest,
            sim_dir=SIM_DIR,
            host_ngspice=args.host_ngspice,
            docker_image=args.docker_image,
            repeat_count=repeat_count,
        )
    except SimulationError as exc:
        print(f"SIMULATION FAILURE: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
