//! The qemu_test task: audit drain, QT command execution, QTSTATE snapshots.
//!
//! Every observable string below is EXACT — the harness greps them:
//!  * `QTAUDIT <abs_index> <text>`  (`_QTAUDIT_RE`)
//!  * `QTSTATE mode=… relay=… tx=… fault=… speed=… incline=… cons_bytes=…
//!     motor_bytes=… io_relay=… io_tx=… t_us=…`  (`_QTSTATE_RE`) — FIELD ORDER
//!     and SINGLE-SPACE separation are load-bearing.
//!  * `QTOK <verb> …` — the TRAILING SPACE after `<verb>` is load-bearing
//!    (`cmd_ok()` waits for the regex `QTOK <verb> `).
//!  * `QTERR bad_frame|motion_args|k1_args|level_args|unknown_verb <verb>`

use crate::context::{lock, FirmwareContext};
use crate::hal::wdt;
use crate::qemu_test::{K1Mode, QemuTestSafetyIo};
use crate::qt;
use crate::tasks::delay_ms;
use safety_core::hal::{SafetyIo, SerialOut};
use safety_core::safety::controller::{ConnectionIdentity, Transport, EVENT_CAPACITY};
use safety_core::FixedStr;

/// The EXECUTOR handle is HARDCODED to 1: S3/S5/S7 match
/// `lease_acquired:EXECUTOR:1:` by prefix.
const EXECUTOR_HANDLE: i32 = 1;

const BATCH: usize = 32;
const QEMU_TEST_TICK_MS: u32 = 100;

fn parse_int(s: &str) -> Option<i32> {
    if s.is_empty() || s.len() > 10 {
        return None;
    }
    s.parse::<i32>().ok()
}

fn print_state(ctx: &'static FirmwareContext) {
    // Snapshot inside ONE critical section so a QTSTATE line is a coherent
    // instant; print outside.
    let (mode, relay, tx, fault, speed, incline, cons, motor, io_relay, io_tx, t_us) = {
        let g = lock(&ctx.guarded);
        (
            g.controller.mode().as_str(),
            g.controller.relay_cmd().get() as u32,
            g.controller.tx_enable().get() as u32,
            g.controller.fault_latched() as u32,
            g.controller.speed_tenths().get(),
            g.controller.incline_half_percent().get(),
            // cons_bytes/motor_bytes come from ModeStateMachine's counters —
            // NOT the controller's — which is what S1 asserts against.
            g.mode.console_bytes(),
            g.mode.motor_bytes(),
            g.io.observed_relay() as u32,
            g.io.observed_tx() as u32,
            ctx.clock.now().get(),
        )
    };
    qt!(
        "QTSTATE mode={} relay={} tx={} fault={} speed={} incline={} \
cons_bytes={} motor_bytes={} io_relay={} io_tx={} t_us={}",
        mode,
        relay,
        tx,
        fault,
        speed,
        incline,
        cons,
        motor,
        io_relay,
        io_tx,
        t_us
    );
}

struct Owner {
    identity: ConnectionIdentity,
    generation: i64,
}

fn execute_command(ctx: &'static FirmwareContext, line: &str, owner: &mut Owner) {
    let Some(rest) = line.strip_prefix("QT ") else {
        qt!("QTERR bad_frame");
        return;
    };
    let rest = rest.trim_end_matches(['\r', ' ']);
    let (verb, args) = match rest.find(' ') {
        Some(sp) => (&rest[..sp], &rest[sp + 1..]),
        None => (rest, ""),
    };

    match verb {
        "state" => print_state(ctx),

        "lease" => {
            let (connected, acquired, gen) = {
                let mut g = lock(&ctx.guarded);
                // `checked_add` + a real `Option` rather than `+= 1` and
                // `expect`: this build is panic=abort, so ANY reachable panic
                // drops the relay. Overflow needs 2^63 commands and cannot
                // happen, but "unreachable" is not the same as "cannot panic".
                let Some(next) = owner.generation.checked_add(1) else {
                    qt!("QTERR lease_generation_exhausted");
                    return;
                };
                owner.generation = next;
                let Some(identity) =
                    ConnectionIdentity::new(Transport::Executor, EXECUTOR_HANDLE, next)
                else {
                    qt!("QTERR lease_generation_exhausted");
                    return;
                };
                owner.identity = identity;
                let connected = g.controller.connect(&identity);
                let now = ctx.clock.now();
                let acquired = g.controller.acquire(&identity, now);
                (connected, acquired, owner.generation)
            };
            qt!(
                "QTOK lease connect={} acquire={} gen={}",
                connected as u32,
                acquired as u32,
                gen
            );
        }

        "emulate" => {
            // The SAME gate expression production uses: the physical TX pad
            // level (GPIO17 reads 0 in QEMU -> idle-low true).
            let ok = {
                let mut g = lock(&ctx.guarded);
                let idle_low = g.console_uart.tx_idle_low();
                let now = ctx.clock.now();
                let ok = g.controller.request_emulate(&owner.identity, now, idle_low);
                g.apply_outputs();
                ok
            };
            qt!("QTOK emulate ok={}", ok as u32);
        }

        "motion" => {
            let Some(sp2) = args.find(' ') else {
                qt!("QTERR motion_args");
                return;
            };
            let (Some(speed), Some(incline)) =
                (parse_int(&args[..sp2]), parse_int(&args[sp2 + 1..]))
            else {
                qt!("QTERR motion_args");
                return;
            };
            let ok = {
                let mut g = lock(&ctx.guarded);
                let now = ctx.clock.now();
                g.controller.command_motion(
                    &owner.identity,
                    safety_core::units::SpeedTenths::new(speed),
                    safety_core::units::InclineHalfPct::new(incline),
                    now,
                )
            };
            qt!("QTOK motion ok={}", ok as u32);
        }

        "exit" => {
            let ok = {
                let mut g = lock(&ctx.guarded);
                let now = ctx.clock.now();
                let ok = g.controller.request_normal_exit(&owner.identity, now);
                g.apply_outputs();
                ok
            };
            qt!("QTOK exit ok={}", ok as u32);
        }

        "k1" => {
            let Some(m) = K1Mode::parse(args) else {
                qt!("QTERR k1_args");
                return;
            };
            {
                let g = lock(&ctx.guarded);
                g.io.script_k1(m);
            }
            qt!("QTOK k1 mode={}", args);
        }

        "tread" | "vbus" => {
            let Some(v) = parse_int(args) else {
                qt!("QTERR level_args");
                return;
            };
            if v != 0 && v != 1 {
                qt!("QTERR level_args");
                return;
            }
            {
                let g = lock(&ctx.guarded);
                if verb == "tread" {
                    g.io.script_tread_ok(v == 1);
                } else {
                    g.io.script_vbus_present(v == 1);
                }
            }
            qt!("QTOK {} v={}", verb, v);
        }

        // `wsdrophello` is NETWORK-TIER ONLY and out of scope for this port,
        // so it is deliberately NOT implemented and falls through to
        // unknown_verb. Nothing in S1-S7 uses it; only `test_net_scenarios.py`
        // does, and that file is `-m net`.
        _ => qt!("QTERR unknown_verb {}", verb),
    }
}

pub fn run(ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        wdt::abort(c"qemu_test: task WDT subscribe failed");
    }
    crate::logi!("qemu_test task started (WDT-supervised)");

    let mut next_event: u64 = 0;
    let mut owner = Owner {
        identity: ConnectionIdentity::new(Transport::Executor, EXECUTOR_HANDLE, 0)
            .expect("generation 0 is valid"),
        generation: 0,
    };

    // Batch buffer allocated ONCE, off the task stack. The C++ uses `static`
    // arrays here with the comment "Static (not task stack): batch buffers are
    // multi-KB" — and it is not advisory: a 32-entry stack array of
    // (u64, FixedStr<96>) is ~3.6 KB against a 6144-byte stack, and the first
    // version of this task overflowed it into a LoadProhibited panic loop
    // (EXCVADDR=0x5) the moment the first audit event arrived.
    let mut batch: Vec<(u64, FixedStr<96>)> = vec![(0, FixedStr::new()); BATCH];

    loop {
        wdt::feed();

        // (i) Drain the audit ring, <= BATCH events per lock hold, looping
        // until caught up.
        loop {
            let n = {
                let g = lock(&ctx.guarded);
                let total = g.controller.event_count();
                let cap = EVENT_CAPACITY as u64;
                let mut n = 0usize;
                if total > next_event && total - next_event > cap {
                    // Evicted. Jump forward so the harness can SEE the index
                    // gap rather than silently losing events — but FIRST
                    // recover any fault/emergency record inside the skipped
                    // range from the eviction-resistant critical log. Those
                    // records say WHY the machine stopped, and losing them to
                    // a flood of routine traffic is precisely the defect that
                    // log exists to prevent.
                    //
                    // Copied into `batch` and printed OUTSIDE the lock, like
                    // everything else here: printing ~16 lines on UART0 under
                    // the safety mutex would be tens of milliseconds of lock
                    // hold, which is more than the whole relay-feedback
                    // deadline.
                    let skip_to = total - cap;
                    for (idx, text) in g.controller.critical_events() {
                        if idx >= next_event && idx < skip_to && n < BATCH {
                            batch[n] = (idx, FixedStr::from_str_truncating(text));
                            n += 1;
                        }
                    }
                    next_event = skip_to;
                }
                while next_event < total && n < BATCH {
                    let text = g.controller.event_at(next_event).unwrap_or("");
                    batch[n] = (next_event, FixedStr::from_str_truncating(text));
                    n += 1;
                    next_event += 1;
                }
                n
            };
            for (idx, text) in batch.iter().take(n) {
                qt!("QTAUDIT {} {}", idx, text.as_str());
            }
            if n < BATCH {
                break;
            }
        }

        // (ii) Execute queued harness commands. NEVER from inside the
        // serial-engine poll — that would deadlock on the safety lock.
        loop {
            let cmd = {
                let g = lock(&ctx.guarded);
                g.motor_tap.pop_command()
            };
            match cmd {
                Some(line) => execute_command(ctx, line.as_str(), &mut owner),
                None => break,
            }
        }

        delay_ms(QEMU_TEST_TICK_MS);
    }
}

/// Keep the `SafetyIo`/`QemuTestSafetyIo` bound visible in this file: the
/// shim reads the poles through the same trait production uses.
const _: fn(&QemuTestSafetyIo) -> bool = |io| io.tread_ok().get();
