from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest


RUNNER = Path(__file__).with_name("fast.sh")
RS = "hardware/Esp32Tap/firmware/esp32_rs"
IDENTITY = {
    "production": "1" * 64,
    "qemu-test": "2" * 64,
}


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _selection(
    *,
    policies: list[str] | None = None,
    host: list[list[str]] | None = None,
    qemu: list[list[str]] | None = None,
    artifacts: list[str] | None = None,
    broad_reason: str | None = None,
) -> dict[str, object]:
    return {
        "artifact_kinds": artifacts or [],
        "broad_reason": broad_reason,
        "host_argv": host or [],
        "paths": [f"{RS}/program_core/src/state.rs"],
        "policies": policies or ["program-host"],
        "qemu_argv": qemu or [],
        "version": 1,
        "workers": {
            "host": 1 if host else 0,
            "qemu": 4 if qemu else 0,
        },
    }


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo with spaces [literal]"
    tools = root / RS / "tools"
    tools.mkdir(parents=True)
    shutil.copyfile(RUNNER, tools / "fast.sh")
    (tools / "fast.sh").chmod(0o755)
    subprocess.run(["git", "-C", os.fspath(root), "init", "-q"], check=True)

    _write_executable(
        tools / "fast_select.py",
        """#!/usr/bin/env python3
import json, os, sys, time
from dataclasses import dataclass

@dataclass(frozen=True)
class Policy:
    name: str
    host_argv: tuple
    qemu_argv: tuple
    artifact_kinds: tuple
    qemu_workers: int

def configured():
    raw = os.environ.get("POLICY_OUTPUT", os.environ.get("SELECTOR_OUTPUT", ""))
    try:
        return json.loads(raw)
    except Exception:
        return {}

_value = configured()
if not isinstance(_value, dict):
    _value = {}
_names = tuple(_value.get("policies", ()))
_POLICY_ORDER = tuple(name for name in _names if name != "broad")
_POLICIES = {}
for index, name in enumerate(_names):
    _POLICIES[name] = Policy(
        name,
        tuple(tuple(argv) for argv in _value.get("host_argv", ())) if index == 0 else (),
        tuple(tuple(argv) for argv in _value.get("qemu_argv", ())) if index == 0 else (),
        tuple(_value.get("artifact_kinds", ())) if index == 0 else (),
        _value.get("workers", {}).get("qemu", 0) if index == 0 else 0,
    )

if __name__ == "__main__":
    with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(["selector", *sys.argv[1:]]) + "\\n")
    if os.environ.get("SELECTOR_SLEEP"):
        time.sleep(float(os.environ["SELECTOR_SLEEP"]))
    if os.environ.get("SELECTOR_BYTES"):
        sys.stdout.write("x" * int(os.environ["SELECTOR_BYTES"]))
    else:
        sys.stdout.write(os.environ.get("SELECTOR_OUTPUT", ""))
    sys.stderr.write(os.environ.get("SELECTOR_STDERR", ""))
    raise SystemExit(int(os.environ.get("SELECTOR_EXIT", "0")))
""",
    )
    _write_executable(
        tools / "artifact_provenance.py",
        """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["provenance", *args]) + "\\n")
operation = next((item for item in ("verify", "exec", "exec-many") if item in args), "")
if operation == "verify":
    kind = args[args.index("--kind") + 1]
    configured = json.loads(os.environ.get("VERIFY_CODES", "{}"))
    state_path = os.environ["VERIFY_STATE"]
    try:
        state = json.loads(open(state_path, encoding="utf-8").read())
    except FileNotFoundError:
        state = {}
    index = state.get(kind, 0)
    values = configured.get(kind, [0])
    code = values[min(index, len(values) - 1)]
    state[kind] = index + 1
    open(state_path, "w", encoding="utf-8").write(json.dumps(state))
    raise SystemExit(code)
separator = args.index("--")
command = args[separator + 1:]
if "--fast-identity" in command:
    if os.environ.get("REAL_IDENTITY_READER"):
        raise SystemExit(subprocess.run(command, env=os.environ).returncode)
    kind = command[command.index("--fast-identity") + 1]
    identities = json.loads(os.environ["FAKE_IDENTITIES"])
    print(f"{kind} {identities[kind]}")
    raise SystemExit(0)
raise SystemExit(subprocess.run(command, env=os.environ).returncode)
""",
    )
    _write_executable(
        tools / "build.sh",
        """#!/usr/bin/env bash
exec /usr/bin/python3 - <<'PY'
import json, os
with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["build", os.environ.get("ONLY")]) + "\\n")
raise SystemExit(int(os.environ.get("BUILD_EXIT", "0")))
PY
""",
    )
    _write_executable(
        tools / "sweep.sh",
        """#!/usr/bin/env bash
exec /usr/bin/python3 - "$0" <<'PY'
import json, os, sys
with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps([
        "sweep", sys.argv[1], os.getcwd(), os.environ.get("ONLY")
    ]) + "\\n")
raise SystemExit(int(os.environ.get("SWEEP_EXIT", "0")))
PY
""",
    )
    fake_bin = root / "fake bin"
    _write_executable(
        fake_bin / "fake-gate",
        """#!/usr/bin/env python3
import json, os, sys, time
with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["gate", *sys.argv[1:], os.getcwd()]) + "\\n")
if "--sleep" in sys.argv:
    time.sleep(60)
fail = os.environ.get("FAIL_TOKEN")
raise SystemExit(int(os.environ.get("GATE_EXIT", "9")) if fail in sys.argv else 0)
""",
    )

    event_log = tmp_path / "events.jsonl"
    environment = dict(os.environ)
    environment.update(
        {
            "EVENT_LOG": os.fspath(event_log),
            "FAKE_IDENTITIES": json.dumps(IDENTITY),
            "PATH": os.fspath(fake_bin) + os.pathsep + environment["PATH"],
            "SELECTOR_OUTPUT": json.dumps(
                _selection(), sort_keys=True, separators=(",", ":")
            )
            + "\n",
            "VERIFY_CODES": "{}",
            "VERIFY_STATE": os.fspath(tmp_path / "verify-state.json"),
        }
    )
    return root, environment


def _run(
    fixture: tuple[Path, dict[str, str]],
    selection: dict[str, object] | str,
    *args: str,
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    root, base_environment = fixture
    environment = dict(base_environment)
    environment.update(extra)
    payload = (
        selection if isinstance(selection, str) else
        json.dumps(selection, sort_keys=True, separators=(",", ":")) + "\n"
    )
    if len(payload) > 1024 * 1024:
        environment["SELECTOR_OUTPUT"] = ""
        environment["SELECTOR_BYTES"] = str(len(payload))
    else:
        environment["SELECTOR_OUTPUT"] = payload
    return subprocess.run(
        ["bash", os.fspath(root / RS / "tools/fast.sh"), *args],
        cwd=root.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def _events(fixture: tuple[Path, dict[str, str]]) -> list[list[object]]:
    _root, environment = fixture
    path = Path(environment["EVENT_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _operations(events: list[list[object]]) -> list[str]:
    result = []
    for event in events:
        if event[0] == "gate":
            result.append(f"gate:{event[1]}")
        elif event[0] == "provenance":
            result.append(f"provenance:{next(x for x in ('verify', 'exec', 'exec-many') if x in event)}")
        else:
            result.append(str(event[0]))
    return result


def test_runner_exists_is_executable_and_parses_as_bash() -> None:
    assert RUNNER.is_file()
    assert stat.S_IMODE(RUNNER.stat().st_mode) == 0o755
    subprocess.run(["bash", "-n", os.fspath(RUNNER)], check=True)


def test_host_runs_before_provenance_and_valid_bundle_is_reused_under_lock(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["program-control"],
        host=[["fake-gate", "host"]],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )

    completed = _run(fake_repo, selection, "--base", "HEAD~1")

    assert completed.returncode == 0, completed.stderr
    events = _events(fake_repo)
    assert _operations(events) == [
        "selector",
        "gate:host",
        "provenance:verify",
        "provenance:exec",
        "provenance:exec",
        "gate:qemu",
    ]
    assert events[0] == ["selector", "--base", "HEAD~1"]
    assert not any(event[0] == "build" for event in events)
    qemu_exec = next(
        event for event in events if event[0] == "provenance" and "fake-gate" in event
    )
    assert "exec" in qemu_exec
    assert qemu_exec[qemu_exec.index("--kind") + 1] == "qemu-test"


def test_docs_run_host_gate_without_provenance_or_build(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["docs"],
        host=[["fake-gate", "docs"]],
    )

    completed = _run(fake_repo, selection)

    assert completed.returncode == 0
    assert _operations(_events(fake_repo)) == ["selector", "gate:docs"]


@pytest.mark.parametrize("code", [20, 21])
def test_only_missing_or_stale_rebuilds_once_then_rechecks(
    fake_repo: tuple[Path, dict[str, str]], code: int
) -> None:
    selection = _selection(
        policies=["program-control"],
        host=[["fake-gate", "host"]],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )
    codes = json.dumps({"qemu-test": [code, 0]})

    completed = _run(fake_repo, selection, VERIFY_CODES=codes)

    assert completed.returncode == 0, completed.stderr
    events = _events(fake_repo)
    assert [event for event in events if event[0] == "build"] == [["build", "qemu"]]
    verifies = [
        event for event in events if event[0] == "provenance" and "verify" in event
    ]
    assert len(verifies) == 2
    assert _operations(events).index("gate:host") < _operations(events).index(
        "provenance:verify"
    )
    assert _operations(events).index("build") < _operations(events).index("gate:qemu")


@pytest.mark.parametrize("code", [22, 23, 7])
def test_invalid_internal_or_unknown_verify_failure_never_rebuilds_or_runs_qemu(
    fake_repo: tuple[Path, dict[str, str]], code: int
) -> None:
    selection = _selection(
        policies=["program-control"],
        host=[["fake-gate", "host"]],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )

    completed = _run(
        fake_repo,
        selection,
        VERIFY_CODES=json.dumps({"qemu-test": [code]}),
    )

    assert completed.returncode == code
    assert "build" not in _operations(_events(fake_repo))
    assert "gate:qemu" not in _operations(_events(fake_repo))


def test_build_or_recheck_failure_stops_before_qemu(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["program-control"],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )
    first = _run(
        fake_repo,
        selection,
        VERIFY_CODES=json.dumps({"qemu-test": [21]}),
        BUILD_EXIT="8",
    )
    assert first.returncode == 8
    assert "gate:qemu" not in _operations(_events(fake_repo))

    Path(fake_repo[1]["EVENT_LOG"]).unlink()
    Path(fake_repo[1]["VERIFY_STATE"]).unlink()
    second = _run(
        fake_repo,
        selection,
        VERIFY_CODES=json.dumps({"qemu-test": [20, 21]}),
    )
    assert second.returncode == 21
    assert _operations(_events(fake_repo)).count("build") == 1
    assert "gate:qemu" not in _operations(_events(fake_repo))


def test_two_stale_kinds_use_one_both_build_and_each_is_rechecked(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["mixed"],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["production", "qemu"],
    )

    completed = _run(
        fake_repo,
        selection,
        VERIFY_CODES=json.dumps(
            {"production": [20, 0], "qemu-test": [21, 0]}
        ),
    )

    assert completed.returncode == 0, completed.stderr
    events = _events(fake_repo)
    assert [event for event in events if event[0] == "build"] == [["build", "both"]]
    assert sum(event[0] == "provenance" and "verify" in event for event in events) == 4
    qemu_exec = next(
        event for event in events if event[0] == "provenance" and "fake-gate" in event
    )
    assert "exec-many" in qemu_exec
    assert [qemu_exec[index + 1] for index, item in enumerate(qemu_exec) if item == "--kind"] == [
        "production",
        "qemu-test",
    ]


def test_host_failure_prevents_provenance_build_and_qemu(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["program-control"],
        host=[["fake-gate", "host-fail"]],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )

    completed = _run(fake_repo, selection, FAIL_TOKEN="host-fail")

    assert completed.returncode == 9
    assert _operations(_events(fake_repo)) == ["selector", "gate:host-fail"]
    assert "retained" in completed.stderr


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        "[]",
        json.dumps({"version": 1}),
        json.dumps({**_selection(), "extra": True}),
        json.dumps({**_selection(), "host_argv": "fake-gate host"}),
        json.dumps({**_selection(), "artifact_kinds": ["unknown"]}),
        "x" * (4 * 1024 * 1024 + 1),
    ],
    ids=[
        "broken-json",
        "not-object",
        "missing-fields",
        "extra-field",
        "argv-not-array",
        "unknown-artifact",
        "oversized",
    ],
)
def test_malformed_or_oversized_selector_output_fails_broad(
    fake_repo: tuple[Path, dict[str, str]], payload: str
) -> None:
    completed = _run(fake_repo, payload)

    assert completed.returncode == 0, completed.stderr
    events = _events(fake_repo)
    assert [event[0] for event in events] == ["selector", "sweep"]
    assert events[1][1:] == [
        "tools/sweep.sh",
        os.fspath(fake_repo[0] / RS),
        "both",
    ]
    assert "broad fallback" in completed.stdout


def test_selector_process_failure_and_valid_broad_both_invoke_exact_sweep(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    failed = _run(fake_repo, "", SELECTOR_EXIT="4")
    assert failed.returncode == 0
    assert [event[0] for event in _events(fake_repo)] == ["selector", "sweep"]


def test_selector_user_error_exit_two_is_propagated_without_sweep(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    completed = _run(
        fake_repo,
        "",
        SELECTOR_EXIT="2",
        SELECTOR_STDERR="fast-select: no authoritative Git changes\n",
    )

    assert completed.returncode == 2
    assert [event[0] for event in _events(fake_repo)] == ["selector"]
    assert "no authoritative Git changes" in completed.stderr

    Path(fake_repo[1]["EVENT_LOG"]).unlink()
    broad = _selection(
        policies=["broad"],
        host=[["env", "-C", RS, "bash", "tools/sweep.sh"]],
        artifacts=["production", "qemu"],
        broad_reason="unknown-path",
    )
    completed = _run(fake_repo, broad)
    assert completed.returncode == 0
    assert [event[0] for event in _events(fake_repo)] == ["selector", "sweep"]


def test_broad_sweep_forces_normal_both_build_despite_hostile_environment(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    broad = _selection(
        policies=["broad"],
        host=[["env", "-C", RS, "bash", "tools/sweep.sh"]],
        artifacts=["production", "qemu"],
        broad_reason="unknown-path",
    )

    completed = _run(fake_repo, broad, ONLY="prod")

    assert completed.returncode == 0
    sweep = next(event for event in _events(fake_repo) if event[0] == "sweep")
    assert sweep[-1] == "both"


def test_exact_argv_survives_hostile_paths_arguments_and_git_environment(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    hostile = ["semi;colon", "$(touch nope)", "line\nbreak", "*", "--", ""]
    selection = _selection(
        policies=["program-host"],
        host=[["fake-gate", "host", *hostile]],
    )

    completed = _run(
        fake_repo,
        selection,
        GIT_DIR="/definitely/hostile",
        GIT_WORK_TREE="/",
        TMPDIR="/definitely/hostile",
    )

    assert completed.returncode == 0, completed.stderr
    gate = next(event for event in _events(fake_repo) if event[0] == "gate")
    assert gate[1:-1] == ["host", *hostile]
    assert gate[-1] == os.fspath(fake_repo[0])
    assert not (fake_repo[0].parent / "nope").exists()


def test_selector_argv_mismatch_with_declared_policy_fails_broad(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selected = _selection(
        policies=["program-host"],
        host=[["fake-gate", "host"]],
    )
    declared = _selection(
        policies=["program-host"],
        host=[["fake-gate", "different"]],
    )

    completed = _run(
        fake_repo,
        selected,
        POLICY_OUTPUT=json.dumps(
            declared, sort_keys=True, separators=(",", ":")
        )
        + "\n",
    )

    assert completed.returncode == 0
    assert [event[0] for event in _events(fake_repo)] == ["selector", "sweep"]


def test_leased_identity_reader_uses_real_manifest_bytes(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    root, _environment = fake_repo
    identity = IDENTITY["qemu-test"]
    generation = root / RS / ".artifacts/qemu" / identity
    generation.mkdir(parents=True)
    (generation / "artifact-manifest.json").write_text(
        json.dumps(
            {
                "kind": "qemu-test",
                "manifest_sha256": identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    os.symlink(
        f".artifacts/qemu/{identity}",
        root / RS / "build_qemu_test",
    )
    selected = _selection(
        policies=["program-control"],
        artifacts=["qemu"],
    )

    completed = _run(
        fake_repo,
        selected,
        REAL_IDENTITY_READER="1",
    )

    assert completed.returncode == 0, completed.stderr
    assert f"artifact=qemu-test id={identity}" in completed.stdout


def test_summary_prints_policies_gate_times_logs_and_immutable_identities(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["program-control"],
        host=[["fake-gate", "host"]],
        qemu=[["fake-gate", "qemu"]],
        artifacts=["qemu"],
    )

    completed = _run(fake_repo, selection)

    assert completed.returncode == 0, completed.stderr
    assert "policies=program-control" in completed.stdout
    assert "gate=host-1" in completed.stdout
    assert "gate=qemu-1" in completed.stdout
    assert "seconds=" in completed.stdout
    assert "log=" in completed.stdout
    assert f"artifact=qemu-test id={IDENTITY['qemu-test']}" in completed.stdout


def test_signal_terminates_child_group_and_removes_private_log_directory(
    fake_repo: tuple[Path, dict[str, str]],
) -> None:
    selection = _selection(
        policies=["program-host"],
        host=[["fake-gate", "host", "--sleep"]],
    )
    root, environment = fake_repo
    environment = dict(environment)
    environment["SELECTOR_OUTPUT"] = (
        json.dumps(selection, sort_keys=True, separators=(",", ":")) + "\n"
    )
    process = subprocess.Popen(
        ["bash", os.fspath(root / RS / "tools/fast.sh")],
        cwd=root.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if "gate:host" in _operations(_events(fake_repo)):
            break
        time.sleep(0.02)
    else:
        process.kill()
        pytest.fail("host child did not start")

    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    log_paths = [
        Path(value)
        for value in re.findall(r'log="(/tmp/esp32tap-fast\.[^"]+)"', stdout)
    ]
    assert log_paths
    assert all(not path.parent.exists() for path in log_paths)
