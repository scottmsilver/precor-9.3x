//! The complete ESP-IDF boundary for the DevKit diagnostic.
//!
//! Every operation here is observational: there is no pad configuration,
//! output write, pull/hold change, UART installation/remap, radio, or restart.

use core::fmt;
use std::io::{Read, Write};

const EXPECTED_FLASH_BYTES: u32 = 8 * 1024 * 1024;
const EXPECTED_PSRAM_BYTES: usize = 8 * 1024 * 1024;
const GPIO_ENABLE_REG: *const u32 = 0x6000_4020 as *const u32;
const GPIO_ENABLE1_REG: *const u32 = 0x6000_402c as *const u32;
const CHIP_ESP32S3: i32 = 9;

// esp-idf-sys 0.37.2's stock binding header omits esp_chip_info.h.  Keep the
// missing ABI declaration local to this boundary instead of widening bindings.
#[repr(C)]
struct RawChipInfo {
    model: i32,
    features: u32,
    revision: u16,
    cores: u8,
}

extern "C" {
    fn esp_chip_info(out_info: *mut RawChipInfo);
}

#[derive(Debug, Clone, Copy)]
pub enum HardwareError {
    ChipInfo,
    MacRead,
    FlashSize,
    PsramSize,
    GpioRead,
    UartWrite,
}

#[derive(Debug, Clone, Copy)]
pub struct ChipInfo {
    pub model: &'static str,
    pub revision: u16,
    pub mac: [u8; 6],
    pub reset: &'static str,
}

#[derive(Debug, Clone, Copy)]
pub struct MemoryInfo {
    pub flash_bytes: u32,
    pub psram_total: usize,
    pub internal_free: usize,
    pub psram_free: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Input,
    Output,
}

impl Direction {
    pub const fn is_input(self) -> bool {
        matches!(self, Self::Input)
    }
}

impl fmt::Display for Direction {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Input => "input",
            Self::Output => "output",
        })
    }
}

#[derive(Debug, Clone, Copy)]
pub struct PinState {
    pub level: bool,
    pub direction: Direction,
}

fn check_esp(result: esp_idf_sys::esp_err_t, error: HardwareError) -> Result<(), HardwareError> {
    if result == esp_idf_sys::ESP_OK {
        Ok(())
    } else {
        Err(error)
    }
}

pub fn chip_info() -> Result<ChipInfo, HardwareError> {
    // SAFETY: `esp_chip_info` writes one initialized POD to the valid local pointer.
    let raw = unsafe {
        let mut value: RawChipInfo = core::mem::zeroed();
        esp_chip_info(&mut value);
        value
    };
    if raw.model != CHIP_ESP32S3 {
        return Err(HardwareError::ChipInfo);
    }

    let mut mac = [0u8; 6];
    // SAFETY: the six-byte local array is writable for the complete base MAC result.
    let result = unsafe { esp_idf_sys::esp_efuse_mac_get_default(mac.as_mut_ptr()) };
    check_esp(result, HardwareError::MacRead)?;

    // SAFETY: `esp_reset_reason` takes no arguments and only reads latched reset state.
    let reset = reset_reason_name(unsafe { esp_idf_sys::esp_reset_reason() });
    Ok(ChipInfo {
        model: "ESP32-S3",
        revision: raw.revision,
        mac,
        reset,
    })
}

pub fn memory_info() -> Result<MemoryInfo, HardwareError> {
    let mut flash_bytes = 0u32;
    let flash_out = &mut flash_bytes;
    // SAFETY: a null chip selects the default flash and `flash_bytes` is a valid out pointer.
    let result = unsafe { esp_idf_sys::esp_flash_get_size(core::ptr::null_mut(), flash_out) };
    check_esp(result, HardwareError::FlashSize)?;
    if flash_bytes != EXPECTED_FLASH_BYTES {
        return Err(HardwareError::FlashSize);
    }

    // SAFETY: these accessors read allocator/PSRAM metadata and take no pointers.
    let (psram_total, internal_free, psram_free) = unsafe {
        (
            esp_idf_sys::esp_psram_get_size(),
            esp_idf_sys::heap_caps_get_free_size(
                (esp_idf_sys::MALLOC_CAP_INTERNAL | esp_idf_sys::MALLOC_CAP_8BIT) as u32,
            ),
            esp_idf_sys::heap_caps_get_free_size(
                (esp_idf_sys::MALLOC_CAP_SPIRAM | esp_idf_sys::MALLOC_CAP_8BIT) as u32,
            ),
        )
    };
    // SAFETY: this accessor reads the heap capability table and takes no pointers.
    let caps_psram_total = unsafe {
        esp_idf_sys::heap_caps_get_total_size(
            (esp_idf_sys::MALLOC_CAP_SPIRAM | esp_idf_sys::MALLOC_CAP_8BIT) as u32,
        )
    };
    if psram_total != EXPECTED_PSRAM_BYTES || caps_psram_total == 0 {
        return Err(HardwareError::PsramSize);
    }

    Ok(MemoryInfo {
        flash_bytes,
        psram_total,
        internal_free,
        psram_free,
    })
}

pub fn read_pin(pin: u8) -> Result<PinState, HardwareError> {
    let direction = gpio_get_direction(pin)?;
    // SAFETY: the caller supplies one of the valid, fixed ESP32-S3 GPIO numbers.
    let level = unsafe { esp_idf_sys::gpio_get_level(i32::from(pin)) };
    if level != 0 && level != 1 {
        return Err(HardwareError::GpioRead);
    }
    Ok(PinState {
        level: level == 1,
        direction,
    })
}

/// Reads the S3 output-enable register; it never changes pad state.
fn gpio_get_direction(pin: u8) -> Result<Direction, HardwareError> {
    let (register, bit) = match pin {
        0..=31 => (GPIO_ENABLE_REG, pin),
        32..=48 => (GPIO_ENABLE1_REG, pin - 32),
        _ => return Err(HardwareError::GpioRead),
    };
    // SAFETY: documented aligned S3 GPIO output-enable register, read only.
    let output_enable = unsafe { core::ptr::read_volatile(register) };
    Ok(if output_enable & (1u32 << bit) == 0 {
        Direction::Input
    } else {
        Direction::Output
    })
}

pub fn console_write(bytes: &[u8]) -> Result<(), HardwareError> {
    let mut output = std::io::stdout().lock();
    let written = output.write(bytes).map_err(|_| HardwareError::UartWrite)?;
    if written != bytes.len() {
        return Err(HardwareError::UartWrite);
    }
    output.flush().map_err(|_| HardwareError::UartWrite)
}

pub fn console_read_byte() -> Option<u8> {
    let mut byte = [0u8; 1];
    let read = std::io::stdin().lock().read(&mut byte).ok()?;
    if read == 1 {
        Some(byte[0])
    } else {
        None
    }
}

pub fn delay_ms(milliseconds: u32) {
    let ticks = milliseconds.div_ceil(10);
    // SAFETY: `vTaskDelay` blocks only the current task for a bounded tick count.
    unsafe { esp_idf_sys::vTaskDelay(ticks) };
}

pub fn halt() -> ! {
    loop {
        delay_ms(1_000);
    }
}

fn reset_reason_name(reason: esp_idf_sys::esp_reset_reason_t) -> &'static str {
    match reason {
        1 => "power_on",
        2 => "external",
        3 => "software",
        4 => "panic",
        5 => "interrupt_wdt",
        6 => "task_wdt",
        7 => "other_wdt",
        8 => "deep_sleep",
        9 => "brownout",
        10 => "sdio",
        11 => "usb",
        12 => "jtag",
        13 => "efuse",
        14 => "power_glitch",
        15 => "cpu_lockup",
        _ => "unknown",
    }
}
