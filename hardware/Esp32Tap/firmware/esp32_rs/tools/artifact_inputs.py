"""Deterministic, immutable build-input snapshots for Esp32Tap firmware."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_ESP32_RS = PurePosixPath("hardware/Esp32Tap/firmware/esp32_rs")
_GENERATED_PREFIXES = (
    _ESP32_RS / "build",
    _ESP32_RS / "build_qemu_test",
    _ESP32_RS / ".artifacts",
)
_CACHE_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
    }
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "credentials.toml",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
    }
)
_SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})
_UNTRACKED_INPUT_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cmake",
        ".cpp",
        ".csv",
        ".cxx",
        ".h",
        ".hpp",
        ".json",
        ".ld",
        ".lock",
        ".py",
        ".rs",
        ".s",
        ".sh",
        ".toml",
        ".x",
        ".yaml",
        ".yml",
    }
)
_UNTRACKED_INPUT_NAMES = frozenset({"Dockerfile", "Makefile"})
_GATES = (
    "check_unsafe_budget.py",
    "check_case_parity.py",
    "check_pins.py",
    "check_wdt_chain.py",
)


@dataclass(frozen=True)
class Snapshot:
    root: Path
    digest: str
    paths: tuple[str, ...]
    worktree_key: str


def _validated_repo_root(repo_root: Path) -> Path:
    root = Path(repo_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"repo_root is not a directory: {root}")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"repo_root is not a Git worktree: {root}") from exc
    top = Path(result.stdout.strip()).resolve(strict=True)
    if top != root:
        raise ValueError(
            f"repo_root must be the Git worktree root: {root} (top level is {top})"
        )
    return root


def _git_paths(root: Path, *args: str) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"could not enumerate Git inputs in {root}") from exc
    paths: set[str] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        paths.add(_normalise_relative(os.fsdecode(raw)))
    return paths


def _normalise_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError(f"Git returned unsafe repository-relative path: {value!r}")
    normalised = path.as_posix()
    if normalised == ".":
        raise ValueError(f"Git returned unsafe repository-relative path: {value!r}")
    return normalised


def _under(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or prefix in path.parents


def _always_excluded(relative: str) -> bool:
    path = PurePosixPath(relative)
    if any(_under(path, prefix) for prefix in _GENERATED_PREFIXES):
        return True
    if _under(path, _ESP32_RS) and "target" in path.parts[len(_ESP32_RS.parts) :]:
        return True
    if any(part in _CACHE_PARTS for part in path.parts):
        return True
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _SECRET_NAMES or path.suffix.lower() in _SECRET_SUFFIXES:
        return True
    return name.endswith((".pyc", ".pyo"))


def _relevant_untracked(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name
    if name in _UNTRACKED_INPUT_NAMES:
        return True
    if name.startswith("sdkconfig"):
        return True
    return path.suffix.lower() in _UNTRACKED_INPUT_SUFFIXES


def _safe_symlink_target(root: Path, relative: str) -> str:
    link = root / relative
    target_text = os.readlink(link)
    if os.path.isabs(target_text):
        raise ValueError(
            f"absolute symlink is not a build input: {relative} -> {target_text}"
        )
    lexical = os.path.normpath(os.path.join(os.path.dirname(relative), target_text))
    if lexical == ".." or lexical.startswith("../") or os.path.isabs(lexical):
        raise ValueError(
            f"symlink target escapes outside repo: {relative} -> {target_text}"
        )
    target_relative = _normalise_relative(PurePosixPath(lexical).as_posix())
    target = root / target_relative
    try:
        resolved = target.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(
            f"broken symlink is not a build input: {relative} -> {target_text}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"symlink target resolves outside repo: {relative} -> {target_text}"
        ) from exc
    if resolved.is_dir():
        raise ValueError(
            f"directory symlink is not supported as a build input: {relative} -> {target_text}"
        )
    return target_relative


def _collect_paths(root: Path) -> tuple[str, ...]:
    candidates = _git_paths(root, "ls-files", "-z", "-co", "--exclude-standard")
    tracked = _git_paths(root, "ls-files", "-z", "-c")
    selected = {
        relative
        for relative in candidates
        if not _always_excluded(relative)
        and (relative in tracked or _relevant_untracked(relative))
        and os.path.lexists(root / relative)
    }

    pending = list(selected)
    while pending:
        relative = pending.pop()
        path = root / relative
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target_relative = _safe_symlink_target(root, relative)
            if _always_excluded(target_relative):
                raise ValueError(
                    f"symlink target is an excluded generated/cache/secret input: "
                    f"{relative} -> {target_relative}"
                )
            if target_relative not in selected:
                selected.add(target_relative)
                pending.append(target_relative)
        elif not stat.S_ISREG(mode):
            raise ValueError(
                f"build input is not a regular file or symlink: {relative}"
            )
    return tuple(sorted(selected, key=os.fsencode))


def declared_inputs(repo_root: Path) -> tuple[str, ...]:
    """Return the complete, sorted repository-relative build-input set."""

    return _collect_paths(_validated_repo_root(repo_root))


def _record(hasher: object, relative: str, kind: bytes, content: bytes) -> None:
    path_bytes = os.fsencode(relative)
    hasher.update(struct.pack(">Q", len(path_bytes)))
    hasher.update(path_bytes)
    hasher.update(kind)
    hasher.update(struct.pack(">Q", len(content)))
    hasher.update(content)


def _read_input(root: Path, relative: str) -> tuple[bytes, bytes, int]:
    path = root / relative
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode):
        content = os.fsencode(os.readlink(path))
        kind = b"L"
    elif stat.S_ISREG(before.st_mode):
        content = path.read_bytes()
        kind = b"F"
    else:
        raise ValueError(f"build input is not a regular file or symlink: {relative}")
    after = path.lstat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise RuntimeError(f"build input changed while it was being read: {relative}")
    return kind, content, stat.S_IMODE(before.st_mode)


def working_digest(repo_root: Path) -> str:
    """Hash current declared path names, types, and bytes (never mtimes)."""

    root = _validated_repo_root(repo_root)
    hasher = hashlib.sha256()
    for relative in _collect_paths(root):
        kind, content, _ = _read_input(root, relative)
        _record(hasher, relative, kind, content)
    return hasher.hexdigest()


def _worktree_key(root: Path) -> str:
    return hashlib.sha256(os.fsencode(str(root.resolve(strict=True)))).hexdigest()[:12]


def target_cache(repo_root: Path, kind: str) -> Path:
    """Return the physical-worktree-specific Cargo target cache."""

    if kind not in ("prod", "qemu"):
        raise ValueError(
            f"unknown target cache kind {kind!r}; expected 'prod' or 'qemu'"
        )
    root = _validated_repo_root(repo_root)
    return Path("/tmp") / f"esp32tap-target-{_worktree_key(root)}" / kind


def _newest_mtime_ns(root: Path) -> int:
    if not os.path.lexists(root):
        return -1
    newest = root.lstat().st_mtime_ns
    if not root.is_dir():
        return newest
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirnames + filenames:
            newest = max(newest, (base / name).lstat().st_mtime_ns)
    return newest


def _set_input_mtime(path: Path, mtime_ns: int) -> None:
    if path.is_symlink():
        os.utime(path, ns=(mtime_ns, mtime_ns), follow_symlinks=False)
    else:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def create_snapshot(repo_root: Path, destination: Path, target_cache: Path) -> Snapshot:
    """Copy one coherent set of declared bytes to a new snapshot directory."""

    root = _validated_repo_root(repo_root)
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    paths = _collect_paths(root)
    newest_target = _newest_mtime_ns(Path(target_cache))
    snapshot_mtime = max(time.time_ns(), newest_target + 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    hasher = hashlib.sha256()
    try:
        for relative in paths:
            kind, content, permissions = _read_input(root, relative)
            output = staging / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if kind == b"L":
                output.symlink_to(os.fsdecode(content))
            else:
                output.write_bytes(content)
                output.chmod(permissions)
            _set_input_mtime(output, snapshot_mtime)
            if output.lstat().st_mtime_ns <= newest_target:
                raise RuntimeError(
                    f"filesystem could not set snapshot input newer than target cache: {relative}"
                )
            _record(hasher, relative, kind, content)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Snapshot(
        root=destination,
        digest=hasher.hexdigest(),
        paths=paths,
        worktree_key=_worktree_key(root),
    )


def verify_gate_input_completeness(snapshot_root: Path) -> None:
    """Run every current host gate using only snapshot-local repository paths."""

    root = Path(snapshot_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"snapshot_root is not a directory: {root}")
    tools = root / Path(_ESP32_RS.as_posix()) / "tools"
    environment = {
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", ""),
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for gate in _GATES:
        script = tools / gate
        if not script.is_file():
            raise RuntimeError(
                f"gate input completeness failed: missing gate {script.relative_to(root)}"
            )
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode:
            output = result.stdout.strip()
            detail = f"\n{output}" if output else ""
            raise RuntimeError(
                f"gate input completeness failed: {gate} exited {result.returncode}{detail}"
            )
