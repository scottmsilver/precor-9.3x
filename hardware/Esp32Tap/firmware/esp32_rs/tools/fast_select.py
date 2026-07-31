#!/usr/bin/env python3
"""Select the smallest conservative Esp32Tap verification policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping


RS = "hardware/Esp32Tap/firmware/esp32_rs"
_RS_PATH = PurePosixPath(RS)
_MAX_GIT_OUTPUT = 16 * 1024 * 1024
_MAX_UNTRACKED_SOURCE = 1024 * 1024
_GIT_TIMEOUT_SECONDS = 10.0
_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".rs"})
_ROUTE_SYMBOL = re.compile(
    rb"(?:httpd_register_uri_handler|httpd_uri_t|register_[A-Za-z0-9_]+_handlers)"
)


class GitFailure(RuntimeError):
    """Git did not provide a trustworthy, bounded selector input."""


class SelectionError(ValueError):
    """The caller did not provide enough safe information to select gates."""


@dataclass(frozen=True)
class Policy:
    name: str
    host_argv: tuple[tuple[str, ...], ...]
    qemu_argv: tuple[tuple[str, ...], ...]
    artifact_kinds: tuple[str, ...]
    qemu_workers: int


@dataclass(frozen=True)
class Selection:
    paths: tuple[str, ...]
    policies: tuple[str, ...]
    host_argv: tuple[tuple[str, ...], ...]
    qemu_argv: tuple[tuple[str, ...], ...]
    workers: Mapping[str, int]
    artifact_kinds: tuple[str, ...]
    broad_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_kinds": list(self.artifact_kinds),
            "broad_reason": self.broad_reason,
            "host_argv": [list(argv) for argv in self.host_argv],
            "paths": list(self.paths),
            "policies": list(self.policies),
            "qemu_argv": [list(argv) for argv in self.qemu_argv],
            "version": 1,
            "workers": dict(self.workers),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def _cargo(crate: str) -> tuple[str, ...]:
    return (
        "cargo",
        "test",
        "--manifest-path",
        f"{RS}/{crate}/Cargo.toml",
        "-q",
    )


def _qemu(*args: str) -> tuple[str, ...]:
    return (
        "env",
        "-C",
        f"{RS}/tools/qemu_scenarios",
        "python3",
        "-m",
        "pytest",
        *args,
    )


_PROGRAM_HOST = _cargo("program_core")
_SAFETY_HOST = _cargo("safety_core")
_DIFFTEST_HOST = _cargo("difftest")
_POLICIES = {
    "program-host": Policy("program-host", (_PROGRAM_HOST,), (), (), 0),
    "program-control": Policy(
        "program-control",
        (_PROGRAM_HOST, _SAFETY_HOST, _DIFFTEST_HOST),
        (
            _qemu("test_program.py", "-q", "-n", "4"),
            _qemu(
                "test_reviewer_attacks.py",
                "-q",
                "-n",
                "3",
                "-k",
                "console_takeover",
            ),
        ),
        ("qemu",),
        4,
    ),
    "request-api": Policy(
        "request-api",
        (_cargo("reqbudget"),),
        (
            _qemu("test_http_entry.py", "-q"),
            _qemu(
                "test_reviewer_attacks.py",
                "-q",
                "-n",
                "3",
                "-k",
                "body_policy or unread_declared_body",
            ),
        ),
        ("qemu",),
        3,
    ),
    "safety": Policy(
        "safety",
        (_SAFETY_HOST, _DIFFTEST_HOST),
        (
            _qemu("test_normal_exit.py", "-q"),
            _qemu("test_reviewer_attacks.py", "-q", "-n", "3"),
        ),
        ("qemu",),
        3,
    ),
    "ble": Policy(
        "ble",
        (_cargo("ble_core"),),
        (
            _qemu("test_ble_degraded.py", "-q", "-n", "3"),
            _qemu("test_ble_control_point.py", "-q", "-n", "4"),
        ),
        ("qemu",),
        4,
    ),
    "coach": Policy(
        "coach",
        (_cargo("coach_core"),),
        (_qemu("test_coach.py", "-q", "-n", "4"),),
        ("qemu",),
        4,
    ),
    "storage": Policy(
        "storage",
        (),
        (
            _qemu("test_records.py", "-q", "-n", "4"),
            _qemu("test_store_persistence.py", "-q"),
            _qemu("test_store_power_loss.py", "-q", "-n", "4"),
        ),
        ("qemu",),
        4,
    ),
    "docs": Policy(
        "docs",
        (
            (
                "python3",
                "-m",
                "pytest",
                f"{RS}/tools/test_source_layout.py",
                "-q",
            ),
        ),
        (),
        (),
        0,
    ),
    "broad": Policy(
        "broad",
        (("env", "-C", RS, "bash", "tools/sweep.sh"),),
        (),
        ("production", "qemu"),
        0,
    ),
}
_POLICY_ORDER = (
    "program-host",
    "program-control",
    "request-api",
    "safety",
    "ble",
    "coach",
    "storage",
    "docs",
)
_PROGRAM_CONTROL = frozenset(
    {
        f"{RS}/esp32tap/src/net/program.rs",
        f"{RS}/esp32tap/src/tasks/interval_executor.rs",
        f"{RS}/esp32tap/src/control.rs",
    }
)
_COACH_EXACT = f"{RS}/esp32tap/src/net/coach.rs"
_STORAGE_EXACT = frozenset(
    {
        f"{RS}/esp32tap/src/net/records.rs",
        f"{RS}/esp32tap/src/net/store.rs",
    }
)


def _under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path != prefix and prefix in path.parents


def _normalise_path(value: str) -> str:
    if "\0" in value:
        raise ValueError("NUL in repository path")
    try:
        if os.fsdecode(os.fsencode(value)) != value:
            raise ValueError(f"unsafe repository-relative path: {value!r}")
    except UnicodeError as exc:
        raise ValueError(f"unsafe repository-relative path: {value!r}") from exc
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError(f"unsafe repository-relative path: {value!r}")
    return value


def classify_path(value: str) -> tuple[str, str | None]:
    """Return the named policy and, for broad paths, its stable reason."""
    try:
        relative = _normalise_path(value)
    except ValueError:
        return "broad", "unsafe-git-path"
    path = PurePosixPath(relative)

    if relative in _PROGRAM_CONTROL:
        return "program-control", None
    if relative == _COACH_EXACT:
        return "coach", None
    if relative in _STORAGE_EXACT:
        return "storage", None
    if _under(path, _RS_PATH / "program_core"):
        return "program-host", None
    if _under(path, _RS_PATH / "safety_core"):
        return "safety", None
    if _under(path, _RS_PATH / "ble_core") or _under(
        path, _RS_PATH / "esp32tap/src/ble"
    ):
        return "ble", None
    if _under(path, _RS_PATH / "coach_core"):
        return "coach", None
    if _under(path, _RS_PATH / "reqbudget") or _under(
        path, _RS_PATH / "esp32tap/src/net"
    ):
        return "request-api", None

    if _under(path, PurePosixPath("docs")) or (
        path.suffix.lower() == ".md" and not _under(path, _RS_PATH)
    ):
        return "docs", None
    if path.suffix.lower() == ".md" and (
        path.parent == _RS_PATH or not _under(path, _RS_PATH)
    ):
        return "docs", None

    if not _under(path, _RS_PATH):
        return "broad", "path-outside-esp32-rs"
    return "broad", "broad-policy-path"


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    return environment


def _run_git(root: Path, args: tuple[str, ...]) -> bytes:
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["git", "-C", os.fspath(root), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
        with selectors.DefaultSelector() as streams:
            streams.register(process.stdout, selectors.EVENT_READ, "stdout")
            streams.register(process.stderr, selectors.EVENT_READ, "stderr")
            while streams.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GitFailure(f"Git timed out for {args!r}")
                events = streams.select(remaining)
                if not events:
                    raise GitFailure(f"Git timed out for {args!r}")
                for key, _mask in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        streams.unregister(key.fileobj)
                        continue
                    destination = output[key.data]
                    destination.extend(chunk)
                    if len(destination) > _MAX_GIT_OUTPUT:
                        raise GitFailure(
                            f"Git output exceeded selector limit for {args!r}"
                        )
        return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        if return_code != 0:
            raise GitFailure(f"Git exited {return_code} for {args!r}")
        return bytes(output["stdout"])
    except subprocess.TimeoutExpired as exc:
        raise GitFailure(f"Git timed out for {args!r}") from exc
    except OSError as exc:
        raise GitFailure(f"could not execute Git for {args!r}") from exc
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def discover_repo_root(script_path: Path) -> Path:
    physical_script = Path(script_path).resolve(strict=True)
    directory = physical_script if physical_script.is_dir() else physical_script.parent
    output = _run_git(directory, ("rev-parse", "--show-toplevel"))
    try:
        value = os.fsdecode(output).rstrip("\n")
        root = Path(value).resolve(strict=True)
    except (OSError, UnicodeError) as exc:
        raise GitFailure("Git returned an invalid repository root") from exc
    if not value or b"\0" in output or b"\n" in output.rstrip(b"\n"):
        raise GitFailure("Git returned a malformed repository root")
    if not root.is_dir():
        raise GitFailure("Git repository root is not a directory")
    return root


def _parse_nul_paths(output: bytes) -> tuple[str, ...]:
    if not output:
        return ()
    if not output.endswith(b"\0"):
        raise GitFailure("Git path output was not NUL terminated")
    values: list[str] = []
    for raw in output[:-1].split(b"\0"):
        if not raw:
            raise GitFailure("Git returned an empty path")
        try:
            values.append(_normalise_path(os.fsdecode(raw)))
        except ValueError as exc:
            raise GitFailure("Git returned an unsafe path") from exc
    return tuple(values)


def _valid_status(raw: bytes) -> tuple[bool, int]:
    if re.fullmatch(rb"[ACDMTUXB]", raw):
        return True, 1
    match = re.fullmatch(rb"([RC])([0-9]{1,3})", raw)
    if match and int(match.group(2)) <= 100:
        return True, 2
    return False, 0


def _parse_name_status(output: bytes) -> tuple[str, ...]:
    if not output:
        return ()
    if not output.endswith(b"\0"):
        raise GitFailure("Git name-status output was not NUL terminated")
    fields = output[:-1].split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        valid, count = _valid_status(fields[index])
        if not valid or index + count >= len(fields) + 1:
            raise GitFailure("Git returned a malformed name-status record")
        index += 1
        if index + count > len(fields):
            raise GitFailure("Git returned a truncated name-status record")
        for raw in fields[index : index + count]:
            if not raw:
                raise GitFailure("Git returned an empty path")
            try:
                paths.append(_normalise_path(os.fsdecode(raw)))
            except ValueError as exc:
                raise GitFailure("Git returned an unsafe path") from exc
        index += count
    return tuple(paths)


def _safe_revision(value: str, *, is_range: bool) -> str:
    if (
        not value
        or value.startswith("-")
        or any(character.isspace() or character == "\0" for character in value)
    ):
        kind = "range" if is_range else "revision"
        raise SelectionError(f"unsafe Git {kind}: {value!r}")
    if is_range:
        separators = [
            index
            for index in range(len(value) - 1)
            if value[index : index + 2] == ".."
        ]
        if len(separators) != 1:
            raise SelectionError(f"unsafe Git range: {value!r}")
        separator = separators[0]
        left = value[:separator]
        right = value[separator + 2 :]
        if (
            not left
            or not right
            or left.startswith("-")
            or right.startswith("-")
            or ".." in left
            or ".." in right
        ):
            raise SelectionError(f"unsafe Git range: {value!r}")
    elif ".." in value:
        raise SelectionError(f"unsafe Git revision: {value!r}")
    return value


def _route_in_patch(output: bytes) -> bool:
    for line in output.splitlines():
        if line.startswith((b"+++", b"---")):
            continue
        if line.startswith((b"+", b"-")) and _ROUTE_SYMBOL.search(line[1:]):
            return True
    return False


def _route_patch_args(base: str | None, range_spec: str | None) -> tuple[tuple[str, ...], ...]:
    common = ("--no-ext-diff", "--no-textconv", "-U0")
    commands: list[tuple[str, ...]] = [
        ("diff", *common, "--"),
        ("diff", *common, "--cached", "--"),
    ]
    if base is not None:
        commands.append(("diff", *common, base, "--"))
    if range_spec is not None:
        commands.append(("diff", *common, range_spec, "--"))
    return tuple(commands)


def _source_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _SOURCE_SUFFIXES


def _untracked_route_trigger(root: Path, untracked: Iterable[str]) -> bool:
    for relative in untracked:
        if not _source_path(relative):
            continue
        try:
            descriptor = os.open(
                root / relative,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise GitFailure("could not safely inspect untracked source") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_UNTRACKED_SOURCE:
                raise GitFailure("untracked source is not a bounded regular file")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, _MAX_UNTRACKED_SOURCE + 1 - size),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_UNTRACKED_SOURCE:
                    raise GitFailure("untracked source exceeded selector limit")
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if _ROUTE_SYMBOL.search(data):
            return True
    return False


def _broad(paths: tuple[str, ...], reason: str) -> Selection:
    policy = _POLICIES["broad"]
    return Selection(
        paths=paths,
        policies=("broad",),
        host_argv=policy.host_argv,
        qemu_argv=policy.qemu_argv,
        workers=MappingProxyType({"host": 1, "qemu": 0}),
        artifact_kinds=policy.artifact_kinds,
        broad_reason=reason,
    )


def _dedupe(values: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


def select(
    repo_root: Path,
    *,
    base: str | None = None,
    range_spec: str | None = None,
    explicit_paths: Iterable[str] = (),
) -> Selection:
    if base is not None and range_spec is not None:
        raise ValueError("--base and --range are mutually exclusive")
    if base is not None:
        base = _safe_revision(base, is_range=False)
    if range_spec is not None:
        range_spec = _safe_revision(range_spec, is_range=True)
    root = Path(repo_root).resolve(strict=False)

    name_commands: list[tuple[str, ...]] = [
        ("diff", "-M", "--name-status", "-z", "--"),
        ("diff", "-M", "--cached", "--name-status", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    ]
    if base is not None:
        name_commands.append(("diff", "-M", "--name-status", "-z", base, "--"))
    if range_spec is not None:
        name_commands.append(
            ("diff", "-M", "--name-status", "-z", range_spec, "--")
        )

    try:
        outputs = [_run_git(root, command) for command in name_commands]
        authoritative: set[str] = set()
        for output in outputs[:2]:
            authoritative.update(_parse_name_status(output))
        untracked = _parse_nul_paths(outputs[2])
        authoritative.update(untracked)
        for output in outputs[3:]:
            authoritative.update(_parse_name_status(output))
    except (GitFailure, OSError, ValueError):
        return _broad((), "git-enumeration-failed")

    if not authoritative:
        raise SelectionError(
            "no authoritative Git changes; use --base REV or "
            "--range A..B for committed work"
        )

    for value in explicit_paths:
        try:
            authoritative.add(_normalise_path(value))
        except ValueError:
            return _broad(
                tuple(sorted(authoritative, key=os.fsencode)),
                "unsafe-explicit-path",
            )
    paths = tuple(sorted(authoritative, key=os.fsencode))

    if any(_source_path(path) for path in paths):
        try:
            if any(
                _route_in_patch(_run_git(root, command))
                for command in _route_patch_args(base, range_spec)
            ) or _untracked_route_trigger(root, untracked):
                return _broad(paths, "route-registration-diff")
        except (GitFailure, OSError):
            return _broad(paths, "route-inspection-failed")

    classified = [(path, *classify_path(path)) for path in paths]
    broad_paths = [
        (path, reason)
        for path, policy, reason in classified
        if policy == "broad"
    ]
    if broad_paths:
        return _broad(paths, broad_paths[0][1] or "broad-policy-path")

    present = {policy for _path, policy, _reason in classified}
    policy_names = tuple(name for name in _POLICY_ORDER if name in present)
    policies = tuple(_POLICIES[name] for name in policy_names)
    host = _dedupe(argv for policy in policies for argv in policy.host_argv)
    qemu = _dedupe(argv for policy in policies for argv in policy.qemu_argv)
    artifacts = tuple(
        kind
        for kind in ("production", "qemu")
        if any(kind in policy.artifact_kinds for policy in policies)
    )
    return Selection(
        paths=paths,
        policies=policy_names,
        host_argv=host,
        qemu_argv=qemu,
        workers=MappingProxyType(
            {
                "host": 1 if host else 0,
                "qemu": max((p.qemu_workers for p in policies), default=0),
            }
        ),
        artifact_kinds=artifacts,
        broad_reason=None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--base")
    group.add_argument("--range", dest="range_spec")
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_args(argv)
    try:
        root = discover_repo_root(Path(__file__))
        selected = select(
            root,
            base=arguments.base,
            range_spec=arguments.range_spec,
            explicit_paths=arguments.paths,
        )
    except SelectionError as exc:
        print(f"fast-select: {exc}", file=sys.stderr)
        return 2
    except (GitFailure, OSError, ValueError):
        selected = _broad((), "selector-internal-failure")
    print(selected.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
