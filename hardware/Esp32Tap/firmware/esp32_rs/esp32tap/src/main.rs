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

#[cfg(feature = "ble")]
#[allow(unsafe_code)] // NimBLE FFI only — see ble/mod.rs.
mod ble;
mod context;
mod control;
#[allow(unsafe_code)] // THE ONLY unsafe in the firmware — see hal/mod.rs.
mod hal;
mod hr;
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

/// Spawn one pinned core-0 task.
///
/// `ThreadSpawnConfiguration` is GLOBAL-then-spawn, so every spawn must happen
/// single-threaded, in order, from `main`. They cannot be reordered or
/// parallelised without the configuration racing.
///
/// WHETHER A TASK IS WDT-SUPERVISED IS THE TASK'S OWN DECISION, taken in its
/// body by calling `wdt::subscribe_current_task()`. Every task spawned here
/// except the BLE tier does; `tools/check_wdt_chain.py` DISCOVERS the set from
/// those calls and holds it against the normative matrix in `tasks/mod.rs`.
/// The radio's exemption is argued at `ble::run`: the watchdog's remedy is a
/// reboot, and a reboot drops the relay mid-run — which is the wrong trade for
/// a stalled convenience feature.
fn spawn_pinned(
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
    spawn_pinned(c"serial_engine", 8192, 10, tasks::serial_engine::run);
    spawn_pinned(c"emulate_cycle", 6144, 9, tasks::emulate_cycle::run);
    spawn_pinned(c"interval_exec", 16384, 5, tasks::interval_executor::run);

    // (5) Network/application tier — out of scope for this port; absent.

    // (6) Behavioral-harness shim. The banner makes a test image
    // unmistakable: scripted safety IO, motor tap on UART0.
    #[cfg(feature = "qemu-test")]
    {
        logw!("esp32tap QEMU-TEST build (never flash to hardware)");
        spawn_pinned(c"qemu_test", 6144, 4, qemu_test::run);
    }

    logi!("esp32tap phase-1 safety core started (Proxy)");

    // Slice 1: network foundation. AFTER the safety banner and after the
    // supervised tasks exist, so a link failure can never delay the belt
    // reaching its safe state — the treadmill must be controllable from the
    // physical console whether or not any network ever comes up.
    #[cfg(feature = "net")]
    {
        // The persistence tier comes up BEFORE the link, and independently of
        // it: a run must be recorded whether or not a network ever appears.
        // NVS is initialised here rather than inside `tls::identity` alone,
        // because the profile is read from it and that read happens first.
        net::tls::nvs_init();
        net::profile::load();
        if net::store::mount_once() {
            logi!(
                "store: mounted, {} bytes resident",
                net::store::Stores::resident_bytes()
            );
        } else {
            logw!("store: no usable storage partition — nothing will persist");
        }
        // Lower priority than the interval executor: it must never delay a
        // tick, and a 4 KB sector erase is the slowest thing this firmware
        // does on purpose.
        //
        // 12288 BYTES, MEASURED RATHER THAN CHOSEN. At 6144 this task
        // overflowed and rebooted the device ("A stack overflow in task
        // session has been detected"), which drops the relay mid-run. The
        // frame is dominated by ONE read-modify-write of a stored entry:
        // decoding it materialises a `Program` (a `[Interval; 24]` array,
        // ~900 B) and the `Entry` around it, and the value passes through
        // several temporaries on its way out of the store. Level-1 interrupts
        // also run on the interrupted task's stack, so the headroom above that
        // is reserved, not spare.
        spawn_pinned(c"session", net::session::STACK_BYTES, 4, net::session::run);

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
                                    // Published BEFORE the routes: `/ws` is
                                    // registered inside `http::start`, so a
                                    // client can connect the moment it
                                    // returns, and a socket the pusher does
                                    // not know about is a screen that never
                                    // updates.
                                    net::ws::set_server(h);
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
                                    // The persistence tier. Registered even if
                                    // the store failed to mount: the list
                                    // endpoints answer with an empty array in
                                    // that case, which the app handles, and a
                                    // missing route would look like a much
                                    // older firmware.
                                    match net::records::register(h) {
                                        Ok(()) => logi!("record routes registered"),
                                        Err(e) => logi!("net: record register failed ({})", e),
                                    }
                                    match net::profile::register(h) {
                                        Ok(()) => logi!("profile routes registered"),
                                        Err(e) => logi!("net: profile register failed ({})", e),
                                    }
                                    // The heart-rate routes. Registered
                                    // WHETHER OR NOT this build has a radio:
                                    // the app calls all four, and a device
                                    // without Bluetooth must answer
                                    // "not connected" rather than 404, which
                                    // to a client looks like much older
                                    // firmware. Same contract as the Pi with
                                    // hrm-daemon stopped.
                                    match net::hrm::register(h) {
                                        Ok(()) => logi!("hrm routes registered"),
                                        Err(e) => logi!("net: hrm register failed ({})", e),
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

    // (7) The radio. DEAD LAST, and that ordering is the whole safe-degradation
    // argument in one line: the belt is already controllable, the server is
    // already answering, and `/ws` is already pushing before NimBLE is asked
    // to exist. Nothing above this point waits on anything below it, so a
    // controller that is absent (QEMU), broken, or simply slow to come up
    // costs a log line and nothing else.
    //
    // Spawned rather than called: `nimble_port_init` runs on the spawned task,
    // so even a HANG inside it leaves `main` free to return and every other
    // task running.
    #[cfg(feature = "ble")]
    spawn_pinned(c"ble", ble::STACK_BYTES, ble::PRIORITY, ble::run);

    // app_main returns into the IDF main task, which then idles. The
    // supervised tasks own the machine from here.
}
