//! `qemu-test`-only harness surface.
//!
//! Compiled ONLY under the `qemu-test` cargo feature. A cfg'd-out module is
//! never compiled at all, so the production binary provably contains none of
//! these strings — including panic-location strings, which a
//! `if cfg!(...)`-style gate would still emit. S6
//! (`test_s6_production_image_has_no_test_surface`) asserts exactly that, with
//! the test image as the positive control.
//!
//! A test image logs a "QEMU-TEST build" banner at boot so it can never be
//! mistaken for production. NEVER flash a test image to hardware.
//!
//! # Why this exists (proven QEMU ground truth)
//!
//! * The pinned esp-QEMU 9.2.2 hard-wires uart0->serial0 and uart1->serial1;
//!   UART2 has NO chardev and cannot be wired by any mechanism, so the
//!   motor-tap port is remapped to UART0 RX here.
//! * The esp32s3 GPIO model is a stub with ZERO drivable inputs: `GPIO_IN`
//!   always reads 0, so K1 feedback / TREAD_OK / VBUS need the scripted model.
//!
//! The shim only FEEDS the production code paths. It provides no way to bypass
//! clamps, console freshness, gap qualification, or feedback qualification —
//! production semantics are not weakened.

pub mod motor_tap;
pub mod safety_io;
pub mod shim_task;

pub use motor_tap::QemuTestMotorTap;
pub use safety_io::{K1Mode, QemuTestSafetyIo};
pub use shim_task::run;

use safety_core::FixedStr;
use std::sync::atomic::{AtomicBool, Ordering};

static EXECUTOR_HELD: AtomicBool = AtomicBool::new(false);

pub fn set_executor_held(held: bool) {
    EXECUTOR_HELD.store(held, Ordering::Release);
}

pub fn executor_held() -> bool {
    EXECUTOR_HELD.load(Ordering::Acquire)
}

/// Raw console print with NO `ESP_LOG` decoration — the exact equivalent of
/// the C++ shim's `std::printf`.
///
/// `esp_rom_printf` goes straight out UART0, bypassing both the VFS (which is
/// what makes Rust `println!` panic under esp-QEMU) and `ESP_LOG`'s
/// timestamp/colour framing. That framing matters: the harness's
/// `QTAUDIT (\d+) (.*)$` captures to end-of-line, so an ANSI colour reset
/// suffix would corrupt every event text it compares.
pub fn qt_print(line: &str) {
    let mut bytes = [0u8; 257];
    let n = core::cmp::min(line.len(), 256);
    bytes[..n].copy_from_slice(&line.as_bytes()[..n]);
    bytes[n] = 0;
    // SAFETY: `bytes` is NUL-terminated within its own length and lives on
    // this stack frame for the whole call. It is passed as the ARGUMENT to a
    // literal "%s\n" format, so line content can never be interpreted as a
    // format string.
    unsafe {
        esp_idf_sys::esp_rom_printf(
            c"%s\n".as_ptr(),
            bytes.as_ptr() as *const core::ffi::c_char,
        );
    }
}

/// Render `format_args!` into a stack buffer and `qt_print` it. No allocation.
pub fn qt_fmt(args: core::fmt::Arguments<'_>) {
    use core::fmt::Write as _;
    let mut buf = FixedStr::<256>::new();
    let _ = buf.write_fmt(args);
    qt_print(buf.as_str());
}

#[macro_export]
macro_rules! qt {
    ($($arg:tt)*) => {
        $crate::qemu_test::qt_fmt(format_args!($($arg)*))
    };
}
