//! Sub-millisecond busy wait, for the `FEEDBACK_POLL_US` window.

use safety_core::hal::DelayUs;

#[derive(Clone, Copy, Default)]
pub struct RomDelay;

impl RomDelay {
    pub const fn new() -> Self {
        RomDelay
    }
}

impl DelayUs for RomDelay {
    fn delay_us(&self, us: u32) {
        // SAFETY: `esp_rom_delay_us` is a ROM busy-wait loop. It takes a
        // by-value count, touches no memory, and blocks the calling task
        // without yielding — which is exactly what the bounded feedback
        // window wants (a vTaskDelay would round up to a 1 ms tick and make
        // the 10 ms deadline unsatisfiable again).
        unsafe { esp_idf_sys::esp_rom_delay_us(us) }
    }
}
