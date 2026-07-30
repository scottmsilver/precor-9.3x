//! The dedicated sub-millisecond relay-feedback sampling window.
//!
//! Port of `components/portable_core/safety/feedback_window.h`.
//!
//! PLAN gap-safe transition steps (entry 5 / exit 3) require the feedback pole
//! to report the expected contact state continuously for >= 1 ms, PROVEN BY AN
//! ACTUAL GPIO SAMPLE, all strictly before the 10 ms deadline. The serial
//! engine's 5 ms cadence cannot satisfy that: after `observe_interframe_gap`
//! arms the deadline mid-iteration, the next samples land at ~+5 ms and
//! ~+10 ms — the second one AT the fail-closed deadline, so every gap-safe
//! entry/exit would latch a feedback-timeout fault.
//!
//! Fix (task layer only; model semantics untouched): while the controller is
//! in `EntryWaitFeedback` or `ExitWaitFeedback`, poll at `FEEDBACK_POLL_US`
//! instead of returning to the 5 ms cadence. Every iteration FIRST applies the
//! controller's outputs (RELAY_CMD must be physically driven before its
//! feedback can move), THEN takes a real GPIO sample.
//!
//! # Boundedness (rewritten 2026-07-28)
//!
//! This loop spins with the relay ENERGIZED, holding the safety mutex and not
//! feeding the task WDT, so how it terminates matters. It used to be bounded
//! only by the controller's own deadline firing on a monotonic clock, with
//! "a stuck clock spins until the 2 s task WDT panics" as the sole fallback —
//! a recovery path nothing validated, and one that costs 2 s of the relay
//! staying closed.
//!
//! It is now bounded TWICE, and the second bound does not trust the clock:
//!
//!  1. Normally the controller's `RELAY_FEEDBACK_DEADLINE_US` (10 ms) fires
//!     and the transfer either qualifies or fails closed. At
//!     `FEEDBACK_POLL_US` = 200 µs that is ~50 iterations.
//!  2. [`MAX_WINDOW_POLLS`] iterations — 4x that budget — is a hard,
//!     clock-independent cap. Hitting it means the monotonic clock is not
//!     advancing (or the poll cadence is wrong), so the window stops trusting
//!     it and fails closed itself: `emergency_stop("feedback_window_stalled")`
//!     zeroes motion, releases RELAY_CMD and TX_ENABLE, and returns to Proxy.
//!
//! So the worst case is ~40 ms of mutex hold and relay-closed time on a
//! defective clock, deterministically, instead of 2 s and a reboot — and the
//! path is host-testable (`fork_extensions.rs`) instead of theoretical. The
//! task WDT remains behind it as the last resort, unfed on purpose: feeding it
//! from inside an unbounded spin is exactly how a stall becomes invisible.
//!
//! HONEST LIMIT: nothing in the type system stops an implementer from
//! returning to the 5 ms cadence during a transfer. That property is carried
//! ONLY by boot-envelope cases 9/10 (`SerialCadenceSim`) and by S3/S7a timing.
//!
//! HAL-free and generic so the host suite can drive it with a fake clock and
//! fake GPIO at the REAL task cadence.

use crate::safety::constants::{FEEDBACK_POLL_US, RELAY_FEEDBACK_DEADLINE_US};
use crate::safety::controller::{OutputIntent, SafeMode, SafetyController};
use crate::units::{Micros, NcHigh, NoHigh, TreadOk};

pub fn in_feedback_wait(controller: &SafetyController) -> bool {
    matches!(
        controller.mode(),
        SafeMode::EntryWaitFeedback | SafeMode::ExitWaitFeedback
    )
}

/// Everything the window needs, behind ONE handle.
///
/// The C++ version takes five independent lambdas, and in the host cadence
/// simulator all five capture the same `Bridge&` — so `apply_fn` MUTATES the
/// same `FakeSafetyIo` that `nc_fn`/`no_fn` READ, concurrently and unchecked.
/// Rust rejects that outright (E0500), which is why the seam is a single
/// `&mut impl FeedbackWindowIo` here: the aliasing is resolved by construction
/// instead of by convention. Found while porting; a genuine (if benign here)
/// structural improvement.
pub trait FeedbackWindowIo {
    /// Monotonic microseconds.
    fn now(&self) -> Micros;
    /// Real GPIO read of the K1 NC feedback pole.
    fn nc(&self) -> NcHigh;
    /// Real GPIO read of the K1 NO feedback pole.
    fn no(&self) -> NoHigh;
    /// Real GPIO read of the TREAD_OK hardware-permission input.
    ///
    /// DIVERGENCE FROM THE C++ WINDOW (deliberate, an improvement): the C++
    /// window samples ONLY the two feedback poles, and
    /// `enforce_due_safety` tests the CACHED `tread_ok_`. A TREAD_OK drop
    /// during a relay transfer was therefore invisible to firmware for up
    /// to the whole ~10 ms window plus the next 5 ms coarse iteration,
    /// against a PLAN bench gate of "TREAD_OK fault to stable NC at most
    /// 10 ms" — i.e. the gate was carried entirely by the U6 hardware AND
    /// gate, with no software margin at all. One GPIO read per 200 us
    /// buys that margin back.
    fn tread_ok(&self) -> TreadOk;
    /// Push the controller's relay/tx outputs to hardware.
    fn apply(&mut self, intent: OutputIntent);
    /// Wait ~`FEEDBACK_POLL_US` (on the host, advance the fake clock so the
    /// controller's deadline can fire).
    fn delay(&mut self);
}

/// Clock-independent iteration cap for the poll window.
///
/// `RELAY_FEEDBACK_DEADLINE_US / FEEDBACK_POLL_US` = 50 polls covers the whole
/// normative window; this is 4x that. Reaching it is not a timing overrun, it
/// is evidence the monotonic clock is not advancing — the one condition under
/// which the controller's own deadline can never fire.
pub const MAX_WINDOW_POLLS: u32 = 4 * (RELAY_FEEDBACK_DEADLINE_US.get() / FEEDBACK_POLL_US.get()) as u32;

/// Run the bounded poll window.
pub fn run_feedback_window<I: FeedbackWindowIo>(controller: &mut SafetyController, io: &mut I) {
    let mut polls: u32 = 0;
    while in_feedback_wait(controller) {
        if polls >= MAX_WINDOW_POLLS {
            // The clock is not advancing, so waiting for the controller's
            // deadline is waiting forever WITH THE RELAY CLOSED. Fail closed
            // here instead: this releases RELAY_CMD and TX_ENABLE, zeroes
            // motion and returns to Proxy.
            //
            // Rust-only task-layer guard: `safety_model.py` models no task
            // cadence and the C++ window has no equivalent, so this reason
            // string has no model twin and is not differentially compared.
            controller.emergency_stop("feedback_window_stalled", io.now());
            break;
        }
        polls += 1;
        // Drive RELAY_CMD/TX_ENABLE FIRST: the relay must be commanded before
        // its dry-contact feedback can reach the expected state.
        io.apply(controller.output_intent());
        let t = io.now();
        // Hardware permission FIRST, exactly as the 5 ms coarse loop does —
        // a TREAD_OK drop mid-transfer must not wait out the window. Calling
        // it with an unchanged `true` is a no-op (no event, no state change),
        // so this costs one GPIO read per poll.
        controller.set_tread_ok(io.tread_ok(), t);
        if !in_feedback_wait(controller) {
            break;
        }
        let (n, o) = (io.nc(), io.no());
        controller.observe_relay_feedback(n, o, t);
        if !in_feedback_wait(controller) {
            break;
        }
        io.delay();
    }
    // Transfer finished (qualified or failed closed) — apply the final output
    // state before returning to the coarse cadence.
    io.apply(controller.output_intent());
}
