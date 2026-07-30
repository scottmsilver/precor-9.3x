//! R1 — INDEPENDENT reviewer differential. Written by the equivalence reviewer,
//! not by the implementer. Deliberately probes the domains the committed D1/D2/D3
//! suites leave narrow:
//!
//!   * `observe_console_bytes` over PURELY RANDOM bytes (D3 uses 9 fixed
//!     payloads; D1 uses only well-formed captures).
//!   * motion values over the full i32 cliff set incl. i32::MIN/MAX
//!     (D3 uses {0,50,120,121,-1} x {0,10,30,31,-1}).
//!   * identities with negative handles, generation 0, and i64-scale generations
//!     (D3 uses handles {1,7,42}, gens {1,2,10,11}).
//!   * `connect_raw` vs the C++ `connect` for NEGATIVE generations — the one
//!     place the Rust type system moved a check, so parity must be proven.
//!   * codecs over a much wider integer domain than D2's.
//!   * last TEN events compared, not five.

use difftest::cpp::{self, CppController, CppState};
use difftest::gen::{chunkings, gen_buffer, Rng};
use safety_core::kv as rs;
use safety_core::safety::controller::{
    ConnectionIdentity, Feedback, SafeMode, SafetyController, Transport,
};
use safety_core::units::*;

// ── shared observable projection ────────────────────────────────────

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

fn cmp(r: &SafetyController, c: &CppController, ctx: &str) {
    assert_eq!(rust_state(r), c.state(), "{ctx}: state diverged");
    assert_eq!(
        rust_last_events(r, 10),
        c.last_events(10),
        "{ctx}: last-10 events diverged"
    );
}

fn transport_of(t: i32) -> Transport {
    match t.rem_euclid(3) {
        0 => Transport::Wss,
        1 => Transport::Ble,
        _ => Transport::Executor,
    }
}

// ── R1a: console scanner over purely random bytes ───────────────────

#[test]
fn r1_observe_console_bytes_matches_cpp_over_random_and_structured_bytes() {
    let mut rng = Rng::new(0x5EED_0000_0000_00A1);

    for iter in 0..4_000u64 {
        let mut r = SafetyController::new();
        let mut c = CppController::new();
        let mut now = 0i64;

        for step in 0..40 {
            // Half structure-aware, half UNIFORMLY RANDOM bytes — the latter is
            // what D1/D3 never feed this scanner.
            let buf: Vec<u8> = if rng.bool() {
                let cap = 1 + rng.below(160);
                gen_buffer(&mut rng, cap)
            } else {
                let n = rng.below(160);
                (0..n).map(|_| (rng.next_u64() & 0xFF) as u8).collect()
            };

            let rn = r.observe_console_bytes(&buf, Micros::new(now));
            let cn = c.observe_console_bytes(&buf, now);
            assert_eq!(
                rn, cn,
                "iter {iter} step {step}: complete-frame count diverged for {buf:?}"
            );
            cmp(&r, &c, &format!("iter {iter} step {step}"));

            // Stay well inside the 1.5 s freshness window most of the time, but
            // occasionally jump past it so the deadline path is exercised too.
            now += if rng.below(8) == 0 { 1_600_000 } else { 1_000 };
        }
    }
}

// ── R1b: kv_parse over purely random bytes, all chunkings ───────────

struct RsStream {
    buf: Vec<u8>,
    pairs: Vec<(String, String)>,
}
impl RsStream {
    fn new() -> Self {
        RsStream {
            buf: Vec::new(),
            pairs: Vec::new(),
        }
    }
    fn feed(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
        let mut out = [rs::KvPair::empty(); 32];
        let r = rs::kv_parse(&self.buf, &mut out);
        for p in &out[..r.n] {
            self.pairs
                .push((p.key.as_str().to_string(), p.value.as_str().to_string()));
        }
        self.buf.drain(..r.consumed);
    }
    fn drain(&mut self) {
        loop {
            let before = (self.buf.len(), self.pairs.len());
            self.feed(&[]);
            if (self.buf.len(), self.pairs.len()) == before {
                break;
            }
        }
    }
}

struct CppStream {
    buf: Vec<u8>,
    pairs: Vec<(String, String)>,
}
impl CppStream {
    fn new() -> Self {
        CppStream {
            buf: Vec::new(),
            pairs: Vec::new(),
        }
    }
    fn feed(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
        let r = cpp::kv_parse(&self.buf, 32);
        self.pairs.extend(r.pairs);
        self.buf.drain(..r.consumed);
    }
    fn drain(&mut self) {
        loop {
            let before = (self.buf.len(), self.pairs.len());
            self.feed(&[]);
            if (self.buf.len(), self.pairs.len()) == before {
                break;
            }
        }
    }
}

#[test]
fn r1_kv_parse_matches_cpp_over_uniform_random_bytes() {
    let mut rng = Rng::new(0x5EED_0000_0000_00B2);
    for iter in 0..3_000u64 {
        let n = rng.below(400);
        let data: Vec<u8> = (0..n).map(|_| (rng.next_u64() & 0xFF) as u8).collect();

        let mut base_r = RsStream::new();
        base_r.feed(&data);
        base_r.drain();
        let mut base_c = CppStream::new();
        base_c.feed(&data);
        base_c.drain();
        assert_eq!(base_r.pairs, base_c.pairs, "iter {iter}: whole-buffer pairs");
        assert_eq!(base_r.buf, base_c.buf, "iter {iter}: whole-buffer residue");

        for (ci, chunking) in chunkings(&mut rng, data.len()).into_iter().enumerate() {
            let mut r = RsStream::new();
            let mut c = CppStream::new();
            let mut off = 0usize;
            for n in &chunking {
                let chunk = &data[off..off + n];
                r.feed(chunk);
                c.feed(chunk);
                assert_eq!(r.pairs, c.pairs, "iter {iter} chunking {ci}: pairs");
                assert_eq!(r.buf, c.buf, "iter {iter} chunking {ci}: residue");
                off += n;
            }
            r.drain();
            c.drain();
            assert_eq!(r.pairs, c.pairs, "iter {iter} chunking {ci}: after drain");
        }
    }
}

// ── R1c: codecs over a wide integer domain ──────────────────────────

#[test]
fn r1_codecs_match_cpp_over_a_wide_integer_domain() {
    // D2 covers the valid domains; this adds the far out-of-range values a
    // caller could reach through a network tier that forgot to clamp.
    let mut values: Vec<i32> = Vec::new();
    for v in -300..=600 {
        values.push(v);
    }
    for &v in &[
        i32::MIN,
        i32::MIN + 1,
        -100_000,
        -32_768,
        1_000,
        4_095,
        4_096,
        65_535,
        65_536,
        1_000_000,
        i32::MAX - 1,
        i32::MAX,
    ] {
        values.push(v);
    }

    // Independent re-implementation of the uppercase-hex formatting, so the
    // expected value below is not just "whatever the implementation prints".
    fn hex_upper(value: i32) -> String {
        if value == 0 {
            return "0".to_string();
        }
        let mag = (value as i64).unsigned_abs();
        let s = format!("{mag:X}");
        if value < 0 {
            format!("-{s}")
        } else {
            s
        }
    }

    // The ONE known, INTENTIONAL Rust/C++ divergence in the codecs. It is
    // recorded here rather than skipped, so it is exercised and named on every
    // run and can never quietly become a real regression.
    let mut divergences_exercised = 0usize;

    for &v in &values {
        // `encode_speed_hex` multiplies by 10 in `int`, so |v| > i32::MAX/10 is
        // SIGNED OVERFLOW — undefined behaviour in the C++ (UBSan flags
        // kv_protocol.cpp:92) whose result changes with -O0 vs -O2. Comparing
        // against it would be comparing against UB, not against a contract, so
        // the C++ is not the oracle in this sub-domain.
        //
        // EXPECTED DIVERGENCE (documented in units.rs::to_hundredths): Rust
        // SATURATES. Saturating is the safe side — it is total, monotonic, and
        // cannot turn a huge input into a small or sign-flipped wire value the
        // way wrapping can. This block asserts the Rust behaviour EXACTLY, so
        // the divergence is a pinned contract rather than an untested gap.
        if let Some(hundredths) = v.checked_mul(10) {
            assert_eq!(
                cpp::encode_speed_hex(v),
                safety_core::kv::encode_speed_hex(SpeedTenths::new(v)).as_str(),
                "encode_speed_hex({v})"
            );
            // Sanity: inside the non-overflowing domain the two agree AND the
            // Rust value is the plain product, i.e. saturation changed nothing.
            assert_eq!(
                safety_core::kv::encode_speed_hex(SpeedTenths::new(v)).as_str(),
                hex_upper(hundredths),
                "encode_speed_hex({v}) is not the plain x10 encoding"
            );
        } else {
            divergences_exercised += 1;
            let expected = hex_upper(v.saturating_mul(10));
            assert_eq!(
                safety_core::kv::encode_speed_hex(SpeedTenths::new(v)).as_str(),
                expected,
                "encode_speed_hex({v}): the intentional saturating divergence \
                 changed. Rust must SATURATE here; the C++ is UB and is not \
                 the oracle. See units.rs::to_hundredths."
            );
        }
        assert_eq!(
            cpp::encode_incline_hex(v),
            safety_core::kv::encode_incline_hex(InclineHalfPct::new(v)).as_str(),
            "encode_incline_hex({v})"
        );
    }

    assert!(
        divergences_exercised >= 4,
        "the documented encode_speed_hex saturating divergence was exercised \
         only {divergences_exercised} times — the boundary corpus stopped \
         covering it, so the divergence is no longer pinned"
    );
    println!("R1DIVERGENCE encode_speed_hex saturating (C++ side is UB) = {divergences_exercised} inputs");

    // Decode side: every 1-6 char string over a hex-ish alphabet plus junk.
    let alphabet = b"0123456789ABCDEFabcdefxX +-\x00\x7f[]:";
    let mut rng = Rng::new(0x5EED_0000_0000_00C3);
    for _ in 0..300_000 {
        let len = rng.below(8);
        let s: String = (0..len)
            .map(|_| *rng.pick(alphabet) as char)
            .collect::<String>();
        let cs = cpp::decode_speed_hex(&s);
        let rsv = safety_core::kv::decode_speed_hex(&s)
            .map(|t| t.get())
            .unwrap_or(-1);
        assert_eq!(cs, rsv, "decode_speed_hex({s:?})");

        let ci = cpp::decode_incline_hex(&s);
        let ri = safety_core::kv::decode_incline_hex(&s)
            .map(|h| h.get())
            .unwrap_or(-1);
        assert_eq!(ci, ri, "decode_incline_hex({s:?})");
    }
}

// ── R1d: negative generation — the one relocated check ──────────────

#[test]
fn r1_connect_raw_matches_cpp_connect_for_invalid_identities() {
    for gen in [-1i64, -2, i64::MIN, i64::MIN + 1, -1_000_000_000_000] {
        for t in 0..3i32 {
            for h in [-1i32, 0, 7, i32::MIN, i32::MAX] {
                let mut r = SafetyController::new();
                let mut c = CppController::new();
                let rb = r.connect_raw(transport_of(t), h, gen);
                let cb = c.connect(t, h, gen);
                assert_eq!(rb, cb, "connect(t={t}, h={h}, gen={gen}) return");
                cmp(&r, &c, &format!("connect_raw t={t} h={h} gen={gen}"));
            }
        }
    }

    // Valid generations through the raw form must behave identically to the
    // typed form, including the stale-generation rule and the event strings.
    for gen in [0i64, 1, 2, i64::MAX - 1, i64::MAX] {
        let mut r = SafetyController::new();
        let mut c = CppController::new();
        assert_eq!(r.connect_raw(Transport::Wss, 5, gen), c.connect(0, 5, gen));
        cmp(&r, &c, &format!("connect_raw valid gen={gen}"));
        // Re-connect at the same generation: stale on both sides.
        assert_eq!(r.connect_raw(Transport::Wss, 5, gen), c.connect(0, 5, gen));
        cmp(&r, &c, &format!("reconnect same gen={gen}"));
    }
}

// ── R1e: wide-domain controller op sequences ────────────────────────

#[derive(Clone, Copy, Debug)]
enum Op {
    Connect { t: i32, h: i32, g: i64 },
    ConnectRaw { t: i32, h: i32, g: i64 },
    Acquire { t: i32, h: i32, g: i64, now: i64 },
    Heartbeat { t: i32, h: i32, g: i64, now: i64 },
    Motion { t: i32, h: i32, g: i64, s: i32, i: i32, now: i64 },
    Disconnect { t: i32, h: i32, g: i64, now: i64 },
    DisconnectTransport { t: i32, now: i64 },
    Console { which: usize, now: i64 },
    RequestEmulate { t: i32, h: i32, g: i64, now: i64, idle: bool },
    RequestEmulateRecovering { t: i32, h: i32, g: i64, now: i64, idle: bool },
    Gap { now: i64 },
    Fb { nc: bool, no: bool, now: i64 },
    NormalExit { t: i32, h: i32, g: i64, now: i64 },
    TreadOk { v: bool, now: i64 },
    Vbus { hi: bool },
    Tick { now: i64 },
    Emergency { which: usize, now: i64 },
    Watchdog { now: i64 },
    Reset { which: usize, now: i64 },
}

const PAYLOADS: &[&[u8]] = &[
    b"[hmph:0000]",
    b"[loop:5550]",
    b"[inc:0000]",
    b"[hmph:78]",
    b"[hmph:0000",
    b"]",
    b"[bad frame]",
    b"[9key:1]",
    b"\xff\x00",
    b"",
    b"[",
    b"[a:]",
    b"[a]",
    b"[:v]",
    b"[a:b][c:d]",
    b"[k:0123456789012345678901234567890123456789012345678901234567890123]", // 64-char value
    b"[k:012345678901234567890123456789012345678901234567890123456789012]",  // 63-char value
    b"[aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1]", // 64-char key
    b"[hmph:78]\xff[inc:A]\xff[loop:1]\xff",
];

const REASONS: &[&str] = &[
    "tread_not_ok",
    "console_stale",
    "lease_expired",
    "explicit_emergency_stop",
    "brownout",
    "reset",
    "watchdog",
    "",
    "a_very_long_reason_string_that_exceeds_the_event_buffer_capacity_by_a_wide_margin",
];

const RESETS: &[&str] = &["reset", "brownout", "power_glitch", "", "x"];

fn interesting_time(rng: &mut Rng, base: i64) -> i64 {
    const D: &[i64] = &[
        4_000_000, 1_500_000, 1_000_000, 10_000, 1_000, 20_000, 200, 0, 10_800_000_000,
    ];
    match rng.below(12) {
        0..=6 => base + *rng.pick(D) + [-1i64, 0, 1][rng.below(3)],
        7..=8 => base + rng.range_i64(0, 3_000),
        9 => base + rng.range_i64(0, 20_000_000),
        10 => base, // repeated timestamp — same-instant ordering
        _ => base + 1,
    }
}

fn gen_ops(rng: &mut Rng, n: usize) -> Vec<Op> {
    // WIDE identity domain: negative handles, gen 0, huge gens.
    let transports = [0i32, 1, 2];
    let handles = [-1i32, 0, 1, 7, 42, i32::MIN, i32::MAX];
    let gens = [0i64, 1, 2, 10, 11, i64::MAX, 4_294_967_296];
    // WIDE motion domain, straddling both app and absolute clamps.
    let speeds = [0i32, 1, 50, 119, 120, 121, -1, 198, 199, i32::MIN, i32::MAX];
    let inclines = [0i32, 1, 10, 29, 30, 31, -1, 197, 198, 199, i32::MIN, i32::MAX];

    let mut ops = Vec::with_capacity(n);
    let mut now = 0i64;
    for _ in 0..n {
        now = interesting_time(rng, now).max(0);
        let t = *rng.pick(&transports);
        let h = *rng.pick(&handles);
        let g = *rng.pick(&gens);
        let op = match rng.below(21) {
            0 => Op::Connect { t, h, g },
            1 => Op::Acquire { t, h, g, now },
            2 => Op::Heartbeat { t, h, g, now },
            3 => Op::Motion {
                t,
                h,
                g,
                s: *rng.pick(&speeds),
                i: *rng.pick(&inclines),
                now,
            },
            4 => Op::Disconnect { t, h, g, now },
            5 => Op::DisconnectTransport { t, now },
            6 | 7 => Op::Console {
                which: rng.below(PAYLOADS.len()),
                now,
            },
            8 => Op::RequestEmulate {
                t,
                h,
                g,
                now,
                idle: rng.below(8) != 0,
            },
            9 => Op::Gap { now },
            10 | 11 => Op::Fb {
                nc: rng.bool(),
                no: rng.bool(),
                now,
            },
            12 => Op::NormalExit { t, h, g, now },
            13 => Op::TreadOk {
                v: rng.below(4) != 0,
                now,
            },
            14 => Op::Vbus { hi: rng.bool() },
            15 => Op::Tick { now },
            16 => Op::ConnectRaw { t, h, g },
            17 => match rng.below(4) {
                0 => Op::Emergency {
                    which: rng.below(REASONS.len()),
                    now,
                },
                1 => Op::Watchdog { now },
                2 => Op::Reset {
                    which: rng.below(RESETS.len()),
                    now,
                },
                _ => Op::Tick { now },
            },
            18 => Op::RequestEmulateRecovering {
                t,
                h,
                g,
                now,
                idle: rng.below(8) != 0,
            },
            _ => Op::Tick { now },
        };
        ops.push(op);
    }
    ops
}

fn ident(t: i32, h: i32, g: i64) -> Option<ConnectionIdentity> {
    ConnectionIdentity::new(transport_of(t), h, g)
}

fn step(r: &mut SafetyController, c: &mut CppController, op: &Op) -> Result<(), String> {
    let (rb, cb): (i64, i64) = match *op {
        Op::Connect { t, h, g } | Op::ConnectRaw { t, h, g } => {
            // Both forms exist so the typed path and the boundary path are
            // BOTH compared against the single C++ `connect`.
            let rv = match (op, ident(t, h, g)) {
                (Op::Connect { .. }, Some(id)) => r.connect(&id) as i64,
                _ => r.connect_raw(transport_of(t), h, g) as i64,
            };
            (rv, c.connect(t, h, g) as i64)
        }
        Op::Acquire { t, h, g, now } => (
            ident(t, h, g).map_or(0, |id| r.acquire(&id, Micros::new(now)) as i64),
            c.acquire(t, h, g, now) as i64,
        ),
        Op::Heartbeat { t, h, g, now } => (
            ident(t, h, g).map_or(0, |id| r.heartbeat(&id, Micros::new(now)) as i64),
            c.heartbeat(t, h, g, now) as i64,
        ),
        Op::Motion {
            t,
            h,
            g,
            s,
            i,
            now,
        } => (
            ident(t, h, g).map_or(0, |id| {
                r.command_motion(
                    &id,
                    SpeedTenths::new(s),
                    InclineHalfPct::new(i),
                    Micros::new(now),
                ) as i64
            }),
            c.command_motion(t, h, g, s, i, now) as i64,
        ),
        Op::Disconnect { t, h, g, now } => (
            ident(t, h, g).map_or(0, |id| r.disconnect(&id, Micros::new(now)) as i64),
            c.disconnect(t, h, g, now) as i64,
        ),
        Op::DisconnectTransport { t, now } => (
            r.disconnect_transport(transport_of(t), Micros::new(now)) as i64,
            c.disconnect_transport(t, now) as i64,
        ),
        Op::Console { which, now } => (
            r.observe_console_bytes(PAYLOADS[which], Micros::new(now)) as i64,
            c.observe_console_bytes(PAYLOADS[which], now) as i64,
        ),
        Op::RequestEmulate {
            t,
            h,
            g,
            now,
            idle,
        } => (
            ident(t, h, g).map_or(0, |id| {
                r.request_emulate(&id, Micros::new(now), idle) as i64
            }),
            c.request_emulate(t, h, g, now, idle) as i64,
        ),
        Op::RequestEmulateRecovering {
            t,
            h,
            g,
            now,
            idle,
        } => (
            ident(t, h, g).map_or(0, |id| {
                r.request_emulate_recovering(&id, Micros::new(now), idle) as i64
            }),
            c.request_emulate_recovering(t, h, g, now, idle) as i64,
        ),
        Op::Gap { now } => (
            r.observe_interframe_gap(Micros::new(now)) as i64,
            c.observe_interframe_gap(now) as i64,
        ),
        Op::Fb { nc, no, now } => (
            feedback_ord(r.observe_relay_feedback(NcHigh(nc), NoHigh(no), Micros::new(now))),
            c.observe_relay_feedback(nc, no, now),
        ),
        Op::NormalExit { t, h, g, now } => (
            ident(t, h, g).map_or(0, |id| r.request_normal_exit(&id, Micros::new(now)) as i64),
            c.request_normal_exit(t, h, g, now) as i64,
        ),
        Op::TreadOk { v, now } => {
            r.set_tread_ok(TreadOk(v), Micros::new(now));
            c.set_tread_ok(v, now);
            (0, 0)
        }
        Op::Vbus { hi } => {
            r.set_vbus_present_n(hi);
            c.set_vbus_present_n(hi);
            (0, 0)
        }
        Op::Tick { now } => {
            r.tick(Micros::new(now));
            c.tick(now);
            (0, 0)
        }
        Op::Emergency { which, now } => {
            r.emergency_stop(REASONS[which], Micros::new(now));
            c.emergency_stop(REASONS[which], now);
            (0, 0)
        }
        Op::Watchdog { now } => {
            r.watchdog_stall(Micros::new(now));
            c.watchdog_stall(now);
            (0, 0)
        }
        Op::Reset { which, now } => {
            r.reset(Micros::new(now), RESETS[which]);
            c.reset(RESETS[which], now);
            (0, 0)
        }
    };

    if rb != cb {
        return Err(format!("return: rust={rb} cpp={cb} for {op:?}"));
    }
    let (rs_state, cpp_state) = (rust_state(r), c.state());
    if rs_state != cpp_state {
        return Err(format!(
            "state after {op:?}:\n  rust={rs_state:?}\n  cpp ={cpp_state:?}"
        ));
    }
    let (re, ce) = (rust_last_events(r, 10), c.last_events(10));
    if re != ce {
        return Err(format!(
            "last-10 events after {op:?}:\n  rust={re:?}\n  cpp ={ce:?}"
        ));
    }
    Ok(())
}

#[test]
fn r1_controller_wide_domain_op_sequences_match_cpp() {
    let mut rng = Rng::new(0x5EED_0000_0000_00D4);
    for seq in 0..4_000u64 {
        let ops = gen_ops(&mut rng, 80);
        let mut r = SafetyController::new();
        let mut c = CppController::new();
        for (i, op) in ops.iter().enumerate() {
            if let Err(msg) = step(&mut r, &mut c, op) {
                panic!("R1 DIVERGENCE seq #{seq} op #{i}\n  {msg}\n  ops={ops:#?}");
            }
        }
    }
}

#[test]
fn r1_controller_very_long_sequences_match_cpp() {
    // Long enough to wrap the 256-slot audit ring several times and to
    // saturate the connection/generation tables repeatedly.
    let mut rng = Rng::new(0x5EED_0000_0000_00E5);
    for seq in 0..60u64 {
        let ops = gen_ops(&mut rng, 3_000);
        let mut r = SafetyController::new();
        let mut c = CppController::new();
        for (i, op) in ops.iter().enumerate() {
            if let Err(msg) = step(&mut r, &mut c, op) {
                panic!("R1 LONG DIVERGENCE seq #{seq} op #{i}\n  {msg}");
            }
        }
    }
}
