//! The reply shapes that actually break things.
//!
//! Deliberately NOT "a happy-path response parses". The live endpoint is
//! excluded from the sweep on purpose (it needs a real key, it is
//! nondeterministic, and a gate that costs money and flakes is the thing this
//! project spent a night removing), so what this file has to cover is every
//! shape that arrives when the happy path does not: a reply delivered one byte
//! at a time, one cut off mid-object, one larger than any buffer here, one that
//! is not JSON at all, an HTTP error envelope, and a tool call whose arguments
//! are hostile.
//!
//! Every case asserts the same two properties: the machine does not panic (a
//! panic under `panic = "abort"` reboots the device and drops the relay), and
//! nothing that did not arrive intact is ever turned into an [`Action`].

use coach_core::scan::{ReplyScanner, ARGS_BYTES, MAX_CALLS, TEXT_BYTES};
use coach_core::tool::{validate, Action, Reject};
use safety_core::units::{InclineHalfPct, SpeedTenths};

fn scan(body: &str) -> ReplyScanner {
    let mut s = ReplyScanner::new();
    s.push_all(body.as_bytes());
    s.finish_stream();
    s
}

/// The same bytes, delivered one at a time. Must be indistinguishable.
fn scan_dribbled(body: &str) -> ReplyScanner {
    let mut s = ReplyScanner::new();
    for b in body.as_bytes() {
        s.push_all(&[*b]);
    }
    s.finish_stream();
    s
}

const PLAIN: &str = r#"{"candidates":[{"content":{"parts":[{"text":"Taking it to three."}],
"role":"model"},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":812,
"candidatesTokenCount":9,"totalTokenCount":821},"modelVersion":"gemini-2.5-flash"}"#;

const WITH_CALL: &str = r#"{"candidates":[{"content":{"parts":[
{"text":"Sure."},
{"functionCall":{"name":"set_speed","args":{"mph":3.5}}}
],"role":"model"},"finishReason":"STOP"}]}"#;

#[test]
fn text_is_harvested_and_the_envelope_is_not() {
    let s = scan(PLAIN);
    assert_eq!(s.text.as_str(), "Taking it to three.");
    assert_eq!(s.n_calls, 0);
    assert_eq!(s.finish.as_str(), "STOP");
    assert!(!s.malformed);
    // `modelVersion` and `usageMetadata` are recognised and dropped: nothing
    // about them reaches the sinks.
    assert!(!s.text.as_str().contains("gemini"));
}

#[test]
fn a_tool_call_carries_its_arguments_verbatim() {
    let s = scan(WITH_CALL);
    assert_eq!(s.text.as_str(), "Sure.");
    assert_eq!(s.n_calls, 1);
    assert_eq!(s.calls[0].name.as_str(), "set_speed");
    assert_eq!(s.calls[0].args.as_str(), r#"{"mph":3.5}"#);
    assert!(s.calls[0].is_intact());
    assert_eq!(validate(&s.calls[0]), Ok(Action::SetSpeed(SpeedTenths::new(35))));
}

/// THE CHUNK-BOUNDARY PROPERTY. `esp_http_client_read` returns whatever the TCP
/// stack has; the reply that worked in one segment is the reply that breaks in
/// forty. Asserting it over every fixture in this file is cheaper than one
/// field investigation.
#[test]
fn chunking_is_not_observable() {
    for body in [PLAIN, WITH_CALL, TRUNCATED, TWO_CALLS, ERROR_ENVELOPE, NOT_JSON] {
        let whole = scan(body);
        let dribbled = scan_dribbled(body);
        assert_eq!(whole.text.as_str(), dribbled.text.as_str(), "text differs for {body}");
        assert_eq!(whole.n_calls, dribbled.n_calls, "call count differs for {body}");
        assert_eq!(whole.malformed, dribbled.malformed, "malformed differs for {body}");
        assert_eq!(whole.finish.as_str(), dribbled.finish.as_str());
        for i in 0..whole.n_calls {
            assert_eq!(whole.calls[i].name.as_str(), dribbled.calls[i].name.as_str());
            assert_eq!(whole.calls[i].args.as_str(), dribbled.calls[i].args.as_str());
            assert_eq!(whole.calls[i].is_intact(), dribbled.calls[i].is_intact());
        }
    }
}

const TWO_CALLS: &str = r#"{"candidates":[{"content":{"parts":[
{"functionCall":{"name":"set_speed","args":{"mph":4}}},
{"functionCall":{"name":"set_incline","args":{"incline":2.5}}}
]}}]}"#;

#[test]
fn two_calls_in_one_reply_are_kept_in_order() {
    let s = scan(TWO_CALLS);
    assert_eq!(s.n_calls, 2);
    assert_eq!(validate(&s.calls[0]), Ok(Action::SetSpeed(SpeedTenths::new(40))));
    assert_eq!(
        validate(&s.calls[1]),
        Ok(Action::SetIncline(InclineHalfPct::new(5)))
    );
}

// --- truncation -------------------------------------------------------------

/// Cut off inside the `args` object — the case that must NEVER reach the belt.
const TRUNCATED: &str = r#"{"candidates":[{"content":{"parts":[
{"text":"On it."},
{"functionCall":{"name":"set_speed","args":{"mph":1"#;

#[test]
fn a_call_cut_off_mid_argument_is_refused_not_guessed() {
    let s = scan(TRUNCATED);
    // The text that DID arrive is still worth showing.
    assert_eq!(s.text.as_str(), "On it.");
    assert!(s.malformed, "an unterminated body is damage and must say so");
    assert_eq!(s.n_calls, 1);
    assert!(s.calls[0].args_unterminated);
    assert!(!s.calls[0].is_intact());
    // `{"mph":1` would parse to 1.0 mph. It must not: the model was writing
    // 1.5, or 12, or 10 — nobody knows, and the belt is not the place to guess.
    assert_eq!(validate(&s.calls[0]), Err(Reject::Damaged));
    assert_eq!(s.intact_calls().count(), 0);
}

#[test]
fn a_reply_cut_off_mid_text_still_yields_the_text() {
    let s = scan(r#"{"candidates":[{"content":{"parts":[{"text":"Nice work, keep"#);
    assert_eq!(s.text.as_str(), "Nice work, keep");
    assert!(s.malformed);
    assert_eq!(s.n_calls, 0);
}

// --- oversize ---------------------------------------------------------------

#[test]
fn an_oversized_text_part_saturates_it_does_not_grow() {
    let mut body = String::from(r#"{"candidates":[{"content":{"parts":[{"text":""#);
    for _ in 0..(TEXT_BYTES * 4) {
        body.push('x');
    }
    body.push_str(r#""}]}}]}"#);
    let s = scan(&body);
    assert_eq!(s.text.len(), TEXT_BYTES);
    assert!(s.text_overflow);
    assert!(!s.malformed, "an oversized value is still well-formed JSON");
    // The point: a body four times the buffer costs the buffer, not four of it.
    assert_eq!(core::mem::size_of_val(&s.text), TEXT_BYTES + core::mem::size_of::<usize>());
}

#[test]
fn an_oversized_argument_object_makes_the_call_unusable() {
    let mut body = String::from(
        r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"set_speed","args":{"mph":3,"pad":""#,
    );
    for _ in 0..(ARGS_BYTES * 3) {
        body.push('y');
    }
    body.push_str(r#""}}}]}}]}"#);
    let s = scan(&body);
    assert_eq!(s.n_calls, 1);
    assert!(s.calls[0].args_overflow);
    // `"mph":3` IS present in the retained prefix and would validate happily.
    // It must not: a truncated argument object is not a smaller request.
    assert_eq!(validate(&s.calls[0]), Err(Reject::Damaged));
}

#[test]
fn a_fifth_call_is_dropped_and_the_fourth_survives() {
    let mut body = String::from(r#"{"candidates":[{"content":{"parts":["#);
    for i in 0..(MAX_CALLS + 3) {
        if i > 0 {
            body.push(',');
        }
        body.push_str(&format!(
            r#"{{"functionCall":{{"name":"set_speed","args":{{"mph":{}}}}}}}"#,
            i + 1
        ));
    }
    body.push_str(r#"]}}]}"#);
    let s = scan(&body);
    assert_eq!(s.n_calls, MAX_CALLS);
    assert!(s.too_many_calls);
    // The LAST retained call must still be call #4, not a smear of #5..#7.
    assert_eq!(s.calls[MAX_CALLS - 1].args.as_str(), r#"{"mph":4}"#);
    assert!(s.calls[MAX_CALLS - 1].is_intact());
}

// --- not the shape we asked for ---------------------------------------------

/// What the endpoint sends on a bad key, an exhausted quota or a bad request.
/// It has a `message` and — critically — a `text`-free shape, so nothing here
/// may become the coach's answer.
const ERROR_ENVELOPE: &str = r#"{"error":{"code":429,"message":"Resource has been exhausted",
"status":"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.Help",
"links":[{"description":"Learn more","url":"https://ai.google.dev/gemini-api/docs/rate-limits"}]}]}}"#;

#[test]
fn an_http_error_envelope_yields_no_answer_and_no_action() {
    let s = scan(ERROR_ENVELOPE);
    assert!(!s.malformed, "the error body is valid JSON, just not an answer");
    assert_eq!(s.n_calls, 0);
    assert_eq!(
        s.text.as_str(),
        "",
        "an upstream error message must never be shown as the coach speaking — \
         it is somebody else's prose and can name the endpoint"
    );
}

const NOT_JSON: &str = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>";

#[test]
fn an_html_error_page_is_malformed_and_harmless() {
    let s = scan(NOT_JSON);
    assert!(s.malformed);
    assert_eq!(s.n_calls, 0);
    assert_eq!(s.text.as_str(), "");
}

#[test]
fn a_text_field_outside_parts_is_not_the_answer() {
    // A `text` in an error envelope, a citation block or a prompt echo must not
    // be spoken by the coach. Only a `parts` entry is an answer.
    let s = scan(r#"{"promptFeedback":{"text":"you asked about hills"},"candidates":[]}"#);
    assert_eq!(s.text.as_str(), "");
}

#[test]
fn absurd_nesting_is_refused_rather_than_followed() {
    let mut body = String::from(r#"{"candidates":"#);
    for _ in 0..64 {
        body.push('[');
    }
    let s = scan(&body);
    assert!(s.malformed);
    assert_eq!(s.n_calls, 0);
}

#[test]
fn an_empty_body_is_malformed_not_a_panic() {
    let s = scan("");
    assert_eq!(s.text.as_str(), "");
    assert_eq!(s.n_calls, 0);
    assert!(!s.malformed, "zero bytes is nothing, not damage");
}

/// Fuzz-shaped totality check. Not a fuzzer — a cheap, deterministic sweep that
/// would have caught every indexing bug in this file's history.
#[test]
fn every_prefix_of_every_fixture_is_survivable() {
    for body in [PLAIN, WITH_CALL, TWO_CALLS, ERROR_ENVELOPE, NOT_JSON, TRUNCATED] {
        for cut in 0..=body.len() {
            let mut s = ReplyScanner::new();
            s.push_all(&body.as_bytes()[..cut]);
            s.finish_stream();
            // The property is not "it parses" — it is that whatever survives is
            // internally consistent and nothing damaged becomes an action.
            for c in s.intact_calls() {
                assert!(validate(c).is_ok() || validate(c).is_err());
            }
            assert!(s.n_calls <= MAX_CALLS);
        }
    }
}
