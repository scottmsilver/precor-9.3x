#![deny(unsafe_code)]

use bringup_core::{
    format_error, format_input_sample, parse_command, Command, DiagnosticErrorCode, OutputBuffer,
    PinSample,
};
use core::fmt::{self, Write};

#[allow(unsafe_code)]
mod hardware;

const STARTUP_SETTLE_MS: u32 = 5_000;
const MAX_REPORT_LINE_BYTES: usize = 512;
const MAX_RESPONSE_LINE_BYTES: usize = 128;
const RECIPE_ID: &str = env!("ESP32TAP_RECIPE_ID");
const GIT_COMMIT: &str = env!("ESP32TAP_GIT_COMMIT");

#[derive(Debug, Clone, Copy)]
enum FailureCode {
    BadRecipe,
    ChipInfo,
    MacRead,
    FlashSize,
    PsramSize,
    GpioRead,
    ProtectedDirection,
    UartWrite,
}

impl FailureCode {
    const fn text(self) -> &'static str {
        match self {
            Self::BadRecipe => "BAD_RECIPE",
            Self::ChipInfo => "CHIP_INFO",
            Self::MacRead => "MAC_READ",
            Self::FlashSize => "FLASH_SIZE",
            Self::PsramSize => "PSRAM_SIZE",
            Self::GpioRead => "GPIO_READ",
            Self::ProtectedDirection => "PROTECTED_DIRECTION",
            Self::UartWrite => "UART_WRITE",
        }
    }
}

impl From<hardware::HardwareError> for FailureCode {
    fn from(error: hardware::HardwareError) -> Self {
        match error {
            hardware::HardwareError::ChipInfo => Self::ChipInfo,
            hardware::HardwareError::MacRead => Self::MacRead,
            hardware::HardwareError::FlashSize => Self::FlashSize,
            hardware::HardwareError::PsramSize => Self::PsramSize,
            hardware::HardwareError::GpioRead => Self::GpioRead,
            hardware::HardwareError::UartWrite => Self::UartWrite,
        }
    }
}

fn main() {
    esp_idf_sys::link_patches();
    hardware::delay_ms(STARTUP_SETTLE_MS);
    if let Err(code) = write_startup_report() {
        fail_and_halt(code);
    }
    sample_loop()
}

fn write_startup_report() -> Result<(), FailureCode> {
    if !lowercase_hex(RECIPE_ID, 64) || !lowercase_hex(GIT_COMMIT, 40) {
        return Err(FailureCode::BadRecipe);
    }

    let chip = hardware::chip_info()?;
    let memory = hardware::memory_info()?;
    let pins = Pins::read()?;
    if !pins.gpio15.direction.is_input()
        || !pins.gpio17.direction.is_input()
        || !pins.gpio21.direction.is_input()
    {
        return Err(FailureCode::ProtectedDirection);
    }

    write_line(format_args!("ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS"))?;
    write_line(format_args!(
        "BUILD recipe={} git={}",
        RECIPE_ID, GIT_COMMIT
    ))?;
    write_line(format_args!(
        "CHIP model={} revision={} mac={:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x} crystal_mhz=40 reset={}",
        chip.model,
        chip.revision,
        chip.mac[0],
        chip.mac[1],
        chip.mac[2],
        chip.mac[3],
        chip.mac[4],
        chip.mac[5],
        chip.reset,
    ))?;
    write_line(format_args!(
        "MEMORY flash_bytes={} psram_total={} internal_free={} psram_free={}",
        memory.flash_bytes, memory.psram_total, memory.internal_free, memory.psram_free,
    ))?;
    write_line(format_args!(
        "PINS gpio4={}/{} gpio5={}/{} gpio6={}/{} gpio7={}/{} gpio15={}/{} gpio16={}/{} gpio17={}/{} gpio18={}/{} gpio21={}/{} gpio38={}/{}",
        level(pins.gpio4.level), pins.gpio4.direction,
        level(pins.gpio5.level), pins.gpio5.direction,
        level(pins.gpio6.level), pins.gpio6.direction,
        level(pins.gpio7.level), pins.gpio7.direction,
        level(pins.gpio15.level), pins.gpio15.direction,
        level(pins.gpio16.level), pins.gpio16.direction,
        level(pins.gpio17.level), pins.gpio17.direction,
        level(pins.gpio18.level), pins.gpio18.direction,
        level(pins.gpio21.level), pins.gpio21.direction,
        level(pins.gpio38.level), pins.gpio38.direction,
    ))?;
    write_line(format_args!("BRINGUP STAGE0 PASS"))
}

fn fail_and_halt(code: FailureCode) -> ! {
    // Best effort, exactly once: a broken UART may lose this one attempt, but
    // the permanent halt below never retries or emits a duplicate record.
    let _ = write_line(format_args!("BRINGUP FAIL code={}", code.text()));
    hardware::halt()
}

fn sample_loop() -> ! {
    let mut command = [0u8; bringup_core::MAX_COMMAND_BYTES];
    let mut length = 0usize;
    let mut overflow = false;
    loop {
        let Some(byte) = hardware::console_read_byte() else {
            continue;
        };

        if !overflow {
            if length < command.len() {
                command[length] = byte;
                length += 1;
            } else {
                overflow = true;
            }
        }

        if byte != b'\n' {
            continue;
        }

        if overflow {
            write_command_error();
        } else {
            handle_command(&command[..length]);
        }
        length = 0;
        overflow = false;
    }
}

fn handle_command(bytes: &[u8]) {
    let Ok(Command::Sample(sequence)) = parse_command(bytes) else {
        write_command_error();
        return;
    };
    let pins = match Pins::read() {
        Ok(pins) => pins,
        Err(_) => fail_and_halt(FailureCode::GpioRead),
    };
    let sample = PinSample {
        sequence,
        gpio4: pins.gpio4.level,
        gpio5: pins.gpio5.level,
        gpio6: pins.gpio6.level,
        gpio7: pins.gpio7.level,
        gpio15_is_input: pins.gpio15.direction.is_input(),
        gpio17_is_input: pins.gpio17.direction.is_input(),
        gpio21_is_input: pins.gpio21.direction.is_input(),
    };
    let mut output = OutputBuffer::<MAX_RESPONSE_LINE_BYTES>::new();
    if format_input_sample(&sample, &mut output).is_err() {
        fail_and_halt(FailureCode::UartWrite);
    }
    if write_protocol_line(output.as_bytes()).is_err() {
        fail_and_halt(FailureCode::UartWrite);
    }
}

fn write_command_error() {
    let mut output = OutputBuffer::<MAX_RESPONSE_LINE_BYTES>::new();
    if format_error(DiagnosticErrorCode::BadCommand, &mut output).is_err() {
        fail_and_halt(FailureCode::UartWrite);
    }
    if write_protocol_line(output.as_bytes()).is_err() {
        fail_and_halt(FailureCode::UartWrite);
    }
}

fn write_protocol_line(bytes: &[u8]) -> Result<(), FailureCode> {
    let mut line = LineBuffer::<MAX_RESPONSE_LINE_BYTES>::new();
    line.push_bytes(bytes).map_err(|_| FailureCode::UartWrite)?;
    line.push_bytes(b"\n").map_err(|_| FailureCode::UartWrite)?;
    hardware::console_write(line.as_bytes()).map_err(Into::into)
}

fn write_line(arguments: fmt::Arguments<'_>) -> Result<(), FailureCode> {
    let mut line = LineBuffer::<MAX_REPORT_LINE_BYTES>::new();
    line.write_fmt(arguments)
        .map_err(|_| FailureCode::UartWrite)?;
    line.push_bytes(b"\n").map_err(|_| FailureCode::UartWrite)?;
    hardware::console_write(line.as_bytes()).map_err(Into::into)
}

const fn lowercase_hex(value: &str, expected_len: usize) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != expected_len {
        return false;
    }
    let mut index = 0;
    while index < bytes.len() {
        if !matches!(bytes[index], b'0'..=b'9' | b'a'..=b'f') {
            return false;
        }
        index += 1;
    }
    true
}

const fn level(value: bool) -> u8 {
    value as u8
}

struct Pins {
    gpio4: hardware::PinState,
    gpio5: hardware::PinState,
    gpio6: hardware::PinState,
    gpio7: hardware::PinState,
    gpio15: hardware::PinState,
    gpio16: hardware::PinState,
    gpio17: hardware::PinState,
    gpio18: hardware::PinState,
    gpio21: hardware::PinState,
    gpio38: hardware::PinState,
}

impl Pins {
    fn read() -> Result<Self, FailureCode> {
        Ok(Self {
            gpio4: hardware::read_pin(4)?,
            gpio5: hardware::read_pin(5)?,
            gpio6: hardware::read_pin(6)?,
            gpio7: hardware::read_pin(7)?,
            gpio15: hardware::read_pin(15)?,
            gpio16: hardware::read_pin(16)?,
            gpio17: hardware::read_pin(17)?,
            gpio18: hardware::read_pin(18)?,
            gpio21: hardware::read_pin(21)?,
            gpio38: hardware::read_pin(38)?,
        })
    }
}

struct LineBuffer<const CAPACITY: usize> {
    bytes: [u8; CAPACITY],
    length: usize,
}

impl<const CAPACITY: usize> LineBuffer<CAPACITY> {
    const fn new() -> Self {
        Self {
            bytes: [0; CAPACITY],
            length: 0,
        }
    }

    fn push_bytes(&mut self, bytes: &[u8]) -> fmt::Result {
        let end = self.length.checked_add(bytes.len()).ok_or(fmt::Error)?;
        if end > CAPACITY {
            return Err(fmt::Error);
        }
        self.bytes[self.length..end].copy_from_slice(bytes);
        self.length = end;
        Ok(())
    }

    fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.length]
    }
}

impl<const CAPACITY: usize> Write for LineBuffer<CAPACITY> {
    fn write_str(&mut self, value: &str) -> fmt::Result {
        self.push_bytes(value.as_bytes())
    }
}
