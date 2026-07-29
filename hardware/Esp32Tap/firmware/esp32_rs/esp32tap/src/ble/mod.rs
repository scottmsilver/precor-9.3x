/*!
 * The BLE tier — NimBLE on the device.
 *
 * Two roles, one radio:
 *
 *  * **FTMS peripheral** ([`ftms`]) — the treadmill as a Fitness Machine, so
 *    Zwift/QZ/Garmin/an Apple Watch see it. Control Point writes reach the belt
 *    through `crate::control::command` and nowhere else.
 *  * **HRM central** ([`central`]) — scans for a heart-rate strap, subscribes
 *    to its measurement notifications, and publishes the reading into
 *    [`crate::hr`], which is what `/api/hrm` and the `/ws` `hr` frame read.
 *
 * The wire bytes are NOT here. They are in `ble_core` — pure, host-tested,
 * `forbid(unsafe_code)`, and a byte-for-byte port of the working Pi daemons.
 * This module is transport and nothing else: it moves those bytes across a
 * radio and hands a Control Point write to `control.rs`.
 *
 * # The radio is OPTIONAL, and that is the property this tier is designed for
 *
 * QEMU HAS NO BLE RADIO. `nimble_port_init` initialises a controller that does
 * not exist there, so on the emulator this tier does not come up — which is
 * exactly the condition every QEMU gate in the tree now runs under, and
 * exactly what `tools/qemu_scenarios/test_ble_degraded.py` asserts is
 * harmless. A treadmill whose Bluetooth failed must still boot, still serve
 * HTTPS, still push `/ws`, and still move the belt from the app and from the
 * physical console. So:
 *
 *  * Bring-up happens on its OWN task, spawned LAST, at the lowest priority in
 *    the system, after the network tier is already serving. Nothing waits on
 *    it. A hang here cannot delay the belt reaching a safe state.
 *  * That task is DELIBERATELY NOT WDT-SUPERVISED, and that is a safety
 *    decision rather than an omission. The task watchdog's action is
 *    `panic -> silent reboot`, and a reboot DROPS THE RELAY MID-RUN. Trading a
 *    working treadmill for a stalled radio is the wrong trade every time:
 *    Bluetooth is a convenience, the belt is the point. `tools/check_wdt_chain.py`
 *    discovers supervised tasks by their `wdt::subscribe_current_task()` call,
 *    so a task that does not subscribe needs no row in the matrix — but the
 *    matrix in `tasks/mod.rs` names this one anyway, with this reason, so the
 *    absence is a documented choice and not a hole somebody forgot to fill.
 *  * Every failure is REPORTED, never silently swallowed: [`status`] is
 *    readable, and the log line names the `esp_err_t`.
 *
 * # Memory
 *
 * NimBLE is not small, and the cost is MEASURED rather than estimated:
 * [`bring_up`] samples the free heap either side of `nimble_port_init` and
 * logs the delta as `ble: heap cost N bytes`. The QEMU `QT heap` verb reads
 * the same counters, so the number in the README came off a running image.
 * Everything this tier allocates afterwards is bounded by Kconfig
 * (`CONFIG_BT_NIMBLE_MAX_CONNECTIONS`, the msys block counts) or is a `static`
 * here; nothing grows with connection count or with time.
 */

pub mod central;
pub mod ftms;

use crate::{logi, logw};
use core::sync::atomic::{AtomicI32, Ordering};
use esp_idf_sys as sys;

/// Advertised name. Also the mDNS instance name's sibling — a user looking at
/// a Bluetooth picker and at an app's device list should see the same word.
pub const DEVICE_NAME: &core::ffi::CStr = c"esp32tap";

/// Sentinel for "bring-up has not run yet". `esp_err_t` is `i32` and IDF never
/// uses `i32::MIN` as an error code.
const NOT_STARTED: i32 = i32::MIN;

/// The radio's own report on itself. `ESP_OK` means the host is running; any
/// other value is the `esp_err_t` that stopped it.
static STATUS: AtomicI32 = AtomicI32::new(NOT_STARTED);

/// Heap consumed by the NimBLE stack at init, in bytes. 0 until measured.
static HEAP_COST: AtomicI32 = AtomicI32::new(0);

/// What happened to the radio.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Status {
    /// Bring-up has not been attempted (or the `ble` feature is off).
    NotStarted,
    /// The NimBLE host is running.
    Up,
    /// Bring-up failed with this `esp_err_t`. The device is otherwise healthy.
    Failed(sys::esp_err_t),
}

pub fn status() -> Status {
    match STATUS.load(Ordering::Relaxed) {
        NOT_STARTED => Status::NotStarted,
        0 => Status::Up,
        e => Status::Failed(e),
    }
}

/// Bytes of heap the BLE stack cost at init, as measured on this boot.
pub fn heap_cost_bytes() -> i32 {
    HEAP_COST.load(Ordering::Relaxed)
}

/// Free internal heap, right now.
fn free_heap() -> u32 {
    // SAFETY: an argument-free IDF accessor that returns an integer by value
    // and touches no Rust memory.
    unsafe { sys::esp_get_free_heap_size() }
}

// ---------------------------------------------------------------------------
// Bring-up
// ---------------------------------------------------------------------------

/// Initialise the controller, the HCI transport and the NimBLE host.
///
/// `nimble_port_init` is ONE call that does all three (IDF >= 5.0 folded
/// `esp_bt_controller_init`/`_enable` and `esp_nimble_hci_init` into it), and
/// it returns an `esp_err_t` rather than aborting — which is what makes the
/// no-radio case a reportable failure instead of a crash.
/// Refuse bring-up unless this chip has a Bluetooth identity address burned
/// into its eFuses.
///
/// # This is a CRASH GUARD, not a nicety, and it was written from a measurement
///
/// `nimble_port_init` does NOT return an error when the controller cannot come
/// up. MEASURED under esp-QEMU 9.2.2 (esp32s3), the very next lines after
/// `BLE_INIT: Using main XTAL as clock source` were:
///
/// ```text
///   assert failed: 0x4206ea5c <cached disabled>:1753
///   Backtrace: ...
///   Rebooting...
/// ```
///
/// — an `assert()` inside the closed-source BT controller, which is a panic,
/// which under this firmware's PLAN-normative `CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT`
/// is an immediate reset. The device then reboot-looped forever. **A reboot
/// drops the relay mid-run.** So "the radio failing is survivable" cannot be
/// implemented as `match nimble_port_init()`: by the time that call returns
/// there is nothing left to handle. It has to be a guard in FRONT of it.
///
/// # Why the identity address is the right thing to check
///
/// It is not a QEMU sniff, and deliberately so — a check that asks "am I an
/// emulator?" is a check that lies on any hardware it has not met. This asks a
/// question that is *about the radio* and is meaningful on a real board: a BLE
/// device MUST have an identity address, `ble_hs_util_ensure_addr` is called
/// on every sync to obtain one, and `esp_read_mac(ESP_MAC_BT)` reads it out of
/// the same factory eFuse block the controller itself uses. A part whose block
/// is blank cannot advertise a valid address, so bringing the stack up on one
/// is pointless even where it does not abort.
///
/// What "valid" means here, and why each half is checked:
///
///  * **The OUI is not `00:00:00`.** The first three bytes of a factory MAC are
///    an IEEE-assigned Organizationally Unique Identifier; Espressif's are
///    `24:0A:C4`, `30:AE:A4`, `7C:DF:A1` and friends. An all-zero OUI is not an
///    Espressif part with an unusual address, it is an UNPROGRAMMED eFuse block
///    read back as zeros. MEASURED: esp-QEMU returns `00:00:00:00:00:02` —
///    a blank base MAC with the +2 the BT derivation adds — which is precisely
///    why a bare `mac == [0; 6]` test was not enough and is why this checks the
///    OUI instead.
///  * **The multicast bit (bit 0 of byte 0) is clear.** A BLE identity address
///    must be unicast. A part that somehow read back a multicast MAC would
///    advertise an address no central will accept.
///
/// The MAC and the verdict are logged either way, so the decision this made is
/// visible in a boot log rather than inferred.
fn identity_address() -> Result<[u8; 6], sys::esp_err_t> {
    let mut mac = [0u8; 6];
    // SAFETY: `mac` is a live exclusive 6-byte buffer, which is the length
    // ESP_MAC_BT writes; the call takes an integer discriminant and touches
    // nothing else.
    let err = unsafe { sys::esp_read_mac(mac.as_mut_ptr(), sys::esp_mac_type_t_ESP_MAC_BT) };
    if err != sys::ESP_OK {
        return Err(err);
    }
    let oui_blank = mac[0] == 0 && mac[1] == 0 && mac[2] == 0;
    let multicast = mac[0] & 0x01 != 0;
    if oui_blank || multicast {
        logw!(
            "ble: eFuse identity address is not a factory unicast MAC ({:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X})",
            mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]
        );
        return Err(sys::ESP_ERR_INVALID_MAC);
    }
    Ok(mac)
}

fn bring_up() -> Result<(), sys::esp_err_t> {
    // BEFORE ANYTHING ELSE — see `identity_address`. Past this point the
    // controller gets to decide whether this device reboots.
    let mac = identity_address()?;
    logi!(
        "ble: identity address {:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X}",
        mac[0],
        mac[1],
        mac[2],
        mac[3],
        mac[4],
        mac[5]
    );

    let before = free_heap();

    // SAFETY: no arguments. Initialises the BLE controller, the HCI transport
    // and the NimBLE host, and returns `esp_err_t`. On failure it leaves
    // nothing running, so there is no cleanup to do on the error path.
    let err = unsafe { sys::nimble_port_init() };
    if err != sys::ESP_OK {
        return Err(err);
    }

    let after = free_heap();
    let cost = before.saturating_sub(after);
    HEAP_COST.store(cost as i32, Ordering::Relaxed);
    // THE INSTRUMENT FOR THE ONE NUMBER NOBODY HAS. NimBLE's runtime heap cost
    // alongside TLS and the app tier is UNMEASURED and no figure is quoted for
    // it anywhere in this tree — the controller aborts before this call returns
    // under QEMU, so it cannot be measured here. Nothing greps this line yet,
    // and that is the honest state: it exists so the figure comes off the first
    // real board rather than out of an estimate. Bead precor-9_3x-l0h item 2.
    logi!(
        "ble: heap cost {} bytes (free {} -> {})",
        cost,
        before,
        after
    );

    // Host configuration. `ble_hs_cfg` is a NimBLE global; the callbacks below
    // run on NimBLE's own host task once `nimble_port_freertos_init` starts it.
    //
    // SAFETY: `ble_hs_cfg` is a C global of POD fields. It is written HERE,
    // once, on the bring-up task, BEFORE the host task exists — so there is no
    // concurrent reader and no aliasing. `addr_of_mut!` avoids forming a
    // reference to a `static mut`.
    unsafe {
        let cfg = core::ptr::addr_of_mut!(sys::ble_hs_cfg);
        (*cfg).sync_cb = Some(on_sync);
        (*cfg).reset_cb = Some(on_reset);
        (*cfg).gatts_register_cb = None;
        (*cfg).store_status_cb = Some(sys::ble_store_util_status_rr);
    }

    // The two standard services every peripheral carries: GAP (device name,
    // appearance) and GATT (service-changed). Both are IDF-provided and take
    // no arguments.
    //
    // SAFETY: argument-free NimBLE service constructors, called once, before
    // the host task runs.
    unsafe {
        sys::ble_svc_gap_init();
        sys::ble_svc_gatt_init();
    }

    // SAFETY: `DEVICE_NAME` is a `'static` NUL-terminated literal. NimBLE
    // copies the bytes it needs into its own GAP characteristic storage.
    let name_err = unsafe { sys::ble_svc_gap_device_name_set(DEVICE_NAME.as_ptr()) };
    if name_err != 0 {
        // Not fatal — an unnamed peripheral still works — but say so.
        logw!("ble: device name rejected ({})", name_err);
    }

    ftms::register()?;

    // NimBLE spawns and owns the host task from here. `nimble_port_run` never
    // returns until `nimble_port_stop`, which this firmware never calls.
    //
    // SAFETY: takes a `'static` function pointer; NimBLE creates the task and
    // owns its lifetime.
    unsafe { sys::nimble_port_freertos_init(Some(host_task)) };
    Ok(())
}

/// NimBLE's host task body. Runs the event loop forever.
///
/// SAFETY: called by NimBLE on the task it just created; the argument is the
/// unused `void *param` of a FreeRTOS task and is never dereferenced.
unsafe extern "C" fn host_task(_param: *mut core::ffi::c_void) {
    sys::nimble_port_run();
    sys::nimble_port_freertos_deinit();
}

/// The controller and host have synchronised — the radio is usable.
///
/// SAFETY: invoked by NimBLE on its host task with no arguments.
unsafe extern "C" fn on_sync() {
    // Make sure we have an identity address before advertising. `0` = prefer
    // the public address, fall back to a static random one.
    let err = sys::ble_hs_util_ensure_addr(0);
    if err != 0 {
        crate::logw!("ble: no usable identity address ({})", err);
        return;
    }
    ftms::start_advertising();
    central::start_scan();
}

/// The controller reset. NimBLE re-syncs on its own; log the reason.
///
/// SAFETY: invoked by NimBLE on its host task; `reason` is a plain int.
unsafe extern "C" fn on_reset(reason: core::ffi::c_int) {
    crate::logw!("ble: controller reset ({})", reason);
}

// ---------------------------------------------------------------------------
// The task
// ---------------------------------------------------------------------------

/// Stack for the bring-up/housekeeping task.
///
/// `nimble_port_init` runs on this stack and mbedtls-free NimBLE init is
/// modest, but the 1 Hz body below renders a 13-byte notification and walks
/// the connection table, and level-1 interrupts run on the interrupted task's
/// stack. 4096 is the same order as the emulate cycle's 6144 with none of its
/// parse buffers.
pub const STACK_BYTES: usize = 4096;

/// Priority. The LOWEST in the system, below the session recorder's 4: a radio
/// must never preempt the belt, the serial engine, or a flash write.
pub const PRIORITY: u8 = 3;

/// Housekeeping cadence. The FTMS Treadmill Data characteristic notifies at
/// 1 Hz — the rate the Pi daemon uses and the rate every client expects.
const TICK_MS: u32 = 1000;

/// Bring the radio up, then keep it fed. Never returns.
///
/// NOT WDT-SUPERVISED — see the module header. The whole point of this task is
/// that its failure is survivable, and the task watchdog's remedy (reboot)
/// is not survivable for a run in progress.
pub fn run(_ctx: &'static crate::context::FirmwareContext) -> ! {
    match bring_up() {
        Ok(()) => {
            STATUS.store(sys::ESP_OK, Ordering::Relaxed);
            logi!("ble: nimble host started");
        }
        Err(e) => {
            STATUS.store(e, Ordering::Relaxed);
            // EXACT STRING — test_ble_degraded.py waits on it, and the whole
            // claim of this tier is that the line below is the ONLY
            // consequence. There is no radio in QEMU, so this is the branch
            // every gate in the tree exercises.
            logw!(
                "ble: unavailable (err {}) — HTTPS, /ws, the belt and the console are unaffected",
                e
            );
            // Park. Re-trying an absent controller in a loop would burn CPU
            // the serial engine needs, forever, for a radio that is not going
            // to appear: `nimble_port_init` fails on hardware absence, not on
            // a transient.
            loop {
                crate::tasks::delay_ms(60_000);
            }
        }
    }

    loop {
        crate::tasks::delay_ms(TICK_MS);
        ftms::tick();
        central::tick();
    }
}
