//! Task watchdog subscribe + feed.
//!
//! **`TWDTDriver` IS DELIBERATELY NOT USED.** `esp_idf_hal::task::watchdog::
//! TWDTDriver` is unsuitable for a safety core on three independent counts:
//!
//! 1. `watch_current_task(&mut self)` mutably borrows the single TWDT
//!    peripheral, so three tasks on three threads cannot each subscribe.
//! 2. The returned `WatchdogSubscription` has a `Drop` impl calling
//!    `esp_task_wdt_delete` — dropping it SILENTLY UNSUBSCRIBES the task with
//!    no diagnostic, and the stall-detect guarantee evaporates.
//! 3. `TWDTDriver::new` calls `esp_task_wdt_reconfigure`, which can lower
//!    `trigger_panic` AT RUNTIME while the mandated
//!    `grep CONFIG_ESP_TASK_WDT_PANIC=y sdkconfig` gate still passes.
//!
//! Two lines of our own FFI have none of those failure modes and match
//! `esp_hal/wdt.cpp` exactly.
//!
//! HONEST LIMIT: Report 1 established that the task-WDT PANIC PATH cannot be
//! validated under esp-QEMU in EITHER language — a plain-C control on the same
//! emulator stalls identically with no panic. `qemu_smoke.sh`'s
//! `forbid "Task watchdog got triggered"` therefore currently forbids an
//! unreachable condition. That is a pre-existing gap in the C++ firmware's own
//! verification, not something this port introduces, and it needs bench
//! hardware to close.

/// Subscribe the CALLING task. Returns false if the subscribe failed; every
/// caller must then `esp_system_abort` — running unsupervised is not an option
/// under the PLAN WDT matrix.
pub fn subscribe_current_task() -> bool {
    // SAFETY: `esp_task_wdt_add(NULL)` means "the calling task"; passing a
    // null handle is the documented IDF idiom, not a dereference. The TWDT is
    // system-initialised before app_main (CONFIG_ESP_TASK_WDT_INIT=y), so the
    // subsystem is live. No memory is shared.
    unsafe { esp_idf_sys::esp_task_wdt_add(core::ptr::null_mut()) == esp_idf_sys::ESP_OK }
}

/// Feed the calling task's WDT.
pub fn feed() {
    // SAFETY: `esp_task_wdt_reset` takes no arguments and only touches the
    // TWDT's own bookkeeping for the calling task, which subscribed above.
    unsafe {
        esp_idf_sys::esp_task_wdt_reset();
    }
}

/// Fail loud: panic -> silent reboot -> GPIO21 Hi-Z -> R23 pull-down -> relay
/// released. The hardware completes the guarantee; there is deliberately no
/// software "WDT handler".
pub fn abort(msg: &core::ffi::CStr) -> ! {
    // SAFETY: `esp_system_abort` takes a NUL-terminated string it only reads,
    // and never returns. `msg` is a 'static CStr.
    unsafe {
        esp_idf_sys::esp_system_abort(msg.as_ptr());
    }
}
