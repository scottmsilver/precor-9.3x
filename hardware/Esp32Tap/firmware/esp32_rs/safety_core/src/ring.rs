//! `RingBuffer<SIZE, MSG>` — fixed-capacity circular message log.
//!
//! Port of `components/portable_core/util/ring_buffer.h`.
//!
//! DIVERGENCE (deliberate, reported): the C++ class owns a `std::mutex`; this
//! one does not. Interior locking in a `no_std` core would mean pulling a
//! `critical-section` implementation into the safety core, and Rust does not
//! need it: `push` takes `&mut self`, `snapshot`/`at` take `&self`, so the
//! borrow checker already forbids a concurrent read during a write. Callers
//! that genuinely share a ring across threads wrap it in a `Mutex` — which is
//! exactly what the ported threaded case (`concurrent_push_and_snapshot`)
//! does, so the case still proves the same property.
//!
//! LOAD-BEARING QUIRK: `count` is a MONOTONIC TOTAL, not a fill level. Case
//! 1.3/4 pushes 5 messages into a 4-slot ring and expects `count == 5`. The
//! field is named `total_pushed` here so the trap is self-documenting; the
//! `count()` accessor keeps the vector 1:1.

use crate::fixed_str::FixedStr;

pub struct RingBuffer<const SIZE: usize, const MSG: usize> {
    msgs: [[u8; MSG]; SIZE],
    lens: [u16; SIZE],
    head: usize,
    total_pushed: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Snapshot {
    pub head: usize,
    /// Monotonic total of pushes ever performed — NOT the number of live
    /// entries. See the module note.
    pub count: u32,
}

impl<const SIZE: usize, const MSG: usize> RingBuffer<SIZE, MSG> {
    pub const fn new() -> Self {
        RingBuffer {
            msgs: [[0u8; MSG]; SIZE],
            lens: [0u16; SIZE],
            head: 0,
            total_pushed: 0,
        }
    }

    /// Push a message, truncated to `MSG - 1` bytes (the C++ reserves the last
    /// byte for the NUL terminator; case 1.3/5 asserts the resulting length).
    pub fn push(&mut self, msg: &str) {
        let bytes = msg.as_bytes();
        let copy_len = core::cmp::min(bytes.len(), MSG - 1);
        self.msgs[self.head][..copy_len].copy_from_slice(&bytes[..copy_len]);
        self.lens[self.head] = copy_len as u16;
        self.head = (self.head + 1) % SIZE;
        self.total_pushed = self.total_pushed.wrapping_add(1);
    }

    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            head: self.head,
            count: self.total_pushed,
        }
    }

    /// Message at a ring index. Negative indices wrap, matching the C++
    /// `idx % Size; if (mod < 0) mod += Size`.
    pub fn at(&self, idx: i32) -> FixedStr<MSG> {
        let size = SIZE as i32;
        let mut m = idx % size;
        if m < 0 {
            m += size;
        }
        let slot = m as usize;
        let len = self.lens[slot] as usize;
        let mut out = FixedStr::<MSG>::new();
        out.push_str(core::str::from_utf8(&self.msgs[slot][..len]).unwrap_or(""));
        out
    }

    pub const fn size() -> usize {
        SIZE
    }
    pub const fn msg_size() -> usize {
        MSG
    }
}

impl<const SIZE: usize, const MSG: usize> Default for RingBuffer<SIZE, MSG> {
    fn default() -> Self {
        Self::new()
    }
}
