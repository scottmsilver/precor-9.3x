//! Slice 1/3 — the app-facing server, on ESP-IDF's own `esp_https_server`.
//!
//! HTTPS ONLY. There is no plaintext listener and no fallback: the mDNS record
//! advertises `scheme=https`, and a plaintext port alongside it would mean a
//! client that ignored the TXT record still worked, so nothing downstream would
//! ever notice TLS breaking. `esp_https_server` is a thin wrapper that installs
//! an `open_fn` on the same `esp_http_server` — every handler below is
//! byte-for-byte what it was over plain HTTP.
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
//! THE BOUND HAS TWO PIECES AND BOTH ARE NEEDED. `tls_handshake_timeout_ms`
//! below covers the one phase of a connection that runs on the worker BEFORE
//! any recv/send timeout applies (at the IDF default it was a 10 s outage of
//! the whole surface per silent connection — see the derivation there). It was
//! once described here as the LAST piece, and that was WRONG: `recv_wait_timeout`
//! is a PER-RECV budget, and every body reader loops until the declared length
//! arrives, so a peer that answers each recv — one byte every 0.5 s — held the
//! single worker for 60 s with the belt moving and `POST /api/program/stop`
//! unable to complete its own handshake. The second piece is
//! `net::api::Deadline`, a stamp taken at handler entry that abandons the body
//! read once the WHOLE read has run past budget.
//!
//! TLS MOVES THAT BUDGET, so it is re-derived rather than carried over: an
//! mbedtls session is ~40 KB of context and record buffers, not a bare socket,
//! and IDF's own `HTTPD_SSL_CONFIG_DEFAULT()` drops to 4 sockets for exactly
//! that reason. 7 would be ~280 KB of a ~400 KB internal heap held by idle
//! connections. `lru_purge_enable` stays FALSE: a burst must be REFUSED, never
//! answered by silently killing the app's live WebSocket.
//!
//! IDF DEFAULTS ARE REPLICATED, NOT GUESSED: `HTTPD_DEFAULT_CONFIG()` and
//! `HTTPD_SSL_CONFIG_DEFAULT()` are C macros that do not survive bindgen, so
//! their expansions are transcribed from esp_http_server.h:53 and
//! esp_https_server.h:169 (IDF v5.5) with every deviation called out.

use crate::net::tls::Identity;
use esp_idf_sys as sys;

/// Banner served at `/`. Byte-identical to what `python/server.py` returns, so
/// a client cannot tell the Pi and the device apart at this endpoint.
const BANNER: &[u8] = br#"{"service":"precor-treadmill","api":"/api","ws":"/ws"}"#;

const PORT: u16 = 8000;

/// The httpd task's stack, in bytes.
///
/// Public because it is a BUDGET that handler modules must live inside, and a
/// budget nobody can name is a budget nobody can check: `net::program` asserts
/// its worst-case frame against this number at compile time. Raising it here
/// without re-deriving there is the drift this exists to stop.
///
/// DEVIATION from `HTTPD_DEFAULT_CONFIG`'s 4096, and NOT a guess: 10240 is
/// esp_https_server's own default, because the handshake runs on the server
/// task and mbedtls' ECDHE/ASN.1 frames do not fit in 4 KB.
///
/// RAISED to 14336 for the persistence tier, with the arithmetic rather than
/// by feel. A stored record decodes to a ~1 KB `Entry`, and the largest frame
/// in the image — `net::program::post_impl`, measured at 4288 B against the
/// built ELF — now performs the history write inside itself. 4288 + ~1.4 KB
/// for that write + 4096 reserved for the mbedtls record layer that sits on
/// top of every `respond` is ~9.8 KB, which fitted 10240 only by ~450 bytes;
/// level-1 interrupts also run on the interrupted task's stack, so that is not
/// a margin. It cost a reboot to find that out, and a reboot drops the relay.
///
/// RAISED AGAIN to 20480, and this time because 14336 was ALSO not a margin —
/// it was a number that happened to fit. `POST /api/programs/history/{id}/resume`
/// overflowed it intermittently: `history_load` holds a decoded `Entry` (~1 KB)
/// while `store::find` has one or two more live from its own returns, hands
/// `install` a `Program` (~900 B) BY VALUE, and then renders a full program
/// snapshot into `net::program::STATE_BUF` (~2.1 KB) on top of all of it,
/// underneath mbedtls. That is ~10 KB of the firmware's own frames before the
/// TLS record layer, and mbedtls' own usage varies with the record size and
/// the handshake — which is exactly why it failed in some runs and not others.
/// An INTERMITTENT stack overflow is the worst version of this bug: it reboots
/// the device, which drops the relay mid-run, and it does so unpredictably.
/// 20480 puts ~10 KB between the measured frames and the ceiling.
///
/// The three modules that build large frames on this task —
/// `net::program`, `net::records` and `net::session`'s WebSocket push — all
/// assert against this number at COMPILE time, so the next thing that grows
/// fails the build instead of the device. Those assertions did NOT catch this
/// one, and that is worth stating: they count the frames this code declares,
/// not the ones the compiler actually lays out or the ones mbedtls adds
/// underneath. They are a floor, not a proof.
pub const HTTPD_STACK_BYTES: u32 = 20_480;

/// GET / — the discovery banner.
///
/// SAFETY: `req` is a live IDF request pointer valid for the call; the two
/// helpers only read it and the byte slices outlive the call (both are
/// `'static`). Returning ESP_OK tells IDF the response is complete.
unsafe extern "C" fn banner_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    banner_impl(req)
}

fn banner_impl(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    if crate::net::api::reject_unexpected_body(req) {
        return sys::ESP_OK;
    }
    crate::net::api::respond(req, c"200 OK", BANNER)
}


/// Maximum inbound WebSocket frame we will read, in bytes.
///
/// A HARD BOUND, not a tuning knob: the frame is read into a fixed stack
/// buffer, so an oversized frame is refused rather than allocated for. The app
/// only ever sends small control JSON on this socket; anything larger is
/// either a mistake or an attack.
const MAX_WS_FRAME: usize = 512;

fn ws_handshake(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    if crate::net::api::reject_unexpected_body(req) {
        return sys::ESP_OK;
    }
    // Greet the client so a connection that opens but never receives anything
    // is distinguishable from a healthy idle one.
    let hello = br#"{"type":"connection","connected":true}"#;
    let mut frame: sys::httpd_ws_frame_t = zeroed();
    frame.type_ = sys::httpd_ws_type_t_HTTPD_WS_TYPE_TEXT;
    frame.payload = hello.as_ptr() as *mut u8;
    frame.len = hello.len();
    // SAFETY: `req` is live for the callback and `frame` borrows the static
    // `hello` payload for this synchronous send only.
    unsafe { sys::httpd_ws_send_frame(req, &mut frame) }
}

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
        return ws_handshake(req);
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
    // SAFETY: `httpd_ssl_config_t` (and the `httpd_config_t` it embeds) is a
    // bindgen POD of integers, pointers and function pointers; all-zero is a
    // valid initial value and every field the server requires is set explicitly
    // below. Zero is also the correct default for the fields deliberately left
    // alone — `cacert_pem`/NULL (no client-cert auth), `user_cb`/NULL,
    // `alpn_protos`/NULL, `ecdsa_curve`/SECP256R1.
    unsafe { core::mem::zeroed() }
}

/// Start the HTTPS server and register the banner route.
///
/// `id` must be `'static`: `httpd_ssl_start` stores the PEM POINTERS in the
/// server's transport context and dereferences them on every handshake, long
/// after this frame is gone.
///
/// Returns the handle so later slices can register more routes on the same
/// server rather than starting a second one.
pub fn start(id: &'static Identity) -> Result<sys::httpd_handle_t, sys::esp_err_t> {
    // HTTPD_SSL_CONFIG_DEFAULT() — esp_https_server.h:169. Deviations marked.
    let mut ssl: sys::httpd_ssl_config_t = zeroed();

    // ...whose `.httpd` member is HTTPD_DEFAULT_CONFIG() with two of its own
    // overrides (stack 10240, sockets 4), so the inner struct is filled in
    // exactly as it was for plain HTTP except where TLS forces a change.
    let cfg = &mut ssl.httpd;
    cfg.task_priority = 5; // tskIDLE_PRIORITY + 5
    cfg.stack_size = HTTPD_STACK_BYTES as usize; // see the const's derivation
    cfg.core_id = i32::MAX; // tskNO_AFFINITY
    cfg.task_caps = (sys::MALLOC_CAP_INTERNAL | sys::MALLOC_CAP_8BIT) as u32;
    cfg.max_req_hdr_len = sys::CONFIG_HTTPD_MAX_REQ_HDR_LEN as usize;
    cfg.max_uri_len = sys::CONFIG_HTTPD_MAX_URI_LEN as usize;
    // server_port is NOT set here: httpd_ssl_start overwrites it with
    // `port_secure` (https_server.c:422). Setting it would be a lie in the
    // source about what the server listens on.
    cfg.ctrl_port = 32769; // ESP_HTTPD_DEF_CTRL_PORT + 1, per the SSL default
    // DEVIATION from Slice 1's 7 — see the module header: TLS sessions cost
    // ~40 KB each, and 4 is IDF's own SSL default for that reason.
    cfg.max_open_sockets = 4;
    // 2 (banner, ws) + 3 control + 10 program + 8 record + 11 profile
    // + 4 hrm + 5 coach = 43, and registration fails with
    // ESP_ERR_HTTPD_HANDLERS_FULL the moment the table is full.
    //
    // RAISED FROM 40, AND IT WAS ALREADY FULL. The coach tier's five routes
    // could not register at all: the log said
    // `httpd_register_uri_handler: no slots left`, `main` printed
    // `coach register failed (45057)` — and every other route kept working, so
    // the device looked completely healthy while `POST /api/chat` answered
    // `404 Nothing matches the given URI`. That is the failure mode this
    // comment already warned about, reproduced by the very next tier to be
    // added, which is the argument for headroom rather than an exact size.
    //
    // 56 is not "40 plus what we needed". A handler slot is a small struct in
    // the server's own allocation, so the cost of being generous is bytes, and
    // the cost of being exact is a route that silently does not exist.
    cfg.max_uri_handlers = 56;
    // DEVIATION from the IDF default's exact-match `httpd_uri_match_wildcard`
    // is REQUIRED, not a convenience: `/api/workouts/{id}` and
    // `/api/programs/history/{id}/load` carry the record id IN THE PATH, which
    // an exact matcher cannot express at all.
    //
    // It changes nothing for the routes already registered. The IDF matcher
    // only relaxes templates that END in `*` or `?`; every other template is
    // still compared for exact equality including length, so `/api/status`
    // cannot start answering `/api/statusXYZ`. Handlers are walked in
    // REGISTRATION ORDER and the first match wins, which is why each module's
    // table lists its exact routes before its wildcards.
    cfg.uri_match_fn = Some(sys::httpd_uri_match_wildcard);
    cfg.max_resp_headers = 8;
    cfg.backlog_conn = 5;
    // DEVIATION from the SSL default's `true`: purging the least-recently-used
    // socket to admit a new one would evict the app's own status WebSocket,
    // which is exactly the bug the C++ tier shipped. Refuse instead.
    cfg.lru_purge_enable = false;
    // DEVIATION from the IDF default's 5 s, and it is the OTHER HALF of the
    // handshake bound below — not an independent knob.
    //
    // `httpd_accept_conn` (httpd_main.c:85) sets SO_RCVTIMEO/SO_SNDTIMEO from
    // these on the new fd and THEN runs the TLS handshake through `open_fn`.
    // `esp_mbedtls_server_session_create` only tests its own elapsed budget
    // when `mbedtls_ssl_handshake` returns WANT_READ, so the worker is parked
    // for a whole SO_RCVTIMEO before the handshake budget is even consulted:
    // measured, `tls_handshake_timeout_ms = 1000` with a 5 s recv timeout
    // still blocked every other client for 5.3 s. The real outage is the
    // handshake budget ROUNDED UP to this granularity, so both must be 1 s.
    //
    // 1 is the smallest value the field can express (`tv_sec`, whole seconds).
    // It is a PER-RECV budget on a socket that httpd only reads when `select`
    // has already said it is readable, so an idle WebSocket is unaffected and
    // a LAN client never comes close; a peer that dribbles bytes is exactly
    // what it is meant to cut off.
    cfg.recv_wait_timeout = 1;
    cfg.send_wait_timeout = 1;

    ssl.servercert = id.cert().as_ptr();
    ssl.servercert_len = id.cert().len();
    ssl.prvtkey_pem = id.key().as_ptr();
    ssl.prvtkey_len = id.key().len();
    ssl.transport_mode = sys::httpd_ssl_transport_mode_t_HTTPD_SSL_TRANSPORT_SECURE;
    ssl.port_secure = PORT; // DEVIATION: 443 -> 8000, the mDNS-advertised port
    // No plaintext listener exists in SECURE mode; this field is inert here and
    // is set only so the struct never reads as "port 0 means something".
    ssl.port_insecure = PORT;
    // A handshake must not be able to hold the single worker open-endedly.
    //
    // `esp_https_server` runs `esp_tls_create_server_session` inside
    // `httpd_accept_conn`, ON THE HTTPD WORKER. A peer that completes the TCP
    // handshake and then sends NOTHING parks that worker for the whole budget
    // and NO other client is served meanwhile — including the app's Stop
    // button, with the belt moving. MEASURED at the IDF default (10 s, which
    // is what 0 means): ONE idle socket pushed `POST /api/program/stop` from
    // 0.25 s to 15.25 s, ending in ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT; the
    // step is at one socket, not at `max_open_sockets`, so it is the handshake
    // budget and not socket exhaustion. Anything on the LAN — a port scanner,
    // a captive-portal probe, a half-open client — could hold it indefinitely
    // by reconnecting.
    //
    // THE BUDGET IS NOT A LIMIT ON A HEALTHY HANDSHAKE, and reading it as one
    // is what makes it look like a dangerous number to shrink. The socket is
    // BLOCKING with SO_RCVTIMEO = `recv_wait_timeout`, so
    // `mbedtls_ssl_handshake` returns WANT_READ only when a whole second of
    // SILENCE has already elapsed — and this budget is consulted ONLY on that
    // return (esp_tls_mbedtls.c:1147). A client that answers within the recv
    // timeout never reaches the check at all, however slow the server's own
    // crypto is. What the value selects is simply HOW MANY 1 s silences a peer
    // is granted before the worker is taken back: anything below one recv
    // timeout means ONE. Measured end to end: 10 s -> 15.25 s Stop latency,
    // 1000 ms -> 2.3 s (1000 is not > 1000, so silence is granted twice),
    // 500 ms -> ~1.2 s, which is one silence plus the real request.
    ssl.tls_handshake_timeout_ms = 500;

    let mut handle: sys::httpd_handle_t = core::ptr::null_mut();
    // SAFETY: `ssl` is a live exclusive borrow — `httpd_ssl_start` mutates the
    // config in place (it installs its own `open_fn` and rewrites
    // `httpd.server_port`) and does not retain the struct itself. It DOES
    // retain the certificate/key pointers, which is why `id` is `'static`.
    // `handle` is written exactly once on success.
    let err = unsafe { sys::httpd_ssl_start(&mut handle, &mut ssl) };
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
