from __future__ import annotations

import json
import fcntl
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
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
if not all(isinstance(name, str) for name in _names):
    _names = ()
_workers = _value.get("workers", {})
if not isinstance(_workers, dict):
    _workers = {}
_POLICY_ORDER = tuple(name for name in _names if name != "broad")
_POLICIES = {}
for index, name in enumerate(_names):
    _POLICIES[name] = Policy(
        name,
        tuple(tuple(argv) for argv in _value.get("host_argv", ())) if index == 0 else (),
        tuple(tuple(argv) for argv in _value.get("qemu_argv", ())) if index == 0 else (),
        tuple(_value.get("artifact_kinds", ())) if index == 0 else (),
        _workers.get("qemu", 0) if index == 0 else 0,
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
import fcntl, json, os, subprocess, sys, time
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
if "--fast-gate-report" in command and os.environ.get("BEFORE_GATE_READY"):
    open(os.environ["BEFORE_GATE_READY"], "w").close()
    deadline = time.monotonic() + 10
    while not os.path.exists(os.environ["BEFORE_GATE_CONTINUE"]):
        if time.monotonic() >= deadline:
            raise SystemExit(23)
        time.sleep(0.01)
lock = None
if os.environ.get("FAKE_PROVENANCE_LOCK"):
    lock = open(os.environ["FAKE_PROVENANCE_LOCK"], "a+")
    fcntl.flock(lock, fcntl.LOCK_SH)
if "--fast-identity" in command:
    if os.environ.get("REAL_IDENTITY_READER"):
        raise SystemExit(subprocess.run(command, env=os.environ).returncode)
    kind = command[command.index("--fast-identity") + 1]
    identities = json.loads(os.environ["FAKE_IDENTITIES"])
    print(f"{kind} {identities[kind]}")
    raise SystemExit(0)
try:
    raise SystemExit(subprocess.run(command, env=os.environ).returncode)
finally:
    if lock is not None:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
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
import json, os, signal, subprocess, sys, time
with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(["gate", *sys.argv[1:], os.getcwd()]) + "\\n")
if "--orphan" in sys.argv:
    pid_path = sys.argv[sys.argv.index("--orphan") + 1]
    code = '''
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1], "w").write(str(os.getpid()))
while True:
    time.sleep(1)
'''
    subprocess.Popen([sys.executable, "-c", code, pid_path])
    deadline = time.monotonic() + 5
    while not os.path.exists(pid_path):
        if time.monotonic() >= deadline:
            raise SystemExit(23)
        time.sleep(0.01)
    raise SystemExit(0)
if "--barrier" in sys.argv:
    ready = sys.argv[sys.argv.index("--barrier") + 1]
    go = sys.argv[sys.argv.index("--barrier") + 2]
    open(ready, "w").close()
    deadline = time.monotonic() + 10
    while not os.path.exists(go):
        if time.monotonic() >= deadline:
            raise SystemExit(23)
        time.sleep(0.01)
if "--consume-bundle" in sys.argv:
    bundle = sys.argv[sys.argv.index("--consume-bundle") + 1]
    identity = os.path.basename(os.readlink(bundle))
    with open(os.environ["EVENT_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(["consume", identity]) + "\\n")
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
    _write_bundle(root, "production", IDENTITY["production"])
    _write_bundle(root, "qemu-test", IDENTITY["qemu-test"])
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


def _write_bundle(root: Path, kind: str, identity: str) -> Path:
    store = "prod" if kind == "production" else "qemu"
    public = "build" if kind == "production" else "build_qemu_test"
    generation = root / RS / ".artifacts" / store / identity
    generation.mkdir(parents=True, exist_ok=True)
    (generation / "artifact-manifest.json").write_text(
        json.dumps(
            {"kind": kind, "manifest_sha256": identity},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    link = root / RS / public
    temporary = link.with_name(f".{public}.test-link")
    if os.path.lexists(temporary):
        temporary.unlink()
    os.symlink(f".artifacts/{store}/{identity}", temporary)
    os.replace(temporary, link)
    return link


def _wait_path(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {path}")


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paths", [["nested"]]),
        ("paths", [{"nested": "dict"}]),
        ("paths", [1]),
        ("policies", [["unhashable"]]),
        ("policies", [{"unhashable": "dict"}]),
        ("policies", [1]),
        ("artifact_kinds", [["unhashable"]]),
        ("artifact_kinds", [{"unhashable": "dict"}]),
        ("artifact_kinds", [1]),
        ("host_argv", [{"nested": "command"}]),
        ("host_argv", [[["nested"]]]),
        ("host_argv", [[{"nested": "dict"}]]),
        ("host_argv", [[1]]),
        ("qemu_argv", [{"nested": "command"}]),
        ("qemu_argv", [[["nested"]]]),
        ("qemu_argv", [[{"nested": "dict"}]]),
        ("qemu_argv", [[1]]),
        ("workers", [["host", 1]]),
        ("workers", ["host", "qemu"]),
        ("workers", {"host": [], "qemu": 0}),
        ("workers", {"host": {}, "qemu": 0}),
        ("workers", {"host": "1", "qemu": 0}),
        ("broad_reason", ["nested"]),
        ("broad_reason", {"nested": "dict"}),
        ("broad_reason", 1),
    ],
    ids=lambda value: type(value).__name__,
)
def test_nested_or_wrong_typed_schema_values_fail_broad_without_partial_execution(
    fake_repo: tuple[Path, dict[str, str]],
    field: str,
    value: object,
) -> None:
    malformed = _selection()
    malformed[field] = value

    completed = _run(fake_repo, malformed)

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stdout
    assert "Traceback" not in completed.stderr
    assert [event[0] for event in _events(fake_repo)] == ["selector", "sweep"]


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
    _write_bundle(root, "qemu-test", identity)
    selected = _selection(
        policies=["program-control"],
        qemu=[["fake-gate", "qemu"]],
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


def test_natural_leader_exit_kills_and_reaps_term_ignoring_grandchild(
    fake_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    grandchild_pid = tmp_path / "grandchild.pid"
    selection = _selection(
        policies=["program-host"],
        host=[["fake-gate", "host", "--orphan", os.fspath(grandchild_pid)]],
    )
    pid = None
    try:
        completed = _run(fake_repo, selection)
        _wait_path(grandchild_pid)
        pid = int(grandchild_pid.read_text(encoding="ascii"))

        assert completed.returncode == 0, completed.stderr
        assert not Path(f"/proc/{pid}").exists()
    finally:
        if pid is not None and Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_gate_reports_generation_published_after_preflight_before_gate_lease(
    fake_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    root, base_environment = fake_repo
    old_identity = "3" * 64
    new_identity = "4" * 64
    bundle = _write_bundle(root, "qemu-test", old_identity)
    _write_bundle(root, "qemu-test", new_identity)
    _write_bundle(root, "qemu-test", old_identity)
    ready = tmp_path / "before-gate.ready"
    go = tmp_path / "before-gate.continue"
    selection = _selection(
        policies=["program-control"],
        qemu=[
            [
                "fake-gate",
                "qemu",
                "--consume-bundle",
                os.fspath(bundle),
            ]
        ],
        artifacts=["qemu"],
    )
    environment = dict(base_environment)
    environment.update(
        {
            "SELECTOR_OUTPUT": json.dumps(
                selection, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            "BEFORE_GATE_READY": os.fspath(ready),
            "BEFORE_GATE_CONTINUE": os.fspath(go),
        }
    )
    process = subprocess.Popen(
        ["bash", os.fspath(root / RS / "tools/fast.sh")],
        cwd=root.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_path(ready)
    _write_bundle(root, "qemu-test", new_identity)
    go.touch()
    stdout, stderr = process.communicate(timeout=20)

    assert process.returncode == 0, stderr
    assert f"artifact=qemu-test id={new_identity}" in stdout
    assert ["consume", new_identity] in _events(fake_repo)
    assert ["consume", old_identity] not in _events(fake_repo)


def test_publisher_cannot_swap_between_gate_identity_report_and_consumption(
    fake_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    root, base_environment = fake_repo
    old_identity = "5" * 64
    new_identity = "6" * 64
    bundle = _write_bundle(root, "qemu-test", old_identity)
    _write_bundle(root, "qemu-test", new_identity)
    _write_bundle(root, "qemu-test", old_identity)
    gate_ready = tmp_path / "gate.ready"
    gate_go = tmp_path / "gate.continue"
    publisher_done = tmp_path / "publisher.done"
    lock_path = tmp_path / "provenance.lock"
    lock_path.touch()
    selection = _selection(
        policies=["program-control"],
        qemu=[
            [
                "fake-gate",
                "qemu",
                "--barrier",
                os.fspath(gate_ready),
                os.fspath(gate_go),
                "--consume-bundle",
                os.fspath(bundle),
            ]
        ],
        artifacts=["qemu"],
    )
    environment = dict(base_environment)
    environment.update(
        {
            "SELECTOR_OUTPUT": json.dumps(
                selection, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            "FAKE_PROVENANCE_LOCK": os.fspath(lock_path),
        }
    )
    process = subprocess.Popen(
        ["bash", os.fspath(root / RS / "tools/fast.sh")],
        cwd=root.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_path(gate_ready)

    def publish() -> None:
        with lock_path.open("r+") as descriptor:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _write_bundle(root, "qemu-test", new_identity)
            publisher_done.touch()

    publisher = threading.Thread(target=publish)
    publisher.start()
    time.sleep(0.2)
    assert not publisher_done.exists()
    assert Path(os.readlink(bundle)).name == old_identity
    gate_go.touch()
    stdout, stderr = process.communicate(timeout=20)
    publisher.join(timeout=5)

    assert process.returncode == 0, stderr
    assert not publisher.is_alive()
    assert f"artifact=qemu-test id={old_identity}" in stdout
    assert ["consume", old_identity] in _events(fake_repo)
    assert publisher_done.exists()
    assert Path(os.readlink(bundle)).name == new_identity


def test_signal_during_success_log_cleanup_overrides_zero_after_cleanup(
    fake_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    root, base_environment = fake_repo
    ready = tmp_path / "cleanup.ready"
    go = tmp_path / "cleanup.continue"
    custom = tmp_path / "site"
    custom.mkdir()
    (custom / "sitecustomize.py").write_text(
        """
import os
from pathlib import Path
import shutil
import time

original = shutil.rmtree
def barrier(path, *args, **kwargs):
    if Path(path).name.startswith("esp32tap-fast."):
        Path(os.environ["CLEANUP_READY"]).touch()
        deadline = time.monotonic() + 10
        while not Path(os.environ["CLEANUP_CONTINUE"]).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("cleanup barrier timed out")
            time.sleep(0.01)
    return original(path, *args, **kwargs)
shutil.rmtree = barrier
""",
        encoding="utf-8",
    )
    selection = _selection(policies=["program-host"])
    environment = dict(base_environment)
    environment.update(
        {
            "SELECTOR_OUTPUT": json.dumps(
                selection, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            "PYTHONPATH": os.fspath(custom),
            "CLEANUP_READY": os.fspath(ready),
            "CLEANUP_CONTINUE": os.fspath(go),
        }
    )
    process = subprocess.Popen(
        ["bash", os.fspath(root / RS / "tools/fast.sh")],
        cwd=root.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_path(ready)
    process.send_signal(signal.SIGTERM)
    go.touch()
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    log_roots = {
        Path(value).parent
        for value in re.findall(r'log="(/tmp/esp32tap-fast\.[^"]+)"', stdout)
    }
    assert log_roots
    assert all(not path.exists() for path in log_roots)
