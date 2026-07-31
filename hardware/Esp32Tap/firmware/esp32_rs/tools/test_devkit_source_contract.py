"""Source/configuration gates for the output-isolated DevKit firmware.

These tests deliberately inspect the small firmware crate rather than building
it for the host: the crate targets the ESP32-S3 Xtensa toolchain.  A later
artifact gate performs the real cross-build and also leaves a generated
``sdkconfig`` for this suite to validate when one is present.
"""

from __future__ import annotations

import os
import re
import stat
import struct
import tomllib
from pathlib import Path

import pytest


# Source-only verification (works in a clean checkout):
#   python3 -m pytest -q tools/test_devkit_source_contract.py
#
# Mandatory post-cross-build verification (fails closed if either path is
# absent, missing, or stale enough to violate the asserted contents):
#   DEVKIT_VERIFY_GENERATED=1 \
#   DEVKIT_GENERATED_SDKCONFIG=/absolute/path/to/generated/sdkconfig \
#   DEVKIT_FIRMWARE_ELF=/absolute/path/to/devkit_bringup \
#   DEVKIT_EXPECTED_RECIPE_ID=<64-lowercase-hex> \
#   DEVKIT_EXPECTED_GIT_COMMIT=<40-lowercase-hex> \
#   python3 -m pytest -q tools/test_devkit_source_contract.py

ESP32_RS = Path(__file__).resolve().parent.parent
DEVKIT = ESP32_RS / "devkit_bringup"
SRC = DEVKIT / "src"
DEFAULTS = ESP32_RS / "sdkconfig.defaults.devkit"
UNSAFE_GATE = ESP32_RS / "tools" / "check_unsafe_budget.py"

PROTECTED_INPUT_ONLY = {4, 5, 6, 7, 15, 17, 21}
UNTOUCHED = {16, 18, 38}
OBSERVED_PINS = PROTECTED_INPUT_ONLY | UNTOUCHED
FORBIDDEN_TEXT = {
    "gpio_set_direction",
    "gpio_config",
    "gpio_set_level",
    "gpio_pullup_en",
    "gpio_pullup_dis",
    "gpio_pulldown_en",
    "gpio_pulldown_dis",
    "gpio_set_pull_mode",
    "gpio_hold_en",
    "uart_set_pin",
    "uart_driver_install",
    "esp_wifi",
    "nimble",
    "safety_core",
    "program_core",
    "ble_core",
    "coach_core",
}
FAILURE_CODES = {
    "BAD_RECIPE",
    "CHIP_INFO",
    "MAC_READ",
    "FLASH_SIZE",
    "PSRAM_SIZE",
    "GPIO_READ",
    "PROTECTED_DIRECTION",
    "UART_WRITE",
}
TEST_RECIPE_ID = "a" * 64
TEST_GIT_COMMIT = "b" * 40
DEVKIT_BANNER = "ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS".encode()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _rust_sources() -> dict[str, str]:
    if not SRC.is_dir():
        return {}
    return {path.name: _read(path) for path in sorted(SRC.glob("*.rs"))}


def _without_comments_and_strings(text: str) -> str:
    """Remove enough Rust lexical noise to make API/token checks meaningful."""

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(
        r'br?#+".*?"#+|br?".*?"|"(?:\\.|[^"\\])*"', '""', text, flags=re.DOTALL
    )
    return text


def _parse_kconfig(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in _read(path).splitlines():
        line = raw_line.strip()
        unset = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        if unset:
            values[unset.group(1)] = "n"
            continue
        assignment = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=(.*)", line)
        if assignment:
            values[assignment.group(1)] = assignment.group(2).strip('"')
    return values


def _generated_artifacts() -> tuple[Path, Path]:
    if os.environ.get("DEVKIT_VERIFY_GENERATED") != "1":
        pytest.skip("set DEVKIT_VERIFY_GENERATED=1 after the pinned cross-build")
    sdkconfig_value = os.environ.get("DEVKIT_GENERATED_SDKCONFIG")
    elf_value = os.environ.get("DEVKIT_FIRMWARE_ELF")
    assert sdkconfig_value, "DEVKIT_GENERATED_SDKCONFIG is required in generated mode"
    assert elf_value, "DEVKIT_FIRMWARE_ELF is required in generated mode"
    sdkconfig = _absolute_regular_file(sdkconfig_value, "generated sdkconfig")
    elf = _absolute_regular_file(elf_value, "DevKit ELF")
    return sdkconfig, elf


def _expected_build_identity() -> tuple[bytes, bytes]:
    recipe = os.environ.get("DEVKIT_EXPECTED_RECIPE_ID")
    git_commit = os.environ.get("DEVKIT_EXPECTED_GIT_COMMIT")
    assert recipe, "DEVKIT_EXPECTED_RECIPE_ID is required in generated mode"
    assert git_commit, "DEVKIT_EXPECTED_GIT_COMMIT is required in generated mode"
    assert re.fullmatch(
        r"[0-9a-f]{64}", recipe
    ), "expected recipe ID must be exactly 64 lowercase hexadecimal characters"
    assert re.fullmatch(
        r"[0-9a-f]{40}", git_commit
    ), "expected git commit must be exactly 40 lowercase hexadecimal characters"
    return recipe.encode(), git_commit.encode()


def _absolute_regular_file(value: str, label: str) -> Path:
    path = Path(value)
    assert path.is_absolute(), f"{label} path must be absolute: {path}"
    assert not path.is_symlink(), f"{label} path must not be a symlink: {path}"
    assert path.exists(), f"{label} not found: {path}"
    assert stat.S_ISREG(path.stat().st_mode), f"{label} is not a regular file: {path}"
    return path


def _validate_generated_sdkconfig(path: Path) -> None:
    expected_header = (
        "#\n"
        "# Automatically generated file. DO NOT EDIT.\n"
        "# Espressif IoT Development Framework (ESP-IDF) 5.5.4 Project Configuration\n"
        "#\n"
    )
    assert _read(path).startswith(
        expected_header
    ), f"generated sdkconfig lacks the canonical IDF 5.5.4 generated header: {path}"


def _read_xtensa_elf(path: Path) -> bytes:
    image = path.read_bytes()
    assert len(image) >= 52, f"ELF header is truncated or empty: {path}"
    assert image[:4] == b"\x7fELF", f"ELF magic is missing: {path}"
    assert image[4] == 1, f"ELF is not 32-bit: {path}"
    assert image[5] == 1, f"ELF is not little-endian: {path}"
    assert image[6] == 1, f"ELF header version is invalid: {path}"
    assert image[7:16] == b"\0" * 9, f"ELF ident metadata is invalid: {path}"
    (
        elf_type,
        machine,
        version,
        entry,
        phoff,
        shoff,
        _flags,
        ehsize,
        phentsize,
        phnum,
        shentsize,
        shnum,
        shstrndx,
    ) = struct.unpack_from("<HHIIIIIHHHHHH", image, 16)
    assert elf_type == 2, f"ELF is not executable: {path}"
    assert machine == 94, f"ELF machine is not Xtensa: {path}"
    assert version == 1, f"ELF version is not current: {path}"
    assert entry != 0, f"ELF entry point is zero: {path}"
    assert ehsize == 52, f"ELF header size is not 52: {path}"
    assert phentsize == 32 and phnum > 0, f"ELF program header shape is invalid: {path}"
    assert shentsize == 40 and shnum > 0, f"ELF section header shape is invalid: {path}"
    assert phoff >= ehsize, f"ELF program header offset overlaps its header: {path}"
    assert shoff >= ehsize, f"ELF section header offset overlaps its header: {path}"
    _bounded_table(phoff, phentsize, phnum, len(image), "program header table", path)
    _bounded_table(shoff, shentsize, shnum, len(image), "section header table", path)

    has_nonempty_load = False
    for index in range(phnum):
        start = phoff + index * phentsize
        program = struct.unpack_from("<IIIIIIII", image, start)
        program_type, file_offset, _, _, file_size, memory_size, _, _ = program
        assert file_size <= memory_size, f"ELF segment filesz exceeds memsz: {path}"
        if file_size:
            _bounded_range(
                file_offset, file_size, len(image), "segment file range", path
            )
        if program_type == 1 and file_size > 0:
            has_nonempty_load = True
    assert has_nonempty_load, f"ELF has no nonempty PT_LOAD segment: {path}"

    assert 0 < shstrndx < shnum, f"ELF shstrndx is invalid: {path}"
    sections = [
        struct.unpack_from("<IIIIIIIIII", image, shoff + index * shentsize)
        for index in range(shnum)
    ]
    for section in sections:
        _, section_type, _, _, file_offset, file_size, _, _, _, _ = section
        if section_type != 8 and file_size:
            _bounded_range(
                file_offset, file_size, len(image), "section file range", path
            )

    shstr = sections[shstrndx]
    assert shstr[1] == 3 and shstr[5] > 1, f"ELF section-name table is invalid: {path}"
    shstr_bytes = image[shstr[4] : shstr[4] + shstr[5]]
    section_names: set[str] = set()
    for section in sections:
        name_offset = section[0]
        assert name_offset < len(
            shstr_bytes
        ), f"ELF section name offset is invalid: {path}"
        name_end = shstr_bytes.find(b"\0", name_offset)
        assert name_end >= 0, f"ELF section name is unterminated: {path}"
        section_names.add(shstr_bytes[name_offset:name_end].decode("ascii"))
        if section[1] == 2:
            assert (
                section[6] < shnum and sections[section[6]][1] == 3
            ), f"ELF symbol table has no linked string table: {path}"

    required_sections = {
        ".flash.text",
        ".flash.rodata",
        ".symtab",
        ".strtab",
        ".shstrtab",
    }
    assert (
        required_sections <= section_names
    ), f"ELF required sections are missing: {sorted(required_sections - section_names)}"
    return image


def _validate_devkit_identity(
    image: bytes, expected_recipe: bytes, expected_git_commit: bytes
) -> None:
    identity_cluster = expected_recipe + expected_git_commit + DEVKIT_BANNER
    assert (
        image.count(identity_cluster) == 1
    ), "ELF must contain exactly one expected recipe/git/banner identity cluster"

    identity_end = image.index(identity_cluster) + len(identity_cluster)
    build_anchor = b"BUILD recipe= git="
    build_offset = image.find(build_anchor, identity_end, identity_end + 64)
    assert (
        build_offset >= 0 and image.count(build_anchor) == 1
    ), "ELF identity cluster is not in the BUILD recipe/git record context"

    protocol_markers = {
        b"CHIP model= revision= mac=: crystal_mhz=40 reset=",
        b"MEMORY flash_bytes= psram_total= internal_free= psram_free=",
        b"PINS gpio4=/",
        b"BRINGUP STAGE0 PASS",
        b"BRINGUP FAIL code=",
        b"INPUT SAMPLE seq=",
        b"BRINGUP ERROR code=",
        b"BAD_COMMAND",
    }
    for marker in protocol_markers:
        assert (
            image.count(marker) == 1
        ), f"ELF must contain exactly one protocol identity marker: {marker!r}"
    for failure_code in FAILURE_CODES:
        assert (
            failure_code.encode() in image
        ), f"ELF is missing failure code: {failure_code}"


def _bounded_table(
    offset: int, entry_size: int, count: int, total: int, label: str, path: Path
) -> None:
    assert (
        offset <= total and count <= (total - offset) // entry_size
    ), f"ELF {label} exceeds file bounds: {path}"


def _bounded_range(offset: int, size: int, total: int, label: str, path: Path) -> None:
    assert (
        offset <= total and size <= total - offset
    ), f"ELF {label} exceeds file bounds: {path}"


def _generated_sdkconfig_fixture() -> bytes:
    header = (
        "#\n"
        "# Automatically generated file. DO NOT EDIT.\n"
        "# Espressif IoT Development Framework (ESP-IDF) 5.5.4 Project Configuration\n"
        "#\n"
    )
    return (header + _read(DEFAULTS)).encode()


def _pseudo_xtensa_elf_fixture() -> bytes:
    return b"\x7fELF\x01\x01\x01" + b"\x00" * 9 + b"\x02\x00\x5e\x00"


def _structural_xtensa_elf_fixture(
    identity: tuple[str, str] | None = None,
) -> bytes:
    """Small coherent ELF32 used only to corrupt individual structures."""

    phoff = 52
    text_offset = 0x100
    rodata_offset = 0x104 if identity is None else 0x180
    symtab_offset = 0x108
    strtab_offset = 0x118
    shstrtab_offset = 0x120
    shoff = 0x180 if identity is None else 0x500
    shnum = 6
    shstrtab = b"\0.flash.text\0.flash.rodata\0.symtab\0.strtab\0.shstrtab\0"
    names = {
        name: shstrtab.index(name.encode())
        for name in (".flash.text", ".flash.rodata", ".symtab", ".strtab", ".shstrtab")
    }
    image = bytearray(shoff + shnum * 40)
    ident = b"\x7fELF\x01\x01\x01" + b"\x00" * 9
    image[:52] = ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2,
        94,
        1,
        0x4037_4000,
        phoff,
        shoff,
        0,
        52,
        32,
        1,
        40,
        shnum,
        5,
    )
    image[phoff : phoff + 32] = struct.pack(
        "<IIIIIIII", 1, text_offset, 0x4200_0000, 0x4200_0000, 4, 4, 5, 4
    )
    image[text_offset : text_offset + 4] = b"TEXT"
    rodata = b"DATA" if identity is None else _identity_blob(*identity)
    image[rodata_offset : rodata_offset + len(rodata)] = rodata
    image[symtab_offset : symtab_offset + 16] = b"\0" * 16
    image[strtab_offset : strtab_offset + 8] = b"\0symbol\0"
    image[shstrtab_offset : shstrtab_offset + len(shstrtab)] = shstrtab

    sections = (
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (names[".flash.text"], 1, 6, 0x4200_0000, text_offset, 4, 0, 0, 4, 0),
        (
            names[".flash.rodata"],
            1,
            2,
            0x3C04_0000,
            rodata_offset,
            len(rodata),
            0,
            0,
            4,
            0,
        ),
        (names[".symtab"], 2, 0, 0, symtab_offset, 16, 4, 0, 4, 16),
        (names[".strtab"], 3, 0, 0, strtab_offset, 8, 0, 0, 1, 0),
        (
            names[".shstrtab"],
            3,
            0,
            0,
            shstrtab_offset,
            len(shstrtab),
            0,
            0,
            1,
            0,
        ),
    )
    for index, section in enumerate(sections):
        start = shoff + index * 40
        image[start : start + 40] = struct.pack("<IIIIIIIIII", *section)
    return bytes(image)


def _identity_blob(recipe: str, git_commit: str) -> bytes:
    markers = [
        recipe.encode() + git_commit.encode() + DEVKIT_BANNER,
        b"BUILD recipe= git=",
        b"CHIP model= revision= mac=: crystal_mhz=40 reset=",
        b"MEMORY flash_bytes= psram_total= internal_free= psram_free=",
        b"PINS gpio4=/ gpio5=/ gpio6=/ gpio7=/ gpio15=/ gpio16=/ gpio17=/ gpio18=/ gpio21=/ gpio38=/",
        b"BRINGUP STAGE0 PASS",
        b"BRINGUP FAIL code=",
        b"INPUT SAMPLE seq=",
        b"BRINGUP ERROR code=",
        b"BAD_COMMAND",
        *(code.encode() for code in sorted(FAILURE_CODES)),
    ]
    return b"\0".join(markers) + b"\0"


def _invoke_generated_verifier(
    monkeypatch: pytest.MonkeyPatch,
    sdkconfig: str,
    elf: str,
    recipe: str = TEST_RECIPE_ID,
    git_commit: str = TEST_GIT_COMMIT,
) -> None:
    monkeypatch.setenv("DEVKIT_VERIFY_GENERATED", "1")
    monkeypatch.setenv("DEVKIT_GENERATED_SDKCONFIG", sdkconfig)
    monkeypatch.setenv("DEVKIT_FIRMWARE_ELF", elf)
    monkeypatch.setenv("DEVKIT_EXPECTED_RECIPE_ID", recipe)
    monkeypatch.setenv("DEVKIT_EXPECTED_GIT_COMMIT", git_commit)
    test_generated_sdkconfig_and_elf_are_mandatory_in_generated_mode()


def test_all_isolated_firmware_files_exist() -> None:
    required = {
        DEVKIT / "Cargo.toml",
        DEVKIT / "build.rs",
        DEVKIT / ".cargo" / "config.toml",
        DEVKIT / "rust-toolchain.toml",
        SRC / "main.rs",
        SRC / "hardware.rs",
        DEFAULTS,
    }
    assert not (
        missing := sorted(str(path) for path in required if not path.is_file())
    ), missing


def test_manifest_has_only_the_two_direct_dependencies() -> None:
    manifest = tomllib.loads(_read(DEVKIT / "Cargo.toml"))
    assert set(manifest["dependencies"]) == {"bringup_core", "esp-idf-sys"}
    assert set(manifest["build-dependencies"]) == {"embuild"}
    assert manifest["dependencies"]["bringup_core"]["path"] == "../bringup_core"


def test_build_script_fail_closes_recipe_and_commit_identity() -> None:
    source = _read(DEVKIT / "build.rs")
    assert 'var("ESP32TAP_RECIPE_ID")' in source
    assert 'var("ESP32TAP_GIT_COMMIT")' in source
    assert "len() != 64" in source
    assert "len() != 40" in source
    assert "is_ascii_hexdigit" in source and "is_ascii_lowercase" in source
    assert "cargo:rustc-env=ESP32TAP_RECIPE_ID={recipe}" in source
    assert "cargo:rustc-env=ESP32TAP_GIT_COMMIT={git_commit}" in source
    assert source.count("cargo:rerun-if-env-changed=ESP32TAP_RECIPE_ID") == 1
    assert source.count("cargo:rerun-if-env-changed=ESP32TAP_GIT_COMMIT") == 1


def test_crate_imports_only_its_read_only_hardware_boundary() -> None:
    sources = _rust_sources()
    main = _without_comments_and_strings(sources.get("main.rs", ""))
    modules = set(re.findall(r"(?m)^\s*mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", main))
    assert modules == {"hardware"}

    joined = "\n".join(
        _without_comments_and_strings(text).lower() for text in sources.values()
    )
    assert not (
        present := sorted(token for token in FORBIDDEN_TEXT if token in joined)
    ), present


def test_gpio_getters_exist_only_in_hardware_boundary() -> None:
    sources = _rust_sources()
    hardware = _without_comments_and_strings(sources.get("hardware.rs", ""))
    for getter in ("gpio_get_direction", "gpio_get_level"):
        assert getter in hardware
        assert all(
            getter not in _without_comments_and_strings(text)
            for name, text in sources.items()
            if name != "hardware.rs"
        )


def test_hardware_ffi_surface_is_read_only_and_status_checked() -> None:
    source = _without_comments_and_strings(_read(SRC / "hardware.rs"))
    calls = set(re.findall(r"esp_idf_sys::([A-Za-z_][A-Za-z0-9_]*)\s*\(", source))
    assert calls == {
        "esp_efuse_mac_get_default",
        "esp_flash_get_size",
        "esp_psram_get_size",
        "esp_reset_reason",
        "gpio_get_level",
        "heap_caps_get_free_size",
        "heap_caps_get_total_size",
        "vTaskDelay",
    }
    # IDF 5.5.4 has no public gpio_get_direction symbol.  The local getter
    # reads the S3 output-enable registers directly and remains observational.
    assert "fn gpio_get_direction(" in source
    assert "read_volatile(register)" in source
    # esp-idf-sys 0.37.2 also omits esp_chip_info.h from its binding header;
    # the exact IDF 5.5.4 C ABI is declared only inside this unsafe boundary.
    assert "fn esp_chip_info(out_info: *mut RawChipInfo);" in source
    assert source.count("check_esp(") >= 2
    assert "written != bytes.len()" in source
    assert "read == 1" in source
    assert "level != 0 && level != 1" in source


def test_unsafe_is_confined_and_every_block_has_adjacent_justification() -> None:
    unsafe_files: set[str] = set()
    for name, raw in _rust_sources().items():
        code = _without_comments_and_strings(raw)
        if re.search(r"\bunsafe\b", code):
            unsafe_files.add(name)
        lines = raw.splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\bunsafe\s*\{", _without_comments_and_strings(line)):
                previous = index - 1
                while previous >= 0 and not lines[previous].strip():
                    previous -= 1
                assert previous >= 0 and lines[previous].lstrip().startswith(
                    "// SAFETY:"
                ), (
                    name,
                    index + 1,
                )
    assert unsafe_files == {"hardware.rs"}


def test_unsafe_budget_has_an_independent_devkit_allowlist_and_budget() -> None:
    gate = _read(UNSAFE_GATE)
    assert 'DEVKIT_FW_SRC = ESP32_RS / "devkit_bringup" / "src"' in gate
    assert 'DEVKIT_UNSAFE = {"hardware.rs"}' in gate
    assert re.search(r"(?m)^DEVKIT_UNSAFE_LINES = \d+$", gate)
    assert "devkit unsafe budget" in gate.lower()
    production_assignment = re.search(
        r"PRODUCTION_UNSAFE\s*=\s*\{(.*?)\n\}", gate, re.DOTALL
    )
    assert (
        production_assignment and "devkit" not in production_assignment.group(1).lower()
    )


def test_startup_report_contains_every_required_bounded_record() -> None:
    main = _read(SRC / "main.rs")
    required_fragments = {
        "ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS",
        "BUILD recipe={} git={}",
        "CHIP model={} revision={} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} crystal_mhz=40 reset={}",
        "MEMORY flash_bytes={} psram_total={} internal_free={} psram_free={}",
        "PINS gpio4={}/{} gpio5={}/{} gpio6={}/{} gpio7={}/{} gpio15={}/{} gpio16={}/{} gpio17={}/{} gpio18={}/{} gpio21={}/{} gpio38={}/{}",
        "BRINGUP STAGE0 PASS",
        "BRINGUP FAIL code={}",
    }
    assert not (
        missing := sorted(
            fragment for fragment in required_fragments if fragment not in main
        )
    ), missing
    assert "const MAX_REPORT_LINE_BYTES: usize" in main
    assert "bringup_core::MAX_COMMAND_BYTES" in main
    assert (
        "OutputBuffer" in main
        and "format_input_sample" in main
        and "format_error" in main
    )


def test_every_observed_pin_has_level_and_direction_in_one_record() -> None:
    main = _read(SRC / "main.rs")
    pins_record = next(
        (line for line in main.splitlines() if '"PINS gpio4=' in line), ""
    )
    assert {
        int(pin) for pin in re.findall(r"gpio(\d+)=\{\}/\{\}", pins_record)
    } == OBSERVED_PINS


def test_failure_codes_are_exact_and_protected_directions_gate_pass() -> None:
    main = _read(SRC / "main.rs")
    enum_body = re.search(r"enum FailureCode\s*\{(.*?)\n\}", main, re.DOTALL)
    assert enum_body
    mappings = dict(re.findall(r"Self::([A-Za-z0-9_]+)\s*=>\s*\"([A-Z_]+)\"", main))
    assert set(mappings.values()) == FAILURE_CODES
    for pin in (15, 17, 21):
        assert f"pins.gpio{pin}.direction.is_input()" in main
    assert "FailureCode::ProtectedDirection" in main


def test_settle_delay_is_fixed_once_before_first_report_write() -> None:
    main = _read(SRC / "main.rs")
    assert main.count("const STARTUP_SETTLE_MS: u32 = 5_000;") == 1
    call = "hardware::delay_ms(STARTUP_SETTLE_MS);"
    assert main.count(call) == 1
    assert main.index(call) < main.index("write_startup_report(")


def test_terminal_result_is_single_shot_and_halt_cannot_restart() -> None:
    sources = _rust_sources()
    main = sources.get("main.rs", "")
    code = "\n".join(_without_comments_and_strings(text) for text in sources.values())
    assert main.count('"BRINGUP STAGE0 PASS"') == 1
    assert main.count('"BRINGUP FAIL code={}"') == 1
    assert re.search(r"fn fail_and_halt\([^)]*\)\s*->\s*!", main)
    assert re.search(r"fn halt\(\)\s*->\s*!", _read(SRC / "hardware.rs"))
    assert "esp_restart" not in code
    assert "esp_task_wdt" not in code
    fail_body = re.search(r"fn fail_and_halt\(.*?\n\}", main, re.DOTALL)
    assert fail_body and fail_body.group(0).count("write_line(") == 1


def test_post_pass_gpio_and_uart_failures_emit_one_best_effort_failure() -> None:
    main = _read(SRC / "main.rs")
    handle = re.search(r"fn handle_command\(.*?\n\}", main, re.DOTALL)
    command_error = re.search(r"fn write_command_error\(.*?\n\}", main, re.DOTALL)
    assert handle and command_error
    assert "Err(_) => fail_and_halt(FailureCode::GpioRead)" in handle.group(0)
    assert "fail_and_halt(FailureCode::UartWrite)" in handle.group(0)
    assert "fail_and_halt(FailureCode::UartWrite)" in command_error.group(0)
    assert "Best effort, exactly once" in main


def test_standalone_sdkconfig_is_n8r8_uart_and_halt_only() -> None:
    config = _parse_kconfig(DEFAULTS)
    _assert_n8r8_uart_halt_config(config)
    assert config.get("CONFIG_ESP_WIFI_ENABLED") == "n"


def _assert_n8r8_uart_halt_config(config: dict[str, str]) -> None:
    assert config.get("CONFIG_IDF_TARGET") == "esp32s3"
    assert config.get("CONFIG_IDF_TARGET_ESP32S3") == "y"
    assert config.get("CONFIG_ESPTOOLPY_FLASHSIZE_8MB") == "y"
    assert config.get("CONFIG_PARTITION_TABLE_CUSTOM") == "y"
    assert config.get("CONFIG_PARTITION_TABLE_CUSTOM_FILENAME") == (
        "/project/hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv"
    )
    assert config.get("CONFIG_SPIRAM") == "y"
    assert config.get("CONFIG_SPIRAM_MODE_OCT") == "y"
    assert config.get("CONFIG_SPIRAM_BOOT_HW_INIT") == "y"
    assert config.get("CONFIG_SPIRAM_BOOT_INIT") == "y"
    assert config.get("CONFIG_SPIRAM_USE_CAPS_ALLOC") == "y"
    assert config.get("CONFIG_ESP_CONSOLE_UART_DEFAULT") == "y"
    assert config.get("CONFIG_ESP_CONSOLE_UART") == "y"
    assert config.get("CONFIG_ESP_CONSOLE_SECONDARY_NONE") == "y"
    assert config.get("CONFIG_USJ_ENABLE_USB_SERIAL_JTAG") == "n"
    assert config.get("CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT") == "y"

    forbidden_enabled = {
        "CONFIG_ETH_USE_OPENETH",
        "CONFIG_BT_ENABLED",
        "CONFIG_USJ_ENABLE_USB_SERIAL_JTAG",
        "CONFIG_ESP_CONSOLE_USB_CDC",
        "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG",
        "CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG",
        "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG_ENABLED",
        "CONFIG_ESP_DEBUG_OCDAWARE",
        "CONFIG_ESP_DEBUG_INCLUDE_OCD_STUB_BINS",
        "CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT",
        "CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT",
        "CONFIG_ESP_SYSTEM_PANIC_GDBSTUB",
        "CONFIG_ESP_TASK_WDT_INIT",
    }
    assert not {key for key in forbidden_enabled if config.get(key) == "y"}


def test_generated_sdkconfig_and_elf_are_mandatory_in_generated_mode() -> None:
    """Check semantics, not IDF's hidden always-y Wi-Fi capability symbol.

    ESP_WIFI_ENABLED has no prompt on ESP32-S3 and Kconfig forces it to y even
    when defaults request n.  The meaningful invariant is that this binary
    neither references nor links a radio/network initialization entry point.
    """

    sdkconfig, elf = _generated_artifacts()
    expected_recipe, expected_git_commit = _expected_build_identity()
    _validate_generated_sdkconfig(sdkconfig)
    config = _parse_kconfig(sdkconfig)
    _assert_n8r8_uart_halt_config(config)
    # ESP_WIFI_ENABLED is an invisible capability symbol forced on for an S3
    # by IDF 5.5.4.  Prove operational radio absence from the linked image.
    assert config.get("CONFIG_ESP_WIFI_ENABLED") in {"n", "y"}
    image = _read_xtensa_elf(elf)
    _validate_devkit_identity(image, expected_recipe, expected_git_commit)
    for symbol in (
        b"esp_wifi_init",
        b"esp_wifi_start",
        b"esp_wifi_connect",
        b"esp_wifi_set_mode",
        b"esp_wifi_set_config",
        b"nimble_port_init",
        b"esp_eth_driver_install",
    ):
        assert symbol not in image


def test_generated_verifier_rejects_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sdkconfig").write_bytes(_generated_sdkconfig_fixture())
    (tmp_path / "firmware.elf").write_bytes(_structural_xtensa_elf_fixture())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssertionError, match="absolute"):
        _invoke_generated_verifier(monkeypatch, "sdkconfig", "firmware.elf")


def test_generated_verifier_rejects_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdkconfig = tmp_path / "real-sdkconfig"
    elf = tmp_path / "real.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_structural_xtensa_elf_fixture())
    sdk_link = tmp_path / "sdkconfig"
    elf_link = tmp_path / "firmware.elf"
    sdk_link.symlink_to(sdkconfig)
    elf_link.symlink_to(elf)
    with pytest.raises(AssertionError, match="symlink"):
        _invoke_generated_verifier(monkeypatch, str(sdk_link), str(elf_link))


def test_generated_verifier_rejects_defaults_and_text_as_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_sdkconfig = tmp_path / "sdkconfig"
    valid_elf = tmp_path / "firmware.elf"
    valid_sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    valid_elf.write_bytes(_structural_xtensa_elf_fixture())
    with pytest.raises(AssertionError, match="generated header"):
        _invoke_generated_verifier(monkeypatch, str(DEFAULTS), str(valid_elf))
    with pytest.raises(AssertionError, match="ELF"):
        _invoke_generated_verifier(
            monkeypatch, str(valid_sdkconfig), str(DEVKIT / "Cargo.toml")
        )


@pytest.mark.parametrize("bad_elf", [b"", b"not an elf", b"\x7fELF\x02\x01\x01"])
def test_generated_verifier_rejects_empty_or_malformed_elf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_elf: bytes
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(bad_elf)
    with pytest.raises(AssertionError, match="ELF"):
        _invoke_generated_verifier(monkeypatch, str(sdkconfig), str(elf))


def test_generated_verifier_rejects_20_byte_pseudo_elf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_pseudo_xtensa_elf_fixture())
    with pytest.raises(AssertionError, match="ELF header"):
        _invoke_generated_verifier(monkeypatch, str(sdkconfig), str(elf))


def test_generated_verifier_requires_expected_build_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_structural_xtensa_elf_fixture((TEST_RECIPE_ID, TEST_GIT_COMMIT)))
    monkeypatch.setenv("DEVKIT_VERIFY_GENERATED", "1")
    monkeypatch.setenv("DEVKIT_GENERATED_SDKCONFIG", str(sdkconfig))
    monkeypatch.setenv("DEVKIT_FIRMWARE_ELF", str(elf))
    monkeypatch.delenv("DEVKIT_EXPECTED_RECIPE_ID", raising=False)
    monkeypatch.delenv("DEVKIT_EXPECTED_GIT_COMMIT", raising=False)
    with pytest.raises(AssertionError, match="DEVKIT_EXPECTED_RECIPE_ID"):
        test_generated_sdkconfig_and_elf_are_mandatory_in_generated_mode()
    monkeypatch.setenv("DEVKIT_EXPECTED_RECIPE_ID", TEST_RECIPE_ID)
    with pytest.raises(AssertionError, match="DEVKIT_EXPECTED_GIT_COMMIT"):
        test_generated_sdkconfig_and_elf_are_mandatory_in_generated_mode()


@pytest.mark.parametrize(
    ("recipe", "git_commit", "message"),
    [
        ("A" * 64, TEST_GIT_COMMIT, "recipe ID"),
        ("a" * 63, TEST_GIT_COMMIT, "recipe ID"),
        (TEST_RECIPE_ID, "B" * 40, "git commit"),
        (TEST_RECIPE_ID, "b" * 39, "git commit"),
    ],
)
def test_generated_verifier_rejects_malformed_expected_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recipe: str,
    git_commit: str,
    message: str,
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_structural_xtensa_elf_fixture((TEST_RECIPE_ID, TEST_GIT_COMMIT)))
    with pytest.raises(AssertionError, match=message):
        _invoke_generated_verifier(
            monkeypatch, str(sdkconfig), str(elf), recipe, git_commit
        )


@pytest.mark.parametrize(
    ("recipe", "git_commit", "message"),
    [
        ("c" * 64, TEST_GIT_COMMIT, "identity cluster"),
        (TEST_RECIPE_ID, "c" * 40, "identity cluster"),
    ],
)
def test_generated_verifier_rejects_wrong_embedded_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recipe: str,
    git_commit: str,
    message: str,
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_structural_xtensa_elf_fixture((TEST_RECIPE_ID, TEST_GIT_COMMIT)))
    with pytest.raises(AssertionError, match=message):
        _invoke_generated_verifier(
            monkeypatch, str(sdkconfig), str(elf), recipe, git_commit
        )


def test_generated_verifier_rejects_generic_structural_elf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdkconfig = tmp_path / "sdkconfig"
    elf = tmp_path / "firmware.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_structural_xtensa_elf_fixture())
    with pytest.raises(AssertionError, match="identity cluster"):
        _invoke_generated_verifier(monkeypatch, str(sdkconfig), str(elf))


@pytest.mark.parametrize(
    ("field_offset", "bad_value", "message"),
    [
        (28, 0x260, "program header table"),
        (32, 0x260, "section header table"),
    ],
)
def test_elf_rejects_truncated_header_tables(
    tmp_path: Path, field_offset: int, bad_value: int, message: str
) -> None:
    image = bytearray(_structural_xtensa_elf_fixture())
    struct.pack_into("<I", image, field_offset, bad_value)
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(image)
    with pytest.raises(AssertionError, match=message):
        _read_xtensa_elf(elf)


def test_elf_requires_nonempty_load_segment(tmp_path: Path) -> None:
    image = bytearray(_structural_xtensa_elf_fixture())
    struct.pack_into("<I", image, 52, 0)
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(image)
    with pytest.raises(AssertionError, match="PT_LOAD"):
        _read_xtensa_elf(elf)


def test_elf_rejects_program_and_section_ranges_outside_file(tmp_path: Path) -> None:
    program = bytearray(_structural_xtensa_elf_fixture())
    struct.pack_into("<I", program, 52 + 4, len(program) - 1)
    elf = tmp_path / "program-range.elf"
    elf.write_bytes(program)
    with pytest.raises(AssertionError, match="segment file range"):
        _read_xtensa_elf(elf)

    section = bytearray(_structural_xtensa_elf_fixture())
    shoff = struct.unpack_from("<I", section, 32)[0]
    struct.pack_into("<I", section, shoff + 2 * 40 + 16, len(section) - 1)
    elf = tmp_path / "section-range.elf"
    elf.write_bytes(section)
    with pytest.raises(AssertionError, match="section file range"):
        _read_xtensa_elf(elf)


def test_elf_requires_real_esp_section_names(tmp_path: Path) -> None:
    image = bytearray(_structural_xtensa_elf_fixture())
    shstrtab = image.find(b".flash.rodata")
    image[shstrtab : shstrtab + len(b".flash.rodata")] = b".fake.section"
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(image)
    with pytest.raises(AssertionError, match="required sections"):
        _read_xtensa_elf(elf)


def test_no_output_pull_hold_or_protected_output_mask_exists() -> None:
    sources = _rust_sources()
    code = "\n".join(_without_comments_and_strings(text) for text in sources.values())
    lowered = code.lower()
    assert not (
        present := sorted(token for token in FORBIDDEN_TEXT if token in lowered)
    ), present
    assert not re.search(
        r"(?i)output_mask\s*[:=][^;]*(?:gpio)?(?:4|5|6|7|15|17|21)\b", code
    )
