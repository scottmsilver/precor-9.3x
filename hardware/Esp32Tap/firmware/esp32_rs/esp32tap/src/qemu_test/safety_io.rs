//! Scripted stand-in for `Esp32SafetyIo`.
//!
//! WRAPS a real one, so output-pin init ordering (level before direction) is
//! preserved exactly. Boot state models the bench rig: K1 released = BYPASS
//! (NC closed/LOW, NO open/HIGH), TREAD_OK asserted, VBUS present — unlike the
//! default build's floating-GPIO BOTH_CLOSED boot fault.
//!
//! `assert_boot_proxy` in the harness asserts `fault=0` and is therefore
//! TEST-IMAGE-ONLY; `qemu_smoke.sh` deliberately does not assert `fault`. Do
//! not "fix" the production boot to fault=0 under QEMU — the correct behaviour
//! on a real board is a real BYPASS sample.

use crate::hal::Esp32SafetyIo;
use safety_core::hal::SafetyIo;
use safety_core::safety::controller::OutputIntent;
use safety_core::units::{Micros, NcHigh, NoHigh, TreadOk, VbusPresent};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};

/// Break-before-make transit time of the modeled K1 relay: for this long after
/// a relay edge BOTH poles read open (BOTH_OPEN), then the target pole state
/// appears.
///
/// 2 ms sits inside the real-relay envelope: with `FEEDBACK_POLL_US = 200` the
/// feedback window sees transition -> candidate -> 1 ms stable ->
/// qualification at ~+1.2 ms, well before the 10 ms fail-closed deadline.
///
/// This is a MODELLING CHOICE, not a measurement. If `FEEDBACK_POLL_US`
/// changes, S3/S7a timing changes with it. Real relay transit is a bench
/// measurement.
pub const K1_TRANSIT_US: i64 = 2_000;

/// K1 feedback-path scripting (`QT k1`).
///
/// `Auto` is the command-coupled model; `Stuck` freezes the poles at the
/// settled state captured when scripted; the `Force*` modes pin the pole reads
/// outright. These exist to exercise the FAIL-CLOSED feedback paths (entry/exit
/// feedback timeouts, EMULATING-time feedback loss) that a well-behaved relay
/// never reaches.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(u8)]
pub enum K1Mode {
    Auto = 0,
    Stuck = 1,
    ForceBypass = 2,
    ForceEmulate = 3,
    ForceOpen = 4,
    ForceClosed = 5,
}

impl K1Mode {
    fn from_u8(v: u8) -> K1Mode {
        match v {
            1 => K1Mode::Stuck,
            2 => K1Mode::ForceBypass,
            3 => K1Mode::ForceEmulate,
            4 => K1Mode::ForceOpen,
            5 => K1Mode::ForceClosed,
            _ => K1Mode::Auto,
        }
    }
    pub fn parse(s: &str) -> Option<K1Mode> {
        match s {
            "auto" => Some(K1Mode::Auto),
            "stuck" => Some(K1Mode::Stuck),
            "bypass" => Some(K1Mode::ForceBypass),
            "emulate" => Some(K1Mode::ForceEmulate),
            "open" => Some(K1Mode::ForceOpen),
            "closed" => Some(K1Mode::ForceClosed),
            _ => None,
        }
    }
}

/// Atomics, like the C++: the scripting commands run on the qemu_test task
/// while the serial engine reads the poles.
pub struct QemuTestSafetyIo {
    real: Esp32SafetyIo,
    relay_on: AtomicBool,
    tx_on: AtomicBool,
    /// Relay-edge timestamp in µs, split hi/lo.
    ///
    /// Xtensa is a 32-bit target and has NO `AtomicI64`/`AtomicU64`. A single
    /// low-32-bit field is NOT sufficient: with `wrapping_sub`, once the relay
    /// has been unchanged for ~71.6 minutes the subtraction dips back under
    /// `K1_TRANSIT_US` for 2 ms and fabricates a BOTH_OPEN transit — which in
    /// EMULATING would trip a false `relay_feedback_invalid` emergency stop.
    /// Found by the session's `codex` review; the long-running S2a/S2b
    /// scenarios sit uncomfortably close to that window.
    ///
    /// Both halves are written in `apply` and read in `k1_*`, and EVERY such
    /// access happens under the `guarded` mutex, so the pair cannot tear.
    relay_edge_lo: AtomicU32,
    relay_edge_hi: AtomicU32,
    tread_ok: AtomicBool,
    vbus: AtomicBool,
    k1_mode: AtomicU8,
    /// Frozen pole state for `Stuck`.
    stuck_relay_on: AtomicBool,
}

impl Default for QemuTestSafetyIo {
    fn default() -> Self {
        Self::new()
    }
}

impl QemuTestSafetyIo {
    pub const fn new() -> Self {
        QemuTestSafetyIo {
            real: Esp32SafetyIo::new(),
            relay_on: AtomicBool::new(false),
            tx_on: AtomicBool::new(false),
            // Boot: force "not in transit" without relying on wrapping.
            relay_edge_lo: AtomicU32::new(0),
            relay_edge_hi: AtomicU32::new(0),
            tread_ok: AtomicBool::new(true),
            vbus: AtomicBool::new(true),
            k1_mode: AtomicU8::new(K1Mode::Auto as u8),
            stuck_relay_on: AtomicBool::new(false),
        }
    }

    /// Wrap the real HAL so output-pin configuration order (outputs LOW first)
    /// is preserved; input configs are harmless GPIO-stub writes.
    pub fn init(&mut self) -> bool {
        self.real.init()
    }

    fn now_us(&self) -> i64 {
        crate::hal::Esp32Clock::new().now().get()
    }

    fn edge_us(&self) -> i64 {
        let lo = self.relay_edge_lo.load(Ordering::Relaxed) as u64;
        let hi = self.relay_edge_hi.load(Ordering::Relaxed) as u64;
        ((hi << 32) | lo) as i64
    }

    fn store_edge_us(&self, v: i64) {
        let u = v as u64;
        self.relay_edge_lo.store(u as u32, Ordering::Relaxed);
        self.relay_edge_hi.store((u >> 32) as u32, Ordering::Relaxed);
    }

    fn in_transit(&self) -> bool {
        let edge = self.edge_us();
        if edge == 0 {
            return false; // no edge has ever been driven
        }
        let elapsed = self.now_us() - edge;
        (0..K1_TRANSIT_US).contains(&elapsed)
    }

    pub fn script_tread_ok(&self, v: bool) {
        self.tread_ok.store(v, Ordering::Relaxed);
    }

    pub fn script_vbus_present(&self, v: bool) {
        self.vbus.store(v, Ordering::Relaxed);
    }

    pub fn script_k1(&self, m: K1Mode) {
        if m == K1Mode::Stuck {
            // Freeze at the CURRENT SETTLED pole state (the harness scripts
            // Stuck only outside the 2 ms transit window).
            self.stuck_relay_on
                .store(self.relay_on.load(Ordering::Relaxed), Ordering::Relaxed);
        }
        self.k1_mode.store(m as u8, Ordering::Relaxed);
    }

    /// Levels the shim recorded crossing the IO boundary, reported in QTSTATE
    /// as `io_relay`/`io_tx`.
    ///
    /// WHAT THIS IS AND IS NOT (corrected 2026-07-28). These are the values
    /// `apply()` pushed through `SafetyIo` — one layer BELOW the controller,
    /// so a scenario asserting `io_relay=1` is not taking the controller's
    /// word for it, which is the property S3/S7 rely on. They are NOT a
    /// GPIO read-back and NOT a measurement at the pads: the real HAL's
    /// pad-reading `observed_relay()` (`hal/gpio.rs`) is shadowed by this
    /// method in the test image and is never consulted by the harness.
    /// Under QEMU there is no relay, no coil and no contact — the K1 model
    /// below is a MODEL — so "at the pads" evidence for the relay does not
    /// exist in this gate at all. It is a bench measurement (PLAN TC1/TC2,
    /// contact-observed), and the QEMU harness must not be read as standing
    /// in for it.
    pub fn observed_relay(&self) -> bool {
        self.relay_on.load(Ordering::Relaxed)
    }
    pub fn observed_tx(&self) -> bool {
        self.tx_on.load(Ordering::Relaxed)
    }

    fn set_relay_cmd(&mut self, on: bool) {
        let prev = self.relay_on.swap(on, Ordering::Relaxed);
        if prev != on {
            self.store_edge_us(self.now_us());
        }
    }
}

impl SafetyIo for QemuTestSafetyIo {
    /// Delegates to the real HAL — so the tx-before-relay write ORDER is the
    /// production one and really does reach `gpio_set_level` — then records
    /// the levels and the relay edge for the K1 model. (Under QEMU those
    /// `gpio_set_level` calls drive an emulated pad with nothing attached; see
    /// `observed_relay` for exactly what the recorded levels do and do not
    /// evidence.)
    fn apply(&mut self, intent: OutputIntent) {
        self.real.apply(intent);
        self.tx_on.store(intent.tx_enable.get(), Ordering::Relaxed);
        self.set_relay_cmd(intent.relay.get());
    }

    fn tread_ok(&self) -> TreadOk {
        TreadOk(self.tread_ok.load(Ordering::Relaxed))
    }

    /// Pull-up semantics: HIGH = contact OPEN. Break-before-make transit shows
    /// BOTH_OPEN, then the target pole state:
    ///   relay released  -> BYPASS  (NC closed/LOW, NO open/HIGH)
    ///   relay energized -> EMULATE (NC open/HIGH, NO closed/LOW)
    fn k1_nc_high(&self) -> NcHigh {
        match K1Mode::from_u8(self.k1_mode.load(Ordering::Relaxed)) {
            K1Mode::Stuck => return NcHigh(self.stuck_relay_on.load(Ordering::Relaxed)),
            K1Mode::ForceBypass => return NcHigh(false), // NC closed
            K1Mode::ForceEmulate => return NcHigh(true), // NC open
            K1Mode::ForceOpen => return NcHigh(true),
            K1Mode::ForceClosed => return NcHigh(false),
            K1Mode::Auto => {}
        }
        if self.in_transit() {
            return NcHigh(true);
        }
        NcHigh(self.relay_on.load(Ordering::Relaxed))
    }

    fn k1_no_high(&self) -> NoHigh {
        match K1Mode::from_u8(self.k1_mode.load(Ordering::Relaxed)) {
            K1Mode::Stuck => return NoHigh(!self.stuck_relay_on.load(Ordering::Relaxed)),
            K1Mode::ForceBypass => return NoHigh(true), // NO open
            K1Mode::ForceEmulate => return NoHigh(false), // NO closed
            K1Mode::ForceOpen => return NoHigh(true),
            K1Mode::ForceClosed => return NoHigh(false),
            K1Mode::Auto => {}
        }
        if self.in_transit() {
            return NoHigh(true);
        }
        NoHigh(!self.relay_on.load(Ordering::Relaxed))
    }

    fn vbus_present(&self) -> VbusPresent {
        VbusPresent(self.vbus.load(Ordering::Relaxed))
    }

    fn set_status_led(&mut self, on: bool) {
        self.real.set_status_led(on);
    }
}

/// Unused-import guard: `Micros` is part of the clock contract this file
/// depends on through `now_us`.
const _: Option<Micros> = None;
