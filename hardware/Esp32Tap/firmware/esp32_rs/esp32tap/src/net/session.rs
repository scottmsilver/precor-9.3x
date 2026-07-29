//! The session recorder — what turns a workout into a run record.
//!
//! One WDT-supervised 1 s task. It watches the belt and the program, keeps the
//! four metrics `python/workout_session.py` keeps, and writes them to flash:
//! a record once the session passes 5 s, a checkpoint every 30 s carrying
//! `end_reason: "in_progress"`, and a finalisation with the real reason.
//!
//! # Why it is not part of the interval executor
//!
//! Because that task promises never to touch flash, and the promise is
//! load-bearing: it is the task that keeps the belt moving to the plan, and a
//! 4 KB sector erase inside its tick would put flash latency on the path
//! between "this interval is over" and "the motor is told". So the recorder is
//! a separate, lower-priority task in the network tier, and the executor
//! remains unable to name flash at all.
//!
//! # Why it is not part of the HTTP tier either
//!
//! Because a run must be recorded whether or not anybody is connected. The
//! whole point of this device is that a workout survives the tablet walking
//! away; a checkpoint that only happened when a client polled would lose
//! exactly the runs that prove it.
//!
//! # Integer metrics
//!
//! `workout_session.py` accumulates floats. Here every accumulator is an
//! integer in a fine unit and the division happens once, at write time, so the
//! numbers are reproducible and there is no float in the loop. The ACSM
//! equation is ported term for term; see [`Metrics::tick`].
//!
//! # `disconnect` is never written, and that is not an omission
//!
//! The Pi ends a session with `disconnect` when the client's WebSocket drops.
//! This device has no such notion by design — nothing about a run depends on a
//! client being there — so a session here ends as `user_stop` (the belt went
//! to zero) or `program_complete` (the program finished). Writing
//! `disconnect` would require inventing a dependency the firmware deliberately
//! does not have.

use crate::context::{lock, FirmwareContext};
use crate::hal::wdt;
use crate::net::store::{self, Which};
use crate::tasks::delay_ms;
use program_core::record::{EndReason, Run};
use safety_core::FixedStr;
use std::sync::Mutex;

/// `_RUN_SAVE_INTERVAL` in `python/server.py`.
const CHECKPOINT_S: u32 = 30;
/// `server.py` creates no record for a session shorter than this.
const MIN_RUN_S: u32 = 5;

const TICK_MS: u32 = 1000;

/// This task's stack, in bytes. Named here rather than at the spawn site so
/// the frame below can be asserted against it at COMPILE time.
///
/// MEASURED, NOT CHOSEN: at 6144 this task overflowed and rebooted the device
/// on one read-modify-write of a stored entry, and a reboot drops the relay.
///
/// THE WEBSOCKET FRAMES ARE NOT IN THIS BUDGET, and that is a consequence of
/// where they are rendered rather than an omission: [`push_frames`] runs on the
/// HTTPD task (see `net::ws`), so its ~2.7 KB of buffers is asserted against
/// `net::http::HTTPD_STACK_BYTES` below. This task only asks.
pub const STACK_BYTES: usize = 12_288;

/// Worst-case frame: the progress write holds one decoded `Entry` while the
/// store hands back another, plus this task's own state, plus slack for the
/// call frames the compiler adds. Level-1 interrupts run on the interrupted
/// task's stack, so the reserve below is not spare.
const FRAME_BYTES: usize = core::mem::size_of::<Session>()
    + 2 * core::mem::size_of::<program_core::record::Entry>()
    + core::mem::size_of::<program_core::record::Head>()
    + 1024;

const _: () = assert!(
    FRAME_BYTES + 2048 < STACK_BYTES,
    "the session recorder's frame plus interrupt reserve no longer fits its \
     stack — raise net::session::STACK_BYTES"
);

/// Stack [`push_frames`] puts on the HTTPD task: the three rendered frames plus
/// the call frames around them.
const PUSH_FRAME_BYTES: usize =
    crate::net::program::STATE_BUF + crate::net::api::STATUS_BUF + SESSION_BUF + 512;

const _: () = assert!(
    PUSH_FRAME_BYTES + 4096 < crate::net::http::HTTPD_STACK_BYTES as usize,
    "the WebSocket push frame plus mbedtls headroom no longer fits the httpd \
     task stack — raise net::http::HTTPD_STACK_BYTES"
);

// ---------------------------------------------------------------------------
// The live push. See `net::ws` for why it is here and not in a fifth task.
// ---------------------------------------------------------------------------

/// Bytes a `session` frame renders into. Measured longest (six-digit elapsed,
/// three-decimal distance) is ~140; the writer below REFUSES to overflow, so a
/// wrong number here truncates into a dropped frame rather than smashing the
/// stack.
const SESSION_BUF: usize = 256;

struct FrameBuf {
    b: [u8; SESSION_BUF],
    n: usize,
}

impl core::fmt::Write for FrameBuf {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let x = s.as_bytes();
        if self.n + x.len() > SESSION_BUF {
            return Err(core::fmt::Error);
        }
        self.b[self.n..self.n + x.len()].copy_from_slice(x);
        self.n += x.len();
        Ok(())
    }
}

/// What a `session` frame needs, published for the httpd task to render.
///
/// A FIXED-SIZE VALUE BEHIND A LOCK, not a rendered buffer handed across: the
/// push runs on the httpd task (see `net::ws`) and this task keeps advancing
/// while it does, so a shared BUFFER would be a second writer to the bytes IDF
/// is reading. Six integers copied under a lock cannot be half-written.
#[derive(Clone, Copy, Default)]
struct Snapshot {
    active: bool,
    elapsed_s: u32,
    distance_milli: u32,
    vert_feet_tenths: u32,
    calories_tenths: u32,
    end_reason: Option<EndReason>,
}

static SNAPSHOT: Mutex<Snapshot> = Mutex::new(Snapshot {
    active: false,
    elapsed_s: 0,
    distance_milli: 0,
    vert_feet_tenths: 0,
    calories_tenths: 0,
    end_reason: None,
});

fn publish(s: &Session, active: bool, reason: Option<EndReason>) {
    let mut r = Run::new("", "", false);
    s.metrics.write_into(&mut r);
    *lock(&SNAPSHOT) = Snapshot {
        active,
        elapsed_s: r.elapsed_s,
        distance_milli: r.distance_milli,
        vert_feet_tenths: r.vert_feet_tenths,
        calories_tenths: r.calories_tenths,
        end_reason: reason,
    };
}

/// Render the `session` frame — `WorkoutSession.to_dict()`, field for field.
///
/// `wall_started_at` is `""`, and that is the Pi's own value for a session that
/// has not started rather than an invention: this device has no RTC and no
/// SNTP, so any ISO timestamp here would be a number computed from uptime and
/// presented as a date. The Kotlin model types it `String` with NO default, so
/// it must be PRESENT — an omission throws MissingFieldException and kills the
/// whole frame — but nothing in the app renders it.
///
/// `end_reason` is `String?`; `null` while a session is live is what the Pi
/// sends too.
fn render_session(s: &Snapshot) -> FrameBuf {
    use core::fmt::Write;
    let mut b = FrameBuf {
        b: [0u8; SESSION_BUF],
        n: 0,
    };
    let _ = write!(
        b,
        r#"{{"type":"session","active":{},"elapsed":{}.0,"distance":{}.{:03},"vert_feet":{}.{},"calories":{}.{},"wall_started_at":""#,
        s.active,
        s.elapsed_s,
        s.distance_milli / 1000,
        s.distance_milli % 1000,
        s.vert_feet_tenths / 10,
        s.vert_feet_tenths % 10,
        s.calories_tenths / 10,
        s.calories_tenths % 10,
    );
    let _ = match s.end_reason {
        Some(x) => write!(b, r#"","end_reason":"{}"}}"#, x.as_str()),
        None => b.write_str(r#"","end_reason":null}"#),
    };
    b
}

/// Push `status`, `program` and `session` down every open WebSocket.
///
/// RUNS ON THE HTTPD TASK, invoked by `net::ws`'s queued work item — never on
/// the recorder. See that module: sending from a second task raced the server's
/// own session teardown over one mbedtls context, intermittently.
///
/// THE APP HAS NO OTHER SOURCE for any of this. Its program-endpoint calls
/// discard their response bodies entirely, so without these three frames the
/// belt moves and the Running screen does not.
///
/// Rendered under the locks, sent OUTSIDE them, exactly as every HTTP handler
/// here does it: a frame send blocks for up to `send_wait_timeout` per socket,
/// and the interval executor takes the program lock every tick under a 2 s
/// watchdog. A program that cannot be rendered is SKIPPED rather than sent
/// truncated — half a workout would decode as a different one.
pub fn push_frames() {
    if !crate::net::ws::any_client() {
        return;
    }
    let mut status = [0u8; crate::net::api::STATUS_BUF];
    let status_n = crate::net::api::render_status(&mut status, "");

    let mut prog = [0u8; crate::net::program::STATE_BUF];
    let prog_n = {
        let p = lock(&crate::CTX.program);
        crate::net::program::render_state(&mut prog, &p, "")
    }
    .unwrap_or(0);

    let sess = render_session(&lock(&SNAPSHOT));

    // ONE call, so the three frames share one budget and one enumeration.
    match prog_n {
        0 => crate::net::ws::send_all(&[&status[..status_n], &sess.b[..sess.n]]),
        n => crate::net::ws::send_all(&[&status[..status_n], &prog[..n], &sess.b[..sess.n]]),
    }
}

/// The history entry the loaded program came from, so its progress can be
/// written back. Empty when the running program did not come from the store
/// (a quick start, or a program POSTed straight to `/api/program/start`).
static CURRENT_HISTORY: Mutex<FixedStr<{ program_core::record::MAX_ID }>> =
    Mutex::new(FixedStr::new());

pub fn set_current(id: &FixedStr<{ program_core::record::MAX_ID }>) {
    *lock(&CURRENT_HISTORY) = *id;
}

/// Read the current history id. THE CALLER MUST HOLD THE PROGRAM LOCK — the id
/// and the loaded program are one fact, and reading them separately let a
/// checkpoint write one program's progress into the other's entry. The lock
/// order is `program` then this; nothing takes this and then wants `program`.
fn current_locked() -> FixedStr<{ program_core::record::MAX_ID }> {
    *lock(&CURRENT_HISTORY)
}

/// The four numbers a run carries, accumulated in integer units.
#[derive(Default)]
struct Metrics {
    elapsed_s: u32,
    /// Sum of `speed_tenths` over moving seconds. Miles = sum / 36000.
    speed_acc: u64,
    /// Sum of `speed_tenths * incline_half`. Feet x 10 = sum * 11 / 1500.
    vert_acc: u64,
    /// Sum of `vo2_milli * weight_grams`. kcal x 10 = sum / 1_200_000_000.
    cal_acc: u64,
}

impl Metrics {
    /// One second of accumulation, porting `WorkoutSession.tick` exactly.
    ///
    /// ACSM metabolic equations, in milli-units so nothing is a float:
    ///   walking (< 4.5 mph): VO2 = 3.5 + 0.1*S + 1.8*S*G
    ///   running (>= 4.5):    VO2 = 3.5 + 0.2*S + 0.9*S*G
    /// with S the speed in m/min and G the fractional grade. `S_milli` is
    /// `speed_mph * 26.8224 * 1000` (26.8224 m/min per mph) and `G_milli` is
    /// `incline_half / 200 * 1000`, which is exact.
    fn tick(&mut self, speed_tenths: i32, incline_half: i32, weight_grams: u64) {
        if speed_tenths <= 0 {
            return;
        }
        let st = speed_tenths.max(0) as u64;
        let ih = incline_half.max(0) as u64;
        // SATURATING, not wrapping. The controller's own clamps make overflow
        // unreachable in practice, but "unreachable" is not "cannot"; a
        // wrapped accumulator would report a nonsense run, and in a debug
        // build it would panic, and a panic here is a reboot.
        self.speed_acc = self.speed_acc.saturating_add(st);
        self.vert_acc = self.vert_acc.saturating_add(st.saturating_mul(ih));

        let s_milli = st.saturating_mul(2682); // speed_m_min x 1000
        let g_milli = ih.saturating_mul(5); // fractional grade x 1000
        let grade_term = s_milli.saturating_mul(g_milli) / 10_000_000;
        let vo2_milli = if speed_tenths < 45 {
            3500 + s_milli / 10 + grade_term.saturating_mul(18)
        } else {
            3500 + s_milli / 5 + grade_term.saturating_mul(9)
        };
        self.cal_acc = self
            .cal_acc
            .saturating_add(vo2_milli.saturating_mul(weight_grams));
    }

    fn write_into(&self, r: &mut Run) {
        r.elapsed_s = self.elapsed_s;
        // `min` before the cast: `as u32` truncates silently, and a truncated
        // distance is a wrong number rather than a big one.
        r.distance_milli = (self.speed_acc / 36).min(u32::MAX as u64) as u32;
        r.vert_feet_tenths =
            (self.vert_acc.saturating_mul(11) / 1500).min(u32::MAX as u64) as u32;
        r.calories_tenths = (self.cal_acc / 1_200_000_000).min(u32::MAX as u64) as u32;
    }
}

/// A session in flight. Nothing here is resident when no session is running
/// except the struct itself, which is fixed size.
struct Session {
    metrics: Metrics,
    /// Slot position of this session's run record, once it has one.
    run_pos: Option<usize>,
    id: FixedStr<{ program_core::record::MAX_ID }>,
    program_name: FixedStr<{ program_core::MAX_PROGRAM_NAME }>,
    /// `record::fingerprint` of the program this session is running, captured
    /// when it starts. It is what lets a history entry or a saved workout find
    /// this run afterwards — `program_fingerprint` on the Pi — and it CANNOT be
    /// the program's name: a rename would orphan every run of it.
    program_fp: u64,
    is_manual: bool,
    last_checkpoint_s: u32,
}

impl Session {
    fn new() -> Session {
        Session {
            metrics: Metrics::default(),
            run_pos: None,
            id: FixedStr::new(),
            program_name: FixedStr::new(),
            program_fp: 0,
            is_manual: false,
            last_checkpoint_s: 0,
        }
    }

    fn run(&self, reason: EndReason, completed: bool) -> Run {
        let mut r = Run::new(self.id.as_str(), self.program_name.as_str(), self.is_manual);
        r.fp = self.program_fp;
        r.end_reason = reason;
        r.program_completed = completed;
        self.metrics.write_into(&mut r);
        r
    }
}

/// Write the run record — creating, checkpointing or finalising it.
///
/// A checkpoint REWRITES THE SAME SLOT (`put_run(Some(pos), …)`). Appending
/// instead would consume the whole 4-slot ring in two minutes of 30-second
/// checkpoints and evict every earlier run, which is the defect this shape
/// exists to prevent.
fn persist(s: &mut Session, reason: EndReason, completed: bool) -> bool {
    // Every slot in flight, or no store. The CALLER retries; reporting success
    // here would leave a finalised run stuck at `in_progress` forever.
    let Ok(mut lease) = reqbudget::admit(reqbudget::SLOT_BYTES) else {
        return false;
    };
    store::with(|st| {
        let buf = lease.buf();
        if s.run_pos.is_none() {
            s.id = st.next_run_id();
        }
        let rec = s.run(reason, completed);
        if !st.put_run(s.run_pos, &rec, buf) {
            return false;
        }
        // Re-find rather than assume: `append` chooses the slot and a replace
        // re-sequences it, so the position that means "this record" is only
        // knowable by looking.
        s.run_pos = st.find_run(rec.id.as_str(), buf);
        s.run_pos.is_some()
    })
    .unwrap_or(false)
}

/// Write the running program's progress back into its history entry, so
/// `/api/programs/history/{id}/resume` can pick the session up where it
/// stopped and `completed` can gate it.
fn persist_progress(
    id: &FixedStr<{ program_core::record::MAX_ID }>,
    interval: u32,
    elapsed_s: u32,
    completed: bool,
) {
    if id.is_empty() {
        return;
    }
    let Ok(mut lease) = reqbudget::admit(reqbudget::SLOT_BYTES) else {
        return;
    };
    store::with(|st| {
        let buf = lease.buf();
        if let Some((pos, mut e)) = st.find(Which::History, id.as_str(), buf) {
            e.last_interval = interval;
            e.last_elapsed_s = elapsed_s;
            e.completed = completed;
            st.put(Which::History, Some(pos), &e, buf);
        }
    });
}

pub fn run(ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        wdt::abort(c"session: task WDT subscribe failed");
    }
    crate::logi!("session recorder started (WDT-supervised)");

    let mut s = Session::new();
    let mut active = false;
    // A finalisation that has not reached flash yet. It is RETRIED every tick
    // until it lands: the budget pool can be momentarily full, and giving up
    // after one attempt would leave a finished run reading `in_progress`
    // forever — which is exactly the state a reader cannot distinguish from a
    // crash mid-workout.
    let mut pending_final: Option<(EndReason, bool)> = None;
    // Why the session that just ended ended, carried into the LAST `session`
    // frame and no further. The Pi's own `to_dict()` reports `end_reason`
    // alongside `active: false`, which is how the app's Running screen knows
    // to run its completion transition rather than just going quiet.
    let mut final_reason: Option<EndReason> = None;

    loop {
        wdt::feed();
        delay_ms(TICK_MS);
        // FED AGAIN AFTER THE DELAY, and this is arithmetic rather than
        // belt-and-braces. The budget is CONFIG_ESP_TASK_WDT_TIMEOUT_S = 2 with
        // PANIC=y, and a panic here is a reboot, which drops the relay mid-run.
        // Feeding only at the top of the loop spent HALF the budget on the
        // delay before any work began, leaving ~1000 ms for a tick that can
        // perform TWO read-modify-writes (`persist` and `persist_progress`, and
        // both again at session end) — each a 4 KB sector erase, datasheet
        // worst case ~400 ms on the W25Q/GD25Q-class parts, before any wait on
        // the STORES mutex behind an HTTP handler doing its own erase. 800 ms
        // of work inside a 1000 ms remainder is not a margin. `wdt::feed()` is
        // one register write; the full 2 s belongs to the work, not to the
        // sleep that precedes it.
        wdt::feed();

        // Program first, then the safety lock — the firmware's one mandatory
        // lock order. The history id is read INSIDE the program hold, so it
        // and the program it describes are one snapshot. Both locks are
        // released before any flash is touched, so a sector erase never
        // happens under either.
        let (running, paused, completed, interval, prog_elapsed, name, fp, manual, hist_id) = {
            let p = lock(&ctx.program);
            (
                p.running(),
                p.paused(),
                p.completed(),
                p.current_interval() as u32,
                p.total_elapsed() as u32,
                p.program().map(|x| x.name),
                // Folded HERE, under the lock, rather than by copying the
                // ~900-byte `Program` out to fold it later.
                p.program().map(program_core::record::fingerprint).unwrap_or(0),
                p.is_manual(),
                current_locked(),
            )
        };
        let (speed_tenths, incline_half) = {
            let g = lock(&ctx.guarded);
            (
                g.controller.speed_tenths().get(),
                g.controller.incline_half_percent().get(),
            )
        };

        let moving = speed_tenths > 0;
        let live = moving || (running && !paused);

        // Retry a finalisation that could not be written last tick, before
        // anything else can start a new session on top of it.
        if let Some((reason, done)) = pending_final {
            if persist(&mut s, reason, done) {
                pending_final = None;
            }
        }

        if live && !active && pending_final.is_none() {
            // A new session. Everything about the previous one is gone —
            // there is no accumulation across runs to leak.
            s = Session::new();
            s.program_name = name.unwrap_or_else(FixedStr::new);
            s.program_fp = fp;
            s.is_manual = manual;
            active = true;
            final_reason = None;
        }

        if active && live {
            if !paused {
                s.metrics.elapsed_s = s.metrics.elapsed_s.saturating_add(1);
            }
            s.metrics.tick(
                speed_tenths,
                incline_half,
                crate::net::profile::current().weight_grams(),
            );

            let due = s.metrics.elapsed_s >= MIN_RUN_S
                && (s.run_pos.is_none()
                    || s.metrics.elapsed_s - s.last_checkpoint_s >= CHECKPOINT_S);
            if due {
                // The checkpoint clock advances only on a WRITE THAT LANDED.
                // Advancing it first meant a failed write waited another 30 s
                // to be retried, which contradicted the comment that said the
                // next tick would try again.
                if persist(&mut s, EndReason::InProgress, false) {
                    s.last_checkpoint_s = s.metrics.elapsed_s;
                }
                if running {
                    persist_progress(&hist_id, interval, prog_elapsed, false);
                }
            }
        }

        if active && !live {
            active = false;
            let reason = if completed {
                EndReason::ProgramComplete
            } else {
                EndReason::UserStop
            };
            final_reason = Some(reason);
            // A session that never reached the floor never existed, exactly as
            // on the Pi: `_save_run_record` is a no-op without a row.
            if s.run_pos.is_some() {
                if !persist(&mut s, reason, completed) {
                    pending_final = Some((reason, completed));
                }
            }
            persist_progress(&hist_id, interval, prog_elapsed, completed);
            if completed {
                // The program is over; a later load starts a fresh history
                // entry, and this one must not keep receiving progress. Under
                // the PROGRAM lock, like every other write to this id.
                let _p = lock(&ctx.program);
                *lock(&CURRENT_HISTORY) = FixedStr::new();
            }
        }

        // LAST, after every lock is released and every flash write is done.
        // This only PUBLISHES and ASKS — the frames are rendered and sent on
        // the httpd task (see `net::ws`), so nothing here can block on a
        // network write and nothing here is a second writer to a TLS session.
        publish(&s, active, final_reason);
        crate::net::ws::request_push();
    }
}
