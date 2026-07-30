//! `FixedStr<N>` — a stack-only, `Copy` string with a `core::fmt::Write` impl.
//!
//! Written ourselves (~90 lines) rather than pulling `heapless::String`.
//! `heapless` is already in the device crate's transitive graph via
//! esp-idf-hal, so using it would add zero crates — it is declined anyway
//! because a non-Espressif crate in the safety-core event path is exactly what
//! the owner's dependency preference argues against, and this is small.
//!
//! Truncation is SILENT and saturating, matching the C++ `append_bounded`
//! helper in `safety_controller.cpp` (events are capped at `EVENT_MAX_LEN`).

use core::fmt;

#[derive(Clone, Copy)]
pub struct FixedStr<const N: usize> {
    bytes: [u8; N],
    len: usize,
}

impl<const N: usize> FixedStr<N> {
    pub const fn new() -> Self {
        FixedStr {
            bytes: [0u8; N],
            len: 0,
        }
    }

    /// Build from a `&str`, silently truncating at `N` bytes (on a UTF-8
    /// boundary — all callers here are ASCII).
    pub fn from_str_truncating(s: &str) -> Self {
        let mut out = Self::new();
        out.push_str(s);
        out
    }

    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    pub fn capacity(&self) -> usize {
        N
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len]
    }

    pub fn as_str(&self) -> &str {
        // Only ASCII is ever pushed (push_str truncates on a char boundary),
        // but stay total: fall back to the valid prefix rather than panic.
        match core::str::from_utf8(self.as_bytes()) {
            Ok(s) => s,
            Err(e) => core::str::from_utf8(&self.bytes[..e.valid_up_to()]).unwrap_or(""),
        }
    }

    pub fn clear(&mut self) {
        self.len = 0;
        self.bytes = [0u8; N];
    }

    /// Append, saturating at capacity. Truncates on a UTF-8 char boundary.
    pub fn push_str(&mut self, s: &str) {
        for ch in s.chars() {
            let mut buf = [0u8; 4];
            let enc = ch.encode_utf8(&mut buf);
            if self.len + enc.len() > N {
                return;
            }
            self.bytes[self.len..self.len + enc.len()].copy_from_slice(enc.as_bytes());
            self.len += enc.len();
        }
    }

    pub fn push_byte(&mut self, b: u8) {
        if self.len < N {
            self.bytes[self.len] = b;
            self.len += 1;
        }
    }

    /// Append a base-10 signed integer. No allocation, no `format!`.
    pub fn push_i64(&mut self, mut v: i64) {
        if v < 0 {
            self.push_byte(b'-');
            // i64::MIN has no positive counterpart; handle it digit-wise.
            let mut digits = [0u8; 20];
            let mut n = 0;
            while v != 0 {
                let d = -(v % 10);
                digits[n] = b'0' + d as u8;
                n += 1;
                v /= 10;
            }
            if n == 0 {
                self.push_byte(b'0');
            }
            while n > 0 {
                n -= 1;
                self.push_byte(digits[n]);
            }
            return;
        }
        if v == 0 {
            self.push_byte(b'0');
            return;
        }
        let mut digits = [0u8; 20];
        let mut n = 0;
        while v > 0 {
            digits[n] = b'0' + (v % 10) as u8;
            n += 1;
            v /= 10;
        }
        while n > 0 {
            n -= 1;
            self.push_byte(digits[n]);
        }
    }
}

impl<const N: usize> Default for FixedStr<N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const N: usize> fmt::Write for FixedStr<N> {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.push_str(s);
        Ok(())
    }
}

impl<const N: usize> fmt::Debug for FixedStr<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.as_str(), f)
    }
}

impl<const N: usize> fmt::Display for FixedStr<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl<const N: usize> PartialEq for FixedStr<N> {
    fn eq(&self, other: &Self) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
impl<const N: usize> Eq for FixedStr<N> {}

impl<const N: usize> PartialEq<str> for FixedStr<N> {
    fn eq(&self, other: &str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
impl<const N: usize> PartialEq<&str> for FixedStr<N> {
    fn eq(&self, other: &&str) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}
