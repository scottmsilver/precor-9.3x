//! Normative safety deadlines and clamps, in exact integer units.
//!
//! Port of `components/portable_core/safety/safety_constants.h`. Each constant
//! names its twin in `firmware/safety_model.py` and
//! `firmware/safety_manifest.schema.json`. Time is monotonic MICROSECONDS
//! (PLAN D4): the model's float-epsilon comparisons become exact integer ones.
//!
//! NEVER reintroduce floats here. The translation is only sound because every
//! timestamp in the corpus is an exact microsecond multiple.

use crate::units::{InclineHalfPct, Micros, SpeedTenths};

/// `console_fresh_seconds = 1.5` — `Controller.CONSOLE_FRESH_SECONDS`.
pub const CONSOLE_FRESH_US: Micros = Micros::new(1_500_000);

/// `transfer_gap_seconds = 1.0` — `Controller.TRANSFER_GAP_DEADLINE_SECONDS`.
pub const TRANSFER_GAP_DEADLINE_US: Micros = Micros::new(1_000_000);

/// `relay_feedback_seconds = 0.01` — `Controller.RELAY_FEEDBACK_DEADLINE_SECONDS`.
pub const RELAY_FEEDBACK_DEADLINE_US: Micros = Micros::new(10_000);

/// `relay_feedback_stable_seconds = 0.001` — `Controller.RELAY_FEEDBACK_STABLE_SECONDS`.
pub const RELAY_FEEDBACK_STABLE_US: Micros = Micros::new(1_000);

// --- Bench-gate constants -------------------------------------------------
//
// These five are ASSERTED BY THE TESTS AND USED NOWHERE in the controller —
// they are bench measurement gates, not runtime logic. Kept so vector 54
// (`model_constants_are_the_normative_deadlines`) ports 1:1. Do not invent
// uses for them.

/// `watchdog_seconds = 2.0`; also `CONFIG_ESP_TASK_WDT_TIMEOUT_S=2`.
#[allow(dead_code)]
pub const WDT_US: Micros = Micros::new(2_000_000);
/// `tread_ok_to_nc_max_seconds = 0.01` (bench gate).
#[allow(dead_code)]
pub const TREAD_OK_TO_NC_MAX_US: Micros = Micros::new(10_000);
/// `software_to_nc_max_seconds = 0.25` (bench gate).
#[allow(dead_code)]
pub const SOFTWARE_TO_NC_MAX_US: Micros = Micros::new(250_000);
/// `watchdog_to_nc_max_seconds = 2.25` (bench gate).
#[allow(dead_code)]
pub const WDT_TO_NC_MAX_US: Micros = Micros::new(2_250_000);
/// `normal_transition_acceptance_cycles = 1000` (bench gate).
#[allow(dead_code)]
pub const NORMAL_TRANSITION_ACCEPTANCE_CYCLES: i32 = 1_000;

// --- Motion clamps (PLAN "Clamps on-MCU") ---------------------------------

/// Speed 0..=120 tenths of mph (12.0 mph).
pub const SPEED_MAX_TENTHS: SpeedTenths = SpeedTenths::MAX;
/// Incline APPLICATION limit 0..=30 half-pct (15%). The remote box is no
/// longer a trust boundary, so this clamp is enforced on-MCU.
pub const INCLINE_APP_MAX_HALF: InclineHalfPct = InclineHalfPct::APP_MAX;
/// Incline ABSOLUTE hardware guard 0..=198 half-pct (99%). Guards the emulate
/// cycle encoder (via `ModeStateMachine`), never user commands.
#[allow(dead_code)]
pub const INCLINE_ABS_MAX_HALF: InclineHalfPct = InclineHalfPct::ABS_MAX;

/// Emulate 3-hour no-change timeout.
///
/// SINGLE SOURCE OF TRUTH (corrected 2026-07-28). This module is documented as
/// the normative home of the deadlines, but this value used to be an
/// INDEPENDENT literal while the runtime path (`EmulationCycle::tick`) read a
/// second, separately written literal in `cycle.rs` — so the documented
/// constant was not the one the firmware used, and editing it would have
/// changed nothing. It is now an alias of the runtime constant, which is where
/// the number is defined once; the `const _` assertions below make a
/// re-divergence a COMPILE ERROR rather than a review catch.
pub const EMULATE_TIMEOUT_US: Micros = crate::cycle::EMU_TIMEOUT_US;

// --- single-source-of-truth assertions (compile-time) ----------------------
//
// Every constant this module re-exports is an ALIAS. If anyone replaces an
// alias with a literal that drifts from the value the runtime actually
// applies, one of these fails to compile.
const _: () = assert!(EMULATE_TIMEOUT_US.get() == crate::cycle::EMU_TIMEOUT_US.get());
const _: () = assert!(EMULATE_TIMEOUT_US.get() == 3 * 3600 * 1_000_000);
const _: () = assert!(SPEED_MAX_TENTHS.get() == crate::mode::MAX_SPEED_TENTHS);
const _: () = assert!(INCLINE_ABS_MAX_HALF.get() == crate::mode::MAX_INCLINE);
const _: () = assert!(SPEED_MAX_TENTHS.get() == 120);
const _: () = assert!(INCLINE_APP_MAX_HALF.get() == 30);
const _: () = assert!(INCLINE_ABS_MAX_HALF.get() == 198);

/// Relay-feedback sampling cadence while in `ENTRY_WAIT_FEEDBACK` /
/// `EXIT_WAIT_FEEDBACK`.
///
/// The 10 ms deadline with a 1 ms continuous-stable requirement is
/// UNSATISFIABLE at the serial task's 5 ms cadence (first sample ~+5 ms, next
/// ~+10 ms — exactly the fail-closed deadline). 200 µs gives ~5 samples per
/// stable millisecond and qualification by ~+1.2 ms.
///
/// Changing this silently retunes S3/S7a in the QEMU harness.
pub const FEEDBACK_POLL_US: Micros = Micros::new(200);

/// Console inter-frame gap qualification threshold.
///
/// TODO(M2): 20 ms is an explicit PLACEHOLDER consistent with the 100 ms burst
/// gap, not a bench measurement — carried over verbatim because S1–S7 and the
/// harness's 0.15 s pacer are tuned around it.
pub const GAP_QUALIFY_US: Micros = Micros::new(20_000);
