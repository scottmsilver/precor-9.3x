//! D2 — structure-aware fuzz + full-domain codec differential.
//!
//! Asserts the Rust and the COMMITTED C++ implementations agree on:
//!   * `kv_parse` over generated adversarial buffers (pair count, consumed
//!     count, and every key/value byte);
//!   * `kv_build` over generated key/value pairs;
//!   * `encode/decode_speed_hex` over the full 0..=120 domain and
//!     `encode/decode_incline_hex` over 0..=198, plus the overflow guards.
//!
//! Any divergence is a hard failure and must be classified in the report as
//! BUG-RUST / BUG-CPP / IMPROVEMENT / MODEL-QUIRK.

use difftest::cpp;
use difftest::gen::{gen_buffer, Rng, INTERESTING_LENS};
use safety_core::kv as rs;
use safety_core::units::{InclineHalfPct, SpeedTenths};

/// Reduce a fuzz failure to a minimal reproducer by truncation bisection.
fn shrink(buf: &[u8], fails: impl Fn(&[u8]) -> bool) -> Vec<u8> {
    let mut best = buf.to_vec();
    let mut changed = true;
    while changed {
        changed = false;
        // Try dropping a prefix, then a suffix.
        for take_front in [true, false] {
            let mut i = 1;
            while i < best.len() {
                let cand: Vec<u8> = if take_front {
                    best[i..].to_vec()
                } else {
                    best[..best.len() - i].to_vec()
                };
                if fails(&cand) {
                    best = cand;
                    changed = true;
                    break;
                }
                i *= 2;
            }
        }
    }
    best
}

fn rust_parse(buf: &[u8], max_pairs: usize) -> (Vec<(String, String)>, usize) {
    let mut pairs = vec![rs::KvPair::empty(); max_pairs.max(1)];
    let r = rs::kv_parse(buf, &mut pairs[..max_pairs]);
    let out = pairs[..r.n]
        .iter()
        .map(|p| (p.key.as_str().to_string(), p.value.as_str().to_string()))
        .collect();
    (out, r.consumed)
}

fn parse_diverges(buf: &[u8]) -> bool {
    for &max_pairs in &[1usize, 2, 4, 32] {
        let (rp, rc) = rust_parse(buf, max_pairs);
        let c = cpp::kv_parse(buf, max_pairs);
        if rp != c.pairs || rc != c.consumed {
            return true;
        }
    }
    false
}

#[test]
fn d2_kv_parse_matches_cpp_over_structured_fuzz() {
    let mut rng = Rng::new(0xD1FF_7E57_0000_0001);
    for i in 0..20_000u64 {
        let buf = gen_buffer(&mut rng, 300);
        if parse_diverges(&buf) {
            let min = shrink(&buf, parse_diverges);
            let (rp, rc) = rust_parse(&min, 32);
            let c = cpp::kv_parse(&min, 32);
            panic!(
                "D2 kv_parse DIVERGENCE at case {i}\n  input (shrunk): {min:?}\n\
                   rust: pairs={rp:?} consumed={rc}\n  cpp : pairs={:?} consumed={}",
                c.pairs, c.consumed
            );
        }
    }
}

/// Rust `kv_build` returns `None` rather than silently truncating past
/// `KV_FRAME_CAPACITY`; C++ `std::string` just grows. Wherever the frame FITS
/// the two must be byte-identical, and wherever it does not, Rust must refuse
/// rather than emit a frame missing its `0xFF` delimiter.
///
/// This asymmetry was FOUND BY THIS TEST (see the note on `kv_build`): the
/// first version of the Rust builder silently dropped the trailing delimiter
/// on a 137-byte frame. Classification: BUG-RUST, fixed; the residual
/// difference is an IMPROVEMENT over C++, and unreachable from the firmware
/// either way (every real call site is a <=4-byte key with a <=4-byte value,
/// and `MAX_WRITE_BYTES` is 50).
fn check_build(key: &str, value: &str, ctx: &str) {
    let c = cpp::kv_build(key, value);
    match rs::kv_build(key, value) {
        Some(r) => assert_eq!(r.as_bytes(), c.as_slice(), "kv_build {ctx}"),
        None => assert!(
            c.len() > rs::KV_FRAME_CAPACITY,
            "kv_build {ctx}: Rust refused a frame that fits in {} bytes (cpp len {})",
            rs::KV_FRAME_CAPACITY,
            c.len()
        ),
    }
}

#[test]
fn d2_kv_build_matches_cpp() {
    let mut rng = Rng::new(0xD1FF_7E57_0000_0002);
    // Deterministic boundary sweep first: field lengths at 63/64/65 etc.
    for &klen in INTERESTING_LENS {
        for &vlen in INTERESTING_LENS {
            let key = "k".repeat(klen.max(1));
            let value = "v".repeat(vlen);
            check_build(&key, &value, &format!("klen={klen} vlen={vlen}"));
        }
    }
    // Then random keys/values.
    for _ in 0..5_000 {
        let klen = 1 + rng.below(70);
        let vlen = rng.below(70);
        let key: String = (0..klen)
            .map(|_| *rng.pick(b"abcdefghijklmnopqrstuvwxyz_0123456789") as char)
            .collect();
        let value: String = (0..vlen)
            .map(|_| *rng.pick(b"0123456789ABCDEF.- ") as char)
            .collect();
        check_build(&key, &value, &format!("key={key:?} value={value:?}"));
    }
    // Every frame the PARSER can produce must round-trip through the builder:
    // 63-byte key + 63-byte value is the true worst case on the wire.
    check_build(&"k".repeat(63), &"v".repeat(63), "maximal KvField pair");
}

#[test]
fn d2_speed_codec_full_domain_matches_cpp() {
    // Full valid domain.
    for t in 0..=120i32 {
        let r = rs::encode_speed_hex(SpeedTenths::new(t));
        let c = cpp::encode_speed_hex(t);
        assert_eq!(r.as_str(), c, "encode_speed_hex({t})");
        let rd = rs::decode_speed_hex(&c).map(|v| v.get()).unwrap_or(-1);
        let cd = cpp::decode_speed_hex(&c);
        assert_eq!(rd, cd, "decode_speed_hex({c:?})");
    }
    // Out-of-domain encodes (the encoder has no clamp in either implementation).
    for t in [-1000i32, -1, 121, 500, 5000, i32::MAX / 100] {
        assert_eq!(
            rs::encode_speed_hex(SpeedTenths::new(t)).as_str(),
            cpp::encode_speed_hex(t),
            "encode_speed_hex({t}) out of domain"
        );
    }
}

#[test]
fn d2_incline_codec_full_domain_matches_cpp() {
    for hp in 0..=198i32 {
        let r = rs::encode_incline_hex(InclineHalfPct::new(hp));
        let c = cpp::encode_incline_hex(hp);
        assert_eq!(r.as_str(), c, "encode_incline_hex({hp})");
        let rd = rs::decode_incline_hex(&c).map(|v| v.get()).unwrap_or(-1);
        let cd = cpp::decode_incline_hex(&c);
        assert_eq!(rd, cd, "decode_incline_hex({c:?})");
    }
    for hp in [-500i32, -1, 199, 1000, 100_000] {
        assert_eq!(
            rs::encode_incline_hex(InclineHalfPct::new(hp)).as_str(),
            cpp::encode_incline_hex(hp),
            "encode_incline_hex({hp}) out of domain"
        );
    }
}

#[test]
fn d2_decode_guards_match_cpp_over_fuzzed_strings() {
    let mut rng = Rng::new(0xD1FF_7E57_0000_0003);
    // Explicit guard vectors first (len > 10, > 5000 / > 500 caps).
    let fixed = [
        "", "0", "00", "F", "4B0", "1F4", "10000", "1000", "500", "501", "1F5", "FFFFFFFF",
        "FFFFFFFFFF", "FFFFFFFFFFF", "FFFFFFFFFFFFFFFF", "-1", "+1", "0x10", " 10", "10 ", "g",
        "1.5", "١٢٣",
    ];
    for s in fixed {
        let rs_s = rs::decode_speed_hex(s).map(|v| v.get()).unwrap_or(-1);
        assert_eq!(rs_s, cpp::decode_speed_hex(s), "decode_speed_hex({s:?})");
        let rs_i = rs::decode_incline_hex(s).map(|v| v.get()).unwrap_or(-1);
        assert_eq!(rs_i, cpp::decode_incline_hex(s), "decode_incline_hex({s:?})");
    }
    // Then random strings over a hex-biased alphabet, including over-length.
    for _ in 0..50_000 {
        let n = rng.below(14);
        let s: String = (0..n)
            .map(|_| *rng.pick(b"0123456789ABCDEFabcdefxX+- gG") as char)
            .collect();
        let rs_s = rs::decode_speed_hex(&s).map(|v| v.get()).unwrap_or(-1);
        assert_eq!(rs_s, cpp::decode_speed_hex(&s), "decode_speed_hex({s:?})");
        let rs_i = rs::decode_incline_hex(&s).map(|v| v.get()).unwrap_or(-1);
        assert_eq!(
            rs_i,
            cpp::decode_incline_hex(&s),
            "decode_incline_hex({s:?})"
        );
    }
}
