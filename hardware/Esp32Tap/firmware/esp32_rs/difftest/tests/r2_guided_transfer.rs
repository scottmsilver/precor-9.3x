//! R2 — INDEPENDENT reviewer differential, GUIDED into the states D3/R1 never
//! reach.
//!
//! Measured fact (reviewer probe): D3's random op sequences spend 300 000/300 000
//! samples in `SafeMode::Proxy`. `relay_cmd` is never once true, `tx_enable` is
//! never once true, and none of ENTRY_WAIT_GAP / ENTRY_WAIT_FEEDBACK / EMULATING
//! / EXIT_WAIT_GAP / EXIT_WAIT_FEEDBACK is ever entered, because a random
//! `ObserveFeedback` latches BOTH_CLOSED early and `request_emulate`'s
//! preconditions then never hold again.
//!
//! This suite therefore drives entry DELIBERATELY (the same preamble the 56
//! vectors use) and only then randomises, with a relay model that keeps the
//! feedback lines physically plausible. It reports its own coverage so the
//! claim is checkable rather than assumed.

use difftest::cpp::{CppController, CppState};
use difftest::gen::Rng;
use safety_core::safety::controller::{
    ConnectionIdentity, Feedback, SafeMode, SafetyController, Transport,
};
use safety_core::units::*;
use std::collections::BTreeMap;

fn mode_ord(m: SafeMode) -> i64 {
    match m {
        SafeMode::Proxy => 0,
        SafeMode::EntryWaitGap => 1,
        SafeMode::EntryWaitFeedback => 2,
        SafeMode::Emulating => 3,
        SafeMode::ExitWaitGap => 4,
        SafeMode::ExitWaitFeedback => 5,
    }
}
fn feedback_ord(f: Feedback) -> i64 {
    match f {
        Feedback::Unknown => 0,
        Feedback::Bypass => 1,
        Feedback::Emulate => 2,
        Feedback::BothClosed => 3,
        Feedback::BothOpen => 4,
    }
}
fn transport_ord(t: Transport) -> i64 {
    match t {
        Transport::Wss => 0,
        Transport::Ble => 1,
        Transport::Executor => 2,
    }
}

fn rust_state(c: &SafetyController) -> CppState {
    CppState {
        mode: mode_ord(c.mode()),
        speed: c.speed_tenths().get() as i64,
        incline: c.incline_half_percent().get() as i64,
        tread_ok: c.tread_ok().get(),
        feedback: feedback_ord(c.feedback()),
        fault_latched: c.fault_latched(),
        relay_cmd: c.relay_cmd().get(),
        tx_enable: c.tx_enable().get(),
        usb_pullup: c.usb_pullup_enabled(),
        last_frame_at: c.last_complete_console_frame_at().map(|m| m.get()),
        owner: c
            .owner()
            .map(|o| (transport_ord(o.transport), o.handle.0 as i64, o.generation.get())),
        lease_expires_at: c.lease_expires_at().map(|m| m.get()),
        event_count: c.event_count(),
    }
}
fn rust_last_events(c: &SafetyController, n: u64) -> Vec<String> {
    let count = c.event_count();
    let start = count.saturating_sub(n);
    (start..count)
        .map(|i| c.event_at(i).unwrap_or("").to_string())
        .collect()
}

struct Pair {
    r: SafetyController,
    c: CppController,
    trace: Vec<String>,
}

impl Pair {
    fn new() -> Self {
        Pair {
            r: SafetyController::new(),
            c: CppController::new(),
            trace: Vec::new(),
        }
    }
    fn check(&mut self, what: String) {
        self.trace.push(what.clone());
        let (rs, cs) = (rust_state(&self.r), self.c.state());
        assert_eq!(
            rs,
            cs,
            "STATE DIVERGENCE after {what}\n  rust={rs:?}\n  cpp ={cs:?}\n  trace={:#?}",
            self.trace
        );
        let (re, ce) = (rust_last_events(&self.r, 10), self.c.last_events(10));
        assert_eq!(
            re, ce,
            "EVENT DIVERGENCE after {what}\n  trace={:#?}",
            self.trace
        );
    }
    fn ret(&mut self, rb: i64, cb: i64, what: String) {
        assert_eq!(rb, cb, "RETURN DIVERGENCE for {what}\n  trace={:#?}", self.trace);
        self.check(what);
    }

    fn connect(&mut self, id: &ConnectionIdentity) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (self.r.connect(id) as i64, self.c.connect(t, h, g) as i64);
        self.ret(rb, cb, format!("connect({t},{h},{g})"));
    }
    fn acquire(&mut self, id: &ConnectionIdentity, now: i64) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.acquire(id, Micros::new(now)) as i64,
            self.c.acquire(t, h, g, now) as i64,
        );
        self.ret(rb, cb, format!("acquire({t},{h},{g},{now})"));
    }
    fn console(&mut self, data: &[u8], now: i64) {
        let (rb, cb) = (
            self.r.observe_console_bytes(data, Micros::new(now)) as i64,
            self.c.observe_console_bytes(data, now) as i64,
        );
        self.ret(rb, cb, format!("console({data:?},{now})"));
    }
    fn request_emulate(&mut self, id: &ConnectionIdentity, now: i64, idle: bool) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.request_emulate(id, Micros::new(now), idle) as i64,
            self.c.request_emulate(t, h, g, now, idle) as i64,
        );
        self.ret(rb, cb, format!("request_emulate({t},{h},{g},{now},{idle})"));
    }
    fn gap(&mut self, now: i64) {
        let (rb, cb) = (
            self.r.observe_interframe_gap(Micros::new(now)) as i64,
            self.c.observe_interframe_gap(now) as i64,
        );
        self.ret(rb, cb, format!("gap({now})"));
    }
    fn fb(&mut self, nc: bool, no: bool, now: i64) {
        let rb = feedback_ord(
            self.r
                .observe_relay_feedback(NcHigh(nc), NoHigh(no), Micros::new(now)),
        );
        let cb = self.c.observe_relay_feedback(nc, no, now);
        self.ret(rb, cb, format!("fb({nc},{no},{now})"));
    }
    fn motion(&mut self, id: &ConnectionIdentity, s: i32, i: i32, now: i64) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.command_motion(
                id,
                SpeedTenths::new(s),
                InclineHalfPct::new(i),
                Micros::new(now),
            ) as i64,
            self.c.command_motion(t, h, g, s, i, now) as i64,
        );
        self.ret(rb, cb, format!("motion({t},{h},{g},{s},{i},{now})"));
    }
    fn normal_exit(&mut self, id: &ConnectionIdentity, now: i64) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.request_normal_exit(id, Micros::new(now)) as i64,
            self.c.request_normal_exit(t, h, g, now) as i64,
        );
        self.ret(rb, cb, format!("normal_exit({t},{h},{g},{now})"));
    }
    fn tick(&mut self, now: i64) {
        self.r.tick(Micros::new(now));
        self.c.tick(now);
        self.check(format!("tick({now})"));
    }
    fn heartbeat(&mut self, id: &ConnectionIdentity, now: i64) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.heartbeat(id, Micros::new(now)) as i64,
            self.c.heartbeat(t, h, g, now) as i64,
        );
        self.ret(rb, cb, format!("heartbeat({t},{h},{g},{now})"));
    }
    fn tread(&mut self, v: bool, now: i64) {
        self.r.set_tread_ok(TreadOk(v), Micros::new(now));
        self.c.set_tread_ok(v, now);
        self.check(format!("tread({v},{now})"));
    }
    fn disconnect(&mut self, id: &ConnectionIdentity, now: i64) {
        let (t, h, g) = (
            transport_ord(id.transport) as i32,
            id.handle.0,
            id.generation.get(),
        );
        let (rb, cb) = (
            self.r.disconnect(id, Micros::new(now)) as i64,
            self.c.disconnect(t, h, g, now) as i64,
        );
        self.ret(rb, cb, format!("disconnect({t},{h},{g},{now})"));
    }
    fn disconnect_transport(&mut self, t: Transport, now: i64) {
        let (rb, cb) = (
            self.r.disconnect_transport(t, Micros::new(now)) as i64,
            self.c
                .disconnect_transport(transport_ord(t) as i32, now) as i64,
        );
        self.ret(rb, cb, format!("disconnect_transport({t:?},{now})"));
    }
    fn emergency(&mut self, reason: &str, now: i64) {
        self.r.emergency_stop(reason, Micros::new(now));
        self.c.emergency_stop(reason, now);
        self.check(format!("emergency({reason},{now})"));
    }
    fn watchdog(&mut self, now: i64) {
        self.r.watchdog_stall(Micros::new(now));
        self.c.watchdog_stall(now);
        self.check(format!("watchdog({now})"));
    }
    fn reset(&mut self, reason: &str, now: i64) {
        self.r.reset(Micros::new(now), reason);
        self.c.reset(reason, now);
        self.check(format!("reset({reason},{now})"));
    }
}

const TRANSPORTS: [Transport; 3] = [Transport::Wss, Transport::Ble, Transport::Executor];

/// The feedback the relay would physically report for a commanded coil state,
/// with a configurable fault injection so mismatch/BOTH_* paths are reached too.
fn plausible_feedback(relay_on: bool, mode: u8) -> (bool, bool) {
    match mode {
        0 => {
            if relay_on {
                (true, false) // EMULATE
            } else {
                (false, true) // BYPASS
            }
        }
        1 => (true, true),   // BOTH_OPEN — legitimate break-before-make transit
        2 => (false, false), // BOTH_CLOSED — welded, must fault
        _ => {
            if relay_on {
                (false, true) // wrong-way: relay on but reads BYPASS
            } else {
                (true, false)
            }
        }
    }
}

#[test]
fn r2_guided_entry_exit_transfer_differential() {
    let mut rng = Rng::new(0xA5A5_0000_0000_0001);
    let mut mode_hits: BTreeMap<i64, u64> = BTreeMap::new();
    let mut relay_on_samples = 0u64;
    let mut tx_on_samples = 0u64;

    for _seq in 0..12_000u64 {
        let mut p = Pair::new();
        let t = TRANSPORTS[rng.below(3)];
        let owner = ConnectionIdentity::new(t, 1 + rng.below(3) as i32, 1).unwrap();
        let mut now = 0i64;

        // --- deliberate, model-faithful entry preamble -------------------
        p.fb(false, true, now); // BYPASS sample
        p.connect(&owner);
        p.acquire(&owner, now);
        p.console(b"[hmph:0000]", now);
        // Occasionally violate a precondition so the reject paths are compared.
        let idle = rng.below(30) != 0;
        if rng.below(40) == 0 {
            p.tread(false, now);
        }
        p.request_emulate(&owner, now, idle);

        // Gap: sometimes at the exact 1 s deadline, sometimes late, sometimes fine.
        now += *rng.pick(&[100_000i64, 100_000, 100_000, 20_000, 20_000, 999_999, 1_000_000, 1_000_001]);
        p.gap(now);

        // Feedback qualification: sometimes exactly at the 10 ms deadline,
        // sometimes < 1 ms apart (too soon), sometimes right.
        let first = *rng.pick(&[200i64, 1_000, 5_000, 8_000, 9_999]);
        let (nc1, no1) = plausible_feedback(p.r.relay_cmd().get(), if rng.below(10) < 8 { 0 } else { (rng.below(3) + 1) as u8 });
        p.fb(nc1, no1, now + first);
        let second = first + *rng.pick(&[1_000i64, 1_000, 1_001, 1_001, 2_000, 0, 999, 10_000]);
        let (nc2, no2) = plausible_feedback(p.r.relay_cmd().get(), if rng.below(10) < 8 { 0 } else { (rng.below(3) + 1) as u8 });
        p.fb(nc2, no2, now + second);
        now += second;

        *mode_hits.entry(mode_ord(p.r.mode())).or_default() += 1;

        // --- randomised operation after the transfer ---------------------
        for _ in 0..60 {
            if p.r.relay_cmd().get() {
                relay_on_samples += 1;
            }
            if p.r.tx_enable().get() {
                tx_on_samples += 1;
            }
            *mode_hits.entry(mode_ord(p.r.mode())).or_default() += 1;

            // Time advances by an interesting delta, clustered on the deadlines.
            now += *rng.pick(&[
                1i64, 199, 200, 999, 1_000, 1_001, 9_999, 10_000, 10_001, 19_999, 20_000,
                100_000, 100_000, 200_000, 200_000, 500_000,
                999_999, 1_000_000, 1_499_999, 1_500_000, 1_500_001, 3_999_999, 4_000_000,
            ]);
            // Keep the console bridge alive most of the time, so EMULATING is
            // not immediately torn down by the 1.5 s freshness deadline.
            if rng.below(3) != 0 {
                p.console(b"[loop:5550]", now);
            }
            if rng.below(4) == 0 {
                p.heartbeat(&owner, now);
            }

            match rng.below(14) {
                0 => p.console(b"[loop:5550]", now),
                1 => p.console(b"[hmph:0000]", now),
                2 => p.tick(now),
                3 => p.heartbeat(&owner, now),
                4 => p.motion(
                    &owner,
                    *rng.pick(&[0i32, 50, 120, 121, -1]),
                    *rng.pick(&[0i32, 10, 30, 31, -1]),
                    now,
                ),
                5 => p.normal_exit(&owner, now),
                6 => p.gap(now),
                7 | 8 => {
                    let m = rng.below(10);
                    let m = if m < 6 { 0 } else { (m - 5) as u8 };
                    let (nc, no) = plausible_feedback(p.r.relay_cmd().get(), m);
                    p.fb(nc, no, now);
                }
                9 => p.tread(rng.below(6) != 0, now),
                10 => p.request_emulate(&owner, now, rng.below(10) != 0),
                11 => p.disconnect_transport(TRANSPORTS[rng.below(3)], now),
                12 => {
                    if rng.below(6) == 0 {
                        p.disconnect(&owner, now)
                    } else {
                        p.tick(now)
                    }
                }
                _ => match rng.below(20) {
                    0 => p.emergency("explicit_emergency_stop", now),
                    1 => p.watchdog(now),
                    2 => p.reset("brownout", now),
                    _ => p.tick(now),
                },
            }
        }
    }

    let names = [
        "Proxy",
        "EntryWaitGap",
        "EntryWaitFeedback",
        "Emulating",
        "ExitWaitGap",
        "ExitWaitFeedback",
    ];
    for (i, n) in names.iter().enumerate() {
        let v = mode_hits.get(&(i as i64)).copied().unwrap_or(0);
        println!("R2COVERAGE mode {n} = {v}");
    }
    println!("R2COVERAGE relay_cmd_on_samples = {relay_on_samples}");
    println!("R2COVERAGE tx_enable_on_samples = {tx_on_samples}");

    // The whole point of this suite: assert it actually got where D3 could not.
    for (i, n) in names.iter().enumerate() {
        assert!(
            mode_hits.get(&(i as i64)).copied().unwrap_or(0) > 0,
            "R2 never reached {n} — the guided generator is not doing its job"
        );
    }
    assert!(relay_on_samples > 0, "relay was never commanded on");
    assert!(tx_on_samples > 0, "tx_enable was never asserted");
}
