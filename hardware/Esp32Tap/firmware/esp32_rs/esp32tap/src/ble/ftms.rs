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
use core::sync::atomic::{AtomicI32, AtomicU16, AtomicU32, AtomicUsize, Ordering};
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
// The Control Point's own handle is deliberately NOT kept: the indication is
// answered on the `attr_handle` NimBLE passes to the access callback for the
// write being answered, which is that handle by definition. A cached second
// copy would be state that can disagree with the request in hand.
static H_MACHINE_STATUS: AtomicU16 = AtomicU16::new(0);
static H_TRAINING_STATUS: AtomicU16 = AtomicU16::new(0);

/// Seconds since the current peer connected. Feeds the idle alive-signal's
/// elapsed field when there is no workout — the daemon's `session_secs`.
static CONN_SECS: AtomicU32 = AtomicU32::new(0);

/// Which characteristics the CURRENT peer has enabled notifications on, as a
/// bitmask. One byte, and there is only ever one peripheral connection, so
/// this does not move with connection count.
///
/// WHY THIS EXISTS. `ble_gatts_notify_custom` and `ble_gatts_indicate_custom`
/// are the UNCONDITIONAL variants — they send the PDU as given, without
/// consulting anybody's CCCD (`ble_gatts_chr_updated` is the one that does).
/// With no `BLE_GAP_EVENT_SUBSCRIBE` arm the device never learned a client's
/// CCCD state at all, so a peer that connected and browsed services without
/// subscribing was sent an unsolicited 13-byte Handle Value Notification every
/// second for as long as it stayed connected. GATT forbids notifying a
/// characteristic the client has not configured: a strict client stack
/// discards them, a defensive one may disconnect, and it burns radio time on
/// every connected-but-idle app.
///
/// `AtomicU32` for a value that needs three bits, where `AtomicU8` is the
/// obvious type: the ESP32-S3's only atomic read-modify-write instruction is
/// the 32-bit `S32C1I`, so a sub-word RMW is a masked compare-exchange loop
/// the compiler synthesises. A word-sized atomic is the native one here.
static SUBSCRIBED: AtomicU32 = AtomicU32::new(0);
const SUB_TREADMILL_DATA: u32 = 1 << 0;
const SUB_MACHINE_STATUS: u32 = 1 << 1;
const SUB_TRAINING_STATUS: u32 = 1 << 2;

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
    // valid for the call and beyond it. Slot 1 (the Control Point) is written
    // by NimBLE and never read back — see the note on H_MACHINE_STATUS.
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
    HANDLE_STORE.store(handles.as_mut_ptr() as usize, Ordering::Relaxed);
    logi!("ble: FTMS service registered");
    Ok(())
}

/// Address of the four-handle array, so it can live in an atomic.
///
/// `AtomicUsize` and not `AtomicI32`: a heap address on this part happens to
/// sit below `0x8000_0000` and would survive the round trip through a signed
/// integer, but "happens to" is not a property to depend on — an address with
/// the top bit set would read back negative and any future comparison against
/// it would be wrong.
///
/// Set once by [`register`], read by [`publish_handles`].
static HANDLE_STORE: AtomicUsize = AtomicUsize::new(0);

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
    let h = unsafe { *(p as *const [u16; 4]) };
    H_TREADMILL_DATA.store(h[0], Ordering::Relaxed);
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
            // Machine Status and Training Status answer with the DAEMON'S
            // values. They used to answer with nothing, justified as "what the
            // daemon's snapshot read does for a machine with nothing to say" —
            // which is not what the daemon does: `ftms_service.rs` has an
            // unconditional read handler for each, returning [0x02,0x01] and
            // [0x00,0x01]. Both characteristics have MANDATORY fixed leading
            // fields (Training Status is Flags + Status, minimum 2 octets), so
            // a zero-length read response is a malformed characteristic value.
            // Training Status is mandatory precisely BECAUSE the Control Point
            // is present, and a client that reads it during discovery to
            // decide whether the machine is controllable got 0 bytes.
            Some(proto::CHAR_MACHINE_STATUS) => {
                append(ctxt, &proto::encode_machine_status_stopped())
            }
            Some(proto::CHAR_TRAINING_STATUS) => {
                append(ctxt, &proto::encode_training_status_idle())
            }
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

/// A Control Point write that will not be acted on, and the answer it gets.
pub(crate) struct CpRefusal {
    /// The opcode to echo in the response indication.
    pub echo_opcode: u8,
    pub result: u8,
    pub why: &'static str,
}

/// Parse a Control Point write, or decide how to refuse it.
///
/// SPLIT OUT OF [`on_control_point`] ON PURPOSE, and the split is the seam the
/// QEMU shim uses. Everything from here down — `plan`, `effect_of`,
/// `motion_for`, [`apply`], the lease, the clamps and the FTMS result mapping
/// — contains NO FFI and NO radio. Only `access_cb`'s mbuf copy above it does.
/// So `QT ble_cp <hex>` can drive the ENTIRE belt edge of the BLE tier under
/// the existing QEMU harness on a machine with no Bluetooth at all, which is
/// how the Stop-during-a-program defect and the carried-axis window below
/// stopped being review-only claims.
///
/// A ZERO-LENGTH WRITE IS NOT OPCODE 0x00. `parse_control_point(&[])` returns
/// `None` because there is no first byte, and the old recovery
/// (`bytes.first().copied().unwrap_or(0)`) then answered
/// `[0x80, 0x00, 0x02]` — "RequestControl not supported" — naming an opcode
/// the peer never sent, for the one opcode this device ALWAYS accepts. A
/// client debugging its handshake was told the exact opposite of the truth.
/// An empty write is a malformed request, so it gets INVALID_PARAM, and the
/// echoed opcode stays 0 only because the response frame has nowhere else to
/// put "there wasn't one".
pub(crate) fn plan(bytes: &[u8]) -> Result<proto::ControlCommand, CpRefusal> {
    let Some(&opcode) = bytes.first() else {
        return Err(CpRefusal {
            echo_opcode: 0,
            result: proto::RESULT_INVALID_PARAM,
            why: "zero-length write, no opcode",
        });
    };
    match proto::parse_control_point(bytes) {
        Some(cmd) => Ok(cmd),
        // Unknown opcode, or a parameter shorter than the opcode requires.
        // The daemon answers with the byte it saw and NOT_SUPPORTED; do the
        // same, so a client's error message names the opcode it sent.
        None => Err(CpRefusal {
            echo_opcode: opcode,
            result: proto::RESULT_NOT_SUPPORTED,
            why: "unknown opcode or short parameter",
        }),
    }
}

/// Smallest free stack ever seen on the NimBLE host task, in bytes.
/// `u32::MAX` until the first sample.
static HOST_STACK_LOW: AtomicU32 = AtomicU32::new(u32::MAX);

/// The measured free stack on the NimBLE host task, or `None` before the first
/// Control Point write.
pub fn host_stack_low_water() -> Option<u32> {
    match HOST_STACK_LOW.load(Ordering::Relaxed) {
        u32::MAX => None,
        n => Some(n),
    }
}

/// Replace `CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE`'s arithmetic with a
/// measurement, taken where the deepest untrusted call chain has just unwound.
///
/// LOGS ONLY ON A NEW MINIMUM, which is what makes this safe to call on a path
/// a radio peer drives. The watermark is monotonically non-increasing, so the
/// number of lines this can ever emit is bounded by the number of distinct
/// minima — a handful in practice, and it cannot be pumped by repeating a
/// write. See the note on log volume in [`apply`].
#[inline(never)]
fn sample_host_stack() {
    // SAFETY: `uxTaskGetStackHighWaterMark(NULL)` reads the calling task's own
    // TCB and returns a byte count. No pointer is dereferenced by us and
    // nothing is retained.
    let free = unsafe { sys::uxTaskGetStackHighWaterMark(core::ptr::null_mut()) } as u32;
    // LOAD/COMPARE/STORE RATHER THAN `fetch_min`, AND THAT IS NOT A STYLE
    // CHOICE. `AtomicU32::fetch_min` here made the xtensa backend emit
    // `error: Undefined temporary symbol` at assembly time — no source
    // location, no symbol name — and reverting just this expression is what
    // turned the image green again (measured, twice, both directions). It
    // lowers to a compare-exchange loop and something in that expansion
    // confuses the assembler in this toolchain.
    //
    // The plain form is also the correct one on the merits: the NimBLE host
    // task is the ONLY writer, since this runs from its own callbacks, so
    // there is no contention for an RMW to win.
    if free < HOST_STACK_LOW.load(Ordering::Relaxed) {
        HOST_STACK_LOW.store(free, Ordering::Relaxed);
        logi!("ble: host task stack low-water {} bytes", free);
    }
}

/// Handle one Control Point write, end to end: parse, act, answer.
///
/// SAFETY: runs on NimBLE's host task; the only raw operations are the
/// notify/indicate sends, each of which is its own block below.
unsafe fn on_control_point(conn_handle: u16, cp_handle: u16, bytes: &[u8]) {
    let cmd = match plan(bytes) {
        Ok(cmd) => cmd,
        Err(refusal) => {
            logw!(
                "ble: control point opcode {} rejected ({})",
                refusal.echo_opcode,
                refusal.why
            );
            indicate(
                conn_handle,
                cp_handle,
                &proto::encode_control_response(refusal.echo_opcode, refusal.result),
            );
            return;
        }
    };

    let effect = proto::effect_of(cmd);
    let result = apply(effect);
    sample_host_stack();
    let completion = proto::complete_control_point(cmd, result);

    // Preserve the daemon's successful wire order: Fitness Machine Status,
    // Training Status, then the Control Point indication. Unlike the daemon,
    // emit the request echo only after this device's safety controller
    // accepted it; a refusal must not announce motion that never happened.
    if let Some(note) = completion.machine_status {
        notify(
            conn_handle,
            H_MACHINE_STATUS.load(Ordering::Relaxed),
            SUB_MACHINE_STATUS,
            note.as_slice(),
        );
    }
    if let Some(ts) = completion.training_status {
        notify(
            conn_handle,
            H_TRAINING_STATUS.load(Ordering::Relaxed),
            SUB_TRAINING_STATUS,
            &ts,
        );
    }

    indicate(conn_handle, cp_handle, &completion.response);
}

/// Turn a Control Point effect into belt motion, through THE ONE PATH.
///
/// Returns the FTMS result code for the indication. No clamping, no
/// pre-validation: `control::command` decides, and this function reports what
/// it decided.
///
/// # Why the BLE surface is `Surface::Http`, and why that is not a shortcut
///
/// `Surface` selects an owner slot in `Guarded` and a `Transport`/handle pair
/// for the lease. A THIRD surface would be a third lease holder, and
/// `SafetyController::connect` emergency-stops when a new generation
/// supersedes a lease-holding one — so a phone on Bluetooth and the same phone
/// on HTTP would take the belt from each other, relay-cycling the treadmill
/// mid-stride every time the user touched the other control.
///
/// They are the same person commanding the same machine from the same room,
/// and treating them as one owner is the honest model. It also leaves the
/// existing arbitration untouched: a running program still refuses BOTH, and
/// `Reject::NotOwner` still means what it meant.
#[inline(never)]
pub(crate) fn apply(effect: proto::CpEffect) -> u8 {
    // STOP IS NOT A MOTION REQUEST, AND IT CANNOT BE DENIED.
    //
    // Every effect used to go straight to `control::command(Surface::Http,..)`,
    // Stop included. While the interval executor owned the lease that returned
    // `Reject::NotOwner`, so a user running a 30-minute program at 6 mph who
    // pressed stop in Zwift got RESULT_FAILED and a belt that kept running —
    // and the only working stop, `POST /api/program/stop`, is not something a
    // BLE-only peer can call. On the Pi there is no per-surface lease at all:
    // `treadmill::send_stop` wrote straight to `treadmill_io` and the zero
    // always landed. The port introduced the denial, and commit a055117
    // already settled the principle for the analogous HTTP defect: the Stop
    // button cannot be denied.
    //
    // The fix is NOT a second path to the belt. Stop takes the same route the
    // app's stop button takes — end the program, which hands the lease back
    // through `control::release`, and then command zero as this surface. Both
    // steps are `control::command`; the one path is preserved and the stop
    // becomes unconditional.
    if let proto::CpEffect::Stop { .. } = effect {
        return stop_the_belt();
    }

    // ONE LOCK HOLD FOR READ-AND-COMMAND.
    //
    // `motion_for` carries the OTHER axis through unchanged, so a
    // SetTargetSpeed commands whatever incline was current when it was read.
    // Reading under one lock hold and commanding under a second left a window:
    // the httpd task shares `Surface::Http` with this one BY DESIGN (see the
    // header above), so the lease does not serialise them — only the mutex
    // does. A user raising the incline from the app in between the two
    // acquisitions had it silently reverted by the next BLE speed write.
    // `net::api`'s motion handler already does the read and the command inside
    // a single hold; this one now matches it.
    let outcome = {
        let mut g = crate::context::lock(&crate::CTX.guarded);
        let now_belt = proto::BeltNow {
            speed: g.controller.speed_tenths(),
            incline: g.controller.incline_half_percent(),
            resume_speed: SpeedTenths::new(RESUME_TENTHS.load(Ordering::Relaxed)),
        };
        let Some((speed, incline)) = proto::motion_for(effect, now_belt) else {
            // AckOnly — RequestControl. Nothing is commanded, and that is NOT
            // the same as re-commanding the current motion, which would take
            // the lease away from a running program.
            return proto::RESULT_SUCCESS;
        };
        let now = crate::CTX.clock.now();
        let intent = match effect {
            proto::CpEffect::SetSpeed(s) if s.get() > 0 => {
                control::EntryIntent::ExplicitRecovery
            }
            proto::CpEffect::Start => control::EntryIntent::ExplicitRecovery,
            _ => control::EntryIntent::Ordinary,
        };
        (
            control::command(&mut g, Surface::Http, intent, speed, incline, now),
            speed,
            incline,
        )
    };
    let (outcome, speed, incline) = outcome;

    let loud = cp_log_due();
    match outcome {
        Ok(()) => {
            if speed.get() > 0 {
                RESUME_TENTHS.store(speed.get(), Ordering::Relaxed);
            }
            if loud {
                logi!(
                    "ble: control point accepted (speed {} tenths, incline {} half-pct)",
                    speed.get(),
                    incline.get()
                );
            }
            proto::RESULT_SUCCESS
        }
        Err(control::Reject::NotOwner) => {
            if loud {
                logw!("ble: control point refused — a program owns the belt");
            }
            proto::result_for_reject(proto::CpReject::NotOwner)
        }
        Err(control::Reject::ExecutorInhibited) => {
            if loud {
                logw!("ble: control point refused — program executor is safety-paused");
            }
            proto::result_for_reject(proto::CpReject::Other)
        }
        Err(control::Reject::Refused) => {
            if loud {
                logw!("ble: control point refused by the safety controller");
            }
            proto::result_for_reject(proto::CpReject::Refused)
        }
        Err(control::Reject::GenerationExhausted) => {
            proto::result_for_reject(proto::CpReject::Other)
        }
    }
}

/// Should this Control Point write emit its log line?
///
/// AN UNAUTHENTICATED PEER DRIVES THIS PATH. Each write emitted ~100 bytes of
/// BLOCKING UART0 logging on the NimBLE host task; at ATT write rate (one per
/// connection interval, minimum 7.5 ms) that saturates a 115200-baud console
/// from the car park. It cannot reach the belt and it cannot reboot the device
/// — the serial engine, emulate cycle and executor all run at higher priority
/// and this task is deliberately not WDT-supervised — but it starves the radio
/// and it drowns the console when something else needs reading.
///
/// First write is loud, then one in [`CP_LOG_EVERY`]. The counter resets on
/// disconnect, so an ordinary session (a human pressing buttons) is unchanged
/// and only a flood is thinned.
fn cp_log_due() -> bool {
    CP_WRITES.fetch_add(1, Ordering::Relaxed) % CP_LOG_EVERY == 0
}

static CP_WRITES: AtomicU32 = AtomicU32::new(0);
const CP_LOG_EVERY: u32 = 64;

/// `CpEffect::Stop` — the one Control Point opcode whose failure mode is
/// safety-shaped, and the only stop control a BLE-only peer has.
///
/// Exactly what `POST /api/program/stop` does, through the same functions:
/// `ProgramState::stop` under the program lock, then its plan driven by
/// `apply_plan` with `release_belt = true`.
///
/// # THE ZERO COMES FROM THE PLAN, NOT FROM A SECOND COMMAND
///
/// `ProgramState::stop` returns `Plan::zero()` when a program was running, and
/// `apply_plan` issues it through `control::command` as `Surface::Executor` —
/// under the lease the executor ALREADY HOLDS. That is what actually stops the
/// belt, and it cannot be denied because it is the owner commanding.
///
/// A first version of this function drove the plan and then commanded zero
/// again as `Surface::Http`, on the theory that the release had handed the
/// lease back. IT HAD NOT, and the QEMU scenario caught it: `control::release`
/// on an emulating controller calls `request_normal_exit`, which begins a
/// gap-safe exit and holds the lease until that exit COMPLETES. The follow-up
/// command therefore arrived while the executor still owned the belt and was
/// answered `NotOwner` — the very defect this function exists to fix, moved
/// three lines down. `net/program.rs`'s Quick Start comment had already
/// written the rule down ("if a release is ever wanted it must COMPLETE before
/// the replacement plan is driven"); this is the same trap.
///
/// So the second command is issued ONLY when there was no program to stop, in
/// which case a moving belt is manually commanded on this surface and there is
/// no exit in flight to wait for.
///
/// LOCK ORDER IS `program` THEN `guarded`, mandatory, see `context.rs`. The
/// program lock is dropped before the fallback command is issued.
///
/// `apply_plan` is called directly rather than through `net::program::drive`
/// because `drive` IS that call plus the lock (and plus a `net` feature that
/// the BLE tier does not require). Same function, same arguments, same order.
#[inline(never)]
fn stop_the_belt() -> u8 {
    let program_zeroed_the_belt = {
        let mut p = crate::context::lock(&crate::CTX.program);
        let plan = p.stop();
        let had_plan = !plan.is_empty();
        let now = crate::CTX.clock.now();
        let mut g = crate::context::lock(&crate::CTX.guarded);
        // `release_belt = true` even for an empty plan: it is the half that
        // hands the lease back, and it is a no-op when this surface never had
        // it.
        let accepted =
            crate::tasks::interval_executor::apply_plan(&mut g, plan, true, now).unwrap_or(0);
        had_plan && accepted > 0
    };

    if program_zeroed_the_belt {
        logi!("ble: control point stop — program ended, belt zeroed");
        return proto::RESULT_SUCCESS;
    }

    let outcome = {
        let mut g = crate::context::lock(&crate::CTX.guarded);
        let now = crate::CTX.clock.now();
        control::command(
            &mut g,
            Surface::Http,
            control::EntryIntent::Ordinary,
            SpeedTenths::ZERO,
            safety_core::units::InclineHalfPct::ZERO,
            now,
        )
    };

    match outcome {
        Ok(()) => {
            logi!("ble: control point stop — belt zeroed");
            proto::RESULT_SUCCESS
        }
        Err(control::Reject::Refused) => {
            // A latched fault. The belt is not moving in that state, so the
            // peer's intent is satisfied even though the command was refused.
            logw!("ble: control point stop refused by the safety controller");
            proto::result_for_reject(proto::CpReject::Refused)
        }
        Err(_) => {
            logw!("ble: control point stop could not take the lease");
            proto::result_for_reject(proto::CpReject::Other)
        }
    }
}

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
    // SATURATE, never truncate. The daemon does
    // `session_start.elapsed().as_secs().min(u16::MAX as u64) as u16` and this
    // device's own workout path does `.min(u16::MAX as u32)`; a bare `as u16`
    // here made the idle alive-signal's elapsed field wrap from 65535 to 0
    // after 18h12m of connected idle, so a client deriving a duration or a
    // delta from it saw time run backwards. The rule everywhere else in this
    // tree is that a huge value must never become a small one.
    let alive = CONN_SECS.load(Ordering::Relaxed).min(u16::MAX as u32) as u16;
    proto::encode_treadmill_data_with_alive(&snap, alive)
}

/// Send a notification, but ONLY to a peer that asked for this characteristic.
///
/// A no-op when the handle has not been published yet (so a mis-ordered
/// bring-up drops a frame instead of notifying attribute 0), and a no-op when
/// the client's CCCD for `sub_bit` is clear — see [`SUBSCRIBED`].
fn notify(conn_handle: u16, attr_handle: u16, sub_bit: u32, data: &[u8]) {
    if attr_handle == 0 || data.is_empty() {
        return;
    }
    if SUBSCRIBED.load(Ordering::Relaxed) & sub_bit == 0 {
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
///
/// DELIBERATELY NOT GATED ON THE CCCD, unlike [`notify`]. This is a SOLICITED
/// answer to a write the peer made a moment ago on the same characteristic —
/// the peer has demonstrated it wants it, which is exactly what the CCCD check
/// exists to establish for the periodic notifications. Suppressing it would
/// leave a client that writes before it subscribes waiting forever for a
/// response instead of being told what happened to its request.
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
    notify(
        c,
        H_TREADMILL_DATA.load(Ordering::Relaxed),
        SUB_TREADMILL_DATA,
        &data,
    );
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
                // A fresh peer has configured nothing yet. CCCDs are not
                // persisted (no bonding — see the module header), so every
                // connection starts from silence.
                SUBSCRIBED.store(0, Ordering::Relaxed);
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
            SUBSCRIBED.store(0, Ordering::Relaxed);
            CP_WRITES.store(0, Ordering::Relaxed);
            // A treadmill that stops advertising after its first app closes is
            // a treadmill nobody can find again without a power cycle.
            start_advertising();
        }
        sys::BLE_GAP_EVENT_ADV_COMPLETE => start_advertising(),
        sys::BLE_GAP_EVENT_SUBSCRIBE => on_subscribe(&(*event).__bindgen_anon_1.subscribe),
        _ => {}
    }
    0
}

/// A client wrote a CCCD. Record it, and send the initial value the daemon
/// sends.
///
/// THE INITIAL VALUE IS THE POINT, not just the bookkeeping. The daemon pushes
/// Machine Status `[0x02, 0x01]` and Training Status `[0x00, 0x01]` the instant
/// a client subscribes, each with the comment "so client knows machine state"
/// (`ftms_service.rs`). This device sent neither, and — before the read
/// handlers above were fixed — also answered a READ of those characteristics
/// with zero bytes. A freshly connected app therefore had NO WAY AT ALL to
/// learn machine or training state until it wrote the Control Point.
///
/// `cur_notify` / `cur_indicate` are bitfield ACCESSORS in the generated
/// bindings (`ble_gap_event__bindgen_ty_1__bindgen_ty_13`), not fields; either
/// counts as "the client wants this characteristic".
///
/// SAFETY: `sub` is a live borrow of the event union member NimBLE is handing
/// us for this call, selected by the event type. Nothing is retained.
#[inline(never)]
unsafe fn on_subscribe(sub: &sys::ble_gap_event__bindgen_ty_1__bindgen_ty_13) {
    let wants = sub.cur_notify() != 0 || sub.cur_indicate() != 0;
    let h = sub.attr_handle;

    let bit = if h == H_TREADMILL_DATA.load(Ordering::Relaxed) {
        SUB_TREADMILL_DATA
    } else if h == H_MACHINE_STATUS.load(Ordering::Relaxed) {
        SUB_MACHINE_STATUS
    } else if h == H_TRAINING_STATUS.load(Ordering::Relaxed) {
        SUB_TRAINING_STATUS
    } else {
        // The Control Point's own CCCD, or a characteristic that does not
        // notify. Nothing periodic is gated on it — see `on_control_point`.
        return;
    };

    if wants {
        SUBSCRIBED.fetch_or(bit, Ordering::Relaxed);
    } else {
        SUBSCRIBED.fetch_and(!bit, Ordering::Relaxed);
        return;
    }

    match bit {
        SUB_MACHINE_STATUS => notify(
            sub.conn_handle,
            h,
            bit,
            &proto::encode_machine_status_stopped(),
        ),
        SUB_TRAINING_STATUS => {
            notify(sub.conn_handle, h, bit, &proto::encode_training_status_idle())
        }
        // Treadmill Data needs no priming: `tick()` sends one within a second.
        _ => {}
    }
}
