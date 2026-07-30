//! Motor tap remapped to UART0 RX (UART2 is unwireable in this QEMU).
//!
//! `read()` drains UART0 RX and runs a LINE MUX: at line start, a line
//! beginning `"QT "` is diverted into a command queue; every other byte passes
//! through as motor-tap data. Unambiguous because the harness is the only
//! UART0-RX writer, frames commands as `"\nQT ...\n"`, and motor KV bytes
//! contain no `\n`.
//!
//! DIVERGENCE (deliberate, reported): the C++ uses a lock-free SPSC ring with
//! `AtomicU32` head/tail. Here it is a `Mutex<CmdQueue>`. The SPSC discipline
//! (exactly one producer, exactly one consumer) is an invariant Rust cannot
//! check, this is a 100 ms cold path, and the mutex removes an `UnsafeCell`
//! for zero measurable cost. No harness assertion changes.
//!
//! The queue is a FIXED ARRAY plus head/len, NOT a `Vec`. `push_command` is
//! reached from `read()`, which the serial engine calls every 5 ms, so a
//! `Vec` put a heap allocation (and an O(n) `remove(0)` memmove) on the hot
//! path of the very image the equivalence gate certifies. Rare is not never,
//! and "the validated image has a different allocation profile from
//! production" is exactly the gap that makes a gate stop meaning what it
//! says. `safety_core` cannot allocate at all (`no_std`, never names
//! `alloc`); this keeps the QEMU-test surface to the same rule.

use safety_core::FixedStr;
use std::sync::Mutex;

/// Max command line length.
pub const CMD_MAX: usize = 96;
/// Queue capacity.
pub const QUEUE_SLOTS: usize = 8;

/// Commands dropped because the queue was full. Reported by the shim task, not
/// logged here — this is reached from the serial read path.
pub static DROPPED: core::sync::atomic::AtomicU32 = core::sync::atomic::AtomicU32::new(0);

/// Take and clear the dropped-command count.
pub fn take_dropped() -> u32 {
    DROPPED.swap(0, core::sync::atomic::Ordering::Relaxed)
}

const CMD_PREFIX: &[u8] = b"QT ";

pub type CmdLine = FixedStr<CMD_MAX>;

pub struct QemuTestMotorTap {
    ready: bool,
    // Line mux state — touched only by the serial-engine task via `read()`.
    at_line_start: bool,
    prefix_matched: usize,
    in_cmd: bool,
    dropping_oversize: bool,
    cmd_buf: CmdLine,
    /// Producer: `read()`. Consumer: the qemu_test task.
    queue: Mutex<CmdQueue>,
}

/// Fixed-capacity FIFO. No allocation, no memmove on pop.
struct CmdQueue {
    slots: [CmdLine; QUEUE_SLOTS],
    head: usize,
    len: usize,
}

impl Default for QemuTestMotorTap {
    fn default() -> Self {
        Self::new()
    }
}

impl QemuTestMotorTap {
    pub const fn new() -> Self {
        QemuTestMotorTap {
            ready: false,
            at_line_start: true,
            prefix_matched: 0,
            in_cmd: false,
            dropping_oversize: false,
            cmd_buf: CmdLine::new(),
            queue: Mutex::new(CmdQueue {
                slots: [CmdLine::new(); QUEUE_SLOTS],
                head: 0,
                len: 0,
            }),
        }
    }

    /// Install the UART0 driver for RX. Do NOT reconfigure params: UART0 stays
    /// the debug console, and chardev bytes ignore baud anyway.
    pub fn init(&mut self) -> bool {
        // SAFETY: both calls take a valid UART port number by value.
        // `uart_driver_install` with a null queue pointer is the documented
        // "no event queue" form.
        unsafe {
            if !esp_idf_sys::uart_is_driver_installed(0)
                && esp_idf_sys::uart_driver_install(0, 1024, 0, 0, core::ptr::null_mut(), 0)
                    != esp_idf_sys::ESP_OK
            {
                return false;
            }
        }
        self.ready = true;
        true
    }

    fn push_command(&self, line: CmdLine) {
        let mut q = self.queue.lock().unwrap_or_else(|e| e.into_inner());
        if q.len < QUEUE_SLOTS {
            let tail = (q.head + q.len) % QUEUE_SLOTS;
            q.slots[tail] = line;
            q.len += 1;
        } else {
            // A DROP MUST BE VISIBLE. This used to drop silently on the
            // assumption that "the harness sends at a low rate" — false under
            // the adversarial storm scenario, which starves the consumer task
            // while still probing. The symptom was a probe that simply never
            // answered: 3 of 7 replies, no error, indistinguishable from a
            // wedged device. Counting here (no logging: this runs on the 5 ms
            // serial read path) lets the shim task report it on its next turn,
            // so silence keeps meaning "something is genuinely wrong".
            DROPPED.fetch_add(1, core::sync::atomic::Ordering::Relaxed);
        }
    }

    /// Pop one queued command line (`"QT ..."`, no newline). Single consumer:
    /// the qemu_test task.
    pub fn pop_command(&self) -> Option<CmdLine> {
        let mut q = self.queue.lock().unwrap_or_else(|e| e.into_inner());
        if q.len == 0 {
            return None;
        }
        let line = q.slots[q.head];
        q.head = (q.head + 1) % QUEUE_SLOTS;
        q.len -= 1;
        Some(line)
    }

    /// Drain UART0 RX through the line mux. Returns motor-tap bytes only.
    pub fn read(&mut self, out: &mut [u8]) -> usize {
        if !self.ready || out.len() < 8 {
            return 0;
        }
        let mut tmp = [0u8; 256];
        // Leave room for a flushed partial "QT " prefix (<= 3 bytes).
        let want = core::cmp::min(out.len() - 4, tmp.len());
        // SAFETY: `uart_read_bytes` writes at most `want` bytes, and `want`
        // is <= tmp.len(). Timeout 0 makes it non-blocking.
        let n = unsafe {
            esp_idf_sys::uart_read_bytes(
                0,
                tmp.as_mut_ptr() as *mut core::ffi::c_void,
                want as u32,
                0,
            )
        };
        if n <= 0 {
            return 0;
        }

        let mut w = 0usize;
        for &b in &tmp[..n as usize] {
            if self.in_cmd {
                if self.dropping_oversize {
                    if b == b'\n' {
                        self.dropping_oversize = false;
                        self.in_cmd = false;
                        self.at_line_start = true;
                    }
                    continue;
                }
                if b == b'\n' {
                    self.push_command(self.cmd_buf);
                    self.in_cmd = false;
                    self.cmd_buf.clear();
                    self.at_line_start = true;
                    continue;
                }
                if self.cmd_buf.len() >= CMD_MAX {
                    // Length-validated: drop the whole line.
                    self.dropping_oversize = true;
                    self.cmd_buf.clear();
                    continue;
                }
                self.cmd_buf.push_byte(b);
                continue;
            }
            if self.at_line_start || self.prefix_matched > 0 {
                if b == CMD_PREFIX[self.prefix_matched] {
                    self.prefix_matched += 1;
                    self.at_line_start = false;
                    if self.prefix_matched == CMD_PREFIX.len() {
                        self.in_cmd = true;
                        self.cmd_buf.clear();
                        self.cmd_buf.push_str("QT ");
                        self.prefix_matched = 0;
                    }
                    continue;
                }
                // Mismatch: flush the partially matched prefix as motor data.
                for p in 0..self.prefix_matched {
                    out[w] = CMD_PREFIX[p];
                    w += 1;
                }
                self.prefix_matched = 0;
            }
            out[w] = b;
            w += 1;
            self.at_line_start = b == b'\n';
        }
        w
    }
}


impl safety_core::hal::SerialIn for QemuTestMotorTap {
    fn read(&mut self, out: &mut [u8]) -> usize {
        QemuTestMotorTap::read(self, out)
    }
}
