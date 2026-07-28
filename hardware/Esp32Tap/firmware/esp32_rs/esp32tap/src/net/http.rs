//! Slice 1 — the HTTP server, on ESP-IDF's own `esp_http_server`.
//!
//! Plain HTTP for now; TLS is the same server started via `httpd_ssl_start`
//! once the device can generate its own certificate, and the handler shape
//! below does not change when that happens.
//!
//! SOCKET BUDGET IS A SAFETY PARAMETER, NOT A TUNING KNOB. `max_open_sockets`
//! bounds how many clients can occupy the server at once, and IDF runs ONE
//! worker across all of them. The C++ attempt at this tier shipped 3 and the
//! app's own request burst LRU-purged its own WebSocket; it also proved that a
//! client which dribbles bytes can hold the single worker indefinitely. Both
//! are why `recv_wait_timeout`/`send_wait_timeout` are set low here and why a
//! request-duration bound belongs on top of them before any endpoint that can
//! command the belt exists.
//!
//! IDF DEFAULTS ARE REPLICATED, NOT GUESSED: `HTTPD_DEFAULT_CONFIG()` is a C
//! macro that does not survive bindgen, so its expansion is transcribed from
//! esp_http_server.h:53 (IDF v5.5) with every deviation called out.

use esp_idf_sys as sys;

/// Banner served at `/`. Byte-identical to what `python/server.py` returns, so
/// a client cannot tell the Pi and the device apart at this endpoint.
const BANNER: &[u8] = br#"{"service":"precor-treadmill","api":"/api","ws":"/ws"}"#;

const PORT: u16 = 8000;

/// GET / — the discovery banner.
///
/// SAFETY: `req` is a live IDF request pointer valid for the call; the two
/// helpers only read it and the byte slices outlive the call (both are
/// `'static`). Returning ESP_OK tells IDF the response is complete.
unsafe extern "C" fn banner_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    sys::httpd_resp_set_type(req, c"application/json".as_ptr());
    sys::httpd_resp_send(
        req,
        BANNER.as_ptr() as *const core::ffi::c_char,
        BANNER.len() as isize,
    )
}


/// Maximum inbound WebSocket frame we will read, in bytes.
///
/// A HARD BOUND, not a tuning knob: the frame is read into a fixed stack
/// buffer, so an oversized frame is refused rather than allocated for. The app
/// only ever sends small control JSON on this socket; anything larger is
/// either a mistake or an attack.
const MAX_WS_FRAME: usize = 512;

/// GET /ws — the live status stream.
///
/// IDF calls this once with `method == HTTP_GET` to complete the handshake
/// (return OK and send nothing), then again per inbound frame.
///
/// SAFETY: `req` is a live IDF request pointer valid for the duration of the
/// call. `frame` is fully initialised before every FFI use; `buf` outlives
/// each call that borrows it. No pointer derived from `req` is retained.
unsafe extern "C" fn ws_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    if (*req).method == sys::http_method_HTTP_GET as i32 {
        // Handshake completed by IDF. Greet the client so a connection that
        // opens but never receives anything is distinguishable from a healthy
        // idle one — the C++ attempt had a saturation path where the socket
        // opened and then silently delivered nothing forever.
        let hello = br#"{"type":"connection","connected":true}"#;
        let mut frame: sys::httpd_ws_frame_t = core::mem::zeroed();
        frame.type_ = sys::httpd_ws_type_t_HTTPD_WS_TYPE_TEXT;
        frame.payload = hello.as_ptr() as *mut u8;
        frame.len = hello.len();
        return sys::httpd_ws_send_frame(req, &mut frame);
    }

    // Inbound frame: read the header first (max_len = 0 reports the true
    // length without copying), refuse anything over budget, then read the body
    // into a fixed buffer.
    let mut frame: sys::httpd_ws_frame_t = core::mem::zeroed();
    let err = sys::httpd_ws_recv_frame(req, &mut frame, 0);
    if err != sys::ESP_OK {
        return err;
    }
    if frame.len > MAX_WS_FRAME {
        // Refuse at admission rather than allocating for a hostile frame.
        return sys::ESP_ERR_INVALID_SIZE;
    }
    let mut buf = [0u8; MAX_WS_FRAME];
    if frame.len > 0 {
        frame.payload = buf.as_mut_ptr();
        let err = sys::httpd_ws_recv_frame(req, &mut frame, MAX_WS_FRAME);
        if err != sys::ESP_OK {
            return err;
        }
    }
    // Slice 1 has no command vocabulary yet — later slices dispatch here.
    sys::ESP_OK
}

fn zeroed<T>() -> T {
    // SAFETY: `httpd_config_t` is a bindgen POD of integers, pointers and
    // function pointers; all-zero is a valid initial value and every field the
    // server requires is set explicitly below.
    unsafe { core::mem::zeroed() }
}

/// Start the HTTP server and register the banner route.
///
/// Returns the handle so later slices can register more routes on the same
/// server rather than starting a second one.
pub fn start() -> Result<sys::httpd_handle_t, sys::esp_err_t> {
    // HTTPD_DEFAULT_CONFIG() — esp_http_server.h:53. Deviations are marked.
    let mut cfg: sys::httpd_config_t = zeroed();
    cfg.task_priority = 5; // tskIDLE_PRIORITY + 5
    cfg.stack_size = 4096;
    cfg.core_id = i32::MAX; // tskNO_AFFINITY
    cfg.task_caps = (sys::MALLOC_CAP_INTERNAL | sys::MALLOC_CAP_8BIT) as u32;
    cfg.max_req_hdr_len = sys::CONFIG_HTTPD_MAX_REQ_HDR_LEN as usize;
    cfg.max_uri_len = sys::CONFIG_HTTPD_MAX_URI_LEN as usize;
    cfg.server_port = PORT; // DEVIATION: 80 -> 8000, the mDNS-advertised port
    cfg.ctrl_port = 32768;
    cfg.max_open_sockets = 7;
    cfg.max_uri_handlers = 8;
    cfg.max_resp_headers = 8;
    cfg.backlog_conn = 5;
    cfg.lru_purge_enable = false;
    cfg.recv_wait_timeout = 5;
    cfg.send_wait_timeout = 5;

    let mut handle: sys::httpd_handle_t = core::ptr::null_mut();
    // SAFETY: `cfg` is a live borrow read for the call; `handle` is written
    // exactly once on success. The server task IDF spawns outlives this frame,
    // which is why nothing borrowed from the stack is handed to it.
    let err = unsafe { sys::httpd_start(&mut handle, &cfg) };
    if err != sys::ESP_OK {
        return Err(err);
    }

    let uri = sys::httpd_uri_t {
        uri: c"/".as_ptr(),
        method: sys::http_method_HTTP_GET,
        handler: Some(banner_handler),
        user_ctx: core::ptr::null_mut(),
        is_websocket: false,
        handle_ws_control_frames: false,
        supported_subprotocol: core::ptr::null(),
    };
    // SAFETY: `handle` is the live server just started; `uri` is read for the
    // call and IDF copies what it retains. The handler is a `'static` fn.
    let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
    if err != sys::ESP_OK {
        return Err(err);
    }

    let ws = sys::httpd_uri_t {
        uri: c"/ws".as_ptr(),
        method: sys::http_method_HTTP_GET,
        handler: Some(ws_handler),
        user_ctx: core::ptr::null_mut(),
        is_websocket: true,
        // Let IDF answer PING/CLOSE itself. The C++ attempt took control
        // frames and then never answered PING nor completed the CLOSE
        // handshake; delegating removes that whole class of bug.
        handle_ws_control_frames: false,
        supported_subprotocol: core::ptr::null(),
    };
    // SAFETY: as above — live handle, borrowed-for-the-call descriptor.
    let err = unsafe { sys::httpd_register_uri_handler(handle, &ws) };
    if err != sys::ESP_OK {
        return Err(err);
    }
    Ok(handle)
}

pub const fn port() -> u16 {
    PORT
}
