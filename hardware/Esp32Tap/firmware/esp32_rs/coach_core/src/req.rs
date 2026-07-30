//! Building the `generateContent` body, in place, into a fixed buffer.
//!
//! # Why the whole body IS buffered when the whole reply is NOT
//!
//! That asymmetry is deliberate and is the difference between the two
//! directions. The REPLY is written by a remote server and its size is that
//! server's choice, so it is streamed through a chunk and never held. The
//! REQUEST is written by this crate out of caps this crate owns — a bounded
//! system prompt, a bounded history ring, a bounded user message, a constant
//! tool declaration — so its worst case is arithmetic, is asserted at COMPILE
//! time in [`REQ_BYTES`], and holding it lets `esp_http_client` send a real
//! `Content-Length` instead of chunking.
//!
//! # There is no escape path here, on purpose
//!
//! Every string written between quotes below was sanitised at INGEST —
//! `hist::History::push` and `tool::string` replace any byte below 0x20, any
//! `"` and any `\` with `_`. So the builder can write them verbatim. That is
//! `program_core::json`'s rule ("names are sanitised on the way in, not escaped
//! on the way out") applied to the second place this firmware emits JSON, for
//! the same reason: one rule applied once at the boundary cannot be got wrong
//! at the twelve places that emit.
//!
//! # Overflow is a REFUSAL, never a truncation
//!
//! A truncated request body is not a smaller question, it is invalid JSON that
//! the endpoint answers 400 to — after the round trip has been paid for. So
//! [`Builder`] tracks overflow and [`build_chat`] returns `None`, which the
//! caller reports as a failed turn without opening a socket at all.

use crate::hist::{History, TURNS, TURN_BYTES};
use crate::prompt;

/// Longest user message carried into a turn.
///
/// The endpoint reads at most this many bytes of `"message"`; a longer one is
/// truncated at ingest. Matched to `hist::TURN_BYTES` so the message that is
/// SENT is exactly the message that will be REMEMBERED — a window that stored
/// less than it sent would make the model's own previous turn unrecognisable
/// to it.
pub const MSG_BYTES: usize = TURN_BYTES;

/// The device-state line appended to the system prompt.
///
/// Fixed width by construction: speed, incline, mode, and (only when a program
/// is running) the session's elapsed seconds and current interval index. No
/// wall-clock time appears, because there is none.
pub const STATE_BYTES: usize = 192;

/// The request buffer, in bytes. Reserved at link time, reused every turn.
///
/// DERIVED, NOT CHOSEN, and the derivation is the assertion below rather than
/// this comment. 4096 was the first guess and the compiler rejected it: the
/// worst case is ~4.5 KB, dominated by [`prompt::TOOL_DECLARATIONS`] (1523 B)
/// and a full history ring (6 × 208 B). 6144 carries that with ~1.6 KB spare.
///
/// It is a STATIC buffer, not a stack one: 6 KB on the coach task's stack on
/// top of an mbedtls session is how you get an intermittent overflow that
/// reboots the device, and a reboot drops the relay. `net::session` learned
/// that the expensive way at 6144 bytes of stack.
pub const REQ_BYTES: usize = 6144;

/// The worst-case body: the envelope, the longest prompt, the state line, a
/// full history ring, the longest user message, and the tool declarations.
/// Every term is a cap this crate owns.
const WORST_CASE: usize = 512 // envelope: keys, braces, generationConfig
    + longest(prompt::CHAT_SYSTEM.len(), prompt::PROGRAM_SYSTEM.len())
    + STATE_BYTES
    + TURNS * (TURN_BYTES + 48) // each turn's own {"role":"model","parts":[{"text":""}]}
    + MSG_BYTES
    + prompt::TOOL_DECLARATIONS.len();

const fn longest(a: usize, b: usize) -> usize {
    if a > b {
        a
    } else {
        b
    }
}

const _: () = assert!(
    WORST_CASE <= REQ_BYTES,
    "the worst-case coach request no longer fits REQ_BYTES — raise it \
     deliberately, or lower a cap (hist::TURNS, req::MSG_BYTES, the tool \
     declarations). Do NOT let it truncate: a truncated body is invalid JSON \
     that costs a round trip to be told about."
);

/// A bounded writer over the caller's buffer. Overflow is recorded, never
/// silently dropped.
pub struct Builder<'a> {
    buf: &'a mut [u8],
    len: usize,
    overflow: bool,
}

impl<'a> Builder<'a> {
    pub fn new(buf: &'a mut [u8]) -> Self {
        Builder {
            buf,
            len: 0,
            overflow: false,
        }
    }

    pub fn raw(&mut self, s: &str) {
        let b = s.as_bytes();
        if self.len + b.len() > self.buf.len() {
            self.overflow = true;
            return;
        }
        self.buf[self.len..self.len + b.len()].copy_from_slice(b);
        self.len += b.len();
    }

    /// A pre-sanitised string, written verbatim between the caller's quotes.
    ///
    /// SANITISES AGAIN, cheaply, rather than trusting the caller. Ingest is
    /// where the rule is applied, but this is the last line before bytes leave
    /// the device, and a `"` reaching it would produce a body the endpoint
    /// rejects — or, worse, one it accepts with the prompt reshaped by whatever
    /// the user typed. Two applications of an idempotent rule cost nothing.
    pub fn text(&mut self, s: &str) {
        for b in s.as_bytes() {
            if self.len >= self.buf.len() {
                self.overflow = true;
                return;
            }
            self.buf[self.len] = crate::tool::sanitise(*b);
            self.len += 1;
        }
    }

    pub fn int(&mut self, v: i64) {
        let mut tmp: safety_core::FixedStr<24> = safety_core::FixedStr::new();
        tmp.push_i64(v);
        self.raw(tmp.as_str());
    }

    pub fn finish(self) -> Option<usize> {
        if self.overflow {
            None
        } else {
            Some(self.len)
        }
    }
}

/// Build a chat turn's request body into `buf`. Returns its length.
///
/// `state` is the device-state sentence (already sanitised); `msg` is the
/// pending user message, appended LAST and deliberately not yet in `history` —
/// see the note in `hist` about why there is no rollback.
pub fn build_chat(
    buf: &mut [u8],
    history: &History,
    state: &str,
    msg: &str,
) -> Option<usize> {
    let mut w = Builder::new(buf);
    w.raw(r#"{"systemInstruction":{"parts":[{"text":""#);
    // The prompt is a constant this crate owns and carries no byte that
    // would need escaping (`prompt::prompts_need_no_escaping`), so it goes out
    // verbatim. The STATE line is built by the firmware from live values and
    // is sanitised, because "built by us" is not the same as "constant".
    w.raw(prompt::CHAT_SYSTEM);
    w.raw(" ");
    w.text(state);
    w.raw(r#""}]},"contents":["#);
    for turn in history.iter() {
        w.raw(r#"{"role":""#);
        w.raw(turn.role.wire());
        w.raw(r#"","parts":[{"text":""#);
        w.text(turn.text.as_str());
        w.raw(r#""}]},"#);
    }
    w.raw(r#"{"role":"user","parts":[{"text":""#);
    w.text(msg);
    w.raw(r#""}]}],"tools":["#);
    w.raw(prompt::TOOL_DECLARATIONS);
    // maxOutputTokens matches `call_gemini`'s chat default. temperature is the
    // Pi's 0.7.
    w.raw(r#"],"generationConfig":{"temperature":0.7,"maxOutputTokens":1024}}"#);
    w.finish()
}

/// Build the workout-generation body. Structured output, no tools.
///
/// `maxOutputTokens` is 2048 rather than the Pi's 4096: this device's program
/// buffer is `MAX_PROGRAM_JSON_BYTES` (2048 bytes), so asking for more tokens
/// than can be stored would only buy a longer reply to throw away. A workout
/// the model runs out of tokens inside is repaired by [`crate::salvage`].
pub fn build_program(buf: &mut [u8], description: &str) -> Option<usize> {
    let mut w = Builder::new(buf);
    w.raw(r#"{"systemInstruction":{"parts":[{"text":""#);
    w.raw(prompt::PROGRAM_SYSTEM);
    w.raw(r#""}]},"contents":[{"role":"user","parts":[{"text":""#);
    w.text(description);
    w.raw(r#""}]}],"generationConfig":{"temperature":0.7,"#);
    w.raw(r#""responseMimeType":"application/json","maxOutputTokens":2048}}"#);
    w.finish()
}
