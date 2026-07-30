//! The portable hardware abstraction the safety core consumes.
//!
//! Port of `components/portable_core/hal/hal.h`. Implemented by
//! `esp32tap/src/hal/` (ESP-IDF target) and `safety_core/tests/common/fake_hal.rs`
//! (host tests).
//!
//! THE ONE STRUCTURAL CHANGE vs the C++ interface: [`SafetyIo`] exposes
//! [`SafetyIo::apply`] taking a whole [`OutputIntent`], and deliberately does
//! NOT expose `set_relay_cmd`/`set_tx_enable`. In C++ the tx-before-relay
//! order lives in `apply_outputs_locked()`, a convention any caller can bypass
//! by reaching for the setters (the QEMU shim does exactly that on its
//! wrapper). Here there is no such API, so the order exists at exactly one
//! site per implementation.
//!
//! HONEST LIMIT: single-sourced is not the same as correct. tx-before-relay
//! remains a semantic invariant carried by boot-envelope case 2 and the S3
//! audit subsequence.

use crate::safety::controller::OutputIntent;
use crate::units::{Micros, NcHigh, NoHigh, TreadOk, VbusPresent};

/// Monotonic microsecond clock.
pub trait Clock {
    fn now(&self) -> Micros;
}

/// Non-blocking read of whatever bytes are available.
pub trait SerialIn {
    fn read(&mut self, out: &mut [u8]) -> usize;
}

/// Blocking whole-message TX.
pub trait SerialOut {
    fn write(&mut self, bytes: &[u8]) -> bool;
    /// Physical idle check for emulate-entry step 3 (the line rests LOW).
    fn tx_idle_low(&self) -> bool;
}

/// Safety GPIO. Note the asymmetry: outputs go through one `apply`, inputs are
/// individually readable.
pub trait SafetyIo {
    /// Drive the commanded outputs. Implementations MUST write `tx_enable`
    /// before `relay`.
    fn apply(&mut self, intent: OutputIntent);
    /// Read-only by construction (R32-isolated on the board).
    fn tread_ok(&self) -> TreadOk;
    /// K1 pole-B NC feedback (10k pull-up).
    fn k1_nc_high(&self) -> NcHigh;
    /// K1 pole-B NO feedback (10k pull-up).
    fn k1_no_high(&self) -> NoHigh;
    /// Already de-inverted — GPIO7 is active-low and the inversion happens
    /// exactly once, in the HAL.
    fn vbus_present(&self) -> VbusPresent;
    fn set_status_led(&mut self, on: bool);
}

/// Sub-millisecond busy wait, for the `FEEDBACK_POLL_US` window.
pub trait DelayUs {
    fn delay_us(&self, us: u32);
}
