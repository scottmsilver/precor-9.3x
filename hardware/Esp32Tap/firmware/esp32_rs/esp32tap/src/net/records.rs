//! Slice 5 — the endpoints that make the device keep its own data.
//!
//! `/api/programs/history` (+ `/{id}/load`, `/{id}/resume`), `/api/workouts`
//! (list, save, rename, delete, load) and `/api/runs`, all served straight out
//! of the flash rings in [`crate::net::store`].
//!
//! # Constant memory, per request and per stored volume
//!
//! Two mechanisms, and neither is a convention:
//!
//! * A handler leases ONE `reqbudget` slot and reads records into it, one at a
//!   time. The slot is the same fixed pool `/api/speed` uses, so N concurrent
//!   list requests cost N slots out of 4 and the (N+1)th is refused 503 — it
//!   does not allocate. Nothing parsed outlives the record it came from.
//! * A list response is CHUNKED ([`ChunkSink`]). Twenty history entries
//!   serialise to ~50 KB, which no device buffer could hold; the sink renders
//!   into a fixed 512-byte buffer and flushes it, so the response body's size
//!   has no relationship to memory at all.
//!
//! That is the property whose absence let ~15 unauthenticated requests exhaust
//! the C++ tier's heap and reboot it mid-run, dropping the relay.
//!
//! # Client strings are bounded at the door
//!
//! Ids are refused above [`record::MAX_ID`], names truncate at
//! `MAX_PROGRAM_NAME`, prompts at `record::MAX_PROMPT`, and a body larger than
//! one slot is refused 413 before a byte is parsed. The C++ tier had unbounded
//! name and prompt fields.
//!
//! TWO OF THOSE BOUNDS ARE VISIBLE TO A CLIENT AUTHOR AND ARE STATED HERE
//! RATHER THAN DISCOVERED:
//!
//! * `POST /api/workouts` refuses a body over one 2048-byte slot with 413,
//!   before parsing. The Pi's `SaveWorkoutRequest` accepts a 5000-character
//!   `prompt`; a client that fills it gets a 413 that Retrofit turns into an
//!   `HttpException` and the app renders as "Failed to save workout" with no
//!   indication why. The stored field is bounded to `record::MAX_PROMPT`
//!   anyway and truncates rather than refusing, so the 413 comes purely from
//!   the slot size. The unchanged Android app cannot reach it — its only
//!   `saveWorkout` call site sends `{"history_id":…}`, ~20 bytes.
//! * A `\uXXXX`-escaped name is DESTROYED, one `_` per escape, by
//!   `program_core::json`'s escape handling (`extract_str` here does the same).
//!   That is a deliberate trade — "a label is not worth a decoder" — and it is
//!   invisible to the Android app, whose kotlinx-serialization emits raw UTF-8
//!   and never escapes. It is NOT invisible to the iOS client, to curl, or to
//!   several JS serializers, all of which escape by default: for them a
//!   non-ASCII workout name is annihilated rather than truncated.
//!
//! # Types, not just fields
//!
//! Every field in `HistoryEntry`, `SavedWorkout` and `RunRecord` — the three
//! models THIS module emits — has a kotlinx default, so an OMITTED field passes
//! silently and only a WRONG TYPE breaks a screen. The two that can break one
//! are object-shaped: `program` and `last_run`. Both are emitted whole or as
//! `null`, never as a scalar.
//!
//! THAT RULE IS ABOUT THESE THREE MODELS ONLY, and this header used to state it
//! as though it were universal. It is FALSE of `Program` (`name`, `intervals`),
//! `Interval` (all four), `ProgramMessage`, `StatusMessage` and
//! `SessionMessage`, every one of which declares fields with no default —
//! `coerceInputValues` rewrites an explicit null into a default that EXISTS, it
//! cannot invent one, so an omission there throws MissingFieldException. A
//! reader who carried the old wording over to `net::api` or `net::session`
//! would believe dropping a field from a status or program body was safe. It is
//! not; see the derivations at `net::api::format_status` and
//! `program_core::json::write_state_with_lead`. `program_core::record`'s host
//! tests pin the shapes this module emits.

use crate::context::lock;
use crate::net::api::{respond, MAX_CMD_BODY};
use crate::net::store::{self, Which};
use esp_idf_sys as sys;
use program_core::record::{self, Entry, Source};
use program_core::{json, Program};
use safety_core::FixedStr;

/// Bytes rendered before a flush. Small on purpose: it is a STACK buffer on
/// the httpd task, which also carries an mbedtls record layer, and the whole
/// point of chunking is that this number is unrelated to the response size.
const CHUNK: usize = 512;

/// Worst-case stack this module puts on the httpd task, in bytes.
///
/// THE LIST IS NOT THE LARGEST FRAME, and asserting on it was measuring the
/// wrong handler. The mutation paths are: `workout_save` holds the parsed
/// `Option<Program>` from the body AND the `Entry` it builds AND the `Entry`
/// the store hands back, then renders through a `ChunkSink`. The history list
/// holds one `Entry`, the saved index and a sink. This counts the mutation
/// path, which dominates, plus slack for call and formatting frames the
/// compiler adds.
const WORST_FRAME_BYTES: usize = core::mem::size_of::<Option<Program>>()
    + 2 * core::mem::size_of::<Entry>()
    + core::mem::size_of::<SavedIndex>()
    + CHUNK
    + 1024;

const _: () = assert!(
    WORST_FRAME_BYTES + 4096 < crate::net::http::HTTPD_STACK_BYTES as usize,
    "net::records' worst-case frame plus mbedtls headroom no longer fits the \
     httpd task stack — shrink CHUNK or render entries through a reqbudget slot"
);

/// Worst-case stack [`record_loaded`] adds ON TOP of the caller's frame.
///
/// It is called from `net::program::post_impl`, which is already the largest
/// frame in the image, so this is not a separate budget — it is an addition to
/// that one, and `net::program` asserts the sum. THE FIRST VERSION DID NOT
/// COUNT IT and the device rebooted mid-test: the history write nested a
/// `find_by_name` that decoded a whole `Entry` per slot, so three ~1 KB
/// programs were live at once on the httpd task's 10 KB stack. The scan now
/// carries a `Head`; this constant is what keeps the arithmetic honest.
pub(crate) const HISTORY_WRITE_FRAME_BYTES: usize =
    core::mem::size_of::<Entry>() + core::mem::size_of::<record::Head>() + 256;

// ---------------------------------------------------------------------------
// Chunked output.
// ---------------------------------------------------------------------------

/// A `core::fmt::Write` that flushes to the client every [`CHUNK`] bytes.
///
/// OVERFLOW IS IMPOSSIBLE RATHER THAN CHECKED: a write that does not fit
/// flushes first, so the buffer bounds the flush size and nothing else. A
/// transport failure is recorded and every later write becomes a no-op, so a
/// broken connection cannot turn into a partial JSON document that a client
/// would try to parse.
struct ChunkSink {
    req: *mut sys::httpd_req_t,
    buf: [u8; CHUNK],
    len: usize,
    failed: bool,
}

impl ChunkSink {
    fn new(req: *mut sys::httpd_req_t) -> ChunkSink {
        ChunkSink {
            req,
            buf: [0u8; CHUNK],
            len: 0,
            failed: false,
        }
    }

    fn flush(&mut self) {
        if self.failed || self.len == 0 {
            return;
        }
        // SAFETY: `req` is live for the handler call; the buffer is a live
        // borrow of exactly `len` bytes that IDF copies out before returning.
        let rc = unsafe {
            sys::httpd_resp_send_chunk(
                self.req,
                self.buf.as_ptr() as *const core::ffi::c_char,
                self.len as isize,
            )
        };
        self.len = 0;
        if rc != sys::ESP_OK {
            self.failed = true;
        }
    }

    /// Flush what is left and terminate the chunked response.
    fn finish(mut self) -> sys::esp_err_t {
        self.flush();
        if self.failed {
            return sys::ESP_FAIL;
        }
        // SAFETY: `req` is live; a zero-length chunk is IDF's documented
        // end-of-response marker and reads no buffer.
        unsafe { sys::httpd_resp_send_chunk(self.req, core::ptr::null(), 0) }
    }
}

impl core::fmt::Write for ChunkSink {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        if self.failed {
            return Err(core::fmt::Error);
        }
        for part in s.as_bytes().chunks(CHUNK) {
            if self.len + part.len() > CHUNK {
                self.flush();
                if self.failed {
                    return Err(core::fmt::Error);
                }
            }
            self.buf[self.len..self.len + part.len()].copy_from_slice(part);
            self.len += part.len();
        }
        Ok(())
    }
}

/// Begin a 200 chunked JSON response.
fn begin_json(req: *mut sys::httpd_req_t) {
    // SAFETY: `req` is live for the call; both arguments are `'static` C
    // strings IDF copies or borrows only for the duration.
    unsafe {
        sys::httpd_resp_set_status(req, c"200 OK".as_ptr());
        sys::httpd_resp_set_type(req, c"application/json".as_ptr());
    }
}

// ---------------------------------------------------------------------------
// URI parsing. Ids arrive in the path, so this is a trust boundary.
// ---------------------------------------------------------------------------

/// The request path, without any query string.
///
/// SAFETY: `req` is live for the call and `uri` is IDF's own NUL-terminated
/// buffer inside it, bounded by `max_uri_len`; the returned borrow lives only
/// as long as the caller's use of `req`.
unsafe fn uri_of(req: &sys::httpd_req_t) -> &str {
    // `uri` is an INLINE array in `httpd_req_t` (CONFIG_HTTPD_MAX_URI_LEN+1
    // bytes), not a pointer, so the borrow is of the request struct itself.
    let bytes = core::ffi::CStr::from_ptr(req.uri.as_ptr()).to_bytes();
    let end = bytes.iter().position(|&b| b == b'?').unwrap_or(bytes.len());
    core::str::from_utf8(&bytes[..end]).unwrap_or("")
}

/// Split `<prefix>/<id>[/<action>]` into its two parts.
///
/// An id longer than a record id is REFUSED here rather than truncated: a
/// truncated id would silently address a DIFFERENT record, which is the worst
/// possible failure for a delete.
fn split_id_action<'a>(uri: &'a str, prefix: &str) -> Option<(&'a str, &'a str)> {
    let rest = uri.strip_prefix(prefix)?;
    let (id, action) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i + 1..]),
        None => (rest, ""),
    };
    if id.is_empty() || id.len() > record::MAX_ID {
        return None;
    }
    Some((id, action))
}

// ---------------------------------------------------------------------------
// Bodies.
// ---------------------------------------------------------------------------

/// Copy a JSON string field's value out of a body, sanitising it.
///
/// The SAME sanitisation `program_core::json` applies to names on the way in
/// (`"`, `\` and control bytes become `_`), for the same reason: every stored
/// string is then already safe to emit verbatim, so the serialiser has no
/// escape path that could be got wrong.
///
/// A SCANNER, NOT A PARSER, and the limit is stated rather than discovered: it
/// takes the FIRST `"key":` anywhere in the body, so a nested
/// `{"meta":{"name":"x"},"name":"y"}` yields `x`. It is the same technique
/// `/api/speed`'s number scanner already uses, and a second JSON parser on the
/// device would be more code than the ambiguity is worth. What bounds the
/// damage is that the value is sanitised and length-capped either way, so the
/// worst outcome is the wrong LABEL — never injected JSON, never an overrun,
/// and never a different record (ids come from the path, not the body).
pub(crate) fn extract_str<const N: usize>(body: &[u8], key: &str, out: &mut FixedStr<N>) -> bool {
    let mut pat: FixedStr<32> = FixedStr::new();
    pat.push_byte(b'"');
    pat.push_str(key);
    pat.push_byte(b'"');
    let Some(pos) = body
        .windows(pat.len())
        .position(|w| w == pat.as_bytes())
    else {
        return false;
    };
    // A COLON IS REQUIRED. Without it `not-json "name" "evil"` matched, which
    // is not a document this endpoint should accept anything from.
    let mut i = pos + pat.len();
    while i < body.len() && body[i] == b' ' {
        i += 1;
    }
    if i >= body.len() || body[i] != b':' {
        return false;
    }
    i += 1;
    while i < body.len() && body[i] == b' ' {
        i += 1;
    }
    if i >= body.len() || body[i] != b'"' {
        return false;
    }
    i += 1;
    out.clear();
    while i < body.len() && body[i] != b'"' {
        let c = body[i];
        // A backslash escape ends here: the value is a label, and `_` is
        // total. Consuming the escaped byte prevents `\"` from terminating.
        if c == b'\\' {
            out.push_byte(b'_');
            i += 2;
            continue;
        }
        out.push_byte(if c < 0x20 { b'_' } else { c });
        i += 1;
    }
    i < body.len()
}

/// Read a body of up to one request slot, admitted before anything is read.
///
/// Returns the length read into `lease`, or `None` when the request has
/// already been answered.
fn read_slot_body(req: *mut sys::httpd_req_t, lease: &mut reqbudget::Lease) -> Option<usize> {
    // SAFETY: reading a scalar field of a live request.
    let declared = unsafe { (*req).content_len };
    if declared == 0 {
        return Some(0);
    }
    if declared > lease.capacity() {
        respond(
            req,
            c"413 Payload Too Large",
            br#"{"ok":false,"error":"body too large"}"#,
        );
        return None;
    }
    // Bounded end to end, not per recv — see `net::api::Deadline`.
    let deadline = crate::net::api::Deadline::start();
    let mut got = 0usize;
    while got < declared {
        if deadline.expired() {
            crate::net::api::abandon_body(req);
            return None;
        }
        let buf = lease.buf();
        // SAFETY: `buf` is a live exclusive borrow of a slot with room for
        // `declared` bytes; IDF writes at most the length we pass, at an
        // offset inside it.
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
            return None;
        }
        got += n as usize;
    }
    Some(got)
}

/// Lease a slot to read records into, or answer 503.
///
/// A GET has no body, but it still needs a buffer — and taking it from the
/// SAME fixed pool is the point: the number of requests that can be reading
/// flash at once is the number of slots, and the rest are refused rather than
/// served from memory that does not exist.
fn scratch(req: *mut sys::httpd_req_t) -> Option<reqbudget::Lease> {
    match reqbudget::admit(reqbudget::SLOT_BYTES) {
        Ok(l) => Some(l),
        Err(_) => {
            respond(
                req,
                c"503 Service Unavailable",
                br#"{"ok":false,"error":"server busy"}"#,
            );
            None
        }
    }
}

fn no_store(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    respond(
        req,
        c"503 Service Unavailable",
        br#"{"ok":false,"error":"no storage"}"#,
    )
}

const NOT_FOUND: &[u8] = br#"{"ok":false,"error":"Not found"}"#;

// ---------------------------------------------------------------------------
// `saved` / `saved_workout_id` — the heart icon in the app's history list.
// ---------------------------------------------------------------------------

/// Where each saved workout lives, keyed by its program FINGERPRINT.
///
/// NOT BY NAME, and that is the fix for a defect the app made visible: keying
/// on `program.name` meant `PUT /api/workouts/{id}` desynced the history row's
/// heart icon (measured: `saved:true, saved_workout_id:"w1"` became
/// `saved:false, saved_workout_id:null` on a rename), the app's `handleToggleSave`
/// then took its else branch and POSTed `/api/workouts` again, and the store
/// ended up with TWO records for one program — the older one unreachable from
/// the row that created it, and another added by every subsequent rename.
/// `python/server.py` keys this on `_program_fingerprint`, whose docstring says
/// in as many words that it ignores the name, so a rename cannot break the link
/// there.
///
/// A HASH IS A FILTER, NOT AN ANSWER. A collision would mark an unsaved program
/// as saved and — far worse — hand the app the id of a DIFFERENT workout, which
/// its unsave button would then delete. So a hit is confirmed against the
/// stored bytes by [`record::entry_matches_program`]; the hash only keeps the
/// scan from being 20x20 record reads.
struct SavedIndex {
    n: usize,
    items: [(u64, u8); store::WORKOUT_SLOTS],
}

fn build_saved_index(st: &store::Stores, scratch: &mut [u8]) -> SavedIndex {
    let mut idx = SavedIndex {
        n: 0,
        items: [(0, 0); store::WORKOUT_SLOTS],
    };
    for n in 0..store::WORKOUT_SLOTS {
        if let Some(h) = st.head_at(Which::Workouts, n, scratch) {
            idx.items[idx.n] = (h.fp, n as u8);
            idx.n += 1;
        }
    }
    idx
}

/// The id of the saved workout holding this exact program, if there is one.
///
/// Reads the record's HEAD, not the record: the history list already holds one
/// decoded [`Entry`] live, and a second one is ~1 KB of the httpd task's stack.
fn saved_id_for(
    st: &store::Stores,
    idx: &SavedIndex,
    program: &Program,
    scratch: &mut [u8],
) -> Option<FixedStr<{ record::MAX_ID }>> {
    let want = record::fingerprint(program);
    for k in 0..idx.n {
        let (fp, pos) = idx.items[k];
        if fp != want {
            continue;
        }
        if let Some(id) = st.match_at(Which::Workouts, pos as usize, program, scratch) {
            return Some(id);
        }
    }
    None
}

/// The newest run per program fingerprint — `server.py::_last_run_by_fingerprint`.
///
/// Four slots, so this is four `Run` decodes (~100 bytes each, no `Program`
/// inside one) and no scan at render time. Built once per list request, exactly
/// as the Pi builds its dict once per request.
struct RunIndex {
    n: usize,
    items: [(u64, u8); store::RUN_SLOTS],
}

fn build_run_index(st: &store::Stores, scratch: &mut [u8]) -> RunIndex {
    let mut idx = RunIndex {
        n: 0,
        items: [(0, 0); store::RUN_SLOTS],
    };
    for n in 0..store::RUN_SLOTS {
        if let Some(r) = st.run_at(n, scratch) {
            // Ring order is newest-first, so the FIRST fingerprint seen wins —
            // the same rule the Pi's `by_fp` dict uses over its newest-first
            // rows.
            if idx.items[..idx.n].iter().any(|(fp, _)| *fp == r.fp) {
                continue;
            }
            idx.items[idx.n] = (r.fp, n as u8);
            idx.n += 1;
        }
    }
    idx
}

/// The newest run of this program, if the ring still holds one.
///
/// THE FINGERPRINT ALONE DECIDES HERE, unlike [`saved_id_for`], and the
/// asymmetry is deliberate rather than an oversight. A `Run` stores its
/// program's fingerprint, not its intervals — the record is ~120 bytes and
/// carrying a whole `Program` per run would cost more flash than the four run
/// slots are worth — so there is nothing to confirm a hit against. What bounds
/// the damage is the CONSEQUENCE: a 64-bit FNV collision between two of at most
/// four stored runs would put the wrong duration and distance in one
/// `last_run_text` LABEL. It cannot delete anything and it cannot mis-address a
/// record, which is exactly why the saved-workout join — whose id the app's
/// unsave button deletes — is confirmed exactly and this one is not.
fn last_run_for(
    st: &store::Stores,
    idx: &RunIndex,
    program: &Program,
    scratch: &mut [u8],
) -> Option<record::Run> {
    let want = record::fingerprint(program);
    for k in 0..idx.n {
        let (fp, pos) = idx.items[k];
        if fp == want {
            return st.run_at(pos as usize, scratch);
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Handlers.
// ---------------------------------------------------------------------------

const R_HISTORY: usize = 0;
const R_WORKOUTS: usize = 1;
const R_RUNS: usize = 2;

/// GET /api/programs/history, /api/workouts, /api/runs — the three lists.
///
/// SAFETY: `req` is live for the call. Reading `user_ctx` reads a scalar field
/// this module set at registration; nothing derived from `req` is retained.
unsafe extern "C" fn list_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    list_impl(req, (*req).user_ctx as usize)
}

/// THE STORE LOCK IS NEVER HELD ACROSS A NETWORK WRITE, and that is a SAFETY
/// property rather than a style preference. A chunk send blocks for up to
/// `send_wait_timeout` (1 s) per flush, and the session recorder is a
/// WDT-supervised task that takes the same lock: a client that stops reading
/// mid-list would have parked the recorder behind the httpd worker for seconds,
/// tripping its 2 s watchdog, rebooting the SoC and DROPPING THE RELAY mid-run.
/// So each record is read under a SHORT hold — one flash read — and the lock is
/// released before a byte is written to the socket.
///
/// The list is therefore not a transaction: a concurrent write between two
/// records could, in principle, be half-visible. That is the right trade. The
/// only concurrent writer is the session recorder updating ONE in-progress run
/// or ONE history entry's progress, the effect is a field one tick stale, and
/// the alternative is a watchdog reboot.
fn list_impl(req: *mut sys::httpd_req_t, kind: usize) -> sys::esp_err_t {
    use core::fmt::Write;
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    begin_json(req);
    let mut sink = ChunkSink::new(req);

    if store::with(|_| ()).is_none() {
        // No store: an EMPTY LIST, not an error. A device whose flash is
        // unusable still has to get the app past its lobby, and the app's list
        // screens handle empty; a 503 here would look like the whole server
        // was down.
        let _ = sink.write_str("[]");
        return sink.finish();
    }

    let _ = sink.write_char('[');
    let mut first = true;
    let sep = |sink: &mut ChunkSink, first: &mut bool| {
        if !*first {
            let _ = sink.write_char(',');
        }
        *first = false;
    };

    match kind {
        R_RUNS => {
            for n in 0..store::RUN_SLOTS {
                let r = store::with(|st| st.run_at(n, lease.buf())).flatten();
                if let Some(r) = r {
                    sep(&mut sink, &mut first);
                    let _ = record::write_run(&mut sink, &r);
                }
            }
        }
        R_WORKOUTS => {
            let runs = store::with(|st| build_run_index(st, lease.buf()));
            for n in 0..store::WORKOUT_SLOTS {
                let row = store::with(|st| {
                    let e = st.entry_at(Which::Workouts, n, lease.buf())?;
                    let run = runs
                        .as_ref()
                        .and_then(|r| last_run_for(st, r, &e.program, lease.buf()));
                    Some((e, run))
                })
                .flatten();
                if let Some((e, run)) = row {
                    sep(&mut sink, &mut first);
                    let _ = record::write_saved_workout(&mut sink, &e, run.as_ref());
                }
            }
        }
        _ => {
            let idx = store::with(|st| build_saved_index(st, lease.buf()));
            let runs = store::with(|st| build_run_index(st, lease.buf()));
            for n in 0..store::HISTORY_SLOTS {
                // Entry, saved-workout id AND last run resolved in ONE hold, so
                // the row a client sees is at least self-consistent.
                let row = store::with(|st| {
                    let e = st.entry_at(Which::History, n, lease.buf())?;
                    let saved = idx
                        .as_ref()
                        .and_then(|i| saved_id_for(st, i, &e.program, lease.buf()));
                    let run = runs
                        .as_ref()
                        .and_then(|r| last_run_for(st, r, &e.program, lease.buf()));
                    Some((e, saved, run))
                })
                .flatten();
                if let Some((e, saved, run)) = row {
                    sep(&mut sink, &mut first);
                    let _ = record::write_history_entry(
                        &mut sink,
                        &e,
                        saved.as_ref().map(|s| s.as_str()),
                        run.as_ref(),
                    );
                }
            }
        }
    }
    let _ = sink.write_char(']');
    sink.finish()
}

const V_HIST_LOAD: usize = 0;
const V_WORKOUT_SAVE: usize = 1;
const V_WORKOUT_ID: usize = 2;

/// Every POST/PUT/DELETE below; `user_ctx` selects which.
///
/// SAFETY: `req` is live for the call. Reading `user_ctx` and `method` reads
/// scalar fields; the URI borrow does not outlive the call.
unsafe extern "C" fn mutate_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let verb = (*req).user_ctx as usize;
    let method = (*req).method;
    // Borrowed FROM the request, so the compiler bounds the URI's lifetime
    // by the request's rather than letting a caller name `'static` for it.
    let uri = uri_of(&*req);
    mutate_impl(req, verb, method, uri)
}

fn mutate_impl(
    req: *mut sys::httpd_req_t,
    verb: usize,
    method: i32,
    uri: &str,
) -> sys::esp_err_t {
    // THE ACTION IS MATCHED EXACTLY. A wildcard route hands this function
    // everything under its prefix, so treating "not `resume`" as "load" made
    // `POST /api/programs/history/h1/delete` load a program and
    // `DELETE /api/workouts/w1/load` DELETE a workout. Anything not listed
    // here is 404, which is what a client that mistyped a URL should get.
    let not_found = |req| respond(req, c"404 Not Found", NOT_FOUND);
    match verb {
        V_HIST_LOAD => match split_id_action(uri, "/api/programs/history/") {
            Some((id, "load")) | Some((id, "")) => history_load(req, id, false),
            Some((id, "resume")) => history_load(req, id, true),
            _ => not_found(req),
        },
        V_WORKOUT_SAVE => workout_save(req),
        _ => match split_id_action(uri, "/api/workouts/") {
            Some((id, "")) if method == sys::http_method_HTTP_DELETE as i32 => {
                workout_delete(req, id)
            }
            Some((id, "")) if method == sys::http_method_HTTP_PUT as i32 => {
                workout_rename(req, id)
            }
            Some((id, "load")) if method == sys::http_method_HTTP_POST as i32 => {
                workout_load(req, id)
            }
            _ => not_found(req),
        },
    }
}

/// Load a program into the running state, stopping whatever was there.
///
/// THE BELT IS STOPPED FIRST, exactly as `POST /api/program/load` does and for
/// the same reason: leaving it moving under a program that no longer exists is
/// not a state this device will hold. `python/server.py` only cancels its
/// asyncio task here, because on the Pi the belt is somebody else's problem.
fn install(program: Program, resume: Option<(usize, i64)>, history: &FixedStr<{ record::MAX_ID }>) {
    let now = crate::CTX.clock.now();
    let mut p = lock(&crate::CTX.program);
    let stop = p.stop();
    // THE BELT IS HANDED BACK WHEN NOTHING IS ABOUT TO TAKE IT, which is what
    // `POST /api/program/load` does (`net::program::post_impl` V_LOAD drives
    // the identical plan with `release_belt = true`). Driving it with `false`
    // here left the lease with an executor that was no longer running, and the
    // only thing that took it back was the executor noticing its own `ended`
    // edge on its NEXT tick — an unstated rescue in another task that nothing
    // asserted. A paused plan, or a longer tick, and manual control would be
    // dead until an explicit `/api/program/stop`.
    //
    // ...and NOT when a resume is about to re-command in this same lock hold.
    // That is the Quick Start defect exactly (see `net::program` V_QUICK):
    // releasing puts the controller in ExitWaitGap, the replacement plan is
    // accepted by the still-current lease owner but does NOT re-enter emulate,
    // and the exit then completes and drops the relay under a program that
    // reports itself running.
    let release_belt = resume.is_none();
    if !stop.is_empty() {
        crate::net::program::drive(stop, release_belt);
    }
    p.load(program);
    // UNDER THE PROGRAM LOCK, with the load. The session recorder reads both
    // while holding the same lock, so "which history entry is this program
    // from?" can never be answered about the PREVIOUS program: setting it
    // outside left a window in which a 30 s checkpoint wrote the old
    // program's interval and elapsed time into the new entry.
    crate::net::session::set_current(history);
    if let Some((interval, elapsed)) = resume {
        let plan = p.start(now, interval, elapsed);
        crate::net::program::drive(plan, false);
    }
}

fn history_load(req: *mut sys::httpd_req_t, id: &str, resume: bool) -> sys::esp_err_t {
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    let found = store::with(|st| st.find(Which::History, id, lease.buf()));
    let Some(found) = found else {
        return no_store(req);
    };
    let Some((_, e)) = found else {
        // 200 with `ok:false`, which is what `server.py` answers for an id it
        // does not know. The Pi's extra 404 branch is a cross-PROFILE check,
        // and this device has one profile, so it cannot arise.
        return respond(req, c"200 OK", NOT_FOUND);
    };
    if resume && e.completed {
        return respond(
            req,
            c"200 OK",
            br#"{"ok":false,"error":"Program already completed - use load to start over"}"#,
        );
    }
    // The session recorder writes this entry's progress back as the program
    // runs, so it has to know which one is playing — set inside `install`,
    // under the program lock, so the two can never disagree.
    install(
        e.program,
        resume.then_some((e.last_interval as usize, e.last_elapsed_s as i64)),
        &e.id,
    );
    drop(lease);
    if resume {
        // `server.py::api_resume_history` answers `{"ok": True,
        // **sess.prog.to_dict()}` — the FULL state, not a `{"program": …}`
        // wrapper, because the caller is about to render a running workout and
        // needs `running`/`current_interval`/`total_elapsed` with it.
        //
        // Rendered by `net::program`, which buffers under the program lock and
        // RELEASES IT BEFORE SENDING. Streaming it straight to the socket
        // instead would hold that lock across a 1 s-per-flush network write,
        // and the interval executor — the task that keeps the belt to the plan
        // — takes the same lock every tick under a 2 s watchdog.
        return crate::net::program::respond_state(req, r#""ok":true,"#);
    }
    reply_program(req, &e.program)
}

fn workout_load(req: *mut sys::httpd_req_t, id: &str) -> sys::esp_err_t {
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    let outcome = store::with(|st| {
        let buf = lease.buf();
        let (n, mut e) = st.find(Which::Workouts, id, buf)?;
        // `db.update_workout_usage` — in place, so the record keeps its slot.
        e.times_used = e.times_used.saturating_add(1);
        let mut ok = st.put(Which::Workouts, Some(n), &e, buf);
        // `server.py` ALSO writes a history entry when a workout is loaded,
        // which is what puts it back at the top of the lobby's recent list.
        let mut h = e;
        h.times_used = 0;
        h.completed = false;
        h.last_interval = 0;
        h.last_elapsed_s = 0;
        ok = st.add_history(&mut h, buf) && ok;
        // A FLASH WRITE THAT FAILED IS NOT A SUCCESS. Returning `ok:true` here
        // would leave the app showing a use count and a lobby entry that do
        // not exist, and would point the session recorder at a history id that
        // was never written.
        Some((e.program, h.id, ok))
    });
    let Some(outcome) = outcome else {
        return no_store(req);
    };
    let Some((program, hid, stored)) = outcome else {
        return respond(req, c"200 OK", NOT_FOUND);
    };
    if !stored {
        return respond(
            req,
            c"500 Internal Server Error",
            br#"{"ok":false,"error":"could not write to storage"}"#,
        );
    }
    install(program, None, &hid);
    drop(lease);
    reply_program(req, &program)
}

/// `{"ok":true,"program":{…}}` — the shape `server.py` returns from both load
/// endpoints, and the one the app's `LoadHistoryResponse` decodes.
fn reply_program(req: *mut sys::httpd_req_t, p: &Program) -> sys::esp_err_t {
    use core::fmt::Write;
    begin_json(req);
    let mut sink = ChunkSink::new(req);
    let _ = sink.write_str(r#"{"ok":true,"program":"#);
    let _ = json::write_program(&mut sink, p);
    let _ = sink.write_char('}');
    sink.finish()
}

fn workout_save(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    let Some(n) = read_slot_body(req, &mut lease) else {
        return sys::ESP_OK;
    };

    // The two input modes are read out of the body BEFORE the store is
    // touched, so nothing is written on a malformed request.
    let mut history_id: FixedStr<{ record::MAX_ID }> = FixedStr::new();
    let mut source_s: FixedStr<16> = FixedStr::new();
    let mut prompt: FixedStr<{ record::MAX_PROMPT }> = FixedStr::new();
    let by_history = extract_str(&lease.buf()[..n], "history_id", &mut history_id);
    extract_str(&lease.buf()[..n], "source", &mut source_s);
    extract_str(&lease.buf()[..n], "prompt", &mut prompt);
    // `json::parse_program` accepts the wrapper shape, so a body of
    // `{"program":{…},"source":…}` yields the program directly.
    let parsed = json::parse_program(&lease.buf()[..n]).ok();

    if !by_history && parsed.is_none() {
        return respond(
            req,
            c"200 OK",
            br#"{"ok":false,"error":"Provide history_id or program"}"#,
        );
    }

    let saved = store::with(|st| {
        let buf = lease.buf();
        let mut e = if by_history {
            let (_, h) = st.find(Which::History, history_id.as_str(), buf)?;
            let mut e = h;
            // `server.py::api_save_workout` INFERS the source on this path (its
            // pydantic validator only guards the direct-program path), and this
            // is its rule EXACTLY: a "GPX:" prompt is gpx, a manual program is
            // manual, and ANYTHING ELSE is generated.
            //
            // There is deliberately no empty-prompt clause any more. The extra
            // `else if e.prompt.is_empty() { Manual }` that used to sit here had
            // no counterpart on the Pi, and since every program this device
            // stores has an empty prompt (`record_loaded`'s only call site
            // passes ""), it made EVERY saved workout report "manual" —
            // confirmed on the device: a non-manual program came back as
            // `{"source":"manual"}`.
            e.source = if e.prompt.as_str().starts_with("GPX:") {
                Source::Gpx
            } else if e.program.manual {
                Source::Manual
            } else {
                Source::Generated
            };
            e
        } else {
            let mut e = Entry::new("", parsed?);
            // The closed set the Pi validates against. An unknown value is not
            // an error here because it cannot be STORED — there is nowhere to
            // put it — so it degrades to the same default the Pi's own
            // history path infers.
            e.source = Source::parse(source_s.as_str()).unwrap_or(Source::Manual);
            e.prompt = prompt;
            e
        };
        e.times_used = 0;
        e.completed = false;
        e.last_interval = 0;
        e.last_elapsed_s = 0;
        e.id = st.next_id(Which::Workouts);
        if !st.put(Which::Workouts, None, &e, buf) {
            return None;
        }
        // Looked up in the SAME hold, so the single-workout reply carries the
        // same `last_run`/`usage_text` the next list request will — a reply
        // that disagreed with the list would show the user two different
        // "Never used" / "Last run:" states for one tap.
        let runs = build_run_index(st, buf);
        let run = last_run_for(st, &runs, &e.program, buf);
        Some((e, run))
    });

    let Some(saved) = saved else {
        return no_store(req);
    };
    let Some((e, run)) = saved else {
        return respond(req, c"200 OK", NOT_FOUND);
    };
    use core::fmt::Write;
    begin_json(req);
    let mut sink = ChunkSink::new(req);
    let _ = sink.write_str(r#"{"ok":true,"workout":"#);
    let _ = record::write_saved_workout(&mut sink, &e, run.as_ref());
    let _ = sink.write_char('}');
    sink.finish()
}

fn workout_rename(req: *mut sys::httpd_req_t, id: &str) -> sys::esp_err_t {
    let mut body = [0u8; MAX_CMD_BODY];
    let Some(n) = crate::net::api::read_body(req, &mut body) else {
        return sys::ESP_OK;
    };
    let mut name: FixedStr<{ program_core::MAX_PROGRAM_NAME }> = FixedStr::new();
    // `RenameWorkoutRequest` is `min_length=1, max_length=200` on the Pi. The
    // upper bound here is the field's own capacity (truncating, as every name
    // in this firmware does); the lower bound is enforced, because an empty
    // name would leave a workout the user cannot identify.
    if !extract_str(&body[..n], "name", &mut name) || name.is_empty() {
        return respond(
            req,
            c"400 Bad Request",
            br#"{"ok":false,"error":"name is required"}"#,
        );
    }
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    let updated = store::with(|st| {
        let buf = lease.buf();
        let (pos, mut e) = st.find(Which::Workouts, id, buf)?;
        // ONE name, so a rename cannot half-apply. `db.rename_workout` has to
        // write the `name` column AND the name inside the stored program blob,
        // and a device that updated only one would desync from what
        // `/{id}/load` then loads.
        e.program.name = FixedStr::from_str_truncating(name.as_str());
        if !st.put(Which::Workouts, Some(pos), &e, buf) {
            return None;
        }
        let runs = build_run_index(st, buf);
        let run = last_run_for(st, &runs, &e.program, buf);
        Some((e, run))
    });
    let Some(updated) = updated else {
        return no_store(req);
    };
    let Some((e, run)) = updated else {
        return respond(req, c"200 OK", NOT_FOUND);
    };
    use core::fmt::Write;
    begin_json(req);
    let mut sink = ChunkSink::new(req);
    let _ = sink.write_str(r#"{"ok":true,"workout":"#);
    let _ = record::write_saved_workout(&mut sink, &e, run.as_ref());
    let _ = sink.write_char('}');
    sink.finish()
}

fn workout_delete(req: *mut sys::httpd_req_t, id: &str) -> sys::esp_err_t {
    let Some(mut lease) = scratch(req) else {
        return sys::ESP_OK;
    };
    let gone = store::with(|st| {
        let buf = lease.buf();
        let pos = st.find_by_id(Which::Workouts, id, buf)?;
        Some(st.erase(Which::Workouts, pos))
    });
    match gone {
        None => no_store(req),
        Some(None) | Some(Some(false)) => respond(req, c"200 OK", NOT_FOUND),
        Some(Some(true)) => respond(req, c"200 OK", br#"{"ok":true}"#),
    }
}

/// Write a freshly loaded program into the history ring.
///
/// Called from `net::program` on load/start-with-body, which is where
/// `server.py` calls `_add_to_history`. NOT from quick-start: `ensure_manual`
/// does not add to history on the Pi either, and a lobby full of "Quick Start"
/// entries would push out the workouts the user actually built.
/// Returns the history id the program was stored under, so the CALLER can
/// publish it under the program lock — the recorder must never be pointed at a
/// history entry that does not match the loaded program.
pub fn record_loaded(
    program: &Program,
    prompt: &str,
) -> Option<FixedStr<{ record::MAX_ID }>> {
    // Every slot in flight. History is a nicety; the program has already
    // loaded and the belt is unaffected.
    let mut lease = reqbudget::admit(reqbudget::SLOT_BYTES).ok()?;
    store::with(|st| {
        let mut e = Entry::new("", *program);
        // Sanitised on the way in like every other stored string, so the
        // serialiser can emit it verbatim. The only caller passes "" today;
        // this keeps the invariant true of the FUNCTION rather than of its
        // current call site.
        let mut clean: FixedStr<{ record::MAX_PROMPT }> = FixedStr::new();
        for &b in prompt.as_bytes() {
            clean.push_byte(if b < 0x20 || b == b'"' || b == b'\\' { b'_' } else { b });
        }
        e.prompt = clean;
        e.source = if program.manual {
            Source::Manual
        } else {
            Source::Generated
        };
        if !st.add_history(&mut e, lease.buf()) {
            return None;
        }
        Some(e.id)
    })
    .flatten()
}

/// Register every record route on the already-started server.
///
/// ORDER IS LOAD-BEARING: IDF walks the table and takes the FIRST match, and
/// `httpd_uri_match_wildcard` makes `/api/workouts/*` match anything under it.
/// The exact routes must therefore be registered before the wildcards.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly and
    // introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H, usize); 8] = [
        (c"/api/programs/history", sys::http_method_HTTP_GET, list_handler, R_HISTORY),
        (c"/api/workouts", sys::http_method_HTTP_GET, list_handler, R_WORKOUTS),
        (c"/api/runs", sys::http_method_HTTP_GET, list_handler, R_RUNS),
        (c"/api/workouts", sys::http_method_HTTP_POST, mutate_handler, V_WORKOUT_SAVE),
        (c"/api/programs/history/*", sys::http_method_HTTP_POST, mutate_handler, V_HIST_LOAD),
        (c"/api/workouts/*", sys::http_method_HTTP_POST, mutate_handler, V_WORKOUT_ID),
        (c"/api/workouts/*", sys::http_method_HTTP_PUT, mutate_handler, V_WORKOUT_ID),
        (c"/api/workouts/*", sys::http_method_HTTP_DELETE, mutate_handler, V_WORKOUT_ID),
    ];
    for (path, method, handler, ctx) in routes {
        let uri = sys::httpd_uri_t {
            uri: path.as_ptr(),
            method,
            handler: Some(handler),
            user_ctx: ctx as *mut core::ffi::c_void,
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
