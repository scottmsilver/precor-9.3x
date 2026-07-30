//! A BLE peer's identity, bounded and made safe to repeat.
//!
//! Everything in this module handles bytes that came off the air from a device
//! nobody in this project controls: a chest strap's advertised name, a scan
//! result's address, the address string an app sends back in
//! `POST /api/hrm/select`. That is untrusted input in the strict sense, and it
//! ends up inside a JSON string in `/api/hrm` and in the `/ws` `hr` frame.
//!
//! It lives HERE rather than in the firmware crate for one reason: it is pure,
//! and pure code about untrusted input is exactly the code that has to be
//! tested on a host. The firmware's copy would be reachable only through a
//! radio that QEMU does not have.
//!
//! The Pi daemon has no equivalent to sanitise, because `bluer` hands it a
//! `String` and `serde_json` escapes on the way out. This device builds JSON
//! by hand into fixed buffers — every renderer in the firmware does — so the
//! escaping has to happen once, at INGEST, or every renderer has to remember.

/// Longest peer name retained. Bounded because it is rendered into
/// `/api/status`, whose buffer is a fixed-size stack array.
pub const MAX_NAME: usize = 24;

/// `"AA:BB:CC:DD:EE:FF"` — 17 bytes.
pub const ADDR_TEXT_LEN: usize = 17;

/// A bounded, JSON-safe, NUL-free name.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct FixedName {
    b: [u8; MAX_NAME],
    n: usize,
}

impl FixedName {
    pub const EMPTY: FixedName = FixedName {
        b: [0; MAX_NAME],
        n: 0,
    };

    /// Copy at most [`MAX_NAME`] bytes, replacing everything that is not
    /// printable ASCII — and both characters that can escape a JSON string —
    /// with `.`.
    ///
    /// TRUNCATION CANNOT SPLIT A CHARACTER, and that is a property of the
    /// filter rather than of the bound: only single-byte values survive it, so
    /// by the time the length limit applies there is no multi-byte sequence
    /// left to cut in half. The result is always valid UTF-8 and always safe
    /// between two `"`.
    pub fn set(&mut self, src: &[u8]) {
        self.n = 0;
        for &c in src.iter().take(MAX_NAME) {
            self.b[self.n] = match c {
                b'"' | b'\\' => b'.',
                0x20..=0x7e => c,
                _ => b'.',
            };
            self.n += 1;
        }
    }

    pub fn as_str(&self) -> &str {
        // Every stored byte is in `0x20..=0x7e` by construction. The fallback
        // keeps this total rather than panicking on a treadmill.
        core::str::from_utf8(&self.b[..self.n]).unwrap_or("")
    }

    pub const fn len(&self) -> usize {
        self.n
    }

    pub const fn is_empty(&self) -> bool {
        self.n == 0
    }
}

impl Default for FixedName {
    fn default() -> Self {
        Self::EMPTY
    }
}

/// A BLE device address in both forms at once.
///
/// BOTH, because neither is derivable from the other: the app sends
/// `"AA:BB:CC:DD:EE:FF"` and no address type, while the radio cannot connect
/// without the type it observed during the scan. Most straps advertise a
/// RANDOM static address, so an implementation that assumed "public" would
/// fail to connect to nearly every real device.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Addr {
    /// Six bytes, LITTLE-ENDIAN — the order NimBLE's `ble_addr_t.val` uses.
    pub val: [u8; 6],
    /// NimBLE `ble_addr_t.type`: 0 public, 1 random, 2/3 identity.
    pub kind: u8,
    pub present: bool,
}

impl Addr {
    pub const NONE: Addr = Addr {
        val: [0; 6],
        kind: 0,
        present: false,
    };

    pub const fn new(val: [u8; 6], kind: u8) -> Addr {
        Addr {
            val,
            kind,
            present: true,
        }
    }

    /// Render as `AA:BB:CC:DD:EE:FF` — big-endian, which is what every BLE UI
    /// and both shipping clients display. Returns the number of bytes written,
    /// or 0 when there is no address.
    pub fn text(&self, out: &mut [u8; ADDR_TEXT_LEN]) -> usize {
        if !self.present {
            return 0;
        }
        const HEX: &[u8; 16] = b"0123456789ABCDEF";
        let mut i = 0;
        for k in (0..6).rev() {
            if i > 0 {
                out[i] = b':';
                i += 1;
            }
            out[i] = HEX[(self.val[k] >> 4) as usize];
            out[i + 1] = HEX[(self.val[k] & 0x0f) as usize];
            i += 2;
        }
        i
    }

    /// Parse `AA:BB:CC:DD:EE:FF` into NimBLE byte order.
    ///
    /// Case-insensitive; `:` and `-` separators are optional and may appear
    /// anywhere. TOTAL — any malformed input yields `None` rather than a
    /// partially parsed address, because a partially parsed address is a
    /// connection attempt to a device the user did not choose.
    pub fn parse(text: &[u8]) -> Option<[u8; 6]> {
        let mut nib = [0u8; 12];
        let mut n = 0usize;
        for &c in text {
            let v = match c {
                b'0'..=b'9' => c - b'0',
                b'a'..=b'f' => c - b'a' + 10,
                b'A'..=b'F' => c - b'A' + 10,
                b':' | b'-' => continue,
                _ => return None,
            };
            if n == 12 {
                return None; // too many hex digits
            }
            nib[n] = v;
            n += 1;
        }
        if n != 12 {
            return None;
        }
        let mut out = [0u8; 6];
        // Text is big-endian; `val` is little-endian.
        for k in 0..6 {
            out[5 - k] = (nib[2 * k] << 4) | nib[2 * k + 1];
        }
        Some(out)
    }
}
