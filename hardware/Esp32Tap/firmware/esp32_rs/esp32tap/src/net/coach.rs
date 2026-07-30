//! The AI coach — the socket half. `coach_core` is the judgement half.
//!
//! # THE SHAPE OF THE CALL, AND WHY IT IS THIS SHAPE
//!
//! **IDF runs ONE httpd worker.** Every request this device answers — including
//! `POST /api/program/stop`, the Stop button, with the belt moving — is
//! serialised behind it. That is not a detail of this module; it is the fact
//! that has already produced two belt-availability defects in this firmware:
//! a peer that completed TCP and then went silent parked the worker for the
//! whole TLS handshake budget (10 s at the IDF default, measured: Stop went
//! from 0.25 s to 15.25 s), and a peer that dribbled a body one byte per half
//! second held it for 60 s and rising. `net::http`'s handshake budget and
//! `net::api::Deadline` are what those cost.
//!
//! **A Gemini call takes SECONDS.** Not milliseconds — seconds, dominated by
//! the model, not by us, and unbounded from this device's point of view. So
//! making that call from the httpd handler would be building the same denial
//! deliberately, and it would be the worst version of it yet: not an attacker
//! holding the worker, but the product's own headline feature holding it, on
//! every use, by design.
//!
//! **So the handler waits for nothing at all.** `POST /api/chat` does exactly
//! four things — admit through `reqbudget`, read a bounded body, drop the
//! message into a single-slot mailbox, and answer — and then it is done with
//! the worker. Occupancy is a memcpy. The round trip happens on
//! [`run`], a task of its own, and the answer is delivered two ways: pushed
//! down `/ws` as a `coach` frame the moment it lands, and readable at
//! `GET /api/chat` for a client that would rather poll.
//!
//! **A "bounded wait" in the handler was considered and rejected**, because it
//! only renames the problem. Any budget long enough to usually return the
//! answer (2 s? 5 s?) is a budget long enough to be a multi-second Stop outage
//! on every coach message; any budget short enough to be safe (250 ms) never
//! returns the answer anyway, so the client needs the async path regardless and
//! the wait bought nothing but the outage. There is no middle setting that is
//! both useful and safe, which is why there is no wait.
//!
//! `test_stop_stays_responsive_while_a_coach_call_is_in_flight` in
//! `tools/qemu_scenarios/test_coach.py` is the assertion, not this paragraph: a
//! stub endpoint holds the device's request open for 8 s while the scenario
//! drives `POST /api/program/stop` with a program running and the belt moving,
//! and requires Stop to complete within 2 s of its own baseline.
//!
//! WHAT THIS COSTS THE ANDROID APP, stated rather than left to be discovered.
//! `ChatResponse` decodes the 202 body without complaint (`text` and `actions`
//! have kotlinx defaults), so nothing crashes — and nothing is SHOWN either,
//! because the app does not yet handle the `coach` frame or poll
//! `GET /api/chat`. Bead precor-9_3x-lsx is that change; it is small, it is in
//! `TreadmillViewModel.handleMessage`, and it is a no-op against the Pi (which
//! answers 200 with the full body and never sends a coach frame), so one client
//! works with both machines.
//!
//! The SYMPTOM until it lands was worse than "nothing is shown", and that was
//! measured rather than reasoned: `ChatSheet.kt:56-57` is
//! `onToast(res.text.ifBlank { "No response" })`, so an empty `text` in the 202
//! made every coach message on this device toast the words "No response" — a
//! healthy device reading as a broken feature. [`PENDING_TEXT`] is why it now
//! reads as a slow one instead.
//!
//! The 429 for a SECOND message while one is in flight keeps its status, and
//! that is a decision. Retrofit throws on any non-2xx, so the crafted sentence
//! in its body never reaches the user — the app says "Error connecting to AI"
//! for a device that is working correctly, which is bead precor-9_3x-lsx's to
//! fix along with the frame. Answering 202 instead would be worse than the
//! wrong toast: it would tell the client the message was ACCEPTED and hand it
//! a turn number belonging to somebody else's message, so it would wait forever
//! for an answer to a question the device never took. A refusal that is honest
//! about being a refusal is the smaller of the two wrongs.
//!
//! # ONE MODEL CALL PER TURN, NOT THE PI'S THREE
//!
//! `server.py::_run_chat_core` loops up to three times: it executes the tool
//! calls, appends a `functionResponse` for each, and asks again so the model can
//! narrate what happened. This device makes ONE call (plus the generation call,
//! when a workout is asked for), and that is a deliberate divergence rather than
//! an unfinished port.
//!
//! Each extra round trip is SECONDS of latency on a device whose whole point is
//! that the answer arrives while you are still running, and it buys narration —
//! the RESULTS are already reported to the user directly, in the `result` string
//! of each action, rendered from the same `describe()` the model would have been
//! told. What is genuinely lost is the model's chance to react to a refusal
//! ("a workout is running, so I left the belt alone") within the same turn; the
//! user sees the sentence and can ask again, which is one message instead of
//! several seconds on every message.
//!
//! # THE COACH TASK IS NOT WDT-SUPERVISED, and that is deliberate
//!
//! Same argument as `ble::run`, for the same reason: the task watchdog's remedy
//! is a panic, which is a silent reboot, which drops the relay MID-RUN. Trading
//! a working treadmill for a stalled coach is the wrong trade every time. A
//! network call is exactly the kind of unbounded wait a 2 s watchdog cannot
//! coexist with, so the task is bounded by its own budgets instead —
//! [`HTTP_TIMEOUT_MS`] per socket operation and [`TURN_BUDGET`] over the whole
//! turn — and a turn that overruns is ABANDONED and reported, not rebooted
//! through. It is named in `tasks/mod.rs`'s matrix as an absence so the next
//! person does not "fix" it.
//!
//! It never holds the SAFETY or PROGRAM lock across a network call, which is
//! the part that matters: it takes `CTX.guarded` / `CTX.program` only to APPLY
//! an already-validated, already-clamped action, for microseconds, exactly as
//! the interval executor does. It DOES hold its own `WORK` lock across the
//! round trip — stated rather than glossed, because "holds no lock" would be
//! false. `WORK` is the coach's own request buffer, scanner and history ring,
//! and this task is the only code that can name it, so nothing else can ever
//! wait on it.
//!
//! # THE KEY IS A PER-DEVICE SECRET
//!
//! It lives in NVS, is written by `POST /api/coach/key`, and is never returned
//! by any endpoint, never logged, never put in an error message, and never
//! placed in a URL. `GET /api/coach` reports whether one is CONFIGURED and
//! nothing about what it is.
//!
//! **It is sent as a header, not a query parameter**, and that is a security
//! decision rather than a style one: `?key=...` lands in every proxy log, every
//! redirect `Location`, and every error body that echoes the request URL.
//!
//! **It is only ever sent to the pinned host.** The endpoint is configurable
//! ([`NVS_KEY_URL`]) because hard-coding a server URL is forbidden in this
//! project and because the QEMU scenarios need a stub they control — and a
//! configurable endpoint on an UNAUTHENTICATED LAN surface is an exfiltration
//! path: anyone could point the device at their own server and collect the key
//! on the next turn. So [`key_allowed`] sends the key only when the URL begins
//! with [`PINNED_PREFIX`]. A stub endpoint therefore never receives a key, and
//! there is no configuration in which one leaves the device to anywhere else.
//!
//! # TLS AND THE CA BUNDLE
//!
//! `crt_bundle_attach = esp_crt_bundle_attach`. The bundle is already enabled
//! in the generated sdkconfig by IDF's own defaults
//! (`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE=y`,
//! `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_DEFAULT_FULL=y`), so nothing here changes
//! Kconfig — but referencing `esp_crt_bundle_attach` is what pulls the embedded
//! blob into the image. **MEASURED FLASH COST: 68,983 bytes** (the generated
//! `esp-idf/mbedtls/x509_crt_bundle`, the full Mozilla root store). That figure
//! is exact because it is a file on disk.
//!
//! The IMAGE delta is not attributed to this tier, and that is deliberate
//! rather than coy: the qemu-test image went from 1,143,152 to 1,295,952 bytes
//! of a 2,097,152-byte factory partition (54% -> 61%) across a window in which
//! the persistence tier ALSO swapped `recstore` for LittleFS, so the ~150 KB is
//! two changes and splitting it by eye would be a number nobody could
//! reproduce. What is certain: the bundle is 68,983 of it, and the partition
//! has ~800 KB spare. `..._DEFAULT_CMN` would cut the bundle to roughly a third and
//! is the obvious lever if the image ever gets tight; it is NOT taken now
//! because changing an sdkconfig key is gated by `check_sdkconfig.py` and
//! deserves its own deliberate act.
//!
//! # RESIDENT MEMORY IS CONSTANT
//!
//! `coach_core::resident_bytes()` plus this module's mailbox and reply slot.
//! Nothing here grows with conversation length, with the number of requests, or
//! with how large a model reply is: the request body is a fixed static buffer
//! whose worst case is a compile-time assertion, the reply is streamed through
//! a [`CHUNK_BYTES`] buffer and never held whole, the conversation is a
//! 6-turn ring, and the published answer is two fixed strings.

use crate::context::lock;
use crate::control::{self, Surface};
use crate::net::api::{parse_key_str, read_body_into, respond};
use crate::net::program::{drive, drive_stop};
use crate::{logi, logw};
use coach_core::hist::Role;
use coach_core::scan::ReplyScanner;
use coach_core::tool::{describe, validate, Action};
use coach_core::{req, salvage, History};
use esp_idf_sys as sys;
use safety_core::units::Micros;
use safety_core::FixedStr;
use std::sync::Mutex;

// ---------------------------------------------------------------------------
// Budgets. Every one of them bounds a wait that is NOT on the httpd worker.
// ---------------------------------------------------------------------------

/// Per-socket-operation timeout handed to `esp_http_client`.
const HTTP_TIMEOUT_MS: i32 = 15_000;

/// How long ONE turn may take, end to end, including the generation call.
///
/// A ceiling on the whole thing rather than on each piece: `timeout_ms` bounds
/// an individual read, and a server that answers one byte every 14 s satisfies
/// it forever. Nothing bad happens to the belt when this expires — the coach
/// task is not the belt — but a turn that never finishes would leave the
/// mailbox busy and every later message refused, which to the user is a coach
/// that stopped working with no explanation.
///
/// IT IS CHECKED AT EVERY SOCKET STEP, not only in the read loop, and that
/// correction matters because this file's value is the bounds it states.
/// `esp_http_client_open` (DNS + TCP + TLS), the write loop and
/// `fetch_headers` are each bounded only by [`HTTP_TIMEOUT_MS`], and a turn
/// makes TWO full round trips when a workout is generated — so a budget
/// enforced only on reads described 45 s and permitted roughly 2 x (15+15+15)
/// plus reads. `POST /api/chat` answering 429 for ~100 s where the header says
/// 45 s is exactly the kind of drift that makes the WDT matrix in
/// `tasks/mod.rs` — which cites this constant as the reason the coach task
/// needs no watchdog — stop being evidence.
const TURN_BUDGET: Micros = Micros::from_secs(45);

/// Ceiling on the reply we will listen to. Beyond it the socket is closed and
/// what was extracted so far is used.
///
/// Not a buffer size — nothing here holds this many bytes. It is a bound on
/// TIME and on attention: a server that streams forever cannot keep the coach
/// task busy forever.
const MAX_RESPONSE_BYTES: usize = 64 * 1024;

/// The streaming read chunk, on the coach task's stack.
const CHUNK_BYTES: usize = 512;

/// The coach task's stack.
///
/// Derived: `CHUNK_BYTES` + one `Program` (~900 B) materialised by
/// `parse_program` on the generation path + the `record_loaded` write frame +
/// mbedtls' own frames underneath `esp_http_client_read`, which are the largest
/// term and are reserved rather than hoped for. The big buffers — the request
/// body, the scanner, the history — are STATIC, deliberately: 6 KB of request
/// buffer on a stack that also carries an mbedtls session is how `net::session`
/// got an intermittent overflow, and an intermittent reboot drops the relay.
pub const STACK_BYTES: usize = 12_288;

/// Lowest priority in the system alongside the radio. It must never delay the
/// interval executor, the serial engine or the session recorder — a coach
/// sentence is a convenience and the belt is the point.
pub const PRIORITY: u8 = 3;

/// Poll interval when the mailbox is empty.
const IDLE_MS: u32 = 100;

// ---------------------------------------------------------------------------
// Configuration — NVS, and the pinning rule.
// ---------------------------------------------------------------------------

const NVS_KEY_API: &core::ffi::CStr = c"coach_key";
const NVS_KEY_URL: &core::ffi::CStr = c"coach_url";

const MAX_KEY: usize = 128;
const MAX_URL: usize = 192;

/// The ONLY host the API key is ever sent to. See the module header.
const PINNED_PREFIX: &str = "https://generativelanguage.googleapis.com/";

/// The default endpoint, used when NVS holds no override.
///
/// It is the vendor's published `generateContent` URL for the model
/// `python/program_engine.py` uses (`gemini-2.5-flash`), and it is a DEFAULT
/// rather than a constant in the code path: `POST /api/coach/url` overrides it,
/// which is what lets the QEMU scenarios point the device at a stub they
/// control without a live key ever existing.
const DEFAULT_URL: &str = concat!(
    "https://generativelanguage.googleapis.com/v1beta/models/",
    "gemini-2.5-flash:generateContent"
);

struct Config {
    key: FixedStr<MAX_KEY>,
    url: FixedStr<MAX_URL>,
}

static CONFIG: Mutex<Config> = Mutex::new(Config {
    key: FixedStr::new(),
    url: FixedStr::new(),
});

/// Whether the API key may be sent to `url`.
///
/// The whole exfiltration argument in one function: a configurable endpoint on
/// an unauthenticated LAN surface must not be able to collect the secret.
fn key_allowed(url: &str) -> bool {
    url.len() >= PINNED_PREFIX.len() && url.as_bytes().starts_with(PINNED_PREFIX.as_bytes())
}

/// Read the persisted configuration. Call once, from `main`, before the task.
pub fn load() {
    let mut h: sys::nvs_handle_t = 0;
    let mut cfg = lock(&CONFIG);
    cfg.url = FixedStr::from_str_truncating(DEFAULT_URL);
    if crate::net::tls::nvs_open_rw(&mut h) == sys::ESP_OK {
        let mut buf = [0u8; MAX_URL];
        if let Some(n) = crate::net::tls::nvs_read(h, NVS_KEY_URL, &mut buf) {
            if let Ok(s) = core::str::from_utf8(&buf[..n]) {
                if !s.is_empty() {
                    cfg.url = FixedStr::from_str_truncating(s);
                }
            }
        }
        let mut kbuf = [0u8; MAX_KEY];
        if let Some(n) = crate::net::tls::nvs_read(h, NVS_KEY_API, &mut kbuf) {
            if let Ok(s) = core::str::from_utf8(&kbuf[..n]) {
                cfg.key = FixedStr::from_str_truncating(s);
            }
        }
        // The buffers go out of scope here. Nothing is logged about either
        // value: not its length, not its prefix, not whether it "looks right".
        crate::net::tls::nvs_close(h);
    }
    // DELIBERATELY SAYS ONLY WHETHER, NEVER WHAT.
    logi!(
        "coach: {} key, endpoint {}",
        if cfg.key.is_empty() { "no" } else { "a" },
        if key_allowed(cfg.url.as_str()) {
            "pinned"
        } else {
            "overridden (no key will be sent)"
        }
    );
}

fn store(key: &core::ffi::CStr, value: &str) -> bool {
    let mut h: sys::nvs_handle_t = 0;
    if crate::net::tls::nvs_open_rw(&mut h) != sys::ESP_OK {
        return false;
    }
    let mut ok = crate::net::tls::nvs_write(h, key, value.as_bytes()) == sys::ESP_OK;
    if ok {
        ok = crate::net::tls::nvs_commit(h) == sys::ESP_OK;
    }
    crate::net::tls::nvs_close(h);
    ok
}

// ---------------------------------------------------------------------------
// The mailbox and the published reply. ONE turn in flight, by construction.
// ---------------------------------------------------------------------------

/// Bytes of coach answer published to clients.
///
/// Shorter than the scanner's text sink (which is sized for a generated
/// program): a coaching sentence is two lines, and this is what rides in a
/// `/ws` frame every time a turn completes.
pub const REPLY_BYTES: usize = 480;

/// Bytes of rendered `actions` array.
///
/// DERIVED, and derived somewhere it can be TESTED: `coach_core::render` sizes
/// it from `scan::MAX_CALLS` x the widest entry the scanner can produce, with a
/// compile-time assertion. It was a hand-picked 384 here — smaller than a
/// single worst-case entry — and the array saturated mid-token at the DECLARED
/// maximum of four tool calls, which made `GET /api/chat` and the `/ws` coach
/// frame invalid JSON on the happy path.
const ACTIONS_BYTES: usize = coach_core::render::ACTIONS_BYTES;

struct Mailbox {
    /// Handed out by `POST /api/chat`, monotonic, never reused.
    next_turn: u32,
    /// Submitted and not yet picked up. `None` when idle.
    queued: Option<FixedStr<{ req::MSG_BYTES }>>,
    queued_turn: u32,
    /// The turn the task is working on; 0 = none.
    in_flight: u32,
}

static MAILBOX: Mutex<Mailbox> = Mutex::new(Mailbox {
    next_turn: 1,
    queued: None,
    queued_turn: 0,
    in_flight: 0,
});

struct Reply {
    turn: u32,
    text: FixedStr<REPLY_BYTES>,
    /// The `actions` array body, already rendered, WITHOUT its brackets.
    actions: FixedStr<ACTIONS_BYTES>,
}

static REPLY: Mutex<Reply> = Mutex::new(Reply {
    turn: 0,
    text: FixedStr::new(),
    actions: FixedStr::new(),
});

/// The conversation and the two big buffers. STATIC, not stack — see
/// `STACK_BYTES`. Only the coach task touches them, and it is the only task
/// that ever will, so one mutex over the set is honest rather than coarse.
struct Work {
    history: History,
    scanner: ReplyScanner,
    req: [u8; req::REQ_BYTES],
}

static WORK: Mutex<Work> = Mutex::new(Work {
    history: History::new(),
    scanner: ReplyScanner::new(),
    req: [0u8; req::REQ_BYTES],
});

/// Whether a coach turn is queued or running.
fn busy() -> bool {
    let m = lock(&MAILBOX);
    m.queued.is_some() || m.in_flight != 0
}

// ---------------------------------------------------------------------------
// The task.
// ---------------------------------------------------------------------------

pub fn run(_ctx: &'static crate::context::FirmwareContext) -> ! {
    // NO `wdt::subscribe_current_task()`. See the module header: a watchdog
    // whose remedy is a reboot must not supervise a task that blocks on a
    // network call, because the reboot drops the relay mid-run.
    logi!("coach task started (not WDT-supervised — see net/coach.rs)");
    loop {
        let taken = {
            let mut m = lock(&MAILBOX);
            match m.queued.take() {
                Some(msg) => {
                    m.in_flight = m.queued_turn;
                    Some((m.queued_turn, msg))
                }
                None => None,
            }
        };
        match taken {
            Some((turn, msg)) => {
                turn_impl(turn, msg.as_str());
                lock(&MAILBOX).in_flight = 0;
                // Tell every `/ws` client the answer (and any belt change it
                // caused) is available. Asking is all a non-httpd task may do.
                crate::net::ws::request_push();
            }
            None => crate::tasks::delay_ms(IDLE_MS),
        }
    }
}

/// One complete turn: ask, validate, apply, publish.
fn turn_impl(turn: u32, msg: &str) {
    let started = crate::CTX.clock.now();
    let (url, key_hdr) = {
        let cfg = lock(&CONFIG);
        let send_key = key_allowed(cfg.url.as_str()) && !cfg.key.is_empty();
        (cfg.url, if send_key { Some(cfg.key) } else { None })
    };
    if key_hdr.is_none() && key_allowed(url.as_str()) {
        publish(turn, "The coach is not set up on this machine yet.", "");
        return;
    }

    let mut text: FixedStr<REPLY_BYTES> = FixedStr::new();
    let mut actions = coach_core::render::Actions::new();
    // A generation is DEFERRED, not executed in the loop, because it is a
    // second model call and the loop is holding the work buffers it needs. The
    // call's own arguments ride along so the transcript still shows what the
    // model asked for — pushing an entry in the loop AND another after the
    // generation gave the user two `generate_workout` lines for one request.
    let mut pending_gen: Option<(
        FixedStr<{ coach_core::tool::DESC_BYTES }>,
        FixedStr<{ coach_core::scan::ARGS_BYTES }>,
    )> = None;

    {
        let mut w = lock(&WORK);
        let mut state: FixedStr<{ req::STATE_BYTES }> = FixedStr::new();
        render_state_line(&mut state);
        let built = {
            let Work { history, req: buf, .. } = &mut *w;
            req::build_chat(&mut buf[..], history, state.as_str(), msg)
        };
        let Some(n) = built else {
            // Cannot happen without a cap changing (the worst case is a
            // compile-time assertion), and is still handled: a truncated body
            // would cost a round trip to be told it was invalid.
            publish(turn, "I could not put that message together.", "");
            return;
        };
        w.scanner.reset();
        let outcome = {
            let Work { scanner, req: buf, .. } = &mut *w;
            post(url.as_str(), key_hdr.as_ref(), &buf[..n], scanner, started)
        };
        match outcome {
            Err(e) => {
                // NAMES THE FAILURE, NOT THE ENDPOINT AND NEVER THE KEY.
                logw!("coach: turn {} failed (err {})", turn, e);
                publish(turn, "I could not reach the coach just now.", "");
                return;
            }
            Ok(status) if !(200..300).contains(&status) => {
                logw!("coach: turn {} answered {}", turn, status);
                publish(turn, "The coach service turned that request down.", "");
                return;
            }
            Ok(_) => {}
        }
        if w.scanner.malformed && w.scanner.text.is_empty() && w.scanner.n_calls == 0 {
            publish(turn, "The coach sent something I could not read.", "");
            return;
        }
        for b in w.scanner.text.as_bytes().iter().take(REPLY_BYTES) {
            text.push_byte(*b);
        }
        // Validate and apply, in the order the model emitted them.
        for i in 0..w.scanner.n_calls {
            let call = w.scanner.calls[i];
            let mut result: FixedStr<{ coach_core::render::RESULT_BYTES }> = FixedStr::new();
            match validate(&call) {
                Ok(action) => {
                    // THE SAME CLAMPED VALUE IS DESCRIBED AND APPLIED. One
                    // rendering, so what the user is told and what the belt was
                    // asked for cannot diverge — WHICH REQUIRES EVERY ACTION TO
                    // HAVE A FAILURE BRANCH. `describe` runs first and `apply`
                    // overrides it only on failure, so an action that cannot
                    // report failure reports success unconditionally. That is
                    // exactly what `stop_treadmill` did: it said "treadmill
                    // stopped" for a manually commanded belt it never touched.
                    describe(&action, &mut result);
                    if let Action::GenerateWorkout(d) = action {
                        pending_gen = Some((d, call.args));
                        continue; // its entry is pushed with its OUTCOME, below
                    }
                    if let Some(why) = apply(&action) {
                        result = FixedStr::from_str_truncating(why);
                    }
                }
                Err(r) => result = FixedStr::from_str_truncating(r.message()),
            }
            // `push_call` — not `push` — because the decision about whether the
            // argument object may be echoed verbatim is `ToolCall::is_intact()`,
            // and it belongs next to the flags that set it rather than at each
            // call site.
            actions.push_call(&call, result.as_str());
        }
        if w.scanner.too_many_calls {
            actions.push(
                "ignored",
                None,
                "the coach asked for more changes than one turn may make",
            );
        }
    }

    // The generation is a SECOND call and happens outside the borrow above so
    // the work buffers can be reused for it.
    if let Some((desc, args)) = pending_gen {
        let outcome = generate(desc.as_str(), &url, key_hdr.as_ref(), started);
        // `Some(&args)`: this call reached `validate` and was ACCEPTED, and
        // `validate` refuses anything that is not `is_intact()`, so the object
        // is balanced by the same predicate `push_call` uses.
        actions.push("generate_workout", Some(&args), outcome);
    }

    // COMMITTED ONLY NOW. Nothing was written to the ring while the turn could
    // still fail — see `coach_core::hist` for why a ring cannot be rolled back.
    {
        let mut w = lock(&WORK);
        w.history.push(Role::User, msg);
        if !text.is_empty() {
            w.history.push(Role::Model, text.as_str());
        }
    }
    publish(turn, text.as_str(), actions.as_str());
}

/// Apply one already-clamped action. Returns an override message when the
/// device refused it.
///
/// EVERY BRANCH GOES THROUGH THE EXISTING PATH. `control::command` is the one
/// path to the belt and `ProgramState` is the one interval executor; the coach
/// is just another owner. There is no code here that decides whether a motion
/// is safe.
fn apply(action: &Action) -> Option<&'static str> {
    let now = crate::CTX.clock.now();
    match action {
        Action::SetSpeed(s) => {
            let mut g = lock(&crate::CTX.guarded);
            let inc = g.controller.incline_half_percent();
            let intent = if s.get() > 0 {
                control::EntryIntent::ExplicitRecovery
            } else {
                control::EntryIntent::Ordinary
            };
            match control::command(&mut g, Surface::Http, intent, *s, inc, now) {
                Ok(()) => None,
                Err(control::Reject::NotOwner) => {
                    Some("a workout is running, so I left the belt alone")
                }
                Err(_) => Some("the treadmill refused that change"),
            }
        }
        Action::SetIncline(i) => {
            let mut g = lock(&crate::CTX.guarded);
            let sp = g.controller.speed_tenths();
            match control::command(
                &mut g,
                Surface::Http,
                control::EntryIntent::Ordinary,
                sp,
                *i,
                now,
            ) {
                Ok(()) => None,
                Err(control::Reject::NotOwner) => {
                    Some("a workout is running, so I left the belt alone")
                }
                Err(_) => Some("the treadmill refused that change"),
            }
        }
        Action::StartWorkout => {
            let mut p = lock(&crate::CTX.program);
            if p.program().is_none() {
                return Some("there is no workout loaded to start");
            }
            let plan = p.start(now, 0, 0);
            drive(plan, false);
            None
        }
        Action::StopTreadmill => {
            // THROUGH THE SAME STOP THE HTTP ENDPOINT USES, and the answer
            // comes from what HAPPENED rather than from what was asked for.
            //
            // This was the one action with no failure branch, which — given
            // that `describe()` runs before `apply()` and only a failure
            // overrides it — meant the transcript said "treadmill stopped"
            // unconditionally. It said that for a belt commanded manually,
            // which `ProgramState::stop()` alone never touched: with no
            // program running its plan is EMPTY, and an empty plan releases
            // the executor's lease and nothing else. The user asked the coach
            // to stop the treadmill and was told it had stopped while it ran.
            let mut p = lock(&crate::CTX.program);
            let plan = p.stop();
            if drive_stop(plan) {
                None
            } else {
                Some("I could not stop the belt — use the stop button")
            }
        }
        Action::PauseProgram | Action::ResumeProgram => {
            let mut p = lock(&crate::CTX.program);
            if !p.running() {
                return Some("no workout is running");
            }
            let plan = p.toggle_pause(now);
            drive(plan, false);
            None
        }
        Action::SkipInterval => {
            let mut p = lock(&crate::CTX.program);
            if !p.running() {
                return Some("no workout is running");
            }
            let plan = p.skip(now);
            let ended = !p.running();
            drive(plan, ended);
            None
        }
        Action::ExtendInterval(s) => {
            let mut p = lock(&crate::CTX.program);
            if p.extend_current(*s as i64) {
                None
            } else {
                Some("no workout is running")
            }
        }
        // Handled by the caller: it is a second model call, not a local effect.
        Action::GenerateWorkout(_) => None,
    }
}

/// The generation call, and the one place a coach-built workout becomes real.
///
/// It lands through `program_core::json::parse_program` and `ProgramState::load`
/// — the SAME two calls `POST /api/program/load` makes, with the same clamps
/// (`Interval::new`) and the same history write (`records::record_loaded`).
/// Loaded, NOT started: the Pi's `generate_workout` does not start either, and
/// a belt that starts because a sentence was ambiguous is not a thing this
/// device will do.
fn generate(
    description: &str,
    url: &FixedStr<MAX_URL>,
    key: Option<&FixedStr<MAX_KEY>>,
    started: Micros,
) -> &'static str {
    let mut w = lock(&WORK);
    let built = {
        let Work { req: buf, .. } = &mut *w;
        req::build_program(&mut buf[..], description)
    };
    let Some(n) = built else {
        return "I could not put that workout request together";
    };
    w.scanner.reset();
    let outcome = {
        let Work { scanner, req: buf, .. } = &mut *w;
        post(url.as_str(), key, &buf[..n], scanner, started)
    };
    match outcome {
        Err(_) => return "I could not reach the coach to build that workout",
        Ok(s) if !(200..300).contains(&s) => {
            return "the coach service turned the workout request down"
        }
        Ok(_) => {}
    }

    // Truncation repair — `program_engine.generate_program`'s brace salvage.
    // Tried ONLY after the honest parse has failed, so a well-formed reply is
    // never "repaired".
    let mut program = program_core::json::parse_program(w.scanner.text.as_bytes());
    if program.is_err() && salvage::repair_program(&mut w.scanner.text) {
        program = program_core::json::parse_program(w.scanner.text.as_bytes());
    }
    let Ok(program) = program else {
        return "the coach did not send a workout I could use";
    };
    drop(w);

    // History write BEFORE the program lock, exactly as `net::program` does it,
    // so a 4 KB sector erase never sits inside the section the interval
    // executor contends for.
    let history_id = crate::net::records::record_loaded(&program, description);
    let mut p = lock(&crate::CTX.program);
    match history_id.as_ref() {
        Some(id) => crate::net::session::set_current(id),
        None => crate::net::session::set_current(&safety_core::FixedStr::new()),
    }
    // Loading over a RUNNING program stops it first, belt and all — the same
    // rule `POST /api/program/load` follows, and for the same reason: a moving
    // belt under a program that no longer exists is not a state this device
    // will hold.
    let stop = p.stop();
    let stopping = !stop.is_empty();
    p.load(program);
    drop(p);
    if stopping {
        drive(stop, true);
    }
    "workout ready — say start when you are"
}

/// The device-state sentence handed to the model. NO WALL-CLOCK TIME: this
/// device has no RTC and no SNTP, so the only time it can honestly offer is the
/// running session's own elapsed tick.
fn render_state_line(out: &mut FixedStr<{ req::STATE_BYTES }>) {
    use core::fmt::Write;
    let (speed, incline) = {
        let g = lock(&crate::CTX.guarded);
        (
            g.controller.speed_tenths().get(),
            g.controller.incline_half_percent().get(),
        )
    };
    let p = lock(&crate::CTX.program);
    let _ = write!(
        out,
        "Right now: belt {}.{} mph, incline {}.{}%.",
        speed / 10,
        speed % 10,
        incline / 2,
        if incline % 2 == 0 { 0 } else { 5 }
    );
    if p.running() {
        let _ = write!(
            out,
            " A workout is {}, on interval {} of {}, {}s elapsed in this session.",
            if p.paused() { "paused" } else { "running" },
            p.current_interval() + 1,
            p.program().map(|x| x.len()).unwrap_or(0),
            p.total_elapsed()
        );
    } else if p.program().is_some() {
        let _ = write!(out, " A workout is loaded but not started.");
    } else {
        let _ = write!(out, " No workout is loaded.");
    }
}

fn publish(turn: u32, text: &str, actions: &str) {
    let mut r = lock(&REPLY);
    r.turn = turn;
    r.text.clear();
    // THE SAME FILTER `coach_core::render` APPLIES TO A NAME AND A RESULT. It
    // was three hand-written copies of this loop with the tool NAME left out
    // entirely; one shared function cannot have that divergence.
    for b in text.as_bytes() {
        r.text.push_byte(coach_core::tool::sanitise(*b));
    }
    r.actions = FixedStr::from_str_truncating(actions);
}

// ---------------------------------------------------------------------------
// The HTTP client. The ONLY FFI in this file.
// ---------------------------------------------------------------------------

/// POST `body`, stream the response through `scanner`, return the status code.
///
/// Streaming rather than `esp_http_client_perform`: `perform` hands the whole
/// body to a callback (or buffers it), and the whole body is a remote server's
/// choice of size. `open`/`write`/`fetch_headers`/`read` lets the reply pass
/// through one [`CHUNK_BYTES`] buffer that is reused, so resident memory is a
/// constant.
fn post(
    url: &str,
    key: Option<&FixedStr<MAX_KEY>>,
    body: &[u8],
    scanner: &mut ReplyScanner,
    started: Micros,
) -> Result<i32, i32> {
    // Both must be NUL-terminated for the C API, and both are bounded.
    let mut url_c = [0u8; MAX_URL + 1];
    if url.len() >= url_c.len() {
        return Err(sys::ESP_ERR_INVALID_SIZE);
    }
    url_c[..url.len()].copy_from_slice(url.as_bytes());
    let mut key_c = [0u8; MAX_KEY + 1];
    if let Some(k) = key {
        if k.len() >= key_c.len() {
            return Err(sys::ESP_ERR_INVALID_SIZE);
        }
        key_c[..k.len()].copy_from_slice(k.as_bytes());
    }

    let mut cfg: sys::esp_http_client_config_t = zeroed();
    cfg.url = url_c.as_ptr() as *const core::ffi::c_char;
    cfg.method = sys::esp_http_client_method_t_HTTP_METHOD_POST;
    cfg.timeout_ms = HTTP_TIMEOUT_MS;
    cfg.buffer_size = CHUNK_BYTES as i32;
    cfg.buffer_size_tx = 1024;
    // A redirect must NOT be followed: the key is scoped to the pinned host by
    // `key_allowed`, and a 302 is the pinned host asking us to send it
    // somewhere else. Refuse and report the status.
    cfg.disable_auto_redirect = true;
    // The CA bundle. See the module header for the measured flash cost.
    cfg.crt_bundle_attach = Some(sys::esp_crt_bundle_attach);

    // SAFETY: `cfg` is a live borrow for the call; IDF copies the strings it
    // keeps out of it (esp_http_client_init duplicates url/host/path), and
    // `url_c` outlives the call regardless. A null return is the only failure
    // signal the C API offers.
    let client = unsafe { sys::esp_http_client_init(&cfg) };
    if client.is_null() {
        return Err(sys::ESP_FAIL);
    }
    let rc = post_inner(client, key.map(|_| &key_c), body, scanner, started);
    // SAFETY: `client` is the live handle from `init`, used for the last time
    // here. `cleanup` closes the connection and frees IDF's own state.
    unsafe {
        sys::esp_http_client_cleanup(client);
    }
    rc
}

/// The body of [`post`], split out so `cleanup` runs on every exit path.
fn post_inner(
    client: sys::esp_http_client_handle_t,
    key_c: Option<&[u8; MAX_KEY + 1]>,
    body: &[u8],
    scanner: &mut ReplyScanner,
    started: Micros,
) -> Result<i32, i32> {
    // SAFETY: `client` is live; both arguments are NUL-terminated byte strings
    // that outlive the call, and IDF copies what it retains.
    let mut rc = unsafe {
        sys::esp_http_client_set_header(
            client,
            c"Content-Type".as_ptr(),
            c"application/json".as_ptr(),
        )
    };
    // CHECKED BEFORE THE NEXT CALL, not after both. Assigning over `rc` and
    // testing once means a failed Content-Type is ERASED by a successful
    // auth header — the request would then go out as the wrong media type and
    // come back 400, with nothing on the device naming the cause.
    if rc != sys::ESP_OK {
        return Err(rc);
    }
    if let Some(k) = key_c {
        // THE KEY GOES IN A HEADER, NEVER IN THE URL. See the module header.
        // SAFETY: as above — live handle, NUL-terminated buffer that outlives
        // the call.
        rc = unsafe {
            sys::esp_http_client_set_header(
                client,
                c"x-goog-api-key".as_ptr(),
                k.as_ptr() as *const core::ffi::c_char,
            )
        };
        if rc != sys::ESP_OK {
            return Err(rc);
        }
    }

    // THE WHOLE-TURN CEILING, BEFORE EVERY BLOCKING STEP. `open` is DNS + TCP
    // + the TLS handshake and is bounded only by `timeout_ms`; so are the
    // write loop and `fetch_headers`; and `generate` makes a second full set
    // of them with the ORIGINAL `started`. Checking only the read loop
    // described a 45 s turn and permitted about twice that.
    if over_budget(started) {
        return Err(sys::ESP_ERR_TIMEOUT);
    }
    // SAFETY: `client` is live; the length is the body we are about to write.
    let rc = unsafe { sys::esp_http_client_open(client, body.len() as i32) };
    if rc != sys::ESP_OK {
        return Err(rc);
    }

    let mut sent = 0usize;
    while sent < body.len() {
        if over_budget(started) {
            return Err(sys::ESP_ERR_TIMEOUT);
        }
        // SAFETY: `client` is live and open; the pointer/length name a
        // sub-slice of `body`, which outlives the call and is only read.
        let n = unsafe {
            sys::esp_http_client_write(
                client,
                body.as_ptr().add(sent) as *const core::ffi::c_char,
                (body.len() - sent) as core::ffi::c_int,
            )
        };
        if n <= 0 {
            return Err(sys::ESP_FAIL);
        }
        sent += n as usize;
    }

    if over_budget(started) {
        return Err(sys::ESP_ERR_TIMEOUT);
    }
    // SAFETY: `client` is live and the request has been written.
    let content = unsafe { sys::esp_http_client_fetch_headers(client) };
    if content < 0 {
        return Err(sys::ESP_FAIL);
    }
    // SAFETY: reads an integer out of the live client.
    let status = unsafe { sys::esp_http_client_get_status_code(client) };

    let mut chunk = [0u8; CHUNK_BYTES];
    let mut total = 0usize;
    loop {
        if over_budget(started) {
            // The whole-turn ceiling. Whatever arrived is what we have; the
            // socket is closed by `cleanup`.
            break;
        }
        // SAFETY: `client` is live; `chunk` is a live exclusive borrow and IDF
        // writes at most the length passed.
        let n = unsafe {
            sys::esp_http_client_read(
                client,
                chunk.as_mut_ptr() as *mut core::ffi::c_char,
                CHUNK_BYTES as i32,
            )
        };
        if n <= 0 {
            break;
        }
        scanner.push_all(&chunk[..n as usize]);
        total += n as usize;
        if total >= MAX_RESPONSE_BYTES {
            break;
        }
    }
    scanner.finish_stream();
    Ok(status)
}

/// Whether the turn that began at `started` has used its whole budget.
///
/// ONE predicate, so every step of every round trip in a turn is bounded by
/// the same number the module header quotes.
fn over_budget(started: Micros) -> bool {
    crate::CTX.clock.now() - started > TURN_BUDGET
}

fn zeroed<T>() -> T {
    // SAFETY: `esp_http_client_config_t` is a bindgen POD of integers,
    // pointers and function pointers; all-zero is a valid initial value and is
    // the C API's own convention for "default". Every field the client needs is
    // set explicitly by the caller.
    unsafe { core::mem::zeroed() }
}

// ---------------------------------------------------------------------------
// Endpoints. Every handler is THIN: the counting rule in
// check_unsafe_budget.py attributes the whole body of an `unsafe extern "C"`
// fn to the unsafe budget, so each one reads the raw field a callback must and
// delegates to safe code.
// ---------------------------------------------------------------------------

/// Largest `POST /api/chat` body. The message itself is capped at
/// `req::MSG_BYTES`; the rest is JSON envelope and the fields the app sends
/// that this device ignores (`smartass`).
const CHAT_BODY_BYTES: usize = 512;

/// SAFETY: `req` is a live IDF request pointer valid for the call; nothing
/// derived from it is retained.
unsafe extern "C" fn chat_post_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    chat_post_impl(req)
}

/// SAFETY: as above.
unsafe extern "C" fn chat_get_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    chat_get_impl(req)
}

/// SAFETY: as above.
unsafe extern "C" fn coach_get_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    coach_get_impl(req)
}

/// SAFETY: `req` is live for the call; `user_ctx` is a scalar this module set
/// at registration. Nothing derived from `req` is retained.
unsafe extern "C" fn config_post_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    config_post_impl(req, (*req).user_ctx as usize)
}

fn chat_post_impl(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut body = [0u8; CHAT_BODY_BYTES];
    let Some(n) = read_body_into(req, &mut body) else {
        return sys::ESP_OK; // already answered
    };
    let mut msg = [0u8; req::MSG_BYTES];
    let Some(len) = parse_key_str(&body[..n], b"message", &mut msg) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"missing message"}"#,
        );
    };
    let Ok(text) = core::str::from_utf8(&msg[..len]) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"message is not valid utf-8"}"#,
        );
    };

    // ONE guard. `lock(&CONFIG).key... && key_allowed(lock(&CONFIG).url...)`
    // takes the same non-reentrant mutex twice in one expression, and the first
    // temporary is still alive when the second is taken — a self-deadlock on
    // the httpd worker, which is every request on the device including Stop.
    let unconfigured = {
        let cfg = lock(&CONFIG);
        cfg.key.is_empty() && key_allowed(cfg.url.as_str())
    };
    if unconfigured {
        return respond(
            req,
            c"200 OK",
            br#"{"text":"The coach is not set up on this machine yet.","actions":[],"pending":false,"turn":0}"#,
        );
    }

    let turn = {
        let mut m = lock(&MAILBOX);
        if m.queued.is_some() || m.in_flight != 0 {
            // ONE turn in flight, by construction. Queueing would make memory a
            // function of how fast somebody types, and a queue of stale
            // questions answered minutes later is worse than a refusal.
            return respond(
                req,
                c"429 Too Many Requests",
                br#"{"ok":false,"text":"I am still working on the last one.","actions":[]}"#,
            );
        }
        let t = m.next_turn;
        m.next_turn = m.next_turn.wrapping_add(1).max(1);
        m.queued = Some(FixedStr::from_str_truncating(text));
        m.queued_turn = t;
        t
    };

    // AND THAT IS THE WHOLE HANDLER. The worker is free from here; the round
    // trip runs on the coach task and the answer arrives on `/ws` (and at
    // `GET /api/chat`).
    let mut out = [0u8; 256];
    let n = render_pending(&mut out, turn);
    respond(req, c"202 Accepted", &out[..n])
}

/// What an UNCHANGED client is told while the answer is still being fetched.
///
/// NOT AN EMPTY STRING, and the reason is measured rather than aesthetic:
/// `ChatSheet.kt:56-57` does `onToast(res.text.ifBlank { "No response" })`, and
/// Retrofit treats 202 as success and decodes the body — so an empty `text`
/// made every single coach message on this device toast the words "No
/// response". A user reads that as a broken feature, not a slow one. Until
/// bead `precor-9_3x-lsx` teaches the app the `coach` frame, a non-blank
/// placeholder degrades the unchanged app to a WAIT instead of to an ERROR,
/// and it costs nothing on the Pi (which answers 200 with the real text and
/// never sends this body).
const PENDING_TEXT: &str = "Thinking...";

/// The pending answer, in the shape the app already decodes.
///
/// `text` and `actions` are what `ChatResponse` requires (both have kotlinx
/// defaults, so their presence is belt and braces); `pending` and `turn` are
/// additions an existing client ignores under `ignoreUnknownKeys`.
fn render_pending(out: &mut [u8; 256], turn: u32) -> usize {
    let mut s: FixedStr<256> = FixedStr::new();
    s.push_str(r#"{"text":""#);
    s.push_str(PENDING_TEXT);
    s.push_str(r#"","actions":[],"pending":true,"turn":"#);
    s.push_i64(turn as i64);
    s.push_str("}");
    let b = s.as_bytes();
    out[..b.len()].copy_from_slice(b);
    b.len()
}

/// The rendered `GET /api/chat` body (and the `/ws` coach frame).
///
/// SIZED BY ARITHMETIC AND ASSERTED, because `render_reply` writes into a
/// `FixedStr` of this size and `FixedStr::push_str` saturates SILENTLY — so an
/// undersized buffer here does not overflow, it emits a body that stops
/// mid-token. `req::REQ_BYTES` has had exactly this assertion since the
/// request builder was written; this one was missing, and the worst case had
/// 23 bytes of margin nobody had counted.
pub const REPLY_BODY_BYTES: usize = REPLY_BYTES + ACTIONS_BYTES + 96;

/// The punctuation `render_reply` writes around the two variable strings, with
/// the widest `lead` (`"type":"coach",` = 15) and a full-width `turn`.
const REPLY_FRAME_BYTES: usize = 1  // {
    + 15                            // lead
    + 8                             // "text":"
    + 13                            // ","actions":[
    + 9                             // ],"turn":
    + 10                            // u32 as decimal
    + 11                            // ,"pending":
    + 5                             // false
    + 1; // }

const _: () = assert!(
    REPLY_BODY_BYTES >= REPLY_BYTES + ACTIONS_BYTES + REPLY_FRAME_BYTES,
    "REPLY_BODY_BYTES no longer holds a full-width reply plus the frame around \
     it — render_reply would silently truncate a /ws frame into malformed JSON"
);

/// The published answer, in the shape `POST /api/chat` on the Pi returns.
///
/// `lead` is inserted right after the opening brace, which is how the `/ws`
/// frame gets its `"type":"coach"` without a second renderer. One rendering for
/// both surfaces, so the socket and the endpoint cannot drift apart.
///
/// `busy()` is read BEFORE the REPLY lock is taken. Nesting REPLY inside
/// MAILBOX (or the reverse) would be a lock-order pair with no reason to exist,
/// and `std::sync::Mutex` is not reentrant — the cheapest fix is to not do it.
fn render_reply(out: &mut FixedStr<{ REPLY_BODY_BYTES }>, lead: &str) {
    let pending = busy();
    let r = lock(&REPLY);
    out.clear();
    out.push_str("{");
    out.push_str(lead);
    out.push_str(r#""text":""#);
    out.push_str(r.text.as_str());
    out.push_str(r#"","actions":["#);
    out.push_str(r.actions.as_str());
    out.push_str(r#"],"turn":"#);
    out.push_i64(r.turn as i64);
    out.push_str(r#","pending":"#);
    out.push_str(if pending { "true" } else { "false" });
    out.push_str("}");
}

fn chat_get_impl(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut body: FixedStr<{ REPLY_BODY_BYTES }> = FixedStr::new();
    render_reply(&mut body, "");
    respond(req, c"200 OK", body.as_bytes())
}

/// The `/ws` frame buffer. One `FixedStr`, sized like the endpoint body plus
/// the `type` every frame on that socket carries.
pub const WS_FRAME_BUF: usize = REPLY_BODY_BYTES;

/// Render the coach frame, or 0 if nothing has been answered yet.
pub fn render_ws(out: &mut FixedStr<{ WS_FRAME_BUF }>) -> usize {
    if published_turn() == 0 {
        return 0;
    }
    render_reply(out, r#""type":"coach","#);
    out.len()
}

/// The turn number of the published reply. `net::session` pushes a coach frame
/// only when this CHANGES, so a completed answer is delivered once rather than
/// every second forever.
pub fn published_turn() -> u32 {
    lock(&REPLY).turn
}

fn coach_get_impl(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let cfg = lock(&CONFIG);
    let mut s: FixedStr<192> = FixedStr::new();
    // WHETHER, NEVER WHAT. No prefix, no length, no fingerprint of the key.
    s.push_str(r#"{"ok":true,"configured":"#);
    s.push_str(if cfg.key.is_empty() { "false" } else { "true" });
    s.push_str(r#","endpoint_pinned":"#);
    s.push_str(if key_allowed(cfg.url.as_str()) {
        "true"
    } else {
        "false"
    });
    s.push_str(r#","busy":"#);
    drop(cfg);
    s.push_str(if busy() { "true" } else { "false" });
    s.push_str(r#","turn":"#);
    s.push_i64(published_turn() as i64);
    s.push_str("}");
    respond(req, c"200 OK", s.as_bytes())
}

const V_KEY: usize = 0;
const V_URL: usize = 1;

fn config_post_impl(req: *mut sys::httpd_req_t, verb: usize) -> sys::esp_err_t {
    let mut body = [0u8; CHAT_BODY_BYTES];
    let Some(n) = read_body_into(req, &mut body) else {
        return sys::ESP_OK;
    };
    let mut val = [0u8; MAX_URL];
    let field: &[u8] = if verb == V_KEY { b"key" } else { b"url" };
    let Some(len) = parse_key_str(&body[..n], field, &mut val) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"missing value"}"#,
        );
    };
    let Ok(text) = core::str::from_utf8(&val[..len]) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"value is not valid utf-8"}"#,
        );
    };

    if verb == V_KEY {
        if text.len() >= MAX_KEY {
            return respond(
                req,
                c"400 Bad Request",
                br#"{"ok":false,"error":"key too long"}"#,
            );
        }
        let ok = store(NVS_KEY_API, text);
        if ok {
            lock(&CONFIG).key = FixedStr::from_str_truncating(text);
        }
        // NOTHING ABOUT THE VALUE IS RETURNED OR LOGGED — not an echo, not a
        // length, not a masked prefix.
        return respond(
            req,
            if ok { c"200 OK" } else { c"500 Internal Server Error" },
            if ok {
                br#"{"ok":true,"configured":true}"#
            } else {
                br#"{"ok":false,"error":"could not store the key"}"#
            },
        );
    }

    if text.len() >= MAX_URL {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"url too long"}"#,
        );
    }
    let ok = store(NVS_KEY_URL, text);
    if ok {
        lock(&CONFIG).url = FixedStr::from_str_truncating(text);
    }
    let pinned = key_allowed(text);
    // SAYS WHETHER THE KEY WILL BE SENT, because that is the consequence the
    // operator needs to see and it says nothing about the key itself.
    logi!(
        "coach: endpoint set, key will {}be sent",
        if pinned { "" } else { "NOT " }
    );
    respond(
        req,
        if ok { c"200 OK" } else { c"500 Internal Server Error" },
        if !ok {
            br#"{"ok":false,"error":"could not store the endpoint"}"#
        } else if pinned {
            br#"{"ok":true,"endpoint_pinned":true}"#
        } else {
            br#"{"ok":true,"endpoint_pinned":false}"#
        },
    )
}

/// Register the coach routes on an already-started server.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly and
    // introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 5] = [
        (c"/api/chat", sys::http_method_HTTP_POST, chat_post_handler, usize::MAX),
        (c"/api/chat", sys::http_method_HTTP_GET, chat_get_handler, usize::MAX),
        (c"/api/coach", sys::http_method_HTTP_GET, coach_get_handler, usize::MAX),
        (c"/api/coach/key", sys::http_method_HTTP_POST, config_post_handler, V_KEY),
        (c"/api/coach/url", sys::http_method_HTTP_POST, config_post_handler, V_URL),
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
        // SAFETY: `handle` is a live server; `uri` is read for the call and IDF
        // copies what it retains. The handler is a `'static` fn.
        let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
        if err != sys::ESP_OK {
            return Err(err);
        }
    }
    Ok(())
}
