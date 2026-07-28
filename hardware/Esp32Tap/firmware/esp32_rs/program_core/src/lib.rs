/*!
 * program_core — the interval executor, ported from `python/program_engine.py`
 * (`ProgramState`), whose `python/tests/test_program_engine.py` is the
 * specification this crate is written against.
 *
 * THE POINT OF THIS CRATE: a workout must survive the tablet walking away.
 * Once a program is loaded, nothing in here needs the network, the AI tier,
 * or a filesystem to run it to completion.
 *
 * Structural guarantees, the same three `safety_core` carries:
 *
 *  * `#![no_std]` outside `cfg(test)` and `alloc` is never named, so an
 *    allocation anywhere in the tick loop or the JSON path is a COMPILE ERROR.
 *    A program is a fixed-size value; loading one cannot grow the heap. The
 *    C++ server tier died of exactly the opposite (a per-store rapidjson pool
 *    that only ever grew), and a heap exhaustion here is a belt AVAILABILITY
 *    event: `panic = "abort"` reboots the device, which drops the relay and
 *    interrupts a run.
 *  * `#![forbid(unsafe_code)]`.
 *  * Speed and incline are `safety_core`'s newtypes, so the value the executor
 *    hands `SafetyController::command_motion` cannot be in the wrong unit.
 *
 * NO SIDE EFFECTS. Every mutating method returns a [`Plan`] — the motion the
 * caller must command — instead of invoking a callback. That is what keeps the
 * hard requirement enforceable: the executor has no way to reach the belt
 * except by feeding a `Plan` through the SAME `SafetyController` path an HTTP
 * request uses, and this crate cannot touch hardware even by mistake because
 * it cannot name it.
 *
 * ## Deliberate divergences from `ProgramState`, all stated
 *
 * 1. **Bounded storage.** [`MAX_INTERVALS`] intervals, [`MAX_NAME`]-byte
 *    interval names, [`MAX_PROGRAM_NAME`]-byte program names, and durations
 *    capped at [`MAX_DURATION_S`]. Python has no bound on any of these; a
 *    client-supplied program can grow its heap without limit. A `Program` here
 *    is a fixed-size value with no indirection, and an oversized submission is
 *    REJECTED rather than truncated (truncating would silently run a different
 *    workout than the one the user built).
 * 2. **No encouragement engine.** `_check_encouragement` — milestones, the
 *    every-3-intervals random message, and the `<<30>>s til the end` countdown
 *    — is not ported. It is a coaching-tier concern with no consumer on this
 *    device (there is no Gemini here and no TTS), it needs a random source in
 *    the tick loop, and its output is advisory text that cannot affect the
 *    belt. Porting it would be work with no observable effect; leaving it out
 *    is stated here rather than discovered later.
 * 3. **No `split_for_manual` and no `add_intervals`.** Both mutate a running
 *    program from the AI/manual-course tier, which does not exist here.
 *    `adjust_duration` IS ported because the app's manual-workout screen calls
 *    it directly.
 * 4. **No `_task`.** Python cancels an asyncio task; the tick is a real
 *    1 s FreeRTOS task that runs whether or not a program is loaded, so
 *    "cancel" is just `running = false`.
 * 5. **`round(x, 1)` becomes exact half-up on decimal hundredths.** Python
 *    rounds a binary float, so `round(2.55, 1)` is 2.5 and `round(2.65, 1)` is
 *    2.7 depending on representation. Parsing straight to integer hundredths
 *    and rounding half-up is total, reproducible, and cannot differ between
 *    builds.
 * 6. **Time is `Micros`, not `float` seconds.** Same wall-clock algorithm
 *    (`total_elapsed = int(now - loop_start - pause_accumulated)`), integer
 *    arithmetic. Truncation toward zero matches Python's `int()`.
 *
 * ## What this crate does NOT decide
 *
 * It clamps a stored program to the application range so that what the app
 * DISPLAYS is what will be commanded. It is not the safety authority:
 * `SafetyController` clamps again, holds the lease, and owns the entry/exit
 * choreography. If the two ever disagree, the controller wins — by
 * construction, because it is downstream.
 */

#![cfg_attr(not(test), no_std)]
#![forbid(unsafe_code)]

pub mod json;
pub mod model;
pub mod state;

pub use model::{
    Interval, Program, MAX_DURATION_S, MAX_INTERVALS, MAX_NAME, MAX_PROGRAM_NAME, MIN_DURATION_S,
};
pub use state::{Plan, ProgramState};

/// The largest request body a program submission may occupy, in bytes.
///
/// This is not a wish: it is the size of ONE `reqbudget` slot, and the
/// firmware asserts the equality at compile time
/// (`net/program.rs`). [`model::max_program_json_bytes`] proves that the
/// worst-case program — every interval present, every name at maximum length,
/// every number at its widest — serialises inside it, so the bound and the
/// storage limits cannot drift apart silently.
pub const MAX_PROGRAM_JSON_BYTES: usize = 2048;
