//! Slice 1 — network foundation.
//!
//! Brings up an Ethernet netif and waits for DHCP. Under QEMU this is the
//! emulated `openeth` NIC (`-nic user,model=open_eth,...`); on real hardware
//! the same netif plumbing is fed by WiFi instead, which is why link bring-up
//! is behind [`bring_up`] rather than inlined into `main`.
//!
//! WHY RAW `esp-idf-sys` AND NOT `esp-idf-svc`: esp-idf-svc would take the
//! target graph from 41 to 61 crates (serde, chrono, embedded-svc and their
//! tails) to provide wrappers over exactly the calls below. The firmware
//! already owns a documented `unsafe` boundary in `hal/`, so this module
//! follows that pattern.
//!
//! UNSAFE DISCIPLINE: every FFI call gets its own one-expression `unsafe`
//! block with the invariant it relies on. Struct setup stays in safe code, so
//! the audited surface is the FFI boundary itself and nothing more.
//!
//! IDF DEFAULTS ARE REPLICATED, NOT GUESSED. `ETH_*_DEFAULT_CONFIG` and
//! `ESP_NETIF_DEFAULT_ETH` are C macros and do not survive bindgen, so their
//! expansions are transcribed with the IDF v5.5 source line they came from. If
//! IDF changes a default, that citation is what to re-check.

pub mod api;
pub mod tls;
pub mod http;

use esp_idf_sys as sys;

pub type NetResult = Result<(), sys::esp_err_t>;

fn check(err: sys::esp_err_t) -> NetResult {
    if err == sys::ESP_OK {
        Ok(())
    } else {
        Err(err)
    }
}

/// Each wrapper below is one IDF C call. Arguments are plain values or
/// pointers to caller-owned stack structs the callee only reads for the
/// duration of the call; none retain Rust memory. They run once, on one task,
/// before any socket use — guaranteed by `bring_up` being called once from
/// `main` before the network task starts.
fn netif_init() -> sys::esp_err_t {
    // SAFETY: no arguments; idempotent IDF init, returns esp_err_t.
    unsafe { sys::esp_netif_init() }
}
fn event_loop_create() -> sys::esp_err_t {
    // SAFETY: no arguments; returns ESP_ERR_INVALID_STATE if already created,
    // which the caller treats as success.
    unsafe { sys::esp_event_loop_create_default() }
}
fn netif_new(cfg: &sys::esp_netif_config_t) -> *mut sys::esp_netif_t {
    // SAFETY: `cfg` is a live borrow for the call; IDF copies what it needs.
    // The returned netif is owned by IDF and only ever passed back to it.
    unsafe { sys::esp_netif_new(cfg) }
}
fn mac_new_openeth(cfg: &sys::eth_mac_config_t) -> *mut sys::esp_eth_mac_t {
    // SAFETY: `cfg` is a live borrow, read-only for the call; the returned MAC
    // is owned by IDF and handed straight to esp_eth_driver_install.
    unsafe { sys::esp_eth_mac_new_openeth(cfg) }
}
fn phy_new_dp83848(cfg: &sys::eth_phy_config_t) -> *mut sys::esp_eth_phy_t {
    // SAFETY: as above — live read-only borrow, IDF-owned return value.
    unsafe { sys::esp_eth_phy_new_dp83848(cfg) }
}
fn driver_install(
    cfg: &sys::esp_eth_config_t,
    out: &mut sys::esp_eth_handle_t,
) -> sys::esp_err_t {
    // SAFETY: `cfg` is read for the call; `out` is a live exclusive borrow the
    // callee writes exactly once on success.
    unsafe { sys::esp_eth_driver_install(cfg, out) }
}
fn attach_glue(netif: *mut sys::esp_netif_t, h: sys::esp_eth_handle_t) -> sys::esp_err_t {
    // SAFETY: both handles are IDF-owned and still live (the driver was
    // installed above and is never stopped here). The glue allocated by
    // esp_eth_new_netif_glue is adopted by the netif.
    // The glue is an opaque driver handle to esp_netif_attach, which takes
    // it as `*mut c_void`; the cast is the C API's own convention.
    unsafe {
        sys::esp_netif_attach(netif, sys::esp_eth_new_netif_glue(h).cast())
    }
}
fn eth_start(h: sys::esp_eth_handle_t) -> sys::esp_err_t {
    // SAFETY: `h` is the live handle from driver_install.
    unsafe { sys::esp_eth_start(h) }
}
fn zeroed<T>() -> T {
    // SAFETY: every config struct used here is a bindgen POD of integers and
    // pointers, for which all-zero is a valid (and IDF-conventional) initial
    // value; each field the driver requires is then set explicitly.
    unsafe { core::mem::zeroed() }
}

/// Bring up the Ethernet link and start DHCP.
///
/// Call exactly once, before any socket work. Returns as soon as the driver is
/// started; use [`wait_for_ip`] to block until an address is bound.
pub fn bring_up() -> NetResult {
    check(netif_init())?;
    // Already-created is benign: another component may own the default loop.
    let ev = event_loop_create();
    if ev != sys::ESP_OK && ev != sys::ESP_ERR_INVALID_STATE {
        return Err(ev);
    }

    // ESP_NETIF_DEFAULT_ETH() — esp_netif_defaults.h:142.
    let netif_cfg = sys::esp_netif_config_t {
        base: core::ptr::addr_of!(sys::_g_esp_netif_inherent_eth_config),
        driver: core::ptr::null_mut(),
        // SAFETY: reading an immutable static exported by IDF.
        stack: unsafe { sys::_g_esp_netif_netstack_default_eth },
    };
    let netif = netif_new(&netif_cfg);
    if netif.is_null() {
        return Err(sys::ESP_FAIL);
    }

    // ETH_MAC_DEFAULT_CONFIG() — esp_eth_mac.h:407.
    let mut mac_cfg: sys::eth_mac_config_t = zeroed();
    mac_cfg.sw_reset_timeout_ms = 100;
    mac_cfg.rx_task_stack_size = 4096;
    mac_cfg.rx_task_prio = 15;
    mac_cfg.flags = 0;

    // ETH_PHY_DEFAULT_CONFIG() — esp_eth_phy.h:289, with the openeth override
    // the IDF reference itself applies (eth_connect.c:149): autonegotiation
    // must not wait 4 s on an emulated link.
    let mut phy_cfg: sys::eth_phy_config_t = zeroed();
    phy_cfg.phy_addr = sys::ESP_ETH_PHY_ADDR_AUTO;
    phy_cfg.reset_timeout_ms = 100;
    phy_cfg.autonego_timeout_ms = 100;
    phy_cfg.reset_gpio_num = -1; // no reset line on the emulated NIC
    phy_cfg.hw_reset_assert_time_us = 0;
    phy_cfg.post_hw_reset_delay_ms = 0;

    let mac = mac_new_openeth(&mac_cfg);
    if mac.is_null() {
        return Err(sys::ESP_FAIL);
    }
    // openeth is paired with the dp83848 PHY driver — eth_connect.c:151.
    let phy = phy_new_dp83848(&phy_cfg);
    if phy.is_null() {
        return Err(sys::ESP_FAIL);
    }

    // ETH_DEFAULT_CONFIG(mac, phy) — esp_eth_driver.h:190.
    let mut eth_cfg: sys::esp_eth_config_t = zeroed();
    eth_cfg.mac = mac;
    eth_cfg.phy = phy;
    eth_cfg.check_link_period_ms = 2000;

    let mut handle: sys::esp_eth_handle_t = core::ptr::null_mut();
    check(driver_install(&eth_cfg, &mut handle))?;
    check(attach_glue(netif, handle))?;
    check(eth_start(handle))
}

/// Current IPv4 address of the Ethernet netif, or 0 if none is bound.
fn eth_ipv4() -> u32 {
    // SAFETY: `esp_netif_get_handle_from_ifkey` returns a handle owned by IDF
    // which we only read; `ip_info` is fully written by the callee on ESP_OK
    // and is not read otherwise.
    unsafe {
        let netif = sys::esp_netif_get_handle_from_ifkey(c"ETH_DEF".as_ptr());
        if netif.is_null() {
            return 0;
        }
        let mut ip: sys::esp_netif_ip_info_t = core::mem::zeroed();
        if sys::esp_netif_get_ip_info(netif, &mut ip) == sys::ESP_OK {
            ip.ip.addr
        } else {
            0
        }
    }
}

fn delay_ms(ms: u32) {
    // SAFETY: plain FreeRTOS delay; no pointers involved.
    unsafe { sys::vTaskDelay(ms / 10) }
}

/// Block until the Ethernet netif has an IPv4 address, or the deadline passes.
///
/// Polls rather than subscribing to the event loop: this runs once at startup,
/// the poll is cheap, and it keeps the module free of static event handlers
/// whose lifetimes would outlive any Rust borrow.
pub fn wait_for_ip(timeout_ms: u32) -> Result<u32, sys::esp_err_t> {
    let mut waited = 0u32;
    loop {
        let addr = eth_ipv4();
        if addr != 0 {
            return Ok(addr);
        }
        if waited >= timeout_ms {
            return Err(sys::ESP_ERR_TIMEOUT);
        }
        delay_ms(100);
        waited += 100;
    }
}
