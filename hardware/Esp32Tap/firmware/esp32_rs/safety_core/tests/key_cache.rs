//! Port of `host/tests/test_key_cache.cpp` — 8 cases, 1:1 by name,
//! PLUS one extra property test (the 149th case) replacing the single
//! deliberately non-ported ASSERTION. See `key_cache_prev_value_survives_...`.

use safety_core::key_cache::KeyCache;
use safety_core::kv::KV_FIELD_SIZE;
use safety_core::mode::{ModeStateMachine, TransitionResult};

// cpp: "first sighting of a tracked key returns empty and stores it"
#[test]
fn first_sighting_of_a_tracked_key_returns_empty_and_stores_it() {
    let mut cache = KeyCache::new();

    let prev = cache.exchange("hmph", "78");
    assert!(prev.is_empty());

    let prev = cache.exchange("hmph", "78");
    assert_eq!(prev, *"78");
}

// cpp: "exchange returns the previous value in the caller's buffer"
//
// The BEHAVIORAL half ports unchanged and keeps its name.
//
// NOT PORTED: the C++-only aliasing assertion `prev.data() == buf.data()`.
// That assertion exists to prove the returned `string_view` aliases the
// CALLER's buffer rather than internal storage or a destroyed local — the
// regression guard for a real [high] dangling-view finding. `PrevValue` is
// owned and `Copy`, so there is no view, no caller buffer, and the hazard is
// structurally impossible. This is the ONE assertion in the 148 whose subject
// is a C++-only hazard. The replacement property is the extra test below.
#[test]
fn exchange_returns_the_previous_value_in_the_caller_s_buffer() {
    let mut cache = KeyCache::new();

    let _ = cache.exchange("hmph", "78");
    let prev = cache.exchange("hmph", "A0");
    assert_eq!(prev, *"78");

    let prev = cache.exchange("hmph", "B4");
    assert_eq!(prev, *"A0");
}

// cpp: "hmph and inc are tracked independently"
#[test]
fn hmph_and_inc_are_tracked_independently() {
    let mut cache = KeyCache::new();

    let _ = cache.exchange("hmph", "78");
    let _ = cache.exchange("inc", "A");

    assert_eq!(cache.exchange("hmph", "78"), *"78");
    assert_eq!(cache.exchange("inc", "1E"), *"A");
    assert_eq!(cache.exchange("inc", "1E"), *"1E");
}

// cpp: "untracked keys return empty and do not disturb the cache"
#[test]
fn untracked_keys_return_empty_and_do_not_disturb_the_cache() {
    let mut cache = KeyCache::new();

    let _ = cache.exchange("hmph", "78");
    assert!(cache.exchange("belt", "1").is_empty());
    assert!(cache.exchange("loop", "5550").is_empty());
    assert!(cache.exchange("", "").is_empty());
    // hmph is untouched by the untracked exchanges above.
    assert_eq!(cache.exchange("hmph", "79"), *"78");
}

// cpp: "oversized values are truncated to KV_FIELD_SIZE - 1"
#[test]
fn oversized_values_are_truncated_to_kv_field_size_1() {
    let mut cache = KeyCache::new();

    let big = "X".repeat(200);
    let _ = cache.exchange("hmph", &big);
    let prev = cache.exchange("hmph", "0");
    assert_eq!(prev.len(), KV_FIELD_SIZE - 1);
    assert_eq!(prev.as_str(), "X".repeat(KV_FIELD_SIZE - 1));
}

/// Mirrors the serial engine task's `on_kv` wiring: exchange, then feed
/// prev/new into the mode machine. This is the NORMATIVE console-takeover
/// path — a console button press while emulating must stop emulation.
struct TakeoverHarness {
    cache: KeyCache,
    mode: ModeStateMachine,
}

impl TakeoverHarness {
    fn new() -> Self {
        TakeoverHarness {
            cache: KeyCache::new(),
            mode: ModeStateMachine::new(),
        }
    }
    fn feed(&mut self, key: &str, value: &str) -> TransitionResult {
        let prev = self.cache.exchange(key, value);
        // `prev` is owned, so it stays valid across this call by construction.
        self.mode
            .auto_proxy_on_console_change(key, prev.as_str(), value)
    }
}

// cpp: "console takeover: hmph change while emulating stops emulation"
#[test]
fn console_takeover_hmph_change_while_emulating_stops_emulation() {
    let mut h = TakeoverHarness::new();

    // Baseline value observed while still in proxy.
    let r = h.feed("hmph", "78");
    assert!(!r.emulate_stopped);

    h.mode.request_emulate(true);
    assert!(h.mode.is_emulating());

    // Same value repeated: not a button press, stay emulating.
    let r = h.feed("hmph", "78");
    assert!(!r.emulate_stopped);
    assert!(h.mode.is_emulating());

    // Value change: console button press — emulation must stop.
    let r = h.feed("hmph", "A0");
    assert!(r.emulate_stopped);
    assert!(!h.mode.is_emulating());
}

// cpp: "console takeover: inc change while emulating stops emulation"
#[test]
fn console_takeover_inc_change_while_emulating_stops_emulation() {
    let mut h = TakeoverHarness::new();
    let _ = h.feed("inc", "A");
    h.mode.request_emulate(true);
    assert!(h.mode.is_emulating());

    let r = h.feed("inc", "B");
    assert!(r.emulate_stopped);
    assert!(!h.mode.is_emulating());
}

// cpp: "console takeover: first-ever value while emulating does not trigger"
#[test]
fn console_takeover_first_ever_value_while_emulating_does_not_trigger() {
    let mut h = TakeoverHarness::new();
    h.mode.request_emulate(true);
    assert!(h.mode.is_emulating());

    // No previous value cached: an empty old_val is "first value", not a change.
    let r = h.feed("hmph", "78");
    assert!(!r.emulate_stopped);
    assert!(h.mode.is_emulating());

    // Untracked key changes never trigger either.
    let r = h.feed("belt", "1");
    assert!(!r.emulate_stopped);
    assert!(h.mode.is_emulating());
}

// EXTRA (not in the C++ 148) — the replacement for the non-ported aliasing
// assertion above. Takes a `PrevValue`, mutates the cache twice more, and
// only THEN uses it in the safety-relevant `auto_proxy_on_console_change`
// call. Under the C++ contract this is exactly the sequence a dangling view
// would corrupt; here it is a compile-time-guaranteed non-event.
#[test]
fn key_cache_prev_value_survives_auto_proxy() {
    let mut cache = KeyCache::new();
    let mut mode = ModeStateMachine::new();

    let _ = cache.exchange("hmph", "78");
    let prev = cache.exchange("hmph", "A0"); // prev == "78"

    // Mutate the cache repeatedly AFTER capturing `prev`.
    let _ = cache.exchange("hmph", "FF");
    let _ = cache.exchange("inc", "1E");
    let _ = cache.exchange("hmph", "00");

    assert_eq!(prev, *"78", "PrevValue is owned; later writes cannot touch it");

    mode.request_emulate(true);
    let r = mode.auto_proxy_on_console_change("hmph", prev.as_str(), "A0");
    assert!(r.emulate_stopped);
    assert!(!mode.is_emulating());
}
