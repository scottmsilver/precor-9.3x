#!/usr/bin/env python3
"""Guarded Raspberry Pi backup, flash, and capture tool for the DevKit bundle."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import hmac
import importlib.metadata
import json
import os
import re
import selectors
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, Protocol

import artifact_provenance as provenance


ESPTOOL = "/home/ssilver/.local/bin/esptool"
ESPTOOL_VERSION = "5.3.1"
FLASH_BYTES = 8_388_608
MAX_TOOL_OUTPUT = 256 * 1024
MAX_SERIAL_LINE = 512
MAX_CAPTURE_LINES = 256
MAX_RECEIPT_BYTES = 4096
MAX_PATH_BYTES = 4096
RECEIPT_SCHEMA = 1
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z")
_TIMESTAMP = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_USB_SERIAL = re.compile(
    r"usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_"
    r"([A-Za-z0-9]{1,128})-if00-port0\Z"
)


class BenchError(Exception):
    """A bounded, user-actionable refusal."""


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


@dataclasses.dataclass(frozen=True)
class VerifiedBundle:
    path: Path
    recipe_id: str
    serial_path: str
    manifest_sha256: str
    flash_argv: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class SerialIdentity:
    target: str
    rdev: int
    usb_serial: str


@dataclasses.dataclass(frozen=True)
class Receipt:
    backup_path: Path
    mac: str
    byte_count: int
    sha256: str
    created_at: str


@dataclasses.dataclass(frozen=True)
class StartupReport:
    mac: str
    terminal: str
    lines: tuple[str, ...]


def _raise_from_artifact(exc: Exception) -> NoReturn:
    raise BenchError(f"invalid devkit kind or bundle: {exc}") from exc


def _bounded_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path):
        raise BenchError(f"{label} must be a path")
    encoded = os.fsencode(path)
    if not encoded or len(encoded) > MAX_PATH_BYTES or b"\0" in encoded:
        raise BenchError(f"{label} is invalid or too long")
    return path


def _parse_flash_args(bundle: Path, raw: bytes) -> tuple[str, ...]:
    if len(raw) > provenance.MEMBER_MAX_BYTES["flash_args"]:
        raise BenchError("flash_args exceeds its size limit")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BenchError("flash_args must be ASCII") from exc
    if not text.endswith("\n") or "\0" in text:
        raise BenchError("flash_args is not canonical")
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise BenchError(f"invalid flash_args: {exc}") from exc
    expected = [
        "--flash_mode",
        "qio",
        "--flash_freq",
        "80m",
        "--flash_size",
        "8MB",
        "0x0",
        "bootloader.bin",
        "0x8000",
        "partition-table.bin",
        "0x10000",
        "esp32tap.bin",
    ]
    if tokens != expected:
        raise BenchError("flash_args is not the exact bounded DevKit write recipe")
    return (
        "--flash-mode",
        "qio",
        "--flash-freq",
        "80m",
        "--flash-size",
        "8MB",
        "0x0",
        str(bundle / "bootloader.bin"),
        "0x8000",
        str(bundle / "partition-table.bin"),
        "0x10000",
        str(bundle / "esp32tap.bin"),
    )


def verify_bundle(bundle: Path) -> VerifiedBundle:
    """Verify one complete DevKit bundle without trusting its directory name."""
    bundle = _bounded_path(bundle, "bundle").absolute()
    try:
        info = bundle.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise BenchError("bundle must resolve to a directory")
        names = {entry.name for entry in os.scandir(bundle)}
    except (FileNotFoundError, OSError) as exc:
        raise BenchError(f"cannot inspect bundle: {exc}") from exc
    required = {*provenance.BUNDLE_MEMBERS, provenance.MANIFEST_NAME}
    if names != required:
        raise BenchError("bundle has missing or extra members")
    try:
        manifest, _toolchain = provenance._load_manifest(
            bundle, expected_kind="devkit-bringup"
        )
        records = provenance._member_records(bundle)
    except provenance.ArtifactError as exc:
        _raise_from_artifact(exc)
    if records != manifest["members"]:
        raise BenchError("bundle member size or hash does not match manifest")
    geometry = manifest["flash_geometry"]
    if geometry != provenance.DEVKIT_FLASH_GEOMETRY:
        raise BenchError("manifest flash geometry is not ESP32-S3 8MB")
    try:
        raw_args = provenance._read_safe_bytes(
            bundle / "flash_args",
            "flash_args",
            limit=provenance.MEMBER_MAX_BYTES["flash_args"],
        )
    except provenance.ArtifactError as exc:
        _raise_from_artifact(exc)
    return VerifiedBundle(
        path=bundle,
        recipe_id=manifest["recipe_id"],
        serial_path=manifest["required_serial_device"],
        manifest_sha256=manifest["manifest_sha256"],
        flash_argv=_parse_flash_args(bundle, raw_args),
    )


def inspect_serial(path: str) -> SerialIdentity:
    if (
        not isinstance(path, str)
        or len(os.fsencode(path)) > MAX_PATH_BYTES
        or "\0" in path
    ):
        raise BenchError("serial path is invalid or too long")
    lexical = Path(path)
    match = _USB_SERIAL.fullmatch(lexical.name)
    if not match or lexical.parent != Path("/dev/serial/by-id"):
        raise BenchError("serial must be the exact CP2102N by-id path")
    try:
        if not stat.S_ISLNK(lexical.lstat().st_mode):
            raise BenchError("serial by-id path must be a symlink")
        target = lexical.resolve(strict=True)
        opened = target.stat()
    except FileNotFoundError as exc:
        raise BenchError("serial device is absent") from exc
    except OSError as exc:
        raise BenchError(f"cannot inspect serial device: {exc}") from exc
    if not stat.S_ISCHR(opened.st_mode):
        raise BenchError("serial path does not resolve to a character device")
    expected_usb_serial = match.group(1)
    try:
        from serial.tools import list_ports

        observed = [
            port.serial_number
            for port in list_ports.comports()
            if os.path.realpath(port.device) == str(target)
        ]
    except (ImportError, OSError) as exc:
        raise BenchError(f"cannot inspect USB serial identity: {exc}") from exc
    if observed != [expected_usb_serial]:
        raise BenchError("USB serial identity does not match the by-id path")
    return SerialIdentity(str(target), opened.st_rdev, expected_usb_serial)


def require_serial(
    serial_path: str,
    manifest_path: str,
    *,
    inspect: Callable[[str], SerialIdentity] = inspect_serial,
) -> SerialIdentity:
    try:
        exact = os.fsencode(serial_path) == manifest_path.encode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise BenchError("serial path is not canonical ASCII") from exc
    if not exact:
        raise BenchError("serial path is not byte-exact with the manifest")
    return inspect(serial_path)


def require_same_serial(
    serial_path: str,
    original: SerialIdentity,
    *,
    inspect: Callable[[str], SerialIdentity] = inspect_serial,
) -> SerialIdentity:
    current = inspect(serial_path)
    if current != original:
        raise BenchError("serial device or USB identity changed")
    return current


def run_esptool(argv: list[str], *, timeout: float) -> str:
    if not argv or argv[0] != ESPTOOL or any(not isinstance(arg, str) for arg in argv):
        raise BenchError("refusing non-canonical esptool argv")
    if not 0 < timeout <= 600:
        raise BenchError("esptool timeout is out of bounds")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    output = bytearray()
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise BenchError("esptool timed out")
            for key, _mask in selector.select(min(remaining, 0.25)):
                block = os.read(key.fd, min(65536, MAX_TOOL_OUTPUT - len(output) + 1))
                output.extend(block)
                if len(output) > MAX_TOOL_OUTPUT:
                    process.kill()
                    process.wait()
                    raise BenchError("esptool output exceeded its bound")
        tail = process.stdout.read(MAX_TOOL_OUTPUT - len(output) + 1)
        output.extend(tail)
        if len(output) > MAX_TOOL_OUTPUT:
            raise BenchError("esptool output exceeded its bound")
    finally:
        selector.close()
    text = output.decode("utf-8", "replace")
    if process.returncode != 0:
        raise BenchError(f"esptool failed ({process.returncode}): {text.strip()}")
    return text


def probe_board(
    serial_path: str,
    expected_mac: str,
    *,
    runner: Callable[..., str] = run_esptool,
) -> None:
    if not _MAC.fullmatch(expected_mac):
        raise BenchError("expected MAC must be lowercase canonical form")
    prefix = [ESPTOOL, "--chip", "esp32s3", "--port", serial_path]
    chip = runner([*prefix, "chip-id"], timeout=30)
    if not re.search(r"(?mi)^Chip is ESP32-S3(?:\s|$)", chip):
        raise BenchError("connected chip is not ESP32-S3")
    mac_output = runner([*prefix, "read-mac"], timeout=30)
    matches = re.findall(
        r"(?mi)^MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*$", mac_output
    )
    if matches != [expected_mac]:
        raise BenchError("connected board MAC does not match authorization")
    flash = runner([*prefix, "flash-id"], timeout=30)
    sizes = re.findall(r"(?mi)^Detected flash size:\s*([^\s]+)\s*$", flash)
    if sizes != ["8MB"]:
        raise BenchError("connected board flash is not exactly 8MB")


def _require_secure_directory(path: Path) -> os.stat_result:
    path = _bounded_path(path, "backup directory").absolute()
    try:
        info = path.lstat()
        physical = path.resolve(strict=True)
    except OSError as exc:
        raise BenchError(f"backup directory is unavailable: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or physical != path:
        raise BenchError("backup directory must be a physical non-symlink directory")
    if info.st_uid != os.geteuid():
        raise BenchError("backup directory must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise BenchError("backup directory mode must be exactly 0700")
    return info


def _require_private_file(
    path: Path, label: str, *, exact_size: int | None = None
) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BenchError(f"{label} is unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BenchError(f"{label} must be an owned regular single-link file")
    if info.st_uid != os.geteuid():
        raise BenchError(f"{label} must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise BenchError(f"{label} mode must be exactly 0600")
    if exact_size is not None and info.st_size != exact_size:
        raise BenchError(f"{label} must be exactly {exact_size} bytes")
    return info


def _hash_private_backup(path: Path) -> str:
    directory = path.parent
    directory_before = _require_secure_directory(directory)
    before = _require_private_file(path, "backup", exact_size=FLASH_BYTES)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BenchError(f"cannot open backup safely: {exc}") from exc
    digest = hashlib.sha256()
    count = 0
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise BenchError("backup changed while opening")
        while count <= FLASH_BYTES:
            block = os.read(fd, min(1024 * 1024, FLASH_BYTES - count + 1))
            if not block:
                break
            count += len(block)
            if count > FLASH_BYTES:
                raise BenchError("backup grew beyond 8MB")
            digest.update(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    directory_after = _require_secure_directory(directory)
    current = _require_private_file(path, "backup", exact_size=FLASH_BYTES)
    if count != FLASH_BYTES:
        raise BenchError("backup must be exactly 8MB")
    if (
        (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or (directory_before.st_dev, directory_before.st_ino)
        != (directory_after.st_dev, directory_after.st_ino)
    ):
        raise BenchError("backup or directory changed while hashing")
    return digest.hexdigest()


def _canonical_json(value: dict) -> bytes:
    try:
        raw = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BenchError(f"cannot serialize receipt: {exc}") from exc
    if len(raw) > MAX_RECEIPT_BYTES:
        raise BenchError("receipt exceeds its size bound")
    return raw


def write_receipt(
    backup_path: Path,
    receipt_path: Path,
    mac: str,
    *,
    timestamp: str | None = None,
) -> Receipt:
    """Exclusively attest an existing backup after fully revalidating it."""
    if not _MAC.fullmatch(mac):
        raise BenchError("receipt MAC must be lowercase canonical form")
    backup_path = _bounded_path(backup_path, "backup").absolute()
    receipt_path = _bounded_path(receipt_path, "receipt").absolute()
    if backup_path.parent != receipt_path.parent:
        raise BenchError("receipt and backup must share the secure directory")
    _require_secure_directory(backup_path.parent)
    digest = _hash_private_backup(backup_path)
    created_at = timestamp or dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    if not _TIMESTAMP.fullmatch(created_at):
        raise BenchError("receipt timestamp is not canonical UTC")
    value = {
        "backup_path": str(backup_path.resolve(strict=True)),
        "byte_count": FLASH_BYTES,
        "created_at": created_at,
        "mac": mac,
        "schema_version": RECEIPT_SCHEMA,
        "sha256": digest,
    }
    raw = _canonical_json(value)
    fd = -1
    try:
        fd = os.open(
            receipt_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except FileExistsError as exc:
        raise BenchError("receipt already exists; refusing to overwrite") from exc
    except OSError as exc:
        raise BenchError(f"cannot create receipt: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    _require_secure_directory(receipt_path.parent)
    _require_private_file(receipt_path, "receipt", exact_size=len(raw))
    dir_fd = os.open(receipt_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return Receipt(backup_path, mac, FLASH_BYTES, digest, created_at)


def _read_private_file(path: Path, label: str, limit: int) -> bytes:
    before = _require_private_file(path, label)
    if before.st_size > limit:
        raise BenchError(f"{label} exceeds its size bound")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BenchError(f"cannot read {label}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise BenchError(f"{label} changed while opening")
        chunks: list[bytes] = []
        count = 0
        while block := os.read(fd, min(4096, limit - count + 1)):
            count += len(block)
            if count > limit:
                raise BenchError(f"{label} exceeds its size bound")
            chunks.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    current = _require_private_file(path, label)
    if (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or (
        after.st_dev,
        after.st_ino,
    ) != (
        current.st_dev,
        current.st_ino,
    ):
        raise BenchError(f"{label} changed while reading")
    return b"".join(chunks)


def validate_receipt(receipt_path: Path, *, expected_mac: str | None = None) -> Receipt:
    receipt_path = _bounded_path(receipt_path, "receipt").absolute()
    _require_secure_directory(receipt_path.parent)
    raw = _read_private_file(receipt_path, "receipt", MAX_RECEIPT_BYTES)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BenchError(f"receipt is malformed: {exc}") from exc
    if raw != _canonical_json(value):
        raise BenchError("receipt is not canonical JSON")
    keys = {
        "backup_path",
        "byte_count",
        "created_at",
        "mac",
        "schema_version",
        "sha256",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise BenchError("receipt fields do not match schema")
    if (
        value["schema_version"] != RECEIPT_SCHEMA
        or type(value["schema_version"]) is not int
    ):
        raise BenchError("unsupported receipt schema")
    if value["byte_count"] != FLASH_BYTES or type(value["byte_count"]) is not int:
        raise BenchError("receipt byte count is not exactly 8MB")
    if not isinstance(value["mac"], str) or not _MAC.fullmatch(value["mac"]):
        raise BenchError("receipt MAC is invalid")
    if expected_mac is not None and value["mac"] != expected_mac:
        raise BenchError("receipt MAC does not match connected board")
    if not isinstance(value["sha256"], str) or not _HEX64.fullmatch(value["sha256"]):
        raise BenchError("receipt hash is invalid")
    if not isinstance(value["created_at"], str) or not _TIMESTAMP.fullmatch(
        value["created_at"]
    ):
        raise BenchError("receipt timestamp is invalid")
    if not isinstance(value["backup_path"], str):
        raise BenchError("receipt backup path is invalid")
    backup = _bounded_path(Path(value["backup_path"]), "receipt backup").absolute()
    if str(backup) != value["backup_path"] or backup.parent != receipt_path.parent:
        raise BenchError("receipt backup path is not canonical or colocated")
    digest = _hash_private_backup(backup)
    if not hmac.compare_digest(digest, value["sha256"]):
        raise BenchError("backup hash does not match receipt")
    _require_secure_directory(receipt_path.parent)
    _require_private_file(receipt_path, "receipt", exact_size=len(raw))
    _require_private_file(backup, "backup", exact_size=FLASH_BYTES)
    return Receipt(backup, value["mac"], FLASH_BYTES, digest, value["created_at"])


def _create_backup_file(path: Path) -> None:
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(fd, 0o600)
        os.close(fd)
    except FileExistsError as exc:
        raise BenchError("backup already exists; refusing to overwrite") from exc
    except OSError as exc:
        raise BenchError(f"cannot create backup: {exc}") from exc


def backup_board(
    verified: VerifiedBundle,
    serial_path: str,
    expected_mac: str,
    backup_dir: Path,
    *,
    runner: Callable[..., str] = run_esptool,
    inspect: Callable[[str], SerialIdentity] = inspect_serial,
) -> tuple[Path, Path]:
    identity = require_serial(serial_path, verified.serial_path, inspect=inspect)
    probe_board(serial_path, expected_mac, runner=runner)
    backup_dir = _bounded_path(backup_dir, "backup directory").absolute()
    _require_secure_directory(backup_dir)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = backup_dir / f"factory-{stamp}-8mb.bin"
    receipt = backup_dir / f"factory-{stamp}-8mb.receipt.json"
    _create_backup_file(raw)
    try:
        require_same_serial(serial_path, identity, inspect=inspect)
        runner(
            [
                ESPTOOL,
                "--chip",
                "esp32s3",
                "--port",
                serial_path,
                "read-flash",
                "0x0",
                "0x800000",
                str(raw),
            ],
            timeout=180,
        )
        _require_secure_directory(backup_dir)
        _require_private_file(raw, "backup", exact_size=FLASH_BYTES)
        write_receipt(raw, receipt, expected_mac)
    except Exception:
        if receipt.exists():
            receipt.unlink()
        if raw.exists():
            raw.unlink()
        raise
    return raw, receipt


def authorize_and_flash(
    verified: VerifiedBundle,
    serial_path: str,
    receipt_path: Path,
    expected_mac: str | None = None,
    *,
    runner: Callable[..., str] = run_esptool,
    inspect: Callable[[str], SerialIdentity] = inspect_serial,
) -> None:
    identity = require_serial(serial_path, verified.serial_path, inspect=inspect)
    receipt = validate_receipt(receipt_path, expected_mac=expected_mac)
    require_same_serial(serial_path, identity, inspect=inspect)
    probe_board(serial_path, receipt.mac, runner=runner)
    require_same_serial(serial_path, identity, inspect=inspect)
    runner(
        [
            ESPTOOL,
            "--chip",
            "esp32s3",
            "--port",
            serial_path,
            "--after",
            "no-reset",
            "write-flash",
            *verified.flash_argv,
        ],
        timeout=180,
    )


def _readline(port, *, label: str) -> str | None:
    raw = port.readline(MAX_SERIAL_LINE + 1)
    if not raw:
        return None
    if len(raw) > MAX_SERIAL_LINE or not raw.endswith(b"\n") or b"\0" in raw:
        raise BenchError(f"{label} line is oversized or unterminated")
    try:
        return raw[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchError(f"{label} is not UTF-8") from exc


def capture_startup(
    port, recipe_id: str, timeout: float, *, clock: Clock = SystemClock()
) -> StartupReport:
    if not _HEX64.fullmatch(recipe_id):
        raise BenchError("recipe ID must be exactly 64 lowercase hex characters")
    if not 0 < timeout <= 600:
        raise BenchError("capture timeout is out of bounds")
    deadline = clock.monotonic() + timeout
    identity = "ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS"
    lines: list[str] = []
    terminal: str | None = None
    idle_after_terminal = False
    while clock.monotonic() < deadline and len(lines) < MAX_CAPTURE_LINES:
        line = _readline(port, label="startup")
        if line is None:
            if terminal is not None:
                idle_after_terminal = True
                break
            continue
        if (
            line == identity
            or line.startswith("BUILD ")
            or line.startswith("CHIP ")
            or line.startswith("MEMORY ")
            or line.startswith("PINS ")
            or line.startswith("BRINGUP ")
        ):
            lines.append(line)
        if line.startswith("BRINGUP STAGE0 PASS") or line.startswith("BRINGUP FAIL"):
            terminal = line
    if len(lines) >= MAX_CAPTURE_LINES:
        raise BenchError("startup capture exceeded its line bound")
    if terminal is None or not idle_after_terminal:
        raise BenchError("startup capture timed out without exactly one terminal")
    if lines.count(identity) != 1:
        raise BenchError("startup must contain exactly one identity banner")
    terminals = [
        line
        for line in lines
        if line.startswith("BRINGUP STAGE0 PASS") or line.startswith("BRINGUP FAIL")
    ]
    if len(terminals) != 1:
        raise BenchError("startup must contain exactly one terminal")
    if terminals[0] != "BRINGUP STAGE0 PASS":
        raise BenchError(f"firmware reported FAIL: {terminals[0]}")
    build = [line for line in lines if line.startswith("BUILD ")]
    chip = [line for line in lines if line.startswith("CHIP ")]
    memory = [line for line in lines if line.startswith("MEMORY ")]
    pins = [line for line in lines if line.startswith("PINS ")]
    if not all(len(group) == 1 for group in (build, chip, memory, pins)):
        raise BenchError("startup must contain every field group exactly once")
    build_match = re.fullmatch(
        r"BUILD recipe=([0-9a-f]{64}) git=([0-9a-f]{40})", build[0]
    )
    if not build_match or build_match.group(1) != recipe_id:
        raise BenchError("startup recipe does not match requested recipe")
    chip_match = re.fullmatch(
        r"CHIP model=ESP32-S3 revision=([0-9]{1,3}) mac=([0-9a-f]{2}(?::[0-9a-f]{2}){5}) crystal_mhz=40 reset=([A-Za-z0-9_-]{1,64})",
        chip[0],
    )
    if not chip_match:
        raise BenchError("startup CHIP fields are invalid")
    memory_match = re.fullmatch(
        r"MEMORY flash_bytes=8388608 psram_total=([0-9]{1,10}) internal_free=([0-9]{1,10}) psram_free=([0-9]{1,10})",
        memory[0],
    )
    if (
        not memory_match
        or int(memory_match.group(1)) != FLASH_BYTES
        or int(memory_match.group(2)) <= 0
        or int(memory_match.group(3)) > FLASH_BYTES
    ):
        raise BenchError("startup MEMORY fields are invalid")
    pin_names = (4, 5, 6, 7, 15, 16, 17, 18, 21, 38)
    pin_pattern = "PINS " + " ".join(
        rf"gpio{number}=([01])/(input|output)" for number in pin_names
    )
    pin_match = re.fullmatch(pin_pattern, pins[0])
    if not pin_match:
        raise BenchError("startup PINS fields are invalid")
    directions = dict(zip(pin_names, pin_match.groups()[1::2], strict=True))
    if any(directions[number] != "input" for number in (15, 17, 21)):
        raise BenchError("protected pins are not input directions")
    return StartupReport(chip_match.group(2), terminals[0], tuple(lines))


def _default_open(serial_path: str):
    try:
        import serial
    except ImportError as exc:
        raise BenchError("pyserial is required on the bench host") from exc
    try:
        return serial.Serial(
            port=serial_path,
            baudrate=115200,
            timeout=0.25,
            write_timeout=1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
    except Exception as exc:
        raise BenchError(f"cannot open exact serial port: {exc}") from exc


def _hard_reset(port) -> None:
    try:
        version = importlib.metadata.version("esptool")
        if version != ESPTOOL_VERSION:
            raise BenchError(
                f"esptool Python package must be exactly {ESPTOOL_VERSION}"
            )
        from esptool.reset import HardReset

        HardReset(port).reset()
    except BenchError:
        raise
    except Exception as exc:
        raise BenchError(f"esptool {ESPTOOL_VERSION} HardReset failed: {exc}") from exc


def _neutral(port) -> None:
    port.setDTR(False)
    port.setRTS(False)


def monitor_serial(
    serial_path: str,
    recipe_id: str,
    timeout: float,
    *,
    opener: Callable[[str], object] = _default_open,
    hard_reset: Callable[[object], None] = _hard_reset,
    clock: Clock = SystemClock(),
) -> StartupReport:
    port = opener(serial_path)
    try:
        _neutral(port)
        hard_reset(port)
        return capture_startup(port, recipe_id, timeout, clock=clock)
    finally:
        port.close()


def sample_inputs(
    port,
    sequence: int,
    expected: tuple[int, int, int, int],
    timeout: float,
    *,
    clock: Clock = SystemClock(),
) -> None:
    if type(sequence) is not int or not 0 <= sequence <= 0xFFFF_FFFF:
        raise BenchError("sample sequence is outside u32")
    if len(expected) != 4 or any(value not in (0, 1) for value in expected):
        raise BenchError("sample expectation must contain four bits")
    port.write(f"SAMPLE {sequence}\n".encode("ascii"))
    port.flush()
    deadline = clock.monotonic() + timeout
    wanted = (
        f"INPUT SAMPLE seq={sequence} gpio4={expected[0]} gpio5={expected[1]} "
        f"gpio6={expected[2]} gpio7={expected[3]} dir15=input dir17=input dir21=input"
    )
    lines = 0
    while clock.monotonic() < deadline and lines < MAX_CAPTURE_LINES:
        line = _readline(port, label="sample")
        if line is None:
            continue
        lines += 1
        if line.startswith("INPUT SAMPLE "):
            if line != wanted:
                raise BenchError(
                    "sample response does not match sequence, tuple, or protected directions"
                )
            return
    raise BenchError("matching sample response timed out")


def wait_cold_cycle(
    serial_path: str,
    original: SerialIdentity,
    timeout: float,
    *,
    inspect: Callable[[str], SerialIdentity | None],
    clock: Clock = SystemClock(),
) -> SerialIdentity:
    if not 0 < timeout <= 600:
        raise BenchError("cold-monitor timeout is out of bounds")
    deadline = clock.monotonic() + timeout
    disappeared = False
    while clock.monotonic() < deadline:
        try:
            current = inspect(serial_path)
        except BenchError as exc:
            if "absent" not in str(exc):
                raise
            current = None
        if current is None:
            disappeared = True
        elif disappeared:
            if current != original:
                raise BenchError("reconnected device changed USB serial identity")
            return current
        clock.sleep(0.05)
    if not disappeared:
        raise BenchError("exact serial symlink did not disappear")
    raise BenchError("exact serial symlink did not reappear before timeout")


def _wait_any_terminal(port, timeout: float, *, clock: Clock = SystemClock()) -> None:
    deadline = clock.monotonic() + timeout
    count = 0
    while clock.monotonic() < deadline and count < MAX_CAPTURE_LINES:
        line = _readline(port, label="startup")
        if line is None:
            continue
        count += 1
        if line.startswith("BRINGUP FAIL"):
            raise BenchError(f"firmware reported FAIL: {line}")
        if line == "BRINGUP STAGE0 PASS":
            return
    raise BenchError("firmware did not become ready for sampling")


def _bounded_int(value: str, minimum: int, maximum: int, label: str) -> int:
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise argparse.ArgumentTypeError(f"{label} must be canonical decimal")
    result = int(value)
    if not minimum <= result <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be in {minimum}..{maximum}")
    return result


def _recipe(value: str) -> str:
    if not _HEX64.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "recipe ID must be 64 lowercase hex characters"
        )
    return value


def _mac(value: str) -> str:
    if not _MAC.fullmatch(value):
        raise argparse.ArgumentTypeError("MAC must be lowercase colon-separated hex")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-bundle")
    verify.add_argument("--bundle", type=Path, required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--bundle", type=Path, required=True)
    backup.add_argument("--serial", required=True)
    backup.add_argument("--expected-mac", type=_mac, required=True)
    backup.add_argument("--backup-dir", type=Path, required=True)
    flash = commands.add_parser("flash-monitor")
    flash.add_argument("--bundle", type=Path, required=True)
    flash.add_argument("--serial", required=True)
    flash.add_argument("--receipt", type=Path, required=True)
    flash.add_argument(
        "--timeout",
        type=lambda value: _bounded_int(value, 1, 600, "timeout"),
        default=30,
    )
    monitor = commands.add_parser("monitor")
    monitor.add_argument("--serial", required=True)
    monitor.add_argument("--recipe-id", type=_recipe, required=True)
    monitor.add_argument(
        "--timeout",
        type=lambda value: _bounded_int(value, 1, 600, "timeout"),
        default=30,
    )
    cold = commands.add_parser("cold-monitor")
    cold.add_argument("--serial", required=True)
    cold.add_argument("--recipe-id", type=_recipe, required=True)
    cold.add_argument(
        "--timeout",
        type=lambda value: _bounded_int(value, 1, 600, "timeout"),
        default=180,
    )
    sample = commands.add_parser("sample")
    sample.add_argument("--serial", required=True)
    sample.add_argument(
        "--sequence",
        type=lambda value: _bounded_int(value, 0, 0xFFFF_FFFF, "sequence"),
        required=True,
    )
    sample.add_argument("--expect", required=True)
    return parser


def _parse_expect(value: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"[01],[01],[01],[01]", value):
        raise BenchError("--expect must be exactly four comma-separated bits")
    return tuple(int(part) for part in value.split(","))  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-bundle":
        verified = verify_bundle(args.bundle)
        print(
            f"VERIFIED manifest={verified.manifest_sha256} recipe={verified.recipe_id}"
        )
    elif args.command == "backup":
        verified = verify_bundle(args.bundle)
        raw, receipt = backup_board(
            verified, args.serial, args.expected_mac, args.backup_dir
        )
        print(f"BACKUP path={raw} receipt={receipt}")
    elif args.command == "flash-monitor":
        verified = verify_bundle(args.bundle)
        authorize_and_flash(verified, args.serial, args.receipt)
        require_serial(args.serial, verified.serial_path)
        report = monitor_serial(args.serial, verified.recipe_id, args.timeout)
        print(f"PASS recipe={verified.recipe_id} mac={report.mac}")
    elif args.command == "monitor":
        identity = require_serial(args.serial, provenance.DEVKIT_REQUIRED_SERIAL_DEVICE)
        require_same_serial(args.serial, identity)
        report = monitor_serial(args.serial, args.recipe_id, args.timeout)
        print(f"PASS recipe={args.recipe_id} mac={report.mac}")
    elif args.command == "cold-monitor":
        original = require_serial(args.serial, provenance.DEVKIT_REQUIRED_SERIAL_DEVICE)
        _cold_confirmation(args.serial)
        wait_cold_cycle(
            args.serial,
            original,
            args.timeout,
            inspect=lambda path: _inspect_optional(path),
        )
        port = _default_open(args.serial)
        try:
            _neutral(port)
            report = capture_startup(
                port, args.recipe_id, min(args.timeout, 30), clock=SystemClock()
            )
        finally:
            port.close()
        print(f"PASS cold recipe={args.recipe_id} mac={report.mac}")
    else:
        identity = require_serial(args.serial, provenance.DEVKIT_REQUIRED_SERIAL_DEVICE)
        port = _default_open(args.serial)
        try:
            _neutral(port)
            _hard_reset(port)
            _wait_any_terminal(port, 30)
            require_same_serial(args.serial, identity)
            sample_inputs(port, args.sequence, _parse_expect(args.expect), 30)
        finally:
            port.close()
        print(f"PASS sample sequence={args.sequence} tuple={args.expect}")
    return 0


def _inspect_optional(path: str) -> SerialIdentity | None:
    try:
        return inspect_serial(path)
    except BenchError as exc:
        if "absent" in str(exc):
            return None
        raise


def _cold_confirmation(serial_path: str) -> None:
    print(
        f"Remove exact UART USB {serial_path}, then type REMOVED: ",
        end="",
        flush=True,
    )
    raw = sys.stdin.buffer.readline(33)
    if raw != b"REMOVED\n":
        raise BenchError("cold-monitor confirmation must be exactly REMOVED")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
