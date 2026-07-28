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
use crate::control::{self, Surface};
use esp_idf_sys as sys;
use safety_core::safety::constants::CONSOLE_FRESH_US;
use safety_core::units::{InclineHalfPct, Micros, SpeedTenths};

/// Largest body we will read on a command endpoint. Well under a budget slot;
/// `{"value":12.5}` is 15 bytes.
pub(crate) const MAX_CMD_BODY: usize = 128;

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
    // SAFETY: reading a scalar field of a live request.
    let declared = unsafe { (*req).content_len };

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
            respond(req, status, body);
            return None;
        }
    };
    // The lease is what bounds us; this handler additionally caps at its own
    // stack buffer, so `declared` can never exceed `out`.
    if declared > MAX_CMD_BODY {
        drop(lease);
        respond(
            req,
            c"413 Payload Too Large",
            br#"{"ok":false,"error":"body too large"}"#,
        );
        return None;
    }

    let mut got = 0usize;
    while got < declared {
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
            respond(
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
fn render_status(buf: &mut [u8; STATUS_BUF], lead: &str) -> usize {
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
    let _ = write!(
        w,
        concat!(
            r#"{{{}"type":"status","proxy":{},"emulate":{},"emu_speed":{},"#,
            r#""emu_speed_mph":{}.{},"emu_incline":{}.{},"speed":{}.{},"#,
            r#""incline":{}.{},"treadmill_connected":{},"mode":"{}","#,
            r#""relay":{},"fault":{}}}"#
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
        fault
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
/// rendering (`"ok":true,` lead, negative-free maxima) is ~215 bytes.
const STATUS_BUF: usize = 320;

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
                InclineHalfPct::new(hundredths * 2 / 100),
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
        control::command(&mut g, Surface::Http, sp, inc, now)
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
        // The controller refused — clamp violation, wrong mode, or a latched
        // fault. It has already recorded WHY in the audit ring; the handler
        // does not second-guess or paraphrase it.
        Err(control::Reject::Refused) => respond(
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


// ---------------------------------------------------------------------------
// Profiles — the surface the Android app needs to get past its FIRST screen.
//
// The app opens on ProfilePickerScreen and cannot reach the Lobby without
// these. That is why they exist now, ahead of the storage tier.
//
// ONE BUILT-IN PROFILE, HELD IN RAM, NOT PERSISTED. This is honest rather than
// pretend: a rename or a second profile would be lost on reboot, so neither is
// offered. Real multi-profile support needs the flash store (Slice 5), and the
// Pi's own model is richer still (avatars as BLOBs, guest mode, per-profile
// history). Emitting a believable-but-unstorable profile set would be worse
// than emitting one true one.
//
// Every field the Kotlin `Profile` model declares has a default, so a client
// tolerates anything we omit — but the fields below are the ones it renders,
// so they are all present and real.
// br##"..."## and not br#"..."#: the colour value contains `"#`, which
// terminates the single-hash raw string early.
const PROFILE_JSON: &[u8] = br##"{"id":"local","name":"Runner","color":"#d4c4a8","initials":"R","weight_lbs":154.0,"vest_lbs":0.0,"has_avatar":false}"##;

/// GET /api/profiles — the list the picker renders.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn profiles_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; 256];
    buf[0] = b'[';
    buf[1..1 + PROFILE_JSON.len()].copy_from_slice(PROFILE_JSON);
    buf[1 + PROFILE_JSON.len()] = b']';
    respond(req, c"200 OK", &buf[..PROFILE_JSON.len() + 2])
}

/// GET /api/profile/active — which profile is current, and whether we are a guest.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn active_profile_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; 256];
    let head = br#"{"guest_mode":false,"profile":"#;
    let n = head.len();
    buf[..n].copy_from_slice(head);
    buf[n..n + PROFILE_JSON.len()].copy_from_slice(PROFILE_JSON);
    buf[n + PROFILE_JSON.len()] = b'}';
    respond(req, c"200 OK", &buf[..n + PROFILE_JSON.len() + 1])
}

/// POST /api/profile/select — accept the selection of the only profile there is.
///
/// Deliberately does NOT read or validate a body: with one built-in profile
/// there is nothing to choose between, and pretending to honour an arbitrary id
/// would be a lie the storage tier has to keep later.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn select_profile_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; 256];
    let head = br#"{"ok":true,"guest_mode":false,"profile":"#;
    let n = head.len();
    buf[..n].copy_from_slice(head);
    buf[n..n + PROFILE_JSON.len()].copy_from_slice(PROFILE_JSON);
    buf[n + PROFILE_JSON.len()] = b'}';
    respond(req, c"200 OK", &buf[..n + PROFILE_JSON.len() + 1])
}

/// Register the control-path and profile routes on an already-started server.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly
    // and introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 6] = [
        (c"/api/status", sys::http_method_HTTP_GET, status_handler, usize::MAX),
        (c"/api/speed", sys::http_method_HTTP_POST, motion_handler, 0),
        (c"/api/incline", sys::http_method_HTTP_POST, motion_handler, 1),
        (c"/api/profiles", sys::http_method_HTTP_GET, profiles_handler, usize::MAX),
        (c"/api/profile/active", sys::http_method_HTTP_GET, active_profile_handler, usize::MAX),
        (c"/api/profile/select", sys::http_method_HTTP_POST, select_profile_handler, usize::MAX),
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
