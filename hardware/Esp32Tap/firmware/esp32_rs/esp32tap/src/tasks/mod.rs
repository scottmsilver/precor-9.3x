//! The supervised core-0 tasks (PLAN's normative WDT matrix).
//!
//! | Task           | Core | Prio | Stack | WDT                       | Cadence | Source              |
//! |----------------|------|------|-------|---------------------------|---------|---------------------|
//! | serial_engine  | 0    | 10   | 8192  | subscribe, ABORT on fail  | 5 ms    | tasks/serial_engine |
//! | emulate_cycle  | 0    | 9    | 6144  | subscribe, ABORT on fail  | 100 ms  | tasks/emulate_cycle |
//! | interval_exec  | 0    | 5    | 16384 | subscribe, ABORT on fail  | 1 s     | tasks/interval_executor |
//! | session        | 0    | 4    | 12288 | subscribe, ABORT on fail  | 1 s     | net/session (feature `net`) |
//! | shim_task      | 0    | 4    | 6144  | subscribe, ABORT on fail  | 100 ms  | qemu_test/shim_task (feature `qemu-test`, NEVER flashed) |
//!
//! THE COACH TIER IS NOT IN THE MATRIX EITHER, for a reason that is one step
//! stronger than the radio's. `net::coach::run` (core 0, prio 3, stack 12288,
//! feature `net`) BLOCKS ON A NETWORK CALL — a Gemini round trip takes seconds
//! and its duration is a remote server's choice — and a 2 s task watchdog whose
//! remedy is a panic cannot coexist with that: the first slow answer would
//! reboot the device and DROP THE RELAY MID-RUN. It is bounded by its own
//! budgets instead (`HTTP_TIMEOUT_MS` per socket operation, `TURN_BUDGET` over
//! the whole turn), and a turn that overruns is abandoned and reported.
//!
//! What that costs, stated plainly: a wedged coach task is NOT detected or
//! recovered, and the symptom is that `POST /api/chat` starts answering 429
//! ("still working on the last one") forever. The belt, the console, the HTTPS
//! server and `/ws` are all unaffected — the coach task holds NO LOCK ANY
//! OTHER TASK CAN ACQUIRE across its network call, and touches the belt only
//! through `control::command`.
//!
//! That wording is deliberate and was wrong before. This file used to say
//! "holds no lock", which `net::coach.rs:86` explicitly denies: `turn_impl`
//! takes the coach's own `WORK` mutex and holds it across the round trip. The
//! effect is harmless — `WORK` is the coach's request buffer, scanner and
//! history ring, private to that module, so nothing else can name it or wait
//! on it — but this matrix is the normative document somebody consults when
//! deciding whether a task is safe unsupervised, and it must not assert
//! something the implementation file denies in the same repository.
//!
//! THE BLE TIER IS NOT IN THE MATRIX, AND THAT IS A DECISION RATHER THAN A
//! HOLE. `ble::run` (core 0, prio 3 — the lowest in the system, stack 4096,
//! 1 s cadence, feature `ble`) deliberately does NOT subscribe to the task
//! watchdog. The watchdog's action here is `panic -> silent reboot`, and a
//! reboot DROPS THE RELAY MID-RUN. Trading a working treadmill for a stalled
//! radio is the wrong trade every time: Bluetooth is a convenience, the belt
//! is the point. `tools/check_wdt_chain.py` discovers supervised tasks by
//! their `wdt::subscribe_current_task()` call, so a task that does not
//! subscribe needs no row — it is named here anyway so the absence is
//! findable, and so the next person does not "fix" it.
//!
//! What that costs, stated plainly: a wedged NimBLE host is NOT detected or
//! recovered. The observable symptom is Bluetooth going quiet; the belt, the
//! console, the HTTPS server and `/ws` are all unaffected, because nothing
//! above them waits on the radio (see the spawn ordering in `main`).
//!
//! THE SESSION RECORDER IS IN THE MATRIX EVEN THOUGH IT LIVES IN `net`. It was
//! absent from this table for a whole slice while being a fourth
//! WDT-supervised task — and the only one that touches flash, which is the
//! slowest thing this firmware does on purpose. A matrix that does not name
//! every supervised task is not a matrix; `tools/check_wdt_chain.py` now
//! DISCOVERS the subscribers instead of hard-coding three of them, and fails if
//! the set drifts from this table.
//!
//! A stall in any of them panics -> silent reboot -> GPIO21 Hi-Z -> R23
//! pull-down -> relay released. The hardware completes the guarantee; there is
//! deliberately no software "WDT handler".

// COMPILER-ENFORCED unsafe containment for this module and every module
// below it. `forbid` (unlike the crate root's `deny`) CANNOT be lifted by an
// inner `#[allow(unsafe_code)]` — that is a hard error, not a warning — so
// this is a guarantee rather than a convention. Added 2026-07-28 after a
// reviewer disproved the "deny contains it" claim by counterexample.
#![forbid(unsafe_code)]


pub mod burst_buffer;
pub mod emulate_cycle;
pub mod interval_executor;
pub mod serial_engine;

/// FreeRTOS tick guard.
///
/// `vTaskDelay(0)` is a busy spin, so if `CONFIG_FREERTOS_HZ` were low enough
/// that `pdMS_TO_TICKS(5)` truncated to 0, the prio-10 core-0 serial task
/// would starve `app_main` (the other supervised tasks never get created) and
/// the core-0 idle task — a 2 s task-WDT panic, silent reboot, forever.
///
/// PARITY, NOT IMPROVEMENT: this is exactly the guarantee the C++
/// `static_assert` already gives. Rust adds nothing here, and the FreeRTOS
/// tick misconfiguration remains one of the three C++ defects Rust does NOT
/// catch on its own.
pub const _: () = assert!(esp_idf_sys::configTICK_RATE_HZ == 1000);

/// Milliseconds -> ticks. Valid only because of the assertion above.
pub const fn ms_to_ticks(ms: u32) -> u32 {
    ms * (esp_idf_sys::configTICK_RATE_HZ / 1000)
}

pub const SERIAL_LOOP_MS: u32 = 5;
pub const EMULATE_BURST_GAP_MS: u32 = 100;
pub const EXECUTOR_TICK_MS: u32 = 1000;

const _: () = assert!(ms_to_ticks(SERIAL_LOOP_MS) > 0);
const _: () = assert!(ms_to_ticks(EMULATE_BURST_GAP_MS) > 0);

pub fn delay_ms(ms: u32) {
    std::thread::sleep(std::time::Duration::from_millis(ms as u64));
}
