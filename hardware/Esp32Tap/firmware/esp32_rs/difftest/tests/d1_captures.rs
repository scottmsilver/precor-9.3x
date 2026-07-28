//! D1 — the two KV parsers over REAL captured wire data.
//!
//! Fixtures in `difftest/fixtures/` are decoded byte streams from
//! `cpp/captures/try*.csv` — genuine pin-6 (console) and pin-3 (motor) traffic
//! from a real Precor 9.31, dumped via the committed harness helper
//! `capture_streams.py` (regenerate with `tools/dump_capture_fixtures.py`).
//!
//! The interesting axis is CHUNKING. A batch parser and a streaming one agree
//! trivially when fed a whole buffer; they diverge at chunk boundaries, which
//! is precisely where a rewrite goes wrong. Each stream is therefore replayed
//! through the same buffered-reader discipline the firmware's `SerialReader`
//! uses (append, parse, shift unconsumed to the front) at 1, 3, 63, 64, 65 and
//! 512 bytes per read, plus random splits.
//!
//! Also replays each stream through BOTH `SafetyController::observe_console_bytes`
//! implementations, which is a genuinely different scanner (candidate-based,
//! 100-byte cap, stricter key grammar) from `kv_parse`.

use difftest::cpp;
use difftest::gen::{chunkings, Rng};
use safety_core::kv as rs;
use safety_core::safety::controller::SafetyController;
use safety_core::units::Micros;
use std::path::PathBuf;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn load(name: &str) -> Vec<u8> {
    let p = fixtures_dir().join(name);
    std::fs::read(&p).unwrap_or_else(|e| {
        panic!(
            "missing capture fixture {}: {e}\nRegenerate with: python3 tools/dump_capture_fixtures.py",
            p.display()
        )
    })
}

fn fixture_names() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(fixtures_dir())
        .expect("fixtures dir must exist")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".bin"))
        .collect();
    names.sort();
    names
}

/// One buffered streaming pass, mirroring `SerialReader::poll`: append the
/// chunk to a parse buffer, parse, then shift the unconsumed tail to the front.
struct RustStream {
    buf: Vec<u8>,
    pairs: Vec<(String, String)>,
}

impl RustStream {
    fn new() -> Self {
        RustStream {
            buf: Vec::new(),
            pairs: Vec::new(),
        }
    }
    fn feed(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
        let mut out = [rs::KvPair::empty(); 32];
        let r = rs::kv_parse(&self.buf, &mut out);
        for p in &out[..r.n] {
            self.pairs
                .push((p.key.as_str().to_string(), p.value.as_str().to_string()));
        }
        self.buf.drain(..r.consumed);
    }
    /// Keep parsing until no further progress — one `feed` yields at most
    /// `max_pairs` (32) pairs, exactly like one `SerialReader::poll`.
    fn drain(&mut self) {
        loop {
            let before = (self.buf.len(), self.pairs.len());
            self.feed(&[]);
            if (self.buf.len(), self.pairs.len()) == before {
                break;
            }
        }
    }
}

struct CppStream {
    buf: Vec<u8>,
    pairs: Vec<(String, String)>,
}

impl CppStream {
    fn new() -> Self {
        CppStream {
            buf: Vec::new(),
            pairs: Vec::new(),
        }
    }
    fn feed(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
        let r = cpp::kv_parse(&self.buf, 32);
        self.pairs.extend(r.pairs);
        self.buf.drain(..r.consumed);
    }
    fn drain(&mut self) {
        loop {
            let before = (self.buf.len(), self.pairs.len());
            self.feed(&[]);
            if (self.buf.len(), self.pairs.len()) == before {
                break;
            }
        }
    }
}

#[test]
fn d1_kv_parse_matches_cpp_over_real_captures_all_chunkings() {
    let mut rng = Rng::new(0xCA97_0000_0000_0001);
    let names = fixture_names();
    assert!(
        names.len() >= 4,
        "expected several capture fixtures, found {names:?}"
    );

    let mut total_pairs = 0usize;
    for name in &names {
        let data = load(name);
        assert!(!data.is_empty(), "{name} is empty");

        // Whole-buffer baseline.
        let mut base_rs = RustStream::new();
        base_rs.feed(&data);
        base_rs.drain();
        let mut base_cpp = CppStream::new();
        base_cpp.feed(&data);
        base_cpp.drain();
        assert_eq!(
            base_rs.pairs, base_cpp.pairs,
            "{name}: whole-buffer parse diverged"
        );
        assert_eq!(
            base_rs.buf, base_cpp.buf,
            "{name}: whole-buffer residue diverged"
        );
        total_pairs += base_rs.pairs.len();

        for (ci, chunking) in chunkings(&mut rng, data.len()).into_iter().enumerate() {
            let mut r = RustStream::new();
            let mut c = CppStream::new();
            let mut off = 0usize;
            for (step, n) in chunking.iter().enumerate() {
                let chunk = &data[off..off + n];
                r.feed(chunk);
                c.feed(chunk);
                assert_eq!(
                    r.pairs, c.pairs,
                    "{name}: chunking #{ci} diverged in pairs after step {step} (offset {off}, chunk {n})"
                );
                assert_eq!(
                    r.buf, c.buf,
                    "{name}: chunking #{ci} diverged in buffered residue after step {step}"
                );
                off += n;
            }
            r.drain();
            c.drain();
            assert_eq!(
                r.pairs, c.pairs,
                "{name}: chunking #{ci} diverged after final drain"
            );
            assert_eq!(r.buf, c.buf, "{name}: chunking #{ci} residue diverged after final drain");
            assert_eq!(
                r.pairs, base_rs.pairs,
                "{name}: chunking #{ci} produced a different pair sequence than the whole-buffer baseline"
            );
            assert_eq!(off, data.len());
        }
    }
    assert!(
        total_pairs > 1_000,
        "captures yielded only {total_pairs} KV pairs — fixtures look wrong"
    );
    println!("D1: {} fixtures, {total_pairs} KV pairs, all chunkings agree", names.len());
}

#[test]
fn d1_observe_console_bytes_matches_cpp_over_real_captures() {
    // The controller's console scanner is a DIFFERENT parser from kv_parse:
    // candidate-based, 100-byte cap, key grammar [A-Za-z][A-Za-z0-9_]{0,31}.
    // Drive it with real console traffic at a realistic timestamp cadence.
    let mut rng = Rng::new(0xCA97_0000_0000_0002);
    for name in fixture_names() {
        let data = load(&name);

        for (ci, chunking) in chunkings(&mut rng, data.len()).into_iter().enumerate() {
            let mut r = SafetyController::new();
            let mut c = cpp::CppController::new();
            let mut off = 0usize;
            // Advance time slowly enough that the 1.5 s console-freshness
            // deadline never fires (both are in PROXY, where it is not
            // enforced anyway) — this isolates the SCANNER.
            let mut now = 0i64;
            for (step, n) in chunking.iter().enumerate() {
                let chunk = &data[off..off + n];
                let rn = r.observe_console_bytes(chunk, Micros::new(now));
                let cn = c.observe_console_bytes(chunk, now);
                assert_eq!(
                    rn, cn,
                    "{name}: chunking #{ci} step {step}: complete-frame count diverged"
                );
                let rs_ts = r.last_complete_console_frame_at().map(|m| m.get());
                let cpp_ts = c.state().last_frame_at;
                assert_eq!(
                    rs_ts, cpp_ts,
                    "{name}: chunking #{ci} step {step}: freshness timestamp diverged"
                );
                assert_eq!(
                    r.event_count(),
                    c.state().event_count,
                    "{name}: chunking #{ci} step {step}: event count diverged"
                );
                off += n;
                now += 1_000;
            }
        }
    }
}
