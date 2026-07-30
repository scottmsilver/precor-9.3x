//! Newtyped units. Every one is `#[repr(transparent)]` + `Copy`, and NONE of
//! them has a blanket `From<i64>`/`From<i32>` — the only way to build one is
//! through a named constructor, so a raw integer cannot silently become the
//! wrong quantity.
//!
//! Unit confusion is a live risk in this codebase: the C++ `ModeStateMachine`
//! carries `speed_tenths_` and `speed_raw_` (= tenths × 10) side by side as two
//! bare `int`s, and the wire encoder takes the *hundredths* one. Here
//! `SpeedHundredths` is obtainable ONLY via `SpeedTenths::to_hundredths()`, so
//! the ×10 can be neither skipped nor applied twice.

use core::ops::{Add, Sub};

// --- motion ---------------------------------------------------------------

/// Speed in tenths of mph. The internal/API unit.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default, Hash)]
pub struct SpeedTenths(i32);

impl SpeedTenths {
    /// `MAX_SPEED_TENTHS` / `SPEED_MAX_TENTHS` = 120 (12.0 mph).
    pub const MAX: SpeedTenths = SpeedTenths(120);
    pub const ZERO: SpeedTenths = SpeedTenths(0);

    pub const fn new(tenths: i32) -> Self {
        SpeedTenths(tenths)
    }
    pub const fn get(self) -> i32 {
        self.0
    }
    /// The ONLY way to reach the wire unit. Cannot be skipped or doubled.
    ///
    /// DELIBERATE, DOCUMENTED DIVERGENCE from the C++ (`kv_protocol.cpp:92`),
    /// which computes `tenths * 10` in `int`. For `|tenths| > i32::MAX / 10`
    /// that is SIGNED OVERFLOW — undefined behaviour, flagged by UBSan, with a
    /// result that differs between -O0 and -O2. Plain `*` here would be no
    /// better: it panics in debug and wraps in release.
    ///
    /// Saturating is the correct side and is chosen on purpose. It is
    /// monotonic, total, and cannot produce a NEGATIVE (i.e. small, or
    /// sign-flipped) wire value from a huge input — wrapping can, which is the
    /// only outcome here that could ever be dangerous. Unreachable through the
    /// clamped API (motion is clamped to 0..=120 long before this), so this
    /// governs only the fuzzed boundary domain the differential explores; it
    /// is asserted, not merely asserted-about, in
    /// `r1_reviewer_independent.rs::r1_codec_wide_domain_matches_cpp`.
    pub const fn to_hundredths(self) -> SpeedHundredths {
        SpeedHundredths(self.0.saturating_mul(10))
    }
    pub fn clamped(self) -> Self {
        SpeedTenths(self.0.clamp(0, Self::MAX.0))
    }
    pub const fn is_zero(self) -> bool {
        self.0 == 0
    }
}

/// Speed as mph × 100 — the WIRE unit (`hmph`). Reachable only from
/// `SpeedTenths::to_hundredths`.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default)]
pub struct SpeedHundredths(i32);

impl SpeedHundredths {
    pub const fn get(self) -> i32 {
        self.0
    }
}

/// mph as the user/API sees it. The only float in the crate; converted at the
/// boundary by `ModeStateMachine::set_speed_mph`.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, PartialOrd, Debug, Default)]
pub struct Mph(pub f64);

impl Mph {
    /// C++ parity: `static_cast<int>(mph * 10 + 0.5)` (truncating, not
    /// `round`: negative inputs behave the same as the original).
    pub fn to_tenths(self) -> SpeedTenths {
        SpeedTenths((self.0 * 10.0 + 0.5) as i32)
    }
}

/// Incline in half-percent units (1 = 0.5%).
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default, Hash)]
pub struct InclineHalfPct(i32);

impl InclineHalfPct {
    /// Application clamp, enforced on-MCU (`INCLINE_APP_MAX_HALF`) = 30 (15%).
    pub const APP_MAX: InclineHalfPct = InclineHalfPct(30);
    /// Absolute hardware guard (`INCLINE_ABS_MAX_HALF` / `MAX_INCLINE`) = 198.
    pub const ABS_MAX: InclineHalfPct = InclineHalfPct(198);
    pub const ZERO: InclineHalfPct = InclineHalfPct(0);

    pub const fn new(half_pct: i32) -> Self {
        InclineHalfPct(half_pct)
    }
    pub const fn get(self) -> i32 {
        self.0
    }
    /// Clamp to the ABSOLUTE guard (the cycle-engine clamp — `MAX_INCLINE`).
    pub fn clamped_abs(self) -> Self {
        InclineHalfPct(self.0.clamp(0, Self::ABS_MAX.0))
    }
    pub const fn is_zero(self) -> bool {
        self.0 == 0
    }
}

// --- time -----------------------------------------------------------------

/// Monotonic microseconds. The ONLY time type in the controller.
///
/// `Sub` yields another `Micros` (a delta) rather than a separate `Duration`
/// newtype: the C++/model arithmetic is plain int64 µs and the 57 vectors read
/// far more clearly this way. Deliberate, and it buys no safety to split.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default, Hash)]
pub struct Micros(i64);

impl Micros {
    pub const ZERO: Micros = Micros(0);
    pub const fn new(us: i64) -> Self {
        Micros(us)
    }
    pub const fn get(self) -> i64 {
        self.0
    }
    pub const fn from_millis(ms: i64) -> Self {
        Micros(ms * 1_000)
    }
    pub const fn from_secs(s: i64) -> Self {
        Micros(s * 1_000_000)
    }
    pub const fn saturating_add(self, other: Micros) -> Micros {
        Micros(self.0.saturating_add(other.0))
    }
}

impl Add for Micros {
    type Output = Micros;
    fn add(self, rhs: Micros) -> Micros {
        Micros(self.0 + rhs.0)
    }
}
impl Sub for Micros {
    type Output = Micros;
    fn sub(self, rhs: Micros) -> Micros {
        Micros(self.0 - rhs.0)
    }
}

/// Milliseconds — exists ONLY at `vTaskDelay`/`pdMS_TO_TICKS` boundaries.
/// One-way: there is deliberately no `Micros -> Millis`.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default)]
pub struct Millis(i64);

impl Millis {
    pub const fn new(ms: i64) -> Self {
        Millis(ms)
    }
    pub const fn get(self) -> i64 {
        self.0
    }
    pub const fn to_micros(self) -> Micros {
        Micros(self.0 * 1_000)
    }
}

// --- lease identity -------------------------------------------------------

/// Connection handle. PLAN D5 phase-1 stand-in for a WSS socket / BLE
/// conn_handle; stays `i32` for exact parity with the C++ `int32_t`.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Default, Hash)]
pub struct Handle(pub i32);

/// Non-reusable connection generation. `new` REJECTS negatives, so the C++
/// `connection_rejected:invalid_identity` state is unrepresentable in a
/// well-typed `ConnectionIdentity`.
///
/// The rejecting behaviour is still reachable through
/// `SafetyController::connect_raw`, the untrusted-boundary form, which is
/// covered by the Rust-only vector `connect_raw_rejects_a_negative_generation`
/// (the C++ has no separate boundary form — it validates inside `connect`
/// itself, so there is no C++ case to be 1:1 with).
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Hash)]
pub struct Generation(i64);

impl Generation {
    pub const fn new(gen: i64) -> Option<Generation> {
        if gen < 0 {
            None
        } else {
            Some(Generation(gen))
        }
    }
    pub const fn get(self) -> i64 {
        self.0
    }
}

impl Default for Generation {
    fn default() -> Self {
        Generation(0)
    }
}

// --- emulate session ------------------------------------------------------

/// Identifies ONE emulate session — incremented by `SafetyController` on every
/// Proxy -> Emulating transition.
///
/// This type exists because a `bool` "is the controller emulating?" ALIASES
/// two different sessions. The emulate task samples the controller once per
/// 100 ms, and a gap-safe normal exit followed by a re-acquire and a second
/// entry needs only ~30 ms of console silence (20 ms exit gap + ~1.2 ms exit
/// feedback, after which the entry gap is ALREADY satisfied, + ~1.2 ms entry
/// feedback) — comfortably inside one sample period, and the console's own
/// ~100 ms inter-burst gaps make that silence normal. A bool sampled at both
/// ends reads `true, true`, so the rising edge that arms the cycle engine is
/// never observed: `cycle.reset()` does not run, the entry-zero gate is not
/// re-closed, and the first frames on the wire after the SECOND relay
/// transfer carry the owner's motion — violating PLAN entry step 6 ("only
/// then transmit the first complete zero frame").
///
/// Comparing session ids instead cannot alias: `Some(1) != Some(2)`.
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, Hash)]
pub struct EmulateSessionId(pub u32);

impl EmulateSessionId {
    /// Wrapping is deliberate and harmless: only INEQUALITY is ever tested,
    /// and a wrap needs 2^32 relay transfers.
    pub const fn next(self) -> Self {
        EmulateSessionId(self.0.wrapping_add(1))
    }
}

// --- GPIO-level newtypes --------------------------------------------------
//
// `Feedback::from_gpio(NcHigh, NoHigh)` cannot take its two `bool`s in the
// wrong order: swapping them is exactly the silent inversion that would fail
// closed in the WRONG direction (BYPASS read as EMULATE).

macro_rules! bool_newtype {
    ($(#[$m:meta])* $name:ident) => {
        $(#[$m])*
        #[repr(transparent)]
        #[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
        pub struct $name(pub bool);
        impl $name {
            pub const fn get(self) -> bool { self.0 }
        }
    };
}

bool_newtype!(
    /// K1 pole-B NC feedback line level (10k pull-up: HIGH means contact OPEN).
    NcHigh
);
bool_newtype!(
    /// K1 pole-B NO feedback line level (10k pull-up: HIGH means contact OPEN).
    NoHigh
);
bool_newtype!(
    /// TREAD_OK hardware permission (already de-inverted by the HAL).
    TreadOk
);
bool_newtype!(
    /// VBUS presence, POST-inversion. GPIO7 is active-low; the inversion
    /// happens exactly once, in the HAL.
    VbusPresent
);
bool_newtype!(
    /// K1 coil command intent.
    RelayCmd
);
bool_newtype!(
    /// RS-485 driver enable intent.
    TxEnable
);
