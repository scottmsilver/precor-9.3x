/*!
 * Heart rate, as the app sees it.
 *
 * ONE fixed-size record behind one lock. The BLE central
 * (`crate::ble::central`) is the only writer; `/api/hrm*`, the `/ws` `hr`
 * frame and `/api/status` are the readers.
 *
 * # Why this is compiled in EVERY build, radio or no radio
 *
 * The Android app calls `GET /api/hrm` whether or not this device has a strap,
 * and the Pi answers it whether or not `hrm-daemon` is running — "graceful
 * degradation: if hrm-daemon isn't running, server.py continues without HR"
 * (CLAUDE.md). A missing route is not the same as "no heart rate": to a client
 * it looks like much older firmware. So the state lives here, outside the `ble`
 * feature, and reads as disconnected when nothing is filling it.
 *
 * # Memory
 *
 * Every field is a fixed-size array and the scan list is [`MAX_DEVICES`]
 * entries. A device that would be the seventh is DROPPED, not appended:
 * anyone with a laptop can advertise a thousand names, and a list that grew
 * with what it heard would be an unbounded allocation driven by a stranger.
 *
 * # The peer's bytes are sanitised BEFORE they get here
 *
 * `FixedName` and `Addr` are `ble_core::peer`'s — pure, host-tested, and the
 * place a strap's advertised name is made safe to put inside a JSON string.
 * They are not re-implemented here, because the copy in the firmware would be
 * reachable only through a radio QEMU does not have.
 */

// COMPILER-ENFORCED: this module holds a peer's bytes and renders them into
// JSON. There is no reason for it to contain `unsafe`, and `forbid` (unlike
// the crate root's `deny`) cannot be lifted by an inner `allow`.
#![forbid(unsafe_code)]

use crate::context::lock;
use ble_core::hrm::HrReading;
use std::sync::Mutex;

pub use ble_core::peer::{Addr, FixedName, ADDR_TEXT_LEN};

/// Scan results retained. Six is more straps than a room has; the seventh is
/// dropped rather than allowed to grow the record.
pub const MAX_DEVICES: usize = 6;

/// One entry of the scan list.
#[derive(Clone, Copy)]
pub struct Device {
    pub addr: Addr,
    pub name: FixedName,
    pub rssi: i8,
}

impl Device {
    pub const EMPTY: Device = Device {
        addr: Addr::NONE,
        name: FixedName::EMPTY,
        rssi: 0,
    };
}

/// Everything the app can learn about heart rate on this device.
pub struct State {
    pub reading: HrReading,
    pub device_name: FixedName,
    pub device_addr: Addr,
    pub scanning: bool,
    /// Bounded scan list; `found` entries of `devices` are live.
    pub devices: [Device; MAX_DEVICES],
    pub found: usize,
    /// The strap the user picked, remembered across disconnects so the central
    /// re-connects without asking again. Cleared by `forget`.
    ///
    /// IN RAM ONLY, deliberately. The Pi persists it to `hrm_config.json`;
    /// doing the same here means an NVS write on every pairing, and the
    /// persistence tier's own rule is that a write must be worth a flash
    /// erase. Re-picking a strap after a power cycle is one tap. Stated so it
    /// is a known difference from the Pi rather than a surprise.
    pub saved: Addr,
}

impl State {
    const fn new() -> State {
        State {
            reading: HrReading::NONE,
            device_name: FixedName::EMPTY,
            device_addr: Addr::NONE,
            scanning: false,
            devices: [Device::EMPTY; MAX_DEVICES],
            found: 0,
            saved: Addr::NONE,
        }
    }
}

static STATE: Mutex<State> = Mutex::new(State::new());

/// Run `f` against the shared state. The ONLY door.
pub fn with<R>(f: impl FnOnce(&mut State) -> R) -> R {
    f(&mut lock(&STATE))
}

// ---------------------------------------------------------------------------
// Writer side — the BLE central
// ---------------------------------------------------------------------------

/// A measurement notification arrived. `payload` is the raw ATT value.
///
/// Parsing is `ble_core`'s, byte for byte the Pi daemon's, so a strap that
/// worked against the Pi reads the same here.
pub fn on_measurement(payload: &[u8]) {
    with(|s| s.reading = s.reading.updated(payload));
}

/// The link dropped. Zeroes the bpm — the sentinel every UI in this project
/// already treats as "no reading" (`heartRate > 0` gates all three Kotlin call
/// sites) — and forgets the connected device, exactly as the Pi daemon's
/// `mark_disconnected` does. `saved` is deliberately kept: a strap that walks
/// out of range should be re-connected to, not re-chosen.
pub fn on_disconnected() {
    with(|s| {
        s.reading = s.reading.disconnected();
        s.device_name = FixedName::EMPTY;
        s.device_addr = Addr::NONE;
    });
}

/// A link came up.
pub fn on_connected(addr: Addr, name: &[u8]) {
    with(|s| {
        s.reading = HrReading {
            bpm: ble_core::hrm::BPM_NONE,
            connected: true,
        };
        s.device_addr = addr;
        s.device_name.set(name);
        s.saved = addr;
    });
}

/// Record a scan hit. Existing entries are UPDATED, never duplicated, and the
/// list never grows past [`MAX_DEVICES`].
pub fn on_scan_result(addr: Addr, name: &[u8], rssi: i8) {
    with(|s| {
        for i in 0..s.found {
            if s.devices[i].addr == addr {
                s.devices[i].rssi = rssi;
                if !name.is_empty() {
                    s.devices[i].name.set(name);
                }
                return;
            }
        }
        if s.found == MAX_DEVICES {
            return; // full: drop the newcomer rather than grow
        }
        let mut d = Device::EMPTY;
        d.addr = addr;
        d.rssi = rssi;
        d.name.set(name);
        s.devices[s.found] = d;
        s.found += 1;
    });
}

/// A scan started (clearing the previous results) or finished.
pub fn set_scanning(on: bool) {
    with(|s| {
        if on {
            s.found = 0;
        }
        s.scanning = on;
    });
}

// ---------------------------------------------------------------------------
// Command mailbox — the app asks, the central acts
// ---------------------------------------------------------------------------

/// What the app has asked the central to do.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Command {
    Connect(Addr),
    Forget,
    Scan,
}

/// ONE slot. A newer request REPLACES an older one rather than queueing behind
/// it, so the memory is constant no matter how many times a button is tapped —
/// and "latest wins" is the honest reading of a user's intent anyway.
static PENDING: Mutex<Option<Command>> = Mutex::new(None);

pub fn post(cmd: Command) {
    *lock(&PENDING) = Some(cmd);
}

pub fn take() -> Option<Command> {
    lock(&PENDING).take()
}

// ---------------------------------------------------------------------------
// Rendering — one place, so `/api/hrm`, `/ws` and `/api/status` cannot diverge
// ---------------------------------------------------------------------------

/// A snapshot copied out under the lock, so rendering never holds it.
#[derive(Clone, Copy)]
pub struct Snapshot {
    pub bpm: u16,
    pub connected: bool,
    pub name: FixedName,
    addr_text: [u8; ADDR_TEXT_LEN],
    addr_len: usize,
    pub scanning: bool,
    pub devices: [Device; MAX_DEVICES],
    pub found: usize,
}

pub fn snapshot() -> Snapshot {
    with(|s| {
        let mut addr_text = [0u8; ADDR_TEXT_LEN];
        let addr_len = s.device_addr.text(&mut addr_text);
        Snapshot {
            bpm: s.reading.bpm,
            connected: s.reading.connected,
            name: s.device_name,
            addr_text,
            addr_len,
            scanning: s.scanning,
            devices: s.devices,
            found: s.found,
        }
    })
}

impl Snapshot {
    pub fn addr_str(&self) -> &str {
        core::str::from_utf8(&self.addr_text[..self.addr_len]).unwrap_or("")
    }
}
