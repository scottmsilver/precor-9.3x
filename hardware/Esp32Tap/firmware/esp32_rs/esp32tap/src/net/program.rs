//! The program endpoints — how a workout gets onto the device, and how a
//! human drives it once it is there.
//!
//! `GET /api/program`, `POST /api/program/{load,start,stop,pause,skip,prev,
//! extend,adjust-duration,quick-start}`. The contract is `python/server.py`'s,
//! endpoint for endpoint: every one of them answers with the SAME
//! `ProgramState.to_dict()` body the Pi returns, so the Android app cannot
//! tell which machine it is talking to.
//!
//! # `/api/program/load` is an addition, and it has to be
//!
//! The Pi's loader is `POST /api/program/generate`, which calls Gemini. There
//! is no Gemini here and there never will be — this device is standalone. So
//! the program arrives already-formed, as JSON, from whatever built it. `load`
//! stores it; `start` runs it. `start` also accepts a program body, so a
//! tablet can load-and-run in one request and then walk away, which is the
//! entire point.
//!
//! # Nothing here is needed to RUN a program
//!
//! These handlers put a program into `CTX.program` and take it out again.
//! Between those two acts the network is irrelevant: `tasks::interval_executor`
//! reads that same state on its own 1 s clock and commands the belt with no
//! reference to a socket, a session, or a connected client. Unplug the
//! Ethernet mid-workout and the workout finishes.
//!
//! # Bounded, at the door
//!
//! `reqbudget::admit` refuses a body larger than one slot BEFORE a byte is
//! parsed, and `program_core` cannot represent more than `MAX_INTERVALS`
//! intervals whatever the body says. The const assertion below ties the two
//! together so they cannot drift.

use crate::context::lock;
use crate::control;
use crate::net::api::{parse_key_hundredths, read_body, respond, MAX_CMD_BODY};
use crate::tasks::interval_executor::{apply_plan, apply_program_entry};
use esp_idf_sys as sys;
use program_core::{json, Plan, Program, ProgramState};
use safety_core::units::{InclineHalfPct, Micros, SpeedTenths};

/// A program submission must fit one request slot, because admission refuses
/// anything larger before parsing. `program_core` derives its interval count
/// from this number; assert the two agree at COMPILE time so raising one
/// without the other is a build failure rather than a silent 413 on the
/// largest legitimate workout.
const _: () = assert!(program_core::MAX_PROGRAM_JSON_BYTES == reqbudget::SLOT_BYTES);

/// Response buffer for a program snapshot.
///
/// A stack buffer, not a heap one, and sized from the same derivation:
/// `max_program_json_bytes()` plus the fixed `to_dict()` envelope. The
/// serialiser writes through [`Sink`], which REFUSES to overflow, so a wrong
/// number here truncates into a 500 rather than smashing the stack — and the
/// assertion below makes a wrong number a build failure anyway.
pub(crate) const STATE_BUF: usize = program_core::model::max_program_json_bytes() + 192;

/// Worst-case stack this module puts on the httpd task, in bytes.
///
/// STATE_BUF ALONE IS NOT THE FRAME, and asserting on it alone was a guard
/// that measured the wrong quantity: it would still have passed if `Program`
/// grew to 8 KB. `post_impl` also holds the parsed body as an
/// `Option<Program>` BY VALUE and a `[u8; MAX_CMD_BODY]`, and `read_program`'s
/// own frame — which returns `Body::Parsed(Program)` by value — is live
/// underneath it, so the program is counted twice. Measured against the built
/// ELF, `post_handler` is the largest single frame in the image (4288 B), so
/// this is the number that matters, not the response buffer.
/// `record_loaded` runs INSIDE this frame (the history write happens before
/// the program lock is taken), so its own worst case is part of this number
/// rather than a separate budget. Leaving it out is what rebooted the device
/// the first time this tier ran — see that constant's note.
const POST_FRAME_BYTES: usize = STATE_BUF
    + 2 * core::mem::size_of::<Option<Program>>()
    + MAX_CMD_BODY
    + crate::net::records::HISTORY_WRITE_FRAME_BYTES;

/// Stack that must remain BELOW this module's frame for the code it calls.
///
/// Both `httpd_req_recv` (-> httpd_ssl_recv -> mbedtls_ssl_read) and `respond`
/// (-> httpd_resp_send -> httpd_ssl_send -> mbedtls_ssl_write) are called FROM
/// INSIDE `post_impl`'s frame, so the mbedtls record layer sits on top of it;
/// level-1 interrupts also run on the interrupted task's stack (see the note
/// in `context.rs`). Reserved, not hoped for.
const MBEDTLS_HEADROOM_BYTES: usize = 4096;

const _: () = assert!(
    POST_FRAME_BYTES + MBEDTLS_HEADROOM_BYTES < crate::net::http::HTTPD_STACK_BYTES as usize,
    "net::program's worst-case frame plus mbedtls headroom no longer fits the \
     httpd task stack — raise net::http::HTTPD_STACK_BYTES or box the parsed \
     program through a reqbudget slot instead of returning it by value"
);

/// A bounded `core::fmt::Write` sink. Overflow is an ERROR, never a truncation:
/// a half-written JSON body would be parsed by the app as a different workout.
struct Sink<'a> {
    buf: &'a mut [u8; STATE_BUF],
    len: usize,
    overflowed: bool,
}

impl core::fmt::Write for Sink<'_> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let b = s.as_bytes();
        if self.len + b.len() > self.buf.len() {
            self.overflowed = true;
            return Err(core::fmt::Error);
        }
        self.buf[self.len..self.len + b.len()].copy_from_slice(b);
        self.len += b.len();
        Ok(())
    }
}

/// Render the state body — the shared reply of EVERY endpoint here, exactly as
/// `server.py` returns `sess.prog.to_dict()` from all of them.
///
/// Takes the caller's buffer rather than returning one, and takes `&state`
/// rather than a copy: `ProgramState` is ~1.5 KB, and neither a copy of it nor
/// a second buffer belongs on the httpd task's stack alongside an
/// mbedtls handshake.
pub(crate) fn render_state(
    buf: &mut [u8; STATE_BUF],
    state: &ProgramState,
    lead: &str,
) -> Option<usize> {
    let mut sink = Sink {
        buf,
        len: 0,
        overflowed: false,
    };
    if json::write_state_with_lead(&mut sink, state, lead).is_err() || sink.overflowed {
        return None;
    }
    Some(sink.len)
}

/// The lead every POST verb answers with.
///
/// `server.py` puts `"ok": True` on quick-start and resume only; the app types
/// quick-start as `GenericOkResponse`, whose `ok` has no kotlinx default, so
/// omitting it makes the app toast "Failed to start workout" while the belt
/// starts. Applying it to every verb rather than to the two the Pi decorates
/// costs 10 bytes and is invisible to a client that types the reply as a
/// program state (`ignoreUnknownKeys`); the alternative is a per-verb table of
/// which replies the Pi happens to wrap, which is a rule nobody could keep.
/// GET keeps the bare `to_dict()` shape.
const OK_LEAD: &str = r#""ok":true,"#;

/// Lock, render into a stack buffer, RELEASE, then send.
///
/// The order matters and is the whole reason this is a function rather than
/// three lines at each call site: a chunked send blocks for up to
/// `send_wait_timeout` per flush, and the interval executor takes this same
/// lock every tick under a 2 s watchdog. Holding it across the socket write
/// would let a client that stops reading reboot the device.
pub(crate) fn respond_state(req: *mut sys::httpd_req_t, lead: &str) -> sys::esp_err_t {
    let mut buf = [0u8; STATE_BUF];
    let n = {
        let p = lock(&crate::CTX.program);
        render_state(&mut buf, &p, lead)
    };
    respond_rendered(req, &buf, n)
}

fn respond_rendered(req: *mut sys::httpd_req_t, buf: &[u8], n: Option<usize>) -> sys::esp_err_t {
    match n {
        Some(n) => respond(req, c"200 OK", &buf[..n]),
        None => respond(
            req,
            c"500 Internal Server Error",
            br#"{"ok":false,"error":"state too large to render"}"#,
        ),
    }
}

/// Run a plan produced by a program operation, under the safety lock.
///
/// Called with the PROGRAM lock already held — that is the mandatory order
/// (`program` then `guarded`, see `context.rs`) and the reason the decision
/// and the belt command cannot be interleaved with a concurrent tick.
pub(crate) fn drive(plan: Plan, release_belt: bool) -> Result<usize, control::Reject> {
    if plan.is_empty() && !release_belt {
        return Ok(0);
    }
    let now = crate::CTX.clock.now();
    let mut g = lock(&crate::CTX.guarded);
    apply_plan(&mut g, plan, release_belt, now)
}

/// Prepare, safely enter, then commit a program Start under program→guarded.
pub(crate) fn start_transaction(
    p: &mut ProgramState,
    now: Micros,
    resume_interval: usize,
    resume_elapsed: i64,
) -> Result<(), control::Reject> {
    let prepared = p.prepare_start(now, resume_interval, resume_elapsed);
    if prepared.plan().is_empty() {
        return Err(control::Reject::Refused);
    }
    let mut g = lock(&crate::CTX.guarded);
    match apply_program_entry(&mut g, prepared.plan(), now) {
        Ok(_) => {
            // The accepted acquisition and sticky-inhibit clear are one
            // guarded act. No ordinary executor tick can observe a gap.
            g.executor_inhibited = false;
            g.executor_inhibited_at = None;
            p.commit_start(prepared);
            Ok(())
        }
        Err(reject) => {
            g.executor_inhibited = true;
            Err(reject)
        }
    }
}

/// Resume a safety/user-paused program with the same atomic entry contract.
pub(crate) fn resume_transaction(p: &mut ProgramState, now: Micros) -> Result<(), control::Reject> {
    let prepared = p.prepare_resume(now);
    if prepared.plan().is_empty() {
        return Err(control::Reject::Refused);
    }
    let mut g = lock(&crate::CTX.guarded);
    match apply_program_entry(&mut g, prepared.plan(), now) {
        Ok(_) => {
            g.executor_inhibited = false;
            g.executor_inhibited_at = None;
            p.commit_resume(prepared);
            Ok(())
        }
        Err(reject) => {
            g.executor_inhibited = true;
            Err(reject)
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PauseOutcome {
    Paused,
    NotRunning,
}

/// Pause only after the zero-speed command was accepted.
///
/// `NotRunning` is a Pi-level semantic no-op, not a safety rejection. Keeping
/// it outside `control::Reject` lets HTTP preserve the existing 200/ok:false
/// contract while genuine controller refusals remain 409.
pub(crate) fn pause_transaction(
    p: &mut ProgramState,
    now: Micros,
) -> Result<PauseOutcome, control::Reject> {
    if !p.running() {
        return Ok(PauseOutcome::NotRunning);
    }
    let plan = p.prepare_pause();
    let mut g = lock(&crate::CTX.guarded);
    apply_plan(&mut g, plan, false, now)?;
    p.commit_pause(now);
    Ok(PauseOutcome::Paused)
}

/// The WHOLE meaning of Stop: the program's plan, and then the belt itself.
///
/// ONE function so `POST /api/program/stop` and the coach's `stop_treadmill`
/// cannot drift. Before this existed, both drove `ProgramState::stop()`'s plan
/// and nothing else — which is empty when no program is running, so a manually
/// commanded belt kept its speed and the coach's transcript still said
/// "treadmill stopped".
///
/// Called with the PROGRAM lock held, like [`drive`], and takes the safety lock
/// ONCE for both halves so nothing can command the belt in between.
///
/// Returns whether the belt is now commanded to zero.
pub(crate) fn drive_stop(plan: Plan) -> bool {
    let now = crate::CTX.clock.now();
    let mut g = lock(&crate::CTX.guarded);
    let _ = apply_plan(&mut g, plan, true, now);
    crate::control::stop_belt(&mut g, now)
}

// ---------------------------------------------------------------------------
// Body reading for the program-sized endpoints.
//
// `net::api::read_body` caps at 128 bytes, which is right for `{"value":3.0}`
// and far too small for a workout. This one admits a full slot and reads into
// it. The lease is HELD across the parse — unlike the small path, which copies
// out and releases immediately — because the parse reads directly from the
// slot and never copies the body a second time.
// ---------------------------------------------------------------------------

/// Outcome of reading a program body. `Answered` means the refusal has already
/// been sent.
enum Body {
    Parsed(Program),
    Answered,
    /// No body at all — `POST /api/program/start` with nothing in it.
    Empty,
}

/// SAFETY-FREE apart from the two FFI calls it makes; see each site.
fn read_program(req: *mut sys::httpd_req_t) -> Body {
    // SAFETY: reading a scalar field of a live request.
    let declared = unsafe { (*req).content_len };
    if declared == 0 {
        return Body::Empty;
    }

    // ADMISSION FIRST — nothing is read before the budget says yes. A body
    // that will not fit a slot is 413 before a byte is parsed.
    let mut lease = match reqbudget::admit(declared) {
        Ok(l) => l,
        Err(reqbudget::Refusal::TooLarge) => {
            respond(
                req,
                c"413 Payload Too Large",
                br#"{"ok":false,"error":"program too large"}"#,
            );
            return Body::Answered;
        }
        Err(reqbudget::Refusal::Busy) => {
            respond(
                req,
                c"503 Service Unavailable",
                br#"{"ok":false,"error":"server busy"}"#,
            );
            return Body::Answered;
        }
    };

    // The WHOLE read is bounded, not each recv — see `net::api::Deadline`. A
    // program body is the largest thing this server accepts, so it is also the
    // easiest one to dribble.
    let deadline = crate::net::api::Deadline::start();
    let mut got = 0usize;
    while got < declared {
        if deadline.expired() {
            crate::net::api::abandon_body(req);
            return Body::Answered;
        }
        let buf = lease.buf();
        // SAFETY: `buf` is a live exclusive borrow of a slot that admission
        // proved is at least `declared` bytes; IDF writes at most the length
        // we pass, at an offset inside it.
        let n = unsafe {
            sys::httpd_req_recv(
                req,
                buf.as_mut_ptr().add(got) as *mut core::ffi::c_char,
                declared - got,
            )
        };
        if n <= 0 {
            respond(
                req,
                c"400 Bad Request",
                br#"{"ok":false,"error":"short body"}"#,
            );
            return Body::Answered;
        }
        got += n as usize;
    }

    match json::parse_program(&lease.buf()[..got]) {
        Ok(p) => Body::Parsed(p),
        Err(e) => {
            let msg: &[u8] = match e {
                json::ParseError::TooManyIntervals => {
                    br#"{"ok":false,"error":"too many intervals"}"#
                }
                json::ParseError::NoIntervals => {
                    br#"{"ok":false,"error":"program has no intervals"}"#
                }
                json::ParseError::MissingField => {
                    br#"{"ok":false,"error":"interval missing duration, speed or incline"}"#
                }
                json::ParseError::NumberOutOfRange => {
                    br#"{"ok":false,"error":"number out of range"}"#
                }
                json::ParseError::Malformed => br#"{"ok":false,"error":"malformed program"}"#,
            };
            respond(req, c"400 Bad Request", msg);
            Body::Answered
        }
    }
}

// ---------------------------------------------------------------------------
// Handlers. `user_ctx` selects the verb so nine POST routes share one function
// — the same trick `motion_handler` uses for speed vs incline.
// ---------------------------------------------------------------------------

const V_LOAD: usize = 0;
const V_START: usize = 1;
const V_STOP: usize = 2;
const V_PAUSE: usize = 3;
const V_SKIP: usize = 4;
const V_PREV: usize = 5;
const V_EXTEND: usize = 6;
const V_ADJUST: usize = 7;
const V_QUICK: usize = 8;

// THE TWO IDF ENTRY POINTS ARE THIN ON PURPOSE.
//
// `check_unsafe_budget.py` attributes every source line of an
// `unsafe extern "C" fn` to the unsafe budget, and that is the right rule: the
// whole body of such a function runs with the compiler's guarantees relaxed.
// So each one below does the minimum an IDF callback must — read the one raw
// field it needs — and hands off to a SAFE function. The logic that decides
// what happens to the belt is not inside an unsafe region.

/// GET /api/program.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn get_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    get_impl(req)
}

/// Every POST verb; `user_ctx` selects which.
///
/// SAFETY: `req` is live for the call. Reading `user_ctx` reads a scalar field
/// this module itself set at registration; nothing derived from `req` is
/// retained.
unsafe extern "C" fn post_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    post_impl(req, (*req).user_ctx as usize)
}

fn get_impl(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; STATE_BUF];
    let n = {
        let p = lock(&crate::CTX.program);
        render_state(&mut buf, &p, "")
    };
    respond_rendered(req, &buf, n)
}

fn post_impl(req: *mut sys::httpd_req_t, verb: usize) -> sys::esp_err_t {
    // Bodies are read BEFORE the program lock is taken. `httpd_req_recv` can
    // block on a dribbling client for up to `recv_wait_timeout`, and holding
    // the program lock across that would stall the interval executor — the one
    // task that must never wait on the network.
    let program = match verb {
        V_LOAD | V_START => match read_program(req) {
            Body::Answered => return sys::ESP_OK,
            Body::Parsed(p) => Some(p),
            Body::Empty => None,
        },
        _ => None,
    };

    // `server.py::_add_to_history` — every program that is LOADED is
    // remembered, not only ones that are saved. Done here, before the program
    // lock is taken, so a 4 KB sector erase never sits inside the critical
    // section the interval executor contends for.
    //
    // Quick Start is deliberately excluded, exactly as on the Pi:
    // `ensure_manual` does not write history, and a lobby full of "Quick
    // Start" entries would evict the workouts the user actually built.
    let history_id = program
        .as_ref()
        .and_then(|p| crate::net::records::record_loaded(p, ""));

    // Small-body verbs read their scalar exactly the way `/api/speed` does.
    let mut small_buf = [0u8; MAX_CMD_BODY];
    let small_len = match verb {
        V_EXTEND | V_ADJUST | V_QUICK => match read_body(req, &mut small_buf) {
            Some(n) => n,
            None => return sys::ESP_OK, // already answered
        },
        _ => 0,
    };
    let small = &small_buf[..small_len];

    if verb == V_LOAD && program.is_none() {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"no program in body"}"#,
        );
    }

    // WHICH VERBS REPLACE THE LOADED PROGRAM. This is the question the history
    // id has to be republished against, and answering it by "did a history
    // write happen?" was a defect rather than a shortcut: Quick Start reads no
    // program body, so `record_loaded` was never called, `set_current` was
    // never called either, and CURRENT_HISTORY still named the program that
    // was LOADED BEFORE IT. The session recorder then checkpointed the Quick
    // Start's interval, elapsed time and `completed` into that entry —
    // MEASURED: a 1-minute Quick Start marked an untouched 60-minute program
    // completed, which refuses `/resume` for it forever. The same hole swallows
    // a genuinely new program whenever `record_loaded` returns None (the
    // reqbudget pool momentarily full, or the ring write failed).
    //
    // So it is an ELSE, not an omission: a verb that replaces the program
    // publishes the new id or CLEARS it. `V_START` WITHOUT a body deliberately
    // does not, because it does not replace anything — it starts what
    // `/api/programs/history/{id}/load` just installed, and clearing there
    // would throw away the entry the run is supposed to be written back to.
    let replaces_program =
        matches!(verb, V_LOAD | V_QUICK) || (verb == V_START && program.is_some());

    let now = crate::CTX.clock.now();
    let mut p = lock(&crate::CTX.program);
    // UNDER THE PROGRAM LOCK, in the same hold as the `load` below. The
    // session recorder reads the program and this id together while holding
    // the same lock, so it can never write one program's progress into the
    // other's history entry.
    if replaces_program {
        match history_id.as_ref() {
            Some(id) => crate::net::session::set_current(id),
            None => crate::net::session::set_current(&safety_core::FixedStr::new()),
        }
    }

    let (plan, release_belt, error): (Plan, bool, Option<&[u8]>) = match verb {
        V_LOAD => {
            // Loading over a RUNNING program stops it first, belt and all.
            // Python's `load` only cancels the task; here the belt is ours to
            // stop and leaving it moving under a program that no longer exists
            // is not a state this device will hold.
            let stop = p.stop();
            p.load(program.expect("checked above"));
            (stop, !stop.is_empty(), None)
        }
        V_START => {
            if let Some(prog) = program {
                p.load(prog);
            }
            if p.program().is_none() {
                (
                    Plan::none(),
                    false,
                    Some(br#"{"ok":false,"error":"No program loaded"}"#),
                )
            } else {
                if let Err(reject) = start_transaction(&mut p, now, 0, 0) {
                    drop(p);
                    return respond_reject(req, reject);
                }
                (Plan::none(), false, None)
            }
        }
        V_STOP => {
            let plan = p.stop();
            (plan, true, None)
        }
        V_PAUSE => {
            let result = if p.paused() {
                resume_transaction(&mut p, now).map(|()| PauseOutcome::Paused)
            } else {
                pause_transaction(&mut p, now)
            };
            match result {
                Ok(PauseOutcome::Paused) => {}
                Ok(PauseOutcome::NotRunning) => {
                    drop(p);
                    return respond(
                        req,
                        c"200 OK",
                        br#"{"ok":false,"error":"No program running"}"#,
                    );
                }
                Err(reject) => {
                    drop(p);
                    return respond_reject(req, reject);
                }
            }
            (Plan::none(), false, None)
        }
        V_SKIP => {
            let plan = p.skip(now);
            let ended = !p.running();
            (plan, ended, None)
        }
        V_PREV => (p.prev(now), false, None),
        V_EXTEND => {
            let ok = parse_seconds(small, b"seconds").is_some_and(|s| p.extend_current(s));
            if ok {
                (Plan::none(), false, None)
            } else {
                (
                    Plan::none(),
                    false,
                    Some(br#"{"ok":false,"error":"No program running"}"#),
                )
            }
        }
        V_ADJUST => {
            let ok = parse_seconds(small, b"delta_seconds").is_some_and(|s| p.adjust_duration(s));
            if ok {
                (Plan::none(), false, None)
            } else {
                (
                    Plan::none(),
                    false,
                    Some(br#"{"ok":false,"error":"No manual program running"}"#),
                )
            }
        }
        V_QUICK => {
            // `server.py::api_quick_start` -> `sess.ensure_manual()`: one
            // manual interval, started immediately. Defaults match the Pi's
            // `QuickStartRequest` (3.0 mph, 0%, 60 min).
            //
            // THE STOP PLAN IS DRIVEN WITHOUT RELEASING THE BELT, and that is
            // load-bearing. `release_belt=true` here reached
            // `control::release` -> `request_normal_exit`, putting the
            // controller in ExitWaitGap; the new program's plan was then driven
            // IN THE SAME LOCK HOLD, and `control::command` accepted it (it was
            // still the lease owner) but did NOT re-enter emulate, because that
            // only happens from Proxy. The exit then ran to completion, dropped
            // the relay and released the lease, and nothing re-commanded — so a
            // 60-minute Quick Start reported `running:true` at 2.0 mph over a
            // dead belt for an hour, with /api/status advertising a speed the
            // motor was never given.
            //
            // Zeroing via the stop plan is all that is wanted here: the new
            // program's own start plan re-commands under the SAME lease three
            // lines later, which is exactly why V_START never had this bug. If
            // a release is ever wanted it must COMPLETE (mode back to Proxy)
            // before the replacement plan is driven.
            let stop = p.stop();
            if !stop.is_empty() {
                let _ = drive(stop, false);
            }
            p.load(quick_program(small));
            if let Err(reject) = start_transaction(&mut p, now, 0, 0) {
                drop(p);
                return respond_reject(req, reject);
            }
            (Plan::none(), false, None)
        }
        _ => (
            Plan::none(),
            false,
            Some(br#"{"ok":false,"error":"unknown verb"}"#),
        ),
    };

    if let Some(msg) = error {
        // `server.py` answers 200 with `{"ok": false, "error": ...}` for these
        // (no program loaded, nothing running, not manual). Keep that shape:
        // the app already handles it, and a 4xx here would be a new contract.
        drop(p);
        return respond(req, c"200 OK", msg);
    }

    // STOP IS THE ONE VERB THAT ALSO OWES THE BELT. Every other verb drives
    // its plan; Stop drives its plan AND zeroes whatever surface still holds
    // the belt, because with no program running the plan is empty and a
    // manually commanded belt kept its speed through a Stop that answered 200.
    // The coach's `stop_treadmill` calls the same function.
    if verb == V_STOP {
        drive_stop(plan);
    } else {
        let _ = drive(plan, release_belt);
    }
    let mut buf = [0u8; STATE_BUF];
    let n = render_state(&mut buf, &p, OK_LEAD);
    drop(p);
    respond_rendered(req, &buf, n)
}

pub(crate) fn respond_reject(
    req: *mut sys::httpd_req_t,
    reject: control::Reject,
) -> sys::esp_err_t {
    let body: &[u8] = match reject {
        control::Reject::NotOwner => {
            br#"{"ok":false,"error":"belt is owned by another control surface"}"#
        }
        control::Reject::ExecutorInhibited => {
            br#"{"ok":false,"error":"program executor is safety-paused"}"#
        }
        control::Reject::Refused => br#"{"ok":false,"error":"rejected by safety controller"}"#,
        control::Reject::GenerationExhausted => {
            br#"{"ok":false,"error":"control generation exhausted"}"#
        }
    };
    respond(req, c"409 Conflict", body)
}

/// `{"seconds": -30}` / `{"delta_seconds": 300}`.
///
/// Reuses the value scanner from `/api/speed` by looking for the key first, so
/// there is one number parser on the small-body path rather than two.
fn parse_seconds(body: &[u8], key: &[u8]) -> Option<i64> {
    // Through the SHARED, ANCHORED key scanner. Finding the bare bytes meant
    // `seconds` matched inside `delta_seconds`, so a body aimed at
    // `/adjust-duration` also satisfied `/extend`.
    let hundredths = parse_key_hundredths(body, key)?;
    // Clamp to the Pi's own bound (`Field(ge=-3600, le=3600)`) so a hostile
    // body cannot ask for a year.
    Some((hundredths as i64 / 100).clamp(-3600, 3600))
}

/// `sess.ensure_manual()` — the single-interval free-run program.
fn quick_program(body: &[u8]) -> Program {
    let speed = find_number(body, b"speed").unwrap_or(300); // 3.00 mph
    let incline = find_number(body, b"incline").unwrap_or(0);
    let minutes = find_number(body, b"duration_minutes").unwrap_or(60_00) / 100;
    let seconds = minutes.clamp(1, 300) as i64 * 60;

    let mut p = Program::new("Quick Start", true);
    p.push(program_core::Interval::new(
        "Seg 1",
        seconds.clamp(0, u32::MAX as i64) as u32,
        SpeedTenths::new(speed / 10),
        InclineHalfPct::new(incline / 50),
    ));
    p
}

fn find_number(body: &[u8], key: &[u8]) -> Option<i32> {
    // ONE number scanner in the firmware, and it is the anchored one. The
    // unanchored copy that used to live here matched a key inside a VALUE and
    // matched `speed` inside a longer key.
    parse_key_hundredths(body, key)
}

/// Register every program route on the already-started server.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly and
    // introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 10] = [
        (
            c"/api/program",
            sys::http_method_HTTP_GET,
            get_handler,
            usize::MAX,
        ),
        (
            c"/api/program/load",
            sys::http_method_HTTP_POST,
            post_handler,
            V_LOAD,
        ),
        (
            c"/api/program/start",
            sys::http_method_HTTP_POST,
            post_handler,
            V_START,
        ),
        (
            c"/api/program/stop",
            sys::http_method_HTTP_POST,
            post_handler,
            V_STOP,
        ),
        (
            c"/api/program/pause",
            sys::http_method_HTTP_POST,
            post_handler,
            V_PAUSE,
        ),
        (
            c"/api/program/skip",
            sys::http_method_HTTP_POST,
            post_handler,
            V_SKIP,
        ),
        (
            c"/api/program/prev",
            sys::http_method_HTTP_POST,
            post_handler,
            V_PREV,
        ),
        (
            c"/api/program/extend",
            sys::http_method_HTTP_POST,
            post_handler,
            V_EXTEND,
        ),
        (
            c"/api/program/adjust-duration",
            sys::http_method_HTTP_POST,
            post_handler,
            V_ADJUST,
        ),
        (
            c"/api/program/quick-start",
            sys::http_method_HTTP_POST,
            post_handler,
            V_QUICK,
        ),
    ];
    for (path, method, handler, ctx) in routes {
        let uri = sys::httpd_uri_t {
            uri: path.as_ptr(),
            method,
            handler: Some(handler),
            user_ctx: if ctx == usize::MAX {
                core::ptr::null_mut()
            } else {
                ctx as *mut core::ffi::c_void
            },
            is_websocket: false,
            handle_ws_control_frames: false,
            supported_subprotocol: core::ptr::null(),
        };
        // SAFETY: `handle` is a live server; `uri` is read for the call and
        // IDF copies what it retains. The handler is a `'static` fn.
        let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
        if err != sys::ESP_OK {
            return Err(err);
        }
    }
    Ok(())
}
