//! Port of `host/tests/test_mode_state.cpp` — 27 cases, 1:1 by name.
//!
//! The C++ `set_emulate_callback(std::function<void(bool)>)` has no Rust
//! counterpart: it is set only by host tests (the firmware never calls it),
//! and `TransitionResult` already reports `emulate_started`/`emulate_stopped`
//! at exactly the same call site. `CallbackLog` below folds the returned
//! results into the same counters the C++ lambdas maintained, so every
//! callback assertion — including the two that assert the callback does NOT
//! fire — is preserved verbatim.

use safety_core::mode::{Mode, ModeStateMachine, TransitionResult, MAX_INCLINE, MAX_SPEED_TENTHS};
use safety_core::units::{InclineHalfPct, Mph, SpeedTenths};

fn tenths(v: i32) -> SpeedTenths {
    SpeedTenths::new(v)
}
fn half(v: i32) -> InclineHalfPct {
    InclineHalfPct::new(v)
}

/// Stands in for the C++ `std::function` emulate callback: every
/// `TransitionResult` the machine returns is folded in, exactly where the C++
/// callback would have fired.
#[derive(Default)]
struct CallbackLog {
    count: i32,
    last_start: Option<bool>,
    started: bool,
    stopped: bool,
}

impl CallbackLog {
    fn observe(&mut self, r: TransitionResult) -> TransitionResult {
        if r.emulate_started {
            self.count += 1;
            self.last_start = Some(true);
            self.started = true;
        }
        if r.emulate_stopped {
            self.count += 1;
            self.last_start = Some(false);
            self.stopped = true;
        }
        r
    }
}

// cpp: "initial state is proxy mode"
#[test]
fn initial_state_is_proxy_mode() {
    let mode = ModeStateMachine::new();
    let snap = mode.snapshot();
    assert!(snap.proxy_enabled);
    assert!(!snap.emulate_enabled);
    assert_eq!(snap.speed_tenths, tenths(0));
    assert_eq!(snap.incline, half(0));
    assert_eq!(snap.mode, Mode::Proxy);
}

// ── Proxy transitions ───────────────────────────────────────────────

// cpp: "request proxy on (already on)"
#[test]
fn request_proxy_on_already_on() {
    let mut mode = ModeStateMachine::new();
    let result = mode.request_proxy(true);
    // LOAD-BEARING: unconditionally `changed`, even when already Proxy.
    assert!(result.changed);
    assert!(mode.is_proxy());
}

// cpp: "request proxy off"
#[test]
fn request_proxy_off() {
    let mut mode = ModeStateMachine::new();
    let result = mode.request_proxy(false);
    assert!(result.changed);
    let snap = mode.snapshot();
    assert!(!snap.proxy_enabled);
    assert_eq!(snap.mode, Mode::Idle);
}

// ── Emulate transitions ─────────────────────────────────────────────

// cpp: "enable emulate stops proxy"
#[test]
fn enable_emulate_stops_proxy() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    let result = cb.observe(mode.request_emulate(true));
    assert!(result.changed);
    assert!(result.emulate_started);
    assert_eq!(cb.last_start, Some(true));

    let snap = mode.snapshot();
    assert!(!snap.proxy_enabled);
    assert!(snap.emulate_enabled);
    // Safety: speed/incline zeroed on emulate start.
    assert_eq!(snap.speed_tenths, tenths(0));
    assert_eq!(snap.incline, half(0));
}

// cpp: "disable emulate"
#[test]
fn disable_emulate() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    cb.observe(mode.request_emulate(true));
    let result = cb.observe(mode.request_emulate(false));
    assert!(result.emulate_stopped);
    assert!(cb.stopped);

    assert!(!mode.snapshot().emulate_enabled);
}

// cpp: "enable emulate while already emulating is no-op"
#[test]
fn enable_emulate_while_already_emulating_is_no_op() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    cb.observe(mode.request_emulate(true));
    assert_eq!(cb.count, 1);

    let result = cb.observe(mode.request_emulate(true));
    assert!(!result.changed);
    assert_eq!(cb.count, 1); // no additional callback
}

// ── Mutual exclusion ────────────────────────────────────────────────

// cpp: "proxy and emulate are mutually exclusive"
#[test]
fn proxy_and_emulate_are_mutually_exclusive() {
    let mut mode = ModeStateMachine::new();

    mode.request_emulate(true);
    let snap1 = mode.snapshot();
    assert!(!snap1.proxy_enabled);
    assert!(snap1.emulate_enabled);

    mode.request_proxy(true);
    let snap2 = mode.snapshot();
    assert!(snap2.proxy_enabled);
    assert!(!snap2.emulate_enabled);
}

// ── Speed/incline auto-emulate ──────────────────────────────────────

// cpp: "set_speed auto-enables emulate"
#[test]
fn set_speed_auto_enables_emulate() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    let result = cb.observe(mode.set_speed(tenths(50)));
    assert!(result.emulate_started);
    assert_eq!(cb.last_start, Some(true));

    let snap = mode.snapshot();
    assert!(snap.emulate_enabled);
    assert!(!snap.proxy_enabled);
    // Speed is written AFTER the entry-zeroing.
    assert_eq!(snap.speed_tenths, tenths(50));
}

// cpp: "set_speed_mph auto-enables emulate"
#[test]
fn set_speed_mph_auto_enables_emulate() {
    let mut mode = ModeStateMachine::new();

    mode.set_speed_mph(Mph(1.2));
    let snap = mode.snapshot();
    assert!(snap.emulate_enabled);
    assert_eq!(snap.speed_tenths, tenths(12));
    assert_eq!(snap.speed_raw.get(), 120);
}

// cpp: "set_incline auto-enables emulate"
#[test]
fn set_incline_auto_enables_emulate() {
    let mut mode = ModeStateMachine::new();

    mode.set_incline(half(10)); // 10 half-pct = 5%
    let snap = mode.snapshot();
    assert!(snap.emulate_enabled);
    assert_eq!(snap.incline, half(10));
}

// ── Clamping ────────────────────────────────────────────────────────

// cpp: "speed clamped to MAX_SPEED_TENTHS"
#[test]
fn speed_clamped_to_max_speed_tenths() {
    let mut mode = ModeStateMachine::new();
    mode.set_speed(tenths(200));
    assert_eq!(mode.speed_tenths().get(), MAX_SPEED_TENTHS);
}

// cpp: "speed clamped to 0"
#[test]
fn speed_clamped_to_0() {
    let mut mode = ModeStateMachine::new();
    mode.set_speed(tenths(-10));
    assert_eq!(mode.speed_tenths(), tenths(0));
}

// cpp: "incline clamped to MAX_INCLINE (198 half-pct)"
#[test]
fn incline_clamped_to_max_incline_198_half_pct() {
    let mut mode = ModeStateMachine::new();
    mode.set_incline(half(300));
    assert_eq!(mode.incline().get(), MAX_INCLINE); // 198 half-pct
}

// cpp: "incline clamped to 0"
#[test]
fn incline_clamped_to_0() {
    let mut mode = ModeStateMachine::new();
    mode.set_incline(half(-5));
    assert_eq!(mode.incline(), half(0));
}

// ── Auto-proxy on console change ────────────────────────────────────

// cpp: "auto_proxy triggers on hmph change while emulating"
#[test]
fn auto_proxy_triggers_on_hmph_change_while_emulating() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    mode.request_emulate(true);

    let result = cb.observe(mode.auto_proxy_on_console_change("hmph", "78", "96"));
    assert!(result.changed);
    assert!(result.emulate_stopped);
    assert_eq!(cb.last_start, Some(false));

    let snap = mode.snapshot();
    assert!(snap.proxy_enabled);
    assert!(!snap.emulate_enabled);
}

// cpp: "auto_proxy triggers on inc change while emulating"
#[test]
fn auto_proxy_triggers_on_inc_change_while_emulating() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    let result = mode.auto_proxy_on_console_change("inc", "5", "7");
    assert!(result.changed);
    assert!(mode.is_proxy());
}

// cpp: "auto_proxy does nothing if not emulating"
#[test]
fn auto_proxy_does_nothing_if_not_emulating() {
    let mut mode = ModeStateMachine::new();
    let result = mode.auto_proxy_on_console_change("hmph", "78", "96");
    assert!(!result.changed);
}

// cpp: "auto_proxy does nothing if same value"
#[test]
fn auto_proxy_does_nothing_if_same_value() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    let result = mode.auto_proxy_on_console_change("hmph", "78", "78");
    assert!(!result.changed);
}

// cpp: "auto_proxy does nothing if first value (empty old)"
#[test]
fn auto_proxy_does_nothing_if_first_value_empty_old() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    let result = mode.auto_proxy_on_console_change("hmph", "", "78");
    assert!(!result.changed);
}

// cpp: "auto_proxy ignores non-hmph/inc keys"
#[test]
fn auto_proxy_ignores_non_hmph_inc_keys() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    let result = mode.auto_proxy_on_console_change("belt", "0", "1");
    assert!(!result.changed);
}

// ── Safety timeout ──────────────────────────────────────────────────

// cpp: "safety_timeout_reset zeros speed and incline"
#[test]
fn safety_timeout_reset_zeros_speed_and_incline() {
    let mut mode = ModeStateMachine::new();

    mode.set_speed(tenths(50));
    mode.set_incline(half(10)); // 10 half-pct = 5%

    assert_eq!(mode.speed_tenths(), tenths(50));
    assert_eq!(mode.incline(), half(10));

    mode.safety_timeout_reset();
    assert_eq!(mode.speed_tenths(), tenths(0));
    assert_eq!(mode.incline(), half(0));
}

// ── Watchdog reset to proxy ─────────────────────────────────────────

// cpp: "watchdog_reset_to_proxy zeros speed/incline and returns to proxy"
#[test]
fn watchdog_reset_to_proxy_zeros_speed_incline_and_returns_to_proxy() {
    let mut mode = ModeStateMachine::new();

    mode.request_emulate(true);
    mode.set_speed(tenths(50));
    mode.set_incline(half(14));

    assert!(mode.is_emulating());
    assert_eq!(mode.speed_tenths(), tenths(50));
    assert_eq!(mode.incline(), half(14));

    mode.watchdog_reset_to_proxy();

    assert_eq!(mode.speed_tenths(), tenths(0));
    assert_eq!(mode.incline(), half(0));
    assert!(mode.is_proxy());
    assert!(!mode.is_emulating());

    let snap = mode.snapshot();
    assert_eq!(snap.mode, Mode::Proxy);
    assert_eq!(snap.speed_tenths, tenths(0));
    assert_eq!(snap.speed_raw.get(), 0);
    assert_eq!(snap.incline, half(0));
}

// cpp: "watchdog_reset_to_proxy does NOT fire emulate callback"
#[test]
fn watchdog_reset_to_proxy_does_not_fire_emulate_callback() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    cb.observe(mode.request_emulate(true));
    assert_eq!(cb.count, 1); // start callback

    // Watchdog fires — must NOT report an emulate transition. In Rust this is
    // structural: `watchdog_reset_to_proxy` returns a `TransitionResult` with
    // both emulate flags clear by construction.
    cb.observe(mode.watchdog_reset_to_proxy());
    assert_eq!(cb.count, 1); // still 1

    assert!(mode.is_proxy());
    assert!(!mode.is_emulating());
}

// cpp: "watchdog_reset_to_proxy is safe when already in proxy"
#[test]
fn watchdog_reset_to_proxy_is_safe_when_already_in_proxy() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    assert!(mode.is_proxy());
    cb.observe(mode.watchdog_reset_to_proxy());

    assert_eq!(cb.count, 0);
    assert!(mode.is_proxy());
    assert_eq!(mode.speed_tenths(), tenths(0));
}

// cpp: "watchdog_reset_to_proxy is safe when in idle mode"
#[test]
fn watchdog_reset_to_proxy_is_safe_when_in_idle_mode() {
    let mut mode = ModeStateMachine::new();

    mode.request_proxy(false);
    assert!(!mode.is_proxy());
    assert!(!mode.is_emulating());

    mode.watchdog_reset_to_proxy();

    assert!(mode.is_proxy());
}

// cpp: "emulate can be re-enabled after watchdog reset"
#[test]
fn emulate_can_be_re_enabled_after_watchdog_reset() {
    let mut mode = ModeStateMachine::new();
    let mut cb = CallbackLog::default();

    cb.observe(mode.request_emulate(true));
    assert!(cb.started);
    cb.started = false;

    mode.watchdog_reset_to_proxy();
    assert!(mode.is_proxy());

    cb.observe(mode.request_emulate(true));
    assert!(cb.started);
    assert!(mode.is_emulating());
}

// ── Byte counters ───────────────────────────────────────────────────

// cpp: "byte counters"
#[test]
fn byte_counters() {
    let mut mode = ModeStateMachine::new();
    assert_eq!(mode.console_bytes(), 0);
    assert_eq!(mode.motor_bytes(), 0);

    mode.add_console_bytes(100);
    mode.add_motor_bytes(50);
    assert_eq!(mode.console_bytes(), 100);
    assert_eq!(mode.motor_bytes(), 50);

    mode.add_console_bytes(200);
    assert_eq!(mode.console_bytes(), 300);
}
