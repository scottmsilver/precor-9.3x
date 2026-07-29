/*!
 * The HRM central — this device connects OUT to a heart-rate strap.
 *
 * Port of what `rust/hrm/src/scanner.rs` DOES, not of how it does it: that
 * side is `bluer` over BlueZ D-Bus and none of it is portable. The one piece
 * that did come across unchanged is the measurement parsing, which lives in
 * `ble_core::hrm` with the daemon's vectors.
 *
 * The sequence, which is the whole of this file:
 *
 * ```text
 *   scan  ->  a peer advertising Heart Rate (0x180D)
 *         ->  connect
 *         ->  discover service 0x180D
 *         ->  discover characteristic 0x2A37 inside it
 *         ->  discover its Client Characteristic Configuration descriptor
 *         ->  write 0x0001 to that descriptor  (subscribe)
 *         ->  BLE_GAP_EVENT_NOTIFY_RX, once per heartbeat
 * ```
 *
 * Each step is a NimBLE callback, so the "state machine" is the callback chain
 * itself plus three handles. There is no queue and no retry ladder: a step
 * that fails drops the link, and the 1 Hz [`tick`] starts a fresh scan. An
 * intermittent that a retry ladder would paper over is worse than a clean
 * reconnect that happens a second later.
 *
 * # Heart rate is ADVISORY
 *
 * Nothing here can move the belt, and nothing in the safety path or the belt
 * path reads a heart rate. A strap is a third-party device sending arbitrary
 * bytes; the worst it can do is put a wrong number on a screen, and its name
 * is sanitised by `ble_core::peer` before it reaches a JSON string.
 *
 * # Memory
 *
 * One connection, three `AtomicU16` handles, and the bounded scan list in
 * [`crate::hr`]. Notification payloads are copied into a fixed stack buffer.
 * Nothing grows with the number of straps in the room or with time.
 */

use crate::hr;
use crate::{logi, logw};
use ble_core::hrm as proto;
use ble_core::peer::Addr;
use core::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use esp_idf_sys as sys;

const NO_CONN: u16 = 0xFFFF;

/// Longest notification payload we look at. A Heart Rate Measurement with
/// every optional field present (uint16 HR, energy expended, and a handful of
/// RR intervals) fits well inside this; a longer one is truncated rather than
/// sized-to-fit, and `parse_hr_measurement` only reads the first three bytes
/// anyway.
const NOTIFY_MAX: usize = 32;

/// How long one scan runs before it is considered finished, in ms.
const SCAN_MS: i32 = 8_000;

static CONN: AtomicU16 = AtomicU16::new(NO_CONN);
static HR_VAL_HANDLE: AtomicU16 = AtomicU16::new(0);
static SVC_START: AtomicU16 = AtomicU16::new(0);
static SVC_END: AtomicU16 = AtomicU16::new(0);
static SCANNING: AtomicBool = AtomicBool::new(false);

fn conn() -> Option<u16> {
    match CONN.load(Ordering::Relaxed) {
        NO_CONN => None,
        h => Some(h),
    }
}

fn hr_uuid() -> sys::ble_uuid16_t {
    // SAFETY: a bindgen POD of two integers; zero is valid and both fields are
    // set explicitly.
    let mut u: sys::ble_uuid16_t = unsafe { core::mem::zeroed() };
    u.u.type_ = sys::BLE_UUID_TYPE_16 as u8;
    u.value = proto::SERVICE_HEART_RATE;
    u
}

fn chr_uuid() -> sys::ble_uuid16_t {
    // SAFETY: as above.
    let mut u: sys::ble_uuid16_t = unsafe { core::mem::zeroed() };
    u.u.type_ = sys::BLE_UUID_TYPE_16 as u8;
    u.value = proto::CHAR_HR_MEASUREMENT;
    u
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/// Start a bounded scan. Idempotent — a scan already running is left alone.
pub fn start_scan() {
    if SCANNING.swap(true, Ordering::AcqRel) {
        return;
    }
    hr::set_scanning(true);

    // SAFETY: `params` is a zeroed bindgen POD filled explicitly and read for
    // the duration of the call; `ble_hs_id_infer_auto` writes one byte through
    // a live exclusive borrow.
    let rc = unsafe {
        let mut own_addr_type: u8 = 0;
        let rc = sys::ble_hs_id_infer_auto(0, &mut own_addr_type);
        if rc != 0 {
            rc
        } else {
            let mut params: sys::ble_gap_disc_params = core::mem::zeroed();
            params.itvl = 0; // NimBLE default
            params.window = 0;
            params.filter_policy = 0;
            params.set_passive(0); // active: ask for scan responses, which is
                                   // where most straps put their name
            params.set_filter_duplicates(1);
            params.set_limited(0);
            sys::ble_gap_disc(
                own_addr_type,
                SCAN_MS,
                &params,
                Some(disc_event),
                core::ptr::null_mut(),
            )
        }
    };
    if rc != 0 {
        SCANNING.store(false, Ordering::Release);
        hr::set_scanning(false);
        // BLE_HS_EALREADY is benign — the controller is already scanning.
        logw!("ble: HRM scan did not start ({})", rc);
    } else {
        logi!("ble: scanning for a heart-rate strap");
    }
}

fn stop_scan() {
    if !SCANNING.swap(false, Ordering::AcqRel) {
        return;
    }
    hr::set_scanning(false);
    // SAFETY: no arguments; returns BLE_HS_EALREADY if no scan is running,
    // which is exactly the case this guard already handled and is harmless.
    unsafe { sys::ble_gap_disc_cancel() };
}

/// GAP events for the CENTRAL role: scan results, our outgoing connection,
/// and the notifications that are the whole point.
///
/// SAFETY: invoked by NimBLE on its host task; `event` is live for the call
/// and nothing derived from it is retained past it.
unsafe extern "C" fn disc_event(
    event: *mut sys::ble_gap_event,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if event.is_null() {
        return 0;
    }
    match (*event).type_ as u32 {
        sys::BLE_GAP_EVENT_DISC => {
            let d = &(*event).__bindgen_anon_1.disc;
            on_advert(d);
        }
        sys::BLE_GAP_EVENT_DISC_COMPLETE => {
            SCANNING.store(false, Ordering::Release);
            hr::set_scanning(false);
            // Nothing chosen and nothing saved: the app decides. The results
            // are already published for `GET /api/hrm`.
            maybe_auto_connect();
        }
        sys::BLE_GAP_EVENT_CONNECT => {
            let c = &(*event).__bindgen_anon_1.connect;
            if c.status == 0 {
                CONN.store(c.conn_handle, Ordering::Relaxed);
                logi!("ble: strap connected (handle {})", c.conn_handle);
                let u = hr_uuid();
                // SAFETY: `u` is a live stack value read for the duration of
                // the call; NimBLE copies what it needs into its procedure
                // state before returning.
                let rc = sys::ble_gattc_disc_svc_by_uuid(
                    c.conn_handle,
                    core::ptr::addr_of!(u.u),
                    Some(on_service),
                    core::ptr::null_mut(),
                );
                if rc != 0 {
                    logw!("ble: HR service discovery did not start ({})", rc);
                    drop_link();
                }
            } else {
                logw!("ble: strap connection failed ({})", c.status);
                CONN.store(NO_CONN, Ordering::Relaxed);
            }
        }
        sys::BLE_GAP_EVENT_DISCONNECT => {
            let d = &(*event).__bindgen_anon_1.disconnect;
            logi!("ble: strap disconnected (reason {})", d.reason);
            CONN.store(NO_CONN, Ordering::Relaxed);
            HR_VAL_HANDLE.store(0, Ordering::Relaxed);
            hr::on_disconnected();
        }
        sys::BLE_GAP_EVENT_NOTIFY_RX => {
            let n = &(*event).__bindgen_anon_1.notify_rx;
            if n.attr_handle == HR_VAL_HANDLE.load(Ordering::Relaxed) {
                let mut buf = [0u8; NOTIFY_MAX];
                let mut len: u16 = 0;
                // BOUNDED COPY — a strap is untrusted and this is its payload.
                //
                // SAFETY: `n.om` is the live mbuf for this event; `buf` is a
                // live exclusive stack buffer whose length is passed
                // explicitly.
                let rc = sys::ble_hs_mbuf_to_flat(
                    n.om,
                    buf.as_mut_ptr() as *mut core::ffi::c_void,
                    NOTIFY_MAX as u16,
                    &mut len,
                );
                if rc == 0 {
                    let len = core::cmp::min(len as usize, NOTIFY_MAX);
                    hr::on_measurement(&buf[..len]);
                }
            }
        }
        _ => {}
    }
    0
}

/// One advertisement. Keep it only if it says it offers Heart Rate.
///
/// SAFETY: `d` is live for the call; `d.data` points at `d.length_data` bytes
/// owned by NimBLE for the duration of the callback.
unsafe fn on_advert(d: &sys::ble_gap_disc_desc) {
    let mut fields: sys::ble_hs_adv_fields = core::mem::zeroed();
    if sys::ble_hs_adv_parse_fields(&mut fields, d.data, d.length_data) != 0 {
        return;
    }
    if !offers_heart_rate(&fields) {
        return;
    }

    let name: &[u8] = if fields.name.is_null() || fields.name_len == 0 {
        &[]
    } else {
        core::slice::from_raw_parts(fields.name, fields.name_len as usize)
    };

    hr::on_scan_result(
        Addr::new(d.addr.val, d.addr.type_),
        name,
        d.rssi,
    );
}

/// Does this advertisement list the Heart Rate service?
///
/// SAFETY: `f` was filled by `ble_hs_adv_parse_fields`; `uuids16` points at
/// `num_uuids16` entries inside the advertisement NimBLE still owns.
unsafe fn offers_heart_rate(f: &sys::ble_hs_adv_fields) -> bool {
    if f.uuids16.is_null() || f.num_uuids16 == 0 {
        return false;
    }
    let list = core::slice::from_raw_parts(f.uuids16, f.num_uuids16 as usize);
    list.iter().any(|u| u.value == proto::SERVICE_HEART_RATE)
}

// ---------------------------------------------------------------------------
// Discovery chain
// ---------------------------------------------------------------------------

/// SAFETY: NimBLE discovery callback; the pointers are live for the call.
unsafe extern "C" fn on_service(
    conn_handle: u16,
    error: *const sys::ble_gatt_error,
    service: *const sys::ble_gatt_svc,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if !service.is_null() {
        SVC_START.store((*service).start_handle, Ordering::Relaxed);
        SVC_END.store((*service).end_handle, Ordering::Relaxed);
        return 0;
    }
    // A null service means the procedure ended. `status` says how.
    let status = if error.is_null() { 0 } else { (*error).status };
    let start = SVC_START.load(Ordering::Relaxed);
    if start == 0 {
        logw!("ble: strap has no Heart Rate service (status {})", status);
        drop_link();
        return 0;
    }
    let u = chr_uuid();
    // SAFETY: `u` is live for the call; NimBLE copies it into its procedure
    // state.
    let rc = sys::ble_gattc_disc_chrs_by_uuid(
        conn_handle,
        start,
        SVC_END.load(Ordering::Relaxed),
        core::ptr::addr_of!(u.u),
        Some(on_characteristic),
        core::ptr::null_mut(),
    );
    if rc != 0 {
        logw!("ble: HR characteristic discovery did not start ({})", rc);
        drop_link();
    }
    0
}

/// SAFETY: NimBLE discovery callback; the pointers are live for the call.
unsafe extern "C" fn on_characteristic(
    conn_handle: u16,
    _error: *const sys::ble_gatt_error,
    chr: *const sys::ble_gatt_chr,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if !chr.is_null() {
        HR_VAL_HANDLE.store((*chr).val_handle, Ordering::Relaxed);
        return 0;
    }
    let val = HR_VAL_HANDLE.load(Ordering::Relaxed);
    if val == 0 {
        logw!("ble: strap has no HR Measurement characteristic");
        drop_link();
        return 0;
    }
    // The CCCD sits immediately after the value handle, but "immediately" is a
    // convention rather than a guarantee, so it is DISCOVERED. `val + 1` as a
    // guess writes 0x0001 to whatever attribute happens to be there.
    let rc = sys::ble_gattc_disc_all_dscs(
        conn_handle,
        val,
        SVC_END.load(Ordering::Relaxed),
        Some(on_descriptor),
        core::ptr::null_mut(),
    );
    if rc != 0 {
        logw!("ble: CCCD discovery did not start ({})", rc);
        drop_link();
    }
    0
}

/// The Client Characteristic Configuration descriptor.
const CCCD_UUID: u16 = 0x2902;
/// Subscribe to notifications: bit 0 of the CCCD.
const CCCD_NOTIFY: [u8; 2] = [0x01, 0x00];

/// SAFETY: NimBLE discovery callback; the pointers are live for the call.
unsafe extern "C" fn on_descriptor(
    conn_handle: u16,
    _error: *const sys::ble_gatt_error,
    _chr_val_handle: u16,
    dsc: *const sys::ble_gatt_dsc,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if dsc.is_null() {
        return 0; // procedure ended; either we subscribed below or we did not
    }
    if (*dsc).uuid.u.type_ != sys::BLE_UUID_TYPE_16 as u8 {
        return 0;
    }
    // The union's 16-bit member. `ble_uuid_any_t` is a union whose first
    // member is the 16-bit form, which is why the type tag is checked first.
    let value = (*dsc).uuid.u16_.value;
    if value != CCCD_UUID {
        return 0;
    }
    // SAFETY: `CCCD_NOTIFY` is a `'static` two-byte constant; NimBLE copies it
    // into the write request before returning.
    let rc = sys::ble_gattc_write_flat(
        conn_handle,
        (*dsc).handle,
        CCCD_NOTIFY.as_ptr() as *const core::ffi::c_void,
        CCCD_NOTIFY.len() as u16,
        None,
        core::ptr::null_mut(),
    );
    if rc != 0 {
        logw!("ble: CCCD subscribe failed ({})", rc);
        drop_link();
        return 0;
    }
    // Publish the connection now, and NOT at BLE_GAP_EVENT_CONNECT: until the
    // CCCD write lands, no notification will ever arrive, and showing the user
    // a connected strap they are not receiving from is the same lie as a
    // frozen heart rate.
    let (addr, name) = target();
    hr::on_connected(addr, name.as_str().as_bytes());
    logi!("ble: subscribed to HR notifications");
    0
}

// ---------------------------------------------------------------------------
// Connecting
// ---------------------------------------------------------------------------

/// The strap this central is attached to, and the name the scan saw for it.
///
/// `saved` is set by every path that starts a connection, so it is the target
/// by construction; the name is looked up in the bounded scan list and is
/// empty when the strap never advertised one.
fn target() -> (Addr, ble_core::peer::FixedName) {
    hr::with(|s| {
        for i in 0..s.found {
            if s.devices[i].addr == s.saved {
                return (s.devices[i].addr, s.devices[i].name);
            }
        }
        (s.saved, ble_core::peer::FixedName::EMPTY)
    })
}

/// Open a link to `addr`.
fn connect(addr: Addr) {
    if !addr.present || conn().is_some() {
        return;
    }
    hr::with(|s| s.saved = addr);
    stop_scan();
    // SAFETY: `peer` is a live stack value read for the duration of the call;
    // NimBLE copies it into its connection state.
    let rc = unsafe {
        let mut own_addr_type: u8 = 0;
        let rc = sys::ble_hs_id_infer_auto(0, &mut own_addr_type);
        if rc != 0 {
            rc
        } else {
            let mut peer: sys::ble_addr_t = core::mem::zeroed();
            peer.type_ = addr.kind;
            peer.val = addr.val;
            sys::ble_gap_connect(
                own_addr_type,
                &peer,
                30_000,
                core::ptr::null(),
                Some(disc_event),
                core::ptr::null_mut(),
            )
        }
    };
    if rc != 0 {
        logw!("ble: connect to strap failed to start ({})", rc);
    }
}

/// Close the link, if any.
fn drop_link() {
    if let Some(c) = conn() {
        // SAFETY: `c` is a live connection handle; the call takes integers
        // only. BLE_ERR_REM_USER_CONN_TERM is the ordinary "we are done"
        // reason code.
        unsafe { sys::ble_gap_terminate(c, sys::ble_error_codes_BLE_ERR_REM_USER_CONN_TERM as u8) };
    }
    CONN.store(NO_CONN, Ordering::Relaxed);
    HR_VAL_HANDLE.store(0, Ordering::Relaxed);
    SVC_START.store(0, Ordering::Relaxed);
    hr::on_disconnected();
}

/// After a scan: connect to the remembered strap, or — when nothing is
/// remembered and the room contains exactly ONE heart-rate device — to that.
///
/// The "exactly one" rule is the Pi daemon's behaviour: it auto-connects to a
/// saved device and otherwise hands the list to the client to choose from.
/// With one candidate there is nothing to choose, and making the user tap
/// through a single-item picker on every boot would be worse than the Pi.
/// With two or more, the list is published and this does nothing.
fn maybe_auto_connect() {
    if conn().is_some() {
        return;
    }
    let target = hr::with(|s| {
        if s.saved.present {
            for i in 0..s.found {
                if s.devices[i].addr == s.saved {
                    return Some(s.devices[i].addr);
                }
            }
            return None;
        }
        if s.found == 1 {
            let a = s.devices[0].addr;
            s.saved = a;
            return Some(a);
        }
        None
    });
    if let Some(a) = target {
        connect(a);
    }
}

// ---------------------------------------------------------------------------
// The 1 Hz housekeeping
// ---------------------------------------------------------------------------

/// Act on whatever the app asked for, then keep looking if nothing is
/// connected.
pub fn tick() {
    match hr::take() {
        Some(hr::Command::Scan) => {
            hr::with(|s| s.found = 0);
            start_scan();
            return;
        }
        Some(hr::Command::Connect(addr)) => {
            hr::with(|s| s.saved = addr);
            if conn().is_some() {
                drop_link();
            }
            connect(addr);
            return;
        }
        Some(hr::Command::Forget) => {
            hr::with(|s| s.saved = Addr::NONE);
            drop_link();
            return;
        }
        None => {}
    }

    // Nothing connected and nothing in flight: look again. This is the whole
    // reconnect story — no backoff ladder, no retry counter. A strap that
    // walked out of range comes back when it comes back, and the cost of
    // asking is one bounded scan.
    if conn().is_none() && !SCANNING.load(Ordering::Relaxed) {
        start_scan();
    }
}
