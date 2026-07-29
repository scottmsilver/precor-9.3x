//! `/api/hrm*` — the heart-rate monitor, as the app already expects it.
//!
//! Four routes, the Pi's exact set: `GET /api/hrm`, `POST /api/hrm/select`,
//! `POST /api/hrm/forget`, `POST /api/hrm/scan`. `TreadmillViewModel` calls
//! all four (`selectHrmDevice`, `forgetHrmDevice`, `scanHrmDevices`), so a
//! device that answered only the GET would 404 the picker.
//!
//! THEY EXIST WITHOUT A RADIO, and that is the point. `crate::hr` is compiled
//! into every build and reads as disconnected when nothing fills it, so a
//! firmware whose Bluetooth failed to come up answers `{"heart_rate":0,
//! "connected":false,...}` — which is exactly what the Pi answers when
//! `hrm-daemon` is not running, and exactly what the three Kotlin call sites
//! already handle (`heartRate > 0` gates every one of them).
//!
//! WRITES ARE ASYNCHRONOUS, deliberately. `select`/`forget`/`scan` post into
//! `crate::hr`'s single-slot mailbox and answer immediately with the CURRENT
//! state; the BLE central acts on the next 1 Hz tick. The alternative is an
//! HTTP handler that blocks the ONE httpd worker on a radio operation that can
//! take seconds — and the Stop button is behind that worker.

use crate::hr;
use crate::net::api::{
    parse_key_str, read_body, respond, respond_and_close, MAX_CMD_BODY,
};
use esp_idf_sys as sys;

/// Longest `/api/hrm` body. Six devices at ~70 bytes each plus the fixed
/// header; `BufWriter` REFUSES to overflow, so a wrong number here truncates
/// into a visible failure rather than smashing the httpd task's stack.
pub const HRM_BUF: usize = 768;

struct W<'a> {
    b: &'a mut [u8],
    n: usize,
}

impl core::fmt::Write for W<'_> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let x = s.as_bytes();
        if self.n + x.len() > self.b.len() {
            return Err(core::fmt::Error);
        }
        self.b[self.n..self.n + x.len()].copy_from_slice(x);
        self.n += x.len();
        Ok(())
    }
}

/// Render the `/api/hrm` body.
///
/// FIELD NAMES ARE THE PI'S: `HrmStatusResponse` in the Android app declares
/// `heart_rate`, `connected`, `device` and `available_devices`, and
/// `HrmDevice` declares `address`, `name`, `rssi`. All of them carry kotlinx
/// defaults, so an omission degrades rather than throwing — but there is no
/// reason to omit any.
///
/// Every string here came off the air from a stranger and is safe to embed
/// because `ble_core::peer::FixedName` made it safe at INGEST: no `"`, no `\`,
/// no control bytes, ever. There is deliberately no escaping step at this end,
/// because an escaping step at this end is one every future renderer would
/// have to remember.
pub fn render(buf: &mut [u8], lead: &str) -> usize {
    use core::fmt::Write;
    let s = hr::snapshot();
    let mut w = W { b: buf, n: 0 };
    let _ = write!(
        w,
        r#"{{{}"heart_rate":{},"connected":{},"device":"{}","address":"{}","scanning":{},"available_devices":["#,
        lead,
        s.bpm,
        s.connected,
        s.name.as_str(),
        s.addr_str(),
        s.scanning,
    );
    for i in 0..s.found {
        let d = &s.devices[i];
        let mut txt = [0u8; hr::ADDR_TEXT_LEN];
        let n = d.addr.text(&mut txt);
        let addr = core::str::from_utf8(&txt[..n]).unwrap_or("");
        let _ = write!(
            w,
            r#"{}{{"address":"{}","name":"{}","rssi":{}}}"#,
            if i == 0 { "" } else { "," },
            addr,
            d.name.as_str(),
            d.rssi
        );
    }
    let _ = w.write_str("]}");
    w.n
}

/// GET /api/hrm
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn get_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut buf = [0u8; HRM_BUF];
    let n = render(&mut buf, "");
    respond(req, c"200 OK", &buf[..n])
}

/// POST /api/hrm/select — `{"address":"AA:BB:CC:DD:EE:FF"}`.
///
/// The address is parsed by `ble_core::peer::Addr::parse`, which is TOTAL and
/// host-tested over garbage: a malformed one is rejected whole rather than
/// half-parsed, because a half-parsed address is a connection attempt to a
/// device the user did not choose.
///
/// THE ADDRESS TYPE COMES FROM THE SCAN, not from the request — the app has no
/// way to know it and most straps are random-static. An address the device has
/// not seen advertise is refused with 404 rather than guessed at.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn select_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let mut body = [0u8; MAX_CMD_BODY];
    let Some(n) = read_body(req, &mut body) else {
        return sys::ESP_OK; // already answered
    };
    let mut text = [0u8; 64];
    let Some(len) = parse_key_str(&body[..n], b"address", &mut text) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"missing or malformed address"}"#,
        );
    };
    let Some(val) = hr::Addr::parse(&text[..len]) else {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"missing or malformed address"}"#,
        );
    };

    let known = hr::with(|s| {
        (0..s.found)
            .map(|i| s.devices[i].addr)
            .find(|a| a.val == val)
    });
    let Some(addr) = known else {
        return respond(
            req,
            c"404 Not Found",
            br#"{"ok":false,"error":"that device has not been seen; scan first"}"#,
        );
    };

    hr::post(hr::Command::Connect(addr));
    let mut buf = [0u8; HRM_BUF];
    let n = render(&mut buf, r#""ok":true,"#);
    respond(req, c"200 OK", &buf[..n])
}

/// POST /api/hrm/forget and POST /api/hrm/scan — no body of their own.
///
/// `user_ctx` carries 0 = forget, 1 = scan.
///
/// THE BODY IS DECLINED, NOT READ, and the connection's read half is shut
/// down with the answer: `httpd_req_delete` purges an unread body through the
/// per-recv timeout with no deadline of ours near it, so a route that answers
/// without reading is exactly as dribble-able as a body reader was. See
/// `net::api::respond_and_close`.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn action_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let scan = (*req).user_ctx as usize == 1;
    hr::post(if scan {
        hr::Command::Scan
    } else {
        hr::Command::Forget
    });
    let mut buf = [0u8; HRM_BUF];
    let n = render(&mut buf, r#""ok":true,"#);
    respond_and_close(req, c"200 OK", &buf[..n])
}

/// Register the HRM routes.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 4] = [
        (c"/api/hrm", sys::http_method_HTTP_GET, get_handler, usize::MAX),
        (
            c"/api/hrm/select",
            sys::http_method_HTTP_POST,
            select_handler,
            usize::MAX,
        ),
        (
            c"/api/hrm/forget",
            sys::http_method_HTTP_POST,
            action_handler,
            0,
        ),
        (
            c"/api/hrm/scan",
            sys::http_method_HTTP_POST,
            action_handler,
            1,
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
        // SAFETY: `handle` is a live server; `uri` is read for the call.
        let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
        if err != sys::ESP_OK {
            return Err(err);
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// The `/ws` frame
// ---------------------------------------------------------------------------

/// Bytes an `hr` frame renders into.
pub const HR_FRAME_BUF: usize = 128;

/// Render the `hr` WebSocket frame — `HRMessage` in `ServerMessages.kt`, field
/// for field.
///
/// `bpm` is NON-NULLABLE WITH NO DEFAULT in the Kotlin model, which makes it
/// REQUIRED: a frame missing it throws `MissingFieldException` in the client
/// and the whole frame is lost. It is always written here.
pub fn render_ws(buf: &mut [u8; HR_FRAME_BUF]) -> usize {
    use core::fmt::Write;
    let s = hr::snapshot();
    let mut w = W { b: buf, n: 0 };
    let _ = write!(
        w,
        r#"{{"type":"hr","bpm":{},"connected":{},"device":"{}","address":"{}"}}"#,
        s.bpm,
        s.connected,
        s.name.as_str(),
        s.addr_str(),
    );
    w.n
}
