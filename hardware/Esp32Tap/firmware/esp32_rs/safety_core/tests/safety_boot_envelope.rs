//! Port of `host/tests/test_safety_boot_envelope.cpp` — 11 cases, 1:1 by name.
//!
//! Integration-style safety-envelope tests through the fake HAL: output edge
//! ORDER on emulate entry (tx_enable before relay_cmd; first TX bytes only
//! after feedback qualification), zero-frame content, boot output state, and
//! the 3-hour timeout through the emulate cycle with a fake clock.
//!
//! Cases 9/10 (`SerialCadenceSim`) are the ONLY coverage of the feedback
//! window cadence — the regression guard for the unsatisfiable-10 ms
//! qualification bug. Rust does not prevent that bug; these cases do.

mod common;

use common::*;
use safety_core::cycle::{EmulationCycle, KvSink};
use safety_core::emulate_policy::EmulateTaskPolicy;
use safety_core::kv::{encode_incline_hex, encode_speed_hex, kv_build};
use safety_core::mode::ModeStateMachine;
use safety_core::safety::constants::{EMULATE_TIMEOUT_US, FEEDBACK_POLL_US, GAP_QUALIFY_US};
use safety_core::safety::controller::{
    ConnectionIdentity, Feedback, SafeMode, SafetyController, Transport,
};
use safety_core::safety::{in_feedback_wait, run_feedback_window};
use safety_core::units::*;

/// Minimal bridge mirroring the firmware tasks: sample HAL inputs into the
/// controller, then apply controller outputs back to the HAL. Same order the
/// serial engine uses.
struct Bridge {
    controller: SafetyController,
    clock: FakeClock,
    io: FakeSafetyIo,
    port: FakePort,
}

impl Bridge {
    fn new() -> Self {
        let clock = FakeClock::new();
        Bridge {
            controller: SafetyController::new(),
            io: FakeSafetyIo::new(clock.clone()),
            port: FakePort::new(clock.clone()),
            clock,
        }
    }

    fn sample_feedback(&mut self) {
        let (nc, no, t) = (self.io.k1_nc_high(), self.io.k1_no_high(), self.clock.now());
        self.controller.observe_relay_feedback(nc, no, t);
        self.apply_outputs();
    }

    /// The ONE output write site. `FakeSafetyIo::apply` writes tx before relay.
    fn apply_outputs(&mut self) {
        self.io.apply(self.controller.output_intent());
    }

    /// Emit the entry zero frame the way the emulate task does after the
    /// controller reports EMULATING.
    fn send_zero_frame(&mut self) {
        let mut w = PortWriter::new(&mut self.port);
        w.write_kv("inc", encode_incline_hex(half(0)).as_str());
        w.write_kv("hmph", encode_speed_hex(tenths(0)).as_str());
    }
}

// cpp: "boot: outputs low, proxy, unknown feedback, no TX"
#[test]
fn boot_outputs_low_proxy_unknown_feedback_no_tx() {
    let mut b = Bridge::new();
    b.apply_outputs();
    assert!(!b.io.relay_cmd);
    assert!(!b.io.tx_en);
    assert_eq!(b.controller.mode(), SafeMode::Proxy);
    assert_eq!(b.controller.feedback(), Feedback::Unknown);
    assert!(b.port.writes.is_empty());
}

// cpp: "entry edge order: tx_enable on before relay_cmd on, TX only after qualification"
#[test]
fn entry_edge_order_tx_enable_on_before_relay_cmd_on_tx_only_after_qualification() {
    let mut b = Bridge::new();
    let owner = identity(Transport::Wss, 1, 1);

    b.io.set_feedback_bypass();
    b.sample_feedback();
    assert!(b.controller.connect(&owner));
    assert!(b.controller.acquire(&owner, b.clock.now()));
    b.controller
        .observe_console_bytes(b"[hmph:0000]", b.clock.now());

    let idle = b.port.tx_idle_low();
    assert!(b.controller.request_emulate(&owner, b.clock.now(), idle));
    b.apply_outputs();
    assert!(b.io.tx_en);
    assert!(!b.io.relay_cmd); // relay must NOT move before the gap
    assert!(b.port.writes.is_empty()); // no byte sent while waiting

    // Gap observed at t=100ms.
    b.clock.advance_ms(100);
    assert!(b.controller.observe_interframe_gap(b.clock.now()));
    b.apply_outputs();
    assert!(b.io.relay_cmd);
    assert!(b.port.writes.is_empty()); // still no TX before qualification

    // Feedback settles to EMULATE; two samples 1 ms apart qualify.
    b.io.set_feedback_emulate();
    b.clock.advance_ms(5);
    b.sample_feedback();
    b.clock.advance_ms(1);
    b.sample_feedback();
    assert_eq!(b.controller.mode(), SafeMode::Emulating);

    // Only NOW does the emulate task send the first complete zero frame.
    b.send_zero_frame();

    let order: Vec<&str> = b.io.edges.iter().map(|e| e.what.as_str()).collect();
    assert!(order.len() >= 2);
    assert_eq!(order[0], "tx_enable:1");
    assert_eq!(order[1], "relay_cmd:1");
    assert!(!b.port.writes.is_empty());
    assert!(b.port.writes[0].at_us >= b.io.edges[1].at_us);
}

// cpp: "entry zero frame content uses the forked codecs"
#[test]
fn entry_zero_frame_content_uses_the_forked_codecs() {
    let mut b = Bridge::new();
    b.send_zero_frame();
    let written = b.port.all_written();
    assert_eq!(written, b"[inc:0]\xff[hmph:0]\xff".to_vec());
    assert_eq!(encode_speed_hex(tenths(0)), *"0");
    assert_eq!(encode_incline_hex(half(0)), *"0");
}

// cpp: "watchdog stall path releases outputs through the HAL"
#[test]
fn watchdog_stall_path_releases_outputs_through_the_hal() {
    let mut b = Bridge::new();
    let owner = identity(Transport::Wss, 1, 1);
    b.io.set_feedback_bypass();
    b.sample_feedback();
    assert!(b.controller.connect(&owner));
    assert!(b.controller.acquire(&owner, b.clock.now()));
    b.controller
        .observe_console_bytes(b"[hmph:0000]", b.clock.now());
    assert!(b.controller.request_emulate(&owner, b.clock.now(), true));
    b.apply_outputs();
    b.clock.advance_ms(100);
    assert!(b.controller.observe_interframe_gap(b.clock.now()));
    b.apply_outputs();
    b.io.set_feedback_emulate();
    b.clock.advance_ms(5);
    b.sample_feedback();
    b.clock.advance_ms(1);
    b.sample_feedback();
    assert_eq!(b.controller.mode(), SafeMode::Emulating);
    assert!(b.io.relay_cmd);

    // Supervisor detects a stall: the model's watchdog action. (On hardware
    // the panic reset itself releases GPIO21 via the pull-down; this asserts
    // the controller-side contract for the same event.)
    b.controller.watchdog_stall(b.clock.now());
    b.apply_outputs();
    assert!(!b.io.relay_cmd);
    assert!(!b.io.tx_en);
    assert_eq!(b.controller.mode(), SafeMode::Proxy);
    assert_eq!(b.controller.feedback(), Feedback::Unknown);
}

// cpp: "tread_ok loss releases outputs immediately"
#[test]
fn tread_ok_loss_releases_outputs_immediately() {
    let mut b = Bridge::new();
    let owner = identity(Transport::Executor, 3, 1);
    b.io.set_feedback_bypass();
    b.sample_feedback();
    assert!(b.controller.connect(&owner));
    assert!(b.controller.acquire(&owner, b.clock.now()));
    b.controller
        .observe_console_bytes(b"[hmph:0000]", b.clock.now());
    assert!(b.controller.request_emulate(&owner, b.clock.now(), true));
    b.apply_outputs();
    assert!(b.io.tx_en);

    b.io.tread_ok_level = false;
    let (t, now) = (b.io.tread_ok(), b.clock.now());
    b.controller.set_tread_ok(t, now);
    b.apply_outputs();
    assert!(!b.io.relay_cmd);
    assert!(!b.io.tx_en);
    assert_eq!(b.controller.mode(), SafeMode::Proxy);
}

// --- emulate task harness -------------------------------------------------

/// Replicates the emulate-cycle task's per-iteration body against the
/// authoritative controller, routing arm / force-proxy / mirror / send through
/// the SAME `EmulateTaskPolicy` the firmware task uses (first-burst-zero gate
/// included).
///
/// `mirror_first` selects the real task ordering (mirror under the lock, tick
/// after); `false` simulates a future reorder — the safety outcome must not
/// depend on it.
struct EmuTaskHarness {
    b: Bridge,
    mode: ModeStateMachine,
    cycle: EmulationCycle,
    policy: EmulateTaskPolicy,
}

impl EmuTaskHarness {
    fn new() -> Self {
        EmuTaskHarness {
            b: Bridge::new(),
            mode: ModeStateMachine::new(),
            cycle: EmulationCycle::new(),
            policy: EmulateTaskPolicy::new(),
        }
    }

    fn mirror(&mut self) {
        self.mode.set_speed(self.b.controller.speed_tenths());
        self.mode.set_incline(self.b.controller.incline_half_percent());
    }

    fn tick_and_consume(&mut self) {
        let now = self.b.clock.now();
        let sent = {
            let mut w = PortWriter::new(&mut self.b.port);
            self.cycle.tick(now, &mut self.mode, &mut w)
        };
        if sent {
            self.policy.on_burst_sent();
        }
        // The token can only come from an actual timeout — that is what makes
        // the back-mirror impossible to invoke speculatively.
        if let Some(proof) = self.cycle.consume_safety_timeout() {
            self.b.controller.safety_timeout_zero_motion(proof, now);
        }
    }

    fn iterate(&mut self, mirror_first: bool) {
        let session = self.b.controller.emulate_session();
        let d = self.policy.step(session, self.mode.is_emulating());
        if d.arm {
            self.mode.request_emulate(true);
            self.cycle.reset(self.b.clock.now());
        } else if d.force_proxy {
            self.mode.watchdog_reset_to_proxy();
        }
        if !d.send_burst {
            return;
        }
        if mirror_first {
            if d.mirror {
                self.mirror();
            }
            self.tick_and_consume();
        } else {
            self.tick_and_consume();
            if d.mirror {
                self.mirror();
            }
        }
    }

    /// Drive the controller into EMULATING with owner motion 50/10.
    fn enter_emulating_at_speed(&mut self) {
        let owner = identity(Transport::Executor, 3, 1);
        self.b.io.set_feedback_bypass();
        self.b.sample_feedback();
        assert!(self.b.controller.connect(&owner));
        assert!(self.b.controller.acquire(&owner, self.b.clock.now()));
        self.b
            .controller
            .observe_console_bytes(b"[hmph:0000]", self.b.clock.now());
        assert!(self
            .b
            .controller
            .request_emulate(&owner, self.b.clock.now(), true));
        self.b.apply_outputs();
        self.b.clock.advance_ms(100);
        assert!(self.b.controller.observe_interframe_gap(self.b.clock.now()));
        self.b.apply_outputs();
        self.b.io.set_feedback_emulate();
        self.b.clock.advance_ms(5);
        self.b.sample_feedback();
        self.b.clock.advance_ms(1);
        self.b.sample_feedback();
        assert_eq!(self.b.controller.mode(), SafeMode::Emulating);
        assert!(self.b.controller.command_motion(
            &owner,
            tenths(50),
            half(10),
            self.b.clock.now()
        ));
    }

    fn controller_has_event(&self, text: &str) -> bool {
        has_event(&self.b.controller, text, 0)
    }
}

fn contains(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

// cpp: "3-hour timeout zeroes the authoritative controller too (task iteration order)"
#[test]
fn three_hour_timeout_zeroes_the_authoritative_controller_too_task_iteration_order() {
    let mut h = EmuTaskHarness::new();
    h.enter_emulating_at_speed();

    // Normal iterations: after the entry zero burst, motion is mirrored and
    // encoded nonzero when burst 0 comes around again (10 iterations = two
    // full 5-burst cycles).
    for _ in 0..10 {
        h.iterate(true);
        h.b.clock.advance_ms(100);
    }
    assert_eq!(h.b.controller.speed_tenths(), tenths(50));
    assert_eq!(h.mode.speed_tenths(), tenths(50));
    // 50 tenths = 5.0 mph -> hmph = 500 = 0x1F4
    assert!(contains(&h.b.port.all_written(), b"[hmph:1F4]"));

    // 3 hours of no changes.
    h.b.clock.advance_us(EMULATE_TIMEOUT_US.get());
    h.b.port.clear();
    h.iterate(true);

    // SAME iteration: mode zeroed by the cycle engine AND the authoritative
    // controller zeroed via the timeout token — no split-brain window into
    // the next mirror.
    assert_eq!(h.mode.speed_tenths(), tenths(0));
    assert_eq!(h.mode.incline(), half(0));
    assert_eq!(h.b.controller.speed_tenths(), tenths(0));
    assert_eq!(h.b.controller.incline_half_percent(), half(0));
    assert!(h.controller_has_event("safety_timeout_zero_motion"));
    // The timeout does not change mode/lease/relay/TX: still emulating at zero.
    let owner = identity(Transport::Executor, 3, 1);
    assert_eq!(h.b.controller.owner(), Some(owner));
    assert_eq!(h.b.controller.mode(), SafeMode::Emulating);
    assert!(h.b.controller.relay_cmd().get());
    assert!(h.b.controller.tx_enable().get());
    assert!(h.b.io.relay_cmd);
    assert!(h.b.io.tx_en);

    // The wire only ever carries zero motion from now on.
    for _ in 0..5 {
        h.b.clock.advance_ms(100);
        h.iterate(true);
    }
    let wire = h.b.port.all_written();
    assert!(contains(&wire, b"[inc:0]\xff"));
    assert!(contains(&wire, b"[hmph:0]\xff"));
    assert!(!contains(&wire, b"[hmph:1F4]"));
}

// cpp: "3-hour timeout: a reordered mirror cannot resurrect stale motion"
#[test]
fn three_hour_timeout_a_reordered_mirror_cannot_resurrect_stale_motion() {
    let mut h = EmuTaskHarness::new();
    h.enter_emulating_at_speed();
    for _ in 0..5 {
        h.iterate(false);
        h.b.clock.advance_ms(100);
    }
    assert_eq!(h.mode.speed_tenths(), tenths(50));

    h.b.clock.advance_us(EMULATE_TIMEOUT_US.get());
    h.iterate(false);
    // The tick fired the timeout and the controller was zeroed BEFORE the
    // (reordered) mirror ran, so the mirror writes zeros — not 50.
    assert_eq!(h.b.controller.speed_tenths(), tenths(0));
    assert_eq!(h.mode.speed_tenths(), tenths(0));

    h.b.port.clear();
    for _ in 0..5 {
        h.b.clock.advance_ms(100);
        h.iterate(false);
    }
    let wire = h.b.port.all_written();
    assert!(contains(&wire, b"[hmph:0]\xff"));
    assert!(!contains(&wire, b"[hmph:1F4]"));
}

// cpp: "PLAN entry step 6: first transmitted burst is zero despite motion
//       commanded during the entry window"
#[test]
fn plan_entry_step_6_first_transmitted_burst_is_zero_despite_motion_commanded_during_the_entry_window(
) {
    // The controller accepts owner command_motion during ENTRY_WAIT_*
    // (faithful to safety_model.py), so it can reach EMULATING already
    // holding nonzero motion. The task layer must still transmit the first
    // post-entry burst as the zero frame.
    let mut h = EmuTaskHarness::new();
    let owner = identity(Transport::Wss, 7, 1);
    h.b.io.set_feedback_bypass();
    h.b.sample_feedback();
    assert!(h.b.controller.connect(&owner));
    assert!(h.b.controller.acquire(&owner, h.b.clock.now()));
    h.b.controller
        .observe_console_bytes(b"[hmph:0000]", h.b.clock.now());
    assert!(h
        .b
        .controller
        .request_emulate(&owner, h.b.clock.now(), true));
    assert_eq!(h.b.controller.mode(), SafeMode::EntryWaitGap);
    // The owner commands motion while the entry is still in flight.
    assert!(h.b.controller.command_motion(
        &owner,
        tenths(50),
        half(10),
        h.b.clock.now()
    ));
    h.b.apply_outputs();
    h.b.clock.advance_ms(100);
    assert!(h.b.controller.observe_interframe_gap(h.b.clock.now()));
    h.b.apply_outputs();
    h.b.io.set_feedback_emulate();
    h.b.clock.advance_ms(5);
    h.b.sample_feedback();
    h.b.clock.advance_ms(1);
    h.b.sample_feedback();
    assert_eq!(h.b.controller.mode(), SafeMode::Emulating);
    // Nonzero owner motion is ALREADY present at EMULATING onset.
    assert_eq!(h.b.controller.speed_tenths(), tenths(50));
    assert_eq!(h.b.controller.incline_half_percent(), half(10));

    // First task iteration after entry: arm + transmit the first burst.
    h.iterate(true);
    assert!(h.b.port.writes.len() >= 2);
    // Burst 0 = inc, hmph — the first complete frame MUST encode zero.
    assert_eq!(h.b.port.writes[0].bytes, b"[inc:0]\xff".to_vec());
    assert_eq!(h.b.port.writes[1].bytes, b"[hmph:0]\xff".to_vec());

    // After the zero burst went out, owner motion is mirrored and reaches the
    // wire when the motion burst comes around again.
    for _ in 0..6 {
        h.b.clock.advance_ms(100);
        h.iterate(true);
    }
    assert_eq!(h.mode.speed_tenths(), tenths(50));
    assert_eq!(h.mode.incline(), half(10));
    let wire = h.b.port.all_written();
    assert!(contains(&wire, b"[hmph:1F4]"));
    assert!(contains(&wire, b"[inc:A]"));
    // The very first frames on the wire were the zero frames.
    assert!(wire.starts_with(b"[inc:0]\xff[hmph:0]\xff"));
}

// RUST-ONLY EXTRA (no C++ twin — the C++ policy has this defect).
//
// PLAN entry step 6 must hold for the SECOND emulate session too. A gap-safe
// normal exit + re-acquire + second entry fits inside ONE 100 ms emulate-task
// period (20 ms exit gap, ~1.2 ms exit feedback, the entry gap is then already
// satisfied because `now - last_console_rx` is still >= GAP_QUALIFY_US, ~1.2 ms
// entry feedback), and the console's own ~100 ms inter-burst gaps make that
// silence normal. The C++ `EmulateTaskPolicy` samples a BOOL, reads
// `true, true` across the whole exit+re-entry, never arms, never calls
// `cycle.reset()`, and mirrors the owner's motion into the FIRST burst of the
// second session. This asserts the Rust policy's `EmulateSessionId` closes it.
#[test]
fn plan_entry_step_6_holds_for_a_re_entry_inside_one_emulate_task_period() {
    let mut h = EmuTaskHarness::new();
    let owner = identity(Transport::Executor, 3, 1);
    h.enter_emulating_at_speed();
    let session1 = h.b.controller.emulate_session();

    // Session 1 runs normally: the entry zero burst goes out, then motion.
    for _ in 0..6 {
        h.iterate(true);
        h.b.clock.advance_ms(100);
    }
    assert!(contains(&h.b.port.all_written(), b"[hmph:1F4]"));

    // ---- everything from here to the next iterate() happens BETWEEN two
    // ---- emulate-task samples, i.e. inside one 100 ms period.

    // Gap-safe normal exit.
    h.b.controller
        .observe_console_bytes(b"[hmph:0000]", h.b.clock.now());
    assert!(h.b.controller.request_normal_exit(&owner, h.b.clock.now()));
    h.b.clock.advance_us(GAP_QUALIFY_US.get());
    assert!(h.b.controller.observe_interframe_gap(h.b.clock.now()));
    h.b.apply_outputs();
    h.b.io.set_feedback_bypass();
    h.b.clock.advance_us(200);
    h.b.sample_feedback();
    // >= RELAY_FEEDBACK_STABLE_US after the candidate, and strictly before
    // the 10 ms deadline: the same sub-ms window the serial engine runs.
    h.b.clock.advance_us(1_000);
    h.b.sample_feedback();
    assert_eq!(h.b.controller.mode(), SafeMode::Proxy);

    // The 5 ms serial engine — NOT this task — notices the controller left
    // EMULATING and forces the cycle parameter engine back to Proxy.
    h.mode.watchdog_reset_to_proxy();

    // Re-acquire and enter again, still without an emulate-task iteration.
    assert!(h.b.controller.acquire(&owner, h.b.clock.now()));
    h.b.controller
        .observe_console_bytes(b"[hmph:0000]", h.b.clock.now());
    assert!(h
        .b
        .controller
        .request_emulate(&owner, h.b.clock.now(), true));
    h.b.apply_outputs();
    h.b.clock.advance_us(GAP_QUALIFY_US.get());
    assert!(h.b.controller.observe_interframe_gap(h.b.clock.now()));
    h.b.apply_outputs();
    h.b.io.set_feedback_emulate();
    h.b.clock.advance_us(200);
    h.b.sample_feedback();
    h.b.clock.advance_us(1_000);
    h.b.sample_feedback();
    assert_eq!(h.b.controller.mode(), SafeMode::Emulating);

    // A NEW session — this is the fact a bool cannot carry.
    let session2 = h.b.controller.emulate_session();
    assert!(session2.is_some());
    assert_ne!(session1, session2);

    // The owner commands motion again before the task ever runs.
    assert!(h.b.controller.command_motion(
        &owner,
        tenths(50),
        half(10),
        h.b.clock.now()
    ));

    // ---- the next emulate-task sample.
    h.b.port.clear();
    h.iterate(true);

    // Burst 0 of the SECOND session must be the zero frame, exactly as it was
    // for the first (PLAN entry step 6).
    assert!(h.b.port.writes.len() >= 2);
    assert_eq!(h.b.port.writes[0].bytes, b"[inc:0]\xff".to_vec());
    assert_eq!(h.b.port.writes[1].bytes, b"[hmph:0]\xff".to_vec());

    // And the 3-hour no-change timer was re-armed with the session (a stale
    // `last_activity_us` can only make it fire EARLY, but "fail-safe" is not
    // the same as "correct").
    for _ in 0..6 {
        h.b.clock.advance_ms(100);
        h.iterate(true);
    }
    assert_eq!(h.mode.speed_tenths(), tenths(50));
    let wire = h.b.port.all_written();
    assert!(wire.starts_with(b"[inc:0]\xff[hmph:0]\xff"));
    assert!(contains(&wire, b"[hmph:1F4]"));
}

// --- serial cadence simulator ---------------------------------------------

/// Drives the controller exactly like `main/serial_engine_task.cpp`: 5 ms
/// coarse iterations (top-of-loop tread_ok + feedback samples, console gap
/// qualification after `GAP_QUALIFY_US` of RX silence, tick, apply), then the
/// dedicated sub-ms feedback window while a relay transfer is in flight.
///
/// The fake relay follows RELAY_CMD instantly, so qualification timing is
/// PURELY the software cadence under test.
/// The window's single IO handle for the cadence simulator.
///
/// In C++ this is five lambdas all capturing the same `Bridge&`; Rust requires
/// the read and write paths to live behind one `&mut`, which this type
/// provides. The fake relay follows RELAY_CMD instantly.
struct CadenceWindowIo<'a> {
    clock: &'a FakeClock,
    io: &'a mut FakeSafetyIo,
}

impl safety_core::safety::FeedbackWindowIo for CadenceWindowIo<'_> {
    fn now(&self) -> Micros {
        self.clock.now()
    }
    fn tread_ok(&self) -> TreadOk {
        self.io.tread_ok()
    }
    fn nc(&self) -> NcHigh {
        NcHigh(self.io.relay_cmd) // energized -> NC open
    }
    fn no(&self) -> NoHigh {
        NoHigh(!self.io.relay_cmd) // energized -> NO closed
    }
    fn apply(&mut self, intent: safety_core::safety::OutputIntent) {
        self.io.apply(intent);
    }
    fn delay(&mut self) {
        self.clock.advance_us(FEEDBACK_POLL_US.get());
    }
}

struct SerialCadenceSim {
    last_console_rx_us: Micros,
}

impl SerialCadenceSim {
    fn new() -> Self {
        SerialCadenceSim {
            last_console_rx_us: Micros::ZERO,
        }
    }

    fn iteration(&mut self, b: &mut Bridge) {
        let now = b.clock.now();
        let t = b.io.tread_ok();
        b.controller.set_tread_ok(t, now);
        // energized -> NC open, NO closed
        let nc = NcHigh(b.io.relay_cmd);
        let no = NoHigh(!b.io.relay_cmd);
        b.controller.observe_relay_feedback(nc, no, now);
        let m = b.controller.mode();
        if (m == SafeMode::EntryWaitGap || m == SafeMode::ExitWaitGap)
            && now - self.last_console_rx_us >= GAP_QUALIFY_US
        {
            b.controller.observe_interframe_gap(now);
        }
        b.controller.tick(now);
        b.apply_outputs();
        if in_feedback_wait(&b.controller) {
            let Bridge {
                controller,
                clock,
                io,
                ..
            } = b;
            let mut wio = CadenceWindowIo { clock, io };
            run_feedback_window(controller, &mut wio);
        }
        b.clock.advance_ms(5);
    }
}

/// Entry driven purely by the real task cadence — no hand-advanced
/// qualification samples.
fn drive_entry_at_task_cadence(
    b: &mut Bridge,
    sim: &mut SerialCadenceSim,
    owner: &ConnectionIdentity,
) {
    b.controller
        .observe_console_bytes(b"[hmph:0000]", b.clock.now());
    sim.last_console_rx_us = b.clock.now();
    sim.iteration(b); // establishes the real BYPASS feedback sample
    assert!(b.controller.connect(owner));
    assert!(b.controller.acquire(owner, b.clock.now()));
    let idle = b.port.tx_idle_low();
    assert!(b.controller.request_emulate(owner, b.clock.now(), idle));
    for _ in 0..20 {
        if b.controller.mode() == SafeMode::Emulating {
            break;
        }
        sim.iteration(b);
    }
}

// cpp: "gap-safe ENTRY completes at the real task cadence (5 ms loop + sub-ms feedback window)"
#[test]
fn gap_safe_entry_completes_at_the_real_task_cadence_5_ms_loop_sub_ms_feedback_window() {
    // Regression for the unsatisfiable-10ms-qualification bug: at a pure 5 ms
    // sampling cadence the first feedback sample lands ~+5 ms after relay_cmd
    // and the next at ~+10 ms — EXACTLY the fail-closed deadline — so every
    // entry latched entry_feedback_timeout. The dedicated feedback window
    // must complete the transfer instead.
    let mut b = Bridge::new();
    let mut sim = SerialCadenceSim::new();
    let owner = identity(Transport::Wss, 1, 1);
    drive_entry_at_task_cadence(&mut b, &mut sim, &owner);

    assert_eq!(b.controller.mode(), SafeMode::Emulating);
    assert!(!b.controller.fault_latched());
    assert!(b.io.relay_cmd);
    assert!(b.io.tx_en);
    assert!(b.controller.owner().is_some());
}

// cpp: "gap-safe EXIT completes at the real task cadence (5 ms loop + sub-ms feedback window)"
#[test]
fn gap_safe_exit_completes_at_the_real_task_cadence_5_ms_loop_sub_ms_feedback_window() {
    let mut b = Bridge::new();
    let mut sim = SerialCadenceSim::new();
    let owner = identity(Transport::Wss, 1, 1);
    drive_entry_at_task_cadence(&mut b, &mut sim, &owner);
    assert_eq!(b.controller.mode(), SafeMode::Emulating);
    assert!(!b.controller.fault_latched());

    assert!(b.controller.request_normal_exit(&owner, b.clock.now()));
    for _ in 0..20 {
        if b.controller.mode() == SafeMode::Proxy {
            break;
        }
        sim.iteration(&mut b);
    }

    assert_eq!(b.controller.mode(), SafeMode::Proxy);
    assert!(!b.controller.fault_latched());
    assert!(!b.io.relay_cmd);
    assert!(!b.io.tx_en);
    // Normal exit releases ownership (PLAN exit step 5).
    assert!(b.controller.owner().is_none());
}

// cpp: "3-hour timeout zeros motion via the emulate cycle"
#[test]
fn three_hour_timeout_zeros_motion_via_the_emulate_cycle() {
    let clock = FakeClock::new();
    let mut mode = ModeStateMachine::new();
    mode.request_emulate(true);
    mode.set_speed(tenths(50));
    mode.set_incline(half(10));

    let mut cycle = EmulationCycle::new();
    let mut sink = RecordingSink::default();
    cycle.reset(clock.now());
    cycle.tick(clock.now(), &mut mode, &mut sink); // observes the change at t=0
    assert_eq!(mode.speed_tenths(), tenths(50));

    clock.advance_us(EMULATE_TIMEOUT_US.get() - 1);
    cycle.tick(clock.now(), &mut mode, &mut sink);
    assert_eq!(mode.speed_tenths(), tenths(50));

    clock.advance_us(1); // exactly 3 hours of no changes
    cycle.tick(clock.now(), &mut mode, &mut sink);
    assert_eq!(mode.speed_tenths(), tenths(0));
    assert_eq!(mode.incline(), half(0));
}

// Keep `kv_build` referenced from this file the way the C++ test uses the
// codecs directly, so the zero-frame byte sequence is asserted against the
// builder rather than a literal alone.
#[test]
#[ignore = "documentation helper, not one of the 149 cases"]
fn _zero_frame_builder_shape() {
    assert_eq!(kv_build("inc", "0").expect("fits").as_bytes(), b"[inc:0]\xff");
}
