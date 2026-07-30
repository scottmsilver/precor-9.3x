//! Port of `host/tests/test_emulation.cpp` — 5 cases, 1:1 by name.

mod common;

use common::*;
use safety_core::cycle::{EmulationCycle, EMU_BURST_GAP_MS, EMU_TIMEOUT_US};
use safety_core::mode::ModeStateMachine;
use safety_core::units::Micros;

/// Drive n bursts with 100 ms fake gaps; returns the final time.
fn run_bursts<S: safety_core::cycle::KvSink>(
    cycle: &mut EmulationCycle,
    mode: &mut ModeStateMachine,
    sink: &mut S,
    n: usize,
    start: Micros,
) -> Micros {
    let mut t = start;
    for _ in 0..n {
        cycle.tick(t, mode, sink);
        t = t + Micros::new(EMU_BURST_GAP_MS * 1000);
    }
    t
}

// cpp: "emulation cycle sends 14-key cycle"
#[test]
fn emulation_cycle_sends_14_key_cycle() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();

    cycle.reset(Micros::ZERO);
    run_bursts(&mut cycle, &mut mode, &mut sink, 5, Micros::ZERO); // one full cycle

    let keys: Vec<&str> = sink.kv_log.iter().map(|(k, _)| k.as_str()).collect();
    assert!(keys.len() >= 14);
    assert_eq!(
        &keys[..14],
        &[
            "inc", "hmph", "amps", "err", "belt", "vbus", "lift", "lfts", "lftg", "part", "ver",
            "type", "diag", "loop",
        ]
    );
}

// cpp: "emulation cycle applies speed and incline"
#[test]
fn emulation_cycle_applies_speed_and_incline() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    // After emulate is enabled (which zeros the values).
    mode.set_speed(tenths(50));
    mode.set_incline(half(14));

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();

    cycle.reset(Micros::ZERO);
    run_bursts(&mut cycle, &mut mode, &mut sink, 5, Micros::ZERO);

    let mut found_inc = false;
    let mut found_hmph = false;
    for (k, v) in &sink.kv_log {
        if k == "inc" && v == "E" {
            found_inc = true;
        }
        // 50 tenths = 500 hundredths = 0x1F4
        if k == "hmph" && v == "1F4" {
            found_hmph = true;
        }
    }
    assert!(found_inc);
    assert!(found_hmph);
}

// cpp: "emulation cycle stops when mode changes"
#[test]
fn emulation_cycle_stops_when_mode_changes() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();

    cycle.reset(Micros::ZERO);
    assert!(cycle.tick(Micros::ZERO, &mut mode, &mut sink));

    mode.request_proxy(true); // disables emulate

    assert!(!cycle.tick(Micros::new(100_000), &mut mode, &mut sink));
    assert!(!cycle.tick(Micros::new(200_000), &mut mode, &mut sink));
}

// cpp: "emulation cycle stops after watchdog_reset_to_proxy"
#[test]
fn emulation_cycle_stops_after_watchdog_reset_to_proxy() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    mode.set_speed(tenths(50)); // belt is running

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();

    cycle.reset(Micros::ZERO);
    run_bursts(&mut cycle, &mut mode, &mut sink, 2, Micros::ZERO);
    assert!(!sink.kv_log.is_empty());

    mode.watchdog_reset_to_proxy();

    let before = sink.kv_log.len();
    assert!(!cycle.tick(Micros::new(300_000), &mut mode, &mut sink));
    assert_eq!(sink.kv_log.len(), before);

    assert_eq!(mode.speed_tenths(), tenths(0));
    assert_eq!(mode.incline(), half(0));
    assert!(mode.is_proxy());
}

// cpp: "emulation cycle 3-hour safety timeout zeros motion"
#[test]
fn emulation_cycle_3_hour_safety_timeout_zeros_motion() {
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    mode.set_speed(tenths(50));
    mode.set_incline(half(10));

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();

    cycle.reset(Micros::ZERO);
    // First tick observes the change and re-arms the timer at t=0.
    cycle.tick(Micros::ZERO, &mut mode, &mut sink);
    assert_eq!(mode.speed_tenths(), tenths(50));

    // Just before 3 hours of no changes: motion persists.
    cycle.tick(EMU_TIMEOUT_US - Micros::new(1), &mut mode, &mut sink);
    assert_eq!(mode.speed_tenths(), tenths(50));
    assert_eq!(mode.incline(), half(10));

    // At/after 3 hours: motion is zeroed.
    cycle.tick(EMU_TIMEOUT_US, &mut mode, &mut sink);
    assert_eq!(mode.speed_tenths(), tenths(0));
    assert_eq!(mode.incline(), half(0));
    // Still emulating — the timeout zeroes motion, it does not exit emulate.
    assert!(mode.is_emulating());
}
