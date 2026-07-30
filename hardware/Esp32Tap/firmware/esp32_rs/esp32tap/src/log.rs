//! Logging — `ESP_LOG`, deliberately NOT `println!`.
//!
//! Two reasons, both load-bearing:
//!
//! 1. It is what the C++ producer sites do (`ESP_LOGI` -> `ets_printf` ->
//!    UART0, bypassing the VFS), and the QEMU harness greps those exact lines.
//! 2. Rust `println!` PANICS on the first print under esp-QEMU: IDF defaults
//!    to `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y`, that peripheral is
//!    unemulated, the VFS write returns 0 bytes, and std turns that into
//!    "failed printing to stdout: Success (os error 0)" — a 31,688-line boot
//!    loop. `CONFIG_ESP_CONSOLE_SECONDARY_NONE=y` is set as belt-and-braces.
//!
//! Rendering goes into a stack `FixedStr<192>` via `core::fmt::Write`, so the
//! log path allocates nothing.

use safety_core::FixedStr;

/// TAG for every line — matches all six C++ producer sites.
pub const TAG: &core::ffi::CStr = c"esp32tap";

pub const LEVEL_ERROR: u32 = esp_idf_sys::esp_log_level_t_ESP_LOG_ERROR;
pub const LEVEL_WARN: u32 = esp_idf_sys::esp_log_level_t_ESP_LOG_WARN;
pub const LEVEL_INFO: u32 = esp_idf_sys::esp_log_level_t_ESP_LOG_INFO;

/// Write one already-rendered line at `level`.
pub fn write_line(level: u32, line: &str) {
    let mut buf = FixedStr::<192>::new();
    buf.push_str(line);
    // NUL-terminate in place: `esp_log_write` takes a C string.
    let mut bytes = [0u8; 193];
    let n = core::cmp::min(buf.as_bytes().len(), 192);
    bytes[..n].copy_from_slice(&buf.as_bytes()[..n]);
    bytes[n] = 0;
    // SAFETY: `bytes` is NUL-terminated within its own length, lives on this
    // stack frame for the whole call, and is passed through a "%s\n" format so
    // esp_log_write treats it strictly as data (no format-string injection
    // from log content). TAG is a 'static CStr.
    unsafe {
        esp_idf_sys::esp_log_write(
            level,
            TAG.as_ptr(),
            c"%s\n".as_ptr(),
            bytes.as_ptr() as *const core::ffi::c_char,
        );
    }
}

/// Render `format_args!` into a stack buffer and emit it. No allocation.
pub fn log_fmt(level: u32, args: core::fmt::Arguments<'_>) {
    use core::fmt::Write as _;
    let mut buf = FixedStr::<192>::new();
    let _ = buf.write_fmt(args);
    write_line(level, buf.as_str());
}

#[macro_export]
macro_rules! logi {
    ($($arg:tt)*) => {
        $crate::log::log_fmt($crate::log::LEVEL_INFO, format_args!($($arg)*))
    };
}

#[macro_export]
macro_rules! logw {
    ($($arg:tt)*) => {
        $crate::log::log_fmt($crate::log::LEVEL_WARN, format_args!($($arg)*))
    };
}

#[macro_export]
macro_rules! loge {
    ($($arg:tt)*) => {
        $crate::log::log_fmt($crate::log::LEVEL_ERROR, format_args!($($arg)*))
    };
}

