//! Slice 3 — DNS-SD advertisement, so the Android app finds this device with
//! the SAME discovery code that finds the Pi.
//!
//! THE RECORD IS A CONTRACT, NOT A CHOICE. Every field below is copied from
//! `deploy/treadmill.avahi-service`, the file the Pi publishes through Avahi:
//!
//!     <type>_treadmill._tcp</type>
//!     <port>8000</port>
//!     <txt-record>scheme=https</txt-record>
//!     <txt-record>path=/</txt-record>
//!
//! Android's `NsdManager` browses for `_treadmill._tcp`, resolves the SRV for
//! host and port, and reads `scheme`/`path` from the TXT record. If any of
//! those four differ, the app needs a second code path — which is the outcome
//! this module exists to prevent. `tools/qemu_scenarios/test_mdns.py` parses
//! the Avahi XML and compares field by field, so the two cannot silently drift.
//!
//! `scheme=https` is only honest because `net::http` really does serve TLS.
//! Advertising it over a plaintext server would make every client's URL
//! construction wrong; the two land together for that reason.
//!
//! WHERE THE COMPONENT COMES FROM: mDNS is NOT in base ESP-IDF. It is a managed
//! component (`espressif/mdns`) pulled in by a `remote_component` entry in
//! esp32tap/Cargo.toml — see the long note there for why that, and not
//! `esp_idf_components`, is the mechanism. Once the component is built,
//! esp-idf-sys's stock bindings.h picks up `mdns.h` on its own via
//! `ESP_IDF_COMP_ESPRESSIF__MDNS_ENABLED`.
//!
//! THE INTERFACE IS REGISTERED EXPLICITLY, AND THAT IS LOAD-BEARING.
//! `CONFIG_MDNS_PREDEF_NETIF_*` defaults to y and looks like it does this for
//! free, but the predefined path only enables a PCB when it *observes* an
//! `ETHERNET_EVENT_CONNECTED` / `IP_EVENT_ETH_GOT_IP`. This firmware waits for
//! DHCP before starting mDNS at all — a record must not advertise an address
//! the device does not yet have — so those events are long gone by the time the
//! responder registers its handler. Symptom: `mdns_init` and `mdns_service_add`
//! both return ESP_OK, the log says the service is up, and the device answers
//! nothing, forever. The predefs are therefore OFF in sdkconfig.defaults and
//! this module drives `mdns_register_netif` + `mdns_netif_action` itself, which
//! is order-independent.

use esp_idf_sys as sys;

use super::{check, eth_netif, NetResult};

/// Service type and protocol, split the way `mdns_service_add` wants them.
const SERVICE_TYPE: &core::ffi::CStr = c"_treadmill";
const SERVICE_PROTO: &core::ffi::CStr = c"_tcp";

/// The mDNS hostname, i.e. `esp32tap.local`. This is the name the SRV record
/// points at and the name the A record answers for.
const HOSTNAME: &core::ffi::CStr = c"esp32tap";

/// Human-readable instance name — what a device picker shows. Mirrors the
/// Pi's `Treadmill on %h` shape without the Avahi wildcard, which has no
/// equivalent here.
const INSTANCE: &core::ffi::CStr = c"Treadmill on esp32tap";

fn init() -> sys::esp_err_t {
    // SAFETY: no arguments; starts the responder task and returns esp_err_t.
    unsafe { sys::mdns_init() }
}

fn hostname_set(name: &core::ffi::CStr) -> sys::esp_err_t {
    // SAFETY: `name` is a `'static` NUL-terminated literal, read for the
    // duration of the call; the component copies what it retains.
    unsafe { sys::mdns_hostname_set(name.as_ptr()) }
}

fn instance_name_set(name: &core::ffi::CStr) -> sys::esp_err_t {
    // SAFETY: as above — `'static` literal, copied by the callee.
    unsafe { sys::mdns_instance_name_set(name.as_ptr()) }
}

fn register_netif(netif: *mut sys::esp_netif_t) -> sys::esp_err_t {
    // SAFETY: `netif` is a live IDF-owned handle (checked non-null by the
    // caller) that outlives the responder — it is the default Ethernet netif,
    // which is never destroyed. mDNS stores the pointer, it does not own it.
    unsafe { sys::mdns_register_netif(netif) }
}

fn netif_action(netif: *mut sys::esp_netif_t, action: sys::mdns_event_actions_t) -> sys::esp_err_t {
    // SAFETY: `netif` is the same live handle just registered; `action` is a
    // plain bitmask. No memory crosses the boundary.
    unsafe { sys::mdns_netif_action(netif, action) }
}

fn service_add(port: u16, txt: &mut [sys::mdns_txt_item_t]) -> sys::esp_err_t {
    // SAFETY: the type/proto strings and every key/value inside `txt` are
    // `'static` literals; `txt` is a live exclusive borrow whose length is
    // passed explicitly, so the callee cannot read past it. The instance name
    // is NULL, which `mdns_service_add` documents as "use the global instance
    // name" — the one set just above. All strings are copied by the component.
    unsafe {
        sys::mdns_service_add(
            core::ptr::null(),
            SERVICE_TYPE.as_ptr(),
            SERVICE_PROTO.as_ptr(),
            port,
            txt.as_mut_ptr(),
            txt.len(),
        )
    }
}

/// Publish `_treadmill._tcp` on `port`.
///
/// Call once, AFTER the netif has an address: the responder needs an interface
/// to answer on, and a service added before the link is up advertises an
/// address the device does not have.
pub fn advertise(port: u16) -> NetResult {
    let netif = eth_netif();
    if netif.is_null() {
        // No interface to answer on. Refuse rather than start a responder that
        // would look healthy and reply to nothing.
        return Err(sys::ESP_ERR_INVALID_STATE);
    }

    check(init())?;
    check(register_netif(netif))?;
    // ENABLE first: this is what opens the UDP PCB on the interface. Without
    // it the responder runs and never hears a query.
    check(netif_action(
        netif,
        sys::mdns_event_actions_t_MDNS_EVENT_ENABLE_IP4,
    ))?;

    check(hostname_set(HOSTNAME))?;
    check(instance_name_set(INSTANCE))?;

    // Built here rather than as a `static` because `mdns_txt_item_t` holds raw
    // pointers and is therefore not `Sync`. It only has to outlive the call —
    // the component copies the strings into its own record.
    let mut txt = [
        sys::mdns_txt_item_t {
            key: c"scheme".as_ptr(),
            value: c"https".as_ptr(),
        },
        sys::mdns_txt_item_t {
            key: c"path".as_ptr(),
            value: c"/".as_ptr(),
        },
    ];
    check(service_add(port, &mut txt))?;

    // ANNOUNCE last, once the record is complete: an unsolicited announcement
    // is what lets a client that is already browsing see the device without
    // waiting for its next query.
    check(netif_action(
        netif,
        sys::mdns_event_actions_t_MDNS_EVENT_ANNOUNCE_IP4,
    ))
}
