//! `EmulationCycle` — the 14-key synthesized console cycle and the 3-hour
//! no-change safety timeout.
//!
//! Port of `components/portable_core/engine/emulation_cycle.h`.
//!
//! DIVERGENCES, both reported:
//!
//! 1. **No `std::string` on the cycle path.** The C++ `value_for()` returns
//!    `std::string` and `SerialWriter::write_kv` calls `kv_build`, which
//!    returns another — TWO heap allocations per keyed slot, on a 100 ms
//!    cadence, forever. Here `value_for` returns a `HexStr` and the sink takes
//!    `&str`; the crate cannot name `String`.
//! 2. **The `on_kv_event` `std::function` is gone.** The [`KvSink`] the cycle
//!    writes through IS the observation point, so tests record from the sink
//!    instead of from a second callback.
//!
//! Borrow structure also differs: the C++ object holds references to the
//! writer and the mode machine for its whole life. Here `tick` takes both as
//! parameters, so the cycle never co-borrows state the caller also needs.

use crate::kv::{encode_incline_hex, encode_speed_hex, HexStr};
use crate::mode::{ModeStateMachine, StateSnapshot};
use crate::safety::controller::SafetyTimeoutFired;
use crate::units::Micros;

/// 3-hour no-change timeout.
pub const EMU_TIMEOUT_SEC: i64 = 3 * 3600;
pub const EMU_TIMEOUT_US: Micros = Micros::new(EMU_TIMEOUT_SEC * 1_000_000);
/// Inter-burst gap; OWNED BY THE CALLER (the task sleeps it, not the cycle).
pub const EMU_BURST_GAP_MS: i64 = 100;

#[derive(Clone, Copy)]
pub struct KvCycleEntry {
    pub key: &'static str,
    /// true = dynamic value, false = bare `[key]` query form.
    pub has_value: bool,
}

/// The 14-key cycle, in order.
pub const KV_CYCLE: [KvCycleEntry; 14] = [
    KvCycleEntry { key: "inc", has_value: true },      //  0 incline (half-pct hex)
    KvCycleEntry { key: "hmph", has_value: true },     //  1 speed (mph*100 hex)
    KvCycleEntry { key: "amps", has_value: false },    //  2
    KvCycleEntry { key: "err", has_value: false },     //  3
    KvCycleEntry { key: "belt", has_value: false },    //  4
    KvCycleEntry { key: "vbus", has_value: false },    //  5
    KvCycleEntry { key: "lift", has_value: false },    //  6
    KvCycleEntry { key: "lfts", has_value: false },    //  7
    KvCycleEntry { key: "lftg", has_value: false },    //  8
    KvCycleEntry { key: "part", has_value: true },     //  9 always "6"
    KvCycleEntry { key: "ver", has_value: false },     // 10
    KvCycleEntry { key: "type", has_value: false },    // 11
    KvCycleEntry { key: "diag", has_value: true },     // 12 always "0"
    KvCycleEntry { key: "loop", has_value: true },     // 13 always "5550"
];

/// Which `KV_CYCLE` indices belong to each burst (-1 = end of burst).
pub const BURSTS: [[i32; 4]; 5] = [
    [0, 1, -1, -1],  // inc, hmph
    [2, 3, 4, -1],   // amps, err, belt
    [5, 6, 7, 8],    // vbus, lift, lfts, lftg
    [9, 10, 11, -1], // part, ver, type
    [12, 13, -1, -1],// diag, loop
];

/// Where the cycle writes. Replaces both `SerialWriter<Port>` and the
/// `on_kv_event` callback.
pub trait KvSink {
    fn write_kv(&mut self, key: &str, value: &str);
}

pub struct EmulationCycle {
    burst: usize,
    last_activity_us: Micros,
    /// `-1` sentinel on first tick, exactly as C++ (`prev_speed_ = -1`), so
    /// the very first observation always re-arms the timer.
    prev_speed: i32,
    prev_incline: i32,
    timeout_fired: bool,
}

impl Default for EmulationCycle {
    fn default() -> Self {
        Self::new()
    }
}

impl EmulationCycle {
    pub const fn new() -> Self {
        EmulationCycle {
            burst: 0,
            last_activity_us: Micros::ZERO,
            prev_speed: -1,
            prev_incline: -1,
            timeout_fired: false,
        }
    }

    /// Re-arm the cycle and the 3-hour timer. Call when emulate starts.
    pub fn reset(&mut self, now: Micros) {
        self.burst = 0;
        self.last_activity_us = now;
        self.prev_speed = -1;
        self.prev_incline = -1;
        self.timeout_fired = false;
    }

    /// Returns `Some(SafetyTimeoutFired)` EXACTLY ONCE after the 3-hour
    /// timeout zeroed the mode machine, so the owning task can also zero the
    /// authoritative controller (which this engine knows nothing about).
    ///
    /// The token is the whole point: `SafetyController::safety_timeout_zero_motion`
    /// will not accept anything else, so the back-mirror cannot be invoked
    /// without having observed the timeout.
    pub fn consume_safety_timeout(&mut self) -> Option<SafetyTimeoutFired> {
        if self.timeout_fired {
            self.timeout_fired = false;
            Some(SafetyTimeoutFired::new())
        } else {
            None
        }
    }

    /// Send one burst if emulating. The CALLER sleeps `EMU_BURST_GAP_MS`
    /// between calls. Returns true if a burst was sent.
    ///
    /// Hot path: no allocation.
    pub fn tick<S: KvSink>(
        &mut self,
        now: Micros,
        mode: &mut ModeStateMachine,
        sink: &mut S,
    ) -> bool {
        if !mode.is_emulating() {
            self.burst = 0;
            return false;
        }

        // Re-arm the 3-hour timer whenever speed or incline changed.
        let snap_check = mode.snapshot();
        if snap_check.speed_tenths.get() != self.prev_speed
            || snap_check.incline.get() != self.prev_incline
        {
            self.last_activity_us = now;
            self.prev_speed = snap_check.speed_tenths.get();
            self.prev_incline = snap_check.incline.get();
        }

        // Timeout: zero motion after 3 hours with no changes. Fires only when
        // motion is actually nonzero.
        if now - self.last_activity_us >= EMU_TIMEOUT_US
            && (!snap_check.speed_tenths.is_zero() || !snap_check.incline.is_zero())
        {
            mode.safety_timeout_reset();
            self.timeout_fired = true;
        }

        // Snapshot is taken PER BURST, not per 5-burst cycle.
        let snap = mode.snapshot();

        for slot in 0..4 {
            let idx = BURSTS[self.burst][slot];
            if idx < 0 {
                break;
            }
            // Re-check between slots inside a burst.
            if !mode.is_emulating() {
                return false;
            }
            let idx = idx as usize;
            let entry = KV_CYCLE[idx];
            if entry.has_value {
                let v = Self::value_for(idx, &snap);
                sink.write_kv(entry.key, v.as_str());
            } else {
                sink.write_kv(entry.key, "");
            }
        }
        self.burst = (self.burst + 1) % 5;
        true
    }

    /// Write one COMPLETE ZERO FRAME: the motion pair, both at zero.
    ///
    /// PLAN normal exit, step 1: "transmit and finish a complete zero frame".
    /// Shape is burst 0 of the ordinary cycle (`BURSTS[0]` = inc, hmph), so
    /// the bytes on the wire are exactly what the motor already sees every
    /// cycle — `[inc:0]\xff[hmph:0]\xff` — with the values forced to zero
    /// rather than read from the mode machine. Reading the mode machine would
    /// be wrong here: it may still hold the owner's last commanded motion, and
    /// the whole point of step 1 is to leave zero as the last thing the motor
    /// was told before the bridge returns to copper.
    ///
    /// Deliberately an ASSOCIATED function, not a method: it must be callable
    /// during exit, when the cycle is no longer emulating and `tick` would
    /// (correctly) refuse to send anything.
    pub fn write_zero_frame<S: KvSink>(sink: &mut S) {
        for slot in 0..4 {
            let idx = BURSTS[0][slot];
            if idx < 0 {
                break;
            }
            let entry = KV_CYCLE[idx as usize];
            // Both keys in burst 0 are the motion keys and both carry values.
            debug_assert!(entry.has_value);
            sink.write_kv(entry.key, "0");
        }
    }

    fn value_for(idx: usize, snap: &StateSnapshot) -> DynValue {
        match idx {
            0 => DynValue::Hex(encode_incline_hex(snap.incline)),
            1 => DynValue::Hex(encode_speed_hex(snap.speed_tenths)),
            9 => DynValue::Static("6"),
            12 => DynValue::Static("0"),
            13 => DynValue::Static("5550"),
            _ => DynValue::Static(""),
        }
    }
}

/// A cycle slot's value: either a freshly encoded hex string or a static
/// literal. Both are `Copy` and neither allocates.
enum DynValue {
    Hex(HexStr),
    Static(&'static str),
}

impl DynValue {
    fn as_str(&self) -> &str {
        match self {
            DynValue::Hex(h) => h.as_str(),
            DynValue::Static(s) => s,
        }
    }
}
