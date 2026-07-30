//! UART drain + safety controller update loop.
//!
//! Coarse cadence is 5 ms; relay transfers get a dedicated SUB-MILLISECOND
//! sampling window, because the 10 ms feedback deadline plus the 1 ms
//! continuous-stable requirement is UNSATISFIABLE at 5 ms sampling.
//!
//! RX is polled, not event-queue driven (the UART event queue is M2 work): at
//! 9600 baud the 128-byte hardware FIFO plus the 1024-byte driver ring hold
//! >1 s of traffic, so a 5 ms poll can never drop bytes. Silence on both taps
//! (bench rig idle, QEMU) is a normal Proxy condition and simply yields
//! zero-byte reads.

use crate::context::{lock, FirmwareContext, Guarded};
use crate::hal::{delay::RomDelay, wdt};
use crate::logi;
use crate::tasks::{delay_ms, SERIAL_LOOP_MS};
use safety_core::hal::{DelayUs, SafetyIo, SerialIn};
use safety_core::kv::kv_parse;
use safety_core::safety::constants::{FEEDBACK_POLL_US, GAP_QUALIFY_US};
use safety_core::safety::controller::{OutputIntent, SafeMode};
use safety_core::safety::{in_feedback_wait, run_feedback_window, FeedbackWindowIo};
use safety_core::units::{Micros, NcHigh, NoHigh, TreadOk};

/// The feedback window's single IO handle.
///
/// The C++ passes five lambdas that all capture the same `ctx`, so the apply
/// path MUTATES the same `safety_io` the nc/no paths READ. Rust rejects that
/// aliasing outright, which is why the window takes one `&mut` handle.
struct WindowIo<'a> {
    io: &'a mut crate::context::SafetyIoImpl,
    clock: &'a crate::hal::Esp32Clock,
    delay: RomDelay,
}

impl FeedbackWindowIo for WindowIo<'_> {
    fn now(&self) -> Micros {
        self.clock.now()
    }
    fn nc(&self) -> NcHigh {
        self.io.k1_nc_high()
    }
    fn no(&self) -> NoHigh {
        self.io.k1_no_high()
    }
    fn tread_ok(&self) -> TreadOk {
        self.io.tread_ok()
    }
    fn apply(&mut self, intent: OutputIntent) {
        self.io.apply(intent);
    }
    fn delay(&mut self) {
        self.delay.delay_us(FEEDBACK_POLL_US.get() as u32);
    }
}

/// Drain the console tap: raw bytes into the controller's console scanner and
/// the byte counter, then parsed KV into the console-takeover path.
///
/// `scratch_raw` / `scratch_pairs` are MEMBERS of `Guarded`, not stack
/// locals — see the comment on those fields. Nothing here may become a
/// multi-KB stack local without re-sizing the task stack.
fn drain_console(g: &mut Guarded, now: Micros) {
    let Guarded {
        console_uart,
        scratch_raw,
        scratch_pairs,
        console_parse,
        controller,
        mode,
        key_cache,
        last_console_rx,
        executor_inhibited,
        ..
    } = g;
    let n = console_uart.read(scratch_raw);
    if n == 0 {
        return;
    }
    *last_console_rx = now;
    controller.observe_console_bytes(&scratch_raw[..n], now);
    mode.add_console_bytes(n as u32);

    console_parse.append(&scratch_raw[..n]);
    let r = kv_parse(console_parse.as_slice(), scratch_pairs);
    for pair in &scratch_pairs[..r.n] {
        let key = pair.key.as_str();
        let value = pair.value.as_str();
        // `prev` is OWNED (`PrevValue`), so it stays valid across the
        // auto_proxy call by construction. The C++ needs a caller-supplied
        // buffer plus a lifetime contract enforced by a test to get here.
        let prev = key_cache.exchange(key, value);
        let result = mode.auto_proxy_on_console_change(key, prev.as_str(), value);
        if result.emulate_stopped {
            // Console button pressed while emulating: the user takes over.
            // Immediate stop is never less safe than staying in Emulate.
            //
            // NOTE: `emergency:console_takeover` does NOT exist in
            // safety_model.py — it is a firmware-only reason token that QEMU
            // scenario S4 asserts by exact string (and asserts is NOT a
            // latched fault). Documented divergence; carried verbatim.
            //
            // TODO(M3): use the gap-safe normal exit when the console is
            // healthy and an owner is present.
            *executor_inhibited = true;
            controller.emergency_stop("console_takeover", now);
        }
    }
    console_parse.consume(r.consumed);
    // WEDGE RECOVERY (see safety_core::parse_buf). An unterminated `[` pins
    // `consumed` at 0 forever; without this the buffer fills, `append` starts
    // dropping every new byte, and this KV path — which is the ONLY feed for
    // the console-takeover interlock — dies silently for the rest of the
    // power cycle. The controller's own console scanner would keep refreshing
    // freshness, so the machine would stay in Emulate with a dead interlock.
    if console_parse.resync_if_wedged(r.consumed).recovered {
        // Not an emergency: dropping unparseable garbage is the correct,
        // conservative action and the bridge is unaffected. It IS worth an
        // audit line, because on a healthy bus it should never happen.
        controller.note_console_parse_resync();
    }
}

/// Drain the motor tap: byte counter plus KV parse (cached for the future
/// status surface; the safety core itself does not consume motor KV).
fn drain_motor(g: &mut Guarded) {
    let Guarded {
        motor_tap,
        scratch_raw,
        scratch_pairs,
        motor_parse,
        mode,
        ..
    } = g;
    let n = motor_tap.read(scratch_raw);
    if n == 0 {
        return;
    }
    mode.add_motor_bytes(n as u32);
    motor_parse.append(&scratch_raw[..n]);
    let r = kv_parse(motor_parse.as_slice(), scratch_pairs);
    motor_parse.consume(r.consumed);
    // Same wedge, same fix. Nothing safety-critical reads the motor tap today,
    // but leaving one of two identical buffers wedge-prone is how the defect
    // comes back when the status surface starts consuming it (M5).
    motor_parse.resync_if_wedged(r.consumed);
}

pub fn run(ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        // PLAN normative WDT matrix: this task MUST be supervised. Refuse to
        // run unsupervised — abort -> panic -> silent reboot -> GPIO21 Hi-Z
        // -> R23 pull-down -> relay released (fail loud).
        wdt::abort(c"serial_engine: task WDT subscribe failed");
    }
    logi!("serial_engine task started (WDT-supervised)");

    // VBUS_PRESENT_N edge detector. `set_vbus_present_n` logs an audit event
    // per call; calling it every 5 ms would push 200 events/s and wrap the
    // 256-slot audit ring in ~1.3 s, EVICTING emergency and fault events.
    // `None` forces one call on the first sample to establish the real level.
    let mut last_vbus_level_n: Option<bool> = None;

    loop {
        wdt::feed();
        {
            let mut g = lock(&ctx.guarded);
            let now = ctx.clock.now();

            // Hardware permission + relay feedback samples FIRST.
            let tread = g.io.tread_ok();
            g.controller.set_tread_ok(tread, now);
            let (nc, no) = (g.io.k1_nc_high(), g.io.k1_no_high());
            g.controller.observe_relay_feedback(nc, no, now);

            let vbus_level_n = !g.io.vbus_present().get();
            if last_vbus_level_n != Some(vbus_level_n) {
                g.controller.set_vbus_present_n(vbus_level_n);
                last_vbus_level_n = Some(vbus_level_n);
            }

            // Drain both taps.
            drain_console(&mut g, now);
            drain_motor(&mut g);

            // Console inter-frame gap qualification for relay transfers.
            // TODO(M2): GAP_QUALIFY_US is a placeholder pending bench capture
            // qualification. Changing it silently retunes S1-S7.
            let now = ctx.clock.now();
            let m = g.controller.mode();
            // PLAN normal exit is ORDERED: step 1 transmits and finishes a
            // complete zero frame, step 2 waits for the gap, step 3 opens K1.
            // Qualifying the gap while the zero frame is still owed would run
            // step 3 before step 1 and leave the motor's last command at the
            // owner's speed as the bridge returns to copper. The emulate task
            // discharges the obligation; this is the interlock that keeps the
            // order. It gates ONLY this voluntary observation — the 1 s
            // exit-gap deadline in `enforce_due_safety` still fires on time
            // and opens K1 without it (N19), so a stuck writer cannot hold the
            // relay closed.
            let exit_frame_owed = m == SafeMode::ExitWaitGap && g.controller.exit_zero_frame_owed();
            if (m == SafeMode::EntryWaitGap || m == SafeMode::ExitWaitGap)
                && !exit_frame_owed
                && now - g.last_console_rx >= GAP_QUALIFY_US
            {
                g.controller.observe_interframe_gap(now);
            }

            g.controller.tick(now);
            g.apply_outputs();

            // Relay transfer in flight: run the dedicated sub-ms window until
            // the controller either qualifies the transfer or fails it closed
            // at its own 10 ms deadline. Bounded well under the 2 s task WDT.
            if in_feedback_wait(&g.controller) {
                let Guarded {
                    controller, io, ..
                } = &mut *g;
                let mut wio = WindowIo {
                    io,
                    clock: &ctx.clock,
                    delay: RomDelay::new(),
                };
                run_feedback_window(controller, &mut wio);
            }

            // Keep the cycle parameter engine consistent with the
            // authoritative safety controller.
            if g.controller.mode() != SafeMode::Emulating && g.mode.is_emulating() {
                g.mode.watchdog_reset_to_proxy();
            }
        }
        delay_ms(SERIAL_LOOP_MS);
    }
}
