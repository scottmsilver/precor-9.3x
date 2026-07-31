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
    assert len(image) >= 20, f"ELF header is truncated or empty: {path}"
    assert image[:4] == b"\x7fELF", f"ELF magic is missing: {path}"
    assert image[4] == 1, f"ELF is not 32-bit: {path}"
    assert image[5] == 1, f"ELF is not little-endian: {path}"
    assert image[6] == 1, f"ELF header version is invalid: {path}"
    assert int.from_bytes(image[16:18], "little") == 2, f"ELF is not executable: {path}"
    assert (
        int.from_bytes(image[18:20], "little") == 94
    ), f"ELF machine is not Xtensa: {path}"
    return image


def _generated_sdkconfig_fixture() -> bytes:
    header = (
        "#\n"
        "# Automatically generated file. DO NOT EDIT.\n"
        "# Espressif IoT Development Framework (ESP-IDF) 5.5.4 Project Configuration\n"
        "#\n"
    )
    return (header + _read(DEFAULTS)).encode()


def _xtensa_elf_fixture() -> bytes:
    # ELF32, little-endian, current ELF version, ET_EXEC, EM_XTENSA (94).
    return b"\x7fELF\x01\x01\x01" + b"\x00" * 9 + b"\x02\x00\x5e\x00"


def _invoke_generated_verifier(
    monkeypatch: pytest.MonkeyPatch, sdkconfig: str, elf: str
) -> None:
    monkeypatch.setenv("DEVKIT_VERIFY_GENERATED", "1")
    monkeypatch.setenv("DEVKIT_GENERATED_SDKCONFIG", sdkconfig)
    monkeypatch.setenv("DEVKIT_FIRMWARE_ELF", elf)
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
    _validate_generated_sdkconfig(sdkconfig)
    config = _parse_kconfig(sdkconfig)
    _assert_n8r8_uart_halt_config(config)
    # ESP_WIFI_ENABLED is an invisible capability symbol forced on for an S3
    # by IDF 5.5.4.  Prove operational radio absence from the linked image.
    assert config.get("CONFIG_ESP_WIFI_ENABLED") in {"n", "y"}
    image = _read_xtensa_elf(elf)
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
    (tmp_path / "firmware.elf").write_bytes(_xtensa_elf_fixture())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssertionError, match="absolute"):
        _invoke_generated_verifier(monkeypatch, "sdkconfig", "firmware.elf")


def test_generated_verifier_rejects_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdkconfig = tmp_path / "real-sdkconfig"
    elf = tmp_path / "real.elf"
    sdkconfig.write_bytes(_generated_sdkconfig_fixture())
    elf.write_bytes(_xtensa_elf_fixture())
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
    valid_elf.write_bytes(_xtensa_elf_fixture())
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
