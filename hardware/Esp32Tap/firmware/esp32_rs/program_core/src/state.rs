//! `ProgramState` — the 1 s tick loop, interval advance, pause/resume, skip,
//! prev, extend, adjust-duration and completion.
//!
//! THE WALL-CLOCK ALGORITHM IS LOAD-BEARING AND IS COPIED EXACTLY. Python's
//! `_tick_loop` does NOT count ticks; it recomputes elapsed time from a
//! monotonic clock on every tick:
//!
//! ```text
//! real_elapsed   = clock() - loop_start - pause_accumulated
//! total_elapsed  = int(real_elapsed)
//! interval_elapsed = total_elapsed - interval_start_elapsed
//! ```
//!
//! That is the fix for the "59:18" bug (`TestWallClockTiming`): a 1 s sleep
//! that actually takes 1.007 s makes a tick-counting engine finish a 60-minute
//! program 25 seconds late. On this device the same risk is larger, not
//! smaller — `vTaskDelay(1000)` on a loaded core-0 overshoots, and a
//! WDT-supervised task may be preempted by the 5 ms serial loop for a long
//! time. `Micros` comes from `esp_timer`, which does not drift with task
//! scheduling.
//!
//! ONE STRUCTURAL IMPROVEMENT over the Python. `_pause_start` is a `float`
//! with `0.0` as the "not paused" sentinel, and every reader has to remember
//! the `> 0` guard (`_effective_pause` does; a future reader might not). Here
//! it is an `Option<Micros>`, so "paused but no pause start" is
//! unrepresentable rather than merely unlikely.

use crate::model::{Interval, Program};
use safety_core::units::{InclineHalfPct, Micros, SpeedTenths};

/// The motion the caller must command, in order, through `SafetyController`.
///
/// AT MOST TWO, and that is not an arbitrary cap: the only operation that
/// produces two is `start()` over an already-running program, which is
/// Python's `await self.stop()` (→ `on_change(0, 0)`) followed by the new
/// program's first interval. Everything else produces zero or one. A fixed
/// array makes the "no allocation in the tick loop" guarantee structural.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Plan {
    n: usize,
    cmds: [(SpeedTenths, InclineHalfPct); 2],
}

impl Plan {
    pub const fn none() -> Self {
        Plan {
            n: 0,
            cmds: [(SpeedTenths::ZERO, InclineHalfPct::ZERO); 2],
        }
    }
    pub const fn one(speed: SpeedTenths, incline: InclineHalfPct) -> Self {
        Plan {
            n: 1,
            cmds: [(speed, incline), (SpeedTenths::ZERO, InclineHalfPct::ZERO)],
        }
    }
    /// Stop-then-start. The zero is Python's `stop()` firing `on_change(0, 0)`.
    pub const fn zero_then(speed: SpeedTenths, incline: InclineHalfPct) -> Self {
        Plan {
            n: 2,
            cmds: [(SpeedTenths::ZERO, InclineHalfPct::ZERO), (speed, incline)],
        }
    }
    pub const fn zero() -> Self {
        Plan::one(SpeedTenths::ZERO, InclineHalfPct::ZERO)
    }
    pub fn is_empty(&self) -> bool {
        self.n == 0
    }
    pub fn commands(&self) -> &[(SpeedTenths, InclineHalfPct)] {
        &self.cmds[..self.n]
    }
}

/// Port of `program_engine.ProgramState`.
#[derive(Clone, Copy, Debug)]
pub struct ProgramState {
    program: Option<Program>,
    running: bool,
    paused: bool,
    completed: bool,
    current_interval: usize,
    interval_elapsed: i64,
    total_elapsed: i64,
    // --- wall-clock timing fields, 1:1 with the Python ---
    loop_start: Micros,
    pause_accumulated: Micros,
    /// `Option`, not Python's `0.0` sentinel — see the module header.
    pause_start: Option<Micros>,
    interval_start_elapsed: i64,
}

impl Default for ProgramState {
    fn default() -> Self {
        Self::new()
    }
}

const SEC: i64 = 1_000_000;

impl ProgramState {
    pub const fn new() -> Self {
        ProgramState {
            program: None,
            running: false,
            paused: false,
            completed: false,
            current_interval: 0,
            interval_elapsed: 0,
            total_elapsed: 0,
            loop_start: Micros::ZERO,
            pause_accumulated: Micros::ZERO,
            pause_start: None,
            interval_start_elapsed: 0,
        }
    }

    // --- read-only surface ------------------------------------------------

    pub fn program(&self) -> Option<&Program> {
        self.program.as_ref()
    }
    pub fn running(&self) -> bool {
        self.running
    }
    pub fn paused(&self) -> bool {
        self.paused
    }
    pub fn completed(&self) -> bool {
        self.completed
    }
    pub fn current_interval(&self) -> usize {
        self.current_interval
    }
    pub fn interval_elapsed(&self) -> i64 {
        self.interval_elapsed
    }
    pub fn total_elapsed(&self) -> i64 {
        self.total_elapsed
    }
    pub fn total_duration(&self) -> i64 {
        self.program.as_ref().map_or(0, Program::total_duration_s)
    }
    /// `ProgramState.is_manual`.
    pub fn is_manual(&self) -> bool {
        self.program.as_ref().is_some_and(|p| p.manual)
    }

    /// `ProgramState.current_iv` — `None` past the end.
    pub fn current_iv(&self) -> Option<&Interval> {
        self.program.as_ref()?.get(self.current_interval)
    }

    fn cumulative_at(&self, idx: usize) -> i64 {
        self.program.as_ref().map_or(0, |p| p.cumulative_at(idx))
    }

    /// The motion the CURRENT interval wants, in the units the belt path takes.
    ///
    /// PUBLIC because the interval executor needs to be able to re-assert it:
    /// `control::command` only ATTEMPTS the emulate transition and
    /// `request_emulate` can decline it (a console frame older than 1.5 s is
    /// enough), and a plan is produced only at interval BOUNDARIES — so without
    /// a way to ask "what does the program want right now?" one declined
    /// transition cost the whole interval. This is the same pair every `Plan`
    /// this state machine emits is built from, so the executor re-asserts the
    /// program's own intent rather than a remembered copy of it.
    pub fn motion_of_current(&self) -> Option<(SpeedTenths, InclineHalfPct)> {
        self.current_iv().map(|iv| (iv.speed, iv.incline))
    }

    // --- lifecycle --------------------------------------------------------

    /// `ProgramState.load` — replaces the program and resets every counter.
    ///
    /// Note it does NOT zero the belt: Python's `load` calls `_cancel_task`,
    /// not `stop`. A load while a program runs leaves the belt where it is
    /// until `start` (whose first act is `stop`) or an explicit `stop`.
    pub fn load(&mut self, program: Program) {
        *self = ProgramState::new();
        self.program = Some(program);
    }

    /// `ProgramState.start`. `resume_interval`/`resume_elapsed` default to 0.
    ///
    /// Returns the zero-then-first-interval plan when a program was already
    /// running (Python's internal `await self.stop()`), otherwise just the
    /// first interval.
    pub fn start(&mut self, now: Micros, resume_interval: usize, resume_elapsed: i64) -> Plan {
        let was_running = self.running;
        let Some(program) = self.program.as_ref() else {
            // `stop()` still ran inside Python's `start()` before the
            // `if not self.program: return`, so a running-but-cleared state
            // still zeroes the belt.
            self.running = false;
            self.paused = false;
            return if was_running { Plan::zero() } else { Plan::none() };
        };

        let resume_interval = resume_interval.min(program.len());
        let resume_elapsed = resume_elapsed.max(0);

        self.running = true;
        self.paused = false;
        self.completed = false;
        self.current_interval = resume_interval;
        self.total_elapsed = resume_elapsed;
        self.interval_elapsed = resume_elapsed - self.cumulative_at(resume_interval);
        self.loop_start = Micros::new(now.get().saturating_sub(resume_elapsed * SEC));
        self.pause_accumulated = Micros::ZERO;
        self.pause_start = None;
        self.interval_start_elapsed = self.cumulative_at(resume_interval);

        match self.motion_of_current() {
            Some((s, i)) if was_running => Plan::zero_then(s, i),
            Some((s, i)) => Plan::one(s, i),
            // Resuming past the end: the belt still has to be zeroed if it was
            // moving. Python emits nothing here, because `stop()` already did.
            None if was_running => Plan::zero(),
            None => Plan::none(),
        }
    }

    /// `ProgramState.stop` — zeroes the belt IF it was running.
    pub fn stop(&mut self) -> Plan {
        let was_running = self.running;
        self.running = false;
        self.paused = false;
        if was_running {
            Plan::zero()
        } else {
            Plan::none()
        }
    }

    /// `ProgramState.reset` — stop, clear the program, clear `completed`.
    pub fn reset(&mut self) -> Plan {
        let was_running = self.running;
        *self = ProgramState::new();
        if was_running {
            Plan::zero()
        } else {
            Plan::none()
        }
    }

    /// `ProgramState._finish` — the program ran out of intervals.
    fn finish(&mut self) -> Plan {
        self.running = false;
        self.paused = false;
        self.completed = true;
        Plan::zero()
    }

    // --- transport controls ----------------------------------------------

    /// `ProgramState.toggle_pause`, PLUS `server.py::_apply_pause_toggle`.
    ///
    /// DELIBERATE MERGE, and the only place two Python files become one here.
    /// `ProgramState.toggle_pause` emits no motion on the way IN, because on
    /// the Pi the belt is zeroed a layer up:
    ///
    /// ```python
    /// if sess.prog.paused:
    ///     state["_paused_speed"] = state["emu_speed"]
    ///     state["emu_speed"] = 0
    ///     await _hw_set_speed(0)          # incline is NOT touched
    /// ```
    ///
    /// There is no layer up on this device. Splitting "pause" across two
    /// components so that one of them can forget to stop the belt is exactly
    /// the failure this port should not inherit, so pausing returns
    /// `(0, current incline)` — speed to zero, incline held, matching the Pi
    /// byte for byte in effect.
    pub fn toggle_pause(&mut self, now: Micros) -> Plan {
        if !self.paused {
            self.paused = true;
            self.pause_start = Some(now);
            // Incline is deliberately preserved: `_apply_pause_toggle` zeroes
            // speed only, so a hill you paused on is still there when you
            // step back on.
            match self.current_iv() {
                Some(iv) if self.running => Plan::one(SpeedTenths::ZERO, iv.incline),
                _ => Plan::none(),
            }
        } else {
            self.paused = false;
            if let Some(started) = self.pause_start.take() {
                self.pause_accumulated = self.pause_accumulated + (now - started);
            }
            if self.running {
                if let Some((s, i)) = self.motion_of_current() {
                    return Plan::one(s, i);
                }
            }
            Plan::none()
        }
    }

    /// `ProgramState._effective_pause` — accumulated plus any in-progress
    /// pause. Getting this wrong is the regression `TestSkipWhilePaused`
    /// pins: skipping during a pause without it makes the timer jump on
    /// resume.
    fn effective_pause(&self, now: Micros) -> Micros {
        match self.pause_start {
            Some(started) if self.paused => self.pause_accumulated + (now - started),
            _ => self.pause_accumulated,
        }
    }

    /// Re-anchor the clock so that `total_elapsed` reads exactly `target`.
    fn seek(&mut self, now: Micros, target: i64) {
        self.loop_start = Micros::new(
            now.get()
                .saturating_sub(self.effective_pause(now).get())
                .saturating_sub(target * SEC),
        );
        self.interval_start_elapsed = target;
        self.total_elapsed = target;
        self.interval_elapsed = 0;
    }

    /// `ProgramState.skip`.
    pub fn skip(&mut self, now: Micros) -> Plan {
        if !self.running {
            return Plan::none();
        }
        self.current_interval += 1;
        match self.motion_of_current() {
            Some((s, i)) => {
                let target = self.cumulative_at(self.current_interval);
                self.seek(now, target);
                Plan::one(s, i)
            }
            // Skipping the last interval FINISHES the program. Python leaves
            // `current_interval == len` here; so do we.
            None => self.finish(),
        }
    }

    /// `ProgramState.prev`. At the first interval it RESTARTS it rather than
    /// doing nothing — `test_prev_at_first_stays` pins that.
    pub fn prev(&mut self, now: Micros) -> Plan {
        if !self.running {
            return Plan::none();
        }
        if self.current_interval > 0 {
            self.current_interval -= 1;
        }
        let target = self.cumulative_at(self.current_interval);
        self.seek(now, target);
        match self.motion_of_current() {
            Some((s, i)) => Plan::one(s, i),
            None => Plan::none(),
        }
    }

    /// `ProgramState.extend_current` — add or subtract seconds from the
    /// CURRENT interval. Returns `false` when there is nothing to extend.
    ///
    /// No `Plan`: changing a duration does not change the commanded motion.
    pub fn extend_current(&mut self, seconds: i64) -> bool {
        if !self.running {
            return false;
        }
        let idx = self.current_interval;
        let Some(program) = self.program.as_mut() else {
            return false;
        };
        let Some(iv) = program.get_mut(idx) else {
            return false;
        };
        let new_dur = (iv.duration_s() as i64).saturating_add(seconds);
        iv.set_duration_s(new_dur);
        true
    }

    /// `ProgramState.adjust_duration` — add or remove time from a MANUAL
    /// program's LAST interval. Manual-only, exactly as in Python.
    pub fn adjust_duration(&mut self, delta_seconds: i64) -> bool {
        if !self.running || !self.is_manual() {
            return false;
        }
        let Some(program) = self.program.as_mut() else {
            return false;
        };
        let Some(last) = program.last_mut() else {
            return false;
        };
        let new_dur = (last.duration_s() as i64).saturating_add(delta_seconds);
        last.set_duration_s(new_dur);
        true
    }

    // --- the tick ---------------------------------------------------------

    /// One 1 s tick. Call it once per second; it does not care how punctual
    /// the caller is, because it reads the clock rather than counting.
    ///
    /// This is the body of Python's `_tick_loop` AFTER `await asyncio.sleep(1)`.
    pub fn tick(&mut self, now: Micros) -> Plan {
        if !self.running {
            return Plan::none();
        }
        if self.paused {
            // Python broadcasts and `continue`s: time passes, elapsed does not.
            return Plan::none();
        }

        let real_elapsed = now.get() - self.loop_start.get() - self.pause_accumulated.get();
        // Python's `int(real_elapsed)` truncates toward zero; so does this.
        self.total_elapsed = real_elapsed / SEC;
        self.interval_elapsed = self.total_elapsed - self.interval_start_elapsed;

        let Some(iv) = self.current_iv() else {
            return self.finish();
        };
        let duration = iv.duration_s() as i64;

        if self.interval_elapsed >= duration {
            self.current_interval += 1;
            // NOT `cumulative_at(current_interval)`: Python anchors the next
            // interval at the ACTUAL elapsed time, so a late tick shortens the
            // interval that ran long instead of pushing every later interval
            // out. Copying `cumulative_at` here would reintroduce drift.
            self.interval_start_elapsed = self.total_elapsed;
            self.interval_elapsed = 0;
            return match self.motion_of_current() {
                Some((s, i)) => Plan::one(s, i),
                None => self.finish(),
            };
        }

        Plan::none()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::tests::fixture;
    use crate::model::{Interval, Program, MAX_DURATION_S, MIN_DURATION_S};

    fn at(secs: i64) -> Micros {
        Micros::from_secs(secs)
    }

    fn loaded() -> ProgramState {
        let mut s = ProgramState::new();
        s.load(fixture());
        s
    }

    fn cmds(p: Plan) -> Vec<(i32, i32)> {
        p.commands().iter().map(|(s, i)| (s.get(), i.get())).collect()
    }

    /// Drive `tick` once per second from `from` to `to`, inclusive of `to`,
    /// collecting every plan. Mirrors the Python tests' `mock_sleep` loop.
    fn run_secs(s: &mut ProgramState, from: i64, to: i64) -> Vec<(i64, Plan)> {
        let mut out = Vec::new();
        for t in from..=to {
            let p = s.tick(at(t));
            if !p.is_empty() {
                out.push((t, p));
            }
        }
        out
    }

    // --- TestLoadProgram --------------------------------------------------

    #[test]
    fn test_load_program() {
        let s = loaded();
        assert!(s.program().is_some());
        assert!(!s.running());
        assert_eq!(s.current_interval(), 0);
    }

    #[test]
    fn test_load_resets_previous() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.tick(at(100));
        assert!(s.total_elapsed() > 0);
        let mut fresh = Program::new("New", false);
        fresh.push(Interval::new(
            "A",
            60,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        s.load(fresh);
        assert_eq!(s.program().unwrap().name.as_str(), "New");
        assert!(!s.running());
        assert_eq!(s.current_interval(), 0);
        assert_eq!(s.total_elapsed(), 0);
    }

    // --- TestStart --------------------------------------------------------

    #[test]
    fn test_start_begins_execution() {
        let mut s = loaded();
        // Python: on_change.call_args_list[0].args == (2.0, 0)
        assert_eq!(cmds(s.start(at(0), 0, 0)), vec![(20, 0)]);
        assert!(s.running());
    }

    #[test]
    fn test_start_without_program_is_noop() {
        let mut s = ProgramState::new();
        assert_eq!(cmds(s.start(at(0), 0, 0)), Vec::<(i32, i32)>::new());
        assert!(!s.running());
    }

    #[test]
    fn test_start_stops_previous() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        // The second start()'s internal stop() zeroes first.
        assert_eq!(cmds(s.start(at(5), 0, 0)), vec![(0, 0), (20, 0)]);
    }

    // --- TestTick ---------------------------------------------------------

    #[test]
    fn test_tick_advances_elapsed() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.tick(at(1));
        assert_eq!(s.total_elapsed(), 1);
        s.tick(at(2));
        assert_eq!(s.total_elapsed(), 2);
        assert_eq!(s.interval_elapsed(), 2);
    }

    #[test]
    fn intervals_advance_and_command_the_next_motion() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        let events = run_secs(&mut s, 1, 59);
        assert!(events.is_empty(), "no advance before 60 s: {events:?}");
        // Warmup(60) ends -> Run(6.0 mph, 3%)
        assert_eq!(cmds(s.tick(at(60))), vec![(60, 6)]);
        assert_eq!(s.current_interval(), 1);
        assert_eq!(s.interval_elapsed(), 0);
        // Run(120) ends at 180 -> Cooldown
        let evs = run_secs(&mut s, 61, 180);
        assert_eq!(evs.len(), 1);
        assert_eq!(evs[0].0, 180);
        assert_eq!(cmds(evs[0].1), vec![(20, 0)]);
        assert_eq!(s.current_interval(), 2);
    }

    // --- TestPause --------------------------------------------------------

    #[test]
    fn test_pause_stops_progress() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        run_secs(&mut s, 1, 2);
        assert_eq!(s.total_elapsed(), 2);
        s.toggle_pause(at(2));
        // Ticks 3..5 are paused: time passes, elapsed does not.
        run_secs(&mut s, 3, 5);
        assert_eq!(s.total_elapsed(), 2, "paused ticks must not advance");
    }

    #[test]
    fn test_toggle_pause() {
        let mut s = loaded();
        s.toggle_pause(at(0));
        assert!(s.paused());
        s.toggle_pause(at(1));
        assert!(!s.paused());
    }

    #[test]
    fn pause_zeroes_speed_and_holds_incline_then_resume_restores_both() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1)); // Run: 6.0 mph, 3% incline
        // `server.py::_apply_pause_toggle` zeroes speed and leaves incline.
        assert_eq!(cmds(s.toggle_pause(at(5))), vec![(0, 6)]);
        assert_eq!(cmds(s.toggle_pause(at(15))), vec![(60, 6)]);
    }

    #[test]
    fn pausing_a_program_that_is_not_running_commands_nothing() {
        let mut s = loaded();
        assert!(s.toggle_pause(at(0)).is_empty());
        assert!(s.toggle_pause(at(1)).is_empty());
    }

    #[test]
    fn a_pause_does_not_consume_program_time() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        run_secs(&mut s, 1, 10);
        s.toggle_pause(at(10));
        run_secs(&mut s, 11, 40); // 30 s of pause
        s.toggle_pause(at(40));
        run_secs(&mut s, 41, 45);
        // 10 s before the pause + 5 s after == 15 s of program time.
        assert_eq!(s.total_elapsed(), 15);
    }

    // --- TestSkip ---------------------------------------------------------

    #[test]
    fn test_skip_advances_interval() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert_eq!(cmds(s.skip(at(1))), vec![(60, 6)]);
        assert_eq!(s.current_interval(), 1);
        assert_eq!(s.interval_elapsed(), 0);
    }

    #[test]
    fn test_skip_jumps_total_elapsed() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1));
        assert_eq!(s.current_interval(), 1);
        assert_eq!(s.total_elapsed(), 60);
        assert_eq!(s.interval_elapsed(), 0);
    }

    #[test]
    fn test_skip_last_finishes() {
        let mut p = Program::new("one", false);
        p.push(Interval::new(
            "Only",
            60,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        let mut s = ProgramState::new();
        s.load(p);
        s.start(at(0), 0, 0);
        assert_eq!(cmds(s.skip(at(1))), vec![(0, 0)], "finishing zeroes the belt");
        assert!(s.completed());
        assert!(!s.running());
    }

    #[test]
    fn test_skip_when_not_running_is_noop() {
        let mut s = loaded();
        assert!(s.skip(at(1)).is_empty());
        assert_eq!(s.current_interval(), 0);
    }

    // --- TestPrev ---------------------------------------------------------

    #[test]
    fn test_prev_goes_back() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1));
        assert_eq!(cmds(s.prev(at(2))), vec![(20, 0)]);
        assert_eq!(s.current_interval(), 0);
        assert_eq!(s.interval_elapsed(), 0);
    }

    #[test]
    fn test_prev_at_first_stays() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        run_secs(&mut s, 1, 15);
        assert_eq!(cmds(s.prev(at(15))), vec![(20, 0)], "restarts the interval");
        assert_eq!(s.current_interval(), 0);
        assert_eq!(s.interval_elapsed(), 0);
        assert_eq!(s.total_elapsed(), 0);
    }

    #[test]
    fn test_prev_rewinds_total_elapsed() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1));
        s.skip(at(2)); // now at Cooldown, cumulative 180
        assert_eq!(s.current_interval(), 2);
        s.prev(at(3));
        assert_eq!(s.current_interval(), 1);
        assert_eq!(s.total_elapsed(), 60);
        assert_eq!(s.interval_elapsed(), 0);
    }

    #[test]
    fn test_prev_when_not_running_is_noop() {
        let mut s = loaded();
        assert!(s.prev(at(1)).is_empty());
    }

    #[test]
    fn test_skip_then_prev_roundtrip() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1));
        assert_eq!(s.current_interval(), 1);
        s.prev(at(2));
        assert_eq!(s.current_interval(), 0);
        assert_eq!(s.interval_elapsed(), 0);
    }

    // --- TestSkipWhilePaused (the regression) -----------------------------

    #[test]
    fn test_skip_while_paused_preserves_timing() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        run_secs(&mut s, 1, 10);
        s.toggle_pause(at(10)); // pause at t=10
        s.skip(at(15)); // skip 5 s INTO the pause
        run_secs(&mut s, 16, 20);
        s.toggle_pause(at(20)); // resume after 10 s of pause
        run_secs(&mut s, 21, 25);

        assert_eq!(s.current_interval(), 1);
        // 60 (the skip target) + 5 non-paused seconds. The 10 s of pause must
        // NOT be counted; without `effective_pause` in `seek` it would be.
        assert_eq!(s.total_elapsed(), 65);
    }

    #[test]
    fn test_prev_while_paused_preserves_timing() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        s.skip(at(1));
        s.skip(at(2)); // interval 2, elapsed 180
        run_secs(&mut s, 3, 20);
        s.toggle_pause(at(20));
        s.prev(at(30)); // 10 s into the pause
        assert_eq!(s.current_interval(), 1);
        assert_eq!(s.total_elapsed(), 60);
        assert_eq!(s.interval_elapsed(), 0);
        s.toggle_pause(at(30));
        // Immediately after resume the clock must read exactly the seek target.
        s.tick(at(30));
        assert_eq!(s.total_elapsed(), 60);
    }

    // --- TestExtend -------------------------------------------------------

    #[test]
    fn test_extend_adds_time() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert!(s.extend_current(30));
        assert_eq!(s.current_iv().unwrap().duration_s(), 90);
    }

    #[test]
    fn test_extend_minimum_clamp() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert!(s.extend_current(-100));
        assert_eq!(s.current_iv().unwrap().duration_s(), MIN_DURATION_S);
    }

    #[test]
    fn test_extend_when_not_running() {
        let mut s = loaded();
        assert!(!s.extend_current(30));
    }

    #[test]
    fn extend_saturates_rather_than_overflowing() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert!(s.extend_current(i64::MAX));
        assert_eq!(s.current_iv().unwrap().duration_s(), MAX_DURATION_S);
    }

    // --- adjust-duration --------------------------------------------------

    #[test]
    fn adjust_duration_is_manual_only() {
        let mut s = loaded(); // manual = false
        s.start(at(0), 0, 0);
        assert!(!s.adjust_duration(60), "structured programs refuse");

        let mut manual = Program::new("Manual", true);
        manual.push(Interval::new(
            "Seg 1",
            600,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        let mut m = ProgramState::new();
        m.load(manual);
        m.start(at(0), 0, 0);
        assert!(m.adjust_duration(300));
        assert_eq!(m.program().unwrap().intervals()[0].duration_s(), 900);
        assert!(m.adjust_duration(-100_000));
        assert_eq!(
            m.program().unwrap().intervals()[0].duration_s(),
            MIN_DURATION_S
        );
    }

    #[test]
    fn adjust_duration_when_not_running() {
        let mut manual = Program::new("Manual", true);
        manual.push(Interval::new(
            "Seg 1",
            600,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        let mut m = ProgramState::new();
        m.load(manual);
        assert!(!m.adjust_duration(60));
    }

    // --- TestStop ---------------------------------------------------------

    #[test]
    fn test_stop_resets() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert_eq!(cmds(s.stop()), vec![(0, 0)]);
        assert!(!s.running());
    }

    #[test]
    fn test_stop_when_not_running() {
        let mut s = loaded();
        assert!(s.stop().is_empty(), "no belt command if nothing was running");
    }

    #[test]
    fn reset_clears_the_program_and_zeroes_if_running() {
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert_eq!(cmds(s.reset()), vec![(0, 0)]);
        assert!(s.program().is_none());
        assert!(!s.completed());
    }

    // --- TestWallClockTiming ---------------------------------------------

    #[test]
    fn test_10s_program_completes_at_10() {
        let mut p = Program::new("t", false);
        p.push(Interval::new(
            "Test",
            10,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        let mut s = ProgramState::new();
        s.load(p);
        s.start(at(0), 0, 0);
        let evs = run_secs(&mut s, 1, 12);
        assert!(s.completed());
        assert_eq!(s.total_elapsed(), 10);
        assert_eq!(evs.len(), 1);
        assert_eq!(cmds(evs[0].1), vec![(0, 0)]);
    }

    #[test]
    fn test_drift_resilience() {
        // 1.007 s per tick — the realistic overshoot from the Python test.
        let mut p = Program::new("t", false);
        p.push(Interval::new(
            "Test",
            10,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        ));
        let mut s = ProgramState::new();
        s.load(p);
        s.start(Micros::ZERO, 0, 0);
        let mut t = 0i64;
        for _ in 0..12 {
            t += 1_007_000;
            s.tick(Micros::new(t));
            if s.completed() {
                break;
            }
        }
        assert!(s.completed());
        assert_eq!(
            s.total_elapsed(),
            10,
            "wall-clock timing must absorb sleep overshoot"
        );
    }

    #[test]
    fn a_long_stall_advances_one_interval_and_restarts_its_clock() {
        // A WDT-supervised task CAN be starved for seconds by the 5 ms serial
        // loop, so ONE tick may cover several intervals' worth of wall time.
        //
        // Python advances exactly ONE interval per tick and anchors the new
        // one at the ACTUAL elapsed time (`_interval_start_elapsed =
        // self.total_elapsed`), so the interval that was starved through gets
        // its full duration starting now rather than being skipped. That is
        // the safe direction: a stall must never fast-forward a user through a
        // hard interval they never ran. Pinned here because the obvious
        // "catch up" implementation (`cumulative_at`) does the opposite.
        let mut s = loaded();
        s.start(at(0), 0, 0);
        assert_eq!(cmds(s.tick(at(500))), vec![(60, 6)], "Warmup -> Run");
        assert!(s.tick(at(501)).is_empty(), "Run just started; 120 s to go");
        assert_eq!(s.interval_elapsed(), 1);
        assert!(s.tick(at(619)).is_empty());
        assert_eq!(cmds(s.tick(at(620))), vec![(20, 0)], "Run -> Cooldown");
        assert_eq!(cmds(s.tick(at(680))), vec![(0, 0)], "Cooldown -> finish");
        assert!(s.completed());
    }

    // --- TestResumeFromPosition ------------------------------------------

    #[test]
    fn test_start_from_resume_position() {
        let mut s = loaded();
        assert_eq!(cmds(s.start(at(0), 1, 60)), vec![(60, 6)]);
        assert_eq!(s.current_interval(), 1);
        run_secs(&mut s, 1, 3);
        assert_eq!(s.total_elapsed(), 63);
    }

    #[test]
    fn test_resume_at_zero_is_normal_start() {
        let mut s = loaded();
        assert_eq!(cmds(s.start(at(0), 0, 0)), vec![(20, 0)]);
        assert_eq!(s.current_interval(), 0);
    }

    #[test]
    fn resume_past_the_end_is_clamped_not_out_of_bounds() {
        let mut s = loaded();
        // A client (or a corrupt saved position) asking to resume interval 99.
        let plan = s.start(at(0), 99, 10_000);
        assert!(plan.is_empty());
        assert_eq!(s.current_interval(), 3, "clamped to len(), not 99");
        // ...and the next tick finishes rather than indexing past the end.
        assert_eq!(cmds(s.tick(at(1))), vec![(0, 0)]);
        assert!(s.completed());
    }

    // --- properties -------------------------------------------------------

    #[test]
    fn test_total_duration() {
        assert_eq!(loaded().total_duration(), 240);
        assert_eq!(ProgramState::new().total_duration(), 0);
    }

    #[test]
    fn test_current_iv() {
        let s = loaded();
        assert_eq!(s.current_iv().unwrap().name.as_str(), "Warmup");
        assert!(ProgramState::new().current_iv().is_none());
    }

    #[test]
    fn an_empty_program_finishes_on_the_first_tick() {
        let mut s = ProgramState::new();
        s.load(Program::new("empty", false));
        assert!(s.start(at(0), 0, 0).is_empty());
        assert_eq!(cmds(s.tick(at(1))), vec![(0, 0)]);
        assert!(s.completed());
    }

    #[test]
    fn ticking_with_no_program_never_commands_anything() {
        let mut s = ProgramState::new();
        for t in 0..100 {
            assert!(s.tick(at(t)).is_empty());
        }
    }
}
