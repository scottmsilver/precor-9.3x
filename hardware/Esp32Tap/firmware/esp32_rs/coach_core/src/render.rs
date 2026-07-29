//! Rendering the `actions` array a client is shown.
//!
//! # Why this is here and not in the firmware
//!
//! It used to be sixteen lines in `net/coach.rs`, and every one of the four
//! defects a reviewer found in the coach tier lived in those sixteen lines:
//!
//!   * a tool NAME was written between two `"` with no escaping, so a name
//!     carrying a `"` injected extra members into the action object and one
//!     carrying a control byte made the whole body unparseable;
//!   * an `args` object was echoed VERBATIM on the premise that it is "balanced
//!     JSON the model itself wrote", which is false for the two cases the
//!     scanner marks damaged — `args_overflow` and `args_unterminated` both
//!     leave a NON-EMPTY, unbalanced prefix in the buffer, and the emptiness
//!     test could not tell them apart;
//!   * the array saturated MID-TOKEN at `MAX_CALLS` calls, which is the
//!     DECLARED maximum rather than an abuse, because `FixedStr::push_str`
//!     truncates silently;
//!   * and none of it could be tested without booting a guest.
//!
//! The last one is why they survived. This module is pure, `no_std` and
//! allocation-free, so every shape above is a host test that runs in the
//! `coachcore` gate in about zero seconds — and the firmware keeps exactly one
//! renderer, so `GET /api/chat` and the `/ws` coach frame cannot disagree.
//!
//! # The two rules
//!
//! **Every string this module writes between quotes goes through
//! [`crate::tool::sanitise`].** A model reply is untrusted input and the
//! scanner DECODES escapes on the way in (`\"` becomes a bare quote, `\n`
//! becomes byte 0x0A), so nothing arriving from a reply is quote-free by
//! construction. One filter, applied to every such string, is the only shape
//! that cannot grow a third exception.
//!
//! **An entry is measured before it is written.** [`Actions::push`] either
//! writes a COMPLETE entry or writes none, and when none fits it appends
//! [`TRUNCATED`] once — so the array is well-formed JSON for every input,
//! including inputs this scanner cannot actually produce. The sizing below
//! makes the sentinel unreachable for anything the scanner CAN produce; it
//! exists so that stays true when a cap changes, rather than because the
//! caller is trusted to keep a counter right.

use crate::scan::{ToolCall, ARGS_BYTES, MAX_CALLS, NAME_BYTES};
use crate::tool::sanitise;
use safety_core::FixedStr;

/// Bytes of `result` sentence. The same cap `tool::describe` renders into.
pub const RESULT_BYTES: usize = 96;

/// The stand-in for entries that would not fit. A COMPLETE entry, so appending
/// it can never be what breaks the array.
pub const TRUNCATED: &str =
    r#"{"name":"truncated","args":{},"result":"some results did not fit and were dropped"}"#;

// The fixed punctuation of one entry, named so the worst case below is
// arithmetic rather than a guess.
const SEP: usize = 1; // the `,` between entries
const OPEN: usize = 9; // {"name":"
const MID: usize = 9; // ","args":
const RES: usize = 11; // ,"result":"
const END: usize = 2; // "}
const EMPTY_ARGS: usize = 2; // {}

/// The widest entry the scanner can hand us: a full-length name, a full-length
/// argument object, and a full-length result.
const WORST_ENTRY: usize = SEP + OPEN + NAME_BYTES + MID + ARGS_BYTES + RES + RESULT_BYTES + END;

/// The `ignored` entry `net::coach` appends when the scanner dropped a fifth
/// call. `{}` args, but its result shares the same cap as any other.
const OVERFLOW_ENTRY: usize = SEP + OPEN + NAME_BYTES + MID + EMPTY_ARGS + RES + RESULT_BYTES + END;

/// Bytes of rendered `actions` array, WITHOUT its brackets.
///
/// DERIVED FROM `MAX_CALLS`, NOT CHOSEN. It was 384, which is smaller than a
/// SINGLE worst-case entry and smaller than four realistic ones — so the
/// declared maximum of four tool calls rendered as invalid JSON, at full width,
/// on the happy path. The number below is what the scanner's own caps permit
/// plus room for the sentinel, and the assertion under it is what keeps the two
/// in step when a cap moves.
pub const ACTIONS_BYTES: usize =
    MAX_CALLS * WORST_ENTRY + OVERFLOW_ENTRY + SEP + TRUNCATED.len() + 32;

const _: () = assert!(
    ACTIONS_BYTES >= MAX_CALLS * WORST_ENTRY + OVERFLOW_ENTRY + SEP + TRUNCATED.len(),
    "ACTIONS_BYTES can no longer hold MAX_CALLS worst-case entries plus the \
     overflow entry plus the truncation sentinel — a turn at the DECLARED \
     maximum would render the sentinel instead of the results"
);

/// The rendered array, built entry by entry.
pub struct Actions(FixedStr<ACTIONS_BYTES>);

impl Actions {
    pub const fn new() -> Self {
        Actions(FixedStr::new())
    }

    pub fn as_str(&self) -> &str {
        self.0.as_str()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    /// Append one entry.
    ///
    /// `args` is `Some` only for a call that survived transport INTACT — see
    /// [`Actions::push_call`], which is the caller that decides. `None` renders
    /// `{}`.
    ///
    /// Nothing here can produce a partial entry: the length is computed first
    /// and the write is all-or-nothing.
    pub fn push(&mut self, name: &str, args: Option<&FixedStr<ARGS_BYTES>>, result: &str) {
        if self.already_truncated() {
            return;
        }
        let args_len = args.map(|a| a.len()).unwrap_or(EMPTY_ARGS);
        // Sanitising is length-preserving (one byte in, one byte out), which is
        // what lets the measurement below be taken before the escaping.
        let need = if self.0.is_empty() { 0 } else { SEP }
            + OPEN
            + name.len()
            + MID
            + args_len
            + RES
            + result.len()
            + END;
        // The sentinel's own room is RESERVED, not hoped for: an array that
        // filled up and then could not say so would be the silent failure this
        // whole module exists to remove.
        if self.0.len() + need + SEP + TRUNCATED.len() > ACTIONS_BYTES {
            self.push_truncated();
            return;
        }
        if !self.0.is_empty() {
            self.0.push_byte(b',');
        }
        self.0.push_str("{\"name\":\"");
        self.push_quoted(name.as_bytes());
        self.0.push_str("\",\"args\":");
        match args {
            // VERBATIM, and now it really is safe to be: the caller passes
            // `Some` only for `ToolCall::is_intact()`, which is exactly the
            // predicate that says the scanner saw the object OPEN and CLOSE
            // inside its budget. `args.is_empty()` was the old test and it is
            // not the same predicate — an overflowed or unterminated object is
            // non-empty and unbalanced.
            Some(a) => self.0.push_str(a.as_str()),
            None => self.0.push_str("{}"),
        }
        self.0.push_str(",\"result\":\"");
        self.push_quoted(result.as_bytes());
        self.0.push_str("\"}");
    }

    /// Append the entry for one scanned call, with the intactness rule applied
    /// in ONE place rather than at each call site.
    pub fn push_call(&mut self, call: &ToolCall, result: &str) {
        let args = if call.is_intact() {
            Some(&call.args)
        } else {
            None
        };
        self.push(call.name.as_str(), args, result);
    }

    fn push_quoted(&mut self, bytes: &[u8]) {
        for b in bytes {
            self.0.push_byte(sanitise(*b));
        }
    }

    /// Whether the sentinel is already the last thing in the buffer.
    ///
    /// Read off the BUFFER rather than kept in a flag, for the same reason the
    /// separator is: a flag and a buffer are two facts that can disagree.
    fn already_truncated(&self) -> bool {
        let b = self.0.as_bytes();
        b.len() >= TRUNCATED.len() && &b[b.len() - TRUNCATED.len()..] == TRUNCATED.as_bytes()
    }

    fn push_truncated(&mut self) {
        if self.already_truncated() {
            return;
        }
        if !self.0.is_empty() {
            self.0.push_byte(b',');
        }
        self.0.push_str(TRUNCATED);
    }
}

impl Default for Actions {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scan::ReplyScanner;

    /// A minimal JSON validity check for `[ ... ]` — enough to catch every
    /// failure mode this module had (an unterminated string, an unbalanced
    /// object, a quote injected mid-name) without pulling a parser into a
    /// `no_std` crate.
    fn array_is_well_formed(body: &str) -> bool {
        let mut depth = 0i32;
        let mut in_string = false;
        let mut escape = false;
        for b in body.as_bytes() {
            if in_string {
                if escape {
                    escape = false;
                } else if *b == b'\\' {
                    escape = true;
                } else if *b == b'"' {
                    in_string = false;
                } else if *b < 0x20 {
                    return false; // a raw control byte inside a string
                }
                continue;
            }
            match *b {
                b'"' => in_string = true,
                b'{' | b'[' => depth += 1,
                b'}' | b']' => {
                    depth -= 1;
                    if depth < 0 {
                        return false;
                    }
                }
                _ => {}
            }
        }
        depth == 0 && !in_string
    }

    /// Count the top-level `{...}` entries, so a test can say how many actions
    /// a client would actually see.
    fn entries(body: &str) -> usize {
        let mut n = 0;
        let mut depth = 0i32;
        let mut in_string = false;
        let mut escape = false;
        for b in body.as_bytes() {
            if in_string {
                if escape {
                    escape = false;
                } else if *b == b'\\' {
                    escape = true;
                } else if *b == b'"' {
                    in_string = false;
                }
                continue;
            }
            match *b {
                b'"' => in_string = true,
                b'{' => {
                    if depth == 0 {
                        n += 1;
                    }
                    depth += 1;
                }
                b'}' => depth -= 1,
                _ => {}
            }
        }
        n
    }

    fn scan(body: &str) -> ReplyScanner {
        let mut s = ReplyScanner::new();
        s.push_all(body.as_bytes());
        s.finish_stream();
        s
    }

    #[test]
    fn a_name_carrying_a_quote_cannot_inject_members() {
        // The reviewer's fixture, byte for byte: the scanner DECODES `\"` into
        // a bare quote, so `name` arrives with a quote in it.
        let s = scan(
            r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"set_speed\", \"x\":\"","args":{"mph":3}}}]}}]}"#,
        );
        assert_eq!(s.n_calls, 1);
        assert!(s.calls[0].name.as_str().contains('"'), "fixture must bite");

        let mut a = Actions::new();
        a.push_call(&s.calls[0], "that tool does not exist on this device");
        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
        assert_eq!(entries(a.as_str()), 1);
        // And the injected member name is gone, not merely escaped away.
        assert!(!a.as_str().contains(r#""x":"#), "{}", a.as_str());
    }

    #[test]
    fn a_name_carrying_a_control_byte_cannot_break_the_body() {
        let s = scan(
            r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"set\nspeed","args":{"mph":3}}}]}}]}"#,
        );
        assert_eq!(s.n_calls, 1);
        assert!(s.calls[0].name.as_bytes().contains(&b'\n'), "fixture must bite");

        let mut a = Actions::new();
        a.push_call(&s.calls[0], "that tool does not exist on this device");
        assert!(array_is_well_formed(a.as_str()), "{:?}", a.as_str());
        assert!(
            !a.as_str().as_bytes().iter().any(|b| *b < 0x20),
            "a raw control byte reached the body: {:?}",
            a.as_str()
        );
    }

    #[test]
    fn a_name_ending_in_a_backslash_cannot_escape_its_own_quote() {
        let s = scan(
            r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"set_speed\\","args":{}}}]}}]}"#,
        );
        assert_eq!(s.n_calls, 1);
        let mut a = Actions::new();
        a.push_call(&s.calls[0], "unknown");
        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
    }

    #[test]
    fn an_oversized_args_object_is_not_echoed_at_all() {
        // One sentence of description is enough to blow ARGS_BYTES (192).
        let long = "a forty five minute progressive hill workout with four climbs, recovery \
                    valleys between them, a long easy cool down at the end, and please keep \
                    the steepest climb under ten percent because my knees are not what they \
                    used to be";
        let body = format!(
            "{}{}{}",
            r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"generate_workout","args":{"description":""#,
            long,
            r#""}}}]}}]}"#
        );
        let s = scan(&body);
        assert_eq!(s.n_calls, 1);
        assert!(s.calls[0].args_overflow, "fixture must actually overflow");
        assert!(!s.calls[0].args.is_empty(), "and must leave a non-empty prefix");

        let mut a = Actions::new();
        a.push_call(&s.calls[0], "the tool call did not arrive intact and was ignored");
        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
        assert!(a.as_str().contains(r#""args":{}"#), "{}", a.as_str());
    }

    #[test]
    fn an_unterminated_args_object_is_not_echoed_either() {
        // The stream simply stops inside the arguments — a truncated reply.
        let s = scan(
            r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"set_speed","args":{"mph":11"#,
        );
        assert_eq!(s.n_calls, 1);
        assert!(s.calls[0].args_unterminated, "fixture must actually truncate");
        assert!(!s.calls[0].args.is_empty());

        let mut a = Actions::new();
        a.push_call(&s.calls[0], "the tool call did not arrive intact and was ignored");
        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
        assert!(a.as_str().contains(r#""args":{}"#), "{}", a.as_str());
        // The number that would have been guessed must not survive anywhere.
        assert!(!a.as_str().contains("11"), "{}", a.as_str());
    }

    #[test]
    fn max_calls_worst_case_entries_all_fit_and_stay_valid() {
        // The DECLARED maximum at FULL WIDTH: four calls, each with a
        // full-length name, a full-length argument object and a full-length
        // result. This is what saturated the old 384-byte buffer mid-token.
        let mut a = Actions::new();
        let mut args: FixedStr<ARGS_BYTES> = FixedStr::new();
        args.push_str("{\"note\":\"");
        while args.len() < ARGS_BYTES - 3 {
            args.push_byte(b'p');
        }
        args.push_str("\"}");
        let mut name: FixedStr<NAME_BYTES> = FixedStr::new();
        while name.len() < NAME_BYTES {
            name.push_byte(b'n');
        }
        let mut result: FixedStr<RESULT_BYTES> = FixedStr::new();
        while result.len() < RESULT_BYTES {
            result.push_byte(b'r');
        }
        for _ in 0..MAX_CALLS {
            a.push(name.as_str(), Some(&args), result.as_str());
        }
        // ...plus the `ignored` entry a fifth dropped call adds.
        a.push("ignored", None, result.as_str());

        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
        assert_eq!(
            entries(a.as_str()),
            MAX_CALLS + 1,
            "the declared maximum must render in full, not as the sentinel: {}",
            a.as_str()
        );
        assert!(!a.as_str().contains("truncated"), "{}", a.as_str());
    }

    #[test]
    fn an_entry_that_cannot_fit_becomes_one_sentinel_and_the_array_stays_valid() {
        // Push far past the cap. Nothing the scanner produces reaches here —
        // the assertion above proves that — so this drives it directly.
        let mut a = Actions::new();
        let mut args: FixedStr<ARGS_BYTES> = FixedStr::new();
        args.push_str("{\"note\":\"");
        while args.len() < ARGS_BYTES - 3 {
            args.push_byte(b'p');
        }
        args.push_str("\"}");
        for _ in 0..40 {
            a.push("generate_workout", Some(&args), "building a workout");
        }
        assert!(array_is_well_formed(a.as_str()), "{}", a.as_str());
        assert!(a.len() <= ACTIONS_BYTES);
        let s = a.as_str();
        assert!(s.ends_with(TRUNCATED), "{}", s);
        // EXACTLY ONE sentinel: a second would be as wrong as none.
        assert_eq!(s.matches(TRUNCATED).count(), 1, "{}", s);
    }

    #[test]
    fn an_empty_array_renders_as_nothing() {
        let a = Actions::new();
        assert!(a.is_empty());
        assert!(array_is_well_formed(a.as_str()));
    }
}
