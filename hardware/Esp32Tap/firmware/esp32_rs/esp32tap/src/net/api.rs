//! Slice 2 — the control path.
//!
//! `/api/status`, `/api/speed`, `/api/incline`. The two that move the belt do
//! it through `crate::control::command` — the SAME function the interval
//! executor calls, so there is one lease, one set of clamps, one auto-emulate
//! policy and one `apply_outputs()` for the whole firmware. An HTTP request is
//! just another owner.
//!
//! The lease bookkeeping that used to live here moved into `control.rs` with
//! a bug fixed on the way: this handler minted a NEW connection generation per
//! request, and `SafetyController::connect` emergency-stops when a fresh
//! generation supersedes a lease-holding one. The second POST to `/api/speed`
//! would therefore have dropped the relay and re-entered emulate. One scenario
//! issuing one request never saw it; `test_program.py` now asserts it directly.
//!
//! ADMISSION BEFORE PARSING. Every body-bearing endpoint calls
//! `reqbudget::admit()` first. If the declared length will not fit a slot it is
//! refused 413 before a byte is read; if no slot is free it is refused 503. A
//! handler is therefore never part-way through a body and out of memory, which
//! is the failure mode that could reboot the C++ tier — and a reboot drops the
//! relay and interrupts a run.
//!
//! WHY THE HANDLERS ARE SMALL AND DUMB. They parse, clamp-by-delegation, and
//! report. They never decide whether a motion is safe: `command_motion` does,
//! because it is the code the differential compares against the C++ core and
//! the Python model. A handler that "helpfully" pre-validated would be a second
//! opinion about safety, which is exactly one opinion too many.

use crate::context::lock;
use crate::control::{self, EntryIntent, Surface};
use esp_idf_sys as sys;
use safety_core::safety::constants::CONSOLE_FRESH_US;
use safety_core::units::{InclineHalfPct, Micros, SpeedTenths};

/// Largest body we will read on a command endpoint. Well under a budget slot;
/// `{"value":12.5}` is 15 bytes.
pub(crate) const MAX_CMD_BODY: usize = 128;

// ---------------------------------------------------------------------------
// The request-duration bound.
//
// `recv_wait_timeout` (net::http) is a PER-RECV budget and nothing more. Every
// body reader in this firmware loops until `got == declared`, so a peer that
// answers each recv within a second — one byte every 0.5 s is plenty — never
// trips it and holds the SINGLE IDF worker for as long as it likes. MEASURED
// before this existed: one TLS connection posting `Content-Length: 900` a space
// at a time held the whole surface for 60 s, during which
// `POST /api/program/stop` could not even complete its TLS handshake, WITH THE
// BELT MOVING. Same belt-availability class as the handshake budget in
// net::http, one phase later.
//
// So the bound is on the WHOLE body read, stamped at handler entry, exactly the
// way `tls_handshake_timeout_ms` bounds the phase before it.
// ---------------------------------------------------------------------------

/// How long a handler may spend reading a request body, end to end.
///
/// NOT a limit on a healthy client: a full 2048-byte slot is one or two TLS
/// records on a LAN, delivered in milliseconds. What it selects is how long a
/// peer that has stopped making progress may keep the one worker — and the
/// answer has to be short, because the Stop button is behind that worker.
/// 2.5 s is one `recv_wait_timeout` (1 s) of legitimate silence plus slack,
/// and it is the same order as the 500 ms handshake budget it continues.
///
/// THE WORST CASE IS THIS PLUS ONE RECV, and that is stated rather than
/// rounded away: the deadline is tested BEFORE each blocking `httpd_req_recv`,
/// so a peer that goes silent immediately after the last check still costs a
/// full `recv_wait_timeout` on top — ~3.5 s in total, per attempt, and
/// repeatable by anyone on the LAN. Checking after the recv instead would not
/// help (the time is already spent). What bounds the damage is that 3.5 s is
/// the whole exposure: it was 60 s and rising when this was measured, and at
/// that client's pacing one connection would have held the worker for 450 s.
const BODY_DEADLINE: Micros = Micros::from_millis(2_500);

/// A stamp taken at handler entry, against which the whole body read is bounded.
#[derive(Clone, Copy)]
pub(crate) struct Deadline(Micros);

impl Deadline {
    pub(crate) fn start() -> Deadline {
        Deadline(crate::CTX.clock.now())
    }

    pub(crate) fn expired(&self) -> bool {
        crate::CTX.clock.now() - self.0 > BODY_DEADLINE
    }
}

/// Answer 408 and make sure IDF cannot re-block on the body we did not read.
///
/// THE SHUTDOWN IS THE POINT, not the status line. `httpd_req_delete`
/// (httpd_parse.c) PURGES any unread body after the handler returns, in a loop
/// of `httpd_req_recv` calls governed by the same per-recv timeout — so simply
/// answering and returning would hand the dribbling client the worker straight
/// back through IDF's own code. Shutting the read half down makes the next
/// recv return 0, the purge fail, and the session close. It is the one action
/// that ends the request rather than deferring it.
pub(crate) fn abandon_body(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    respond_and_close(
        req,
        c"408 Request Timeout",
        br#"{"ok":false,"error":"body not delivered in time"}"#,
    );
    sys::ESP_FAIL
}

/// Answer, then stop reading. For any handler that will NOT consume the body.
///
/// A HANDLER THAT ANSWERS WITHOUT READING ITS BODY IS NOT FINISHED WITH THE
/// CONNECTION — `httpd_req_delete` purges whatever is left through the same
/// per-recv timeout, with no deadline of ours anywhere near it. So a route that
/// declines its input (the multi-profile answers in `net::profile`, an avatar
/// upload this device has no storage for) is exactly as dribble-able as the
/// body readers were, on a body that could be a megabyte. Shutting the read
/// half down makes the next recv return 0, the purge fail, and the session
/// close — after the response has already gone out, so the client still reads
/// the sentence it needs.
pub(crate) fn respond_and_close(
    req: *mut sys::httpd_req_t,
    status: &core::ffi::CStr,
    body: &[u8],
) -> sys::esp_err_t {
    respond(req, status, body);
    // SAFETY: `httpd_req_to_sockfd` reads a scalar out of the live request and
    // returns -1 if there is no socket, which the guard below rejects. Both
    // calls take integers only; no Rust memory is shared with either.
    unsafe {
        let fd = sys::httpd_req_to_sockfd(req);
        if fd >= 0 {
            sys::lwip_shutdown(fd, sys::SHUT_RD as i32);
        }
    }
    sys::ESP_OK
}

/// The body length declared on a live IDF request.
pub(crate) fn request_content_len(req: *mut sys::httpd_req_t) -> usize {
    // SAFETY: reading a scalar field of the live request.
    unsafe { (*req).content_len }
}

/// Reject a body on an endpoint whose contract has no body.
///
/// The read-side shutdown is load-bearing: returning after an ordinary
/// response lets IDF purge the unread body on the sole HTTP worker, and a peer
/// can keep that purge alive forever by sending one byte before each receive
/// timeout. Empty requests preserve their existing endpoint behavior.
pub(crate) fn reject_unexpected_body(req: *mut sys::httpd_req_t) -> bool {
    if request_content_len(req) == 0 {
        return false;
    }
    respond_and_close(
        req,
        c"400 Bad Request",
        br#"{"ok":false,"error":"request body not allowed"}"#,
    );
    true
}

pub(crate) fn respond(
    req: *mut sys::httpd_req_t,
    status: &core::ffi::CStr,
    body: &[u8],
) -> sys::esp_err_t {
    // SAFETY: `req` is live for the call; `status` and `body` outlive it (both
    // are borrowed from the caller's frame or `'static`). IDF copies what it
    // sends before returning.
    unsafe {
        sys::httpd_resp_set_status(req, status.as_ptr());
        sys::httpd_resp_set_type(req, c"application/json".as_ptr());
        sys::httpd_resp_send(
            req,
            body.as_ptr() as *const core::ffi::c_char,
            body.len() as isize,
        )
    }
}

/// Read the request body into a budgeted slot, or answer the refusal ourselves.
///
/// Returns `None` when the request has already been answered.
pub(crate) fn read_body(
    req: *mut sys::httpd_req_t,
    out: &mut [u8; MAX_CMD_BODY],
) -> Option<usize> {
    read_body_into(req, out)
}

/// [`read_body`] over a caller-sized buffer.
///
/// The command endpoints want 128 bytes and the coach endpoints want 512, and
/// the correct answer to that is ONE reader with a caller-supplied cap rather
/// than a second copy of the admission, the deadline and the abandon path. The
/// array-typed `read_body` above is kept because a dozen call sites read better
/// with it, and it is now a one-line delegation rather than a duplicate.
pub(crate) fn read_body_into(req: *mut sys::httpd_req_t, out: &mut [u8]) -> Option<usize> {
    let declared = request_content_len(req);

    // ADMISSION FIRST — nothing is read before the budget says yes.
    let lease = match reqbudget::admit(declared) {
        Ok(l) => l,
        Err(refusal) => {
            let (status, body): (&core::ffi::CStr, &[u8]) = match refusal {
                reqbudget::Refusal::TooLarge => (
                    c"413 Payload Too Large",
                    br#"{"ok":false,"error":"body too large"}"#,
                ),
                reqbudget::Refusal::Busy => (
                    c"503 Service Unavailable",
                    br#"{"ok":false,"error":"server busy"}"#,
                ),
            };
            // No byte was consumed, so close the read side with the response;
            // otherwise IDF purges the rejected body with only a per-recv
            // timeout and a dribbler can retain the sole worker indefinitely.
            respond_and_close(req, status, body);
            return None;
        }
    };
    // The lease is what bounds us; this handler additionally caps at its own
    // stack buffer, so `declared` can never exceed `out`.
    if declared > out.len() {
        drop(lease);
        respond_and_close(
            req,
            c"413 Payload Too Large",
            br#"{"ok":false,"error":"body too large"}"#,
        );
        return None;
    }

    let deadline = Deadline::start();
    let mut got = 0usize;
    while got < declared {
        if deadline.expired() {
            drop(lease);
            abandon_body(req);
            return None;
        }
        // SAFETY: `out` is a live exclusive borrow with room for `declared`
        // bytes (checked above); IDF writes at most the length we pass.
        let n = unsafe {
            sys::httpd_req_recv(
                req,
                out.as_mut_ptr().add(got) as *mut core::ffi::c_char,
                declared - got,
            )
        };
        if n <= 0 {
            drop(lease);
            respond_and_close(
                req,
                c"400 Bad Request",
                br#"{"ok":false,"error":"short body"}"#,
            );
            return None;
        }
        got += n as usize;
    }
    // Lease drops here: the body is already copied into `out`, so the slot is
    // free for the next request while this one computes.
    drop(lease);
    Some(got)
}

/// Extract the number after `"value"` as hundredths, without floating point.
///
/// Accepts `12`, `12.5`, `12.50`; returns hundredths (1250). Deliberately
/// hand-rolled and total: no allocation, no panic path, and a malformed body
/// yields `None` rather than a default that would silently command something.
pub(crate) fn parse_value_hundredths(body: &[u8]) -> Option<i32> {
    let pos = body.windows(5).position(|w| w == b"value")?;
    let mut i = pos + 5;
    while i < body.len() && (body[i] == b'"' || body[i] == b':' || body[i] == b' ') {
        i += 1;
    }
    let neg = i < body.len() && body[i] == b'-';
    if neg {
        i += 1;
    }
    let mut whole: i32 = 0;
    let mut seen = false;
    while i < body.len() && body[i].is_ascii_digit() {
        whole = whole.checked_mul(10)?.checked_add((body[i] - b'0') as i32)?;
        seen = true;
        i += 1;
    }
    if !seen {
        return None;
    }
    let mut frac: i32 = 0;
    let mut scale = 0;
    if i < body.len() && body[i] == b'.' {
        i += 1;
        while i < body.len() && body[i].is_ascii_digit() && scale < 2 {
            frac = frac * 10 + (body[i] - b'0') as i32;
            scale += 1;
            i += 1;
        }
    }
    while scale < 2 {
        frac *= 10;
        scale += 1;
    }
    let v = whole.checked_mul(100)?.checked_add(frac)?;
    Some(if neg { -v } else { v })
}

/// The number after `"<key>"`, in hundredths.
///
/// [`parse_value_hundredths`] anchored on an arbitrary key rather than on the
/// literal `value`, so the small-body endpoints that carry several named
/// numbers (`weight_lbs`, `vest_lbs`) share ONE number parser with
/// `/api/speed` instead of growing a second one.
pub(crate) fn parse_key_hundredths(body: &[u8], key: &[u8]) -> Option<i32> {
    if key.is_empty() {
        return None;
    }
    // ANCHORED AS A JSON MEMBER: `"key"` followed by optional spaces and a
    // colon. Searching for the bare key bytes and then "the next colon
    // anywhere" made `{"note":"weight_lbs","x":500}` set the weight — the key
    // matched inside a VALUE — and made `{"delta_seconds":300}` satisfy a scan
    // for `seconds`, because one key is a suffix of the other. Neither is a
    // parser bug worth a parser; both are a missing quote.
    let mut pat = [0u8; 24];
    if key.len() + 2 > pat.len() {
        return None;
    }
    pat[0] = b'"';
    pat[1..1 + key.len()].copy_from_slice(key);
    pat[1 + key.len()] = b'"';
    let pat = &pat[..key.len() + 2];
    if pat.len() > body.len() {
        return None;
    }
    let pos = body.windows(pat.len()).position(|w| w == pat)?;
    let mut i = pos + pat.len();
    while i < body.len() && body[i] == b' ' {
        i += 1;
    }
    if i >= body.len() || body[i] != b':' {
        return None;
    }
    // Splice the marker the shared scanner looks for in front of the value,
    // rather than duplicating forty lines of digit handling.
    let mut buf = [0u8; 40];
    let head = b"\"value\":";
    buf[..head.len()].copy_from_slice(head);
    let tail = &body[i + 1..];
    let n = core::cmp::min(tail.len(), buf.len() - head.len());
    buf[head.len()..head.len() + n].copy_from_slice(&tail[..n]);
    parse_value_hundredths(&buf[..head.len() + n])
}

/// The STRING after `"<key>"`, copied into `out`. Returns its length.
///
/// The number parsers above cover every endpoint that carries a value; this
/// covers the one that carries an identifier (`POST /api/hrm/select` with a
/// BLE address). Anchored as a JSON member the same way [`parse_key_hundredths`]
/// is, for the same reason: searching for the bare key bytes matches inside a
/// VALUE, so `{"note":"address","x":"..."}` would set the address.
///
/// BOUNDED AND TOTAL. The copy stops at `out.len()` and the scan stops at the
/// end of the body, so a body with an unterminated string yields `None` rather
/// than running off the end. Escape sequences are NOT interpreted — nothing
/// this parses has any (a BLE address is hex and colons), and a `\"` inside the
/// value therefore ends it early, which is a rejected address rather than a
/// misread one.
pub(crate) fn parse_key_str(body: &[u8], key: &[u8], out: &mut [u8]) -> Option<usize> {
    if key.is_empty() {
        return None;
    }
    let mut pat = [0u8; 24];
    if key.len() + 2 > pat.len() {
        return None;
    }
    pat[0] = b'"';
    pat[1..1 + key.len()].copy_from_slice(key);
    pat[1 + key.len()] = b'"';
    let pat = &pat[..key.len() + 2];
    if pat.len() > body.len() {
        return None;
    }
    let pos = body.windows(pat.len()).position(|w| w == pat)?;
    let mut i = pos + pat.len();
    while i < body.len() && body[i] == b' ' {
        i += 1;
    }
    if i >= body.len() || body[i] != b':' {
        return None;
    }
    i += 1;
    while i < body.len() && body[i] == b' ' {
        i += 1;
    }
    if i >= body.len() || body[i] != b'"' {
        return None;
    }
    i += 1;
    let start = i;
    while i < body.len() && body[i] != b'"' {
        i += 1;
    }
    if i >= body.len() {
        return None; // unterminated
    }
    let n = i - start;
    if n > out.len() {
        return None;
    }
    out[..n].copy_from_slice(&body[start..i]);
    Some(n)
}

/// GET /api/status — read-only, no lease, no belt effect.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn status_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; STATUS_BUF];
    let n = render_status(&mut buf, "");
    respond(req, c"200 OK", &buf[..n])
}

/// Every status body in the firmware comes from here, so the shape cannot
/// diverge between GET /api/status and the reply to a motion command.
///
/// `lead` is inserted immediately after the opening brace (`"ok":true,` for the
/// POST replies, empty for GET).
pub(crate) fn render_status(buf: &mut [u8; STATUS_BUF], lead: &str) -> usize {
    let (speed_tenths, incline_half, mode, relay, fault, connected) = {
        let g = lock(&crate::CTX.guarded);
        let now = crate::CTX.clock.now();
        // "Is the treadmill there?" has exactly one honest answer on this
        // device: whether a COMPLETE console frame arrived recently, which is
        // the same freshness the safety controller itself gates emulate entry
        // on. It is not a link-layer notion and it is not faked from the fact
        // that the firmware is running.
        let connected = match g.controller.last_complete_console_frame_at() {
            Some(ts) => {
                let age = now - ts;
                age >= Micros::ZERO && age < CONSOLE_FRESH_US
            }
            None => false,
        };
        (
            g.controller.speed_tenths().get(),
            g.controller.incline_half_percent().get(),
            g.controller.mode(),
            g.controller.relay_cmd().get(),
            g.controller.fault_latched(),
            connected,
        )
    };
    format_status(
        buf,
        lead,
        speed_tenths,
        incline_half,
        mode,
        relay,
        fault,
        connected,
    )
}

/// Render the status JSON without allocating or using floats.
///
/// THE FIELD SET IS THE PI'S, not this device's convenience. The Android app
/// declares `proxy`, `emulate`, `emu_speed`, `emu_speed_mph`, `emu_incline` and
/// `treadmill_connected` with NO kotlinx default, which makes every one of them
/// REQUIRED — `coerceInputValues` rewrites an explicit null into a default that
/// exists, it cannot invent one — so a body missing any of them throws
/// MissingFieldException in the client rather than degrading. `speed`,
/// `incline`, `mode`, `relay` and `fault` are the device's own additions and
/// are ignored by clients that do not know them (`ignoreUnknownKeys`).
///
/// UNITS ARE THE PI'S TOO, and they are not uniform: `emu_speed` is TENTHS of
/// mph as an integer (the app divides by 10 in three places), while
/// `emu_speed_mph`, `speed`, `incline` and `emu_incline` are decimal mph/percent.
///
/// `speed` here is the COMMANDED speed, not a decoded motor reading: the Pi
/// fills it from the motor tap's `hmph` and this firmware's safety core does
/// not consume motor KV. Stated rather than silently different.
#[allow(clippy::too_many_arguments)]
fn format_status(
    buf: &mut [u8; STATUS_BUF],
    lead: &str,
    speed_tenths: i32,
    incline_half: i32,
    mode: safety_core::safety::controller::SafeMode,
    relay: bool,
    fault: bool,
    connected: bool,
) -> usize {
    use core::fmt::Write;
    use safety_core::safety::controller::SafeMode;
    let mut w = crate::net::api::BufWriter { buf, len: 0 };
    let emulating = mode == SafeMode::Emulating;
    // speed is tenths of mph, incline is half-percent — rendered to one decimal
    // exactly as every UI in this project displays them.
    let (sw, sf) = (speed_tenths / 10, speed_tenths % 10);
    let (iw, if_) = (
        incline_half / 2,
        if incline_half % 2 == 0 { 0 } else { 5 },
    );
    // HEART RATE RIDES IN THE STATUS FRAME, and not only in the `hr` one. The
    // app's `StatusMessage` declares `heart_rate`, `hrm_connected` and
    // `hrm_device` (with lenient defaults, so they are optional), and
    // `TreadmillViewModel` writes the SAME three StateFlow fields from both
    // the status frame and the dedicated `hr` push. Sending them here means a
    // client that connected mid-session sees the current bpm on its first
    // frame instead of waiting for the next heartbeat.
    //
    // The device name came off the air from a stranger; it is safe between
    // two `"` because `ble_core::peer::FixedName` made it safe at ingest.
    let hr = crate::hr::snapshot();
    let _ = write!(
        w,
        concat!(
            r#"{{{}"type":"status","proxy":{},"emulate":{},"emu_speed":{},"#,
            r#""emu_speed_mph":{}.{},"emu_incline":{}.{},"speed":{}.{},"#,
            r#""incline":{}.{},"treadmill_connected":{},"mode":"{}","#,
            r#""relay":{},"fault":{},"heart_rate":{},"hrm_connected":{},"#,
            r#""hrm_device":"{}"}}"#
        ),
        lead,
        !emulating,
        emulating,
        speed_tenths,
        sw,
        sf,
        iw,
        if_,
        sw,
        sf,
        iw,
        if_,
        connected,
        mode_name(mode),
        relay,
        fault,
        hr.bpm,
        hr.connected,
        hr.name.as_str(),
    );
    w.len
}

const fn mode_name(m: safety_core::safety::controller::SafeMode) -> &'static str {
    use safety_core::safety::controller::SafeMode as M;
    match m {
        M::Proxy => "proxy",
        M::EntryWaitGap | M::EntryWaitFeedback => "entering",
        M::Emulating => "emulate",
        M::ExitWaitGap | M::ExitWaitFeedback => "exiting",
    }
}

/// Status bodies are rendered into a fixed stack buffer; `BufWriter` REFUSES to
/// overflow it, so a body that outgrows this is truncated into a visible
/// failure rather than smashing the httpd task's stack. Measured longest
/// rendering (`"ok":true,` lead, negative-free maxima) was ~215 bytes before
/// the three heart-rate fields; those add at most 24 (`MAX_NAME`) + 46 for the
/// keys and a five-digit bpm, so ~285. 448 keeps the same margin the 320 had.
pub(crate) const STATUS_BUF: usize = 448;

struct BufWriter<'a> {
    buf: &'a mut [u8; STATUS_BUF],
    len: usize,
}

impl core::fmt::Write for BufWriter<'_> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let b = s.as_bytes();
        if self.len + b.len() > self.buf.len() {
            return Err(core::fmt::Error);
        }
        self.buf[self.len..self.len + b.len()].copy_from_slice(b);
        self.len += b.len();
        Ok(())
    }
}

fn motion_intent(is_incline: bool, speed: SpeedTenths) -> EntryIntent {
    if !is_incline && speed.get() > 0 {
        EntryIntent::ExplicitRecovery
    } else {
        EntryIntent::Ordinary
    }
}

/// POST /api/speed and /api/incline share everything but which unit they set.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn motion_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    // Which endpoint? `user_ctx` carries 0 = speed, 1 = incline.
    let is_incline = (*req).user_ctx as usize == 1;

    let mut body = [0u8; MAX_CMD_BODY];
    let Some(n) = read_body(req, &mut body) else {
        return sys::ESP_OK; // already answered
    };
    let Some(hundredths) = parse_value_hundredths(&body[..n]) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"missing or malformed value"}"#,
        );
    };

    let outcome = {
        let mut g = lock(&crate::CTX.guarded);
        let now = crate::CTX.clock.now();
        // Convert to the controller's units and let IT clamp. Speed is tenths
        // of mph; incline is half-percent. The axis not being set is carried
        // through unchanged, read from the controller rather than remembered.
        let (sp, inc) = if is_incline {
            (
                g.controller.speed_tenths(),
                // `/50`, not `*2/100`: identical for every value either can
                // represent, and total. `{"value":21474836.47}` parses to
                // i32::MAX, and `* 2` on that wraps to a NEGATIVE incline in
                // release and panics in debug — a panic here is a reboot, and
                // a reboot drops the relay.
                InclineHalfPct::new(hundredths / 50),
            )
        } else {
            (
                SpeedTenths::new(hundredths / 10),
                g.controller.incline_half_percent(),
            )
        };
        // THE ONE PATH TO THE BELT — the same call the interval executor
        // makes. Lease, clamps, auto-emulate and apply_outputs all live there,
        // so an HTTP request is just another owner and this handler contains
        // no opinion about safety at all.
        control::command(&mut g, Surface::Http, motion_intent(is_incline, sp), sp, inc, now)
    };
    match outcome {
        // ANSWER WITH THE STATE, not with `{"ok":true}`. The app types
        // `setSpeed`/`setIncline` as returning a full `StatusMessage` (the Pi
        // returns `build_status()` from both), and six of its fields have no
        // kotlinx default, so a bare ok-body throws MissingFieldException in
        // the client — swallowed by its `runCatching`, leaving the UI with only
        // its optimistic local echo and no path back to the truth. `ok` is kept
        // as well: it costs 12 bytes, it is what the QEMU scenarios assert on,
        // and an unknown key is ignored by every client here.
        Ok(()) => {
            let mut buf = [0u8; STATUS_BUF];
            let n = render_status(&mut buf, r#""ok":true,"#);
            respond(req, c"200 OK", &buf[..n])
        }
        // A program owns the belt. Refusing is the deliberate answer: the
        // alternative is taking the lease from the executor, which
        // emergency-stops it — relay open, belt dead, mid-stride. The Pi
        // resolves this by SPLITTING the running manual program at the new
        // speed (`ProgramState.split_for_manual`); that behaviour needs its
        // own slice and is not pretended at here.
        Err(control::Reject::NotOwner) => respond(
            req,
            c"409 Conflict",
            br#"{"ok":false,"error":"a program owns the belt; pause or stop it first"}"#,
        ),
        Err(control::Reject::ExecutorInhibited) => respond(req, c"409 Conflict", br#"{"ok":false,"error":"program executor is safety-paused; explicit resume required"}"#),
        // The controller refused — clamp violation, wrong mode, or a latched
        // fault. It has already recorded WHY in the audit ring; the handler
        // does not second-guess or paraphrase it.
        Err(control::Reject::Refused | control::Reject::ExitInProgress) => respond(
            req,
            c"409 Conflict",
            br#"{"ok":false,"error":"rejected by safety controller"}"#,
        ),
        Err(control::Reject::GenerationExhausted) => respond(
            req,
            c"503 Service Unavailable",
            br#"{"ok":false,"error":"generation exhausted"}"#,
        ),
    }
}


/// Register the control-path routes on an already-started server.
///
/// The profile routes moved to `net::profile` when they stopped being a
/// constant: they now read and write NVS, which is not this module's concern.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly
    // and introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 3] = [
        (c"/api/status", sys::http_method_HTTP_GET, status_handler, usize::MAX),
        (c"/api/speed", sys::http_method_HTTP_POST, motion_handler, 0),
        (c"/api/incline", sys::http_method_HTTP_POST, motion_handler, 1),
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
        // SAFETY: `handle` is a live server; `uri` is read for the call.
        let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
        if err != sys::ESP_OK {
            return Err(err);
        }
    }
    Ok(())
}
