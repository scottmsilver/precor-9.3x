/*!
 * The FTMS peripheral — the treadmill as a Fitness Machine (service 0x1826).
 *
 * TRANSPORT ONLY. Every byte on the wire is produced or consumed by
 * `ble_core::ftms`, which is the host-tested, byte-for-byte port of the
 * working Pi daemon. This file registers the GATT table, advertises, and moves
 * those bytes; it contains no encoding and no opinion about safety.
 *
 * # The Control Point reaches the belt through `control.rs` and nowhere else
 *
 * A peer's write is parsed by `ble_core::ftms::parse_control_point` (total
 * over every byte string), turned into a `CpEffect` carrying `SpeedTenths` /
 * `InclineHalfPct`, and handed to [`crate::control::command`] — the SAME
 * function `POST /api/speed` and the interval executor call. One lease, one
 * set of clamps, one auto-emulate policy, one `apply_outputs()`.
 *
 * There is deliberately NO clamp here. The Pi daemon clamps in
 * `handle_control_command` because on that side each caller reaches
 * `treadmill_io` through its own socket write and there is no shared choke
 * point. Here there is, so a peer that asks for 40 mph is REFUSED and told
 * `RESULT_INVALID_PARAM` — where the Pi silently substituted 12 mph and moved
 * the belt at a speed nobody asked for.
 *
 * # A BLE peer is untrusted
 *
 * Anything within radio range can write the Control Point; there is no
 * pairing requirement on it (the FTMS spec does not mandate one, and neither
 * shipping client does). The defence is that the write is BOUNDED and
 * VALIDATED before it goes near the belt:
 *
 *  * the ATT value is copied into a fixed [`CP_MAX`]-byte stack buffer and a
 *    longer write is truncated to it — never allocated to fit,
 *  * `parse_control_point` rejects unknown opcodes and short parameters and
 *    cannot panic on any input,
 *  * the resulting motion is the safety controller's to accept or refuse.
 *
 * # Memory
 *
 * The GATT table is built ONCE at bring-up and leaked (NimBLE keeps pointers
 * into it for the life of the process, which is the life of the device, so a
 * `static` and a one-time leak are the same object with different syntax).
 * Nothing else here allocates: the notify path encodes into `[u8; 13]` on the
 * stack, and only ONE peripheral connection is tracked, so the resident cost
 * does not move with connection count.
 */

use crate::control::{self, Surface};
use crate::{logi, logw};
use ble_core::ftms as proto;
use core::sync::atomic::{AtomicI32, AtomicU16, AtomicU32, Ordering};
use esp_idf_sys as sys;
use safety_core::units::SpeedTenths;

/// Longest Control Point write we will look at. The longest one FTMS defines
/// that this device supports is 3 bytes (opcode + u16 parameter); 20 is one
/// legacy ATT payload and leaves room for a peer that pads.
const CP_MAX: usize = 20;

/// No connection.
const NO_CONN: u16 = 0xFFFF;

/// The peripheral connection, if any. ONE — this is a treadmill, not a hub,
/// and a second app connecting would fight the first over the Control Point.
static CONN: AtomicU16 = AtomicU16::new(NO_CONN);

/// Characteristic value handles, filled by NimBLE during registration.
static H_TREADMILL_DATA: AtomicU16 = AtomicU16::new(0);
static H_CONTROL_POINT: AtomicU16 = AtomicU16::new(0);
static H_MACHINE_STATUS: AtomicU16 = AtomicU16::new(0);
static H_TRAINING_STATUS: AtomicU16 = AtomicU16::new(0);

/// Seconds since the current peer connected. Feeds the idle alive-signal's
/// elapsed field when there is no workout — the daemon's `session_secs`.
static CONN_SECS: AtomicU32 = AtomicU32::new(0);

/// The speed a Start/Resume returns to: the last NON-ZERO speed this surface
/// accepted. Held here rather than in `ble_core` because that crate holds no
/// state at all, and rather than read back from the controller because after a
/// Stop the controller's answer is zero — resuming to zero is not a resume.
static RESUME_TENTHS: AtomicI32 = AtomicI32::new(0);

fn conn() -> Option<u16> {
    match CONN.load(Ordering::Relaxed) {
        NO_CONN => None,
        h => Some(h),
    }
}

// ---------------------------------------------------------------------------
// UUIDs
// ---------------------------------------------------------------------------

/// A 16-bit SIG UUID in NimBLE's tagged form.
fn uuid16(value: u16) -> sys::ble_uuid16_t {
    // SAFETY: `ble_uuid16_t` is a bindgen POD of two integers; all-zero is a
    // valid initial value and both fields are then set explicitly. Only the
    // struct's own bytes are touched.
    let mut u: sys::ble_uuid16_t = unsafe { core::mem::zeroed() };
    u.u.type_ = sys::BLE_UUID_TYPE_16 as u8;
    u.value = value;
    u
}

/// NimBLE wants `*const ble_uuid_t`; a `ble_uuid16_t` starts with one.
fn as_uuid(u: &'static sys::ble_uuid16_t) -> *const sys::ble_uuid_t {
    core::ptr::addr_of!(u.u)
}

// ---------------------------------------------------------------------------
// GATT registration
// ---------------------------------------------------------------------------

/// Build and register the FTMS service.
///
/// THE TABLE IS LEAKED ON PURPOSE. `ble_gatts_add_svcs` stores the pointers
/// and dereferences them for the life of the host — NimBLE's own examples use
/// `static const` tables for exactly this reason. A one-time `Box::leak` at
/// bring-up is the same object with the same lifetime, and it is the only way
/// to build a bindgen struct whose field set varies with the IDF version
/// without hand-writing a literal that a version bump would silently break.
/// It happens ONCE, so resident memory is constant.
pub fn register() -> Result<(), sys::esp_err_t> {
    let uuids: &'static mut [sys::ble_uuid16_t; 7] = Box::leak(Box::new([
        uuid16(proto::SERVICE_FTMS),
        uuid16(proto::CHAR_FEATURE),
        uuid16(proto::CHAR_TREADMILL_DATA),
        uuid16(proto::CHAR_SPEED_RANGE),
        uuid16(proto::CHAR_INCLINE_RANGE),
        uuid16(proto::CHAR_CONTROL_POINT),
        uuid16(proto::CHAR_MACHINE_STATUS),
    ]));
    let ts_uuid: &'static mut sys::ble_uuid16_t =
        Box::leak(Box::new(uuid16(proto::CHAR_TRAINING_STATUS)));

    // Value-handle out-params. NimBLE writes through these during
    // registration and never again; they are leaked so the pointers stay
    // valid for the call and beyond it.
    let handles: &'static mut [u16; 4] = Box::leak(Box::new([0u16; 4]));

    let mut chrs: Vec<sys::ble_gatt_chr_def> = Vec::with_capacity(8);
    let mut chr = |uuid: *const sys::ble_uuid_t, flags: u32, val_handle: *mut u16| {
        // SAFETY: `ble_gatt_chr_def` is a bindgen POD of pointers and
        // integers; zero is its documented "absent" value for every field
        // (NimBLE terminates the array on a NULL uuid), and the fields this
        // build cares about are set explicitly below. Zeroing rather than
        // naming every field is what keeps this correct across IDF versions
        // that add fields.
        let mut c: sys::ble_gatt_chr_def = unsafe { core::mem::zeroed() };
        c.uuid = uuid;
        c.access_cb = Some(access_cb);
        c.flags = flags;
        c.val_handle = val_handle;
        chrs.push(c);
    };

    let f_read = sys::BLE_GATT_CHR_F_READ;
    let f_notify = sys::BLE_GATT_CHR_F_NOTIFY;
    let f_indicate = sys::BLE_GATT_CHR_F_INDICATE;
    let f_write = sys::BLE_GATT_CHR_F_WRITE;

    chr(as_uuid(&uuids[1]), f_read, core::ptr::null_mut());
    chr(
        as_uuid(&uuids[2]),
        f_read | f_notify,
        core::ptr::addr_of_mut!(handles[0]),
    );
    chr(as_uuid(&uuids[3]), f_read, core::ptr::null_mut());
    chr(as_uuid(&uuids[4]), f_read, core::ptr::null_mut());
    // Control Point: write + indicate is what the spec requires. NOTIFY is
    // advertised as well, and that is the daemon's choice carried across —
    // "FTMS spec requires indicate, but many apps (Kinomap, etc.) only
    // subscribe to notifications. We advertise both so either works."
    chr(
        as_uuid(&uuids[5]),
        f_write | f_indicate | f_notify,
        core::ptr::addr_of_mut!(handles[1]),
    );
    chr(
        as_uuid(&uuids[6]),
        f_read | f_notify,
        core::ptr::addr_of_mut!(handles[2]),
    );
    chr(
        as_uuid(ts_uuid),
        f_read | f_notify,
        core::ptr::addr_of_mut!(handles[3]),
    );
    // Terminator: an all-zero entry, which is a NULL uuid.
    // SAFETY: as above — a zeroed POD, which is NimBLE's array terminator.
    chrs.push(unsafe { core::mem::zeroed() });

    let chrs: &'static mut [sys::ble_gatt_chr_def] = Vec::leak(chrs);

    // SAFETY: zeroed POD; every field NimBLE reads is set below, and the
    // trailing entry is the all-zero terminator the API documents.
    let mut svc: sys::ble_gatt_svc_def = unsafe { core::mem::zeroed() };
    svc.type_ = sys::BLE_GATT_SVC_TYPE_PRIMARY as u8;
    svc.uuid = as_uuid(&uuids[0]);
    svc.characteristics = chrs.as_mut_ptr();
    // SAFETY: as above.
    let svcs: &'static mut [sys::ble_gatt_svc_def; 2] =
        Box::leak(Box::new([svc, unsafe { core::mem::zeroed() }]));

    // SAFETY: `svcs` is a `'static` NULL-terminated table whose pointers all
    // reference leaked, never-freed storage. `count_cfg` only measures it;
    // `add_svcs` records it. Both are called once, on the bring-up task,
    // before the host task exists.
    let rc = unsafe {
        let rc = sys::ble_gatts_count_cfg(svcs.as_ptr());
        if rc != 0 {
            rc
        } else {
            sys::ble_gatts_add_svcs(svcs.as_ptr())
        }
    };
    if rc != 0 {
        logw!("ble: FTMS service registration failed ({})", rc);
        return Err(sys::ESP_FAIL);
    }

    // The handles are filled during `ble_gatts_start`, which NimBLE calls from
    // its host task on sync. Publish the ADDRESSES now and read the values
    // later — see `publish_handles`.
    HANDLE_STORE.store(handles.as_mut_ptr() as usize as i32, Ordering::Relaxed);
    logi!("ble: FTMS service registered");
    Ok(())
}

/// Address of the four-handle array, as an `i32` so it can live in an atomic.
/// Set once by [`register`], read once by [`publish_handles`].
static HANDLE_STORE: AtomicI32 = AtomicI32::new(0);

/// Copy the value handles NimBLE filled in during `ble_gatts_start`.
///
/// Called from the sync callback, i.e. after the GATT database has been
/// started — before that the array is all zeros and a notify would go to
/// handle 0.
fn publish_handles() {
    let p = HANDLE_STORE.load(Ordering::Relaxed);
    if p == 0 {
        return;
    }
    // SAFETY: `p` is the address of a leaked `[u16; 4]` that is never freed
    // and never moved. NimBLE has finished writing it (this runs after
    // `ble_gatts_start`), and this is the only reader.
    let h = unsafe { *(p as usize as *const [u16; 4]) };
    H_TREADMILL_DATA.store(h[0], Ordering::Relaxed);
    H_CONTROL_POINT.store(h[1], Ordering::Relaxed);
    H_MACHINE_STATUS.store(h[2], Ordering::Relaxed);
    H_TRAINING_STATUS.store(h[3], Ordering::Relaxed);
}

// ---------------------------------------------------------------------------
// Characteristic access
// ---------------------------------------------------------------------------

/// NimBLE's read/write callback for every FTMS characteristic.
///
/// SAFETY: invoked by NimBLE on its host task. `ctxt` is live for the call and
/// nothing derived from it is retained; `arg` is unused.
unsafe extern "C" fn access_cb(
    conn_handle: u16,
    attr_handle: u16,
    ctxt: *mut sys::ble_gatt_access_ctxt,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if ctxt.is_null() {
        return sys::BLE_ATT_ERR_UNLIKELY as core::ffi::c_int;
    }
    let op = (*ctxt).op as u32;
    let uuid = (*ctxt).__bindgen_anon_1.chr;
    if uuid.is_null() {
        return sys::BLE_ATT_ERR_UNLIKELY as core::ffi::c_int;
    }
    let short = uuid16_of((*uuid).uuid);

    if op == sys::BLE_GATT_ACCESS_OP_READ_CHR {
        return match short {
            Some(proto::CHAR_FEATURE) => append(ctxt, &proto::encode_feature()),
            Some(proto::CHAR_SPEED_RANGE) => append(ctxt, &proto::encode_speed_range()),
            Some(proto::CHAR_INCLINE_RANGE) => append(ctxt, &proto::encode_incline_range()),
            Some(proto::CHAR_TREADMILL_DATA) => append(ctxt, &treadmill_data()),
            // Machine Status and Training Status are notify-only in practice;
            // answering a read with an empty value is legal and is what the
            // daemon's snapshot read does for a machine with nothing to say.
            Some(_) => 0,
            None => sys::BLE_ATT_ERR_UNLIKELY as core::ffi::c_int,
        };
    }

    if op == sys::BLE_GATT_ACCESS_OP_WRITE_CHR && short == Some(proto::CHAR_CONTROL_POINT) {
        let mut buf = [0u8; CP_MAX];
        let mut n: u16 = 0;
        // BOUNDED COPY. `ble_hs_mbuf_to_flat` writes at most `CP_MAX` bytes
        // and reports what it took; a longer write is truncated here rather
        // than sized-to-fit anywhere.
        //
        // SAFETY: `(*ctxt).om` is the live mbuf NimBLE is handing us for this
        // call; `buf` is a live exclusive stack buffer whose length is passed
        // explicitly, and `n` is written by the callee.
        let rc = sys::ble_hs_mbuf_to_flat(
            (*ctxt).om,
            buf.as_mut_ptr() as *mut core::ffi::c_void,
            CP_MAX as u16,
            &mut n,
        );
        if rc != 0 {
            return sys::BLE_ATT_ERR_UNLIKELY as core::ffi::c_int;
        }
        let n = core::cmp::min(n as usize, CP_MAX);
        on_control_point(conn_handle, attr_handle, &buf[..n]);
        return 0;
    }

    sys::BLE_ATT_ERR_UNLIKELY as core::ffi::c_int
}

/// The 16-bit value of a UUID, or `None` if it is not a 16-bit one.
///
/// SAFETY: `u` is a live `ble_uuid_t` owned by the GATT table.
unsafe fn uuid16_of(u: *const sys::ble_uuid_t) -> Option<u16> {
    if u.is_null() || (*u).type_ != sys::BLE_UUID_TYPE_16 as u8 {
        return None;
    }
    Some((*(u as *const sys::ble_uuid16_t)).value)
}

/// Append a read response value to the access context's mbuf.
///
/// SAFETY: `ctxt` is live for the call; `data` is a live borrow that outlives
/// it (a stack array in the caller). NimBLE copies the bytes.
unsafe fn append(ctxt: *mut sys::ble_gatt_access_ctxt, data: &[u8]) -> core::ffi::c_int {
    let rc = sys::os_mbuf_append(
        (*ctxt).om,
        data.as_ptr() as *const core::ffi::c_void,
        data.len() as u16,
    );
    if rc == 0 {
        0
    } else {
        sys::BLE_ATT_ERR_INSUFFICIENT_RES as core::ffi::c_int
    }
}

// ---------------------------------------------------------------------------
// The Control Point — the belt edge
// ---------------------------------------------------------------------------

/// Handle one Control Point write, end to end: parse, act, answer.
///
/// SAFETY: runs on NimBLE's host task; the only raw operations are the
/// notify/indicate sends, each of which is its own block below.
unsafe fn on_control_point(conn_handle: u16, cp_handle: u16, bytes: &[u8]) {
    let Some(cmd) = proto::parse_control_point(bytes) else {
        // Unknown opcode, or a parameter shorter than the opcode requires.
        // The daemon answers with the byte it saw and NOT_SUPPORTED; do the
        // same, so a client's error message names the opcode it sent.
        let op = bytes.first().copied().unwrap_or(0);
        logw!("ble: control point opcode {} not supported", op);
        indicate(
            conn_handle,
            cp_handle,
            &proto::encode_control_response(op, proto::RESULT_NOT_SUPPORTED),
        );
        return;
    };

    // Fitness Machine Status and Training Status FIRST, exactly as the daemon
    // orders it: a client's own UI mirrors what it asked for, and it should
    // see that echo whether or not the belt accepts the motion.
    if let Some(note) = proto::encode_status_notification(cmd) {
        notify(conn_handle, H_MACHINE_STATUS.load(Ordering::Relaxed), note.as_slice());
    }
    if let Some(ts) = proto::encode_training_status(cmd) {
        notify(conn_handle, H_TRAINING_STATUS.load(Ordering::Relaxed), &ts);
    }

    let effect = proto::effect_of(cmd);
    let result = apply(effect);

    indicate(
        conn_handle,
        cp_handle,
        &proto::encode_control_response(cmd.opcode(), result),
    );
}

/// Turn a Control Point effect into belt motion, through THE ONE PATH.
///
/// Returns the FTMS result code for the indication. No clamping, no
/// pre-validation: `control::command` decides, and this function reports what
/// it decided.
fn apply(effect: proto::CpEffect) -> u8 {
    let now_belt = {
        let g = crate::context::lock(&crate::CTX.guarded);
        proto::BeltNow {
            speed: g.controller.speed_tenths(),
            incline: g.controller.incline_half_percent(),
            resume_speed: SpeedTenths::new(RESUME_TENTHS.load(Ordering::Relaxed)),
        }
    };

    let Some((speed, incline)) = proto::motion_for(effect, now_belt) else {
        // AckOnly — RequestControl. Nothing is commanded, and that is NOT the
        // same as re-commanding the current motion, which would take the lease
        // away from a running program.
        return proto::RESULT_SUCCESS;
    };

    let outcome = {
        let mut g = crate::context::lock(&crate::CTX.guarded);
        let now = crate::CTX.clock.now();
        control::command(&mut g, Surface::Http, speed, incline, now)
    };

    match outcome {
        Ok(()) => {
            if speed.get() > 0 {
                RESUME_TENTHS.store(speed.get(), Ordering::Relaxed);
            }
            logi!(
                "ble: control point accepted (speed {} tenths, incline {} half-pct)",
                speed.get(),
                incline.get()
            );
            proto::RESULT_SUCCESS
        }
        Err(control::Reject::NotOwner) => {
            logw!("ble: control point refused — a program owns the belt");
            proto::result_for_reject(proto::CpReject::NotOwner)
        }
        Err(control::Reject::Refused) => {
            logw!("ble: control point refused by the safety controller");
            proto::result_for_reject(proto::CpReject::Refused)
        }
        Err(control::Reject::GenerationExhausted) => {
            proto::result_for_reject(proto::CpReject::Other)
        }
    }
}

/// WHY THE BLE SURFACE IS `Surface::Http`, and why that is not a shortcut.
///
/// `Surface` selects an owner slot in `Guarded` and a `Transport`/handle pair
/// for the lease. A THIRD surface would mean a third lease holder, and
/// `SafetyController::connect` emergency-stops when a new generation
/// supersedes a lease-holding one — so a phone on Bluetooth and the same phone
/// on HTTP would take the belt from each other, relay-cycling the treadmill
/// mid-stride every time the user touched the other control.
///
/// They are the same user commanding the same machine from the same room, and
/// treating them as one owner is the honest model. It also means the existing
/// arbitration is unchanged: a running program still refuses BOTH, with the
/// same 409-shaped answer.
const _WHY_HTTP_SURFACE: () = ();

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

/// The current Treadmill Data value.
fn treadmill_data() -> [u8; proto::TREADMILL_DATA_LEN] {
    let (speed, incline) = {
        let g = crate::context::lock(&crate::CTX.guarded);
        (
            g.controller.speed_tenths(),
            g.controller.incline_half_percent(),
        )
    };
    #[cfg(feature = "net")]
    let (distance_meters, elapsed_secs) = crate::net::session::ftms_metrics();
    #[cfg(not(feature = "net"))]
    let (distance_meters, elapsed_secs) = (0u32, 0u16);

    let snap = proto::TreadmillSnapshot {
        speed,
        incline,
        distance_meters,
        elapsed_secs,
    };
    proto::encode_treadmill_data_with_alive(&snap, CONN_SECS.load(Ordering::Relaxed) as u16)
}

/// Send a notification. A no-op when nothing is subscribed or the handle has
/// not been published yet, so a mis-ordered bring-up drops a frame instead of
/// notifying attribute 0.
fn notify(conn_handle: u16, attr_handle: u16, data: &[u8]) {
    if attr_handle == 0 || data.is_empty() {
        return;
    }
    // SAFETY: `ble_hs_mbuf_from_flat` COPIES the bytes into a new mbuf, which
    // `ble_gatts_notify_custom` then consumes (it frees the mbuf on every
    // path, success or failure — NimBLE's documented ownership transfer). A
    // null return means the msys pool is exhausted, which is a dropped frame
    // and nothing worse.
    unsafe {
        let om = sys::ble_hs_mbuf_from_flat(
            data.as_ptr() as *const core::ffi::c_void,
            data.len() as u16,
        );
        if om.is_null() {
            return;
        }
        sys::ble_gatts_notify_custom(conn_handle, attr_handle, om);
    }
}

/// Send an indication (the Control Point's acknowledged response).
fn indicate(conn_handle: u16, attr_handle: u16, data: &[u8]) {
    if attr_handle == 0 || data.is_empty() {
        return;
    }
    // SAFETY: same ownership transfer as `notify` — the mbuf is a fresh copy
    // and `ble_gatts_indicate_custom` consumes it on every path.
    unsafe {
        let om = sys::ble_hs_mbuf_from_flat(
            data.as_ptr() as *const core::ffi::c_void,
            data.len() as u16,
        );
        if om.is_null() {
            return;
        }
        sys::ble_gatts_indicate_custom(conn_handle, attr_handle, om);
    }
}

/// 1 Hz: push Treadmill Data to the connected peer.
///
/// The rate is the daemon's and every client expects it. Nothing is sent when
/// no peer is connected, so an unattended treadmill costs no radio time.
pub fn tick() {
    let Some(c) = conn() else {
        return;
    };
    CONN_SECS.fetch_add(1, Ordering::Relaxed);
    let data = treadmill_data();
    notify(c, H_TREADMILL_DATA.load(Ordering::Relaxed), &data);
}

// ---------------------------------------------------------------------------
// GAP
// ---------------------------------------------------------------------------

/// Advertising payload and start. Called on sync and after every disconnect —
/// a peripheral that stops advertising when its peer leaves is invisible.
pub fn start_advertising() {
    publish_handles();

    // SAFETY: `ble_hs_adv_fields` is a bindgen POD with bitfields; zero is the
    // documented "field absent" value for all of it, and every field this
    // advertisement uses is set explicitly. `NAME` and `SVC_UUID` are
    // `'static`, so the pointers stored into `fields` outlive the call that
    // reads them (NimBLE copies the payload into the controller).
    unsafe {
        let mut fields: sys::ble_hs_adv_fields = core::mem::zeroed();
        fields.flags = (sys::BLE_HS_ADV_F_DISC_GEN | sys::BLE_HS_ADV_F_BREDR_UNSUP) as u8;

        let name = super::DEVICE_NAME.to_bytes();
        fields.name = name.as_ptr();
        fields.name_len = name.len() as u8;
        fields.set_name_is_complete(1);

        fields.uuids16 = core::ptr::addr_of!(SVC_UUID);
        fields.num_uuids16 = 1;
        fields.set_uuids16_is_complete(1);

        let rc = sys::ble_gap_adv_set_fields(&fields);
        if rc != 0 {
            logw!("ble: advertisement payload rejected ({})", rc);
            return;
        }

        let mut own_addr_type: u8 = 0;
        let rc = sys::ble_hs_id_infer_auto(0, &mut own_addr_type);
        if rc != 0 {
            logw!("ble: no identity address ({})", rc);
            return;
        }

        let mut params: sys::ble_gap_adv_params = core::mem::zeroed();
        params.conn_mode = sys::BLE_GAP_CONN_MODE_UND as u8;
        params.disc_mode = sys::BLE_GAP_DISC_MODE_GEN as u8;

        let rc = sys::ble_gap_adv_start(
            own_addr_type,
            core::ptr::null(),
            // `BLE_HS_FOREVER` is a C `#define` of `INT32_MAX` and does not
            // survive bindgen, so it is transcribed with the header it came
            // from (nimble/host/ble_hs.h) rather than guessed.
            i32::MAX,
            &params,
            Some(gap_event),
            core::ptr::null_mut(),
        );
        if rc != 0 {
            logw!("ble: advertising failed to start ({})", rc);
        } else {
            logi!("ble: advertising FTMS as {:?}", super::DEVICE_NAME);
        }
    }
}

/// The advertised service UUID. `static` because NimBLE reads it out of
/// `ble_hs_adv_fields` while building the payload.
static SVC_UUID: sys::ble_uuid16_t = sys::ble_uuid16_t {
    u: sys::ble_uuid_t {
        type_: sys::BLE_UUID_TYPE_16 as u8,
    },
    value: proto::SERVICE_FTMS,
};

/// GAP events for the PERIPHERAL role.
///
/// SAFETY: invoked by NimBLE on its host task; `event` is live for the call.
unsafe extern "C" fn gap_event(
    event: *mut sys::ble_gap_event,
    _arg: *mut core::ffi::c_void,
) -> core::ffi::c_int {
    if event.is_null() {
        return 0;
    }
    match (*event).type_ as u32 {
        sys::BLE_GAP_EVENT_CONNECT => {
            let c = &(*event).__bindgen_anon_1.connect;
            if c.status == 0 {
                CONN.store(c.conn_handle, Ordering::Relaxed);
                CONN_SECS.store(0, Ordering::Relaxed);
                logi!("ble: FTMS peer connected (handle {})", c.conn_handle);
            } else {
                // The connection failed; go back to being findable.
                start_advertising();
            }
        }
        sys::BLE_GAP_EVENT_DISCONNECT => {
            let d = &(*event).__bindgen_anon_1.disconnect;
            logi!("ble: FTMS peer disconnected (reason {})", d.reason);
            CONN.store(NO_CONN, Ordering::Relaxed);
            // A treadmill that stops advertising after its first app closes is
            // a treadmill nobody can find again without a power cycle.
            start_advertising();
        }
        sys::BLE_GAP_EVENT_ADV_COMPLETE => start_advertising(),
        _ => {}
    }
    0
}
