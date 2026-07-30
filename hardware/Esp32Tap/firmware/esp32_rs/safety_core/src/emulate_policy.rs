//! `EmulateTaskPolicy` — the per-iteration decision logic of the emulate cycle
//! task, extracted so the host suite can test it and the FreeRTOS task can
//! execute it unchanged.
//!
//! Port of `components/portable_core/engine/emulate_task_policy.h`.
//!
//! PLAN entry step 6 (NORMATIVE): after gap-safe emulate entry, "only then
//! transmit the first complete zero frame". The `SafetyController` accepts
//! owner `command_motion` during `ENTRY_WAIT_*` (faithful to
//! `safety_model.py`), so by the time it reports EMULATING it may already hold
//! NONZERO owner motion. If the task mirrored that into the cycle engine in
//! the same iteration that arms the cycle, the FIRST transmitted burst
//! (burst 0 = inc,hmph) would carry nonzero motion — violating step 6.
//!
//! This policy defers the controller -> mode motion mirror until after the
//! first post-entry burst has ACTUALLY been transmitted; `enter_emulate`
//! zeroes the cycle engine, so that first burst encodes hmph=0/inc=0 by
//! construction. The model itself is not weakened.
//!
//! HONEST LIMIT: this is one of the three C++ defects Rust does NOT catch.
//! The first-frame-nonzero ordering bug is narrowed by the `&SafetyController`
//! mirror signature, the `SafetyTimeoutFired` token and (since the re-entry
//! fix below) the `EmulateSessionId` argument, but the actual property lives
//! here plus boot-envelope case 8 plus S3's `first14[0] == b"[inc:0]"`.
//!
//! DIVERGENCE FROM THE C++ FORK (deliberate, reported): the C++
//! `EmulateTaskPolicy` edge-detects on a bare `controller_emulating` BOOL.
//! That bool aliases two different emulate sessions, and a gap-safe normal
//! exit + re-acquire + second entry fits inside one 100 ms sample period, so
//! the arm edge is missed and the SECOND session's first transmitted burst
//! carries the owner's motion. Reproduced against the unmodified core; see
//! `EmulateSessionId`. This port takes an `Option<EmulateSessionId>` instead,
//! which cannot alias. The C++ core has the same hole and it is NOT fixed
//! there (that tree is reference/fallback and is not modified by this port) —
//! it is filed as a defect against the C++ firmware.

use crate::units::EmulateSessionId;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Decision {
    /// Controller finished gap-safe entry: arm the cycle engine at ZERO
    /// (`mode.request_emulate(true)` + `cycle.reset()`).
    pub arm: bool,
    /// Controller left EMULATING but the cycle engine still thinks it is
    /// emulating: force it back to proxy.
    pub force_proxy: bool,
    /// Safe to mirror owner-commanded motion into the cycle engine this
    /// iteration (false until the first zero burst actually went out).
    pub mirror: bool,
    /// Tick the cycle engine (transmit a burst) this iteration.
    pub send_burst: bool,
}

#[derive(Default)]
pub struct EmulateTaskPolicy {
    last_session: Option<EmulateSessionId>,
    entry_zero_pending: bool,
}

impl EmulateTaskPolicy {
    pub const fn new() -> Self {
        EmulateTaskPolicy {
            last_session: None,
            entry_zero_pending: false,
        }
    }

    /// One iteration's decisions.
    ///
    /// * `session` — `SafetyController::emulate_session()`: `Some(id)` exactly
    ///   while EMULATING, with a DIFFERENT id per entry. Taking the id rather
    ///   than a bool is what makes an exit-and-re-entry between two samples
    ///   observable; see the module note.
    /// * `mode_emulating` — the cycle parameter engine's PRE-ARM state.
    pub fn step(
        &mut self,
        session: Option<EmulateSessionId>,
        mode_emulating: bool,
    ) -> Decision {
        let mut d = Decision::default();
        match session {
            // A NEW session (first entry, or an exit + re-entry that happened
            // entirely between two samples) always re-arms.
            Some(id) if self.last_session != Some(id) => {
                d.arm = true;
                self.entry_zero_pending = true;
            }
            Some(_) => {}
            None => {
                if self.last_session.is_some() && mode_emulating {
                    d.force_proxy = true;
                }
            }
        }
        if session.is_none() {
            self.entry_zero_pending = false;
        } else {
            d.send_burst = true;
            d.mirror = !self.entry_zero_pending;
        }
        self.last_session = session;
        d
    }

    /// Call ONLY after the cycle engine reports a burst was ACTUALLY
    /// transmitted (`EmulationCycle::tick()` returned true). Until then the
    /// entry-zero gate stays closed and the mirror stays deferred.
    pub fn on_burst_sent(&mut self) {
        self.entry_zero_pending = false;
    }

    pub fn entry_zero_pending(&self) -> bool {
        self.entry_zero_pending
    }
}
