//! The 14-key emulate burst loop.
//!
//! Per-iteration decisions (arm / force-proxy / mirror / send) live in
//! `safety_core::emulate_policy` so the host suite tests the exact logic this
//! task executes — in particular PLAN entry step 6: the FIRST transmitted
//! burst after emulate entry encodes hmph=0/inc=0 even if the owner commanded
//! motion during the entry window.

use crate::context::{lock, FirmwareContext};
use crate::hal::wdt;
use crate::logi;
use crate::tasks::{delay_ms, EMULATE_BURST_GAP_MS};
use safety_core::emulate_policy::EmulateTaskPolicy;

pub fn run(ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        wdt::abort(c"emulate_cycle: task WDT subscribe failed");
    }
    logi!("emulate_cycle task started (WDT-supervised)");

    let mut policy = EmulateTaskPolicy::new();

    loop {
        wdt::feed();

        // PLAN NORMAL EXIT, STEP 1: "transmit and finish a complete zero
        // frame", before step 2 waits for the gap and step 3 opens K1.
        //
        // This task owns the motor writer, so this is the only place the
        // obligation can be discharged. The controller records the obligation
        // (`take_exit_zero_frame` hands out an unforgeable token exactly once)
        // and the serial engine refuses to qualify the exit gap while it is
        // outstanding — which is what makes step 1 actually precede step 3
        // rather than racing it.
        //
        // FAIL-SAFE, NOT FAIL-BLOCKING: nothing here can hold the relay
        // closed. The controller's own 1 s exit-gap deadline fires regardless
        // and opens K1 immediately (N19), and every emergency path is
        // untouched, so a wedged writer costs at most that deadline.
        let owed = {
            let mut g = lock(&ctx.guarded);
            g.controller.take_exit_zero_frame()
        };
        if let Some(proof) = owed {
            let mut burst = crate::tasks::burst_buffer::BurstBuffer::new();
            safety_core::cycle::EmulationCycle::write_zero_frame(&mut burst);
            // TX with the SAFETY LOCK RELEASED (as the burst path does): the
            // writer waits for tx-done, which is what "and FINISH" means.
            {
                let mut w = lock(&ctx.writer);
                burst.replay(&mut *w);
            }
            // ONLY NOW is step 1 complete. Discharging before tx-done let the
            // serial engine qualify the exit gap during the ~25-50 ms the
            // burst spends on the wire at 9600 baud, so K1 could open with a
            // truncated frame at the motor — the handover this whole sequence
            // exists to make clean.
            lock(&ctx.guarded).controller.discharge_exit_zero_frame(proof);
        }

        let d = {
            let mut g = lock(&ctx.guarded);
            // The SESSION id, not a bool: a gap-safe exit + re-entry fits
            // inside this task's 100 ms period, and a bool would read
            // `true, true` across it — missing the arm edge, skipping
            // `cycle.reset()`, and putting the owner's motion in the second
            // session's FIRST burst (PLAN entry step 6).
            let session = g.controller.emulate_session();
            let d = policy.step(session, g.mode.is_emulating());
            if d.arm {
                // Controller finished the gap-safe entry: arm the cycle engine
                // at ZERO (entering emulate zeroes motion, so the first burst
                // IS the entry zero frame — PLAN entry step 6).
                g.mode.request_emulate(true);
                let now = ctx.clock.now();
                g.cycle.reset(now);
            } else if d.force_proxy {
                g.mode.watchdog_reset_to_proxy();
            }
            if d.mirror {
                // Owner-commanded motion lives in the safety controller;
                // mirror it into the cycle parameter engine (re-clamped there
                // to the 0..=198 absolute guard). Deferred by the policy until
                // the first post-entry zero burst actually went out.
                let (speed, incline) =
                    (g.controller.speed_tenths(), g.controller.incline_half_percent());
                g.mode.set_speed(speed);
                g.mode.set_incline(incline);
            }
            d
        };

        if d.send_burst {
            let now = ctx.clock.now();

            // (a) Run the cycle under the safety lock, but RECORD the burst
            // instead of transmitting it. `uart_wait_tx_done` waits up to
            // 100 ms per write and a burst is up to 4 writes, so transmitting
            // here would exclude the serial engine from the safety lock for
            // ~400 ms — delaying TREAD_OK, relay feedback, console freshness
            // and every deadline. The C++ keeps TX outside `controller_mu`
            // for exactly this reason.
            let (sent, timeout, burst) = {
                let mut g = lock(&ctx.guarded);
                let mut burst = crate::tasks::burst_buffer::BurstBuffer::new();
                // Destructure so `cycle` and `mode` are disjoint borrows —
                // the C++ holds a reference to the mode machine inside the
                // cycle object, which Rust will not allow.
                let crate::context::Guarded { cycle, mode, .. } = &mut *g;
                let sent = cycle.tick(now, mode, &mut burst);
                let timeout = cycle.consume_safety_timeout();
                (sent, timeout, burst)
            };

            // (b) Transmit with the SAFETY LOCK RELEASED, holding only the
            // writer lock.
            if sent {
                let mut w = lock(&ctx.writer);
                burst.replay(&mut *w);
            }
            if sent {
                policy.on_burst_sent();
            }
            if let Some(proof) = timeout {
                // The 3-hour inactivity timeout zeroed the cycle engine (the
                // wire frames are already zero). Zero the AUTHORITATIVE
                // controller too, BEFORE the next iteration's mirror, so
                // status never reports stale motion and the mirror cannot
                // re-instate it.
                //
                // `proof` is a `SafetyTimeoutFired` token that only
                // `consume_safety_timeout` can mint — the C++ equivalent is a
                // bool any caller could pass `true`.
                let mut g = lock(&ctx.guarded);
                g.controller.safety_timeout_zero_motion(proof, now);
            }
        }
        delay_ms(EMULATE_BURST_GAP_MS);
    }
}
