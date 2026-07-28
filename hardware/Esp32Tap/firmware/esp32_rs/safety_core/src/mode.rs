//! `ModeStateMachine` — the cycle-parameter engine (Pi-parity).
//!
//! Port of `components/portable_core/engine/mode_state.{h,cpp}`.
//!
//! TWO DELIBERATE DIVERGENCES, both reported:
//!
//! 1. **No internal mutex, no `alignas(64) AtomicSnap`.** The C++ class locks
//!    internally and publishes a lock-free atomic snapshot for the data plane.
//!    Here every mutator takes `&mut self`, so the type lives inside the
//!    firmware's single outer `Mutex<Guarded>` and the borrow checker enforces
//!    exclusion. The "lock-free snapshot" property is GONE; nothing asserts it,
//!    and the QT shim reads `console_bytes()`/`motor_bytes()` under a lock it
//!    already holds.
//!
//! 2. **The `std::function` emulate callback becomes the return value.** The
//!    callback is set only by host tests — `grep set_emulate_callback main/`
//!    is empty in the C++ firmware — and `TransitionResult` already carries
//!    exactly `start`/`stop` at exactly the same call site. Dropping it
//!    removes a heap-allocating `std::function` from the safety core and
//!    preserves every assertion, including the two that assert the callback
//!    does NOT fire (`watchdog_reset_to_proxy` returns a `TransitionResult`
//!    with both emulate flags clear, by construction).
//!
//! LOAD-BEARING QUIRK: `request_proxy(true)` reports `changed == true` even
//! when already in Proxy (case 1.2/2). Pi-parity behaviour, not a considered
//! invariant — do not "fix" it.

use crate::units::{InclineHalfPct, Mph, SpeedHundredths, SpeedTenths};

/// `MAX_SPEED_TENTHS` — mirrored in `python/treadmill_client.py`.
///
/// SINGLE SOURCE OF TRUTH (corrected 2026-07-28): these two were independent
/// literals duplicating `units::SpeedTenths::MAX` / `InclineHalfPct::ABS_MAX`,
/// which are what the clamps in `set_speed`/`set_incline` actually apply. Two
/// numbers, one of them dead, is how a clamp silently stops matching the
/// constant that documents it. They are now aliases of the clamping values.
pub const MAX_SPEED_TENTHS: i32 = SpeedTenths::MAX.get();
/// `MAX_INCLINE` — 99% in half-pct units. The ABSOLUTE hardware guard.
pub const MAX_INCLINE: i32 = InclineHalfPct::ABS_MAX.get();

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    /// Neither proxy nor emulate active.
    Idle,
    /// Forwarding console commands to the motor.
    Proxy,
    /// Sending the synthesized cycle to the motor.
    Emulating,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct StateSnapshot {
    pub mode: Mode,
    pub speed_tenths: SpeedTenths,
    /// `speed_tenths * 10` — the wire (hundredths) unit.
    pub speed_raw: SpeedHundredths,
    pub incline: InclineHalfPct,
    pub proxy_enabled: bool,
    pub emulate_enabled: bool,
}

/// What a transition did. Replaces both the C++ struct AND its
/// `std::function` emulate callback.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct TransitionResult {
    pub changed: bool,
    pub emulate_started: bool,
    pub emulate_stopped: bool,
}

pub struct ModeStateMachine {
    mode: Mode,
    speed_tenths: SpeedTenths,
    incline: InclineHalfPct,
    console_bytes: u32,
    motor_bytes: u32,
}

impl ModeStateMachine {
    pub const fn new() -> Self {
        ModeStateMachine {
            mode: Mode::Proxy,
            speed_tenths: SpeedTenths::ZERO,
            incline: InclineHalfPct::ZERO,
            console_bytes: 0,
            motor_bytes: 0,
        }
    }

    /// Safety: emulate ALWAYS starts at zero speed and zero incline.
    fn enter_emulate(&mut self) {
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline = InclineHalfPct::ZERO;
        self.mode = Mode::Emulating;
    }

    fn exit_emulate(&mut self) {
        self.mode = Mode::Idle;
    }

    pub fn request_proxy(&mut self, enabled: bool) -> TransitionResult {
        let mut r = TransitionResult::default();
        if enabled {
            if self.mode == Mode::Emulating {
                self.exit_emulate();
                r.emulate_stopped = true;
            }
            self.mode = Mode::Proxy;
            // Unconditionally true, even when already Proxy — case 1.2/2.
            r.changed = true;
        } else if self.mode == Mode::Proxy {
            self.mode = Mode::Idle;
            r.changed = true;
        }
        r
    }

    pub fn request_emulate(&mut self, enabled: bool) -> TransitionResult {
        let mut r = TransitionResult::default();
        if enabled {
            if self.mode == Mode::Emulating {
                return r; // already emulating — no-op
            }
            self.mode = Mode::Idle; // clear proxy first
            self.enter_emulate();
            r.emulate_started = true;
            r.changed = true;
        } else if self.mode == Mode::Emulating {
            self.exit_emulate();
            r.emulate_stopped = true;
            r.changed = true;
        }
        r
    }

    /// Set speed, auto-enabling emulate. Clamped to `0..=MAX_SPEED_TENTHS`.
    /// The clamp happens BEFORE the entry-zeroing, and the write happens
    /// AFTER, exactly as in C++ (case 1.2/8 asserts speed survives entry).
    pub fn set_speed(&mut self, tenths: SpeedTenths) -> TransitionResult {
        let tenths = tenths.clamped();
        let mut r = TransitionResult::default();
        if self.mode != Mode::Emulating {
            self.mode = Mode::Idle;
            self.enter_emulate();
            r.emulate_started = true;
            r.changed = true;
        }
        self.speed_tenths = tenths;
        r
    }

    pub fn set_speed_mph(&mut self, mph: Mph) -> TransitionResult {
        self.set_speed(mph.to_tenths())
    }

    /// Set incline in half-pct, auto-enabling emulate. Clamped to the
    /// ABSOLUTE guard `0..=MAX_INCLINE` (198) — the 0..=30 application clamp
    /// lives in `SafetyController::command_motion`, not here.
    pub fn set_incline(&mut self, half_pct: InclineHalfPct) -> TransitionResult {
        let half_pct = half_pct.clamped_abs();
        let mut r = TransitionResult::default();
        if self.mode != Mode::Emulating {
            self.mode = Mode::Idle;
            self.enter_emulate();
            r.emulate_started = true;
            r.changed = true;
        }
        self.incline = half_pct;
        r
    }

    /// Console takeover: a changed `hmph`/`inc` value while emulating drops
    /// back to proxy. No change, a first-ever value (empty `old`), or an
    /// untracked key is a no-op.
    pub fn auto_proxy_on_console_change(
        &mut self,
        key: &str,
        old_val: &str,
        new_val: &str,
    ) -> TransitionResult {
        let mut r = TransitionResult::default();
        if old_val.is_empty() || old_val == new_val {
            return r;
        }
        if key != "hmph" && key != "inc" {
            return r;
        }
        if self.mode != Mode::Emulating {
            return r;
        }
        self.exit_emulate();
        r.emulate_stopped = true;
        self.mode = Mode::Proxy;
        r.changed = true;
        r
    }

    /// 3-hour safety timeout: zero motion, leave the mode alone.
    pub fn safety_timeout_reset(&mut self) {
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline = InclineHalfPct::ZERO;
    }

    /// Watchdog reset: zero motion AND return to proxy. Never reports an
    /// emulate transition (the C++ deliberately skips the callback so the
    /// emulate thread exits on its own).
    pub fn watchdog_reset_to_proxy(&mut self) -> TransitionResult {
        self.speed_tenths = SpeedTenths::ZERO;
        self.incline = InclineHalfPct::ZERO;
        self.mode = Mode::Proxy;
        TransitionResult::default()
    }

    pub fn add_console_bytes(&mut self, n: u32) {
        self.console_bytes = self.console_bytes.wrapping_add(n);
    }
    pub fn add_motor_bytes(&mut self, n: u32) {
        self.motor_bytes = self.motor_bytes.wrapping_add(n);
    }
    pub fn console_bytes(&self) -> u32 {
        self.console_bytes
    }
    pub fn motor_bytes(&self) -> u32 {
        self.motor_bytes
    }

    pub fn snapshot(&self) -> StateSnapshot {
        StateSnapshot {
            mode: self.mode,
            speed_tenths: self.speed_tenths,
            speed_raw: self.speed_tenths.to_hundredths(),
            incline: self.incline,
            proxy_enabled: self.mode == Mode::Proxy,
            emulate_enabled: self.mode == Mode::Emulating,
        }
    }

    pub fn is_proxy(&self) -> bool {
        self.mode == Mode::Proxy
    }
    pub fn is_emulating(&self) -> bool {
        self.mode == Mode::Emulating
    }
    pub fn speed_tenths(&self) -> SpeedTenths {
        self.speed_tenths
    }
    pub fn speed_raw(&self) -> SpeedHundredths {
        self.speed_tenths.to_hundredths()
    }
    pub fn incline(&self) -> InclineHalfPct {
        self.incline
    }
    pub fn mode(&self) -> Mode {
        self.mode
    }
}

impl Default for ModeStateMachine {
    fn default() -> Self {
        Self::new()
    }
}
