//! Deterministic, structure-aware input generation.
//!
//! Written ourselves (~140 lines) instead of `arbitrary`/`proptest`. Generic
//! shrinking buys little here — what actually finds parser divergence is
//! BIASING toward the KV grammar's boundaries (`[`, `]`, `:`, `\xff`, `\x00`,
//! non-printables, and field lengths at 63/64/65 / candidate lengths at
//! 99/100/101/102), which a generic generator will essentially never hit.
//!
//! Fully deterministic: every suite seeds explicitly, so a failure reproduces.

/// xorshift64* — a few lines, no dependency, good enough for fuzz selection.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(if seed == 0 { 0x9E3779B97F4A7C15 } else { seed })
    }
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }
    pub fn below(&mut self, n: usize) -> usize {
        if n == 0 {
            0
        } else {
            (self.next_u64() % n as u64) as usize
        }
    }
    pub fn pick<'a, T>(&mut self, items: &'a [T]) -> &'a T {
        &items[self.below(items.len())]
    }
    pub fn bool(&mut self) -> bool {
        self.next_u64() & 1 == 1
    }
    /// Inclusive range.
    pub fn range_i64(&mut self, lo: i64, hi: i64) -> i64 {
        if hi <= lo {
            return lo;
        }
        lo + (self.next_u64() % ((hi - lo + 1) as u64)) as i64
    }
}

/// Bytes the KV grammar treats specially, plus the ones that historically
/// broke framing on the real bus.
pub const INTERESTING_BYTES: &[u8] = &[
    b'[', b']', b':', 0xFF, 0x00, 0x01, 0x1F, 0x20, 0x7E, 0x7F, 0x80, b'a', b'Z', b'0', b'9', b'_',
    b'-', b' ',
];

/// Field/candidate lengths sitting exactly on the implementation's cliffs:
/// `KV_FIELD_SIZE` 63/64/65, and the console candidate cap 99/100/101/102.
pub const INTERESTING_LENS: &[usize] = &[0, 1, 2, 31, 32, 33, 62, 63, 64, 65, 99, 100, 101, 102];

/// Generate one structure-aware buffer: a mix of well-formed frames,
/// almost-well-formed frames, and raw interesting bytes.
pub fn gen_buffer(rng: &mut Rng, max_len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(max_len);
    let chunks = 1 + rng.below(8);
    for _ in 0..chunks {
        if out.len() >= max_len {
            break;
        }
        match rng.below(10) {
            // A well-formed [key:value] frame.
            0..=3 => {
                let kcap = *rng.pick(INTERESTING_LENS);
                let klen = 1 + rng.below(kcap + 1);
                let vlen = *rng.pick(INTERESTING_LENS);
                out.push(b'[');
                for _ in 0..klen {
                    out.push(*rng.pick(b"abcdefghijklmnopqrstuvwxyzABCDEZ0123456789_"));
                }
                if rng.bool() {
                    out.push(b':');
                    for _ in 0..vlen {
                        out.push(*rng.pick(b"0123456789ABCDEFabcdef.- "));
                    }
                }
                out.push(b']');
                if rng.bool() {
                    out.push(0xFF);
                }
            }
            // A frame with a corrupt byte inside.
            4..=5 => {
                out.push(b'[');
                let n = 1 + rng.below(12);
                for _ in 0..n {
                    out.push(*rng.pick(INTERESTING_BYTES));
                }
                if rng.bool() {
                    out.push(b']');
                }
            }
            // An unterminated frame (exercises the incomplete-frame path).
            6 => {
                out.push(b'[');
                let n = rng.below(8);
                for _ in 0..n {
                    out.push(*rng.pick(b"abc:123"));
                }
            }
            // Raw interesting bytes.
            _ => {
                let n = 1 + rng.below(10);
                for _ in 0..n {
                    out.push(*rng.pick(INTERESTING_BYTES));
                }
            }
        }
    }
    out.truncate(max_len);
    out
}

/// Chunkings that stress a STREAMING parser's boundary handling. Fixed sizes
/// straddle `KV_FIELD_SIZE`; the random split is where a rewrite actually
/// diverges from a batch parser.
pub fn chunkings(rng: &mut Rng, len: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    for &size in &[1usize, 3, 63, 64, 65, 512] {
        let mut v = Vec::new();
        let mut left = len;
        while left > 0 {
            let n = size.min(left);
            v.push(n);
            left -= n;
        }
        out.push(v);
    }
    // Two random splits.
    for _ in 0..2 {
        let mut v = Vec::new();
        let mut left = len;
        while left > 0 {
            let n = (1 + rng.below(70)).min(left);
            v.push(n);
            left -= n;
        }
        out.push(v);
    }
    out
}
