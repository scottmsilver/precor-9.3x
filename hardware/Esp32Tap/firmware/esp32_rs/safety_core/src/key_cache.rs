//! `KeyCache` — last-seen console `hmph`/`inc` values, feeding the
//! console-takeover (auto-proxy) path.
//!
//! Port of `components/portable_core/engine/key_cache.h`.
//!
//! THE DANGLING-VIEW DEFECT IS ELIMINATED, NOT CAUGHT.
//!
//! The C++ header documents a real [high] finding: an earlier version returned
//! a `std::string_view` over a function-local `std::array` — a dangling view in
//! the normative console-takeover safety path. The current C++ fix is a
//! LIFETIME CONTRACT ENFORCED BY A TEST: `exchange()` writes the previous value
//! into a caller-owned buffer and returns a view over it, and
//! `test_key_cache.cpp` case 2 asserts `prev.data() == buf.data()` to prove the
//! view aliases the caller's buffer and not internal storage.
//!
//! Here `exchange` returns `PrevValue`, which is OWNED and `Copy`. There is no
//! borrow, so there is nothing to dangle, no caller buffer to thread through,
//! and no contract to enforce. This is the ONE defect of the four found in the
//! C++ core that Rust removes as a class rather than merely catching.

use crate::kv::KV_FIELD_SIZE;

/// The previous value for a tracked key. Owned + `Copy` — it stays valid
/// across any subsequent mutation of the cache, which is precisely the
/// property the C++ aliasing assertion existed to protect.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct PrevValue {
    bytes: [u8; KV_FIELD_SIZE],
    len: u8,
}

impl PrevValue {
    pub const fn empty() -> Self {
        PrevValue {
            bytes: [0u8; KV_FIELD_SIZE],
            len: 0,
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

impl core::fmt::Debug for PrevValue {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        core::fmt::Debug::fmt(self.as_str(), f)
    }
}
impl PartialEq<str> for PrevValue {
    fn eq(&self, other: &str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
impl PartialEq<&str> for PrevValue {
    fn eq(&self, other: &&str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}

#[derive(Default)]
pub struct KeyCache {
    hmph: PrevValueSlot,
    inc: PrevValueSlot,
}

#[derive(Clone, Copy)]
struct PrevValueSlot {
    bytes: [u8; KV_FIELD_SIZE],
    len: u8,
}

impl Default for PrevValueSlot {
    fn default() -> Self {
        PrevValueSlot {
            bytes: [0u8; KV_FIELD_SIZE],
            len: 0,
        }
    }
}

impl KeyCache {
    pub const fn new() -> Self {
        KeyCache {
            hmph: PrevValueSlot {
                bytes: [0u8; KV_FIELD_SIZE],
                len: 0,
            },
            inc: PrevValueSlot {
                bytes: [0u8; KV_FIELD_SIZE],
                len: 0,
            },
        }
    }

    /// Return the previous value for `key` (empty if never seen) and store
    /// `value`, truncated to `KV_FIELD_SIZE - 1` bytes — the same cap as a
    /// `KvPair` field.
    ///
    /// Keys other than `hmph`/`inc` are not tracked: they leave the cache
    /// untouched and return empty.
    pub fn exchange(&mut self, key: &str, value: &str) -> PrevValue {
        let slot = match key {
            "hmph" => &mut self.hmph,
            "inc" => &mut self.inc,
            _ => return PrevValue::empty(),
        };

        let prev = PrevValue {
            bytes: slot.bytes,
            len: slot.len,
        };

        let src = value.as_bytes();
        let n = core::cmp::min(src.len(), KV_FIELD_SIZE - 1);
        slot.bytes = [0u8; KV_FIELD_SIZE];
        slot.bytes[..n].copy_from_slice(&src[..n]);
        slot.len = n as u8;

        prev
    }
}
