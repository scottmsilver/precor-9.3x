//! D3 — controller and mode-machine op-sequence differential.
//!
//! Random sequences over the FULL public op alphabet, with timestamps
//! deliberately CLUSTERED ON THE EXACT DEADLINES (4 s lease, 1.5 s console
//! freshness, 1 s transfer gap, 10 ms feedback, 1 ms stable) and at ±1 µs of
//! each. After every single op the complete observable tuple is compared:
//! mode, speed, incline, tread_ok, feedback, fault, relay, tx, usb pull-up,
//! last-frame timestamp, owner, lease expiry, event count, and the last five
//! event strings.
//!
//! This directly attacks N7 — "every public operation advances all due
//! deadlines before it may consume or mutate state; an input at an exact
//! deadline LOSES to the deadline" — which the whole safety argument rests on
//! and which no hand-written unit grid can cover exhaustively.
//!
//! Failures shrink by bisecting the op list to a minimal reproducer.
//!
//! # 2026-07-28: the safety-critical half is now actually REACHED
//!
//! A reviewer measured that the purely random generator below spends
//! 300 000/300 000 samples in `SafeMode::Proxy`: `relay_cmd` is never once
//! true, `tx_enable` is never once true, and ENTRY_WAIT_GAP /
//! ENTRY_WAIT_FEEDBACK / EMULATING / EXIT_WAIT_GAP / EXIT_WAIT_FEEDBACK are
//! never entered at all. Uniform random ops essentially never assemble the
//! six-precondition entry preamble, and a random `ObserveFeedback` latches
//! BOTH_CLOSED early, after which `request_emulate` can never succeed again.
//! So D3 was differentially comparing the parsing/lease/mode half only — the
//! entry/exit sequencing, feedback qualification, transfer deadlines,
//! emergency paths and ownership-during-transfer were all differentially
//! UNCOVERED, which is exactly the half the safety argument rests on.
//!
//! Two things changed, both here in D3:
//!
//!  1. `gen_ops` is now STATEFUL and mostly GUIDED. It emits the entry
//!     preamble, keeps the console alive, and models the relay physically
//!     (feedback follows the coil, with deliberate fault injection), so the
//!     deep states are reached tens of thousands of times per run. Timestamps
//!     are still clustered on every normative deadline and ±1 µs of each; the
//!     guided path additionally clusters on the feedback-window deadlines,
//!     which the random path could never reach.
//!  2. The suites now MEASURE their own coverage and FAIL on a floor. A
//!     future change that stops reaching EMULATING turns D3 red instead of
//!     quietly reducing it to a Proxy-only differential again. The floors are
//!     printed as `D3COVERAGE` lines so the claim is checkable, not asserted.
//!
//! `Op::SafetyTimeoutZeroMotion` — the one FORK EXTENSION with no
//! `safety_model.py` counterpart — was previously a no-op on both sides
//! (motion is always zero in Proxy, and the method returns early when speed
//! and incline are both zero, so it never emitted an event or changed state).
//! The guided generator commands real motion, and the suite now asserts the op
//! was exercised with NONZERO motion a minimum number of times, so it is
//! genuinely differentially covered rather than nominally listed.

use difftest::cpp::{CppController, CppMode, CppState};
use difftest::gen::Rng;
use safety_core::mode::{Mode, ModeStateMachine};
use safety_core::safety::controller::{
    ConnectionIdentity, Feedback, SafeMode, SafetyController, Transport,
};
use safety_core::units::*;

// ── op alphabet ─────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug)]
enum Op {
    Connect { t: i32, h: i32, g: i64 },
    Acquire { t: i32, h: i32, g: i64, now: i64 },
    Heartbeat { t: i32, h: i32, g: i64, now: i64 },
    CommandMotion { t: i32, h: i32, g: i64, speed: i32, incline: i32, now: i64 },
    Disconnect { t: i32, h: i32, g: i64, now: i64 },
    DisconnectTransport { t: i32, now: i64 },
    ObserveConsole { which: usize, now: i64 },
    RequestEmulate { t: i32, h: i32, g: i64, now: i64, idle_low: bool },
    ObserveGap { now: i64 },
    ObserveFeedback { nc: bool, no: bool, now: i64 },
    RequestNormalExit { t: i32, h: i32, g: i64, now: i64 },
    SetTreadOk { v: bool, now: i64 },
    SetVbusPresentN { level_high: bool },
    Tick { now: i64 },
    SafetyTimeoutZeroMotion { now: i64 },
    EmergencyStop { which: usize, now: i64 },
    WatchdogStall { now: i64 },
    Reset { which: usize, now: i64 },
}

/// Console byte payloads: valid frames, a partial, a corrupt one, an oversized
/// candidate, and an over-long value — the whole scanner cliff set.
const CONSOLE_PAYLOADS: &[&[u8]] = &[
    b"[hmph:0000]",
    b"[loop:5550]",
    b"[inc:0000]",
    b"[hmph:78]",
    b"[hmph:0000",     // partial
    b"]",              // completes the partial
    b"[bad frame]",    // space in key -> rejected
    b"[9key:1]",       // key must start with a letter
    b"\xff\x00",       // pure delimiters
];

const EMERGENCY_REASONS: &[&str] = &[
    "tread_not_ok",
    "console_stale",
    "lease_expired",
    "explicit_emergency_stop",
    "brownout",
    "reset",
    "watchdog",
];

const RESET_REASONS: &[&str] = &["reset", "brownout", "power_glitch"];

/// Timestamps clustered on every normative deadline and ±1 µs of each.
fn interesting_time(rng: &mut Rng, base: i64) -> i64 {
    const DEADLINES: &[i64] = &[
        4_000_000, // MANUAL_LEASE_US
        1_500_000, // CONSOLE_FRESH_US
        1_000_000, // TRANSFER_GAP_DEADLINE_US
        10_000,    // RELAY_FEEDBACK_DEADLINE_US
        1_000,     // RELAY_FEEDBACK_STABLE_US
        20_000,    // GAP_QUALIFY_US
        200,       // FEEDBACK_POLL_US
        0,
    ];
    match rng.below(10) {
        0..=5 => {
            let d = *rng.pick(DEADLINES);
            let jitter = [-1i64, 0, 1][rng.below(3)];
            base + d + jitter
        }
        6..=7 => base + rng.range_i64(0, 2_000),
        8 => base + rng.range_i64(0, 5_000_000),
        _ => base,
    }
}

/// Timestamps for the GUIDED path, additionally clustered on the intra-window
/// boundaries the random path can never reach (200 µs poll, 1 ms stable,
/// 10 ms feedback deadline) at ±1 µs.
fn guided_time(rng: &mut Rng, base: i64) -> i64 {
    const FINE: &[i64] = &[
        0, 1, 199, 200, 201, 999, 1_000, 1_001, 1_200, 5_000, 9_999, 10_000, 10_001, 19_999,
        20_000, 20_001, 100_000, 200_000, 999_999, 1_000_000, 1_000_001, 1_499_999, 1_500_000,
        1_500_001, 3_999_999, 4_000_000, 4_000_001,
    ];
    base + *rng.pick(FINE)
}

/// What the relay's dry contacts would physically report for a commanded coil
/// state, with fault injection so the BOTH_* and mismatch paths are reached.
///
/// `mode`: 0 = faithful, 1 = BOTH_OPEN (legitimate break-before-make transit),
/// 2 = BOTH_CLOSED (welded — must fault), 3 = wrong-way (reports the opposite
/// of the commanded state).
fn plausible_feedback(relay_on: bool, mode: u8) -> (bool, bool) {
    match mode {
        0 => {
            if relay_on {
                (true, false) // EMULATE
            } else {
                (false, true) // BYPASS
            }
        }
        1 => (true, true),   // BOTH_OPEN
        2 => (false, false), // BOTH_CLOSED
        _ => {
            if relay_on {
                (false, true)
            } else {
                (true, false)
            }
        }
    }
}

/// GUIDED generator: drives the six-precondition entry preamble, keeps the
/// console bridge alive, models the relay physically, and then randomises over
/// the full op alphabet from INSIDE the safety-critical states.
///
/// It mirrors the controller's state locally (`relay_on`, `motion`) rather than
/// reading either implementation, so it stays a pure generator: the two
/// implementations are still compared step-for-step by `step()` and nothing
/// here can mask a divergence.
fn gen_guided_ops(rng: &mut Rng, n: usize) -> Vec<Op> {
    let t = *rng.pick(&[0i32, 1, 2]);
    let h = *rng.pick(&[1i32, 7, 42]);
    let g = 1i64;
    let mut ops = Vec::with_capacity(n + 8);
    let mut now = 0i64;

    // --- entry preamble, model-faithful ---------------------------------
    // A real BYPASS sample first: boot feedback is UNKNOWN, and entry requires
    // BYPASS, so without this the entry can never be attempted at all.
    ops.push(Op::ObserveFeedback {
        nc: false,
        no: true,
        now,
    });
    ops.push(Op::Connect { t, h, g });
    ops.push(Op::Acquire { t, h, g, now });
    ops.push(Op::ObserveConsole { which: 0, now }); // "[hmph:0000]" -> fresh
    // Occasionally violate ONE precondition, so the reject paths are compared
    // from a state where everything else is genuinely satisfied.
    if rng.below(24) == 0 {
        ops.push(Op::SetTreadOk { v: false, now });
    }
    ops.push(Op::RequestEmulate {
        t,
        h,
        g,
        now,
        idle_low: rng.below(24) != 0,
    });

    // Entry gap: on, before and exactly AT the 1 s deadline.
    now += *rng.pick(&[20_000i64, 20_000, 100_000, 100_000, 999_999, 1_000_000, 1_000_001]);
    ops.push(Op::ObserveGap { now });

    // Feedback qualification: two samples straddling the 1 ms stable interval
    // and the 10 ms deadline, with occasional injected faults.
    let mut relay_on = true; // the gap above commands the coil on
    let first = *rng.pick(&[200i64, 200, 1_000, 5_000, 9_999]);
    let m1 = if rng.below(10) < 8 { 0 } else { (rng.below(3) + 1) as u8 };
    let (nc1, no1) = plausible_feedback(relay_on, m1);
    ops.push(Op::ObserveFeedback {
        nc: nc1,
        no: no1,
        now: now + first,
    });
    let second = first + *rng.pick(&[999i64, 1_000, 1_000, 1_001, 1_001, 2_000, 10_000]);
    let m2 = if rng.below(10) < 8 { 0 } else { (rng.below(3) + 1) as u8 };
    let (nc2, no2) = plausible_feedback(relay_on, m2);
    ops.push(Op::ObserveFeedback {
        nc: nc2,
        no: no2,
        now: now + second,
    });
    now += second;

    // --- randomised operation from inside the transfer states ------------
    let mut motion_nonzero = false;
    for _ in 0..n {
        now = guided_time(rng, now);

        // Keep the console bridge alive most of the time, or the 1.5 s
        // freshness deadline tears every session down before anything else
        // can be exercised.
        if rng.below(3) != 0 {
            ops.push(Op::ObserveConsole { which: 1, now }); // "[loop:5550]"
        }
        if rng.below(4) == 0 {
            ops.push(Op::Heartbeat { t, h, g, now });
        }

        match rng.below(16) {
            0 | 1 => {
                let speed = *rng.pick(&[50i32, 50, 120, 0, 121, -1]);
                let incline = *rng.pick(&[10i32, 10, 30, 0, 31, -1]);
                let accepted = (0..=120).contains(&speed) && (0..=30).contains(&incline);
                motion_nonzero |= accepted && (speed != 0 || incline != 0);
                ops.push(Op::CommandMotion {
                    t,
                    h,
                    g,
                    speed,
                    incline,
                    now,
                });
                // Fire the 3-hour-timeout back-mirror WHILE motion is plausibly
                // nonzero. Emitted at random elsewhere it almost always lands
                // on zero motion, where the method returns early on both sides
                // and covers nothing — measured at 54 hits per run before this.
                if motion_nonzero && rng.below(2) == 0 {
                    ops.push(Op::SafetyTimeoutZeroMotion { now });
                    motion_nonzero = false;
                }
            }
            2 => ops.push(Op::RequestNormalExit { t, h, g, now }),
            3 => {
                relay_on = !relay_on; // model the coil moving on the exit path
                ops.push(Op::ObserveGap { now });
            }
            4 | 5 | 6 => {
                let m = rng.below(12);
                let m = if m < 8 { 0 } else { (m - 7) as u8 };
                let (nc, no) = plausible_feedback(relay_on, m);
                ops.push(Op::ObserveFeedback { nc, no, now });
            }
            7 => ops.push(Op::Tick { now }),
            8 => ops.push(Op::SetTreadOk {
                v: rng.below(8) != 0,
                now,
            }),
            9 => ops.push(Op::RequestEmulate {
                t,
                h,
                g,
                now,
                idle_low: rng.below(8) != 0,
            }),
            10 => ops.push(Op::SafetyTimeoutZeroMotion { now }),
            11 => ops.push(Op::SetVbusPresentN {
                level_high: rng.bool(),
            }),
            12 => ops.push(Op::Disconnect { t, h, g, now }),
            13 => ops.push(Op::DisconnectTransport {
                t: *rng.pick(&[0i32, 1, 2]),
                now,
            }),
            14 => match rng.below(12) {
                0 => ops.push(Op::EmergencyStop {
                    which: rng.below(EMERGENCY_REASONS.len()),
                    now,
                }),
                1 => ops.push(Op::WatchdogStall { now }),
                2 => ops.push(Op::Reset {
                    which: rng.below(RESET_REASONS.len()),
                    now,
                }),
                // Re-arm after a stop so a single sequence can cover several
                // entry/exit cycles instead of dying in Proxy on the first one.
                3 | 4 => {
                    relay_on = false;
                    ops.push(Op::ObserveFeedback {
                        nc: false,
                        no: true,
                        now,
                    });
                    ops.push(Op::Connect {
                        t,
                        h,
                        g: g + 1 + rng.below(3) as i64,
                    });
                }
                _ => ops.push(Op::Tick { now }),
            },
            _ => ops.push(Op::Connect {
                t,
                h,
                g: 1 + rng.below(4) as i64,
            }),
        }

    }
    ops
}

fn gen_ops(rng: &mut Rng, n: usize) -> Vec<Op> {
    // A tiny identity pool so supersession and reuse actually happen.
    let transports = [0i32, 1, 2]; // WSS, BLE, EXECUTOR
    let handles = [1i32, 7, 42];
    let gens = [1i64, 2, 10, 11];

    let mut ops = Vec::with_capacity(n);
    let mut now = 0i64;
    for _ in 0..n {
        now = interesting_time(rng, now).max(0);
        let t = *rng.pick(&transports);
        let h = *rng.pick(&handles);
        let g = *rng.pick(&gens);
        let op = match rng.below(18) {
            0 => Op::Connect { t, h, g },
            1 => Op::Acquire { t, h, g, now },
            2 => Op::Heartbeat { t, h, g, now },
            3 => Op::CommandMotion {
                t,
                h,
                g,
                // Straddle both clamps: 0/120/121/-1 and 0/30/31/-1.
                speed: [0i32, 50, 120, 121, -1][rng.below(5)],
                incline: [0i32, 10, 30, 31, -1][rng.below(5)],
                now,
            },
            4 => Op::Disconnect { t, h, g, now },
            5 => Op::DisconnectTransport { t, now },
            6 | 7 => Op::ObserveConsole {
                which: rng.below(CONSOLE_PAYLOADS.len()),
                now,
            },
            8 => Op::RequestEmulate {
                t,
                h,
                g,
                now,
                idle_low: rng.below(8) != 0,
            },
            9 => Op::ObserveGap { now },
            10 | 11 => Op::ObserveFeedback {
                nc: rng.bool(),
                no: rng.bool(),
                now,
            },
            12 => Op::RequestNormalExit { t, h, g, now },
            13 => Op::SetTreadOk {
                v: rng.below(4) != 0,
                now,
            },
            14 => Op::SetVbusPresentN {
                level_high: rng.bool(),
            },
            15 => Op::Tick { now },
            16 => match rng.below(6) {
                0 => Op::SafetyTimeoutZeroMotion { now },
                1 => Op::EmergencyStop {
                    which: rng.below(EMERGENCY_REASONS.len()),
                    now,
                },
                2 => Op::WatchdogStall { now },
                3 => Op::Reset {
                    which: rng.below(RESET_REASONS.len()),
                    now,
                },
                _ => Op::Tick { now },
            },
            _ => Op::Tick { now },
        };
        ops.push(op);
    }
    ops
}

// ── observable tuples ───────────────────────────────────────────────

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

fn transport_of(t: i32) -> Transport {
    match t {
        0 => Transport::Wss,
        1 => Transport::Ble,
        _ => Transport::Executor,
    }
}

fn ident(t: i32, h: i32, g: i64) -> ConnectionIdentity {
    ConnectionIdentity::new(transport_of(t), h, g).expect("generations are non-negative here")
}

/// Apply one op to both implementations and return true if they still agree.
fn step(r: &mut SafetyController, c: &mut CppController, op: &Op) -> Result<(), String> {
    // Return values are compared too, not just resulting state.
    let (rb, cb): (i64, i64) = match *op {
        Op::Connect { t, h, g } => (r.connect(&ident(t, h, g)) as i64, c.connect(t, h, g) as i64),
        Op::Acquire { t, h, g, now } => (
            r.acquire(&ident(t, h, g), Micros::new(now)) as i64,
            c.acquire(t, h, g, now) as i64,
        ),
        Op::Heartbeat { t, h, g, now } => (
            r.heartbeat(&ident(t, h, g), Micros::new(now)) as i64,
            c.heartbeat(t, h, g, now) as i64,
        ),
        Op::CommandMotion {
            t,
            h,
            g,
            speed,
            incline,
            now,
        } => (
            r.command_motion(
                &ident(t, h, g),
                SpeedTenths::new(speed),
                InclineHalfPct::new(incline),
                Micros::new(now),
            ) as i64,
            c.command_motion(t, h, g, speed, incline, now) as i64,
        ),
        Op::Disconnect { t, h, g, now } => (
            r.disconnect(&ident(t, h, g), Micros::new(now)) as i64,
            c.disconnect(t, h, g, now) as i64,
        ),
        Op::DisconnectTransport { t, now } => (
            r.disconnect_transport(transport_of(t), Micros::new(now)) as i64,
            c.disconnect_transport(t, now) as i64,
        ),
        Op::ObserveConsole { which, now } => (
            r.observe_console_bytes(CONSOLE_PAYLOADS[which], Micros::new(now)) as i64,
            c.observe_console_bytes(CONSOLE_PAYLOADS[which], now) as i64,
        ),
        Op::RequestEmulate {
            t,
            h,
            g,
            now,
            idle_low,
        } => (
            r.request_emulate(&ident(t, h, g), Micros::new(now), idle_low) as i64,
            c.request_emulate(t, h, g, now, idle_low) as i64,
        ),
        Op::ObserveGap { now } => (
            r.observe_interframe_gap(Micros::new(now)) as i64,
            c.observe_interframe_gap(now) as i64,
        ),
        Op::ObserveFeedback { nc, no, now } => (
            feedback_ord(r.observe_relay_feedback(NcHigh(nc), NoHigh(no), Micros::new(now))),
            // The C++ enum order matches `feedback_ord` by construction
            // (UNKNOWN, BYPASS, EMULATE, BOTH_CLOSED, BOTH_OPEN).
            c.observe_relay_feedback(nc, no, now),
        ),
        Op::RequestNormalExit { t, h, g, now } => (
            r.request_normal_exit(&ident(t, h, g), Micros::new(now)) as i64,
            c.request_normal_exit(t, h, g, now) as i64,
        ),
        Op::SetTreadOk { v, now } => {
            r.set_tread_ok(TreadOk(v), Micros::new(now));
            c.set_tread_ok(v, now);
            (0, 0)
        }
        Op::SetVbusPresentN { level_high } => {
            r.set_vbus_present_n(level_high);
            c.set_vbus_present_n(level_high);
            (0, 0)
        }
        Op::Tick { now } => {
            r.tick(Micros::new(now));
            c.tick(now);
            (0, 0)
        }
        Op::SafetyTimeoutZeroMotion { now } => {
            // `safety_timeout_zero_motion` is the ONE fork extension with no
            // `safety_model.py` counterpart (PROVENANCE deviation 4), so the
            // model cannot arbitrate a Rust/C++ disagreement here — which
            // makes DIFFERENTIAL coverage the only cross-check available, and
            // makes leaving it out the wrong trade.
            //
            // The `SafetyTimeoutFired` token (a real improvement over the C++
            // `bool` any caller can pass `true`) is what previously made this
            // op undrivable. It is not undone: the mint below is behind
            // safety_core's `test-mint` cargo feature, which only THIS crate
            // enables, so the token stays unforgeable in the firmware.
            let proof = safety_core::safety::controller::SafetyTimeoutFired::
                mint_for_differential_test();
            r.safety_timeout_zero_motion(proof, Micros::new(now));
            c.safety_timeout_zero_motion(now);
            (0, 0)
        }
        Op::EmergencyStop { which, now } => {
            r.emergency_stop(EMERGENCY_REASONS[which], Micros::new(now));
            c.emergency_stop(EMERGENCY_REASONS[which], now);
            (0, 0)
        }
        Op::WatchdogStall { now } => {
            r.watchdog_stall(Micros::new(now));
            c.watchdog_stall(now);
            (0, 0)
        }
        Op::Reset { which, now } => {
            r.reset(Micros::new(now), RESET_REASONS[which]);
            c.reset(RESET_REASONS[which], now);
            (0, 0)
        }
    };

    if rb != cb {
        return Err(format!("return value: rust={rb} cpp={cb}"));
    }
    let (rs_state, cpp_state) = (rust_state(r), c.state());
    if rs_state != cpp_state {
        return Err(format!("state:\n  rust={rs_state:?}\n  cpp ={cpp_state:?}"));
    }
    let (re, ce) = (rust_last_events(r, 5), c.last_events(5));
    if re != ce {
        return Err(format!("last 5 events:\n  rust={re:?}\n  cpp ={ce:?}"));
    }
    Ok(())
}

/// Replay a sequence; returns the index and message of the first divergence.
fn replay(ops: &[Op]) -> Option<(usize, String)> {
    let mut r = SafetyController::new();
    let mut c = CppController::new();
    for (i, op) in ops.iter().enumerate() {
        if let Err(msg) = step(&mut r, &mut c, op) {
            return Some((i, msg));
        }
    }
    None
}

// ── coverage ────────────────────────────────────────────────────────
//
// Measured, printed and FLOORED. Without this the suite can silently decay
// back into a Proxy-only differential, which is precisely what happened
// before 2026-07-28.

#[derive(Default, Debug)]
struct Coverage {
    /// Samples per `SafeMode`, indexed by `mode_ord`.
    mode: [u64; 6],
    relay_on: u64,
    tx_on: u64,
    fault_latched: u64,
    /// Entries into EMULATING (a completed gap-safe entry).
    entries_completed: u64,
    /// Completed normal exits (EMULATING -> ... -> PROXY via exit).
    exits_completed: u64,
    /// `safety_timeout_zero_motion` calls that actually had motion to zero —
    /// the only ones that exercise anything on either side.
    timeout_zero_with_motion: u64,
    /// Feedback observations taken while a transfer deadline was armed.
    feedback_in_transfer: u64,
}

impl Coverage {
    fn observe(&mut self, r: &SafetyController) {
        self.mode[mode_ord(r.mode()) as usize] += 1;
        if r.relay_cmd().get() {
            self.relay_on += 1;
        }
        if r.tx_enable().get() {
            self.tx_on += 1;
        }
        if r.fault_latched() {
            self.fault_latched += 1;
        }
    }

    fn print(&self, label: &str) {
        const NAMES: [&str; 6] = [
            "Proxy",
            "EntryWaitGap",
            "EntryWaitFeedback",
            "Emulating",
            "ExitWaitGap",
            "ExitWaitFeedback",
        ];
        for (i, n) in NAMES.iter().enumerate() {
            println!("D3COVERAGE[{label}] mode {n} = {}", self.mode[i]);
        }
        println!("D3COVERAGE[{label}] relay_cmd_on = {}", self.relay_on);
        println!("D3COVERAGE[{label}] tx_enable_on = {}", self.tx_on);
        println!("D3COVERAGE[{label}] fault_latched = {}", self.fault_latched);
        println!("D3COVERAGE[{label}] entries_completed = {}", self.entries_completed);
        println!("D3COVERAGE[{label}] exits_completed = {}", self.exits_completed);
        println!(
            "D3COVERAGE[{label}] safety_timeout_zero_motion_with_motion = {}",
            self.timeout_zero_with_motion
        );
        println!(
            "D3COVERAGE[{label}] feedback_samples_in_transfer = {}",
            self.feedback_in_transfer
        );
    }
}

/// Replay a sequence, accumulating coverage. Same comparison as `replay`.
fn replay_covered(ops: &[Op], cov: &mut Coverage) -> Option<(usize, String)> {
    let mut r = SafetyController::new();
    let mut c = CppController::new();
    for (i, op) in ops.iter().enumerate() {
        let before_mode = r.mode();
        let had_motion = !r.speed_tenths().get().eq(&0) || !r.incline_half_percent().get().eq(&0);
        if matches!(op, Op::ObserveFeedback { .. })
            && matches!(
                before_mode,
                SafeMode::EntryWaitFeedback | SafeMode::ExitWaitFeedback
            )
        {
            cov.feedback_in_transfer += 1;
        }
        if let Err(msg) = step(&mut r, &mut c, op) {
            return Some((i, msg));
        }
        let after_mode = r.mode();
        if before_mode != SafeMode::Emulating && after_mode == SafeMode::Emulating {
            cov.entries_completed += 1;
        }
        if matches!(
            before_mode,
            SafeMode::ExitWaitFeedback | SafeMode::ExitWaitGap
        ) && after_mode == SafeMode::Proxy
            && !r.fault_latched()
        {
            cov.exits_completed += 1;
        }
        if matches!(op, Op::SafetyTimeoutZeroMotion { .. }) && had_motion {
            cov.timeout_zero_with_motion += 1;
        }
        cov.observe(&r);
    }
    None
}

/// Bisect down to a minimal failing prefix/suffix.
fn shrink(ops: &[Op]) -> Vec<Op> {
    let mut best = ops.to_vec();
    let mut improved = true;
    while improved {
        improved = false;
        // Drop a prefix.
        let mut i = 1;
        while i < best.len() {
            let cand = best[i..].to_vec();
            if replay(&cand).is_some() {
                best = cand;
                improved = true;
                break;
            }
            i *= 2;
        }
        // Truncate a suffix.
        if let Some((idx, _)) = replay(&best) {
            if idx + 1 < best.len() {
                best.truncate(idx + 1);
                improved = true;
            }
        }
    }
    best
}

#[test]
fn d3_controller_op_sequences_match_cpp() {
    let mut rng = Rng::new(0xD3_0000_0000_0001);
    for seq in 0..3_000u64 {
        let ops = gen_ops(&mut rng, 60);
        if let Some((i, msg)) = replay(&ops) {
            let minimal = shrink(&ops);
            panic!(
                "D3 CONTROLLER DIVERGENCE\n  sequence #{seq}, op index {i}\n  {msg}\n\
                 \n  minimal reproducer ({} ops):\n{:#?}",
                minimal.len(),
                minimal
            );
        }
    }
}

#[test]
fn d3_controller_long_sequences_match_cpp() {
    // Longer runs let the 256-slot audit ring wrap and the 8/16-entry
    // connection and generation tables saturate.
    let mut rng = Rng::new(0xD3_0000_0000_0002);
    for seq in 0..200u64 {
        let ops = gen_ops(&mut rng, 600);
        if let Some((i, msg)) = replay(&ops) {
            let minimal = shrink(&ops);
            panic!(
                "D3 CONTROLLER DIVERGENCE (long)\n  sequence #{seq}, op index {i}\n  {msg}\n\
                 \n  minimal reproducer ({} ops):\n{:#?}",
                minimal.len(),
                minimal
            );
        }
    }
}

/// THE test the reviewer's finding demanded: the safety-critical controller
/// surface — entry/exit sequencing, feedback qualification, transfer
/// deadlines, emergency paths and ownership DURING a transfer — compared
/// against the C++ op-for-op, from inside the states the random suites above
/// cannot reach.
#[test]
fn d3_guided_safety_transfer_sequences_match_cpp() {
    let mut rng = Rng::new(0xD3_0000_0000_0004);
    let mut cov = Coverage::default();
    for seq in 0..12_000u64 {
        let ops = gen_guided_ops(&mut rng, 60);
        if let Some((i, msg)) = replay_covered(&ops, &mut cov) {
            let minimal = shrink(&ops);
            panic!(
                "D3 GUIDED SAFETY DIVERGENCE\n  sequence #{seq}, op index {i}\n  {msg}\n\
                 \n  minimal reproducer ({} ops):\n{:#?}",
                minimal.len(),
                minimal
            );
        }
    }
    cov.print("guided");

    // Coverage FLOORS. These are what stop this suite decaying back into the
    // Proxy-only differential it used to be. Each is ~2 orders of magnitude
    // below what the generator currently achieves, so they fail on a
    // structural regression, not on RNG noise.
    const NAMES: [&str; 6] = [
        "Proxy",
        "EntryWaitGap",
        "EntryWaitFeedback",
        "Emulating",
        "ExitWaitGap",
        "ExitWaitFeedback",
    ];
    for (i, n) in NAMES.iter().enumerate() {
        assert!(
            cov.mode[i] >= 100,
            "D3 reached {n} only {} times — the guided generator is no longer \
             driving the safety-critical half, so this suite is not comparing it",
            cov.mode[i]
        );
    }
    assert!(cov.relay_on >= 1_000, "relay_cmd was on for only {} samples", cov.relay_on);
    assert!(cov.tx_on >= 1_000, "tx_enable was on for only {} samples", cov.tx_on);
    assert!(cov.fault_latched >= 100, "latched faults: {}", cov.fault_latched);
    assert!(
        cov.entries_completed >= 500,
        "only {} gap-safe entries completed",
        cov.entries_completed
    );
    assert!(
        cov.exits_completed >= 100,
        "only {} normal exits completed — the exit half of the transfer is \
         not being differentially compared",
        cov.exits_completed
    );
    assert!(
        cov.feedback_in_transfer >= 1_000,
        "only {} feedback samples landed inside an armed transfer window",
        cov.feedback_in_transfer
    );
    // The one fork extension with no model counterpart: it must be driven
    // with motion actually present, or it is a no-op on both sides and the
    // differential proves nothing about it.
    assert!(
        cov.timeout_zero_with_motion >= 100,
        "safety_timeout_zero_motion was exercised with nonzero motion only {} \
         times — it is the ONE method safety_model.py cannot arbitrate, so the \
         differential is its only cross-check and a no-op call is not coverage",
        cov.timeout_zero_with_motion
    );
}

/// Documents — as an executable measurement, not a claim — that the ORIGINAL
/// random generator does not reach the safety-critical half. This is the
/// finding that motivated the guided suite; pinning it means nobody can later
/// point at `d3_controller_op_sequences_match_cpp` and believe it covers the
/// transfer logic.
#[test]
fn d3_random_generator_provably_never_reaches_the_transfer_states() {
    let mut rng = Rng::new(0xD3_0000_0000_0005);
    let mut cov = Coverage::default();
    for _ in 0..300u64 {
        let ops = gen_ops(&mut rng, 60);
        assert!(replay_covered(&ops, &mut cov).is_none());
    }
    cov.print("random");
    assert_eq!(cov.relay_on, 0, "the random generator now energises the relay");
    for i in 1..6 {
        assert_eq!(
            cov.mode[i], 0,
            "the random generator now reaches a transfer state — good news, but \
             update this measurement and the D3 header rather than leaving a \
             stale claim in the file"
        );
    }
}

// ── mode state machine ──────────────────────────────────────────────

#[derive(Clone, Copy, Debug)]
enum ModeOp {
    RequestProxy(bool),
    RequestEmulate(bool),
    SetSpeed(i32),
    SetSpeedMph(i64), // milli-mph, to keep the generator integral
    SetIncline(i32),
    AutoProxy { key: usize, old: usize, new: usize },
    SafetyTimeoutReset,
    WatchdogReset,
    AddConsoleBytes(u32),
    AddMotorBytes(u32),
}

const KEYS: &[&str] = &["hmph", "inc", "belt", "", "loop"];
const VALS: &[&str] = &["", "78", "A0", "1E", "0", "5550"];

fn mode_ord2(m: Mode) -> i64 {
    match m {
        Mode::Idle => 0,
        Mode::Proxy => 1,
        Mode::Emulating => 2,
    }
}

#[test]
fn d3_mode_state_op_sequences_match_cpp() {
    let mut rng = Rng::new(0xD3_0000_0000_0003);
    for seq in 0..5_000u64 {
        let mut r = ModeStateMachine::new();
        let mut c = CppMode::new();
        let mut ops: Vec<ModeOp> = Vec::new();

        for _ in 0..80 {
            let op = match rng.below(10) {
                0 => ModeOp::RequestProxy(rng.bool()),
                1 => ModeOp::RequestEmulate(rng.bool()),
                2 => ModeOp::SetSpeed([0i32, 50, 120, 121, 200, -10][rng.below(6)]),
                3 => ModeOp::SetSpeedMph(rng.range_i64(-2_000, 15_000)),
                4 => ModeOp::SetIncline([0i32, 10, 14, 30, 198, 300, -5][rng.below(7)]),
                5 | 6 => ModeOp::AutoProxy {
                    key: rng.below(KEYS.len()),
                    old: rng.below(VALS.len()),
                    new: rng.below(VALS.len()),
                },
                7 => ModeOp::SafetyTimeoutReset,
                8 => ModeOp::WatchdogReset,
                _ => {
                    if rng.bool() {
                        ModeOp::AddConsoleBytes(rng.below(1000) as u32)
                    } else {
                        ModeOp::AddMotorBytes(rng.below(1000) as u32)
                    }
                }
            };
            ops.push(op);

            // Transition results are packed identically on both sides:
            // bit0 changed, bit1 started, bit2 stopped.
            let (rb, cb) = match op {
                ModeOp::RequestProxy(v) => (pack(r.request_proxy(v)), c.request_proxy(v)),
                ModeOp::RequestEmulate(v) => (pack(r.request_emulate(v)), c.request_emulate(v)),
                ModeOp::SetSpeed(v) => (pack(r.set_speed(SpeedTenths::new(v))), c.set_speed(v)),
                ModeOp::SetSpeedMph(milli) => {
                    let mph = milli as f64 / 1000.0;
                    (pack(r.set_speed_mph(Mph(mph))), c.set_speed_mph(mph))
                }
                ModeOp::SetIncline(v) => (
                    pack(r.set_incline(InclineHalfPct::new(v))),
                    c.set_incline(v),
                ),
                ModeOp::AutoProxy { key, old, new } => (
                    pack(r.auto_proxy_on_console_change(KEYS[key], VALS[old], VALS[new])),
                    c.auto_proxy(KEYS[key], VALS[old], VALS[new]),
                ),
                ModeOp::SafetyTimeoutReset => {
                    r.safety_timeout_reset();
                    c.safety_timeout_reset();
                    (0, 0)
                }
                ModeOp::WatchdogReset => {
                    // Rust returns a TransitionResult (always clear); C++
                    // returns void. Both must report no emulate transition.
                    let rr = r.watchdog_reset_to_proxy();
                    assert!(!rr.emulate_started && !rr.emulate_stopped);
                    c.watchdog_reset();
                    (0, 0)
                }
                ModeOp::AddConsoleBytes(n) => {
                    r.add_console_bytes(n);
                    c.add_console_bytes(n);
                    (0, 0)
                }
                ModeOp::AddMotorBytes(n) => {
                    r.add_motor_bytes(n);
                    c.add_motor_bytes(n);
                    (0, 0)
                }
            };

            let rsnap = r.snapshot();
            let csnap = c.snapshot();
            let agree = rb == cb
                && mode_ord2(rsnap.mode) == csnap.mode
                && rsnap.speed_tenths.get() as i64 == csnap.speed_tenths
                && rsnap.speed_raw.get() as i64 == csnap.speed_raw
                && rsnap.incline.get() as i64 == csnap.incline
                && rsnap.proxy_enabled == csnap.proxy
                && rsnap.emulate_enabled == csnap.emulate
                && r.console_bytes() as i64 == csnap.console_bytes
                && r.motor_bytes() as i64 == csnap.motor_bytes;

            assert!(
                agree,
                "D3 MODE DIVERGENCE\n  sequence #{seq}, op {:?}\n  result rust={rb} cpp={cb}\n\
                 rust={rsnap:?}\n  cpp ={csnap:?}\n  ops so far: {ops:?}",
                ops.last().unwrap()
            );
        }
    }
}

fn pack(r: safety_core::mode::TransitionResult) -> i32 {
    (r.changed as i32) | ((r.emulate_started as i32) << 1) | ((r.emulate_stopped as i32) << 2)
}
