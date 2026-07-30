//! The interval executor: what makes a workout survive the tablet walking
//! away.
//!
//! It is a 1 s WDT-supervised task and nothing else. The DECISIONS — advance,
//! finish, what motion each interval wants — belong to `program_core`, which
//! is portable, allocation-free and host-tested in 0.00 s. This file is the
//! part that cannot be host-tested: the clock, the locks, the WDT, and the
//! route to the belt.
//!
//! # It never blocks on the network or on flash
//!
//! Not by care, by construction: the tick touches only the program mutex, the
//! safety mutex and `esp_timer`. After releasing both locks, a network build
//! may queue a non-blocking WebSocket push when safety pauses a program; it
//! never renders or writes a frame here. A wedged socket, an unreachable
//! tablet, a full flash — none of them can stall the tick, so none of them can
//! stall the WDT and reboot the device mid-run.
//!
//! # It has no private route to the belt
//!
//! Every speed and incline it commands goes through [`crate::control::command`],
//! which is the same function `POST /api/speed` calls: same clamps (the
//! controller's), same lease, same auto-emulate attempt, same
//! `apply_outputs()`. There is no `command_motion` call anywhere in this file.
//!
//! # Lock order
//!
//! `program` then `guarded`, and both are held across a tick. That is
//! deliberate: the decision and the command must be atomic, or a concurrent
//! `POST /api/program/stop` can land between them and leave the belt running
//! after the program has stopped. See `context.rs` for why the order is
//! sufficient.
//!
//! The 16384-byte stack is unchanged from the stub: it matches the C++ task
//! and the smoke gate was tuned against the current memory envelope.

use crate::context::{lock, FirmwareContext, Guarded};
use crate::control::{self, EntryIntent, Surface};
use crate::hal::wdt;
use crate::logi;
use crate::tasks::{delay_ms, EXECUTOR_TICK_MS};
use program_core::Plan;
use safety_core::units::Micros;

/// Apply a plan through the single belt path, optionally handing the belt back
/// afterwards.
///
/// THE ONE PLACE the executor's decisions become motion, called both by the
/// tick below and by the HTTP program endpoints (which already hold the
/// program lock, so they pass `g` in and the documented lock order is
/// preserved by the caller rather than re-established here).
///
/// A REFUSED MOTION is NOT retried. The controller has already recorded why in
/// the audit ring, and retrying a motion the safety layer refused is exactly
/// the behaviour that must not exist.
///
/// A DECLINED MODE TRANSITION is a different thing and IS asked again, by
/// `control::reassert` on the tick below. `request_emulate` declining because
/// the console frame is 1.6 s old is not the safety layer saying no to a
/// motion; it is the safety layer saying "not yet", and treating the two the
/// same cost a whole interval of a motionless belt under a running program.
pub fn apply_plan(g: &mut Guarded, plan: Plan, release_belt: bool, now: Micros) -> usize {
    let mut accepted = 0usize;
    for (speed, incline) in plan.commands() {
        if control::command(
            g,
            Surface::Executor,
            EntryIntent::Ordinary,
            *speed,
            *incline,
            now,
        )
        .is_ok()
        {
            accepted += 1;
        }
    }
    if release_belt {
        // The program is over — completed, stopped or reset. Hand the belt
        // back with PLAN's polite exit so a manual command can take it again.
        // Without this the executor's `NoDeadline` lease is held forever and
        // no HTTP request could ever command the belt after one workout.
        control::release(g, Surface::Executor, now);
    }
    accepted
}

pub fn run(ctx: &'static FirmwareContext) -> ! {
    if !wdt::subscribe_current_task() {
        wdt::abort(c"interval_exec: task WDT subscribe failed");
    }
    logi!("interval_executor task started (WDT-supervised)");

    let mut seconds: u32 = 0;
    // EDGE, not level: the belt is handed back on the tick where the program
    // STOPS running, not on every tick where no program is running. Otherwise
    // an idle device would take the safety lock once a second forever, for
    // nothing.
    let mut was_running = false;

    loop {
        wdt::feed();
        delay_ms(EXECUTOR_TICK_MS);
        seconds = seconds.wrapping_add(1);

        // ONE tick, decided and commanded atomically under both locks.
        //
        // `tick` reads the clock rather than counting ticks, so a late wake-up
        // (a starved task, a long serial burst, a slow TLS handshake on the
        // other core) costs nothing: the program still finishes on time. That
        // is the 59:18 fix from the Python, and it matters more here than it
        // did there.
        let now = ctx.clock.now();
        let mut interval_log = None;
        let mut ended_log = false;
        let mut p = lock(&ctx.program);
        let mut g = lock(&ctx.guarded);

        // OWNERSHIP LOSS IS A STICKY PROGRAM EVENT, not merely a failed belt
        // command. The remembered identity must still own the controller lease
        // before every tick. Console takeover sets the inhibit in the serial
        // task's existing guarded hold; other asynchronous lease losses are
        // detected here. In either case, set the interlock before touching the
        // program state so no later background command can reacquire.
        let executor_owns = control::owner(&g, Surface::Executor)
            .identity()
            .is_some_and(|id| g.controller.owner() == Some(id));
        let safety_blocked = p.running() && (g.executor_inhibited || !executor_owns);
        let newly_paused = if safety_blocked {
            g.executor_inhibited = true;
            let changed = !p.paused();
            let plan = p.pause_due_to_safety(now);
            debug_assert!(plan.is_empty());
            changed
        } else {
            let plan = p.tick(now);
            let running = p.running();
            let ended = was_running && !running;
            was_running = running;

            if !plan.is_empty() || ended {
                let (current, elapsed) = (p.current_interval(), p.total_elapsed());
                let n = apply_plan(&mut g, plan, ended, now);
                if !plan.is_empty() {
                    interval_log = Some((current, elapsed, n, plan.commands().len()));
                }
                ended_log = ended;
            }

            // WHAT THE PROGRAM WANTS RIGHT NOW, read from the program itself
            // rather than remembered from the last plan — because a program
            // can be started by HTTP, quick-start, or coach. A copy would be a
            // second fact that can disagree with the first.
            //
            // Paused is excluded: a paused program wants ZERO, which is what
            // the pause plan already commanded, and re-asserting the
            // interval's speed over it would un-pause the belt.
            if running && !p.paused() {
                if let Some((speed, incline)) = p.motion_of_current() {
                    // ASK AGAIN FOR A TRANSITION THAT DID NOT HAPPEN.
                    // `reassert` never mints a lease, and this guarded hold
                    // prevents console takeover from landing between the
                    // ownership check and the request.
                    control::reassert(&mut g, Surface::Executor, speed, incline, now);
                }
            }
            false
        };

        // The network notification is deliberately outside BOTH locks. The
        // queued work renders and writes on the HTTP task, so the executor
        // never blocks on a client and the non-network build never names net.
        drop(g);
        drop(p);
        if let Some((current, elapsed, accepted, commands)) = interval_log {
            logi!(
                "program: interval {} at {}s ({}/{} accepted)",
                current,
                elapsed,
                accepted,
                commands
            );
        }
        if ended_log {
            logi!("program: ended, belt released");
        }
        if newly_paused {
            #[cfg(feature = "net")]
            crate::net::ws::request_push();
        }

        if seconds % 5 == 0 {
            // Cold-path liveness heartbeat on the debug console (UART0, never
            // the treadmill bus). qemu_smoke.sh parses these to prove >=15 s
            // of panic-free guest uptime.
            logi!("heartbeat uptime={}s", seconds);
        }
    }
}
