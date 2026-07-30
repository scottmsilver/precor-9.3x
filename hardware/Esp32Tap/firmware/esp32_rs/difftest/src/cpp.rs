//! Safe wrappers over the C++ shim.
//!
//! Every `unsafe` block here is FFI marshalling with a `// SAFETY:` note.
//! TEST-HARNESS ONLY — counted separately from the firmware's unsafe budget.

use std::ffi::{c_char, CString};

pub const KV_FIELD_SIZE: usize = 64;

extern "C" {
    fn cpp_kv_parse(
        buf: *const u8,
        len: i32,
        max_pairs: i32,
        keys_out: *mut c_char,
        values_out: *mut c_char,
        consumed_out: *mut i32,
    ) -> i32;
    fn cpp_kv_build(key: *const c_char, value: *const c_char, out: *mut u8, cap: i32) -> i32;
    fn cpp_encode_speed_hex(tenths: i32, out: *mut c_char, cap: i32) -> i32;
    fn cpp_decode_speed_hex(hex: *const c_char) -> i32;
    fn cpp_encode_incline_hex(half_pct: i32, out: *mut c_char, cap: i32) -> i32;
    fn cpp_decode_incline_hex(hex: *const c_char) -> i32;

    fn cpp_mode_new() -> *mut core::ffi::c_void;
    fn cpp_mode_free(h: *mut core::ffi::c_void);
    fn cpp_mode_request_proxy(h: *mut core::ffi::c_void, enabled: i32) -> i32;
    fn cpp_mode_request_emulate(h: *mut core::ffi::c_void, enabled: i32) -> i32;
    fn cpp_mode_set_speed(h: *mut core::ffi::c_void, tenths: i32) -> i32;
    fn cpp_mode_set_speed_mph(h: *mut core::ffi::c_void, mph: f64) -> i32;
    fn cpp_mode_set_incline(h: *mut core::ffi::c_void, half_pct: i32) -> i32;
    fn cpp_mode_auto_proxy(
        h: *mut core::ffi::c_void,
        key: *const c_char,
        old_val: *const c_char,
        new_val: *const c_char,
    ) -> i32;
    fn cpp_mode_safety_timeout_reset(h: *mut core::ffi::c_void);
    fn cpp_mode_watchdog_reset(h: *mut core::ffi::c_void);
    fn cpp_mode_add_console_bytes(h: *mut core::ffi::c_void, n: u32);
    fn cpp_mode_add_motor_bytes(h: *mut core::ffi::c_void, n: u32);
    fn cpp_mode_snapshot(h: *mut core::ffi::c_void, out: *mut i64);

    fn cpp_ctl_new() -> *mut core::ffi::c_void;
    fn cpp_ctl_free(h: *mut core::ffi::c_void);
    fn cpp_ctl_connect(h: *mut core::ffi::c_void, t: i32, handle: i32, gen: i64) -> i32;
    fn cpp_ctl_acquire(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
    ) -> i32;
    fn cpp_ctl_heartbeat(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
    ) -> i32;
    fn cpp_ctl_command_motion(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        speed: i32,
        incline: i32,
        now: i64,
    ) -> i32;
    fn cpp_ctl_disconnect(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
    ) -> i32;
    fn cpp_ctl_disconnect_transport(h: *mut core::ffi::c_void, t: i32, now: i64) -> i32;
    fn cpp_ctl_observe_console_bytes(
        h: *mut core::ffi::c_void,
        data: *const u8,
        len: i32,
        now: i64,
    ) -> i32;
    fn cpp_ctl_request_emulate(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
        uart_idle_low: i32,
    ) -> i32;
    fn cpp_ctl_request_emulate_recovering(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
        uart_idle_low: i32,
    ) -> i32;
    fn cpp_ctl_observe_interframe_gap(h: *mut core::ffi::c_void, now: i64) -> i32;
    fn cpp_ctl_observe_relay_feedback(
        h: *mut core::ffi::c_void,
        nc_high: i32,
        no_high: i32,
        now: i64,
    ) -> i32;
    fn cpp_ctl_request_normal_exit(
        h: *mut core::ffi::c_void,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
    ) -> i32;
    fn cpp_ctl_set_tread_ok(h: *mut core::ffi::c_void, value: i32, now: i64);
    fn cpp_ctl_set_vbus_present_n(h: *mut core::ffi::c_void, level_high: i32);
    fn cpp_ctl_tick(h: *mut core::ffi::c_void, now: i64);
    fn cpp_ctl_safety_timeout_zero_motion(h: *mut core::ffi::c_void, now: i64);
    fn cpp_ctl_emergency_stop(h: *mut core::ffi::c_void, reason: *const c_char, now: i64);
    fn cpp_ctl_watchdog_stall(h: *mut core::ffi::c_void, now: i64);
    fn cpp_ctl_reset(h: *mut core::ffi::c_void, reason: *const c_char, now: i64);
    fn cpp_ctl_state(h: *mut core::ffi::c_void, out: *mut i64);
    fn cpp_ctl_event_at(
        h: *mut core::ffi::c_void,
        index: u64,
        out: *mut c_char,
        cap: i32,
    ) -> i32;
}

fn cstr_to_string(buf: &[c_char]) -> String {
    let bytes: Vec<u8> = buf
        .iter()
        .take_while(|&&c| c != 0)
        .map(|&c| c as u8)
        .collect();
    String::from_utf8_lossy(&bytes).into_owned()
}

// ── kv_protocol ─────────────────────────────────────────────────────

#[derive(Debug, PartialEq, Eq)]
pub struct CppParse {
    pub pairs: Vec<(String, String)>,
    pub consumed: usize,
}

pub fn kv_parse(buf: &[u8], max_pairs: usize) -> CppParse {
    let mut keys = vec![0 as c_char; 32 * KV_FIELD_SIZE];
    let mut values = vec![0 as c_char; 32 * KV_FIELD_SIZE];
    let mut consumed: i32 = 0;
    // `try_from`, not `as`: a slice longer than i32::MAX would narrow to a
    // NEGATIVE length, which the C++ shim would widen back into a huge
    // `size_t` and read out of bounds. Unreachable with these fixtures, but
    // the safe wrapper's contract has to hold for all inputs.
    // (codex review finding, test-harness-only.)
    let len = i32::try_from(buf.len()).expect("kv_parse input exceeds i32::MAX");
    // SAFETY: `buf` is a valid slice of `len` bytes and `len` is a faithful,
    // non-negative representation of `buf.len()`; `keys`/`values` are
    // 32 * 64 bytes, and the shim clamps `max_pairs` to 32, so the shim can
    // never write past either. `consumed` is a live local.
    let n = unsafe {
        cpp_kv_parse(
            buf.as_ptr(),
            len,
            max_pairs as i32,
            keys.as_mut_ptr(),
            values.as_mut_ptr(),
            &mut consumed,
        )
    };
    let pairs = (0..n as usize)
        .map(|i| {
            let lo = i * KV_FIELD_SIZE;
            (
                cstr_to_string(&keys[lo..lo + KV_FIELD_SIZE]),
                cstr_to_string(&values[lo..lo + KV_FIELD_SIZE]),
            )
        })
        .collect();
    CppParse {
        pairs,
        consumed: consumed as usize,
    }
}

pub fn kv_build(key: &str, value: &str) -> Vec<u8> {
    let k = CString::new(key).unwrap();
    let v = CString::new(value).unwrap();
    let mut out = vec![0u8; 512];
    // SAFETY: both CStrings are NUL-terminated and outlive the call; `out` has
    // 512 bytes and its capacity is passed as `cap`, which the shim respects.
    let n = unsafe { cpp_kv_build(k.as_ptr(), v.as_ptr(), out.as_mut_ptr(), 512) };
    out.truncate(n as usize);
    out
}

pub fn encode_speed_hex(tenths: i32) -> String {
    let mut buf = vec![0 as c_char; 64];
    // SAFETY: `buf` is 64 bytes and its capacity is passed as `cap`.
    unsafe { cpp_encode_speed_hex(tenths, buf.as_mut_ptr(), 64) };
    cstr_to_string(&buf)
}

pub fn decode_speed_hex(hex: &str) -> i32 {
    let Ok(s) = CString::new(hex) else {
        return -1; // interior NUL: not representable as a C string
    };
    // SAFETY: `s` is NUL-terminated and outlives the call.
    unsafe { cpp_decode_speed_hex(s.as_ptr()) }
}

pub fn encode_incline_hex(half_pct: i32) -> String {
    let mut buf = vec![0 as c_char; 64];
    // SAFETY: `buf` is 64 bytes and its capacity is passed as `cap`.
    unsafe { cpp_encode_incline_hex(half_pct, buf.as_mut_ptr(), 64) };
    cstr_to_string(&buf)
}

pub fn decode_incline_hex(hex: &str) -> i32 {
    let Ok(s) = CString::new(hex) else {
        return -1;
    };
    // SAFETY: `s` is NUL-terminated and outlives the call.
    unsafe { cpp_decode_incline_hex(s.as_ptr()) }
}

// ── mode_state ──────────────────────────────────────────────────────

/// Owning handle to a C++ `ModeStateMachine`. `Drop` frees it — the leak the
/// C++ harness would need `delete` for is impossible here.
pub struct CppMode(*mut core::ffi::c_void);

#[derive(Debug, PartialEq, Eq)]
pub struct CppModeSnapshot {
    pub mode: i64,
    pub speed_tenths: i64,
    pub speed_raw: i64,
    pub incline: i64,
    pub proxy: bool,
    pub emulate: bool,
    pub console_bytes: i64,
    pub motor_bytes: i64,
}

impl Default for CppMode {
    fn default() -> Self {
        Self::new()
    }
}

impl CppMode {
    pub fn new() -> Self {
        // SAFETY: the shim returns a fresh heap `ModeStateMachine*`; `Drop`
        // is the sole owner and frees it exactly once.
        CppMode(unsafe { cpp_mode_new() })
    }
    pub fn request_proxy(&mut self, enabled: bool) -> i32 {
        // SAFETY: `self.0` is a live handle for the lifetime of `self`.
        unsafe { cpp_mode_request_proxy(self.0, enabled as i32) }
    }
    pub fn request_emulate(&mut self, enabled: bool) -> i32 {
        // SAFETY: as above.
        unsafe { cpp_mode_request_emulate(self.0, enabled as i32) }
    }
    pub fn set_speed(&mut self, tenths: i32) -> i32 {
        // SAFETY: as above.
        unsafe { cpp_mode_set_speed(self.0, tenths) }
    }
    pub fn set_speed_mph(&mut self, mph: f64) -> i32 {
        // SAFETY: as above.
        unsafe { cpp_mode_set_speed_mph(self.0, mph) }
    }
    pub fn set_incline(&mut self, half_pct: i32) -> i32 {
        // SAFETY: as above.
        unsafe { cpp_mode_set_incline(self.0, half_pct) }
    }
    pub fn auto_proxy(&mut self, key: &str, old_val: &str, new_val: &str) -> i32 {
        let k = CString::new(key).unwrap();
        let o = CString::new(old_val).unwrap();
        let n = CString::new(new_val).unwrap();
        // SAFETY: all three CStrings are NUL-terminated and outlive the call.
        unsafe { cpp_mode_auto_proxy(self.0, k.as_ptr(), o.as_ptr(), n.as_ptr()) }
    }
    pub fn safety_timeout_reset(&mut self) {
        // SAFETY: as above.
        unsafe { cpp_mode_safety_timeout_reset(self.0) }
    }
    pub fn watchdog_reset(&mut self) {
        // SAFETY: as above.
        unsafe { cpp_mode_watchdog_reset(self.0) }
    }
    pub fn add_console_bytes(&mut self, n: u32) {
        // SAFETY: as above.
        unsafe { cpp_mode_add_console_bytes(self.0, n) }
    }
    pub fn add_motor_bytes(&mut self, n: u32) {
        // SAFETY: as above.
        unsafe { cpp_mode_add_motor_bytes(self.0, n) }
    }
    pub fn snapshot(&self) -> CppModeSnapshot {
        let mut out = [0i64; 8];
        // SAFETY: the shim writes exactly 8 i64s; `out` has 8.
        unsafe { cpp_mode_snapshot(self.0, out.as_mut_ptr()) };
        CppModeSnapshot {
            mode: out[0],
            speed_tenths: out[1],
            speed_raw: out[2],
            incline: out[3],
            proxy: out[4] != 0,
            emulate: out[5] != 0,
            console_bytes: out[6],
            motor_bytes: out[7],
        }
    }
}

impl Drop for CppMode {
    fn drop(&mut self) {
        // SAFETY: `self.0` was produced by `cpp_mode_new` and is freed once.
        unsafe { cpp_mode_free(self.0) }
    }
}

// ── safety_controller ───────────────────────────────────────────────

pub struct CppController(*mut core::ffi::c_void);

/// The full observable tuple the differential suites compare.
#[derive(Debug, PartialEq, Eq, Clone)]
pub struct CppState {
    pub mode: i64,
    pub speed: i64,
    pub incline: i64,
    pub tread_ok: bool,
    pub feedback: i64,
    pub fault_latched: bool,
    pub relay_cmd: bool,
    pub tx_enable: bool,
    pub usb_pullup: bool,
    pub last_frame_at: Option<i64>,
    pub owner: Option<(i64, i64, i64)>,
    pub lease_expires_at: Option<i64>,
    pub event_count: u64,
}

impl Default for CppController {
    fn default() -> Self {
        Self::new()
    }
}

impl CppController {
    pub fn new() -> Self {
        // SAFETY: fresh heap `SafetyController*`, freed once in `Drop`.
        CppController(unsafe { cpp_ctl_new() })
    }

    pub fn connect(&mut self, t: i32, handle: i32, gen: i64) -> bool {
        // SAFETY: `self.0` is a live handle for the lifetime of `self`.
        unsafe { cpp_ctl_connect(self.0, t, handle, gen) != 0 }
    }
    pub fn acquire(&mut self, t: i32, handle: i32, gen: i64, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_acquire(self.0, t, handle, gen, now) != 0 }
    }
    pub fn heartbeat(&mut self, t: i32, handle: i32, gen: i64, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_heartbeat(self.0, t, handle, gen, now) != 0 }
    }
    #[allow(clippy::too_many_arguments)]
    pub fn command_motion(
        &mut self,
        t: i32,
        handle: i32,
        gen: i64,
        speed: i32,
        incline: i32,
        now: i64,
    ) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_command_motion(self.0, t, handle, gen, speed, incline, now) != 0 }
    }
    pub fn disconnect(&mut self, t: i32, handle: i32, gen: i64, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_disconnect(self.0, t, handle, gen, now) != 0 }
    }
    pub fn disconnect_transport(&mut self, t: i32, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_disconnect_transport(self.0, t, now) != 0 }
    }
    pub fn observe_console_bytes(&mut self, data: &[u8], now: i64) -> i32 {
        // See the `kv_parse` note: `try_from`, not `as`.
        let len = i32::try_from(data.len()).expect("console input exceeds i32::MAX");
        // SAFETY: `data` is a valid slice of `len` bytes and `len` is a
        // faithful, non-negative representation of `data.len()`; the shim
        // reads at most that many.
        unsafe { cpp_ctl_observe_console_bytes(self.0, data.as_ptr(), len, now) }
    }
    pub fn request_emulate(
        &mut self,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
        uart_idle_low: bool,
    ) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_request_emulate(self.0, t, handle, gen, now, uart_idle_low as i32) != 0 }
    }
    pub fn request_emulate_recovering(
        &mut self,
        t: i32,
        handle: i32,
        gen: i64,
        now: i64,
        uart_idle_low: bool,
    ) -> bool {
        // SAFETY: as above.
        unsafe {
            cpp_ctl_request_emulate_recovering(
                self.0,
                t,
                handle,
                gen,
                now,
                uart_idle_low as i32,
            ) != 0
        }
    }
    pub fn observe_interframe_gap(&mut self, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_observe_interframe_gap(self.0, now) != 0 }
    }
    pub fn observe_relay_feedback(&mut self, nc_high: bool, no_high: bool, now: i64) -> i64 {
        // SAFETY: as above.
        unsafe {
            cpp_ctl_observe_relay_feedback(self.0, nc_high as i32, no_high as i32, now) as i64
        }
    }
    pub fn request_normal_exit(&mut self, t: i32, handle: i32, gen: i64, now: i64) -> bool {
        // SAFETY: as above.
        unsafe { cpp_ctl_request_normal_exit(self.0, t, handle, gen, now) != 0 }
    }
    pub fn set_tread_ok(&mut self, value: bool, now: i64) {
        // SAFETY: as above.
        unsafe { cpp_ctl_set_tread_ok(self.0, value as i32, now) }
    }
    pub fn set_vbus_present_n(&mut self, level_high: bool) {
        // SAFETY: as above.
        unsafe { cpp_ctl_set_vbus_present_n(self.0, level_high as i32) }
    }
    pub fn tick(&mut self, now: i64) {
        // SAFETY: as above.
        unsafe { cpp_ctl_tick(self.0, now) }
    }
    pub fn safety_timeout_zero_motion(&mut self, now: i64) {
        // SAFETY: as above.
        unsafe { cpp_ctl_safety_timeout_zero_motion(self.0, now) }
    }
    pub fn emergency_stop(&mut self, reason: &str, now: i64) {
        let r = CString::new(reason).unwrap();
        // SAFETY: `r` is NUL-terminated and outlives the call.
        unsafe { cpp_ctl_emergency_stop(self.0, r.as_ptr(), now) }
    }
    pub fn watchdog_stall(&mut self, now: i64) {
        // SAFETY: as above.
        unsafe { cpp_ctl_watchdog_stall(self.0, now) }
    }
    pub fn reset(&mut self, reason: &str, now: i64) {
        let r = CString::new(reason).unwrap();
        // SAFETY: `r` is NUL-terminated and outlives the call.
        unsafe { cpp_ctl_reset(self.0, r.as_ptr(), now) }
    }

    pub fn state(&self) -> CppState {
        let mut out = [0i64; 18];
        // SAFETY: the shim writes exactly 18 i64s; `out` has 18.
        unsafe { cpp_ctl_state(self.0, out.as_mut_ptr()) };
        CppState {
            mode: out[0],
            speed: out[1],
            incline: out[2],
            tread_ok: out[3] != 0,
            feedback: out[4],
            fault_latched: out[5] != 0,
            relay_cmd: out[6] != 0,
            tx_enable: out[7] != 0,
            usb_pullup: out[8] != 0,
            last_frame_at: (out[9] != 0).then_some(out[10]),
            owner: (out[11] != 0).then_some((out[12], out[13], out[14])),
            lease_expires_at: (out[15] != 0).then_some(out[16]),
            event_count: out[17] as u64,
        }
    }

    pub fn event_at(&self, index: u64) -> String {
        let mut buf = vec![0 as c_char; 128];
        // SAFETY: `buf` is 128 bytes and its capacity is passed as `cap`.
        unsafe { cpp_ctl_event_at(self.0, index, buf.as_mut_ptr(), 128) };
        cstr_to_string(&buf)
    }

    /// The last `n` events, oldest first — mirrors the host-test helper.
    pub fn last_events(&self, n: u64) -> Vec<String> {
        let count = self.state().event_count;
        let start = count.saturating_sub(n);
        (start..count).map(|i| self.event_at(i)).collect()
    }
}

impl Drop for CppController {
    fn drop(&mut self) {
        // SAFETY: `self.0` was produced by `cpp_ctl_new` and is freed once.
        unsafe { cpp_ctl_free(self.0) }
    }
}
