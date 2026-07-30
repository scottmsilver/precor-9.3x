//! Shared host-test fakes and helpers.
//!
//! Port of `host/fakes/fake_hal.h` plus the helper functions at the top of
//! `test_safety_controller.cpp` and `test_safety_boot_envelope.cpp`.

#![allow(dead_code)]

use safety_core::cycle::KvSink;
use safety_core::kv::kv_build;
use safety_core::safety::controller::{
    ConnectionIdentity, OutputIntent, SafeMode, SafetyController, Transport,
};
use safety_core::units::*;
use std::cell::RefCell;
use std::rc::Rc;

pub const MS: i64 = 1_000;
pub const S: i64 = 1_000_000;

pub fn us(v: i64) -> Micros {
    Micros::new(v)
}
pub fn ms(v: i64) -> Micros {
    Micros::new(v * MS)
}
pub fn tenths(v: i32) -> SpeedTenths {
    SpeedTenths::new(v)
}
pub fn half(v: i32) -> InclineHalfPct {
    InclineHalfPct::new(v)
}

/// `identity(t = WSS, handle = 100, gen = 1)`.
pub fn identity(t: Transport, handle: i32, gen: i64) -> ConnectionIdentity {
    ConnectionIdentity::new(t, handle, gen).expect("test identities are always valid")
}

pub fn default_identity() -> ConnectionIdentity {
    identity(Transport::Wss, 100, 1)
}

// --- fake clock -----------------------------------------------------------

#[derive(Clone, Default)]
pub struct FakeClock {
    t: Rc<RefCell<Micros>>,
}

impl FakeClock {
    pub fn new() -> Self {
        FakeClock {
            t: Rc::new(RefCell::new(Micros::ZERO)),
        }
    }
    pub fn now(&self) -> Micros {
        *self.t.borrow()
    }
    pub fn set(&self, v: Micros) {
        *self.t.borrow_mut() = v;
    }
    pub fn advance_us(&self, d: i64) {
        let cur = *self.t.borrow();
        *self.t.borrow_mut() = cur + Micros::new(d);
    }
    pub fn advance_ms(&self, d: i64) {
        self.advance_us(d * MS);
    }
}

// --- fake safety IO -------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Edge {
    pub what: String,
    pub at_us: Micros,
}

/// Mirrors `fake_hal.h::FakeSafetyIo`, including the ORDERED EDGE LOG that
/// boot-envelope case 2 asserts on.
///
/// Because `SafetyIo::apply` is the only mutator, the edge log cannot miss an
/// edge — there is no setter to bypass it with.
pub struct FakeSafetyIo {
    pub relay_cmd: bool,
    pub tx_en: bool,
    pub tread_ok_level: bool,
    /// BYPASS at rest: NC closed (low), NO open (high).
    pub nc_high: bool,
    pub no_high: bool,
    pub vbus: bool,
    pub led: bool,
    pub edges: Vec<Edge>,
    clock: FakeClock,
}

impl FakeSafetyIo {
    pub fn new(clock: FakeClock) -> Self {
        FakeSafetyIo {
            relay_cmd: false,
            tx_en: false,
            tread_ok_level: true,
            nc_high: false,
            no_high: true,
            vbus: false,
            led: false,
            edges: Vec::new(),
            clock,
        }
    }

    fn log(&mut self, what: &str, on: bool) {
        self.edges.push(Edge {
            what: format!("{what}:{}", if on { 1 } else { 0 }),
            at_us: self.clock.now(),
        });
    }

    /// The single write site. tx_enable FIRST, then relay — the property
    /// boot-envelope case 2 and the S3 audit subsequence assert.
    pub fn apply(&mut self, intent: OutputIntent) {
        if intent.tx_enable.get() != self.tx_en {
            self.log("tx_enable", intent.tx_enable.get());
        }
        self.tx_en = intent.tx_enable.get();
        if intent.relay.get() != self.relay_cmd {
            self.log("relay_cmd", intent.relay.get());
        }
        self.relay_cmd = intent.relay.get();
    }

    pub fn tread_ok(&self) -> TreadOk {
        TreadOk(self.tread_ok_level)
    }
    pub fn k1_nc_high(&self) -> NcHigh {
        NcHigh(self.nc_high)
    }
    pub fn k1_no_high(&self) -> NoHigh {
        NoHigh(self.no_high)
    }
    pub fn vbus_present(&self) -> VbusPresent {
        VbusPresent(self.vbus)
    }

    pub fn set_feedback_bypass(&mut self) {
        self.nc_high = false;
        self.no_high = true;
    }
    pub fn set_feedback_emulate(&mut self) {
        self.nc_high = true;
        self.no_high = false;
    }
    pub fn set_feedback_both_closed(&mut self) {
        self.nc_high = false;
        self.no_high = false;
    }
    pub fn set_feedback_both_open(&mut self) {
        self.nc_high = true;
        self.no_high = true;
    }
}

// --- fake serial port -----------------------------------------------------

#[derive(Clone, Debug)]
pub struct Write {
    pub at_us: Micros,
    pub bytes: Vec<u8>,
}

/// Mirrors `fake_hal.h::FakePort`: scripted RX plus a TIMESTAMPED write log.
pub struct FakePort {
    pub rx: std::collections::VecDeque<Vec<u8>>,
    pub writes: Vec<Write>,
    pub idle_low: bool,
    clock: FakeClock,
}

impl FakePort {
    pub fn new(clock: FakeClock) -> Self {
        FakePort {
            rx: Default::default(),
            writes: Vec::new(),
            idle_low: true,
            clock,
        }
    }

    pub fn inject(&mut self, bytes: &[u8]) {
        self.rx.push_back(bytes.to_vec());
    }

    pub fn read(&mut self, out: &mut [u8]) -> usize {
        let Some(front) = self.rx.front_mut() else {
            return 0;
        };
        let n = out.len().min(front.len());
        out[..n].copy_from_slice(&front[..n]);
        if n == front.len() {
            self.rx.pop_front();
        } else {
            front.drain(..n);
        }
        n
    }

    pub fn write(&mut self, bytes: &[u8]) -> bool {
        self.writes.push(Write {
            at_us: self.clock.now(),
            bytes: bytes.to_vec(),
        });
        true
    }

    pub fn tx_idle_low(&self) -> bool {
        self.idle_low
    }

    pub fn all_written(&self) -> Vec<u8> {
        self.writes.iter().flat_map(|w| w.bytes.clone()).collect()
    }

    pub fn clear(&mut self) {
        self.writes.clear();
    }
}

/// `KvSink` over a `FakePort`, replacing `SerialWriter<FakePort>`.
///
/// `MAX_WRITE_BYTES = 50`: oversized writes are REJECTED, not truncated —
/// same as the C++ `SerialWriter::write_bytes`.
pub struct PortWriter<'a> {
    pub port: &'a mut FakePort,
    pub kv_log: Vec<(String, String)>,
}

pub const MAX_WRITE_BYTES: usize = 50;

impl<'a> PortWriter<'a> {
    pub fn new(port: &'a mut FakePort) -> Self {
        PortWriter {
            port,
            kv_log: Vec::new(),
        }
    }
}

impl KvSink for PortWriter<'_> {
    fn write_kv(&mut self, key: &str, value: &str) {
        self.kv_log.push((key.to_string(), value.to_string()));
        // `None` (over KV_FRAME_CAPACITY) and "over MAX_WRITE_BYTES" are both
        // dropped writes, exactly as the C++ SerialWriter drops oversized ones.
        let Some(frame) = kv_build(key, value) else {
            return;
        };
        if frame.len() > MAX_WRITE_BYTES {
            return;
        }
        self.port.write(frame.as_bytes());
    }
}

/// A sink that only records (no port), for the emulation cases.
#[derive(Default)]
pub struct RecordingSink {
    pub kv_log: Vec<(String, String)>,
}

impl KvSink for RecordingSink {
    fn write_kv(&mut self, key: &str, value: &str) {
        self.kv_log.push((key.to_string(), value.to_string()));
    }
}

// --- controller helpers ---------------------------------------------------

/// `connected_controller(owner)`: BYPASS sample -> connect -> acquire.
pub fn connected_controller(owner: &ConnectionIdentity) -> SafetyController {
    let mut c = SafetyController::new();
    c.observe_relay_feedback(NcHigh(false), NoHigh(true), Micros::ZERO); // BYPASS
    assert!(c.connect(owner));
    assert!(c.acquire(owner, Micros::ZERO));
    c
}

/// `enter_emulate(c, owner, now = 0)` — the full gap-safe entry.
pub fn enter_emulate(c: &mut SafetyController, owner: &ConnectionIdentity, now: Micros) {
    c.observe_console_bytes(b"[hmph:0000]", now);
    assert!(c.request_emulate(owner, now, true));
    assert_eq!(c.mode(), SafeMode::EntryWaitGap);
    assert!(c.observe_interframe_gap(now + ms(100)));
    assert_eq!(c.mode(), SafeMode::EntryWaitFeedback);
    c.observe_relay_feedback(NcHigh(true), NoHigh(false), now + ms(105));
    c.observe_relay_feedback(NcHigh(true), NoHigh(false), now + ms(106));
    assert_eq!(c.mode(), SafeMode::Emulating);
}

pub fn last_event(c: &SafetyController) -> String {
    if c.event_count() == 0 {
        return String::new();
    }
    c.event_at(c.event_count() - 1).unwrap_or("").to_string()
}

pub fn last_events(c: &SafetyController, n: u64) -> Vec<String> {
    let count = c.event_count();
    let start = count.saturating_sub(n);
    (start..count)
        .map(|i| c.event_at(i).unwrap_or("").to_string())
        .collect()
}

pub fn has_event(c: &SafetyController, text: &str, start: u64) -> bool {
    (start..c.event_count()).any(|i| c.event_at(i) == Some(text))
}
