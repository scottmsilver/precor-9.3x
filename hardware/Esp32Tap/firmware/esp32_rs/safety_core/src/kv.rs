//! KV wire protocol — parser, builder and the speed/incline hex codecs.
//!
//! Rust port of `components/portable_core/protocol/kv_protocol.{h,cpp}`.
//!
//! Two deliberate departures from idiomatic Rust, both in service of the
//! equivalence mandate:
//!
//!  * `KvPair` is OWNED (`KvField([u8; 64], u8)`), not a `&'a str` view into
//!    the input. A borrowed view would be more idiomatic and cheaper, but it
//!    changes the `< KV_FIELD_SIZE` truncate-and-drop semantics that cases
//!    1.1/1..9 depend on. Owned + `Copy` reproduces the C++ byte-for-byte.
//!  * `kv_build` returns a fixed `KvFrame` instead of a `String`. The C++
//!    returns `std::string` and therefore ALLOCATES on every one of the 14 KV
//!    writes per emulate cycle; here the crate cannot name `String` at all.

/// `KV_FIELD_SIZE` — key and value are each NUL-terminated in a 64-byte array,
/// i.e. at most 63 content bytes.
pub const KV_FIELD_SIZE: usize = 64;
/// `MAX_KV_CONTENT_LEN` — carried over verbatim; unused by the parser in both
/// implementations (`raw_len < KV_FIELD_SIZE` is the operative bound).
pub const MAX_KV_CONTENT_LEN: usize = 127;

/// One owned KV field. `Copy`, no lifetime, so it can never dangle.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct KvField {
    bytes: [u8; KV_FIELD_SIZE],
    len: u8,
}

impl KvField {
    pub const fn empty() -> Self {
        KvField {
            bytes: [0u8; KV_FIELD_SIZE],
            len: 0,
        }
    }

    /// Copy up to `KV_FIELD_SIZE - 1` bytes. Returns `None` if `src` does not
    /// fit — the C++ drops the whole pair in that case rather than truncating.
    pub fn from_bytes(src: &[u8]) -> Option<Self> {
        if src.len() >= KV_FIELD_SIZE {
            return None;
        }
        let mut f = KvField::empty();
        f.bytes[..src.len()].copy_from_slice(src);
        f.len = src.len() as u8;
        Some(f)
    }

    /// Copy, truncating at `KV_FIELD_SIZE - 1` (the `KeyCache::exchange` cap).
    pub fn from_bytes_truncating(src: &[u8]) -> Self {
        let n = core::cmp::min(src.len(), KV_FIELD_SIZE - 1);
        let mut f = KvField::empty();
        f.bytes[..n].copy_from_slice(&src[..n]);
        f.len = n as u8;
        f
    }

    pub fn from_str_truncating(s: &str) -> Self {
        Self::from_bytes_truncating(s.as_bytes())
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len as usize]
    }

    pub fn as_str(&self) -> &str {
        core::str::from_utf8(self.as_bytes()).unwrap_or("")
    }

    pub fn len(&self) -> usize {
        self.len as usize
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl core::fmt::Debug for KvField {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        core::fmt::Debug::fmt(self.as_str(), f)
    }
}

impl PartialEq<str> for KvField {
    fn eq(&self, other: &str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
impl PartialEq<&str> for KvField {
    fn eq(&self, other: &&str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct KvPair {
    pub key: KvField,
    pub value: KvField,
}

impl KvPair {
    pub const fn empty() -> Self {
        KvPair {
            key: KvField::empty(),
            value: KvField::empty(),
        }
    }
}

impl Default for KvPair {
    fn default() -> Self {
        Self::empty()
    }
}

/// Outcome of one `kv_parse` call.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct ParseResult {
    /// Number of pairs written into `out`.
    pub n: usize,
    /// Bytes processed; the caller keeps the remainder buffered.
    pub consumed: usize,
}

/// Parse `[key:value]` pairs out of a raw byte buffer.
///
/// Byte-for-byte equivalent to the C++ `kv_parse`, including the parts that
/// look like bugs and are load-bearing:
///  * an incomplete frame (`[` with no `]`) BREAKS, leaving `consumed` at the
///    `[` so the bytes stay buffered (case 1.1/5);
///  * a frame whose key or value is too long is silently DROPPED but `i` still
///    advances past the `]`;
///  * `max_pairs` bounds the output, and the scan stops there (case 1.1/7).
///
/// Hot path: no allocation, no panic (all indexing is slice-bounded).
pub fn kv_parse(buf: &[u8], out: &mut [KvPair]) -> ParseResult {
    let len = buf.len();
    let max_pairs = out.len();
    let mut i = 0usize;
    let mut n = 0usize;

    while i < len && n < max_pairs {
        let b = buf[i];
        if b == 0xFF || b == 0x00 {
            i += 1;
            continue;
        }
        if b == b'[' {
            // Find the closing bracket.
            let mut end: Option<usize> = None;
            for j in (i + 1)..len {
                if buf[j] == b']' {
                    end = Some(j);
                    break;
                }
            }
            let Some(end) = end else {
                break; // incomplete frame — keep the bytes
            };

            let raw_len = end - i - 1;
            let content = &buf[i + 1..end];
            let printable = content.iter().all(|&c| (0x20..=0x7E).contains(&c));

            if printable && raw_len > 0 && raw_len < KV_FIELD_SIZE {
                match content.iter().position(|&c| c == b':') {
                    Some(colon) => {
                        let key_part = &content[..colon];
                        let val_part = &content[colon + 1..];
                        if key_part.len() < KV_FIELD_SIZE && val_part.len() < KV_FIELD_SIZE {
                            // `from_bytes` cannot fail here; be total anyway.
                            if let (Some(k), Some(v)) =
                                (KvField::from_bytes(key_part), KvField::from_bytes(val_part))
                            {
                                out[n] = KvPair { key: k, value: v };
                                n += 1;
                            }
                        }
                    }
                    None => {
                        if content.len() < KV_FIELD_SIZE {
                            if let Some(k) = KvField::from_bytes(content) {
                                out[n] = KvPair {
                                    key: k,
                                    value: KvField::empty(),
                                };
                                n += 1;
                            }
                        }
                    }
                }
            }
            i = end + 1;
        } else {
            i += 1;
        }
    }

    ParseResult { n, consumed: i }
}

// --- builder --------------------------------------------------------------

/// A built wire frame. 136 bytes covers `[` + 63 + `:` + 63 + `]` + 0xFF, i.e.
/// any pair of maximal `KvField`s — everything the parser can ever produce,
/// and far more than the 50-byte `MAX_WRITE_BYTES` the writer will accept.
pub const KV_FRAME_CAPACITY: usize = 136;

#[derive(Clone, Copy)]
pub struct KvFrame {
    bytes: [u8; KV_FRAME_CAPACITY],
    len: u8,
}

impl KvFrame {
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len as usize]
    }
    pub fn len(&self) -> usize {
        self.len as usize
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    fn push(&mut self, b: u8) {
        if (self.len as usize) < KV_FRAME_CAPACITY {
            self.bytes[self.len as usize] = b;
            self.len += 1;
        }
    }
    fn push_slice(&mut self, s: &[u8]) {
        for &b in s {
            self.push(b);
        }
    }
}

impl core::fmt::Debug for KvFrame {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        core::fmt::Debug::fmt(self.as_bytes(), f)
    }
}

/// `[key:value]\xff`, or `[key]\xff` when `value` is empty.
///
/// Allocation-free replacement for the C++ `std::string kv_build(...)`, which
/// heap-allocates on every one of the 14 KV writes per emulate cycle.
///
/// Returns `None` when the frame would exceed [`KV_FRAME_CAPACITY`].
///
/// DIVERGENCE FROM C++, and a deliberate improvement — found by the D2
/// differential fuzzer, which built a 31-byte key with a 102-byte value and
/// caught this implementation SILENTLY TRUNCATING the trailing `0xFF`
/// delimiter. A truncated frame is the worst possible output: it is still a
/// plausible-looking frame, and the delimiter that separates it from the next
/// one is exactly what goes missing. `std::string` grows instead, so the C++
/// builds a 137-byte "frame" that `SerialWriter::write_bytes` then silently
/// drops for exceeding `MAX_WRITE_BYTES` (50).
///
/// Both behaviours are unreachable from the firmware — every real call site
/// passes a cycle key (<= 4 bytes) and a hex value (<= 4 bytes) — but silent
/// truncation is not something to leave latent in a safety core. `None` makes
/// the failure impossible to ignore.
pub fn kv_build(key: &str, value: &str) -> Option<KvFrame> {
    let needed = 1 + key.len() + if value.is_empty() { 0 } else { 1 + value.len() } + 2;
    if needed > KV_FRAME_CAPACITY {
        return None;
    }
    let mut f = KvFrame {
        bytes: [0u8; KV_FRAME_CAPACITY],
        len: 0,
    };
    f.push(b'[');
    f.push_slice(key.as_bytes());
    if !value.is_empty() {
        f.push(b':');
        f.push_slice(value.as_bytes());
    }
    f.push(b']');
    f.push(0xFF);
    Some(f)
}

/// Bare-key form: `[key]\xff`.
pub fn kv_build_bare(key: &str) -> Option<KvFrame> {
    kv_build(key, "")
}

// --- hex codecs -----------------------------------------------------------

/// Uppercase hex digits, fixed capacity. `i32` in hex is at most 8 digits plus
/// a sign, so 16 bytes can never truncate.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct HexStr {
    bytes: [u8; 16],
    len: u8,
}

impl HexStr {
    fn new() -> Self {
        HexStr {
            bytes: [0u8; 16],
            len: 0,
        }
    }
    fn push(&mut self, b: u8) {
        if (self.len as usize) < 16 {
            self.bytes[self.len as usize] = b;
            self.len += 1;
        }
    }
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len as usize]
    }
    pub fn as_str(&self) -> &str {
        core::str::from_utf8(self.as_bytes()).unwrap_or("")
    }
    pub fn len(&self) -> usize {
        self.len as usize
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl core::fmt::Debug for HexStr {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        core::fmt::Debug::fmt(self.as_str(), f)
    }
}
impl PartialEq<str> for HexStr {
    fn eq(&self, other: &str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
impl PartialEq<&str> for HexStr {
    fn eq(&self, other: &&str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}

/// `std::to_chars(value, 16)` + uppercase, exactly. Negative values get a
/// leading `-` then the magnitude, matching `to_chars` for signed ints.
fn to_hex_upper(value: i32) -> HexStr {
    let mut out = HexStr::new();
    if value == 0 {
        out.push(b'0');
        return out;
    }
    let neg = value < 0;
    // i32::MIN has no positive counterpart — work in i64.
    let mut v = (value as i64).unsigned_abs();
    let mut digits = [0u8; 16];
    let mut n = 0usize;
    while v > 0 {
        let d = (v % 16) as u8;
        digits[n] = if d < 10 { b'0' + d } else { b'A' + (d - 10) };
        n += 1;
        v /= 16;
    }
    if neg {
        out.push(b'-');
    }
    while n > 0 {
        n -= 1;
        out.push(digits[n]);
    }
    out
}

/// `std::from_chars(hex, 16)` into `unsigned long`, requiring FULL consumption.
/// Rejects `-`, `+`, `0x` prefixes and whitespace, exactly like `from_chars`
/// for an unsigned type.
fn from_hex_full(hex: &str) -> Option<u64> {
    let b = hex.as_bytes();
    if b.is_empty() {
        return None;
    }
    let mut val: u64 = 0;
    for &c in b {
        let d = match c {
            b'0'..=b'9' => c - b'0',
            b'a'..=b'f' => c - b'a' + 10,
            b'A'..=b'F' => c - b'A' + 10,
            // Anything else means `from_chars` stopped early -> ptr != end.
            _ => return None,
        };
        // from_chars reports result_out_of_range on overflow; the caller's
        // range check would reject anyway, but saturate to stay total.
        val = val.saturating_mul(16).saturating_add(d as u64);
    }
    Some(val)
}

/// Encode tenths-of-mph as the wire `hmph` value: mph × 100 in uppercase hex.
///
/// Takes `SpeedTenths` (not a bare `i32`), so the ×10 to the wire unit happens
/// exactly once, inside `SpeedTenths::to_hundredths`.
pub fn encode_speed_hex(tenths: crate::units::SpeedTenths) -> HexStr {
    to_hex_upper(tenths.to_hundredths().get())
}

/// Decode the wire `hmph` value to tenths of mph. `None` on parse error
/// (the C++ returns -1).
///
/// Guards carried over verbatim: reject `len > 10` before parsing, and reject
/// `val > 5000`. Rounds with `(val + 5) / 10`.
pub fn decode_speed_hex(hex: &str) -> Option<crate::units::SpeedTenths> {
    if hex.is_empty() || hex.len() > 10 {
        return None;
    }
    let val = from_hex_full(hex)?;
    if val > 5000 {
        return None;
    }
    Some(crate::units::SpeedTenths::new(((val + 5) / 10) as i32))
}

/// Encode incline (already half-percent units) as uppercase hex.
pub fn encode_incline_hex(half_pct: crate::units::InclineHalfPct) -> HexStr {
    to_hex_upper(half_pct.get())
}

/// Decode the wire `inc` value to half-percent units. `None` on parse error.
/// Guards: `len > 10` rejected, `val > 500` rejected.
pub fn decode_incline_hex(hex: &str) -> Option<crate::units::InclineHalfPct> {
    if hex.is_empty() || hex.len() > 10 {
        return None;
    }
    let val = from_hex_full(hex)?;
    if val > 500 {
        return None;
    }
    Some(crate::units::InclineHalfPct::new(val as i32))
}
