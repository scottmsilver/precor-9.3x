#!/usr/bin/env python3
"""Attest, publish, and lease immutable ESP32Tap flash bundles.

The public build paths are relative symlinks to content-addressed generations.
Readers and publishers use the same worktree-specific flock as build.sh and
qemu_session.py, so an exec'd consumer retains a continuous read lease.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn


SCHEMA_VERSION = 1
MANIFEST_NAME = "artifact-manifest.json"
BUNDLE_MEMBERS = (
    "esp32tap.bin",
    "bootloader.bin",
    "partition-table.bin",
    "flash_args",
    "sdkconfig",
)

EXIT_MISSING = 20
EXIT_STALE = 21
EXIT_INVALID = 22
EXIT_INTERNAL = 23

_KIND_LAYOUT = {
    "production": ("build", "prod"),
    "qemu-test": ("build_qemu_test", "qemu"),
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40,64}\Z")
_FEATURE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]*\Z")
_TOOLCHAIN_KEYS = (
    "image_id",
    "recipe_sha256",
    "image_tag",
    "idf_commit",
    "rustc_verbose",
    "target",
    "linker_version",
    "esptool_version",
    "component_lock_sha256",
    "profile",
    "features",
)


class ArtifactError(Exception):
    """Expected error with a stable process exit status."""

    code = EXIT_INTERNAL


class MissingError(ArtifactError):
    code = EXIT_MISSING


class StaleError(ArtifactError):
    code = EXIT_STALE


class InvalidError(ArtifactError):
    code = EXIT_INVALID


class InternalError(ArtifactError):
    code = EXIT_INTERNAL


class _ExecIntercept(BaseException):
    """Test-only sentinel: unlike Exception it bypasses the CLI error mapper."""


@dataclass(frozen=True)
class Toolchain:
    image_id: str
    recipe_sha256: str
    image_tag: str
    idf_commit: str
    rustc_verbose: str
    target: str
    linker_version: str
    esptool_version: str
    component_lock_sha256: str
    profile: str
    features: tuple[str, ...]

    def __post_init__(self) -> None:
        scalar_fields = (
            "image_id",
            "image_tag",
            "rustc_verbose",
            "target",
            "linker_version",
            "esptool_version",
            "profile",
        )
        for name in scalar_fields:
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty canonical string")
            if "\x00" in value:
                raise ValueError(f"{name} contains NUL")
        if not _HEX64.fullmatch(self.recipe_sha256):
            raise ValueError("recipe_sha256 must be lowercase SHA-256")
        if not _HEX64.fullmatch(self.component_lock_sha256):
            raise ValueError("component_lock_sha256 must be lowercase SHA-256")
        if not _IMAGE_ID.fullmatch(self.image_id):
            raise ValueError("image_id must be an immutable Docker SHA-256 ID")
        if not _IMAGE_TAG.fullmatch(self.image_tag):
            raise ValueError("image_tag must be a canonical Docker image tag")
        if not _COMMIT.fullmatch(self.idf_commit):
            raise ValueError("idf_commit must be a lowercase commit hash")
        if not isinstance(self.features, tuple) or not all(
            isinstance(feature, str) for feature in self.features
        ):
            raise ValueError("features must be a tuple of strings")
        if len(set(self.features)) != len(self.features):
            raise ValueError("duplicate feature")
        if any(not _FEATURE.fullmatch(feature) for feature in self.features):
            raise ValueError("features must use unambiguous ASCII names")
        object.__setattr__(self, "features", tuple(sorted(self.features)))


@dataclass(frozen=True)
class Result:
    code: int
    message: str
    manifest: dict | None = None

    @property
    def ok(self) -> bool:
        return self.code == 0


def _kind(kind: str) -> tuple[str, str]:
    try:
        return _KIND_LAYOUT[kind]
    except (KeyError, TypeError) as exc:
        raise InvalidError(f"unknown artifact kind: {kind!r}") from exc


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise InvalidError(f"{label} must be a lowercase SHA-256")
    return value


def manifest_bytes(manifest: dict) -> bytes:
    """Return the one canonical on-disk JSON representation."""
    if not isinstance(manifest, dict):
        raise InvalidError("manifest must be an object")
    try:
        encoded = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidError(f"manifest is not canonical JSON: {exc}") from exc
    return encoded + b"\n"


def _toolchain_dict(toolchain: Toolchain) -> dict:
    result = dataclasses.asdict(toolchain)
    result["features"] = list(toolchain.features)
    return result


def _toolchain_from_dict(value: object) -> Toolchain:
    if not isinstance(value, dict) or set(value) != set(_TOOLCHAIN_KEYS):
        raise InvalidError("manifest toolchain fields do not match schema")
    fields = dict(value)
    features = fields.get("features")
    if not isinstance(features, list) or not all(
        isinstance(feature, str) for feature in features
    ):
        raise InvalidError("manifest features must be an array of strings")
    fields["features"] = tuple(features)
    try:
        toolchain = Toolchain(**fields)
    except (TypeError, ValueError) as exc:
        raise InvalidError(f"invalid manifest toolchain: {exc}") from exc
    if list(toolchain.features) != features:
        raise InvalidError("manifest features are not sorted canonically")
    return toolchain


def _safe_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise InvalidError(f"{label} is missing") from exc
    if not stat.S_ISREG(info.st_mode):
        raise InvalidError(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        raise InvalidError(f"{label} must have exactly one hard link")
    return info


def _hash_file(path: Path) -> tuple[int, str]:
    info = _safe_file(path, path.name)
    digest = hashlib.sha256()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise InvalidError(f"{path.name} changed type while opening")
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise InvalidError(f"{path.name} changed while opening")
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
        after = os.fstat(fd)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise InvalidError(f"{path.name} changed while hashing")
        return opened.st_size, digest.hexdigest()
    finally:
        os.close(fd)


def _read_safe_bytes(path: Path, label: str) -> bytes:
    before = _safe_file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InvalidError(f"{label} must not be a symlink") from exc
        raise
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise InvalidError(f"{label} changed while opening")
        chunks = []
        while block := os.read(fd, 1024 * 1024):
            chunks.append(block)
        after = os.fstat(fd)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise InvalidError(f"{label} changed while reading")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _member_records(directory: Path) -> list[dict]:
    records = []
    for name in BUNDLE_MEMBERS:
        size, sha256 = _hash_file(directory / name)
        records.append({"name": name, "sha256": sha256, "size": size})
    return records


def _validate_staging(staging: Path, esp32_rs: Path) -> None:
    if not isinstance(staging, Path):
        raise InvalidError("staging must be a Path")
    if ".." in staging.parts:
        raise InvalidError("staging must not contain path traversal")
    try:
        info = staging.lstat()
    except FileNotFoundError as exc:
        raise InvalidError("staging directory is missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise InvalidError("staging must be a real directory, not a symlink")
    if staging.absolute().parent.resolve(strict=True) != esp32_rs:
        raise InvalidError("staging must be a direct child of esp32_rs")
    names = {entry.name for entry in os.scandir(staging)}
    if names != set(BUNDLE_MEMBERS):
        raise InvalidError("staging member set differs from the exact flash bundle")
    _member_records(staging)


def make_manifest(
    staging: Path,
    kind: str,
    input_digest: str,
    toolchain: Toolchain,
) -> dict:
    """Build a complete, self-identifying manifest for a staging directory."""
    _kind(kind)
    digest = _validate_digest(input_digest, "input_digest")
    if not isinstance(toolchain, Toolchain):
        raise InvalidError("toolchain must be a validated Toolchain")
    try:
        staging_info = staging.lstat()
    except (AttributeError, OSError) as exc:
        raise InvalidError(f"staging directory is unavailable: {exc}") from exc
    if not stat.S_ISDIR(staging_info.st_mode):
        raise InvalidError("staging must be a real directory, not a symlink")
    names = {entry.name for entry in os.scandir(staging)}
    if names != set(BUNDLE_MEMBERS):
        raise InvalidError("staging has a missing or extra bundle member")
    unsigned = {
        "input_digest": digest,
        "kind": kind,
        "members": _member_records(staging),
        "schema_version": SCHEMA_VERSION,
        "toolchain": _toolchain_dict(toolchain),
    }
    result = dict(unsigned)
    result["manifest_sha256"] = hashlib.sha256(manifest_bytes(unsigned)).hexdigest()
    return result


def _validate_manifest_object(
    value: object,
    *,
    expected_kind: str | None = None,
) -> tuple[dict, Toolchain]:
    required = {
        "schema_version",
        "kind",
        "input_digest",
        "toolchain",
        "members",
        "manifest_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise InvalidError("manifest fields do not match schema")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise InvalidError("unsupported manifest schema")
    _kind(value["kind"])
    if expected_kind is not None and value["kind"] != expected_kind:
        raise InvalidError("manifest kind does not match public bundle")
    _validate_digest(value["input_digest"], "manifest input_digest")
    identity = _validate_digest(value["manifest_sha256"], "manifest identity")
    members = value["members"]
    if not isinstance(members, list) or len(members) != len(BUNDLE_MEMBERS):
        raise InvalidError("manifest member list is incomplete")
    normalized = []
    for expected_name, entry in zip(BUNDLE_MEMBERS, members, strict=True):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "sha256", "size"}
            or entry.get("name") != expected_name
            or type(entry.get("size")) is not int
            or entry["size"] < 0
        ):
            raise InvalidError("manifest member schema/order is invalid")
        _validate_digest(entry.get("sha256"), f"{expected_name} digest")
        normalized.append(entry)
    unsigned = dict(value)
    unsigned.pop("manifest_sha256")
    actual_identity = hashlib.sha256(manifest_bytes(unsigned)).hexdigest()
    if not _constant_time_equal(actual_identity, identity):
        raise InvalidError("manifest identity does not match its contents")
    toolchain = _toolchain_from_dict(value["toolchain"])
    return value, toolchain


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def _load_manifest(
    bundle: Path, expected_kind: str | None = None
) -> tuple[dict, Toolchain]:
    path = bundle / MANIFEST_NAME
    if not os.path.lexists(path):
        raise MissingError("artifact manifest is missing")
    try:
        raw = _read_safe_bytes(path, "artifact manifest")
    except FileNotFoundError as exc:
        raise MissingError("artifact manifest is missing") from exc
    except OSError as exc:
        raise InternalError(f"cannot read artifact manifest: {exc}") from exc
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidError(f"malformed artifact manifest: {exc}") from exc
    manifest, toolchain = _validate_manifest_object(value, expected_kind=expected_kind)
    if raw != manifest_bytes(manifest):
        raise InvalidError("artifact manifest is not canonically serialized")
    return manifest, toolchain


def _bundle_kind_from_name(bundle: Path) -> str | None:
    for kind, (public_name, _) in _KIND_LAYOUT.items():
        if bundle.name == public_name:
            return kind
    return None


def _verify_or_raise(
    bundle: Path,
    expected: Toolchain,
    input_digest: str,
) -> dict:
    _validate_digest(input_digest, "expected input_digest")
    if not isinstance(expected, Toolchain):
        raise InvalidError("expected toolchain is invalid")
    try:
        lexical = bundle.lstat()
    except FileNotFoundError as exc:
        raise MissingError("public artifact bundle is missing") from exc
    expected_kind = _bundle_kind_from_name(bundle)
    if not stat.S_ISLNK(lexical.st_mode):
        raise InvalidError("public artifact bundle must be a relative symlink")
    target = os.readlink(bundle)
    if os.path.isabs(target):
        raise InvalidError("public artifact link must be relative")
    if expected_kind is None:
        raise InvalidError("public artifact link has an unknown name")
    _, artifact_name = _kind(expected_kind)
    target_parts = Path(target).parts
    if (
        len(target_parts) != 3
        or target_parts[:2] != (".artifacts", artifact_name)
        or not _HEX64.fullmatch(target_parts[2])
    ):
        raise InvalidError("public artifact link is not canonical")
    artifacts = bundle.parent / ".artifacts"
    kind_root = artifacts / artifact_name
    generation = kind_root / target_parts[2]
    for path, label in (
        (artifacts, "artifact store"),
        (kind_root, "artifact kind store"),
        (generation, "artifact generation"),
    ):
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise MissingError(f"{label} is missing") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise InvalidError(f"{label} must be a real directory")
    try:
        directory_info = bundle.stat()
    except FileNotFoundError as exc:
        raise MissingError("public artifact bundle link is broken") from exc
    if not stat.S_ISDIR(directory_info.st_mode):
        raise InvalidError("artifact bundle is not a directory")
    try:
        names = {entry.name for entry in os.scandir(bundle)}
    except OSError as exc:
        raise InternalError(f"cannot enumerate artifact bundle: {exc}") from exc
    if MANIFEST_NAME not in names:
        raise MissingError("artifact manifest is missing")
    required = {*BUNDLE_MEMBERS, MANIFEST_NAME}
    if names != required:
        raise InvalidError("artifact bundle has missing or extra members")
    manifest, actual_toolchain = _load_manifest(bundle, expected_kind)
    if Path(os.readlink(bundle)).name != manifest["manifest_sha256"]:
        raise InvalidError("public artifact link digest does not match manifest")
    if generation != bundle.parent / Path(os.readlink(bundle)):
        raise InvalidError("public artifact link does not name its exact generation")
    actual_records = _member_records(bundle)
    if actual_records != manifest["members"]:
        raise InvalidError("artifact member size or digest changed")
    if actual_toolchain != expected:
        raise InvalidError("artifact toolchain facts do not match expected")
    if manifest["input_digest"] != input_digest:
        raise StaleError("artifact input digest is stale")
    return manifest


def verify_locked(bundle: Path, expected: Toolchain, input_digest: str) -> Result:
    """Verify a bundle while its caller owns the appropriate shared lock."""
    try:
        manifest = _verify_or_raise(bundle, expected, input_digest)
        return Result(0, "artifact bundle is current", manifest)
    except ArtifactError as exc:
        return Result(exc.code, str(exc))
    except Exception as exc:
        return Result(EXIT_INTERNAL, f"unexpected verification failure: {exc}")


def _physical_esp32_rs(repo_root: Path) -> Path:
    candidate = repo_root / "hardware" / "Esp32Tap" / "firmware" / "esp32_rs"
    try:
        result = candidate.resolve(strict=True)
    except OSError as exc:
        raise InternalError(f"cannot resolve esp32_rs: {exc}") from exc
    if not result.is_dir():
        raise InternalError("resolved esp32_rs is not a directory")
    return result


def _lock_for_esp32_rs(esp32_rs: Path) -> Path:
    physical = esp32_rs.resolve(strict=True)
    digest = hashlib.md5(str(physical).encode(), usedforsecurity=False).hexdigest()[:12]
    return Path("/tmp") / f"esp32tap-build-{digest}.lock"


def lock_path(repo_root: Path, kind: str) -> Path:
    _kind(kind)
    return _lock_for_esp32_rs(_physical_esp32_rs(repo_root))


def _open_locked(path: Path, operation: int, inheritable: bool) -> int:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(fd, inheritable)
        fcntl.flock(fd, operation)
        return fd
    except OSError as exc:
        if "fd" in locals():
            os.close(fd)
        raise InternalError(f"cannot acquire artifact lock {path}: {exc}") from exc


@contextlib.contextmanager
def shared_bundle(repo_root: Path, kind: str) -> Iterator[Path]:
    """Yield the kind's public bundle for exactly the shared-lock lifetime."""
    public_name, _ = _kind(kind)
    esp32_rs = _physical_esp32_rs(repo_root)
    fd = _open_locked(_lock_for_esp32_rs(esp32_rs), fcntl.LOCK_SH, False)
    try:
        yield esp32_rs / public_name
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_member(source: Path, destination: Path, expected: dict) -> None:
    before = _safe_file(source, source.name)
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    destination_fd = -1
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise InvalidError(f"{source.name} changed while publishing")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IMODE(opened.st_mode) & 0o777,
        )
        digest = hashlib.sha256()
        size = 0
        while block := os.read(source_fd, 1024 * 1024):
            digest.update(block)
            size += len(block)
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        after = os.fstat(source_fd)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise InvalidError(f"{source.name} changed while publishing")
        if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
            raise InvalidError(f"{source.name} does not match manifest")
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)


def _generation_matches(generation: Path, manifest: dict) -> bool:
    try:
        names = {entry.name for entry in os.scandir(generation)}
        if names != {*BUNDLE_MEMBERS, MANIFEST_NAME}:
            return False
        raw = _read_safe_bytes(generation / MANIFEST_NAME, "generation manifest")
        if raw != manifest_bytes(manifest):
            return False
        return _member_records(generation) == manifest["members"]
    except (OSError, ArtifactError):
        return False


def _remove_tree_exact(path: Path, artifacts: Path, prefix: str) -> None:
    if path.parent != artifacts or not path.name.startswith(prefix):
        raise InternalError("refusing unsafe backup removal target")
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _failure_point(_name: str) -> None:
    """Monkeypatch seam for deterministic crash/rollback tests."""


def _ensure_real_directory(path: Path, label: str) -> None:
    try:
        path.mkdir(mode=0o755)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise InternalError(f"cannot inspect {label}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise InvalidError(f"{label} must be a real directory, not a symlink")


def _expected_rs_from_public(public_link: Path) -> tuple[Path, str, str]:
    if not isinstance(public_link, Path):
        raise InvalidError("public_link must be a Path")
    if ".." in public_link.parts:
        raise InvalidError("public link must not contain path traversal")
    try:
        esp32_rs = public_link.absolute().parent.resolve(strict=True)
    except OSError as exc:
        raise InvalidError(f"public parent is unavailable: {exc}") from exc
    suffix = ("hardware", "Esp32Tap", "firmware", "esp32_rs")
    if tuple(esp32_rs.parts[-4:]) != suffix:
        raise InvalidError("public link is outside the expected esp32_rs layout")
    for kind, (public_name, artifact_name) in _KIND_LAYOUT.items():
        if public_link.name == public_name:
            return esp32_rs, kind, artifact_name
    raise InvalidError("public link name is not a known bundle")


def _ancestor_pids() -> set[int]:
    result = set()
    pid = os.getpid()
    while pid > 0 and pid not in result:
        result.add(pid)
        try:
            text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            fields = text.rpartition(") ")[2].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError) as exc:
            raise InternalError(f"cannot validate lock ownership: {exc}") from exc
    return result


def _validate_exclusive_lock_fd(lock_fd: int, expected_path: Path) -> None:
    if type(lock_fd) is not int or lock_fd < 0:
        raise InvalidError("lock_fd must be an open integer descriptor")
    try:
        supplied = os.fstat(lock_fd)
        expected = expected_path.lstat()
        inheritable = os.get_inheritable(lock_fd)
    except OSError as exc:
        raise InvalidError(f"lock_fd is not valid: {exc}") from exc
    if not inheritable:
        raise InvalidError("lock_fd must be explicitly inheritable")
    if not stat.S_ISREG(supplied.st_mode) or (
        supplied.st_dev,
        supplied.st_ino,
    ) != (expected.st_dev, expected.st_ino):
        raise InvalidError("lock_fd does not refer to the physical build lock")
    owners = _ancestor_pids()
    device = (os.major(supplied.st_dev), os.minor(supplied.st_dev))
    try:
        locks = Path("/proc/locks").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise InternalError(f"cannot inspect caller lock ownership: {exc}") from exc
    for line in locks:
        fields = line.split()
        if len(fields) < 6 or fields[1] != "FLOCK":
            continue
        try:
            owner = int(fields[4])
            lock_device, inode = fields[5].rsplit(":", 1)
            major, minor = (int(part, 16) for part in lock_device.split(":", 1))
        except (ValueError, IndexError):
            continue
        if (
            owner in owners
            and (major, minor) == device
            and int(inode) == supplied.st_ino
        ):
            if fields[3] != "WRITE":
                raise InvalidError("lock_fd holds a shared lock, not exclusive")
            return
    raise InvalidError("lock_fd has no owned exclusive flock")


def publish_generation_atomic(
    staging: Path,
    public_link: Path,
    manifest: dict,
    *,
    lock_fd: int | None = None,
) -> None:
    """Durably publish a complete generation under one exclusive build lock."""
    esp32_rs, kind, artifact_name = _expected_rs_from_public(public_link)
    lock = _lock_for_esp32_rs(esp32_rs)
    owned_fd: int | None = None
    if lock_fd is None:
        owned_fd = _open_locked(lock, fcntl.LOCK_EX, False)
    else:
        _validate_exclusive_lock_fd(lock_fd, lock)
    try:
        _publish_locked(
            staging, public_link.absolute(), manifest, esp32_rs, kind, artifact_name
        )
    except ArtifactError:
        raise
    except Exception as exc:
        raise InternalError(f"artifact publication failed: {exc}") from exc
    finally:
        if owned_fd is not None:
            os.close(owned_fd)


def _publish_locked(
    staging: Path,
    public_link: Path,
    manifest: dict,
    esp32_rs: Path,
    kind: str,
    artifact_name: str,
) -> None:
    _validate_staging(staging, esp32_rs)
    snapshot = json.loads(manifest_bytes(manifest))
    normalized, _ = _validate_manifest_object(snapshot, expected_kind=kind)
    if _member_records(staging) != normalized["members"]:
        raise InvalidError("staging bytes do not match manifest")

    artifacts = esp32_rs / ".artifacts"
    kind_root = artifacts / artifact_name
    _ensure_real_directory(artifacts, "artifact store")
    _ensure_real_directory(kind_root, "artifact kind store")
    _fsync_dir(artifacts)
    _fsync_dir(esp32_rs)

    identity = normalized["manifest_sha256"]
    generation = kind_root / identity
    temp_generation: Path | None = None
    if os.path.lexists(generation):
        info = generation.lstat()
        if not stat.S_ISDIR(info.st_mode) or not _generation_matches(
            generation, normalized
        ):
            raise InvalidError("destination generation collides with different bytes")
    else:
        temp_generation = kind_root / f".tmp-{identity}-{uuid.uuid4().hex}"
        temp_generation.mkdir(mode=0o755)
        try:
            for expected in normalized["members"]:
                _copy_member(
                    staging / expected["name"],
                    temp_generation / expected["name"],
                    expected,
                )
            manifest_path = temp_generation / MANIFEST_NAME
            manifest_path.write_bytes(manifest_bytes(normalized))
            _fsync_file(manifest_path)
            _fsync_dir(temp_generation)
            os.rename(temp_generation, generation)
            temp_generation = None
            _fsync_dir(kind_root)
            _fsync_dir(artifacts)
        finally:
            if temp_generation is not None and os.path.lexists(temp_generation):
                shutil.rmtree(temp_generation)
    _failure_point("after_generation")

    relative_target = Path(".artifacts") / artifact_name / identity
    if public_link.is_symlink() and os.readlink(public_link) == str(relative_target):
        _fsync_dir(kind_root)
        _fsync_dir(artifacts)
        _fsync_dir(esp32_rs)
        return

    token = uuid.uuid4().hex
    temp_link = esp32_rs / f".{public_link.name}.tmp-{token}"
    rollback_link = esp32_rs / f".{public_link.name}.rollback-{token}"
    legacy_backup: Path | None = None
    old_target: str | None = None
    legacy_moved = False
    swapped = False
    os.symlink(str(relative_target), temp_link)
    if os.path.lexists(public_link):
        old_info = public_link.lstat()
        if stat.S_ISLNK(old_info.st_mode):
            old_target = os.readlink(public_link)
            os.symlink(old_target, rollback_link)
        elif stat.S_ISDIR(old_info.st_mode):
            legacy_backup = artifacts / f".legacy-{public_link.name}-{token}"
        else:
            temp_link.unlink()
            raise InvalidError(
                "public path is neither a legacy directory nor a symlink"
            )
    try:
        # Make both the new and rollback symlinks durable before mutation.
        _fsync_dir(esp32_rs)
        if legacy_backup is not None:
            os.replace(public_link, legacy_backup)
            legacy_moved = True
            _fsync_dir(esp32_rs)
            _fsync_dir(artifacts)
            _failure_point("after_legacy_backup")
        _failure_point("before_link_swap")
        os.replace(temp_link, public_link)
        swapped = True
        _failure_point("after_link_swap")
        _failure_point("before_commit")
        _fsync_dir(esp32_rs)
    except Exception:
        try:
            if swapped and old_target is not None:
                os.replace(rollback_link, public_link)
            elif swapped and os.path.lexists(public_link):
                current = public_link.lstat()
                if not stat.S_ISLNK(current.st_mode) or os.readlink(public_link) != str(
                    relative_target
                ):
                    raise InternalError(
                        "refusing to overwrite changed public path during rollback"
                    )
                public_link.unlink()
            if (
                legacy_moved
                and legacy_backup is not None
                and os.path.lexists(legacy_backup)
            ):
                os.replace(legacy_backup, public_link)
            if os.path.lexists(temp_link):
                temp_link.unlink()
            if os.path.lexists(rollback_link):
                rollback_link.unlink()
            _fsync_dir(esp32_rs)
            _fsync_dir(artifacts)
        except Exception as rollback_error:
            raise InternalError(
                f"publication rollback failed: {rollback_error}"
            ) from rollback_error
        raise

    # The successful esp32_rs fsync above is the commit point. From here on,
    # cleanup is best effort and must not report failure with new public state.
    try:
        _failure_point("after_link_fsync")
        _failure_point("after_commit")
    except Exception:
        pass
    if os.path.lexists(rollback_link):
        try:
            rollback_link.unlink()
            _fsync_dir(esp32_rs)
        except Exception:
            pass
    if legacy_backup is not None and os.path.lexists(legacy_backup):
        retired = artifacts / f".retired-legacy-{public_link.name}-{token}"
        try:
            os.replace(legacy_backup, retired)
            _fsync_dir(artifacts)
            _failure_point("before_retired_cleanup")
            _remove_tree_exact(retired, artifacts, ".retired-legacy-")
            _fsync_dir(artifacts)
        except Exception:
            # The complete retired directory remains recoverable on any
            # failure before recursive cleanup begins.
            return


def _current_input_digest(repo_root: Path) -> str:
    try:
        from artifact_inputs import working_digest

        return working_digest(repo_root)
    except ArtifactError:
        raise
    except Exception as exc:
        raise InternalError(f"cannot compute live input digest: {exc}") from exc


def _current_toolchain(
    repo_root: Path,
    kind: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Toolchain:
    _kind(kind)
    esp32_rs = _physical_esp32_rs(repo_root)
    checker = esp32_rs / "tools" / "build_image.sh"
    try:
        info = checker.lstat()
    except FileNotFoundError as exc:
        raise InvalidError("tools/build_image.sh is missing") from exc
    except OSError as exc:
        raise InternalError(f"cannot inspect tools/build_image.sh: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise InvalidError("tools/build_image.sh must be an executable regular file")
    argv = [str(checker), "--check", "--kind", kind]
    try:
        completed = runner(
            argv,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise InternalError(
            f"cannot execute tools/build_image.sh --check: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise InvalidError(f"tools/build_image.sh --check failed{suffix}")
    try:
        value = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeError, TypeError) as exc:
        raise InvalidError(f"toolchain check emitted malformed JSON: {exc}") from exc
    toolchain = _toolchain_from_dict(value)
    canonical = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    if completed.stdout != canonical:
        raise InvalidError("toolchain check output is not canonical JSON")
    return toolchain


def _verify_current(repo_root: Path, kind: str, bundle: Path) -> Result:
    public_name, _ = _kind(kind)
    esp32_rs = _physical_esp32_rs(repo_root)
    if not isinstance(bundle, Path) or ".." in bundle.parts:
        return Result(EXIT_INVALID, "bundle path is not canonical")
    expected_bundle = esp32_rs / public_name
    try:
        if bundle.absolute().parent.resolve(strict=True) != esp32_rs:
            return Result(EXIT_INVALID, "bundle is outside esp32_rs")
    except OSError as exc:
        return Result(EXIT_INTERNAL, f"cannot resolve bundle parent: {exc}")
    if bundle.name != public_name:
        return Result(EXIT_INVALID, "bundle does not match requested kind")
    fd = _open_locked(_lock_for_esp32_rs(esp32_rs), fcntl.LOCK_SH, False)
    try:
        expected = _current_toolchain(repo_root, kind)
        input_digest = _current_input_digest(repo_root)
        return verify_locked(expected_bundle, expected, input_digest)
    except ArtifactError as exc:
        return Result(exc.code, str(exc))
    except Exception as exc:
        return Result(EXIT_INTERNAL, f"unexpected current verification failure: {exc}")
    finally:
        os.close(fd)


def _raise_result(result: Result) -> None:
    if result.ok:
        return
    error_type = {
        EXIT_MISSING: MissingError,
        EXIT_STALE: StaleError,
        EXIT_INVALID: InvalidError,
        EXIT_INTERNAL: InternalError,
    }.get(result.code, InternalError)
    raise error_type(result.message)


def _acquire_exec_locks(
    repo_root: Path, kinds: tuple[str, ...]
) -> tuple[Path, list[tuple[str, int, Path]]]:
    esp32_rs = _physical_esp32_rs(repo_root)
    held = []
    try:
        for kind in sorted(set(kinds)):
            public_name, _ = _kind(kind)
            fd = _open_locked(_lock_for_esp32_rs(esp32_rs), fcntl.LOCK_SH, True)
            held.append((kind, fd, esp32_rs / public_name))
        return esp32_rs, held
    except BaseException:
        for _, fd, _ in reversed(held):
            os.close(fd)
        raise


def _locked_exec_many(
    repo_root: Path,
    kinds: tuple[str, ...],
    argv: list[str],
) -> NoReturn:
    if not kinds:
        raise InvalidError("at least one kind is required")
    if not argv or not argv[0]:
        raise InvalidError("exec command must not be empty")
    _, held = _acquire_exec_locks(repo_root, kinds)
    try:
        live_digest = _current_input_digest(repo_root)
        for kind, _, bundle in held:
            expected = _current_toolchain(repo_root, kind)
            result = verify_locked(bundle, expected, live_digest)
            _raise_result(result)
        os.execvp(argv[0], argv)
        raise InternalError("execvp returned unexpectedly")
    finally:
        for _, fd, _ in reversed(held):
            os.close(fd)


def locked_exec(repo_root: Path, kind: str, argv: list[str]) -> NoReturn:
    _locked_exec_many(repo_root, (kind,), argv)


def locked_exec_many(
    repo_root: Path,
    kinds: tuple[str, ...],
    argv: list[str],
) -> NoReturn:
    _locked_exec_many(repo_root, kinds, argv)


def _fd_is_open_and_inheritable(fd: int) -> bool:
    try:
        os.fstat(fd)
        return os.get_inheritable(fd)
    except OSError:
        return False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InvalidError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="artifact_provenance.py")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="operation", required=True)
    one = subparsers.add_parser("exec")
    one.add_argument("--kind", required=True, choices=tuple(_KIND_LAYOUT))
    one.add_argument("command", nargs=argparse.REMAINDER)
    many = subparsers.add_parser("exec-many")
    many.add_argument(
        "--kind", action="append", required=True, choices=tuple(_KIND_LAYOUT)
    )
    many.add_argument("command", nargs=argparse.REMAINDER)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--kind", required=True, choices=tuple(_KIND_LAYOUT))
    verify.add_argument("bundle", type=Path)
    return parser


def _command(value: list[str]) -> list[str]:
    result = list(value)
    if result[:1] == ["--"]:
        result.pop(0)
    if not result or not result[0]:
        raise InvalidError("exec command must not be empty")
    return result


def main(
    argv: list[str] | None = None,
    *,
    exec_one: Callable[[Path, str, list[str]], NoReturn] = locked_exec,
    exec_many: Callable[
        [Path, tuple[str, ...], list[str]], NoReturn
    ] = locked_exec_many,
) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.operation == "verify":
            result = _verify_current(args.repo_root, args.kind, args.bundle)
            if not result.ok:
                _raise_result(result)
            return 0
        command = _command(args.command)
        if args.operation == "exec":
            exec_one(args.repo_root, args.kind, command)
        else:
            if len(set(args.kind)) != len(args.kind):
                raise InvalidError("duplicate --kind values are not allowed")
            exec_many(args.repo_root, tuple(args.kind), command)
        raise InternalError("exec helper returned unexpectedly")
    except ArtifactError as exc:
        print(f"artifact_provenance: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:
        print(f"artifact_provenance: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
