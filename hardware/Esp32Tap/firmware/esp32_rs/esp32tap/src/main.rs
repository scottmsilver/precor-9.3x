//! Esp32Tap firmware entry point — Rust safety core (phase 1).
//!
//! Boot order (PLAN normative-safe):
//!   1. Safety IO init — RELAY_CMD/TX_ENABLE driven LOW first (LEVEL before
//!      DIRECTION), feedback/TREAD_OK inputs configured with no internal
//!      pulls. The controller is constructed in PROXY with `Feedback::Unknown`
//!      — boot feedback is UNKNOWN until the first real GPIO sample, never
//!      assumed bypass.
//!   2. UARTs configured inverted 9600 8N1.
//!   3. Task WDT is system-initialised (`CONFIG_ESP_TASK_WDT_INIT=y`, 2 s,
//!      panic) before `app_main` runs.
//!   4. Three supervised core-0 tasks created; each subscribes itself to the
//!      task WDT and aborts if the subscribe fails.
//!
//! A stall in any supervised task panics -> silent reboot -> GPIO21 Hi-Z ->
//! R23 pull-down -> relay released. The hardware completes the guarantee;
//! there is deliberately no software "WDT handler".

#![deny(unsafe_code)]

mod context;
mod control;
#[allow(unsafe_code)] // THE ONLY unsafe in the firmware — see hal/mod.rs.
mod hal;
#[cfg(feature = "net")]
#[allow(unsafe_code)] // esp_netif/esp_eth FFI only — see net/mod.rs.
mod net;
#[allow(unsafe_code)] // esp_log_write FFI only.
mod log;
mod pins;
#[cfg(feature = "qemu-test")]
#[allow(unsafe_code)] // UART0 RX drain + esp_rom_printf FFI only.
mod qemu_test;
mod tasks;

use context::{lock, FirmwareContext};
use esp_idf_hal::cpu::Core;
use esp_idf_hal::task::thread::ThreadSpawnConfiguration;
use crate::hal::ConsoleMotorUart;
use safety_core::hal::SafetyIo;
use safety_core::safety::controller::SafeMode;

/// Static, not on the main-task stack: `FirmwareContext` embeds multi-KB parse
/// buffers and a 256-slot audit ring (PLAN's QEMU-validated stack constraint).
static CTX: FirmwareContext = FirmwareContext::new();

/// Spawn one supervised core-0 task.
///
/// `ThreadSpawnConfiguration` is GLOBAL-then-spawn, so all three spawns must
/// happen single-threaded, in order, from `main`. They cannot be reordered or
/// parallelised without the configuration racing.
fn spawn_supervised(
    name: &'static core::ffi::CStr,
    stack: usize,
    prio: u8,
    body: fn(&'static FirmwareContext) -> !,
) {
    ThreadSpawnConfiguration {
        name: Some(name),
        stack_size: stack,
        priority: prio,
        pin_to_core: Some(Core::Core0),
        ..Default::default()
    }
    .set()
    .expect("thread spawn configuration");

    std::thread::Builder::new()
        .stack_size(stack)
        .spawn(move || body(&CTX))
        .expect("spawn supervised task");
}

fn halt(msg: &str) -> ! {
    loge!("{msg}");
    loop {
        tasks::delay_ms(1000);
    }
}

fn main() {
    esp_idf_sys::link_patches();

    // (1) Safety outputs low before anything else.
    {
        let mut g = lock(&CTX.guarded);
        if !g.io.init() {
            drop(g);
            // Outputs are pulled down in hardware; refuse to start the engine
            // on a half-configured board.
            halt("safety IO init failed — halting in Proxy");
        }
    }


    // (2) Inverted UARTs.
    {
        let mut g = lock(&CTX.guarded);
        let mut uarts_ok = g.console_uart.init();
        uarts_ok = g.motor_tap.init() && uarts_ok;
        if !uarts_ok {
            drop(g);
            halt("UART init failed — halting in Proxy");
        }
    }
    // The writer ADOPTS the already-initialised UART1 rather than
    // re-installing its driver (see `adopt_initialised`).
    {
        let mut w = lock(&CTX.writer);
        w.uart = ConsoleMotorUart::adopt_initialised();
    }

    // (3) Seed the controller with a first REAL feedback/permission sample,
    // then emit the boot-state audit line qemu_smoke.sh asserts.
    {
        let mut g = lock(&CTX.guarded);
        let now = CTX.clock.now();
        let tread = g.io.tread_ok();
        g.controller.set_tread_ok(tread, now);
        let (nc, no) = (g.io.k1_nc_high(), g.io.k1_no_high());
        g.controller.observe_relay_feedback(nc, no, now);
        g.apply_outputs();

        // EXACT STRING — qemu_smoke.sh greps this as a substring. Under QEMU's
        // stub GPIOs (all inputs read 0) the default build boots BOTH_CLOSED
        // and therefore fault=1, while still being
        // "mode=PROXY relay=released tx_enable=0" — which is what the
        // assertion checks. Do NOT "fix" the production boot to fault=0 under
        // QEMU; the correct behaviour on a real board is a real BYPASS sample.
        logi!(
            "boot state: mode={} relay={} tx_enable={} fault={}",
            if g.controller.mode() == SafeMode::Proxy {
                "PROXY"
            } else {
                "NOT_PROXY"
            },
            if g.controller.relay_cmd().get() {
                "energized"
            } else {
                "released"
            },
            g.controller.tx_enable().get() as u32,
            g.controller.fault_latched() as u32
        );
    }

    // (4) Supervised tasks — all pinned to core 0.
    spawn_supervised(c"serial_engine", 8192, 10, tasks::serial_engine::run);
    spawn_supervised(c"emulate_cycle", 6144, 9, tasks::emulate_cycle::run);
    spawn_supervised(c"interval_exec", 16384, 5, tasks::interval_executor::run);

    // (5) Network/application tier — out of scope for this port; absent.

    // (6) Behavioral-harness shim. The banner makes a test image
    // unmistakable: scripted safety IO, motor tap on UART0.
    #[cfg(feature = "qemu-test")]
    {
        logw!("esp32tap QEMU-TEST build (never flash to hardware)");
        spawn_supervised(c"qemu_test", 6144, 4, qemu_test::run);
    }

    logi!("esp32tap phase-1 safety core started (Proxy)");

    // Slice 1: network foundation. AFTER the safety banner and after the
    // supervised tasks exist, so a link failure can never delay the belt
    // reaching its safe state — the treadmill must be controllable from the
    // physical console whether or not any network ever comes up.
    #[cfg(feature = "net")]
    {
        match net::bring_up() {
            Ok(()) => match net::wait_for_ip(15_000) {
                Ok(addr) => {
                    let o = addr.to_le_bytes();
                    logi!("net: link up, ip {}.{}.{}.{}", o[0], o[1], o[2], o[3]);
                    // The identity comes FIRST and the server does not start
                    // without it: there is no plaintext fallback, because the
                    // advertised record says `scheme=https` and a client that
                    // trusted it would be wrong.
                    match net::tls::identity() {
                        Ok((id, origin)) => {
                            logi!(
                                "tls: identity {} ({} byte cert)",
                                match origin {
                                    net::tls::Origin::Nvs => "loaded from NVS",
                                    net::tls::Origin::Generated => "generated this boot",
                                },
                                id.cert().len()
                            );
                            match net::http::start(id) {
                                Ok(h) => {
                                    match net::api::register(h) {
                                        Ok(()) => logi!("api routes registered"),
                                        Err(e) => logi!("net: api register failed ({})", e),
                                    }
                                    // The program endpoints are what let a
                                    // workout be put on the device. They are
                                    // registered separately so a failure here
                                    // is distinguishable in the log from a
                                    // control-path failure.
                                    match net::program::register(h) {
                                        Ok(()) => logi!("program routes registered"),
                                        Err(e) => logi!("net: program register failed ({})", e),
                                    }
                                    // EXACT STRING: the net scenarios wait on
                                    // this before issuing their first request.
                                    logi!("https server up on :{}", net::http::port());
                                    // Advertised LAST, and only on success: a
                                    // record pointing at a server that failed
                                    // to start sends the app to a dead port.
                                    match net::mdns::advertise(net::http::port()) {
                                        Ok(()) => logi!(
                                            "mdns: _treadmill._tcp on :{}",
                                            net::http::port()
                                        ),
                                        Err(e) => logi!("net: mdns failed (err {})", e),
                                    }
                                }
                                Err(e) => logi!("net: https start failed (err {})", e),
                            }
                        }
                        Err(e) => logi!("net: tls identity failed (err {}) — no server", e),
                    }
                }
                Err(e) => logi!("net: no DHCP lease (err {}) — continuing headless", e),
            },
            Err(e) => logi!("net: bring-up failed (err {}) — continuing headless", e),
        }
    }

    // app_main returns into the IDF main task, which then idles. The
    // supervised tasks own the machine from here.
}
