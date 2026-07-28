//! The three supervised core-0 tasks (PLAN's normative WDT matrix).
//!
//! | Task           | Core | Prio | Stack | WDT                       | Cadence |
//! |----------------|------|------|-------|---------------------------|---------|
//! | serial_engine  | 0    | 10   | 8192  | subscribe, ABORT on fail  | 5 ms    |
//! | emulate_cycle  | 0    | 9    | 6144  | subscribe, ABORT on fail  | 100 ms  |
//! | interval_exec  | 0    | 5    | 16384 | subscribe, ABORT on fail  | 1 s     |
//!
//! A stall in any of them panics -> silent reboot -> GPIO21 Hi-Z -> R23
//! pull-down -> relay released. The hardware completes the guarantee; there is
//! deliberately no software "WDT handler".

// COMPILER-ENFORCED unsafe containment for this module and every module
// below it. `forbid` (unlike the crate root's `deny`) CANNOT be lifted by an
// inner `#[allow(unsafe_code)]` — that is a hard error, not a warning — so
// this is a guarantee rather than a convention. Added 2026-07-28 after a
// reviewer disproved the "deny contains it" claim by counterexample.
#![forbid(unsafe_code)]


pub mod burst_buffer;
pub mod emulate_cycle;
pub mod interval_executor;
pub mod serial_engine;

/// FreeRTOS tick guard.
///
/// `vTaskDelay(0)` is a busy spin, so if `CONFIG_FREERTOS_HZ` were low enough
/// that `pdMS_TO_TICKS(5)` truncated to 0, the prio-10 core-0 serial task
/// would starve `app_main` (the other supervised tasks never get created) and
/// the core-0 idle task — a 2 s task-WDT panic, silent reboot, forever.
///
/// PARITY, NOT IMPROVEMENT: this is exactly the guarantee the C++
/// `static_assert` already gives. Rust adds nothing here, and the FreeRTOS
/// tick misconfiguration remains one of the three C++ defects Rust does NOT
/// catch on its own.
pub const _: () = assert!(esp_idf_sys::configTICK_RATE_HZ == 1000);

/// Milliseconds -> ticks. Valid only because of the assertion above.
pub const fn ms_to_ticks(ms: u32) -> u32 {
    ms * (esp_idf_sys::configTICK_RATE_HZ / 1000)
}

pub const SERIAL_LOOP_MS: u32 = 5;
pub const EMULATE_BURST_GAP_MS: u32 = 100;
pub const EXECUTOR_TICK_MS: u32 = 1000;

const _: () = assert!(ms_to_ticks(SERIAL_LOOP_MS) > 0);
const _: () = assert!(ms_to_ticks(EMULATE_BURST_GAP_MS) > 0);

pub fn delay_ms(ms: u32) {
    std::thread::sleep(std::time::Duration::from_millis(ms as u64));
}
