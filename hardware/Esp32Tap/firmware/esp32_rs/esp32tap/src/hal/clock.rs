//! Monotonic microsecond clock.

use safety_core::hal::Clock;
use safety_core::units::Micros;

#[derive(Clone, Copy, Default)]
pub struct Esp32Clock;

impl Esp32Clock {
    pub const fn new() -> Self {
        Esp32Clock
    }
    pub fn now(&self) -> Micros {
        // SAFETY: `esp_timer_get_time` is a pure read of the 64-bit monotonic
        // esp_timer counter. It takes no arguments, touches no memory we own,
        // and is safe to call from any task at any time after the early boot
        // esp_timer init (which precedes app_main).
        Micros::new(unsafe { esp_idf_sys::esp_timer_get_time() })
    }
}

impl Clock for Esp32Clock {
    fn now(&self) -> Micros {
        Esp32Clock::now(self)
    }
}
