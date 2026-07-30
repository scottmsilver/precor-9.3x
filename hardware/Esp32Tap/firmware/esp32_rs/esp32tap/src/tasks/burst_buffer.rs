//! A `KvSink` that RECORDS a burst instead of transmitting it.
//!
//! Exists so the emulate task can run `EmulationCycle::tick` (which needs the
//! mode machine, and therefore the safety lock) WITHOUT holding the safety
//! lock across the blocking UART writes.
//!
//! `uart_wait_tx_done` waits up to 100 ms per write and a burst is up to four
//! writes, so transmitting under the safety lock could exclude the serial
//! engine from it for ~400 ms — delaying TREAD_OK sampling, relay feedback,
//! console freshness and every deadline in `enforce_due_safety`. The C++ task
//! avoids this by keeping TX outside `controller_mu` entirely; this type is
//! how the Rust task gets the same property while `mode` lives inside the
//! lock.
//!
//! Found by the session's `codex` security review.

use safety_core::cycle::KvSink;
use safety_core::FixedStr;

/// A burst is at most 4 slots; keys are <= 4 bytes and values <= 4 bytes
/// ("5550", "1F4", "1E"), so 8 bytes each cannot truncate.
pub const BURST_SLOTS: usize = 4;

#[derive(Clone, Copy)]
pub struct BurstBuffer {
    items: [(FixedStr<8>, FixedStr<8>); BURST_SLOTS],
    len: usize,
}

impl Default for BurstBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl BurstBuffer {
    pub const fn new() -> Self {
        BurstBuffer {
            items: [(FixedStr::new(), FixedStr::new()); BURST_SLOTS],
            len: 0,
        }
    }

    pub fn clear(&mut self) {
        self.len = 0;
    }

    /// Replay the recorded burst into the real sink, in order.
    pub fn replay<S: KvSink>(&self, sink: &mut S) {
        for (k, v) in &self.items[..self.len] {
            sink.write_kv(k.as_str(), v.as_str());
        }
    }
}

impl KvSink for BurstBuffer {
    fn write_kv(&mut self, key: &str, value: &str) {
        if self.len >= BURST_SLOTS {
            return;
        }
        self.items[self.len] = (
            FixedStr::from_str_truncating(key),
            FixedStr::from_str_truncating(value),
        );
        self.len += 1;
    }
}
