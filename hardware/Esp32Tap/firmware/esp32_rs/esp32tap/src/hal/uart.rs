//! Inverted UARTs — the RS-485-style idle-LOW treadmill bus.
//!
//! `uart_set_line_inverse` has NO safe wrapper anywhere in esp-idf-hal 0.46 /
//! esp-idf-svc 0.52. It is the replacement for pigpio's `bb_serial_invert`,
//! and without it the bus is unreadable — so it is load-bearing, and it lives
//! here as ONE reviewed wrapper rather than scattered call sites.
//!
//! `gpio_input_enable(ESP_TX)` likewise has no safe wrapper; it turns on the
//! input path of the TX pad so `tx_idle_low()` can read the PHYSICAL line
//! level for emulate-entry step 3.
//!
//! The UART driver install / param config / pin assignment are done through
//! raw IDF calls too, so the whole port lifecycle is visible in one file with
//! the same zero-init-then-assign discipline the C++ uses (IDF adds
//! `uart_config_t` fields across versions; an exhaustive Rust struct literal
//! would stop compiling — this is Report 1 sharp edge 1, and why the IDF
//! revision is pinned).

use crate::pins;
use safety_core::hal::{SerialIn, SerialOut};

pub const CONSOLE_UART: u32 = 1;
pub const MOTOR_TAP_UART: u32 = 2;
/// >= the 128-byte FIFO; ~1 s of 9600-baud RX, so a 5 ms poll cannot drop.
const RX_BUF_BYTES: i32 = 1024;

fn configure_uart(port: u32, tx_pin: i32, rx_pin: i32, inverse_mask: u32) -> bool {
    let mut cfg: esp_idf_sys::uart_config_t = Default::default();
    cfg.baud_rate = 9600;
    cfg.data_bits = esp_idf_sys::uart_word_length_t_UART_DATA_8_BITS;
    cfg.parity = esp_idf_sys::uart_parity_t_UART_PARITY_DISABLE;
    cfg.stop_bits = esp_idf_sys::uart_stop_bits_t_UART_STOP_BITS_1;
    cfg.flow_ctrl = esp_idf_sys::uart_hw_flowcontrol_t_UART_HW_FLOWCTRL_DISABLE;
    cfg.rx_flow_ctrl_thresh = 0;
    cfg.__bindgen_anon_1.source_clk = esp_idf_sys::soc_periph_uart_clk_src_legacy_t_UART_SCLK_DEFAULT;

    // SAFETY: each call below is an IDF C entry point taking scalars, or (for
    // `uart_param_config`) a pointer to a live, fully zero-initialised local
    // that the callee only reads and does not retain. `port` is 1 or 2, both
    // valid ESP32-S3 UART numbers; the pin numbers come from the checked
    // `pins::` constants. Ordering is the IDF-documented one: install driver,
    // configure params, assign pins, then invert.
    unsafe {
        if esp_idf_sys::uart_driver_install(port, RX_BUF_BYTES, 0, 0, core::ptr::null_mut(), 0)
            != esp_idf_sys::ESP_OK
        {
            return false;
        }
        if esp_idf_sys::uart_param_config(port, &cfg as *const _) != esp_idf_sys::ESP_OK {
            return false;
        }
        if esp_idf_sys::uart_set_pin(
            port,
            tx_pin,
            rx_pin,
            esp_idf_sys::UART_PIN_NO_CHANGE,
            esp_idf_sys::UART_PIN_NO_CHANGE,
        ) != esp_idf_sys::ESP_OK
        {
            return false;
        }
        // The RS-485-style idle-LOW inversion. No safe wrapper exists.
        if esp_idf_sys::uart_set_line_inverse(port, inverse_mask) != esp_idf_sys::ESP_OK {
            return false;
        }
    }
    true
}

fn read_port(port: u32, out: &mut [u8]) -> usize {
    if out.is_empty() {
        return 0;
    }
    // SAFETY: `uart_read_bytes` writes at most `out.len()` bytes into the
    // pointer we hand it, and we hand it exactly `out.len()`. Timeout 0 makes
    // it non-blocking. `out` is a live mutable borrow for the whole call.
    let n = unsafe {
        esp_idf_sys::uart_read_bytes(
            port,
            out.as_mut_ptr() as *mut core::ffi::c_void,
            out.len() as u32,
            0,
        )
    };
    if n > 0 {
        n as usize
    } else {
        0
    }
}

/// UART1: console RX (GPIO18) + motor TX (GPIO17), both inverted.
#[derive(Default)]
pub struct ConsoleMotorUart {
    ready: bool,
}

impl ConsoleMotorUart {
    pub const fn new() -> Self {
        ConsoleMotorUart { ready: false }
    }

    pub fn init(&mut self) -> bool {
        // Tie the typed-pin world back to the CHECKED constants: esp-idf-hal's
        // UART constructor takes a typed pin, not a runtime integer, so
        // check_pins.py would otherwise lose its teeth. These const assertions
        // make pin-map drift a COMPILE ERROR.
        const _: () = assert!(pins::K_ESP_TX == 17);
        const _: () = assert!(pins::K_CONS_RX == 18);

        if !configure_uart(
            CONSOLE_UART,
            pins::K_ESP_TX as i32,
            pins::K_CONS_RX as i32,
            esp_idf_sys::uart_signal_inv_t_UART_SIGNAL_RXD_INV
                | esp_idf_sys::uart_signal_inv_t_UART_SIGNAL_TXD_INV,
        ) {
            return false;
        }
        // Enable the input path on the TX pad so `tx_idle_low()` can read the
        // physical level. No safe wrapper exists for this either.
        //
        // SAFETY: `gpio_input_enable` takes a valid pin number by value and
        // only flips that pad's input-enable bit.
        unsafe {
            esp_idf_sys::gpio_input_enable(pins::K_ESP_TX as i32);
        }
        self.ready = true;
        true
    }
}

impl ConsoleMotorUart {
    /// A SECOND handle onto the ALREADY-INITIALISED UART1.
    ///
    /// The UART driver is global per port, so a second `uart_driver_install`
    /// on the same port is both wrong and, under the QEMU test image, FATAL —
    /// it silently panicked the guest into a reboot loop, which is how this
    /// was found. The C++ avoids the question by keeping ONE
    /// `ConsoleMotorUart` in `FirmwareContext` and handing the writer a
    /// reference to it; splitting the safety lock from the writer lock here
    /// means two handles, so this is the explicit "adopt, do not re-install"
    /// constructor.
    pub const fn adopt_initialised() -> Self {
        ConsoleMotorUart { ready: true }
    }
}

impl SerialIn for ConsoleMotorUart {
    fn read(&mut self, out: &mut [u8]) -> usize {
        if !self.ready {
            return 0;
        }
        read_port(CONSOLE_UART, out)
    }
}

impl SerialOut for ConsoleMotorUart {
    /// Blocking whole-message TX. `uart_wait_tx_done` can block ~50 ms at
    /// 9600 baud, which is why the caller holds the writer mutex and NOT the
    /// controller mutex here.
    fn write(&mut self, bytes: &[u8]) -> bool {
        if !self.ready || bytes.is_empty() {
            return false;
        }
        // SAFETY: `uart_write_bytes` reads exactly `bytes.len()` bytes from
        // the pointer; `bytes` is a live borrow for the call and is not
        // retained. `uart_wait_tx_done` takes scalars only.
        unsafe {
            let written = esp_idf_sys::uart_write_bytes(
                CONSOLE_UART,
                bytes.as_ptr() as *const core::ffi::c_void,
                bytes.len(),
            );
            if written < 0 || written as usize != bytes.len() {
                return false;
            }
            // 100 ms in ticks; CONFIG_FREERTOS_HZ=1000 is const-asserted in
            // tasks/mod.rs, so this is 100 ticks.
            esp_idf_sys::uart_wait_tx_done(CONSOLE_UART, 100) == esp_idf_sys::ESP_OK
        }
    }

    fn tx_idle_low(&self) -> bool {
        if !self.ready {
            return false;
        }
        // SAFETY: pure register read of a valid pin (input path enabled in
        // `init`).
        unsafe { esp_idf_sys::gpio_get_level(pins::K_ESP_TX as i32) == 0 }
    }
}

/// UART2: motor tap RX only (GPIO16), inverted.
#[derive(Default)]
pub struct MotorTapUart {
    ready: bool,
}

impl MotorTapUart {
    pub const fn new() -> Self {
        MotorTapUart { ready: false }
    }

    pub fn init(&mut self) -> bool {
        const _: () = assert!(pins::K_PIN3_RX == 16);
        if !configure_uart(
            MOTOR_TAP_UART,
            esp_idf_sys::UART_PIN_NO_CHANGE,
            pins::K_PIN3_RX as i32,
            esp_idf_sys::uart_signal_inv_t_UART_SIGNAL_RXD_INV,
        ) {
            return false;
        }
        self.ready = true;
        true
    }
}

impl SerialIn for MotorTapUart {
    fn read(&mut self, out: &mut [u8]) -> usize {
        if !self.ready {
            return 0;
        }
        read_port(MOTOR_TAP_UART, out)
    }
}
