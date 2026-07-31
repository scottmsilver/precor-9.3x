#!/usr/bin/env bash
# Provenance-safe, impact-aware ESP32Tap inner-loop runner.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
exec /usr/bin/python3 - "$HERE" "$@" <<'PY'
from __future__ import annotations

import ctypes
import json
import importlib.util
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath


SELECTOR_LIMIT = 4 * 1024 * 1024
LOG_LIMIT = 64 * 1024 * 1024
SELECTOR_TIMEOUT = 30.0
HOST_TIMEOUT = 600.0
VERIFY_TIMEOUT = 60.0
BUILD_TIMEOUT = 3600.0
QEMU_TIMEOUT = 1800.0
MAX_GATES = 64
MAX_ARGV = 256
MAX_TEXT = 64 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_KEYS = {
    "artifact_kinds",
    "broad_reason",
    "host_argv",
    "paths",
    "policies",
    "qemu_argv",
    "version",
    "workers",
}
KIND_MAP = {
    "production": ("production", "build", "prod"),
    "qemu": ("qemu-test", "build_qemu_test", "qemu"),
}

tools = Path(sys.argv[1]).resolve(strict=True)
selector_arguments = sys.argv[2:]
esp32_rs = tools.parent
current_child: subprocess.Popen[bytes] | None = None
received_signal: int | None = None
keep_logs = False
WATCHED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
original_handlers = {
    watched: signal.getsignal(watched) for watched in WATCHED_SIGNALS
}


def clean_environment() -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    result["LC_ALL"] = "C"
    result["LANG"] = "C"
    return result


environment = clean_environment()


def repo_root() -> Path:
    completed = subprocess.run(
        ["git", "-C", os.fspath(esp32_rs), "rev-parse", "--show-toplevel"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=10,
        check=True,
    )
    raw = completed.stdout
    if (
        not raw.endswith(b"\n")
        or b"\0" in raw
        or b"\n" in raw[:-1]
        or len(raw) > 1024 * 1024
    ):
        raise RuntimeError("Git returned a malformed repository root")
    root = Path(os.fsdecode(raw[:-1])).resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("Git repository root is not a directory")
    return root


try:
    root = repo_root()
except (OSError, UnicodeError, subprocess.SubprocessError, RuntimeError) as exc:
    print(f"fast: cannot discover repository root: {exc}", file=sys.stderr)
    raise SystemExit(23)


log_root = Path(tempfile.mkdtemp(prefix="esp32tap-fast.", dir="/tmp"))
log_info = log_root.lstat()
if (
    not stat.S_ISDIR(log_info.st_mode)
    or log_info.st_uid != os.geteuid()
    or stat.S_IMODE(log_info.st_mode) != 0o700
    or log_root.resolve(strict=True) != log_root
):
    print("fast: private log directory validation failed", file=sys.stderr)
    raise SystemExit(23)

# Adopt orphaned descendants so command cleanup can reap an entire process
# group even when its original leader exits before its children.
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
    error = ctypes.get_errno()
    print(f"fast: cannot become child subreaper: errno {error}", file=sys.stderr)
    raise SystemExit(23)


def handle_signal(signum: int, _frame: object) -> None:
    global received_signal
    if received_signal is None:
        received_signal = signum
    child = current_child
    if child is not None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass


for watched in WATCHED_SIGNALS:
    signal.signal(watched, handle_signal)


def safe_log(label: str, suffix: str = "log") -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,48}", label):
        raise RuntimeError("unsafe internal log label")
    path = log_root / f"{label}.{suffix}"
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    os.close(descriptor)
    return path


def terminate(process: subprocess.Popen[bytes], signum: int = signal.SIGTERM) -> None:
    process_group = process.pid
    term_delivered = False
    try:
        os.killpg(process_group, signum)
        term_delivered = True
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + 1.0
    while term_delivered and time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        process.poll()
        time.sleep(0.02)

    # Always issue the non-catchable group kill after the TERM grace. The
    # leader may already be reaped while an inherited descendant remains.
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait()
    except ChildProcessError:
        pass

    reap_deadline = time.monotonic() + 1.0
    while True:
        reaped = False
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pid = 0
            if pid <= 0:
                break
            reaped = True
        try:
            os.killpg(process_group, 0)
            group_exists = True
        except ProcessLookupError:
            group_exists = False
        if not group_exists and not reaped:
            break
        if time.monotonic() >= reap_deadline:
            break
        time.sleep(0.01)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    label: str,
    timeout: float,
    capture_limit: int = LOG_LIMIT,
    extra_environment: dict[str, str] | None = None,
) -> tuple[int, bytes, Path]:
    global current_child
    if (
        not argv
        or not argv[0]
        or len(argv) > MAX_ARGV
        or any(not isinstance(value, str) or "\0" in value for value in argv)
    ):
        raise RuntimeError("unsafe internal command argv")
    log = safe_log(label)
    command_environment = dict(environment)
    if extra_environment:
        command_environment.update(extra_environment)
    started = time.monotonic()
    print(
        f"FAST gate={label} status=RUN seconds=0.000 "
        f"log={json.dumps(os.fspath(log))}",
        flush=True,
    )
    descriptor = os.open(log, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        current_child = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            env=command_environment,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        os.close(descriptor)
        current_child = None
        print(f"fast: cannot execute {argv[0]!r}: {exc}", file=sys.stderr)
        return 23, b"", log
    os.close(descriptor)
    deadline = started + timeout
    status: int
    try:
        while current_child.poll() is None:
            if received_signal is not None:
                terminate(current_child, received_signal)
                break
            try:
                size = log.stat().st_size
            except OSError:
                terminate(current_child)
                status = 23
                break
            if size > capture_limit:
                terminate(current_child)
                status = 23
                print(
                    f"fast: {label} exceeded its {capture_limit}-byte log limit",
                    file=sys.stderr,
                )
                break
            if time.monotonic() >= deadline:
                terminate(current_child)
                status = 23
                print(f"fast: {label} timed out", file=sys.stderr)
                break
            time.sleep(0.02)
        else:
            return_code = current_child.returncode
            status = return_code if return_code is not None and return_code >= 0 else 23
        if received_signal is not None:
            status = 128 + received_signal
        elif "status" not in locals():
            return_code = current_child.returncode
            status = return_code if return_code is not None and return_code >= 0 else 23
    finally:
        if current_child is not None:
            terminate(current_child)
        current_child = None
    elapsed = time.monotonic() - started
    try:
        data = log.read_bytes()
    except OSError:
        data = b""
        status = 23
    if len(data) > capture_limit:
        data = data[:capture_limit]
        status = 23
    state = "PASS" if status == 0 else "FAIL"
    print(
        f"FAST gate={label} status={state} exit={status} "
        f"seconds={elapsed:.3f} log={json.dumps(os.fspath(log))}",
        flush=True,
    )
    return status, data, log


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def validate_text(value: object, label: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value)
        or len(value) > MAX_TEXT
        or "\0" in value
    ):
        raise ValueError(f"invalid {label}")
    return value


def validate_commands(value: object, label: str) -> list[list[str]]:
    if not isinstance(value, list) or len(value) > MAX_GATES:
        raise ValueError(f"invalid {label}")
    commands: list[list[str]] = []
    for item in value:
        if not isinstance(item, list) or not item or len(item) > MAX_ARGV:
            raise ValueError(f"invalid {label} argv")
        argv = [
            validate_text(argument, f"{label} argument", empty=index > 0)
            for index, argument in enumerate(item)
        ]
        commands.append(argv)
    return commands


def validate_policy_contract(value: dict[str, object]) -> None:
    module_name = "_esp32tap_fast_select_contract"
    specification = importlib.util.spec_from_file_location(
        module_name, tools / "fast_select.py"
    )
    if specification is None or specification.loader is None:
        raise ValueError("selector policy module cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException as exc:
        raise ValueError(f"selector policy module failed to load: {exc}") from exc
    finally:
        sys.modules.pop(module_name, None)
    try:
        table = module._POLICIES
        order = tuple(module._POLICY_ORDER)
        names = tuple(value["policies"])
        policies = tuple(table[name] for name in names)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("selector names an undeclared policy") from exc
    if names == ("broad",):
        expected_names = names
    else:
        expected_names = tuple(name for name in order if name in names)
    if names != expected_names:
        raise ValueError("selector policies are not in declared canonical order")

    def dedupe(commands: object) -> list[list[str]]:
        result: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for policy in policies:
            for command in getattr(policy, commands):
                exact = tuple(command)
                if exact not in seen:
                    seen.add(exact)
                    result.append(list(exact))
        return result

    expected_host = dedupe("host_argv")
    expected_qemu = dedupe("qemu_argv")
    expected_artifacts = [
        kind
        for kind in ("production", "qemu")
        if any(kind in policy.artifact_kinds for policy in policies)
    ]
    expected_workers = {
        "host": 1 if expected_host else 0,
        "qemu": max((policy.qemu_workers for policy in policies), default=0),
    }
    if (
        value["host_argv"] != expected_host
        or value["qemu_argv"] != expected_qemu
        or value["artifact_kinds"] != expected_artifacts
        or value["workers"] != expected_workers
    ):
        raise ValueError("selector argv/artifacts differ from declared policies")


def validate_selection(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"malformed selector JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or len(value) != len(EXPECTED_KEYS)
        or any(not isinstance(key, str) for key in value)
        or sorted(value) != sorted(EXPECTED_KEYS)
    ):
        raise ValueError("selector fields do not match schema")
    canonical = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    )
    if text != canonical:
        raise ValueError("selector JSON is not canonical")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported selector version")
    paths = value["paths"]
    if not isinstance(paths, list) or not paths or len(paths) > 4096:
        raise ValueError("selector paths are invalid")
    previous: bytes | None = None
    seen_paths: set[str] = set()
    for item in paths:
        path = validate_text(item, "selector path")
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or pure.as_posix() != path
            or any(part in ("", ".", "..") for part in pure.parts)
            or path in seen_paths
        ):
            raise ValueError("selector path is unsafe")
        encoded = os.fsencode(path)
        if previous is not None and encoded <= previous:
            raise ValueError("selector paths are not canonically ordered")
        previous = encoded
        seen_paths.add(path)
    policies = value["policies"]
    if (
        not isinstance(policies, list)
        or not policies
        or len(policies) > 16
    ):
        raise ValueError("selector policies are invalid")
    validated_policies: list[str] = []
    for policy in policies:
        validated_policies.append(validate_text(policy, "policy"))
    if len(set(validated_policies)) != len(validated_policies):
        raise ValueError("selector policies contain duplicates")
    policies = validated_policies
    value["policies"] = policies
    host = validate_commands(value["host_argv"], "host")
    qemu = validate_commands(value["qemu_argv"], "qemu")
    artifacts = value["artifact_kinds"]
    if not isinstance(artifacts, list) or len(artifacts) > len(KIND_MAP):
        raise ValueError("artifact kinds are invalid")
    validated_artifacts = [
        validate_text(item, "artifact kind") for item in artifacts
    ]
    if validated_artifacts != [
        item
        for item in ("production", "qemu")
        if item in validated_artifacts
    ]:
        raise ValueError("artifact kinds are invalid")
    artifacts = validated_artifacts
    value["artifact_kinds"] = artifacts
    workers = value["workers"]
    if (
        not isinstance(workers, dict)
        or len(workers) != 2
        or any(not isinstance(key, str) for key in workers)
        or sorted(workers) != ["host", "qemu"]
    ):
        raise ValueError("worker fields are invalid")
    if any(type(item) is not int or item < 0 or item > 64 for item in workers.values()):
        raise ValueError("worker counts are invalid")
    reason = value["broad_reason"]
    if reason is None:
        if "broad" in policies:
            raise ValueError("focused selector includes broad policy")
        if qemu and "qemu" not in artifacts:
            raise ValueError("QEMU selection lacks qemu provenance")
    else:
        validate_text(reason, "broad reason")
        if policies != ["broad"]:
            raise ValueError("broad reason lacks exact broad policy")
    value["host_argv"] = host
    value["qemu_argv"] = qemu
    validate_policy_contract(value)
    return value


def selector() -> tuple[int, dict[str, object] | None, str]:
    status, output, _ = run_command(
        [sys.executable, os.fspath(tools / "fast_select.py"), *selector_arguments],
        cwd=root,
        label="selector",
        timeout=SELECTOR_TIMEOUT,
        capture_limit=SELECTOR_LIMIT,
    )
    if status == 2:
        try:
            diagnostic = output.decode("utf-8", "replace").strip()
        except Exception:
            diagnostic = "selector rejected the requested change set"
        return 2, None, diagnostic
    if status != 0:
        return 0, None, f"selector exited {status}"
    try:
        return 0, validate_selection(output), ""
    except Exception as exc:
        return 0, None, str(exc)


def verification_argv(kind: str) -> list[str]:
    provenance_kind, public_name, _only = KIND_MAP[kind]
    return [
        sys.executable,
        os.fspath(tools / "artifact_provenance.py"),
        "--repo-root",
        os.fspath(root),
        "verify",
        "--kind",
        provenance_kind,
        os.fspath(esp32_rs / public_name),
    ]


GATE_WRAPPER = r'''
# FAST_GATE_IDENTITY_WRAPPER
import json, os, re, stat, sys

report_index = sys.argv.index("--fast-gate-report")
bundles_index = sys.argv.index("--fast-gate-bundles")
separator = sys.argv.index("--", bundles_index + 2)
report = sys.argv[report_index + 1]
raw_bundles = sys.argv[bundles_index + 1]
command = sys.argv[separator + 1:]
if not command or not command[0]:
    raise SystemExit(22)
try:
    bundles = json.loads(raw_bundles)
except json.JSONDecodeError:
    raise SystemExit(22)
if (
    not isinstance(bundles, dict)
    or not bundles
    or any(
        not isinstance(kind, str)
        or not isinstance(bundle, str)
        or kind not in ("production", "qemu-test")
        for kind, bundle in bundles.items()
    )
):
    raise SystemExit(22)
canonical_bundles = json.dumps(
    bundles, sort_keys=True, separators=(",", ":"), ensure_ascii=True
)
if raw_bundles != canonical_bundles:
    raise SystemExit(22)

identities = {}
for kind, bundle in bundles.items():
    path = os.path.join(bundle, "artifact-manifest.json")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 1024 * 1024
        ):
            raise SystemExit(22)
        chunks = []
        remaining = info.st_size + 1
        while remaining:
            block = os.read(fd, min(64 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if len(raw) != info.st_size:
        raise SystemExit(22)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise SystemExit(22)
    if not isinstance(value, dict):
        raise SystemExit(22)
    identity = value.get("manifest_sha256")
    if (
        value.get("kind") != kind
        or not isinstance(identity, str)
        or re.fullmatch(r"[0-9a-f]{64}", identity) is None
        or os.path.basename(os.readlink(bundle)) != identity
    ):
        raise SystemExit(22)
    identities[kind] = identity

encoded = (
    json.dumps(identities, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    + "\n"
).encode("ascii")
flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
report_fd = os.open(report, flags)
try:
    opened = os.fstat(report_fd)
    lexical = os.lstat(report)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != os.geteuid()
        or (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino)
    ):
        raise SystemExit(22)
    view = memoryview(encoded)
    while view:
        written = os.write(report_fd, view)
        if written <= 0:
            raise SystemExit(22)
        view = view[written:]
    os.fsync(report_fd)
finally:
    os.close(report_fd)
os.execvp(command[0], command)
'''


def leased_prefix(kinds: list[str]) -> list[str]:
    base = [
        sys.executable,
        os.fspath(tools / "artifact_provenance.py"),
        "--repo-root",
        os.fspath(root),
    ]
    mapped = [KIND_MAP[kind][0] for kind in kinds]
    if len(mapped) == 1:
        return [*base, "exec", "--kind", mapped[0], "--"]
    result = [*base, "exec-many"]
    for kind in mapped:
        result.extend(("--kind", kind))
    result.append("--")
    return result


def read_gate_identities(
    report: Path, expected_kinds: list[str]
) -> dict[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(report, flags)
    try:
        opened = os.fstat(descriptor)
        lexical = report.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(lexical.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or (opened.st_dev, opened.st_ino)
            != (lexical.st_dev, lexical.st_ino)
            or opened.st_size > 1024 * 1024
        ):
            raise ValueError("gate identity report is not one safe regular file")
        chunks: list[bytes] = []
        remaining = opened.st_size + 1
        while remaining:
            block = os.read(descriptor, min(64 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) != opened.st_size:
        raise ValueError("gate identity report changed while reading")
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("gate identity report is malformed") from exc
    expected = [KIND_MAP[kind][0] for kind in expected_kinds]
    if (
        not isinstance(value, dict)
        or sorted(value) != sorted(expected)
        or any(
            not isinstance(identity, str) or not HEX64.fullmatch(identity)
            for identity in value.values()
        )
        or text
        != json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        + "\n"
    ):
        raise ValueError("gate identity report does not match leased kinds")
    return value


def finish(status: int) -> int:
    global keep_logs
    if received_signal is not None:
        status = 128 + received_signal
        keep_logs = False
    if status != 0 and received_signal is None:
        keep_logs = True
        print(f"fast: failed logs retained at {log_root}", file=sys.stderr)
    return status


def main() -> int:
    selected_status, selected, broad_detail = selector()
    if received_signal is not None:
        return finish(128 + received_signal)
    if selected_status == 2:
        print(
            broad_detail or "fast-select rejected the requested change set",
            file=sys.stderr,
        )
        return finish(2)
    if selected is None:
        reason = broad_detail or "selector failure"
        print(f"FAST broad fallback reason={json.dumps(reason)}", flush=True)
        status, _output, _log = run_command(
            ["bash", "tools/sweep.sh"],
            cwd=esp32_rs,
            label="broad-sweep",
            timeout=BUILD_TIMEOUT,
            extra_environment={
                "ONLY": "both",
                "PROFILE": "",
                "NET_FEATURE": "",
            },
        )
        return finish(status)

    policies = selected["policies"]
    assert isinstance(policies, list)
    print(f"FAST policies={','.join(policies)}", flush=True)
    broad_reason = selected["broad_reason"]
    if broad_reason is not None:
        print(
            f"FAST broad fallback reason={json.dumps(broad_reason)}",
            flush=True,
        )
        status, _output, _log = run_command(
            ["bash", "tools/sweep.sh"],
            cwd=esp32_rs,
            label="broad-sweep",
            timeout=BUILD_TIMEOUT,
            extra_environment={
                "ONLY": "both",
                "PROFILE": "",
                "NET_FEATURE": "",
            },
        )
        return finish(status)

    host_commands = selected["host_argv"]
    assert isinstance(host_commands, list)
    for index, argv in enumerate(host_commands, 1):
        status, _output, _log = run_command(
            argv,
            cwd=root,
            label=f"host-{index}",
            timeout=HOST_TIMEOUT,
        )
        if status != 0:
            return finish(status)

    kinds = selected["artifact_kinds"]
    assert isinstance(kinds, list)
    initial: dict[str, int] = {}
    for kind in kinds:
        status, _output, _log = run_command(
            verification_argv(kind),
            cwd=root,
            label=f"verify-{kind}",
            timeout=VERIFY_TIMEOUT,
        )
        initial[kind] = status
    severe = [
        initial[kind]
        for kind in kinds
        if initial[kind] not in (0, 20, 21)
    ]
    if severe:
        return finish(severe[0])

    rebuild = [kind for kind in kinds if initial[kind] in (20, 21)]
    if rebuild:
        only = (
            "both"
            if len(rebuild) == 2
            else KIND_MAP[rebuild[0]][2]
        )
        status, _output, _log = run_command(
            ["bash", "tools/build.sh"],
            cwd=esp32_rs,
            label="build",
            timeout=BUILD_TIMEOUT,
            extra_environment={
                "ONLY": only,
                "PROFILE": "",
                "NET_FEATURE": "",
            },
        )
        if status != 0:
            return finish(status)
        for kind in kinds:
            status, _output, _log = run_command(
                verification_argv(kind),
                cwd=root,
                label=f"recheck-{kind}",
                timeout=VERIFY_TIMEOUT,
            )
            if status != 0:
                return finish(status)

    qemu_commands = selected["qemu_argv"]
    assert isinstance(qemu_commands, list)
    prefix = leased_prefix(kinds) if qemu_commands else []
    bundles = {
        KIND_MAP[kind][0]: os.fspath(esp32_rs / KIND_MAP[kind][1])
        for kind in kinds
    }
    encoded_bundles = json.dumps(
        bundles, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    for index, argv in enumerate(qemu_commands, 1):
        report = safe_log(f"qemu-{index}", "identity")
        status, _output, _log = run_command(
            [
                *prefix,
                sys.executable,
                "-c",
                GATE_WRAPPER,
                "--fast-gate-report",
                os.fspath(report),
                "--fast-gate-bundles",
                encoded_bundles,
                "--",
                *argv,
            ],
            cwd=root,
            label=f"qemu-{index}",
            timeout=QEMU_TIMEOUT,
        )
        if status != 0:
            return finish(status)
        try:
            identities = read_gate_identities(report, kinds)
        except (OSError, ValueError) as exc:
            print(f"fast: invalid leased gate identity: {exc}", file=sys.stderr)
            return finish(22)
        for provenance_kind, identity in identities.items():
            print(
                f"FAST artifact={provenance_kind} id={identity} "
                f"gate=qemu-{index}",
                flush=True,
            )
    print("FAST ALL GREEN", flush=True)
    return finish(0)


try:
    exit_status = main()
except (OSError, RuntimeError, ValueError) as exc:
    print(f"fast: internal error: {exc}", file=sys.stderr)
    exit_status = finish(23)
finally:
    if not keep_logs:
        try:
            info = log_root.lstat()
            if (
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == os.geteuid()
                and log_root.parent == Path("/tmp")
                and log_root.name.startswith("esp32tap-fast.")
                and log_root.resolve(strict=True) == log_root
            ):
                shutil.rmtree(log_root)
        except OSError:
            pass
    # Keep the latch installed through cleanup. Block watched signals before
    # restoring the caller's handlers so a cleanup-time delivery cannot be
    # lost between the final status check and process exit.
    signal.pthread_sigmask(signal.SIG_BLOCK, WATCHED_SIGNALS)
    pending = signal.sigpending()
    if received_signal is None:
        received_signal = next(
            (watched for watched in WATCHED_SIGNALS if watched in pending),
            None,
        )
    for watched, original in original_handlers.items():
        signal.signal(watched, original)
    if received_signal is not None:
        exit_status = 128 + received_signal

raise SystemExit(exit_status)
PY
