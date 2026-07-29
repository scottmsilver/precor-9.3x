/*!
 * coach_core — everything about the AI coach that is NOT a socket.
 *
 * The device tier that talks to Gemini splits cleanly in two, and this crate is
 * the half that can be tested in milliseconds on a host: build the request,
 * consume the reply through a bounded buffer, validate what the model asked
 * for, and clamp it. The other half — `esp32tap/src/net/coach.rs` — owns the
 * NVS key, the `esp_http_client` round trip and the task it runs on, and
 * contains no judgement about what a model reply MEANS.
 *
 * ## A MODEL REPLY IS UNTRUSTED INPUT
 *
 * That is the whole reason this crate exists as a crate. Gemini is documented
 * to round numbers, to invent them, and to emit tool calls whose arguments were
 * never in range; the tokens also arrive from a public endpoint over a link
 * anyone on the LAN can point somewhere else (see `net::coach`'s endpoint
 * pinning). So every value the model produces is parsed by a TOTAL parser,
 * bounded by a fixed buffer, and CLAMPED into `safety_core`'s newtypes before
 * anything downstream sees it.
 *
 * The clamp here is not the safety clamp. `SafetyController::command_motion`
 * clamps again and `program_core::Interval::new` clamps again, and both are
 * downstream, so if they ever disagree with this crate the DEVICE wins. What
 * clamping here buys is an honest ANSWER: the user is told the belt went to
 * 12.0 mph because the model asked for 999, instead of the request vanishing.
 *
 * ## Bounded, and independent of how long the conversation is
 *
 * Nothing in this crate allocates — `#![no_std]` outside `cfg(test)` and
 * `alloc` is never named, so an allocation is a COMPILE ERROR. Every buffer is
 * a `FixedStr<N>` or a fixed array:
 *
 *  * the reply text/JSON sink is [`scan::TEXT_BYTES`] and is REUSED, not grown;
 *  * at most [`scan::MAX_CALLS`] tool calls are extracted per reply, each with
 *    a [`scan::ARGS_BYTES`] raw-argument budget;
 *  * the conversation is [`hist::TURNS`] turns of [`hist::TURN_BYTES`] each,
 *    and the ring OVERWRITES rather than growing.
 *
 * So resident memory is the same after ten thousand turns as after one. The
 * arithmetic is in [`resident_bytes`] and pinned by a test, for the same reason
 * `reqbudget::budget_bytes` is: a bound nobody can name is a bound nobody can
 * check.
 *
 * ## No clock
 *
 * This device has no RTC and no SNTP. Nothing here produces or consumes a
 * timestamp, the system prompt says so to the model, and the only notion of
 * time offered to the coach is the session tick `program_core` already keeps.
 * Inventing a date would produce a coach that confidently compares runs that
 * never happened.
 */

#![cfg_attr(not(test), no_std)]
#![forbid(unsafe_code)]

pub mod hist;
pub mod prompt;
pub mod req;
pub mod salvage;
pub mod scan;
pub mod tool;

pub use hist::{History, Role};
pub use scan::{ReplyScanner, ToolCall};
pub use tool::{Action, Reject};

/// Every byte of coach state that is resident for the life of the device.
///
/// One line, the way `reqbudget::budget_bytes()` is one line. It is a function
/// of the CAPS above and of nothing else — not of the conversation length, not
/// of the number of requests served, not of how large a model reply was.
pub const fn resident_bytes() -> usize {
    core::mem::size_of::<ReplyScanner>()
        + core::mem::size_of::<History>()
        + req::REQ_BYTES
}

#[cfg(test)]
mod resident_tests {
    use super::*;

    #[test]
    fn resident_memory_is_arithmetic_not_emergent() {
        // The number itself is allowed to move when a cap moves; what must NOT
        // move is that it is BOUNDED and small. A regression that made any
        // buffer grow with input would blow this by orders of magnitude.
        let n = resident_bytes();
        assert!(n < 16 * 1024, "coach resident state grew to {n} bytes");
        assert!(n > 4 * 1024, "suspiciously small — did a buffer become a pointer?");
    }

    #[test]
    fn resident_memory_does_not_depend_on_conversation_length() {
        let mut h = History::new();
        let before = core::mem::size_of_val(&h);
        for i in 0..10_000 {
            h.push(Role::User, "a message that is quite a lot longer than the cap allows, repeatedly");
            h.push(Role::Model, "and a model answer too");
            assert!(h.turns() <= hist::TURNS, "ring grew at turn {i}");
        }
        assert_eq!(core::mem::size_of_val(&h), before);
    }
}
