from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import artifact_provenance as provenance
import devkit_bench as bench


REAL_BUNDLE = (
    Path(__file__).resolve().parents[1]
    / ".artifacts/devkit/5e2a8c825fcdd4468c29e5803c7cf79c2b960de0929d78e82627512a15f5d64c"
)
SERIAL = provenance.DEVKIT_REQUIRED_SERIAL_DEVICE
MAC = "94:a9:90:db:b0:e4"
RECIPE = "ac3caa97900889b8f5d4d8f03199f7fd1b681dd73ade1dfe5d498ed89ced25d1"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    target = tmp_path / "bundle"
    shutil.copytree(REAL_BUNDLE, target)
    for path in target.iterdir():
        path.chmod(0o600)
    target.chmod(0o700)
    return target


def rewrite_manifest(bundle: Path, mutate, *, canonical: bool = True) -> None:
    path = bundle / provenance.MANIFEST_NAME
    value = json.loads(path.read_bytes())
    mutate(value)
    unsigned = dict(value)
    unsigned.pop("manifest_sha256", None)
    value["manifest_sha256"] = hashlib.sha256(
        provenance.manifest_bytes(unsigned)
    ).hexdigest()
    raw = provenance.manifest_bytes(value)
    if not canonical:
        raw = json.dumps(value, indent=2).encode() + b"\n"
    path.write_bytes(raw)


def test_verify_bundle_accepts_real_sealed_generation() -> None:
    verified = bench.verify_bundle(REAL_BUNDLE)
    assert verified.recipe_id == RECIPE
    assert verified.serial_path == SERIAL
    assert verified.flash_argv == (
        "--flash-mode",
        "qio",
        "--flash-freq",
        "80m",
        "--flash-size",
        "8MB",
        "0x0",
        str(REAL_BUNDLE / "bootloader.bin"),
        "0x8000",
        str(REAL_BUNDLE / "partition-table.bin"),
        "0x10000",
        str(REAL_BUNDLE / "esp32tap.bin"),
    )


def isolated_verify(tmp_path: Path, bundle: Path) -> subprocess.CompletedProcess[str]:
    tool = tmp_path / "devkit_bench.py"
    shutil.copy2(Path(bench.__file__), tool)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(tool),
            "verify-bundle",
            "--bundle",
            str(bundle),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_copied_tool_verifies_bundle_without_repository_modules(
    tmp_path: Path,
) -> None:
    copied_bundle = tmp_path / "bundle"
    shutil.copytree(REAL_BUNDLE, copied_bundle)
    result = isolated_verify(tmp_path, copied_bundle)
    assert result.returncode == 0, result.stderr
    assert f"VERIFIED manifest={REAL_BUNDLE.name} recipe={RECIPE}" in result.stdout


def test_copied_tool_still_rejects_malformed_bundle(tmp_path: Path) -> None:
    copied_bundle = tmp_path / "bundle"
    shutil.copytree(REAL_BUNDLE, copied_bundle)
    copied_bundle.chmod(0o700)
    (copied_bundle / "unexpected").write_bytes(b"x")
    result = isolated_verify(tmp_path, copied_bundle)
    assert result.returncode == 2
    assert "missing or extra members" in result.stderr


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update(kind="production"), "kind"),
        (
            lambda value: value.update(
                flash_geometry={
                    "chip": "esp32",
                    "size": 8_388_608,
                    "offsets": [0, 32768, 65536],
                }
            ),
            "geometry",
        ),
        (
            lambda value: value.update(
                flash_geometry={
                    "chip": "esp32s3",
                    "size": 4_194_304,
                    "offsets": [0, 32768, 65536],
                }
            ),
            "geometry",
        ),
        (
            lambda value: value.update(
                required_serial_device="/dev/serial/by-id/other"
            ),
            "serial",
        ),
    ],
)
def test_verify_bundle_rejects_wrong_manifest_identity(
    bundle: Path, mutation, match: str
) -> None:
    rewrite_manifest(bundle, mutation)
    with pytest.raises(bench.BenchError, match=match):
        bench.verify_bundle(bundle)


def test_verify_bundle_rejects_noncanonical_and_oversized_manifest(
    bundle: Path,
) -> None:
    rewrite_manifest(bundle, lambda _value: None, canonical=False)
    with pytest.raises(bench.BenchError, match="canonical"):
        bench.verify_bundle(bundle)
    (bundle / provenance.MANIFEST_NAME).write_bytes(
        b" " * (provenance.MAX_MANIFEST_BYTES + 1)
    )
    with pytest.raises(bench.BenchError, match="size limit"):
        bench.verify_bundle(bundle)


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "symlink", "hardlink", "size", "hash"]
)
def test_verify_bundle_rejects_unsafe_or_changed_members(
    bundle: Path, mutation: str
) -> None:
    member = bundle / "esp32tap.bin"
    if mutation == "missing":
        member.unlink()
    elif mutation == "extra":
        (bundle / "extra").write_bytes(b"x")
    elif mutation == "symlink":
        member.unlink()
        member.symlink_to("bootloader.bin")
    elif mutation == "hardlink":
        os.link(member, bundle / "extra-link")
    elif mutation == "size":
        member.write_bytes(member.read_bytes() + b"x")
    else:
        data = bytearray(member.read_bytes())
        data[0] ^= 1
        member.write_bytes(data)
    with pytest.raises(bench.BenchError):
        bench.verify_bundle(bundle)


def test_flash_args_are_bounded_and_exact(bundle: Path) -> None:
    args = bundle / "flash_args"
    args.write_text(
        "--flash_mode qio --flash_freq 80m --flash_size 8MB\n0x0 ../../evil\n"
    )
    rewrite_manifest(
        bundle,
        lambda value: value["members"][3].update(
            size=args.stat().st_size,
            sha256=hashlib.sha256(args.read_bytes()).hexdigest(),
        ),
    )
    with pytest.raises(bench.BenchError, match="flash_args"):
        bench.verify_bundle(bundle)


def test_serial_must_be_byte_exact_and_same_character_device() -> None:
    identity = bench.SerialIdentity("/dev/ttyUSB0", 188, "4cd513")

    def inspect(path: str) -> bench.SerialIdentity:
        if path == SERIAL:
            return identity
        return bench.SerialIdentity("/dev/ttyUSB0", 189, "other")

    assert bench.require_serial(SERIAL, SERIAL, inspect=inspect) == identity
    with pytest.raises(bench.BenchError, match="manifest"):
        bench.require_serial("/dev/ttyUSB0", SERIAL, inspect=inspect)
    changed = bench.SerialIdentity("/dev/ttyUSB1", 189, "4cd513")
    with pytest.raises(bench.BenchError, match="changed"):
        bench.require_same_serial(SERIAL, identity, inspect=lambda _path: changed)


class FakeRunner:
    def __init__(self, *, chip="ESP32-S3", mac=MAC, size="8MB") -> None:
        self.chip = chip
        self.mac = mac
        self.size = size
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], *, timeout: float) -> str:
        assert argv[0] == "/home/ssilver/.local/bin/esptool"
        self.calls.append(argv)
        command = argv[-1]
        if command == "chip-id":
            return f"Chip is {self.chip} (QFN56)\n"
        if command == "read-mac":
            return f"MAC: {self.mac}\n"
        if command == "flash-id":
            return f"Detected flash size: {self.size}\n"
        return "ok\n"


@pytest.mark.parametrize(
    "runner,match",
    [
        (FakeRunner(chip="ESP32-C3"), "chip"),
        (FakeRunner(mac="00:11:22:33:44:55"), "MAC"),
        (FakeRunner(size="4MB"), "flash"),
    ],
)
def test_board_probe_rejects_wrong_chip_mac_or_flash(
    runner: FakeRunner, match: str
) -> None:
    with pytest.raises(bench.BenchError, match=match):
        bench.probe_board(SERIAL, MAC, runner=runner)
    assert all(isinstance(call, list) for call in runner.calls)


def secure_dir(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


def make_backup(path: Path, data: bytes = b"x" * bench.FLASH_BYTES) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def test_receipt_round_trip_is_canonical_private_and_detects_mutation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    raw = directory / "factory.bin"
    make_backup(raw)
    receipt = directory / "factory.receipt.json"
    old_umask = os.umask(0)
    try:
        bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:56Z")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert receipt.read_bytes() == provenance.manifest_bytes(
        json.loads(receipt.read_bytes())
    )
    authorized = bench.validate_receipt(receipt, expected_mac=MAC)
    assert authorized.backup_path == raw.resolve()
    with raw.open("r+b") as output:
        output.write(b"y")
    with pytest.raises(bench.BenchError, match="hash"):
        bench.validate_receipt(receipt, expected_mac=MAC)


@pytest.mark.parametrize(
    "target,mode", [("dir", 0o755), ("raw", 0o644), ("receipt", 0o644)]
)
def test_receipt_rejects_unsafe_directory_or_files(
    tmp_path: Path, target: str, mode: int
) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    raw = directory / "factory.bin"
    make_backup(raw)
    receipt = directory / "factory.receipt.json"
    bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:56Z")
    {"dir": directory, "raw": raw, "receipt": receipt}[target].chmod(mode)
    with pytest.raises(bench.BenchError, match="mode"):
        bench.validate_receipt(receipt, expected_mac=MAC)


def test_receipt_never_overwrites(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    raw = directory / "factory.bin"
    make_backup(raw)
    receipt = directory / "factory.receipt.json"
    bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:56Z")
    with pytest.raises(bench.BenchError, match="exists"):
        bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:57Z")


@pytest.mark.parametrize("target", ["raw", "receipt"])
def test_receipt_rejects_hardlinked_backup_or_receipt(
    tmp_path: Path, target: str
) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    raw = directory / "factory.bin"
    make_backup(raw)
    receipt = directory / "factory.receipt.json"
    bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:56Z")
    selected = {"raw": raw, "receipt": receipt}[target]
    os.link(selected, directory / f"{target}.extra-link")
    with pytest.raises(bench.BenchError, match="single-link"):
        bench.validate_receipt(receipt, expected_mac=MAC)


def test_backup_stays_private_under_permissive_umask_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    verified = bench.verify_bundle(REAL_BUNDLE)
    identity = bench.SerialIdentity("/dev/ttyUSB0", 188, "4cd513")
    probe = FakeRunner()

    def runner(argv: list[str], *, timeout: float) -> str:
        if "read-flash" in argv:
            destination = Path(argv[-1])
            destination.write_bytes(b"x" * bench.FLASH_BYTES)
            assert stat.S_IMODE(destination.stat().st_mode) == 0o600
            return "ok\n"
        return probe(argv, timeout=timeout)

    old_umask = os.umask(0)
    try:
        raw, receipt = bench.backup_board(
            verified,
            SERIAL,
            MAC,
            directory,
            runner=runner,
            inspect=lambda _path: identity,
        )
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600

    unsafe = tmp_path / "unsafe"
    secure_dir(unsafe)

    def mutating_runner(argv: list[str], *, timeout: float) -> str:
        if "read-flash" in argv:
            destination = Path(argv[-1])
            destination.write_bytes(b"x" * bench.FLASH_BYTES)
            destination.chmod(0o644)
            return "ok\n"
        return probe(argv, timeout=timeout)

    with pytest.raises(bench.BenchError, match="mode"):
        bench.backup_board(
            verified,
            SERIAL,
            MAC,
            unsafe,
            runner=mutating_runner,
            inspect=lambda _path: identity,
        )


STARTUP = [
    b"ESP32TAP DEVKIT BRINGUP \xe2\x80\x94 NO CONTROL OUTPUTS\n",
    f"BUILD recipe={RECIPE} git={'1' * 40}\n".encode(),
    f"CHIP model=ESP32-S3 revision=2 mac={MAC} crystal_mhz=40 reset=POWERON\n".encode(),
    b"MEMORY flash_bytes=8388608 psram_total=8388608 internal_free=300000 psram_free=8000000\n",
    b"PINS gpio4=0/input gpio5=1/input gpio6=1/input gpio7=0/input gpio15=0/input gpio16=0/input gpio17=0/input gpio18=0/input gpio21=0/input gpio38=0/input\n",
    b"BRINGUP STAGE0 PASS\n",
]


class FakeSerial:
    def __init__(self, lines=STARTUP, events=None) -> None:
        self.lines = list(lines)
        self.events = events if events is not None else []
        self.dtr = None
        self.rts = None
        self.writes: list[bytes] = []

    def setDTR(self, value: bool) -> None:
        self.dtr = value
        self.events.append("neutral-dtr")

    def setRTS(self, value: bool) -> None:
        self.rts = value
        self.events.append("neutral-rts")

    def readline(self, _limit=513) -> bytes:
        self.events.append("read")
        return self.lines.pop(0) if self.lines else b""

    def write(self, value: bytes) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.events.append("close")


class TickClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 0.01
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_capture_validates_complete_single_startup_report() -> None:
    report = bench.capture_startup(FakeSerial(), RECIPE, timeout=30, clock=TickClock())
    assert report.mac == MAC
    assert report.terminal == "BRINGUP STAGE0 PASS"


def test_capture_accepts_real_uart_crlf_startup_records() -> None:
    crlf_startup = [line.removesuffix(b"\n") + b"\r\n" for line in STARTUP]
    report = bench.capture_startup(
        FakeSerial(crlf_startup), RECIPE, timeout=30, clock=TickClock()
    )
    assert report.mac == MAC
    assert report.terminal == "BRINGUP STAGE0 PASS"


def test_capture_tolerates_non_utf8_rom_bytes_before_exact_banner() -> None:
    report = bench.capture_startup(
        FakeSerial([b"\xff\xfeROM chatter\n", *STARTUP]),
        RECIPE,
        timeout=30,
        clock=TickClock(),
    )
    assert report.terminal == "BRINGUP STAGE0 PASS"


def test_capture_rejects_non_utf8_bytes_after_application_banner() -> None:
    with pytest.raises(bench.BenchError, match="ASCII|UTF-8"):
        bench.capture_startup(
            FakeSerial([STARTUP[0], b"\xffbad application line\n", *STARTUP[1:]]),
            RECIPE,
            timeout=30,
            clock=TickClock(),
        )


@pytest.mark.parametrize(
    "lines,match",
    [
        (STARTUP + [STARTUP[0]], "identity"),
        (
            [
                *STARTUP[:1],
                STARTUP[1].replace(RECIPE.encode(), b"f" * 64),
                *STARTUP[2:],
            ],
            "recipe",
        ),
        ([*STARTUP[:-1], b"BRINGUP FAIL code=GPIO_READ\n"], "FAIL"),
        ([*STARTUP, b"BRINGUP STAGE0 PASS\n"], "terminal"),
        (
            [
                *STARTUP[:4],
                STARTUP[4].replace(b"gpio15=0/input", b"gpio15=0/output"),
                STARTUP[5],
            ],
            "protected",
        ),
    ],
)
def test_capture_rejects_bad_identity_recipe_terminal_and_directions(
    lines, match
) -> None:
    with pytest.raises(bench.BenchError, match=match):
        bench.capture_startup(FakeSerial(lines), RECIPE, timeout=1, clock=TickClock())


def test_monitor_order_is_open_neutral_hard_reset_then_read() -> None:
    events: list[str] = []
    port = FakeSerial(events=events)

    def opened(_path: str):
        events.append("open")
        return port

    def reset(value) -> None:
        assert value is port
        events.append("HardReset")

    bench.monitor_serial(
        SERIAL, RECIPE, 30, opener=opened, hard_reset=reset, clock=TickClock()
    )
    assert events[:5] == ["open", "neutral-dtr", "neutral-rts", "HardReset", "read"]
    assert not hasattr(port, "reset_input_buffer")


def test_sample_sends_canonical_command_and_requires_exact_response() -> None:
    port = FakeSerial(
        [
            b"INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input\n"
        ]
    )
    bench.sample_inputs(port, 7, (0, 1, 1, 0), timeout=1, clock=TickClock())
    assert port.writes == [b"SAMPLE 7\n"]
    bad = FakeSerial(
        [
            b"INPUT SAMPLE seq=8 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input\n"
        ]
    )
    with pytest.raises(bench.BenchError, match="sample"):
        bench.sample_inputs(bad, 7, (0, 1, 1, 0), timeout=1, clock=TickClock())


def test_sample_accepts_real_uart_crlf_response() -> None:
    port = FakeSerial(
        [
            b"INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 "
            b"dir15=input dir17=input dir21=input\r\n"
        ]
    )
    bench.sample_inputs(port, 7, (0, 1, 1, 0), timeout=1, clock=TickClock())
    assert port.writes == [b"SAMPLE 7\n"]


@pytest.mark.parametrize(
    "raw",
    [
        b"record\r",
        b"record\rinside\n",
        b"record\r\r\n",
        b"record\0\n",
        b"record",
        b"x" * (bench.MAX_SERIAL_LINE + 1),
    ],
)
def test_serial_line_rejects_noncanonical_terminators_and_unsafe_bytes(
    raw: bytes,
) -> None:
    with pytest.raises(bench.BenchError, match="oversized|unterminated|carriage|NUL"):
        bench._readline_bytes(FakeSerial([raw]), label="capture")


def test_cold_monitor_requires_disappearance_and_same_usb_identity() -> None:
    original = bench.SerialIdentity("/dev/ttyUSB0", 188, "4cd513")
    changed = bench.SerialIdentity("/dev/ttyUSB1", 189, "different")
    with pytest.raises(bench.BenchError, match="disappear"):
        bench.wait_cold_cycle(
            SERIAL, original, 0.05, inspect=lambda _p: original, clock=TickClock()
        )
    states = iter([None, changed])
    with pytest.raises(bench.BenchError, match="identity"):
        bench.wait_cold_cycle(
            SERIAL, original, 1, inspect=lambda _p: next(states), clock=TickClock()
        )
    states = iter([None, original])
    assert (
        bench.wait_cold_cycle(
            SERIAL, original, 1, inspect=lambda _p: next(states), clock=TickClock()
        )
        == original
    )


def test_flash_requires_matching_receipt_before_write(
    tmp_path: Path, bundle: Path
) -> None:
    directory = tmp_path / "backups"
    secure_dir(directory)
    raw = directory / "factory.bin"
    make_backup(raw)
    receipt = directory / "factory.receipt.json"
    bench.write_receipt(raw, receipt, MAC, timestamp="2026-07-31T12:34:56Z")
    verified = bench.verify_bundle(bundle)
    runner = FakeRunner()
    identity = bench.SerialIdentity("/dev/ttyUSB0", 188, "4cd513")
    bench.authorize_and_flash(
        verified,
        SERIAL,
        receipt,
        MAC,
        runner=runner,
        inspect=lambda _p: identity,
    )
    write = runner.calls[-1]
    assert write[1:7] == ["--chip", "esp32s3", "--port", SERIAL, "--after", "no-reset"]
    assert "write-flash" in write
    missing = directory / "missing.json"
    with pytest.raises(bench.BenchError, match="receipt"):
        bench.authorize_and_flash(
            verified,
            SERIAL,
            missing,
            MAC,
            runner=runner,
            inspect=lambda _p: identity,
        )
