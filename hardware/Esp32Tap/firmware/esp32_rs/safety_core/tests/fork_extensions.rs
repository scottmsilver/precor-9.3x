//! Host coverage for the Rust-only HARDENING extensions.
//!
//! Deliberately a SEPARATE file from the seven 1:1 ported suites: nothing here
//! has a C++ or `safety_model.py` twin, so putting these cases in
//! `safety_controller.rs` would make `check_case_parity.py` count extras it
//! has to be told to ignore. Keeping them out means the 148/148 parity number
//! keeps meaning exactly what it says.
//!
//! Covered:
//!   1. the eviction-resistant critical-event log (a routine-traffic flood
//!      must not be able to erase WHY the machine stopped);
//!   2. `ParseBuf`'s bounded wedge recovery (an unterminated `[` must not be
//!      able to permanently kill the console-takeover interlock).

use safety_core::kv::kv_parse;
use safety_core::parse_buf::{ParseBuf, MAX_FRAME_BYTES};
use safety_core::safety::controller::{
    ConnectionIdentity, SafeMode, SafetyController, Transport, CRITICAL_CAPACITY, EVENT_CAPACITY,
};
use safety_core::units::*;

// ── 1. critical-event retention ─────────────────────────────────────

/// Push enough routine events to wrap the 256-slot ring several times.
fn flood_routine(c: &mut SafetyController, n: usize) {
    // `set_vbus_present_n` emits exactly one usb_attach/usb_detach per call
    // and touches nothing else — the cheapest pure-noise generator there is,
    // and the real source of the flood the reviewer described (the serial task
    // used to call it every 5 ms before it was edge-detected).
    for i in 0..n {
        c.set_vbus_present_n(i % 2 == 0);
    }
}

#[test]
fn emergency_record_survives_a_routine_flood_that_wraps_the_ring() {
    let mut c = SafetyController::new();

    c.emergency_stop("tread_not_ok", Micros::new(1_000));
    let idx = c.event_count() - 1;
    assert_eq!(c.event_at(idx), Some("emergency:tread_not_ok"));
    assert_eq!(c.critical_event_count(), 1);

    // Wrap the main ring more than four times.
    flood_routine(&mut c, EVENT_CAPACITY * 4 + 7);

    // The main ring has certainly evicted it...
    assert!(c.event_count() - idx > EVENT_CAPACITY as u64);
    // ...and it is STILL readable, which is the whole point.
    assert_eq!(
        c.event_at(idx),
        Some("emergency:tread_not_ok"),
        "routine traffic evicted the record of why the machine stopped"
    );
    assert!(c
        .critical_events()
        .any(|(i, t)| i == idx && t == "emergency:tread_not_ok"));
}

#[test]
fn the_first_critical_event_is_never_overwritten_by_later_ones() {
    let mut c = SafetyController::new();
    c.emergency_stop("brownout", Micros::ZERO);
    let first = c.event_count() - 1;

    // Far more critical events than the log has slots.
    for i in 0..(CRITICAL_CAPACITY as i64 * 5) {
        c.emergency_stop("explicit_emergency_stop", Micros::new(i));
    }
    let last = c.event_count() - 1;
    flood_routine(&mut c, EVENT_CAPACITY * 2);

    // Slot 0: the FIRST cause, still there.
    assert_eq!(
        c.event_at(first),
        Some("emergency:brownout"),
        "the first fault — usually the cause, with everything after it a \
         consequence — was overwritten"
    );
    // Rolling slots: the most recent one, still there.
    assert_eq!(c.event_at(last), Some("emergency:explicit_emergency_stop"));
    assert_eq!(c.critical_event_count() as usize, 1 + CRITICAL_CAPACITY * 5);
    assert_eq!(c.critical_events().count(), CRITICAL_CAPACITY);
}

#[test]
fn non_emergency_fault_latches_are_retained_too() {
    // `proxy_feedback_invalid` latches a fault WITHOUT an emergency stop
    // (N24), so it has no `emergency:` twin protecting it.
    let mut c = SafetyController::new();
    c.observe_relay_feedback(NcHigh(true), NoHigh(false), Micros::ZERO); // EMULATE while PROXY
    let idx = c.event_count() - 1;
    assert_eq!(c.event_at(idx), Some("proxy_feedback_invalid"));
    assert!(c.fault_latched());

    flood_routine(&mut c, EVENT_CAPACITY * 3);
    assert_eq!(c.event_at(idx), Some("proxy_feedback_invalid"));
}

#[test]
fn routine_events_are_never_promoted_into_the_critical_log() {
    // If the classifier were loose the critical log would just become a second
    // rolling window and protect nothing.
    let mut c = SafetyController::new();
    let id = ConnectionIdentity::new(Transport::Wss, 1, 1).unwrap();
    c.connect(&id);
    c.acquire(&id, Micros::ZERO);
    c.observe_console_bytes(b"[hmph:0000]", Micros::ZERO);
    flood_routine(&mut c, 500);
    assert_eq!(
        c.critical_event_count(),
        0,
        "a routine event was classified as critical"
    );
}

// ── 1b. feedback window: bounded on a STUCK clock ───────────────────

/// A window IO whose monotonic clock never advances, and whose relay never
/// reports the expected feedback — the exact condition under which the
/// controller's own 10 ms deadline can NEVER fire.
struct StuckClockIo {
    polls: u32,
    applied: Vec<safety_core::safety::controller::OutputIntent>,
}

impl safety_core::safety::FeedbackWindowIo for StuckClockIo {
    fn now(&self) -> Micros {
        Micros::new(12_345) // frozen
    }
    fn nc(&self) -> NcHigh {
        NcHigh(false) // BYPASS while the entry wants EMULATE
    }
    fn no(&self) -> NoHigh {
        NoHigh(true)
    }
    fn tread_ok(&self) -> TreadOk {
        TreadOk(true)
    }
    fn apply(&mut self, intent: safety_core::safety::controller::OutputIntent) {
        self.applied.push(intent);
    }
    fn delay(&mut self) {
        self.polls += 1;
    }
}

#[test]
fn the_feedback_window_fails_closed_on_a_stuck_clock_instead_of_spinning() {
    use safety_core::safety::{run_feedback_window, MAX_WINDOW_POLLS};

    // Drive a real entry to ENTRY_WAIT_FEEDBACK at t = 12_345 µs, so the
    // window's frozen clock equals the moment the deadline was armed.
    let mut c = SafetyController::new();
    let now = Micros::new(12_345);
    c.observe_relay_feedback(NcHigh(false), NoHigh(true), now); // BYPASS
    let id = ConnectionIdentity::new(Transport::Executor, 1, 1).unwrap();
    assert!(c.connect(&id));
    assert!(c.acquire(&id, now));
    c.observe_console_bytes(b"[hmph:0000]", now);
    assert!(c.request_emulate(&id, now, true));
    assert!(c.observe_interframe_gap(now));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    assert!(c.relay_cmd().get(), "precondition: the coil is energized");

    let mut io = StuckClockIo {
        polls: 0,
        applied: Vec::new(),
    };
    // Before the iteration cap this call did not terminate at all: the clock
    // never reaches the deadline and the feedback never qualifies, so the loop
    // spun with the relay CLOSED and the safety mutex held until the 2 s task
    // WDT panicked the whole device — the "recovery path" nothing validated.
    run_feedback_window(&mut c, &mut io);

    assert!(
        io.polls <= MAX_WINDOW_POLLS,
        "window polled {} times, cap is {MAX_WINDOW_POLLS}",
        io.polls
    );
    assert_eq!(c.mode(), SafeMode::Proxy, "the window did not fail closed");
    assert!(!c.relay_cmd().get(), "the relay was left energized");
    assert!(!c.tx_enable().get(), "TX_ENABLE was left asserted");
    assert!(c.speed_tenths().is_zero() && c.incline_half_percent().is_zero());
    assert_eq!(
        c.event_at(c.event_count() - 1),
        Some("emergency:feedback_window_stalled")
    );
    // And the LAST thing pushed to hardware is the released state.
    let last = io.applied.last().copied().expect("outputs were applied");
    assert!(!last.relay.get() && !last.tx_enable.get());
}

// ── 2. ParseBuf wedge recovery ──────────────────────────────────────

type Buf = ParseBuf<4096>;

/// One serial-engine iteration: append, parse, consume, resync.
fn feed(buf: &mut Buf, bytes: &[u8]) -> usize {
    let mut pairs = [safety_core::kv::KvPair::empty(); 32];
    buf.append(bytes);
    let r = kv_parse(buf.as_slice(), &mut pairs);
    buf.consume(r.consumed);
    buf.resync_if_wedged(r.consumed);
    r.n
}

#[test]
fn an_unterminated_bracket_does_not_wedge_the_parser_forever() {
    let mut buf = Buf::new();

    // Line noise: a bare `[` that never closes.
    assert_eq!(feed(&mut buf, b"["), 0);

    // Then a very long run of bytes with no `]`, as a corrupted burst would
    // produce. Under the old buffer `consumed` stays 0, the buffer fills to
    // 4096 and every later byte is dropped.
    for _ in 0..200 {
        feed(&mut buf, b"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx");
    }
    assert!(buf.resync_count() > 0, "the wedge was never detected");
    assert_eq!(buf.overflow_drop_count(), 0, "bytes were silently dropped");

    // THE PROPERTY: a real console frame still parses afterwards. Without the
    // fix this returns 0 for the rest of the power cycle, and with it the
    // console-takeover interlock is dead.
    assert_eq!(feed(&mut buf, b"[hmph:C8]"), 1);
}

#[test]
fn resync_lands_on_the_last_frame_start_in_the_window() {
    let mut buf = Buf::new();
    // A stale opener plus a long unterminated run: the wedge condition.
    let mut noise = Vec::from(&b"["[..]);
    noise.extend(core::iter::repeat(b'y').take(MAX_FRAME_BYTES));
    assert_eq!(feed(&mut buf, &noise), 0);
    assert_eq!(buf.resync_count(), 1);
    assert!(buf.is_empty(), "no `[` in the window, so the buffer should clear");

    // A REAL frame arriving after the recovery parses normally.
    assert_eq!(feed(&mut buf, b"[inc:1E]"), 1);

    // And a `[` that is still inside the window is KEPT across a recovery:
    // the frame it opens completes on the next read.
    let mut buf = Buf::new();
    let mut noise = Vec::from(&b"["[..]);
    noise.extend(core::iter::repeat(b'y').take(MAX_FRAME_BYTES));
    noise.extend_from_slice(b"[hmph:C8");
    assert_eq!(feed(&mut buf, &noise), 0);
    assert_eq!(buf.resync_count(), 1);
    assert_eq!(buf.as_slice(), b"[hmph:C8");
    assert_eq!(feed(&mut buf, b"]"), 1, "the surviving partial frame did not complete");
}

#[test]
fn a_legitimately_partial_frame_is_never_discarded() {
    // The Postel behaviour that makes the wedge possible is also REQUIRED: a
    // frame split across two UART reads must survive. Any recovery that fires
    // early would break it, so this is the guard on the guard.
    let mut buf = Buf::new();
    assert_eq!(feed(&mut buf, b"[hmph:"), 0);
    assert_eq!(feed(&mut buf, b"78]"), 1);
    assert_eq!(buf.resync_count(), 0);

    // A partial of the LONGEST shape kv_parse can still accept (content must
    // be < KV_FIELD_SIZE, so `[` + 63 + `]`) survives arriving one byte at a
    // time, with a wedge-triggering amount of garbage already behind it.
    let mut buf = Buf::new();
    feed(&mut buf, &vec![b'z'; MAX_FRAME_BYTES * 2]); // no `[` at all: harmless
    let mut partial = Vec::from(&b"[k:"[..]);
    partial.extend(core::iter::repeat(b'v').take(60));
    assert_eq!(partial.len(), 63);
    for b in &partial {
        feed(&mut buf, &[*b]);
    }
    assert_eq!(feed(&mut buf, b"]"), 1, "a still-valid partial frame was discarded");
}

#[test]
fn the_buffer_can_never_fill_with_unconsumable_bytes() {
    // Adversarial: brackets that never close, forever.
    let mut buf = Buf::new();
    for _ in 0..2_000 {
        feed(&mut buf, b"[[[[[[[[[[[[[[[[");
    }
    assert!(
        buf.len() <= MAX_FRAME_BYTES,
        "buffer grew to {} bytes of unconsumable input",
        buf.len()
    );
    assert_eq!(buf.overflow_drop_count(), 0);

    // Recovery is to a BOUNDED buffer, not to an empty one: the retained
    // leading `[`s are swallowed by the first frame that closes them (that is
    // `kv_parse`'s own documented behaviour, unchanged here), and the very
    // next frame parses. What must never happen — and did, before the fix —
    // is that NO later frame ever parses again.
    feed(&mut buf, b"[hmph:C8]");
    assert_eq!(feed(&mut buf, b"[hmph:C8]"), 1);
    assert!(buf.is_empty());
}
