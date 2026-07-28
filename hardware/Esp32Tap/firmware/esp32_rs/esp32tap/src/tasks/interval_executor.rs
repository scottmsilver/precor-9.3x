//! Interval executor — phase-1 stub.
//!
//! The program/interval engine belongs to the network/application tier, which
//! is explicitly OUT OF SCOPE for this port. What remains here is the task
//! itself: WDT-supervised, 1 s tick, and the 5 s heartbeat line that
//! `qemu_smoke.sh` uses to prove GUEST uptime (assertions #11 and #12).
//!
//! The 16384-byte stack is kept even though this body is a stub: it matches
//! the C++ task, the smoke gate was tuned against the current memory envelope,
//! and 16 KB is nothing against 512 KB SRAM.

use crate::context::FirmwareContext;
use crate::hal::wdt;
use crate::logi;
use crate::tasks::{delay_ms, EXECUTOR_TICK_MS};

pub fn run(_ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        wdt::abort(c"interval_exec: task WDT subscribe failed");
    }
    logi!("interval_executor task started (WDT-supervised)");

    let mut seconds: u32 = 0;
    loop {
        wdt::feed();
        delay_ms(EXECUTOR_TICK_MS);
        seconds = seconds.wrapping_add(1);
        if seconds % 5 == 0 {
            // Cold-path liveness heartbeat on the debug console (UART0, never
            // the treadmill bus). qemu_smoke.sh parses these to prove >=15 s
            // of panic-free guest uptime.
            logi!("heartbeat uptime={}s", seconds);
        }
    }
}
