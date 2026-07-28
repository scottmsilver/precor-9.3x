//! Safety GPIO.
//!
//! **`PinDriver` IS DELIBERATELY NOT USED FOR THE SAFETY OUTPUTS.**
//!
//! `esp_idf_hal::gpio::PinDriver::output()` calls only `gpio_set_direction` —
//! it never drives the level first, so the pad emits whatever the output
//! register happened to hold. For `RELAY_CMD` and `TX_ENABLE` that ordering IS
//! the safety property: the C++ `configure_output_low` sets the LEVEL, then
//! the DIRECTION, then re-asserts the level, so the pad goes push-pull already
//! low. Board-level R23/R27 pull-downs and the TREAD_OK AND-gate still fail
//! safe, but there is no reason to give up the firmware layer as well.
//!
//! Inputs are configured with `PULLUP_DISABLE`/`PULLDOWN_DISABLE`: every input
//! has its board resistor (R25/R26/R30 pull-ups, R21/R22 on TREAD_OK, R7/R8
//! series taps), and an internal pull fighting them would be a real hazard.
//!
//! `K_TREAD_OK_MCU` is never passed to an output function anywhere in this
//! file — input-only by construction, matching R32's purpose.

use crate::pins;
use safety_core::hal::SafetyIo;
use safety_core::safety::controller::OutputIntent;
use safety_core::units::{NcHigh, NoHigh, TreadOk, VbusPresent};

fn set_level(io: u32, level: u32) -> bool {
    // SAFETY: `gpio_set_level` takes a pin number and a level by value. `io`
    // is always one of the `pins::` constants, each a valid ESP32-S3 GPIO
    // (checked against design.py by tools/check_pins.py), so the driver's own
    // range check cannot fail. No memory is shared.
    unsafe { esp_idf_sys::gpio_set_level(io as i32, level) == esp_idf_sys::ESP_OK }
}

fn get_level(io: u32) -> u32 {
    // SAFETY: `gpio_get_level` is a pure register read for a valid pin number
    // (see `set_level`).
    unsafe { esp_idf_sys::gpio_get_level(io as i32) as u32 }
}

fn config(cfg: &esp_idf_sys::gpio_config_t) -> bool {
    // SAFETY: `gpio_config` reads the pointed-to struct and returns; it does
    // not retain the pointer. `cfg` is a live borrow for the whole call, and
    // it is zero-initialised then field-assigned (never partially
    // initialised), so every field the running IDF version reads is valid.
    unsafe { esp_idf_sys::gpio_config(cfg as *const _) == esp_idf_sys::ESP_OK }
}

/// LEVEL BEFORE DIRECTION, then re-assert. This ordering is the safety
/// property — see the module note.
fn configure_output_low(io: u32) -> bool {
    if !set_level(io, 0) {
        return false;
    }
    // Zero-init then assign (not a struct literal): IDF adds
    // `gpio_config_t` fields across versions, and an exhaustive Rust struct
    // literal would stop compiling on any such addition. This is the same
    // hazard the C++ comment in esp32_uart_port.cpp calls out, and the reason
    // the IDF revision is PINNED (Report 1 sharp edge 1).
    let mut cfg: esp_idf_sys::gpio_config_t = Default::default();
    cfg.pin_bit_mask = 1u64 << io;
    cfg.mode = esp_idf_sys::gpio_mode_t_GPIO_MODE_OUTPUT;
    cfg.pull_up_en = esp_idf_sys::gpio_pullup_t_GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = esp_idf_sys::gpio_pulldown_t_GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = esp_idf_sys::gpio_int_type_t_GPIO_INTR_DISABLE;
    if !config(&cfg) {
        return false;
    }
    set_level(io, 0)
}

fn configure_input(io: u32) -> bool {
    let mut cfg: esp_idf_sys::gpio_config_t = Default::default();
    cfg.pin_bit_mask = 1u64 << io;
    cfg.mode = esp_idf_sys::gpio_mode_t_GPIO_MODE_INPUT;
    cfg.pull_up_en = esp_idf_sys::gpio_pullup_t_GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = esp_idf_sys::gpio_pulldown_t_GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = esp_idf_sys::gpio_int_type_t_GPIO_INTR_DISABLE;
    config(&cfg)
}

#[derive(Default)]
pub struct Esp32SafetyIo {
    ready: bool,
}

impl Esp32SafetyIo {
    pub const fn new() -> Self {
        Esp32SafetyIo { ready: false }
    }

    /// Outputs LOW before anything else (boot = Proxy, relay released).
    pub fn init(&mut self) -> bool {
        let mut ok = configure_output_low(pins::K_RELAY_CMD);
        ok = configure_output_low(pins::K_TX_ENABLE) && ok;
        ok = configure_output_low(pins::K_STATUS_LED) && ok;
        // Inputs. TREAD_OK_MCU is deliberately never given an output mode.
        ok = configure_input(pins::K_TREAD_OK_MCU) && ok;
        ok = configure_input(pins::K_K1_NC_FB) && ok;
        ok = configure_input(pins::K_K1_NO_FB) && ok;
        ok = configure_input(pins::K_VBUS_PRESENT_N) && ok;
        self.ready = ok;
        ok
    }

    pub fn is_ready(&self) -> bool {
        self.ready
    }

    /// The levels read back FROM THE PADS (`gpio_get_level`), not
    /// self-reported.
    ///
    /// CORRECTED 2026-07-28. This used to say it was what the QEMU shim's
    /// `io_relay`/`io_tx` report. It is not: in the test image
    /// `QemuTestSafetyIo` shadows `observed_relay`/`observed_tx` with its own
    /// atomic mirror of the last `apply()`, so the values the harness asserts
    /// on are IO-BOUNDARY MIRRORS — one layer below the controller's
    /// self-report, and genuinely independent of it, but NOT pad reads. These
    /// methods are the production path; nothing in the QEMU gate calls them.
    /// See `qemu_test/safety_io.rs`.
    pub fn observed_relay(&self) -> bool {
        get_level(pins::K_RELAY_CMD) != 0
    }
    pub fn observed_tx(&self) -> bool {
        get_level(pins::K_TX_ENABLE) != 0
    }
}

impl SafetyIo for Esp32SafetyIo {
    /// The single output write site. TX_ENABLE FIRST, then RELAY_CMD.
    ///
    /// HONEST LIMIT: making this the only site makes the order
    /// single-sourced, not correct. tx-before-relay is a semantic invariant no
    /// compiler checks; it is carried by boot-envelope case 2 and the S3 audit
    /// subsequence.
    fn apply(&mut self, intent: OutputIntent) {
        set_level(pins::K_TX_ENABLE, intent.tx_enable.get() as u32);
        set_level(pins::K_RELAY_CMD, intent.relay.get() as u32);
    }

    fn tread_ok(&self) -> TreadOk {
        TreadOk(get_level(pins::K_TREAD_OK_MCU) != 0)
    }

    fn k1_nc_high(&self) -> NcHigh {
        NcHigh(get_level(pins::K_K1_NC_FB) != 0)
    }

    fn k1_no_high(&self) -> NoHigh {
        NoHigh(get_level(pins::K_K1_NO_FB) != 0)
    }

    /// GPIO7 is ACTIVE-LOW: LOW means VBUS present. The inversion happens
    /// exactly once — HERE — so `VbusPresent` is always post-inversion.
    fn vbus_present(&self) -> VbusPresent {
        VbusPresent(get_level(pins::K_VBUS_PRESENT_N) == 0)
    }

    fn set_status_led(&mut self, on: bool) {
        set_level(pins::K_STATUS_LED, on as u32);
    }
}
