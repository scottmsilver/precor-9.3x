//! Pushing state down `/ws` — the socket the app feeds its whole live UI from.
//!
//! # Why this exists
//!
//! The `/ws` handler completed the RFC6455 handshake, sent one
//! `{"type":"connection","connected":true}` greeting, and then never spoke
//! again. MEASURED against a booted device: a real WebSocket session spanning a
//! complete running program collected exactly ONE frame in 25 s.
//!
//! That is fatal rather than cosmetic, because the Android app DISCARDS every
//! program-endpoint response body — `startProgram`, `stopProgram`,
//! `pauseProgram`, `skipInterval`, `prevInterval`, `extendInterval` and
//! `quickStart` are all bare `runCatching { api.X() }` with no `onSuccess`, and
//! `_status`/`_session`/`_program` are mutated ONLY in `handleMessage()`, which
//! is fed by `webSocket.messages`. So without a push the belt moves and the
//! screen does not: the hero timer stays at 0:00, the interval never advances,
//! the pause button never reflects a paused program, and elapsed/distance/
//! vert/calories stay at 0.00 for the whole workout.
//!
//! # EVERY FRAME IS SENT ON THE HTTPD TASK, AND THE FIRST VERSION WAS NOT
//!
//! The first version sent frames straight from the session recorder, on the
//! grounds that the app never sends anything on `/ws` so no read could race a
//! write. That reasoning was too narrow and the QEMU suite caught it: it is not
//! the app's frames that race, it is the SERVER'S OWN session teardown.
//! `httpd_ws_send_frame_async` resolves the socket to a `struct sock_db` and
//! calls its `send_fn`, which for TLS is `esp_tls_conn_write` on that session's
//! mbedtls context — while the httpd worker may be closing that same session
//! and freeing that same context, or reusing the descriptor for a new
//! connection. IDF's own documentation says the call is not thread-safe with
//! respect to the server. Symptoms observed: `esp-tls-mbedtls: write error`,
//! then read errors, then the socket closed under a client that was reading it
//! perfectly happily — intermittently, which is the worst way to be wrong.
//!
//! So the recorder now only ASKS. [`request_push`] queues one work item onto
//! the server's own control socket (`httpd_queue_work`, a single non-blocking
//! `sendto`), and the callback runs on the httpd task, serialised with every
//! request and every teardown. There is no second writer.
//!
//! # The worker-occupancy bound, because this now runs where requests run
//!
//! Moving the send onto the single worker means a stalled client's frame is in
//! front of the Stop button, which is the same class of defect as the dribbling
//! writer `net::api::Deadline` exists to stop. It is bounded three ways:
//!
//! * one `send_fn` call is bounded by `SO_SNDTIMEO = send_wait_timeout` (1 s,
//!   see `net::http`) — mbedtls returns the WANT_WRITE up rather than retrying;
//! * [`PUSH_BUDGET`] abandons the remaining frames once the push as a whole has
//!   run long, so a tick costs at most one blocked call plus change;
//! * a socket whose frame FAILED is closed. A client that cannot absorb ~200
//!   bytes in a second is gone, and leaving it in the table would cost that
//!   second again every second, forever.
//!
//! # A dropped tick is a dropped frame, deliberately
//!
//! `httpd_queue_work` can fail (the control socket's queue is full because the
//! worker is busy). Nothing is retried and nothing is buffered: the next tick
//! is 1 s away and carries the newer state anyway. A queue of stale frames
//! would be memory that grows with how busy the server is, which is the exact
//! shape this firmware exists not to have.

use crate::context::lock;
use esp_idf_sys as sys;
use safety_core::units::Micros;
use std::sync::Mutex;

/// The most sockets the server will ever hold open — `net::http` sets
/// `max_open_sockets = 4`. Passed to IDF as the capacity of the array below, so
/// it cannot be overflowed even if the two ever disagree.
const MAX_CLIENTS: usize = 4;

/// How long one push may occupy the single httpd worker.
///
/// NOT a per-send timeout — that is `send_wait_timeout`, and one blocked call
/// can still consume a whole second of it. This bounds how many such calls a
/// tick may make: after it, the rest of the frames are dropped and the socket
/// that stalled is closed. The Stop button is behind this worker.
const PUSH_BUDGET: Micros = Micros::from_millis(500);

/// The running server, as an integer.
///
/// `httpd_handle_t` is a raw pointer and therefore not `Send`; the handle is an
/// opaque IDF token that is only ever passed back to IDF, so it is stored as a
/// `usize` rather than wrapped in an `unsafe impl Send` that would claim
/// something stronger than is true. 0 means "no server".
static SERVER: Mutex<usize> = Mutex::new(0);

/// Publish the started server so a task that is not the worker can ask it to
/// push.
pub fn set_server(h: sys::httpd_handle_t) {
    *lock(&SERVER) = h as usize;
}

fn server() -> sys::httpd_handle_t {
    *lock(&SERVER) as sys::httpd_handle_t
}

/// Ask the server to push a frame set. Safe to call from any task.
///
/// Returns nothing on purpose: a failure here means the worker was busy, the
/// next tick is a second away, and there is nothing a caller could usefully do
/// about it.
pub fn request_push() {
    let h = server();
    if h.is_null() {
        return;
    }
    // SAFETY: `h` is the live server handle published by `set_server`. IDF
    // copies the function pointer and the argument into a control message; the
    // function is `'static` and the argument is null, so nothing of ours has to
    // outlive the call. `httpd_queue_work` is documented as callable from any
    // task and does one non-blocking `sendto` on the server's control socket.
    unsafe {
        sys::httpd_queue_work(h, Some(push_work), core::ptr::null_mut());
    }
}

/// The queued work item. RUNS ON THE HTTPD TASK — that is the entire point.
///
/// SAFETY: invoked by IDF on the server task with the null argument
/// `request_push` supplied; it dereferences nothing and retains nothing.
unsafe extern "C" fn push_work(_arg: *mut core::ffi::c_void) {
    crate::net::session::push_frames();
}

/// Fill `out` with the open WebSocket descriptors; returns how many.
///
/// Only ever called from the httpd task (via [`push_work`]), so the session
/// table it reads cannot change underneath it.
fn ws_clients(out: &mut [core::ffi::c_int; MAX_CLIENTS]) -> usize {
    let h = server();
    if h.is_null() {
        return 0;
    }
    let mut n: usize = MAX_CLIENTS;
    // SAFETY: `h` is the live server handle. `n` is in/out — IDF reads the
    // capacity and writes back the count, and never writes more descriptors
    // than the capacity it was given, which is exactly `out`'s length. Both
    // pointers are live exclusive borrows for the call.
    let rc = unsafe { sys::httpd_get_client_list(h, &mut n, out.as_mut_ptr()) };
    if rc != sys::ESP_OK || n > MAX_CLIENTS {
        return 0;
    }
    // Compact the WebSocket descriptors to the front. A plain HTTP client on
    // the same server must never be sent a WS frame — it would arrive as
    // garbage in the middle of whatever response it was reading.
    let mut k = 0usize;
    for i in 0..n {
        let fd = out[i];
        // SAFETY: scalars only — a live server handle and an integer
        // descriptor. IDF answers from its own session table and retains
        // nothing.
        let kind = unsafe { sys::httpd_ws_get_fd_info(h, fd) };
        if kind == sys::httpd_ws_client_info_t_HTTPD_WS_CLIENT_WEBSOCKET {
            out[k] = fd;
            k += 1;
        }
    }
    k
}

/// Whether any WebSocket is open. Httpd-task only, like everything else here.
///
/// Exists so [`crate::net::session::push_frames`] can skip RENDERING when
/// nobody is listening: the program frame alone is a ~2 KB stack buffer and a
/// full serialisation of the workout.
pub fn any_client() -> bool {
    let mut fds = [0; MAX_CLIENTS];
    ws_clients(&mut fds) > 0
}

/// Send every frame to every open WebSocket. ONE enumeration, ONE budget.
///
/// Takes the whole frame set rather than being called once per frame so the
/// budget below covers the push as a whole; three independent 500 ms budgets
/// would be a 1.5 s bound wearing a 500 ms label.
pub fn send_all(frames: &[&[u8]]) {
    let h = server();
    if h.is_null() {
        return;
    }
    let mut fds = [0; MAX_CLIENTS];
    let n = ws_clients(&mut fds);
    let started = crate::CTX.clock.now();
    for fd in fds.iter().take(n) {
        // Out of the WHOLE push's budget: stop, and blame nobody. A socket that
        // was never written to did nothing wrong, and closing it because an
        // earlier one was slow would disconnect a healthy tablet.
        if crate::CTX.clock.now() - started > PUSH_BUDGET {
            break;
        }
        let began = crate::CTX.clock.now();
        let mut alive = true;
        for payload in frames {
            // SAFETY: `frame` is a bindgen POD; all-zero is a valid initial
            // value and every field IDF reads is set below. `payload` outlives
            // the call and IDF only READS it (the `*mut` is the C API's
            // signature, not a claim of mutation). `h` and `fd` are live IDF
            // tokens, and this runs on the httpd task, so the session `fd`
            // names cannot be closed or reused underneath the call.
            let rc = unsafe {
                let mut frame: sys::httpd_ws_frame_t = core::mem::zeroed();
                frame.type_ = sys::httpd_ws_type_t_HTTPD_WS_TYPE_TEXT;
                frame.payload = payload.as_ptr() as *mut u8;
                frame.len = payload.len();
                sys::httpd_ws_send_frame_async(h, *fd, &mut frame)
            };
            if rc != sys::ESP_OK {
                alive = false;
                break;
            }
            // A SLOW SUCCESS IS ALSO A FAILURE HERE, and reading it as health
            // was a real hole: `httpd_ws_send_frame_async` is SYNCHRONOUS
            // despite its name and makes two `send_fn` calls, each able to
            // consume a whole `send_wait_timeout`. A client that takes a second
            // to absorb ~200 bytes but does absorb it would have been kept, and
            // would have cost that second again on every tick, in front of
            // every request — including Stop.
            if crate::CTX.clock.now() - began > PUSH_BUDGET {
                alive = false;
                break;
            }
        }
        if !alive {
            // SAFETY: scalars only; IDF queues the close on its own control
            // socket and reaps the session itself.
            //
            // THE QUEUED CLOSE CANNOT HIT A REUSED DESCRIPTOR, and that is a
            // property of `httpd_server`'s loop rather than luck: it tests the
            // control fd FIRST and RETURNS, so every queued message is drained
            // before the listener is ever accepted from again. The descriptor
            // this names is still the session it named when we read it.
            unsafe {
                sys::httpd_sess_trigger_close(h, *fd);
            }
        }
    }
}
