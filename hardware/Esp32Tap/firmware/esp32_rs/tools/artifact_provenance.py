#!/usr/bin/env python3
"""Attest, publish, and lease immutable ESP32Tap flash bundles.

The public build paths are relative symlinks to content-addressed generations.
Readers and publishers use the same worktree-specific flock as build.sh and
qemu_session.py, so an exec'd consumer retains a continuous read lease.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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

MAX_MANIFEST_BYTES = 1024 * 1024
MEMBER_MAX_BYTES = {
    "esp32tap.bin": 8 * 1024 * 1024,
    "bootloader.bin": 1024 * 1024,
    "partition-table.bin": 128 * 1024,
    "flash_args": 64 * 1024,
    "sdkconfig": 2 * 1024 * 1024,
}
MAX_TOOLCHAIN_SCALAR_LENGTH = 16 * 1024
MAX_FEATURE_COUNT = 128
MAX_FEATURE_NAME_LENGTH = 128
CHECK_TIMEOUT_SECONDS = 10.0
MAX_CHECK_OUTPUT_BYTES = 1024 * 1024
CHECK_TERMINATE_GRACE_SECONDS = 1.0
BOUNDED_READ_CHUNK_BYTES = 1024 * 1024
LEGACY_MARKER_MAX_BYTES = 4096
LEGACY_MARKER_SCHEMA_VERSION = 1

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
            if len(value) > MAX_TOOLCHAIN_SCALAR_LENGTH:
                raise ValueError(f"{name} exceeds the maximum length")
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
        if len(self.features) > MAX_FEATURE_COUNT:
            raise ValueError("too many features")
        if len(set(self.features)) != len(self.features):
            raise ValueError("duplicate feature")
        if any(len(feature) > MAX_FEATURE_NAME_LENGTH for feature in self.features):
            raise ValueError("feature name exceeds the maximum length")
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
    result = encoded + b"\n"
    if len(result) > MAX_MANIFEST_BYTES:
        raise InvalidError("manifest exceeds the 1 MiB limit")
    return result


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
    limit = MEMBER_MAX_BYTES.get(path.name)
    if limit is not None and info.st_size > limit:
        raise InvalidError(f"{path.name} exceeds its size limit")
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
        if limit is not None and opened.st_size > limit:
            raise InvalidError(f"{path.name} exceeds its size limit")
        size = 0
        while block := os.read(
            fd,
            (
                BOUNDED_READ_CHUNK_BYTES
                if limit is None
                else min(BOUNDED_READ_CHUNK_BYTES, limit - size + 1)
            ),
        ):
            size += len(block)
            if limit is not None and size > limit:
                raise InvalidError(f"{path.name} exceeds its size limit")
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


def _read_safe_bytes(path: Path, label: str, limit: int = MAX_MANIFEST_BYTES) -> bytes:
    before = _safe_file(path, label)
    if before.st_size > limit:
        raise InvalidError(f"{label} exceeds its size limit")
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
        if opened.st_size > limit:
            raise InvalidError(f"{label} exceeds its size limit")
        chunks = []
        size = 0
        while block := os.read(fd, min(BOUNDED_READ_CHUNK_BYTES, limit - size + 1)):
            size += len(block)
            if size > limit:
                raise InvalidError(f"{label} exceeds its size limit")
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
        if entry["size"] > MEMBER_MAX_BYTES[expected_name]:
            raise InvalidError(f"{expected_name} exceeds its size limit")
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
    if not _generation_is_sealed(generation):
        raise InvalidError("artifact generation must be immutable mode 0555/0444")
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


def _normalize_lock_file(fd: int, path: Path) -> os.stat_result:
    try:
        opened = os.fstat(fd)
        lexical = path.lstat()
    except OSError as exc:
        raise InvalidError(f"cannot safely inspect artifact lock: {exc}") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != os.geteuid()
        or (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino)
    ):
        raise InvalidError(
            "artifact lock must be one owned regular file at its exact path"
        )
    if stat.S_IMODE(opened.st_mode) != 0o600:
        try:
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise InternalError(f"cannot normalize artifact lock mode: {exc}") from exc
    try:
        normalized = os.fstat(fd)
        current = path.lstat()
    except OSError as exc:
        raise InvalidError(f"artifact lock changed while normalizing: {exc}") from exc
    if (
        not stat.S_ISREG(normalized.st_mode)
        or normalized.st_nlink != 1
        or normalized.st_uid != os.geteuid()
        or stat.S_IMODE(normalized.st_mode) != 0o600
        or (normalized.st_dev, normalized.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise InvalidError("artifact lock changed while normalizing")
    return normalized


def _open_locked(path: Path, operation: int, inheritable: bool) -> int:
    """Open the exact owned, single-link, regular 0600 lock and flock it."""
    fd = -1
    try:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(path, flags, 0o600)
        os.set_inheritable(fd, inheritable)
        opened = _normalize_lock_file(fd, path)
        fcntl.flock(fd, operation)
        locked_path = path.lstat()
        if (opened.st_dev, opened.st_ino) != (
            locked_path.st_dev,
            locked_path.st_ino,
        ):
            raise InvalidError("artifact lock path changed while acquiring its flock")
        return fd
    except InvalidError:
        if fd >= 0:
            os.close(fd)
        raise
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise InvalidError(
                "artifact lock must be a non-symlink regular file"
            ) from exc
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
    limit = MEMBER_MAX_BYTES[source.name]
    if before.st_size > limit or expected["size"] > limit:
        raise InvalidError(f"{source.name} exceeds its size limit")
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
        if opened.st_size > limit:
            raise InvalidError(f"{source.name} exceeds its size limit")
        if opened.st_size != expected["size"]:
            raise InvalidError(f"{source.name} does not match manifest")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        digest = hashlib.sha256()
        size = 0
        copy_limit = min(limit, expected["size"])
        while block := os.read(
            source_fd,
            min(BOUNDED_READ_CHUNK_BYTES, copy_limit - size + 1),
        ):
            size += len(block)
            if size > copy_limit:
                raise InvalidError(f"{source.name} does not match manifest")
            digest.update(block)
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
        os.fchmod(destination_fd, 0o444)
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
        return _generation_is_sealed(generation) and (
            _member_records(generation) == manifest["members"]
        )
    except (OSError, ArtifactError):
        return False


def _remove_tree_exact(
    path: Path,
    artifacts: Path,
    prefix: str,
    expected_identity: tuple[int, int],
) -> None:
    if path.parent != artifacts or not path.name.startswith(prefix):
        raise InternalError("refusing unsafe backup removal target")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != expected_identity
    ):
        raise InvalidError("retired legacy directory identity changed")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise InternalError("safe descriptor-based tree removal is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        current = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != expected_identity
            or (current.st_dev, current.st_ino) != expected_identity
        ):
            raise InvalidError("retired legacy directory changed before cleanup")
        shutil.rmtree(path)
    finally:
        os.close(fd)


def _failure_point(_name: str) -> None:
    """Monkeypatch seam for deterministic crash/rollback tests."""


def _rename_exchange(left: Path, right: Path) -> None:
    """Atomically exchange two Linux directory entries or fail closed."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise InternalError("renameat2(RENAME_EXCHANGE) is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_exchange = 2
    if (
        renameat2(
            at_fdcwd,
            os.fsencode(left),
            at_fdcwd,
            os.fsencode(right),
            rename_exchange,
        )
        != 0
    ):
        error = ctypes.get_errno()
        if error in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise InternalError(
                "renameat2(RENAME_EXCHANGE) is unavailable on this filesystem"
            )
        raise OSError(error, os.strerror(error), f"{left} <-> {right}")


def _generation_is_sealed(generation: Path) -> bool:
    info = generation.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o555:
        return False
    for name in (*BUNDLE_MEMBERS, MANIFEST_NAME):
        member = (generation / name).lstat()
        if (
            not stat.S_ISREG(member.st_mode)
            or member.st_nlink != 1
            or stat.S_IMODE(member.st_mode) != 0o444
        ):
            return False
    return True


def _legacy_transaction_paths(
    esp32_rs: Path,
    artifacts: Path,
    public_name: str,
    token: str,
) -> tuple[Path, Path, Path]:
    prefix = f".artifact-provenance-legacy-{public_name}-{token}"
    return (
        esp32_rs / f"{prefix}.swap",
        esp32_rs / f"{prefix}.json",
        artifacts / f".retired-legacy-{public_name}-{token}",
    )


def _legacy_marker_bytes(marker: dict) -> bytes:
    try:
        encoded = (
            json.dumps(
                marker,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidError(f"invalid legacy recovery marker: {exc}") from exc
    if len(encoded) > LEGACY_MARKER_MAX_BYTES:
        raise InvalidError("legacy recovery marker exceeds its size limit")
    return encoded


def _write_legacy_marker(path: Path, marker: dict) -> os.stat_result:
    encoded = _legacy_marker_bytes(marker)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(path, flags, 0o400)
    created = os.fstat(fd)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        created = os.fstat(fd)
    except Exception:
        try:
            current = path.lstat()
            if (created.st_dev, created.st_ino) == (current.st_dev, current.st_ino):
                path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    return created


def _load_legacy_marker(
    path: Path,
    public_name: str,
    token: str,
) -> tuple[dict, os.stat_result]:
    info = _safe_file(path, "legacy recovery marker")
    if (
        info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_size > LEGACY_MARKER_MAX_BYTES
    ):
        raise InvalidError("legacy recovery marker is not a private owned file")
    raw = _read_safe_bytes(
        path, "legacy recovery marker", limit=LEGACY_MARKER_MAX_BYTES
    )
    try:
        marker = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidError(f"malformed legacy recovery marker: {exc}") from exc
    required = {
        "legacy_dev",
        "legacy_ino",
        "public_name",
        "schema_version",
        "target",
        "token",
    }
    _, artifact_name = _kind(_bundle_kind_from_name(Path(public_name)) or "")
    if (
        not isinstance(marker, dict)
        or set(marker) != required
        or type(marker.get("schema_version")) is not int
        or marker.get("schema_version") != LEGACY_MARKER_SCHEMA_VERSION
        or marker.get("public_name") != public_name
        or marker.get("token") != token
        or type(marker.get("legacy_dev")) is not int
        or marker["legacy_dev"] < 0
        or type(marker.get("legacy_ino")) is not int
        or marker["legacy_ino"] <= 0
        or not isinstance(marker.get("target"), str)
        or not re.fullmatch(
            rf"\.artifacts/{re.escape(artifact_name)}/[0-9a-f]{{64}}",
            marker["target"],
        )
        or raw != _legacy_marker_bytes(marker)
    ):
        raise InvalidError("legacy recovery marker fields are invalid")
    return marker, info


def _unlink_exact_marker(path: Path, expected: os.stat_result) -> None:
    current = path.lstat()
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise InvalidError("legacy recovery marker changed before cleanup")
    path.unlink()


def _recover_legacy_exchange_leftovers(
    esp32_rs: Path, artifacts: Path, public_name: str
) -> None:
    pattern = re.compile(
        rf"\.artifact-provenance-legacy-{re.escape(public_name)}-"
        r"([0-9a-f]{64})\.json\Z"
    )
    for entry in sorted(os.scandir(esp32_rs), key=lambda item: item.name):
        match = pattern.fullmatch(entry.name)
        if match is None:
            continue
        token = match.group(1)
        leftover, marker_path, retired = _legacy_transaction_paths(
            esp32_rs, artifacts, public_name, token
        )
        marker, marker_info = _load_legacy_marker(marker_path, public_name, token)
        public = esp32_rs / public_name
        try:
            public_info = public.lstat()
        except OSError as exc:
            raise InvalidError(
                f"legacy recovery has no valid public commit state: {exc}"
            ) from exc
        legacy_identity = (marker["legacy_dev"], marker["legacy_ino"])
        if (
            stat.S_ISDIR(public_info.st_mode)
            and (
                public_info.st_dev,
                public_info.st_ino,
            )
            == legacy_identity
        ):
            try:
                temporary_info = leftover.lstat()
            except OSError as exc:
                raise InvalidError(
                    f"legacy recovery has no valid precommit state: {exc}"
                ) from exc
            if (
                not stat.S_ISLNK(temporary_info.st_mode)
                or os.readlink(leftover) != marker["target"]
            ):
                raise InvalidError("legacy recovery precommit swap is invalid")
            current = leftover.lstat()
            if (current.st_dev, current.st_ino) != (
                temporary_info.st_dev,
                temporary_info.st_ino,
            ):
                raise InvalidError("legacy recovery precommit swap changed")
            leftover.unlink()
            _unlink_exact_marker(marker_path, marker_info)
            _fsync_dir(esp32_rs)
            continue
        if (
            not stat.S_ISLNK(public_info.st_mode)
            or os.readlink(public) != marker["target"]
        ):
            raise InvalidError("legacy recovery has no valid public commit state")
        if os.path.lexists(leftover) and os.path.lexists(retired):
            raise InvalidError("legacy recovery has conflicting old directories")
        candidate = leftover if os.path.lexists(leftover) else retired
        if os.path.lexists(candidate):
            candidate_info = candidate.lstat()
            if (
                not stat.S_ISDIR(candidate_info.st_mode)
                or (candidate_info.st_dev, candidate_info.st_ino) != legacy_identity
            ):
                raise InvalidError("legacy recovery old directory identity is invalid")
            if candidate == leftover:
                if os.path.lexists(retired):
                    raise InvalidError("legacy recovery retirement path exists")
                os.rename(leftover, retired)
                moved = retired.lstat()
                if (moved.st_dev, moved.st_ino) != legacy_identity:
                    if not os.path.lexists(leftover):
                        os.rename(retired, leftover)
                        _fsync_dir(esp32_rs)
                        _fsync_dir(artifacts)
                    raise InvalidError("legacy recovery directory changed while moving")
                _fsync_dir(esp32_rs)
                _fsync_dir(artifacts)
        _unlink_exact_marker(marker_path, marker_info)
        _fsync_dir(esp32_rs)


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


def _validate_exclusive_lock_fd(lock_fd: int, expected_path: Path) -> None:
    if type(lock_fd) is not int or lock_fd < 0:
        raise InvalidError("lock_fd must be an open integer descriptor")
    try:
        inheritable = os.get_inheritable(lock_fd)
    except OSError as exc:
        raise InvalidError(f"lock_fd is not valid: {exc}") from exc
    if not inheritable:
        raise InvalidError("lock_fd must be explicitly inheritable")
    supplied = _normalize_lock_file(lock_fd, expected_path)
    try:
        lines = (
            Path(f"/proc/self/fdinfo/{lock_fd}")
            .read_text(encoding="ascii")
            .splitlines()
        )
    except OSError as exc:
        raise InternalError(f"cannot inspect caller lock ownership: {exc}") from exc
    expected_device = (os.major(supplied.st_dev), os.minor(supplied.st_dev))
    for line in lines:
        if not line.startswith("lock:"):
            continue
        fields = line.removeprefix("lock:").split()
        if len(fields) < 8 or fields[1] != "FLOCK":
            continue
        try:
            owner = int(fields[4])
            lock_device, inode = fields[5].rsplit(":", 1)
            major, minor = (int(part, 16) for part in lock_device.split(":", 1))
        except (ValueError, IndexError):
            continue
        # flock(1) records its short-lived helper PID even though the lock
        # remains attached to fd 9's inherited open file description.
        if (
            owner > 0
            and (major, minor) == expected_device
            and int(inode) == supplied.st_ino
        ):
            if fields[3] != "WRITE":
                raise InvalidError("lock_fd holds a shared lock, not exclusive")
            try:
                # This is a no-op only after fdinfo proves this exact open file
                # description already owns WRITE; it never upgrades READ/unlocked.
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InvalidError("lock_fd lost its exclusive flock") from exc
            try:
                locked_path = expected_path.lstat()
            except OSError as exc:
                raise InvalidError(f"artifact lock path changed: {exc}") from exc
            if (supplied.st_dev, supplied.st_ino) != (
                locked_path.st_dev,
                locked_path.st_ino,
            ):
                raise InvalidError("artifact lock path changed after validation")
            return
    raise InvalidError("lock_fd has no exclusive flock on its open file description")


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
    _recover_legacy_exchange_leftovers(esp32_rs, artifacts, public_link.name)

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
            manifest_fd = os.open(
                manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                encoded_manifest = manifest_bytes(normalized)
                view = memoryview(encoded_manifest)
                while view:
                    written = os.write(manifest_fd, view)
                    view = view[written:]
                os.fchmod(manifest_fd, 0o444)
                os.fsync(manifest_fd)
            finally:
                os.close(manifest_fd)
            os.chmod(temp_generation, 0o555)
            _fsync_dir(temp_generation)
            os.rename(temp_generation, generation)
            temp_generation = None
            _fsync_dir(kind_root)
            _fsync_dir(artifacts)
        finally:
            if temp_generation is not None and os.path.lexists(temp_generation):
                os.chmod(temp_generation, 0o755)
                shutil.rmtree(temp_generation)
    _failure_point("after_generation")

    relative_target = Path(".artifacts") / artifact_name / identity
    if public_link.is_symlink() and os.readlink(public_link) == str(relative_target):
        _fsync_dir(kind_root)
        _fsync_dir(artifacts)
        _fsync_dir(esp32_rs)
        return

    token = secrets.token_hex(32)
    legacy_public = False
    legacy_info: os.stat_result | None = None
    old_target: str | None = None
    legacy_exchanged = False
    swapped = False
    if os.path.lexists(public_link):
        old_info = public_link.lstat()
        if stat.S_ISLNK(old_info.st_mode):
            old_target = os.readlink(public_link)
        elif stat.S_ISDIR(old_info.st_mode):
            legacy_public = True
            legacy_info = old_info
        else:
            raise InvalidError(
                "public path is neither a legacy directory nor a symlink"
            )
    marker_path: Path | None = None
    marker_info: os.stat_result | None = None
    retired: Path | None = None
    if legacy_public:
        temp_link, marker_path, retired = _legacy_transaction_paths(
            esp32_rs, artifacts, public_link.name, token
        )
    else:
        temp_link = (
            esp32_rs / f".artifact-provenance-link-{public_link.name}-{token}.swap"
        )
    rollback_link = (
        esp32_rs / f".artifact-provenance-rollback-{public_link.name}-{token}.link"
    )
    os.symlink(str(relative_target), temp_link)
    if old_target is not None:
        os.symlink(old_target, rollback_link)
    try:
        if legacy_public:
            assert legacy_info is not None
            assert marker_path is not None
            marker_info = _write_legacy_marker(
                marker_path,
                {
                    "legacy_dev": legacy_info.st_dev,
                    "legacy_ino": legacy_info.st_ino,
                    "public_name": public_link.name,
                    "schema_version": LEGACY_MARKER_SCHEMA_VERSION,
                    "target": str(relative_target),
                    "token": token,
                },
            )
        # Make both the new and rollback symlinks durable before mutation.
        _fsync_dir(esp32_rs)
        _failure_point("before_link_swap")
        if legacy_public:
            _rename_exchange(public_link, temp_link)
            legacy_exchanged = True
            swapped = True
            _failure_point("after_legacy_exchange")
            _failure_point("after_legacy_backup")
        else:
            os.replace(temp_link, public_link)
            swapped = True
        _failure_point("after_link_swap")
        _failure_point("before_commit")
        _fsync_dir(esp32_rs)
    except Exception:
        try:
            if legacy_exchanged:
                _rename_exchange(public_link, temp_link)
                legacy_exchanged = False
            elif swapped and old_target is not None:
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
            if os.path.lexists(temp_link):
                temp_link.unlink()
            if os.path.lexists(rollback_link):
                rollback_link.unlink()
            if marker_path is not None and marker_info is not None:
                _unlink_exact_marker(marker_path, marker_info)
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
    if legacy_public and os.path.lexists(temp_link):
        assert retired is not None
        assert legacy_info is not None
        legacy_identity = (legacy_info.st_dev, legacy_info.st_ino)
        try:
            if os.path.lexists(retired):
                raise InvalidError("legacy retirement path unexpectedly exists")
            before_retirement = temp_link.lstat()
            if (
                not stat.S_ISDIR(before_retirement.st_mode)
                or (before_retirement.st_dev, before_retirement.st_ino)
                != legacy_identity
            ):
                raise InvalidError("legacy swap identity changed before retirement")
            os.replace(temp_link, retired)
            after_retirement = retired.lstat()
            if (
                not stat.S_ISDIR(after_retirement.st_mode)
                or (after_retirement.st_dev, after_retirement.st_ino) != legacy_identity
            ):
                if not os.path.lexists(temp_link):
                    os.replace(retired, temp_link)
                    _fsync_dir(esp32_rs)
                    _fsync_dir(artifacts)
                raise InvalidError("legacy swap identity changed during retirement")
            _fsync_dir(esp32_rs)
            _fsync_dir(artifacts)
            _failure_point("before_retired_cleanup")
            _remove_tree_exact(
                retired,
                artifacts,
                ".retired-legacy-",
                legacy_identity,
            )
            _fsync_dir(artifacts)
            if marker_path is not None and marker_info is not None:
                _unlink_exact_marker(marker_path, marker_info)
                _fsync_dir(esp32_rs)
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


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=CHECK_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run_bounded_checker(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output: int,
) -> subprocess.CompletedProcess[str]:
    with (
        tempfile.SpooledTemporaryFile(max_size=max_output + 1, mode="w+b") as stdout,
        tempfile.SpooledTemporaryFile(max_size=max_output + 1, mode="w+b") as stderr,
    ):
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise InternalError(
                f"cannot execute tools/build_image.sh --check: {exc}"
            ) from exc
        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        sizes = {stdout: 0, stderr: 0}
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop_process_group(process)
                    raise InternalError(
                        f"tools/build_image.sh --check timed out after {timeout:g}s"
                    )
                for key, _ in selector.select(min(remaining, 0.1)):
                    block = os.read(key.fd, 64 * 1024)
                    if not block:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    destination = key.data
                    sizes[destination] += len(block)
                    if sizes[destination] > max_output:
                        _stop_process_group(process)
                        raise InvalidError(
                            "toolchain check output exceeds its size limit"
                        )
                    destination.write(block)
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                _stop_process_group(process)
                raise InternalError(
                    f"tools/build_image.sh --check timed out after {timeout:g}s"
                ) from exc
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                _stop_process_group(process)
        stdout.seek(0)
        stderr.seek(0)
        try:
            stdout_text = stdout.read(max_output + 1).decode("utf-8")
            stderr_text = stderr.read(max_output + 1).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidError("toolchain check output is not UTF-8") from exc
        return subprocess.CompletedProcess(
            argv, process.returncode, stdout_text, stderr_text
        )


def _current_toolchain(
    repo_root: Path,
    kind: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
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
        completed = (runner or _run_bounded_checker)(
            argv,
            cwd=repo_root,
            timeout=CHECK_TIMEOUT_SECONDS,
            max_output=MAX_CHECK_OUTPUT_BYTES,
        )
    except OSError as exc:
        raise InternalError(
            f"cannot execute tools/build_image.sh --check: {exc}"
        ) from exc
    if (
        not isinstance(completed.stdout, str)
        or not isinstance(completed.stderr, str)
        or len(completed.stdout.encode("utf-8")) > MAX_CHECK_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8")) > MAX_CHECK_OUTPUT_BYTES
    ):
        raise InvalidError("toolchain check output exceeds its size limit")
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
