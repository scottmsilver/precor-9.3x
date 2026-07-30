//! THE ONE PATH TO THE BELT.
//!
//! Two surfaces can command motion — an HTTP request and the interval
//! executor — and there is exactly one function that lets either of them do
//! it: [`command`]. Same clamps (the controller's), same lease, same
//! entry choreography, same `apply_outputs()`. Neither surface can reach
//! `SafetyController::command_motion` without coming through here, because
//! neither surface holds an identity of its own: the identities live in
//! `Guarded` and are minted only by this module.
//!
//! WHY IT IS NOT IN `net/api.rs`. The executor runs in every build; the
//! network tier is behind `feature = "net"`. If this logic lived in the HTTP
//! handler, a non-net build would need a second copy of it, and a second copy
//! is a second opinion about how the belt is commanded.
//!
//! WHY IT IS NOT IN `SafetyController`. The controller is what the
//! differential compares op-for-op against the C++ core and `safety_model.py`.
//! Lease bookkeeping and the auto-emulate policy have no counterpart there, so
//! putting them in would fork the compared behaviour — the same reasoning the
//! auto-emulate comment in `net/api.rs` already gives.
//!
//! # The lease, and why the identity is REUSED
//!
//! `SafetyController::connect` emergency-stops when a NEW generation arrives
//! for a connection that already owns the lease (`owner_superseded`): speed
//! zero, relay off, TX off, mode PROXY. That is correct for a genuinely new
//! socket replacing an old one — and catastrophic if a surface mints a fresh
//! generation per command, because then every command tears the relay down and
//! builds it back up.
//!
//! That is precisely what the first version of the HTTP handler did. It went
//! unnoticed because the only scenario covering it issues ONE request; the
//! second request would have relay-cycled the treadmill mid-stride. The
//! executor makes it unmissable — it commands on every interval boundary.
//!
//! So an identity is minted ONCE per session and reused while it still owns
//! the lease. A new generation is minted only when ownership has actually been
//! lost (an emergency stop, a console takeover, the other surface taking
//! over), which is exactly when a new connection is the honest description.

// COMPILER-ENFORCED unsafe containment: this module decides what the belt is
// told, so it may not contain a single unsafe token.
#![forbid(unsafe_code)]

use crate::context::Guarded;
use crate::logi;
use safety_core::hal::SerialOut;
use safety_core::safety::controller::{ConnectionIdentity, SafeMode, Transport};
use safety_core::units::{InclineHalfPct, Micros, SpeedTenths};

/// Identifies the HTTP tier as an owner.
pub const HTTP_HANDLE: i32 = 0x48_54_54_50; // "HTTP"

/// Identifies the interval executor as an owner.
///
/// NOT 1: the QEMU shim's scripted owner is `EXECUTOR:1` and scenarios S3/S5/S7
/// match `lease_acquired:EXECUTOR:1:` by prefix. A distinct handle keeps
/// `ConnectionIdentity::same_connection` false between the two, so the shim and
/// a running program can never be mistaken for each other in the audit ring.
pub const EXECUTOR_HANDLE: i32 = 2;

/// Which surface is asking. Also selects its owner state — passing the state
/// itself would need a second mutable borrow of `Guarded`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Surface {
    Http,
    Executor,
}

/// Whether this command is allowed to acknowledge a latched fault.
///
/// The distinction is explicit at the application boundary so a background
/// retry can never accidentally become a fault reset merely because it carries
/// a positive speed.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum EntryIntent {
    /// Ordinary motion, incline, zero/Stop, executor tick, or reassertion.
    Ordinary,
    /// A fresh user Start/Resume or positive manual target.
    ExplicitRecovery,
}

impl Surface {
    const fn base(self) -> (Transport, i32) {
        match self {
            Surface::Http => (Transport::Wss, HTTP_HANDLE),
            Surface::Executor => (Transport::Executor, EXECUTOR_HANDLE),
        }
    }
}

/// One surface's connection bookkeeping. Lives inside `Guarded`, so it is
/// unreachable without the safety lock — the same construction that puts
/// `SafetyIoImpl` in there.
#[derive(Clone, Copy, Debug)]
pub struct Owner {
    /// Monotonic, never reused. `i64` to match `Generation`.
    generation: i64,
    /// The identity currently in play, if any.
    current: Option<ConnectionIdentity>,
}

impl Owner {
    pub const fn new() -> Self {
        Owner {
            generation: 0,
            current: None,
        }
    }
    /// The identity this surface last minted, whether or not it still owns
    /// the lease. Read-only; used by the executor to ask the controller
    /// questions about its own session.
    pub fn identity(&self) -> Option<ConnectionIdentity> {
        self.current
    }
}

impl Default for Owner {
    fn default() -> Self {
        Self::new()
    }
}

/// Why a command could not be issued. Maps onto an HTTP status so a handler
/// cannot invent a fourth outcome.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reject {
    /// A safety ownership loss suspended the background executor. Only a
    /// future explicit Start/Resume transaction may clear this interlock.
    ExecutorInhibited,
    /// The other surface holds the lease. While a program runs, the executor
    /// owns the belt and a manual command is refused rather than fighting it.
    NotOwner,
    /// The controller refused the motion: out of clamp, or a latched fault.
    /// It has already recorded WHY in the audit ring.
    Refused,
    /// `i64` generations exhausted. Unreachable (2^63 commands) and still
    /// handled, because unreachable is not the same as cannot-panic under
    /// `panic = "abort"`.
    GenerationExhausted,
}

fn owner_mut(g: &mut Guarded, surface: Surface) -> &mut Owner {
    match surface {
        Surface::Http => &mut g.http_owner,
        Surface::Executor => &mut g.executor_owner,
    }
}

pub fn owner(g: &Guarded, surface: Surface) -> &Owner {
    match surface {
        Surface::Http => &g.http_owner,
        Surface::Executor => &g.executor_owner,
    }
}

/// Return an identity that owns the lease right now, taking it if necessary.
fn hold_lease(
    g: &mut Guarded,
    surface: Surface,
    now: Micros,
    allow_inhibited_executor: bool,
) -> Result<ConnectionIdentity, Reject> {
    if surface == Surface::Executor && g.executor_inhibited && !allow_inhibited_executor {
        return Err(Reject::ExecutorInhibited);
    }

    // Still ours? Reuse it. This is the branch that keeps a running program
    // from relay-cycling the treadmill once a second — see the module header.
    if let Some(id) = owner(g, surface).identity() {
        if g.controller.owner() == Some(id) {
            return Ok(id);
        }
    }

    let (transport, handle) = surface.base();
    let next = owner(g, surface)
        .generation
        .checked_add(1)
        .ok_or(Reject::GenerationExhausted)?;
    let id = ConnectionIdentity::new(transport, handle, next).ok_or(Reject::GenerationExhausted)?;
    {
        let o = owner_mut(g, surface);
        o.generation = next;
        o.current = Some(id);
    }
    if !g.controller.connect(&id) || !g.controller.acquire(&id, now) {
        // The controller recorded the reason. Most commonly: the OTHER surface
        // holds the lease (`lease_rejected:already_owned`).
        return Err(Reject::NotOwner);
    }
    Ok(id)
}

/// Command speed and incline as `surface`, through the full safety path.
///
/// Returns `Ok(())` only when the controller ACCEPTED the motion. The caller
/// never pre-validates: deciding whether a motion is safe is the controller's
/// job, and a caller that helpfully checked first would be a second opinion.
pub fn command(
    g: &mut Guarded,
    surface: Surface,
    intent: EntryIntent,
    speed: SpeedTenths,
    incline: InclineHalfPct,
    now: Micros,
) -> Result<(), Reject> {
    let id = hold_lease(g, surface, now, false)?;
    command_as(g, surface, intent, &id, speed, incline, now)
}

/// Start/Resume-only executor command.
///
/// The sticky inhibit is deliberately *tested through*, not cleared first.
/// The surrounding transaction clears it only after every plan command has
/// been accepted. Keeping this bypass private to an explicit-recovery command
/// prevents an ordinary executor tick from reopening the acquisition window.
pub fn command_program_entry(
    g: &mut Guarded,
    speed: SpeedTenths,
    incline: InclineHalfPct,
    now: Micros,
) -> Result<(), Reject> {
    let id = hold_lease(g, Surface::Executor, now, true)?;
    command_as(
        g,
        Surface::Executor,
        EntryIntent::ExplicitRecovery,
        &id,
        speed,
        incline,
        now,
    )
}

/// Remove any executor ownership created by a failed Start/Resume attempt.
///
/// This touches no other surface: a failed program acquisition must not leave
/// a hidden owner, but neither may it disconnect an unrelated manual owner.
pub fn rollback_program_entry(g: &mut Guarded, now: Micros) {
    let Some(id) = owner(g, Surface::Executor).identity() else {
        return;
    };
    let owns = g.controller.owner() == Some(id);
    if owns && g.controller.mode() == SafeMode::Emulating {
        let _ = command_as(
            g,
            Surface::Executor,
            EntryIntent::Ordinary,
            &id,
            SpeedTenths::ZERO,
            InclineHalfPct::ZERO,
            now,
        );
    }
    // `disconnect` removes the active identity even when it is not the lease
    // owner. That non-owner case is the ownership-conflict hole: connect
    // succeeded, acquire failed, and the attempted identity otherwise stayed
    // active forever. If it DOES own (including a partially entered transfer),
    // disconnect is the immediate fail-safe release rather than a delayed
    // normal exit.
    let _ = g.controller.disconnect(&id, now);
    owner_mut(g, Surface::Executor).current = None;
    g.apply_outputs();
}

#[cfg(feature = "qemu-test")]
pub struct ProgramOwnerDebug {
    pub generation: i64,
    pub current: bool,
    pub active: bool,
    pub owns: bool,
}

/// QEMU-only observation of transaction cleanup; never part of production.
#[cfg(feature = "qemu-test")]
pub fn program_owner_debug(g: &Guarded) -> ProgramOwnerDebug {
    let owner = owner(g, Surface::Executor);
    let id = owner.identity();
    ProgramOwnerDebug {
        generation: owner.generation,
        current: id.is_some(),
        active: id.is_some_and(|id| g.controller.is_connected(&id)),
        owns: id.is_some_and(|id| g.controller.owner() == Some(id)),
    }
}

/// The motion + auto-emulate choreography for an identity that ALREADY owns
/// the lease.
///
/// Split out of [`command`] so [`reassert`] can reuse it without going through
/// `hold_lease` — the difference between the two callers is exactly whether a
/// new generation may be minted, and everything after that must stay one copy.
fn command_as(
    g: &mut Guarded,
    surface: Surface,
    intent: EntryIntent,
    id: &ConnectionIdentity,
    speed: SpeedTenths,
    incline: InclineHalfPct,
    now: Micros,
) -> Result<(), Reject> {
    let id = *id;
    if !g.controller.command_motion(&id, speed, incline, now) {
        g.apply_outputs();
        return Err(Reject::Refused);
    }

    // AUTO-EMULATE, mirroring the Pi. CLAUDE.md: "Speed/incline command
    // received -> auto-enables emulate mode", and that logic lives below the
    // application tier precisely so a mode transition does not depend on the
    // application tier being alive.
    //
    // It is an ATTEMPT, never a demand: `request_emulate` enforces every
    // precondition itself (TREAD_OK, BYPASS feedback, a fresh console, a
    // qualified gap, idle-low TX, no latched fault). If any fails it refuses,
    // records why, and we stay in Proxy — the safe state.
    if g.controller.mode() == SafeMode::Proxy {
        let idle_low = g.console_uart.tx_idle_low();
        let entered = match intent {
            EntryIntent::Ordinary => g.controller.request_emulate(&id, now, idle_low),
            EntryIntent::ExplicitRecovery => {
                g.controller.request_emulate_recovering(&id, now, idle_low)
            }
        };
        if entered {
            // RE-ASSERT THE INTENT. `request_emulate` sets commanded motion to
            // ZERO — PLAN "enter at zero", correct and non-negotiable — which
            // discards the motion accepted three lines above. Nothing else
            // re-sends it, so before this line a single `POST /api/speed 3.0`
            // from PROXY entered emulate and then left the belt at ZERO: the
            // user's command was silently swallowed and only a SECOND request
            // moved the belt.
            //
            // The interval executor made it undeniable — a program whose first
            // interval is ten minutes long would have stood still for ten
            // minutes — but the defect was already there on the HTTP path.
            // `test_http_entry.py` did not catch it because it asserts only
            // that the first frames are zeros, which is true either way.
            //
            // PLAN STEP 6 IS NOT WEAKENED BY THIS. The zero-frame guarantee is
            // enforced by the emulate CYCLE's entry gate, which arms on the
            // rising edge of a new `EmulateSessionId` and refuses to transmit
            // anything but a complete zero frame first — not by whatever
            // happens to be in `speed_tenths`. The scenario asserts the first
            // `[hmph:...]` on the wire after the transfer is `[hmph:0]` with a
            // nonzero speed commanded, which is exactly this case.
            let _ = g.controller.command_motion(&id, speed, incline, now);
            logi!("control: {} auto-entered emulate", surface_name(surface));
        } else {
            // `command_motion` accepted an application value, but Proxy means
            // it cannot reach the motor. Keep the advertised command as
            // truthful as the result: roll it back through the same owner and
            // report the rejected entry to the caller.
            let _ = g
                .controller
                .command_motion(&id, SpeedTenths::ZERO, InclineHalfPct::ZERO, now);
            g.apply_outputs();
            return Err(Reject::Refused);
        }
    }

    g.apply_outputs();
    Ok(())
}

/// Give the belt back: PLAN normal exit (zero frame, gap, relay off, TX off,
/// lease released) when emulating, a plain lease drop when not.
///
/// Called when a program ends — completed, stopped or reset. Without it the
/// executor's `NoDeadline` lease would be held forever and no HTTP request
/// could ever command the belt again.
///
/// A no-op unless this surface actually owns the lease right now, so calling
/// it twice, or after a fail-closed stop already took the belt away, is safe.
///
/// THE IDENTITY IS DELIBERATELY NOT FORGOTTEN. `hold_lease`'s reuse check
/// (`controller.owner() == Some(id)`) is the single source of truth about
/// whether this surface still owns the belt, and it is correct in both
/// outcomes: if the exit completed the lease is gone and the next command
/// mints a fresh generation against a free lease; if the exit is still in
/// flight the lease is still ours and the next command reuses the identity
/// instead of superseding it — which would emergency-stop mid-exit.
pub fn release(g: &mut Guarded, surface: Surface, now: Micros) -> bool {
    let Some(id) = owner(g, surface).identity() else {
        return false;
    };
    if g.controller.owner() != Some(id) {
        return false;
    }
    let released = match g.controller.mode() {
        // `request_normal_exit` refuses when not Emulating, and there is no
        // other non-emergency way to drop a lease. In Proxy the "emergency" is
        // nominal — the relay is already open and TX already silent, so this
        // only zeroes commanded motion and releases ownership. The audit line
        // reads `emergency:owner_disconnect`, which is the honest description
        // of what happened: the owner went away.
        SafeMode::Proxy => g.controller.disconnect(&id, now),
        _ => g.controller.request_normal_exit(&id, now),
    };
    g.apply_outputs();
    released
}

/// Ask AGAIN for a mode transition that did not happen — without taking the
/// belt from anyone.
///
/// # The silence this exists to remove
///
/// [`command`] ATTEMPTS auto-emulate, and `request_emulate` enforces six
/// preconditions of its own; a console frame older than 1.5 s fails one of
/// them, and a 1.5 s gap in a stimulus is not exotic. The interval executor
/// commands only at interval BOUNDARIES, so a single refusal used to leave the
/// belt motionless for the whole interval — up to `MAX_DURATION_S` — while
/// `GET /api/program` reported `running: true` and nothing anywhere said why.
///
/// MEASURED, not argued. Under load a start produced exactly:
/// `connected:EXECUTOR:2:1` -> `lease_acquired:EXECUTOR:2:1` -> `owner_motion`
/// -> `entry_rejected:console_not_fresh`, and then 25 s of nothing with ZERO
/// bytes on the motor UART. That is the worst failure mode this firmware has:
/// a device that looks healthy from every surface and does not move.
///
/// # Why this is not the console-takeover defect
///
/// It refuses unless `surface` STILL OWNS the lease, and it never calls
/// [`hold_lease`]. A console takeover emergency-stops, which DROPS the lease —
/// so the moment the human takes the belt this returns false and stops asking.
/// Re-acquiring would be a running program reclaiming the belt from the person
/// standing on it, which is the one thing it must never do.
///
/// Returns whether the caller should keep asking.
pub fn reassert(
    g: &mut Guarded,
    surface: Surface,
    speed: SpeedTenths,
    incline: InclineHalfPct,
    now: Micros,
) -> bool {
    let Some(id) = owner(g, surface).identity() else {
        return false;
    };
    if g.controller.owner() != Some(id) {
        return false;
    }
    // Emulating, or a transfer already in flight: the transition either
    // happened or is happening, and there is nothing to ask for.
    if g.controller.mode() != SafeMode::Proxy {
        return false;
    }
    command_as(g, surface, EntryIntent::Ordinary, &id, speed, incline, now).is_ok()
}

/// STOP MEANS STOP — for whichever surface actually holds the belt.
///
/// `ProgramState::stop()` describes what the PROGRAM wants, and when no program
/// is running that description is EMPTY. An empty plan through `apply_plan`
/// releases the EXECUTOR's lease and touches nothing else, so a belt commanded
/// manually — `POST /api/speed`, a BLE Control Point write, the coach's
/// `stop_treadmill` — kept its speed and its relay through a Stop the user was
/// told had succeeded. `python/server.py::_apply_stop` has always done
/// `_hw_set_speed(0)` unconditionally; this is that half, and it is HERE rather
/// than in a handler because the HTTP endpoint and the coach must not be able
/// to mean two different things by "stop".
///
/// Zeroing goes through [`command`] like everything else — same clamps, same
/// lease, same `apply_outputs` — and then the lease is handed back so the next
/// owner does not have to wait for a deadman.
///
/// Returns whether the belt is now commanded to zero, so a caller can tell the
/// user what actually happened instead of what was asked for.
pub fn stop_belt(g: &mut Guarded, now: Micros) -> bool {
    // Only a surface that OWNS the belt right now is touched. Commanding as a
    // surface that does not own it would mint a fresh generation against a
    // lease somebody else holds — churn, an audit line, and no effect.
    for surface in [Surface::Executor, Surface::Http] {
        let Some(id) = owner(g, surface).identity() else {
            continue;
        };
        if g.controller.owner() != Some(id) {
            continue;
        }
        // ZEROING ONLY WHILE EMULATING, and that guard is load-bearing rather
        // than an optimisation. `command` ATTEMPTS auto-emulate whenever the
        // controller is in Proxy, so zeroing unconditionally would let a STOP
        // START A RELAY TRANSFER — for a belt this device is not even driving,
        // since in Proxy the motor is the console's. `release` below already
        // zeroes commanded motion on that path (`disconnect`), so the
        // post-condition holds either way.
        if g.controller.mode() == SafeMode::Emulating {
            let inc = g.controller.incline_half_percent();
            let _ = command_as(
                g,
                surface,
                EntryIntent::Ordinary,
                &id,
                SpeedTenths::ZERO,
                inc,
                now,
            );
        }
        release(g, surface, now);
    }
    g.controller.speed_tenths() == SpeedTenths::ZERO
}

const fn surface_name(s: Surface) -> &'static str {
    match s {
        Surface::Http => "http",
        Surface::Executor => "executor",
    }
}
