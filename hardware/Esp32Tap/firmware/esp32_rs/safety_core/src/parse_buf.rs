//! `ParseBuf<N>` — the streaming re-assembly buffer in front of `kv_parse`,
//! with a bounded, self-healing recovery from an unterminated frame.
//!
//! # The defect this exists to fix
//!
//! `kv_parse` is deliberately Postel-tolerant: an incomplete frame (`[` with
//! no `]` yet in the buffer) makes it BREAK and report `consumed` = the index
//! of that `[`, so the partial frame stays buffered until the rest of it
//! arrives. That is correct for a byte stream — and it is also a wedge.
//!
//! A single `[` that is never closed (line noise on the console tap, a
//! truncated burst, a corrupted byte that happens to be `0x5B`) pins
//! `consumed` at that offset forever. The buffer then fills to capacity, and
//! the C++/original Rust `append` silently drops everything that no longer
//! fits. From that moment the KV parse path is DEAD: it yields zero pairs for
//! the rest of the power cycle.
//!
//! That is safety-relevant, and quietly so. `SafetyController::
//! observe_console_bytes` has its OWN scanner, so console FRESHNESS keeps
//! being refreshed and the machine happily stays in Emulate — but
//! `ModeStateMachine::auto_proxy_on_console_change` is fed from THIS path, so
//! the **console-takeover interlock** (a physical console button press
//! reclaiming control from Emulate) stops working with no error, no event and
//! no externally visible symptom until someone presses the button and nothing
//! happens.
//!
//! The C++ `main/serial_engine_task.cpp` — retired with the C++ application,
//! preserved on `archive/esp32tap-cpp-net-tier` — had the same wedge;
//! it is reported, not silently forked. This buffer is task-layer plumbing —
//! it is NOT part of the 148-case ported corpus and changes no model
//! semantics, so it does not affect the differential.
//!
//! # The fix
//!
//! `kv_parse` only ever ACCEPTS a frame whose bracketed content is shorter
//! than `KV_FIELD_SIZE` (64), so the longest acceptable frame is
//! `[` + 63 content bytes + `]` = 65 bytes. Therefore, once the buffer holds
//! [`MAX_FRAME_BYTES`] (a deliberately generous 129) unconsumed bytes, any `[`
//! more than 64 bytes from the end can no longer become an accepted pair —
//! its closing `]` has not arrived yet, so its content is already too long.
//!
//! Recovery is exactly that observation: when the parser consumed nothing and
//! the buffer is at or over the bound, keep only from the LAST `[` inside the
//! trailing [`MAX_FRAME_BYTES`]-byte window (clearing entirely if there is
//! none). Bounded work, no allocation, buffer length bounded by
//! [`MAX_FRAME_BYTES`] afterwards, and no frame the parser could still have
//! accepted is ever discarded — a legitimately partial frame is at most 64
//! bytes old and sits well inside the retained window.

/// Wedge threshold, in bytes.
///
/// `1 + 63 + 1 + 63 + 1` — the shape of a maximal key/value pair. This is
/// deliberately DOUBLE what `kv_parse` can actually accept (its bound is on
/// the whole bracketed content, so 65 bytes), because being generous here only
/// costs latency-to-recovery, while being tight risks discarding a partial
/// frame that was still legitimately forming.
pub const MAX_FRAME_BYTES: usize = 1 + (crate::kv::KV_FIELD_SIZE - 1) + 1 + (crate::kv::KV_FIELD_SIZE - 1) + 1;

/// Outcome of one [`ParseBuf::resync_if_wedged`] call.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Resync {
    /// Bytes discarded from the front of the buffer.
    pub dropped: usize,
    /// True if the buffer was wedged and has now been resynchronised.
    pub recovered: bool,
}

pub struct ParseBuf<const N: usize> {
    bytes: [u8; N],
    len: usize,
    /// Monotonic count of wedge recoveries, for telemetry. A nonzero value on
    /// a healthy bus means the console tap is corrupting bytes.
    resyncs: u32,
    /// Monotonic count of bytes dropped by `append` because the buffer was
    /// full. Should stay 0 now that wedges resynchronise.
    overflow_drops: u32,
}

impl<const N: usize> ParseBuf<N> {
    pub const fn new() -> Self {
        // A buffer smaller than one maximal frame could "recover" a frame that
        // was still legitimately partial.
        assert!(N > MAX_FRAME_BYTES);
        ParseBuf {
            bytes: [0u8; N],
            len: 0,
            resyncs: 0,
            overflow_drops: 0,
        }
    }

    /// Append what fits; excess is dropped and counted.
    pub fn append(&mut self, src: &[u8]) {
        let space = N - self.len;
        let n = core::cmp::min(src.len(), space);
        self.bytes[self.len..self.len + n].copy_from_slice(&src[..n]);
        self.len += n;
        self.overflow_drops = self.overflow_drops.saturating_add((src.len() - n) as u32);
    }

    pub fn consume(&mut self, n: usize) {
        let n = core::cmp::min(n, self.len);
        if n > 0 && n < self.len {
            self.bytes.copy_within(n..self.len, 0);
        }
        self.len -= n;
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.bytes[..self.len]
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn resync_count(&self) -> u32 {
        self.resyncs
    }

    pub fn overflow_drop_count(&self) -> u32 {
        self.overflow_drops
    }

    /// Call with the `consumed` that `kv_parse` just reported for
    /// [`Self::as_slice`], AFTER [`Self::consume`].
    ///
    /// If the parser consumed nothing while holding at least
    /// [`MAX_FRAME_BYTES`], keep only from the LAST `[` inside the trailing
    /// `MAX_FRAME_BYTES` window and discard everything before it (clearing
    /// entirely if the window holds no `[`).
    ///
    /// Resynchronising on the FIRST `[` instead is not enough: a stream of
    /// `[[[[…` would drop one byte per iteration while sixteen arrive, and the
    /// buffer would still fill. Anchoring on the LAST `[` bounds the retained
    /// length by `MAX_FRAME_BYTES` unconditionally.
    pub fn resync_if_wedged(&mut self, consumed: usize) -> Resync {
        if consumed != 0 || self.len < MAX_FRAME_BYTES {
            return Resync::default();
        }
        let window_start = self.len - (MAX_FRAME_BYTES - 1);
        let drop_to = match self.bytes[window_start..self.len]
            .iter()
            .rposition(|&b| b == b'[')
        {
            Some(off) => window_start + off,
            None => self.len,
        };
        self.consume(drop_to);
        self.resyncs = self.resyncs.saturating_add(1);
        debug_assert!(self.len < MAX_FRAME_BYTES);
        Resync {
            dropped: drop_to,
            recovered: true,
        }
    }
}

impl<const N: usize> Default for ParseBuf<N> {
    fn default() -> Self {
        Self::new()
    }
}
