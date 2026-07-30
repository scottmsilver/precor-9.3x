//! Port of `host/tests/test_safety_controller.cpp` — 56 cases, 1:1 by name.
//!
//! Each case also names its counterpart in
//! `hardware/Esp32Tap/tests/test_firmware_safety_model.py`; the numeric
//! vectors are the same, expressed in integer microseconds (PLAN D4).
//!
//! `tools/check_case_parity.py` asserts the three-way name chain
//! (C++ <-> Rust <-> Python) and fails the build on any drift.

mod common;

use common::*;
use safety_core::safety::constants::*;
use safety_core::safety::controller::{
    feedback_from_gpio, Feedback, SafeMode, SafetyController, Transport,
};
use safety_core::units::*;

fn fb(c: &mut SafetyController, nc: bool, no: bool, now: Micros) -> Feedback {
    c.observe_relay_feedback(NcHigh(nc), NoHigh(no), now)
}

// ── Lease identity, generation, supersession ────────────────────────

// py: test_lease_uses_transport_handle_and_generation
#[test]
fn lease_uses_transport_handle_and_generation() {
    for t in [Transport::Wss, Transport::Ble] {
        let owner = identity(t, 42, 7);
        let mut c = connected_controller(&owner);
        assert_eq!(c.owner(), Some(owner));
        assert!(!c.command_motion(&identity(t, 42, 6), tenths(20), half(2), us(S)));
        assert!(!c.command_motion(&identity(t, 42, 8), tenths(20), half(2), us(S)));
        assert_eq!(c.speed_tenths(), tenths(0));
        assert_eq!(c.incline_half_percent(), half(0));
    }
}

// py: test_manual_owner_persists_without_a_deadline
#[test]
fn manual_owner_persists_without_a_deadline() {
    let owner = default_identity();
    let other = identity(Transport::Wss, 101, 1);
    let mut c = connected_controller(&owner);
    assert!(c.connect(&other));

    assert!(c.command_motion(&owner, tenths(30), half(0), Micros::ZERO));
    assert!(c.lease_expires_at().is_none());
    assert!(!c.command_motion(&other, tenths(90), half(8), us(2 * S)));
    assert!(!c.heartbeat(&other, us(9 * S)));
    assert!(c.heartbeat(&owner, us(10 * S)));
    c.tick(us(10 * S));
    assert_eq!(c.owner(), Some(owner));
    assert_eq!(c.speed_tenths(), tenths(30));
    assert_eq!(c.incline_half_percent(), half(0));
    assert!(c.lease_expires_at().is_none());
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(!c.relay_cmd().get());
    assert!(!has_event(&c, "emergency:lease_expired", 0));
}

// py: test_manual_owner_persists_without_heartbeat
#[test]
fn manual_owner_persists_without_heartbeat() {
    for transport in [Transport::Wss, Transport::Ble] {
        let owner = identity(transport, 23, 1);
        let mut c = connected_controller(&owner);
        assert!(c.command_motion(&owner, tenths(30), half(0), Micros::ZERO));

        c.tick(us(10 * S));

        assert_eq!(c.owner(), Some(owner));
        assert_eq!(c.speed_tenths(), tenths(30));
        assert!(c.lease_expires_at().is_none());
        assert!(!has_event(&c, "emergency:lease_expired", 0));
    }
}

// py: test_unrelated_transport_drop_does_not_end_manual_owner
#[test]
fn unrelated_transport_drop_does_not_end_manual_owner() {
    let owner = identity(Transport::Ble, 23, 1);
    let mut c = connected_controller(&owner);
    assert!(c.command_motion(&owner, tenths(30), half(0), Micros::ZERO));

    assert!(!c.disconnect_transport(Transport::Wss, us(10 * S)));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert_eq!(c.owner(), Some(owner));
    assert_eq!(c.speed_tenths(), tenths(30));
    assert!(c.lease_expires_at().is_none());
    assert!(!c.relay_cmd().get());
    assert!(!has_event(&c, "emergency:lease_expired", 0));
}

// py: test_owner_disconnect_is_immediate_but_non_owner_disconnect_is_ignored
#[test]
fn owner_disconnect_immediate_non_owner_disconnect_ignored() {
    let owner = default_identity();
    let other = identity(Transport::Wss, 101, 1);
    let mut c = connected_controller(&owner);
    assert!(c.connect(&other));
    enter_emulate(&mut c, &owner, Micros::ZERO);

    assert!(!c.disconnect(&other, ms(500)));
    assert_eq!(c.mode(), SafeMode::Emulating);
    assert!(c.disconnect(&owner, ms(600)));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert_eq!(last_event(&c), "emergency:owner_disconnect");
}

// py: test_reconnect_and_handle_reuse_cannot_inherit_an_old_lease
#[test]
fn reconnect_and_handle_reuse_cannot_inherit_an_old_lease() {
    let old = identity(Transport::Ble, 23, 10);
    let mut c = connected_controller(&old);
    assert!(c.disconnect(&old, ms(100)));

    assert!(!c.connect(&old)); // stale generation
    let reused = identity(Transport::Ble, 23, 11);
    assert!(c.connect(&reused));
    assert!(c.owner().is_none());
    assert!(!c.command_motion(&reused, tenths(10), half(0), ms(200)));
    assert!(c.acquire(&reused, ms(300)));
    assert_eq!(c.owner(), Some(reused));
    assert!(!c.heartbeat(&old, ms(400)));
}

// py: test_new_generation_invalidates_and_safely_stops_superseded_owner
#[test]
fn new_generation_invalidates_and_safely_stops_superseded_owner() {
    let old = identity(Transport::Ble, 23, 10);
    let mut c = connected_controller(&old);
    enter_emulate(&mut c, &old, Micros::ZERO);

    let fresh = identity(Transport::Ble, 23, 11);
    assert!(c.connect(&fresh));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert!(!c.acquire(&old, ms(200)));
    assert!(c.acquire(&fresh, ms(200)));
}

// py: test_executor_owns_locally_and_network_loss_does_not_renew_or_end_it
#[test]
fn executor_owns_locally_network_loss_does_not_renew_or_end_it() {
    let executor = identity(Transport::Executor, 17, 3);
    let wss = default_identity();
    let mut c = connected_controller(&executor);
    assert!(c.connect(&wss));
    enter_emulate(&mut c, &executor, Micros::ZERO);

    assert!(c.lease_expires_at().is_none());
    assert!(!c.heartbeat(&wss, ms(500)));
    assert!(!c.disconnect(&wss, ms(600)));
    c.observe_console_bytes(b"[loop:5550]", ms(700));
    c.tick(ms(800));
    assert_eq!(c.owner(), Some(executor));
    assert_eq!(c.mode(), SafeMode::Emulating);
}

// py: test_network_failure_matrix
#[test]
fn network_failure_matrix() {
    let rows: &[(Transport, &str, bool)] = &[
        (Transport::Wss, "silence", false),
        (Transport::Wss, "wss_drop", true),
        (Transport::Wss, "ble_drop", false),
        (Transport::Ble, "silence", false),
        (Transport::Ble, "wss_drop", false),
        (Transport::Ble, "ble_drop", true),
        (Transport::Executor, "silence", false),
        (Transport::Executor, "wss_drop", false),
        (Transport::Executor, "ble_drop", false),
    ];
    for &(source, failure, must_proxy) in rows {
        let owner = identity(source, 17, 1);
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);

        match failure {
            "silence" => {
                for now in [ms(1_400), ms(2_800), ms(3_900)] {
                    c.observe_console_bytes(b"[loop:5550]", now);
                }
                c.tick(us(4 * S));
            }
            "wss_drop" => {
                c.disconnect_transport(Transport::Wss, us(S));
            }
            _ => {
                c.disconnect_transport(Transport::Ble, us(S));
            }
        }

        assert_eq!(
            c.mode() == SafeMode::Proxy,
            must_proxy,
            "{source:?}/{failure}"
        );
        assert_eq!(c.relay_cmd().get(), !must_proxy, "{source:?}/{failure}");
    }
}

// py: test_reset_and_watchdog_matrix_always_returns_hardware_to_proxy
#[test]
fn reset_and_watchdog_matrix_always_returns_hardware_to_proxy() {
    for source in [Transport::Wss, Transport::Ble, Transport::Executor] {
        for watchdog in [false, true] {
            let owner = identity(source, 17, 1);
            let mut c = connected_controller(&owner);
            enter_emulate(&mut c, &owner, Micros::ZERO);

            if watchdog {
                c.watchdog_stall(us(S));
            } else {
                c.reset(us(S), "brownout");
            }

            assert_eq!(c.mode(), SafeMode::Proxy);
            assert!(c.owner().is_none());
            assert!(!c.relay_cmd().get());
            assert!(!c.tx_enable().get());
        }
    }
}

// py: test_reset_class_failures_invalidate_pre_reset_connections
#[test]
fn reset_class_failures_invalidate_pre_reset_connections() {
    for watchdog in [false, true] {
        let old = identity(Transport::Wss, 7, 1);
        let mut c = connected_controller(&old);
        if watchdog {
            c.watchdog_stall(us(S));
        } else {
            c.reset(us(S), "reset");
        }

        assert!(!c.acquire(&old, ms(1_100)));
        assert!(!c.connect(&old)); // the generation map survives a reset
        let fresh = identity(Transport::Wss, 7, 2);
        assert!(c.connect(&fresh));
        assert!(c.acquire(&fresh, ms(1_200)));
        assert_eq!(c.owner(), Some(fresh));
    }
}

// py: test_console_source_is_hardware_bridge_and_network_failures_do_nothing
#[test]
fn console_bridge_network_failures_do_nothing() {
    let mut c = SafetyController::new();
    c.disconnect_transport(Transport::Wss, us(S));
    c.disconnect_transport(Transport::Ble, us(2 * S));
    c.tick(us(100 * S));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert_eq!(c.feedback(), Feedback::Unknown);
    assert!(!c.relay_cmd().get());
}

// ── Motion clamps ───────────────────────────────────────────────────

// py: (clamps per PLAN; accept 0/120/0/30, reject 121/-1/31)
#[test]
fn motion_clamps_accept_boundary_values_and_reject_outside() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);

    assert!(c.command_motion(&owner, tenths(0), half(0), ms(100)));
    assert!(c.command_motion(&owner, tenths(120), half(30), ms(200)));
    assert_eq!(c.speed_tenths(), tenths(120));
    assert_eq!(c.incline_half_percent(), half(30));

    assert!(!c.command_motion(&owner, tenths(121), half(0), ms(300)));
    assert_eq!(last_event(&c), "motion_rejected:speed_range");
    assert!(!c.command_motion(&owner, tenths(-1), half(0), ms(300)));
    assert!(!c.command_motion(&owner, tenths(0), half(31), ms(300)));
    assert_eq!(last_event(&c), "motion_rejected:incline_range");
    assert!(!c.command_motion(&owner, tenths(0), half(-1), ms(300)));
    // Rejection is WHOLESALE — no partial application.
    assert_eq!(c.speed_tenths(), tenths(120));
    assert_eq!(c.incline_half_percent(), half(30));
}

// ── Console freshness ───────────────────────────────────────────────

// py: test_console_freshness_requires_a_complete_valid_frame
#[test]
fn console_freshness_requires_a_complete_valid_frame() {
    let mut c = SafetyController::new();
    c.observe_console_bytes(b"[hmph:0000", Micros::ZERO);
    assert!(c.last_complete_console_frame_at().is_none());
    c.observe_console_bytes(b"]", ms(250));
    assert_eq!(c.last_complete_console_frame_at(), Some(ms(250)));

    c.observe_console_bytes(b"\xff[bad frame]\x00", ms(500));
    assert_eq!(c.last_complete_console_frame_at(), Some(ms(250)));
    c.observe_console_bytes(b"[inc:0000]", us(S));
    assert_eq!(c.last_complete_console_frame_at(), Some(us(S)));
}

// py: (partial/corrupt/oversized never refresh)
#[test]
fn partial_corrupt_and_oversized_frames_never_refresh() {
    let mut c = SafetyController::new();
    // Corrupt: a non-printable byte clears the candidate.
    c.observe_console_bytes(&[b'[', b'k', b':', 0x01, b']'], Micros::ZERO);
    assert!(c.last_complete_console_frame_at().is_none());
    // The key must start with a letter.
    c.observe_console_bytes(b"[9key:1]", Micros::ZERO);
    assert!(c.last_complete_console_frame_at().is_none());
    // Oversized: a candidate over 100 bytes is discarded.
    let oversized = format!("[k:{}]", "x".repeat(120));
    c.observe_console_bytes(oversized.as_bytes(), Micros::ZERO);
    assert!(c.last_complete_console_frame_at().is_none());
    // A value longer than 64 is rejected by the frame pattern.
    let longval = format!("[k:{}]", "v".repeat(65));
    c.observe_console_bytes(longval.as_bytes(), Micros::ZERO);
    assert!(c.last_complete_console_frame_at().is_none());
    // A valid frame still parses after all that.
    c.observe_console_bytes(b"[hmph:78]", ms(10));
    assert_eq!(c.last_complete_console_frame_at(), Some(ms(10)));
}

// py: test_late_console_frame_cannot_overwrite_missed_freshness_deadline
#[test]
fn late_console_frame_cannot_overwrite_missed_freshness_deadline() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    // Returns 0 AND consumes nothing.
    assert_eq!(c.observe_console_bytes(b"[loop:5550]", ms(1_500)), 0);
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert_eq!(c.last_complete_console_frame_at(), Some(Micros::ZERO));
    assert_eq!(last_event(&c), "emergency:console_stale");
}

// py: test_stale_console_forces_immediate_zero_and_bypass
#[test]
fn stale_console_forces_immediate_zero_and_bypass() {
    for age in [us(1_500_001), us(20 * S)] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);

        c.tick(age);
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert_eq!(last_event(&c), "emergency:console_stale");
    }
}

// py: test_exactly_one_point_five_seconds_is_stale
#[test]
fn exactly_1_5_s_is_stale() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    c.tick(us(1_499_999));
    assert_eq!(c.mode(), SafeMode::Emulating);
    c.tick(us(1_500_000));
    assert_eq!(c.mode(), SafeMode::Proxy);
}

// py: test_motion_command_at_console_deadline_cannot_refresh_or_mutate
#[test]
fn motion_command_at_console_deadline_cannot_refresh_or_mutate() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    assert!(!c.command_motion(&owner, tenths(60), half(10), ms(1_500)));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert_eq!(c.speed_tenths(), tenths(0));
    assert_eq!(c.incline_half_percent(), half(0));
    assert_eq!(last_event(&c), "emergency:console_stale");
}

// ── Emulate entry ───────────────────────────────────────────────────

// py: test_entry_order_and_first_zero_frame_follow_settled_transfer
#[test]
fn entry_order_and_first_zero_frame_follow_settled_transfer() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);

    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert_eq!(
        last_events(&c, 5),
        vec![
            "command_zero",
            "configure_inverted_uart",
            "verify_physical_idle_low",
            "tx_enable_on",
            "wait_entry_gap",
        ]
    );
    assert!(!c.relay_cmd().get());
    assert!(c.observe_interframe_gap(ms(200)));
    assert_eq!(last_event(&c), "relay_cmd_on");
    assert!(!has_event(&c, "send_first_complete_zero_frame", 0));

    fb(&mut c, true, false, ms(205));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    fb(&mut c, true, false, ms(206));
    assert_eq!(
        last_events(&c, 2),
        vec!["feedback_emulate_stable", "send_first_complete_zero_frame"]
    );
}

// py: test_entry_preconditions (adapted: state reached through the public API)
#[test]
fn entry_rejected_when_not_owner() {
    let owner = default_identity();
    let other = identity(Transport::Wss, 101, 1);
    let mut c = connected_controller(&owner);
    assert!(c.connect(&other));
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(!c.request_emulate(&other, ms(100), true));
    assert_eq!(last_event(&c), "entry_rejected:not_owner");
}

#[test]
fn entry_rejected_when_not_proxy() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);
    c.observe_console_bytes(b"[hmph:0000]", ms(200));
    assert!(!c.request_emulate(&owner, ms(200), true));
    assert_eq!(last_event(&c), "entry_rejected:not_proxy");
}

#[test]
fn entry_rejected_when_tread_not_ok() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    c.set_tread_ok(TreadOk(false), ms(100));
    assert!(!c.request_emulate(&owner, ms(500), true));
    assert_eq!(last_event(&c), "entry_rejected:tread_not_ok");
    assert!(!c.relay_cmd().get());
}

#[test]
fn entry_rejected_when_feedback_is_not_bypass_boot_unknown() {
    let mut c = SafetyController::new(); // boot: feedback UNKNOWN, never sampled
    let owner = default_identity();
    assert!(c.connect(&owner));
    assert!(c.acquire(&owner, Micros::ZERO));
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(!c.request_emulate(&owner, ms(500), true));
    assert_eq!(last_event(&c), "entry_rejected:feedback_not_bypass");
    assert!(!c.relay_cmd().get());
}

#[test]
fn entry_rejected_when_console_unknown_or_stale() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    // Unknown: no frame ever observed.
    assert!(!c.request_emulate(&owner, ms(500), true));
    assert_eq!(last_event(&c), "entry_rejected:console_not_fresh");

    // Stale: frame at 0, entry at 2.0 s.
    let mut c2 = connected_controller(&owner);
    c2.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(!c2.request_emulate(&owner, us(2 * S), true));
    assert_eq!(last_event(&c2), "entry_rejected:console_not_fresh");
    assert!(!c2.relay_cmd().get());
}

#[test]
fn entry_rejected_when_fault_latched() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    // Latch a fault via BOTH_CLOSED, then restore bypass feedback.
    fb(&mut c, false, false, ms(100));
    assert!(c.fault_latched());
    fb(&mut c, false, true, ms(200));
    // The owner's lease died with the emergency stop; reacquire.
    assert!(c.acquire(&owner, ms(250)));
    c.observe_console_bytes(b"[hmph:0000]", ms(300));
    assert!(!c.request_emulate(&owner, ms(400), true));
    assert_eq!(last_event(&c), "entry_rejected:fault_latched");
}

#[test]
fn entry_rejected_when_uart_not_idle_low() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(!c.request_emulate(&owner, ms(500), false));
    assert_eq!(last_event(&c), "entry_rejected:uart_not_idle_low");
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
}

// py: test_entry_gap_timeout_aborts_without_moving_relay
#[test]
fn entry_gap_timeout_aborts_without_moving_relay() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));

    c.tick(us(S));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(!c.relay_cmd().get());
    assert!(!has_event(&c, "relay_cmd_on", 0)); // K1 never moved
    assert_eq!(last_event(&c), "entry_abort:no_gap");
}

// py: test_gap_event_at_entry_deadline_cannot_leave_tx_enabled
#[test]
fn gap_event_at_entry_deadline_cannot_leave_tx_enabled() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    c.observe_console_bytes(b"[loop:5550]", ms(990));

    assert!(!c.observe_interframe_gap(us(S)));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
}

// py: test_reentrant_entry_request_cannot_rewind_an_active_transfer
#[test]
fn reentrant_entry_request_cannot_rewind_an_active_transfer() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    assert!(c.relay_cmd().get());

    assert!(!c.request_emulate(&owner, ms(105), true));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    c.tick(ms(110));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
}

// py: test_reentrant_entry_request_enforces_feedback_deadline_first
#[test]
fn reentrant_entry_request_enforces_feedback_deadline_first() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));

    assert!(!c.request_emulate(&owner, ms(110), true));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(c.fault_latched());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert_eq!(last_event(&c), "emergency:entry_feedback_timeout");
}

// py: test_entry_feedback_timeout_releases_and_latches_fault
#[test]
fn entry_feedback_timeout_releases_and_latches_fault() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(200)));

    c.tick(ms(210));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.fault_latched());
    assert!(!c.relay_cmd().get());
    assert_eq!(last_event(&c), "emergency:entry_feedback_timeout");
}

// py: test_entry_feedback_mismatch_releases_and_latches_fault
#[test]
fn entry_feedback_mismatch_releases_and_latches_fault() {
    for (nc, no) in [(false, true), (true, true)] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
        assert!(c.request_emulate(&owner, Micros::ZERO, true));
        assert!(c.observe_interframe_gap(ms(200)));

        fb(&mut c, nc, no, ms(205));
        c.tick(ms(210));
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.fault_latched());
        assert!(!c.relay_cmd().get());
        assert_eq!(last_event(&c), "emergency:entry_feedback_timeout");
    }
}

// ── Normal exit ─────────────────────────────────────────────────────

// py: test_complete_normal_exit_order
#[test]
fn complete_normal_exit_order() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    assert!(c.request_normal_exit(&owner, ms(500)));
    assert_eq!(
        last_events(&c, 2),
        vec!["send_and_finish_complete_zero_frame", "wait_exit_gap"]
    );
    assert!(c.relay_cmd().get()); // relay still energized
    assert!(c.observe_interframe_gap(ms(700)));
    assert_eq!(last_event(&c), "relay_cmd_off");
    assert!(c.tx_enable().get()); // tx_enable still true

    fb(&mut c, false, true, ms(705));
    assert_eq!(c.mode(), SafeMode::ExitWaitFeedback);
    fb(&mut c, false, true, ms(706));
    assert_eq!(
        last_events(&c, 3),
        vec!["feedback_bypass_stable", "tx_enable_off", "lease_released"]
    );
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
}

// py: test_normal_exit_gap_timeout_bypasses_immediately_then_checks_feedback
#[test]
fn normal_exit_gap_timeout_bypasses_immediately_then_checks_feedback() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);
    assert!(c.request_normal_exit(&owner, ms(500)));
    c.observe_console_bytes(b"[loop:5550]", ms(1_490));

    c.tick(ms(1_500));
    assert!(!c.relay_cmd().get());
    assert_eq!(c.mode(), SafeMode::ExitWaitFeedback);
    assert_eq!(last_events(&c, 2), vec!["exit_gap_timeout", "relay_cmd_off"]);
}

// py: test_gap_event_at_exit_deadline_cannot_leave_relay_energized
#[test]
fn gap_event_at_exit_deadline_cannot_leave_relay_energized() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);
    assert!(c.request_normal_exit(&owner, ms(500)));
    c.observe_console_bytes(b"[loop:5550]", ms(1_490));

    assert!(!c.observe_interframe_gap(ms(1_500)));
    assert_eq!(c.mode(), SafeMode::ExitWaitFeedback);
    assert!(!c.relay_cmd().get());
    assert!(c.tx_enable().get());
}

// py: test_exit_feedback_mismatch_releases_and_latches_fault
#[test]
fn exit_feedback_mismatch_releases_and_latches_fault() {
    for (nc, no) in [(true, false), (true, true)] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);
        assert!(c.request_normal_exit(&owner, ms(500)));
        assert!(c.observe_interframe_gap(ms(700)));

        fb(&mut c, nc, no, ms(705));
        c.tick(ms(710));
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.fault_latched());
        assert!(!c.relay_cmd().get());
        assert_eq!(last_event(&c), "emergency:exit_feedback_timeout");
    }
}

// py: test_exit_feedback_timeout_latches_fault
#[test]
fn exit_feedback_timeout_latches_fault() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);
    assert!(c.request_normal_exit(&owner, ms(500)));
    assert!(c.observe_interframe_gap(ms(700)));

    c.tick(ms(710));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.fault_latched());
    assert_eq!(last_event(&c), "emergency:exit_feedback_timeout");
}

// py: test_stale_console_cannot_be_raced_by_a_gap_observation
#[test]
fn stale_console_cannot_be_raced_by_a_gap_observation() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, ms(1_490), true));

    assert!(!c.observe_interframe_gap(ms(1_500)));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert_eq!(last_event(&c), "emergency:console_stale");
}

// ── Feedback qualification ──────────────────────────────────────────

// py: test_matching_feedback_requires_temporal_stability
#[test]
fn matching_feedback_requires_temporal_stability() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));

    fb(&mut c, true, false, ms(101));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    assert!(!has_event(&c, "send_first_complete_zero_frame", 0));
    c.tick(us(101_999));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    c.tick(ms(102)); // a timer tick alone NEVER qualifies
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    fb(&mut c, true, false, ms(102));
    assert_eq!(c.mode(), SafeMode::Emulating);
    assert_eq!(
        last_events(&c, 2),
        vec!["feedback_emulate_stable", "send_first_complete_zero_frame"]
    );
}

// py: test_transition_feedback_may_pass_through_both_open_before_settling
#[test]
fn transition_feedback_may_pass_through_both_open_before_settling() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));

    fb(&mut c, true, true, ms(101)); // break-before-make
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    assert!(!c.fault_latched());
    fb(&mut c, true, false, ms(105));
    fb(&mut c, true, false, ms(106));
    assert_eq!(c.mode(), SafeMode::Emulating);
    assert!(!c.fault_latched());
}

// py: test_both_closed_feedback_faults_immediately_during_transfer
#[test]
fn both_closed_feedback_faults_immediately_during_transfer() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));

    fb(&mut c, false, false, ms(101));

    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(c.owner().is_none());
    assert!(c.fault_latched());
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert_eq!(last_event(&c), "emergency:relay_feedback_both_closed");
}

// py: (BOTH_CLOSED is a latched fault in every mode)
#[test]
fn both_closed_latches_a_fault_and_releases_in_every_mode() {
    // PROXY
    {
        let mut c = SafetyController::new();
        fb(&mut c, false, false, Micros::ZERO);
        assert!(c.fault_latched());
        assert!(!c.relay_cmd().get());
        assert_eq!(last_event(&c), "emergency:relay_feedback_both_closed");
    }
    // EMULATING
    {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);
        fb(&mut c, false, false, ms(500));
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.fault_latched());
        assert!(!c.relay_cmd().get());
        assert!(!c.tx_enable().get());
    }
}

// py: test_feedback_qualification_requires_a_sample_at_stability_time
#[test]
fn feedback_qualification_requires_a_sample_at_stability_time() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    assert!(c.request_emulate(&owner, Micros::ZERO, true));
    assert!(c.observe_interframe_gap(ms(100)));
    fb(&mut c, true, false, ms(105));

    c.tick(ms(106));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    fb(&mut c, true, false, ms(106));
    assert_eq!(c.mode(), SafeMode::Emulating);
}

// py: test_feedback_at_exact_deadline_always_fails_closed
#[test]
fn feedback_at_exact_10_ms_deadline_always_fails_closed() {
    for via_tick in [true, false] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
        assert!(c.request_emulate(&owner, Micros::ZERO, true));
        assert!(c.observe_interframe_gap(ms(100)));
        fb(&mut c, true, false, ms(108));

        if via_tick {
            c.tick(ms(110));
        } else {
            fb(&mut c, true, false, ms(110));
        }

        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.owner().is_none());
        assert!(c.fault_latched());
        assert!(!c.relay_cmd().get());
        assert!(!c.tx_enable().get());
    }
}

// py: test_console_staleness_never_waits_for_a_transition_deadline
#[test]
fn console_staleness_never_waits_for_a_transition_deadline() {
    for target in [
        SafeMode::EntryWaitGap,
        SafeMode::EntryWaitFeedback,
        SafeMode::ExitWaitGap,
        SafeMode::ExitWaitFeedback,
    ] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
        let entry_mode =
            target == SafeMode::EntryWaitGap || target == SafeMode::EntryWaitFeedback;
        let entry_time = if entry_mode { ms(1_490) } else { Micros::ZERO };
        assert!(c.request_emulate(&owner, entry_time, true));
        if target == SafeMode::EntryWaitFeedback {
            assert!(c.observe_interframe_gap(ms(1_495)));
        }
        if target == SafeMode::ExitWaitGap || target == SafeMode::ExitWaitFeedback {
            assert!(c.observe_interframe_gap(ms(100)));
            fb(&mut c, true, false, ms(105));
            fb(&mut c, true, false, ms(106));
            assert!(c.request_normal_exit(&owner, ms(600)));
        }
        if target == SafeMode::ExitWaitFeedback {
            assert!(c.observe_interframe_gap(ms(1_495)));
        }
        assert_eq!(c.mode(), target);

        c.tick(ms(1_500));
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.owner().is_none());
        assert!(!c.relay_cmd().get());
        assert!(!c.tx_enable().get());
        assert_eq!(last_event(&c), "emergency:console_stale");
    }
}

// ── Emergency / feedback decode / tread_ok / USB / boot ─────────────

// py: test_console_bridge_failure_matrix_remains_hardware_proxy
#[test]
fn console_bridge_failure_matrix_remains_hardware_proxy() {
    for watchdog in [false, true] {
        let mut c = SafetyController::new();
        if watchdog {
            c.watchdog_stall(us(S));
        } else {
            c.reset(us(S), "brownout");
        }
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.owner().is_none());
        assert!(!c.relay_cmd().get());
        assert!(!c.tx_enable().get());
    }
}

// py: test_emergency_paths_never_wait_for_a_gap
#[test]
fn emergency_paths_never_wait_for_a_gap() {
    let reasons = [
        "tread_not_ok",
        "console_stale",
        "explicit_emergency_stop",
        "brownout",
        "reset",
        "watchdog",
    ];
    for reason in reasons {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);
        let before = c.event_count();

        c.emergency_stop(reason, ms(500));

        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(!c.relay_cmd().get());
        assert!(!c.tx_enable().get());
        for i in before..c.event_count() {
            assert!(
                !c.event_at(i).unwrap_or("").contains("wait"),
                "reason={reason}"
            );
        }
    }
}

// py: test_all_four_relay_feedback_states_are_decoded
#[test]
fn all_four_relay_feedback_states_are_decoded() {
    assert_eq!(
        feedback_from_gpio(NcHigh(false), NoHigh(true)),
        Feedback::Bypass
    );
    assert_eq!(
        feedback_from_gpio(NcHigh(true), NoHigh(false)),
        Feedback::Emulate
    );
    assert_eq!(
        feedback_from_gpio(NcHigh(false), NoHigh(false)),
        Feedback::BothClosed
    );
    assert_eq!(
        feedback_from_gpio(NcHigh(true), NoHigh(true)),
        Feedback::BothOpen
    );
}

// py: test_any_non_emulate_feedback_while_emulating_is_a_fault
#[test]
fn any_non_emulate_feedback_while_emulating_is_a_fault() {
    for (nc, no) in [(false, true), (false, false), (true, true)] {
        let owner = default_identity();
        let mut c = connected_controller(&owner);
        enter_emulate(&mut c, &owner, Micros::ZERO);

        fb(&mut c, nc, no, ms(500));
        assert_eq!(c.mode(), SafeMode::Proxy);
        assert!(c.fault_latched());
    }
}

// py: test_tread_ok_loss_is_hardware_permission_loss_and_immediate
#[test]
fn tread_ok_loss_is_hardware_permission_loss_and_immediate() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    c.set_tread_ok(TreadOk(false), ms(500));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert!(!c.relay_cmd().get());
    assert_eq!(last_event(&c), "emergency:tread_not_ok");
}

// py: test_native_usb_attach_is_active_low_and_defaults_detached
#[test]
fn native_usb_attach_is_active_low_and_defaults_detached() {
    let mut c = SafetyController::new();
    assert!(!c.usb_pullup_enabled());

    c.set_vbus_present_n(true);
    assert!(!c.usb_pullup_enabled());
    c.set_vbus_present_n(false);
    assert!(c.usb_pullup_enabled());
    c.set_vbus_present_n(true);
    assert!(!c.usb_pullup_enabled());
}

// py: test_reset_requires_an_actual_bypass_feedback_sample_before_entry
#[test]
fn reset_requires_an_actual_bypass_feedback_sample_before_entry() {
    let old = default_identity();
    let mut c = connected_controller(&old);
    enter_emulate(&mut c, &old, Micros::ZERO);
    c.reset(ms(500), "reset");

    assert_eq!(c.feedback(), Feedback::Unknown);
    let fresh = identity(Transport::Wss, 100, 2);
    assert!(c.connect(&fresh));
    assert!(c.acquire(&fresh, ms(600)));
    c.observe_console_bytes(b"[hmph:0000]", ms(600));
    assert!(!c.request_emulate(&fresh, ms(600), true));
    fb(&mut c, false, true, ms(700));
    assert!(c.request_emulate(&fresh, ms(700), true));
}

// py: test_model_constants_are_the_normative_deadlines
#[test]
fn model_constants_are_the_normative_deadlines() {
    assert_eq!(CONSOLE_FRESH_US.get(), 1_500_000);
    assert_eq!(TRANSFER_GAP_DEADLINE_US.get(), 1_000_000);
    assert_eq!(RELAY_FEEDBACK_DEADLINE_US.get(), 10_000);
    assert_eq!(RELAY_FEEDBACK_STABLE_US.get(), 1_000);
    assert_eq!(WDT_US.get(), 2_000_000);
    assert_eq!(TREAD_OK_TO_NC_MAX_US.get(), 10_000);
    assert_eq!(SOFTWARE_TO_NC_MAX_US.get(), 250_000);
    assert_eq!(WDT_TO_NC_MAX_US.get(), 2_250_000);
    assert_eq!(NORMAL_TRANSITION_ACCEPTANCE_CYCLES, 1_000);
    assert_eq!(SPEED_MAX_TENTHS.get(), 120);
    assert_eq!(INCLINE_APP_MAX_HALF.get(), 30);
    assert_eq!(INCLINE_ABS_MAX_HALF.get(), 198);
}

// Boot state (PLAN: boot = proxy, feedback unknown, no bypass assumption)
// py: (no model twin — safety_model.py has no boot-state vector; the closest,
// test_reset_requires_an_actual_bypass_feedback_sample_before_entry, asserts
// post-RESET state, not construction). This parenthesised marker is
// LOAD-BEARING: check_case_parity.py maps a test to its model twin by
// NEAREST-PRECEDING `// py:` comment, so without it this case would silently
// inherit the previous case's `test_model_constants_are_the_normative_deadlines`.
#[test]
fn boot_state_is_proxy_with_unknown_feedback_and_no_outputs() {
    let c = SafetyController::new();
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert_eq!(c.feedback(), Feedback::Unknown);
    assert!(!c.relay_cmd().get());
    assert!(!c.tx_enable().get());
    assert!(!c.fault_latched());
    assert!(c.owner().is_none());
    assert_eq!(c.speed_tenths(), tenths(0));
    assert_eq!(c.incline_half_percent(), half(0));
    assert!(c.last_complete_console_frame_at().is_none());
    // tread_ok boots TRUE.
    assert!(c.tread_ok().get());
}

// py: (watchdog_stall clears console timestamp and feedback)
#[test]
fn watchdog_stall_clears_connections_console_and_feedback_state() {
    let owner = default_identity();
    let mut c = connected_controller(&owner);
    enter_emulate(&mut c, &owner, Micros::ZERO);

    c.watchdog_stall(ms(500));
    assert_eq!(c.mode(), SafeMode::Proxy);
    assert_eq!(c.feedback(), Feedback::Unknown);
    assert!(c.last_complete_console_frame_at().is_none());
    assert!(!c.usb_pullup_enabled());
    // The pre-stall connection is gone: acquire requires a reconnect.
    assert!(!c.acquire(&owner, ms(600)));
}

// ── Rust-only extra: the untrusted-boundary identity form ───────────

// RUST-ONLY EXTRA (listed in tools/check_case_parity.py RUST_ONLY_EXTRA).
//
// The C++ validates `generation < 0` INSIDE `connect()`
// (safety_controller.cpp), i.e. on the only path, so the rejection is live in
// the shipped image and needs no separate case. This port makes an invalid
// identity UNREPRESENTABLE (`Generation::new` returns None), which moves the
// rejection to the boundary form `connect_raw`. That is an improvement, but
// an untested boundary check is not a check: without this vector the C++
// event string `connection_rejected:invalid_identity` would be unreachable
// AND unexercised in the Rust firmware. `connect_raw` is reserved for the
// M5 network tier, where identities arrive from the wire.
// py: (no model twin — see RUST_ONLY_EXTRA in tools/check_case_parity.py).
// Same nearest-preceding rule as the boot-state case above: without this
// marker the annotation from the previous case would leak into this one.
#[test]
fn connect_raw_rejects_a_negative_generation() {
    let mut c = SafetyController::new();

    // Negative generation: refused at the boundary, with the C++ event text.
    assert!(!c.connect_raw(Transport::Wss, 100, -1));
    assert_eq!(last_event(&c), "connection_rejected:invalid_identity");
    // Nothing was registered: the identity cannot go on to take a lease.
    assert!(!c.acquire(&default_identity(), Micros::ZERO));

    // Same boundary, valid generation: behaves exactly like `connect`.
    assert!(c.connect_raw(Transport::Wss, 100, 1));
    assert_eq!(last_event(&c), "connected:WSS:100:1");
    assert!(c.acquire(&identity(Transport::Wss, 100, 1), Micros::ZERO));
    assert_eq!(c.owner(), Some(identity(Transport::Wss, 100, 1)));

    // And the stale-generation rule still applies through it.
    assert!(!c.connect_raw(Transport::Wss, 100, 0));
    assert_eq!(last_event(&c), "connection_rejected:stale_generation");
}
