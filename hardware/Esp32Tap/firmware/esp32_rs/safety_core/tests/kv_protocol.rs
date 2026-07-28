//! Port of `host/tests/test_kv_protocol.cpp` — 34 cases, 1:1 by name.
//!
//! Naming rule: the doctest case name, lowercased with every non-alphanumeric
//! run collapsed to `_`. `tools/check_case_parity.py` asserts set equality
//! against the C++ file, so a dropped or renamed case is a gate failure.

use safety_core::kv::*;
use safety_core::units::{InclineHalfPct, SpeedTenths};

fn tenths(v: i32) -> SpeedTenths {
    SpeedTenths::new(v)
}
fn half(v: i32) -> InclineHalfPct {
    InclineHalfPct::new(v)
}

// ── kv_parse ────────────────────────────────────────────────────────

// cpp: "kv_parse: basic key:value pair"
#[test]
fn kv_parse_basic_key_value_pair() {
    let data = b"[hmph:78]";
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 1);
    assert_eq!(pairs[0].key, *"hmph");
    assert_eq!(pairs[0].value, *"78");
    assert_eq!(r.consumed, 9);
}

// cpp: "kv_parse: bare key without value"
#[test]
fn kv_parse_bare_key_without_value() {
    let data = b"[amps]";
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 1);
    assert_eq!(pairs[0].key, *"amps");
    assert_eq!(pairs[0].value, *"");
}

// cpp: "kv_parse: multiple pairs with 0xFF delimiter"
#[test]
fn kv_parse_multiple_pairs_with_0xff_delimiter() {
    let data = b"[inc:5]\xff[hmph:78]\xff";
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 2);
    assert_eq!(pairs[0].key, *"inc");
    assert_eq!(pairs[0].value, *"5");
    assert_eq!(pairs[1].key, *"hmph");
    assert_eq!(pairs[1].value, *"78");
}

// cpp: "kv_parse: skips 0x00 and 0xFF delimiters"
#[test]
fn kv_parse_skips_0x00_and_0xff_delimiters() {
    let data = [0xFFu8, 0x00, b'[', b'k', b':', b'v', b']', 0xFF, 0x00];
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(&data, &mut pairs);

    assert_eq!(r.n, 1);
    assert_eq!(pairs[0].key, *"k");
    assert_eq!(pairs[0].value, *"v");
}

// cpp: "kv_parse: incomplete frame preserves bytes"
#[test]
fn kv_parse_incomplete_frame_preserves_bytes() {
    let data = b"[hmph:7"; // no closing bracket
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 0);
    assert!(r.consumed < data.len()); // not all consumed
}

// cpp: "kv_parse: rejects non-printable content"
#[test]
fn kv_parse_rejects_non_printable_content() {
    let data = [b'[', b'k', b':', 0x01, b']'];
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(&data, &mut pairs);

    assert_eq!(r.n, 0);
}

// cpp: "kv_parse: max_pairs limit respected"
#[test]
fn kv_parse_max_pairs_limit_respected() {
    let data = b"[a:1][b:2][c:3]";
    let mut pairs = [KvPair::empty(); 2];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 2);
    assert_eq!(pairs[0].key, *"a");
    assert_eq!(pairs[1].key, *"b");
}

// cpp: "kv_parse: empty input"
#[test]
fn kv_parse_empty_input() {
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(&[], &mut pairs);

    assert_eq!(r.n, 0);
    assert_eq!(r.consumed, 0);
}

// cpp: "kv_parse: garbage between valid frames"
#[test]
fn kv_parse_garbage_between_valid_frames() {
    let data = b"xyz[a:1]garbage[b:2]";
    let mut pairs = [KvPair::empty(); 4];
    let r = kv_parse(data, &mut pairs);

    assert_eq!(r.n, 2);
    assert_eq!(pairs[0].key, *"a");
    assert_eq!(pairs[1].key, *"b");
}

// ── kv_build ────────────────────────────────────────────────────────

// cpp: "kv_build: key with value"
#[test]
fn kv_build_key_with_value() {
    let result = kv_build("inc", "5").expect("fits");

    assert_eq!(result.len(), 8); // "[inc:5]" (7) + 0xFF (1)
    assert_eq!(&result.as_bytes()[..7], b"[inc:5]");
    assert_eq!(result.as_bytes()[7], 0xFF);
}

// cpp: "kv_build: bare key"
#[test]
fn kv_build_bare_key() {
    let result = kv_build_bare("amps").expect("fits");

    assert_eq!(result.len(), 7); // "[amps]" + 0xFF
    assert_eq!(&result.as_bytes()[..6], b"[amps]");
    assert_eq!(result.as_bytes()[6], 0xFF);
}

// cpp: "kv_build: empty value treated as bare key"
#[test]
fn kv_build_empty_value_treated_as_bare_key() {
    let result = kv_build("amps", "").expect("fits");

    assert_eq!(result.len(), 7);
    assert_eq!(&result.as_bytes()[..6], b"[amps]");
    assert_eq!(result.as_bytes()[6], 0xFF);
}

// ── speed hex ───────────────────────────────────────────────────────

// cpp: "encode_speed_hex: 1.2 mph = 12 tenths -> 120 hundredths = 0x78"
#[test]
fn encode_speed_hex_1_2_mph_12_tenths_120_hundredths_0x78() {
    assert_eq!(encode_speed_hex(tenths(12)), *"78");
}

// cpp: "encode_speed_hex: 12.0 mph = 120 tenths -> 1200 hundredths = 0x4B0"
#[test]
fn encode_speed_hex_12_0_mph_120_tenths_1200_hundredths_0x4b0() {
    assert_eq!(encode_speed_hex(tenths(120)), *"4B0"); // uppercase
}

// cpp: "encode_speed_hex: 0 mph"
#[test]
fn encode_speed_hex_0_mph() {
    assert_eq!(encode_speed_hex(tenths(0)), *"0");
}

// cpp: "decode_speed_hex: 78 -> 12 tenths (1.2 mph)"
#[test]
fn decode_speed_hex_78_12_tenths_1_2_mph() {
    assert_eq!(decode_speed_hex("78"), Some(tenths(12)));
}

// cpp: "decode_speed_hex: 4B0 -> 120 tenths (12.0 mph)"
#[test]
fn decode_speed_hex_4b0_120_tenths_12_0_mph() {
    assert_eq!(decode_speed_hex("4B0"), Some(tenths(120)));
}

// cpp: "decode_speed_hex: 0 -> 0"
#[test]
fn decode_speed_hex_0_0() {
    assert_eq!(decode_speed_hex("0"), Some(tenths(0)));
}

// cpp: "decode_speed_hex: empty string -> -1"
#[test]
fn decode_speed_hex_empty_string_1() {
    // C++ returns -1; the Rust signature makes the failure a `None`.
    assert_eq!(decode_speed_hex(""), None);
}

// cpp: "encode/decode round-trip"
#[test]
fn encode_decode_round_trip() {
    for t in 0..=120 {
        let enc = encode_speed_hex(tenths(t));
        assert_eq!(decode_speed_hex(enc.as_str()), Some(tenths(t)), "t={t}");
    }
}

// ── incline hex ─────────────────────────────────────────────────────

// cpp: "encode_incline_hex: 0 half-pct (0%) -> 0x0"
#[test]
fn encode_incline_hex_0_half_pct_0_0x0() {
    assert_eq!(encode_incline_hex(half(0)), *"0");
}

// cpp: "encode_incline_hex: 10 half-pct (5%) -> 0xA"
#[test]
fn encode_incline_hex_10_half_pct_5_0xa() {
    assert_eq!(encode_incline_hex(half(10)), *"A");
}

// cpp: "encode_incline_hex: 30 half-pct (15%) -> 0x1E"
#[test]
fn encode_incline_hex_30_half_pct_15_0x1e() {
    assert_eq!(encode_incline_hex(half(30)), *"1E");
}

// cpp: "encode_incline_hex: 14 half-pct (7%) -> 0xE"
#[test]
fn encode_incline_hex_14_half_pct_7_0xe() {
    assert_eq!(encode_incline_hex(half(14)), *"E");
}

// cpp: "encode_incline_hex: 1 half-pct (0.5%) -> 0x1"
#[test]
fn encode_incline_hex_1_half_pct_0_5_0x1() {
    assert_eq!(encode_incline_hex(half(1)), *"1");
}

// cpp: "decode_incline_hex: A -> 10 half-pct (5%)"
#[test]
fn decode_incline_hex_a_10_half_pct_5() {
    assert_eq!(decode_incline_hex("A"), Some(half(10)));
}

// cpp: "decode_incline_hex: 1E -> 30 half-pct (15%)"
#[test]
fn decode_incline_hex_1e_30_half_pct_15() {
    assert_eq!(decode_incline_hex("1E"), Some(half(30)));
}

// cpp: "decode_incline_hex: 0 -> 0 half-pct"
#[test]
fn decode_incline_hex_0_0_half_pct() {
    assert_eq!(decode_incline_hex("0"), Some(half(0)));
}

// cpp: "decode_incline_hex: 1 -> 1 half-pct (0.5%)"
#[test]
fn decode_incline_hex_1_1_half_pct_0_5() {
    assert_eq!(decode_incline_hex("1"), Some(half(1)));
}

// cpp: "decode_incline_hex: B -> 11 half-pct (5.5%)"
#[test]
fn decode_incline_hex_b_11_half_pct_5_5() {
    assert_eq!(decode_incline_hex("B"), Some(half(11)));
}

// cpp: "decode_incline_hex: empty string -> -1"
#[test]
fn decode_incline_hex_empty_string_1() {
    assert_eq!(decode_incline_hex(""), None);
}

// cpp: "encode/decode incline round-trip (half-pct)"
#[test]
fn encode_decode_incline_round_trip_half_pct() {
    for hp in 0..=198 {
        let enc = encode_incline_hex(half(hp));
        assert_eq!(decode_incline_hex(enc.as_str()), Some(half(hp)), "hp={hp}");
    }
}

// ── overflow guards ─────────────────────────────────────────────────

// cpp: "decode_speed_hex: huge value returns -1"
#[test]
fn decode_speed_hex_huge_value_returns_1() {
    assert_eq!(decode_speed_hex("FFFFFFFF"), None);
    assert_eq!(decode_speed_hex("FFFFFFFFFFFFFFFF"), None); // len > 10
    assert_eq!(decode_speed_hex("10000"), None); // 65536 > 5000 cap
}

// cpp: "decode_incline_hex: huge value returns -1"
#[test]
fn decode_incline_hex_huge_value_returns_1() {
    assert_eq!(decode_incline_hex("FFFFFFFF"), None);
    assert_eq!(decode_incline_hex("FFFFFFFFFFFFFFFF"), None); // len > 10
    assert_eq!(decode_incline_hex("1000"), None); // 4096 > 500 cap
}
