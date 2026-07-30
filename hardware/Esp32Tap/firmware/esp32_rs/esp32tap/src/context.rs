//! Shared firmware state.
//!
//! ONE structural improvement over the C++ `FirmwareContext`: `SafetyIoImpl`
//! lives INSIDE the mutex. In C++ `safety_io` is a plain member reachable
//! without `controller_mu`, so "you may only touch the relay while holding the
//! controller lock" is a convention nothing enforces. Here it is a
//! compile-time fact, and it costs nothing — every `io` access already
//! happened under the lock.
//!
//! The writer gets its OWN mutex: `uart_wait_tx_done` can block ~50 ms at
//! 9600 baud, and holding the controller lock across that would starve the
//! 5 ms serial loop.

// COMPILER-ENFORCED unsafe containment for this module and every module
// below it. `forbid` (unlike the crate root's `deny`) CANNOT be lifted by an
// inner `#[allow(unsafe_code)]` — that is a hard error, not a warning — so
// this is a guarantee rather than a convention. Added 2026-07-28 after a
// reviewer disproved the "deny contains it" claim by counterexample.
#![forbid(unsafe_code)]

#[allow(unused_imports)]
use crate::hal::{ConsoleMotorUart, Esp32Clock, Esp32SafetyIo, MotorTapUart};

/// The safety-IO implementation. Under `qemu-test` this is the SCRIPTED one
/// (the esp32s3 QEMU GPIO model has zero drivable inputs), which WRAPS the
/// real HAL so output-init ordering is preserved.
#[cfg(not(feature = "qemu-test"))]
pub type SafetyIoImpl = Esp32SafetyIo;
#[cfg(feature = "qemu-test")]
pub type SafetyIoImpl = crate::qemu_test::QemuTestSafetyIo;

/// The motor tap. Under `qemu-test` it is remapped to UART0 RX with a command
/// line mux, because UART2 is unwireable in the pinned esp-QEMU.
#[cfg(not(feature = "qemu-test"))]
pub type MotorTapImpl = MotorTapUart;
#[cfg(feature = "qemu-test")]
pub type MotorTapImpl = crate::qemu_test::QemuTestMotorTap;
use safety_core::cycle::{EmulationCycle, KvSink};
use safety_core::hal::{SafetyIo, SerialOut};
use safety_core::key_cache::KeyCache;
use safety_core::kv::{kv_build, KvPair};
use safety_core::mode::ModeStateMachine;
use safety_core::safety::controller::SafetyController;
use safety_core::units::Micros;
use std::sync::Mutex;

/// Everything the safety lock protects.
pub struct Guarded {
    pub controller: SafetyController,
    pub mode: ModeStateMachine,
    /// Inside the lock BY CONSTRUCTION — see the module note.
    pub io: SafetyIoImpl,
    pub key_cache: KeyCache,
    pub last_console_rx: Micros,
    pub console_uart: ConsoleMotorUart,
    pub motor_tap: MotorTapImpl,
    pub cycle: EmulationCycle,
    /// Streaming parse buffers. Members, not stack locals: PLAN's
    /// QEMU-validated stack constraint forbids multi-KB parser buffers on the
    /// serial engine task's stack.
    pub console_parse: ParseBuf,
    pub motor_parse: ParseBuf,
    /// The UART read staging buffer and the `kv_parse` output array — ALSO
    /// members, for exactly the same reason. `KvPair` is 130 bytes, so
    /// `[KvPair; 32]` is 4160 bytes; as a stack local it inflated
    /// `serial_engine::run`'s frame to 4880 bytes (measured with objdump
    /// `entry a1, 0x1310`) against an 8192-byte task stack, versus 144 bytes
    /// for the C++ task, which keeps `rawbuf_`/`pairs_` as members of
    /// `SerialReader` with a comment saying why. Level-1 ISRs run on the
    /// interrupted task's stack, so the headroom is not notional.
    ///
    /// ONE shared pair of buffers, not one per tap: the console and motor
    /// drains run sequentially inside the same critical section and never
    /// hold a borrow across each other.
    pub scratch_raw: [u8; SCRATCH_RAW_BYTES],
    pub scratch_pairs: [KvPair; SCRATCH_PAIRS],
    /// Connection bookkeeping for the two surfaces that may command motion.
    ///
    /// INSIDE THE LOCK BY CONSTRUCTION, for the same reason `io` is: taking a
    /// lease and commanding motion must be one atomic act, or two surfaces can
    /// interleave between `acquire` and `command_motion` and the belt ends up
    /// owned by one and commanded by the other. `crate::control` is the only
    /// module that writes them.
    pub http_owner: crate::control::Owner,
    pub executor_owner: crate::control::Owner,
    /// Sticky ownership-loss interlock for the background program executor.
    ///
    /// Set under this lock by the serial task when the physical console takes
    /// control, and by the executor when it detects any other lease loss.
    /// Ordinary executor ticks may never clear it; only a future explicit
    /// Start/Resume transaction is allowed to do that.
    pub executor_inhibited: bool,
    /// Exact controller-clock instant of physical-console takeover.
    ///
    /// The serial task cannot take the program lock while it holds `guarded`
    /// (that would invert program→guarded), so it leaves this bounded marker
    /// for the executor to consume on its next tick.
    pub executor_inhibited_at: Option<Micros>,
}

pub const SCRATCH_RAW_BYTES: usize = 512;
pub const SCRATCH_PAIRS: usize = 32;

impl Guarded {
    pub const fn new() -> Self {
        Guarded {
            controller: SafetyController::new(),
            mode: ModeStateMachine::new(),
            io: SafetyIoImpl::new(),
            key_cache: KeyCache::new(),
            last_console_rx: Micros::ZERO,
            console_uart: ConsoleMotorUart::new(),
            motor_tap: MotorTapImpl::new(),
            cycle: EmulationCycle::new(),
            console_parse: ParseBuf::new(),
            motor_parse: ParseBuf::new(),
            scratch_raw: [0u8; SCRATCH_RAW_BYTES],
            scratch_pairs: [KvPair::empty(); SCRATCH_PAIRS],
            http_owner: crate::control::Owner::new(),
            executor_owner: crate::control::Owner::new(),
            executor_inhibited: false,
            executor_inhibited_at: None,
        }
    }

    /// Push the controller's commanded outputs to hardware. `SafetyIo::apply`
    /// is the only write site and orders tx before relay.
    pub fn apply_outputs(&mut self) {
        self.io.apply(self.controller.output_intent());
    }
}

impl Default for Guarded {
    fn default() -> Self {
        Self::new()
    }
}

/// A streaming KV parse buffer, mirroring `SerialReader`'s discipline:
/// append, parse, shift the unconsumed tail to the front.
///
/// The implementation moved to `safety_core::parse_buf` so it can be
/// HOST-TESTED: the local copy had a permanent-wedge defect (an unterminated
/// `[` pins `kv_parse`'s `consumed` at 0 forever, the buffer fills, every
/// later byte is silently dropped, and the console-takeover interlock dies
/// with no symptom). See that module for the analysis and the bounded
/// resynchronisation that fixes it.
pub type ParseBuf = safety_core::parse_buf::ParseBuf<4096>;

/// The motor writer, behind its own mutex.
pub struct MotorWriter {
    pub uart: ConsoleMotorUart,
}

impl MotorWriter {
    pub const fn new() -> Self {
        MotorWriter {
            uart: ConsoleMotorUart::new(),
        }
    }
}

impl Default for MotorWriter {
    fn default() -> Self {
        Self::new()
    }
}

impl KvSink for MotorWriter {
    /// `MAX_WRITE_BYTES = 50`: KV commands are short, and an oversized write
    /// is REJECTED rather than truncated — same as the C++ `SerialWriter`.
    fn write_kv(&mut self, key: &str, value: &str) {
        let Some(frame) = kv_build(key, value) else {
            return;
        };
        if frame.len() > MAX_WRITE_BYTES {
            return;
        }
        self.uart.write(frame.as_bytes());
    }
}

pub const MAX_WRITE_BYTES: usize = 50;

pub struct FirmwareContext {
    pub guarded: Mutex<Guarded>,
    /// Separate lock: `uart_wait_tx_done` blocks ~50 ms at 9600 baud.
    pub writer: Mutex<MotorWriter>,
    /// The loaded workout and where it has got to.
    ///
    /// ITS OWN LOCK, and there is a MANDATORY ORDER: `program` is taken
    /// BEFORE `guarded`, never the other way round. Two holders exist — the
    /// interval executor task and the HTTP program endpoints — and both need
    /// the tick decision and the resulting belt command to be one atomic act,
    /// or a `POST /api/program/stop` can land between "the tick decided to
    /// start interval 3" and "the belt was told", leaving the belt running
    /// after a stop.
    ///
    /// Nothing takes `guarded` and then wants `program`, which is what makes
    /// the order sufficient rather than merely stated: the serial engine, the
    /// emulate cycle, the QEMU shim and the motion endpoints never touch a
    /// program at all.
    ///
    /// NOT inside `Guarded`: a ~1 KB program and the 1 s tick have no business
    /// widening the critical section that the 5 ms serial loop contends for.
    pub program: Mutex<program_core::ProgramState>,
    pub clock: Esp32Clock,
}

impl FirmwareContext {
    pub const fn new() -> Self {
        FirmwareContext {
            guarded: Mutex::new(Guarded::new()),
            writer: Mutex::new(MotorWriter::new()),
            program: Mutex::new(program_core::ProgramState::new()),
            clock: Esp32Clock::new(),
        }
    }
}

impl Default for FirmwareContext {
    fn default() -> Self {
        Self::new()
    }
}

/// A poisoned safety mutex means a task panicked while holding it. Panic ==
/// abort in this image (`panic = "abort"` via build-std `panic_abort`), so
/// this cannot actually happen; recover the guard rather than adding an
/// unwrap that would look like a real failure mode.
pub fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}
