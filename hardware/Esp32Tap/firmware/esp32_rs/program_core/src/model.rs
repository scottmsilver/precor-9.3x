//! The stored program: fixed size, fixed count, clamped on the way in.
//!
//! WHY THE BOUNDS ARE HERE AND NOT AT THE HTTP EDGE. A bound checked by a
//! handler is a bound one new handler can forget. A `Program` is a value that
//! CANNOT hold more than [`MAX_INTERVALS`] intervals — the array is that long
//! — so no caller, present or future, can produce an oversized one. The only
//! way to add an interval is [`Program::push`], and it returns `false` when
//! full rather than growing.

use safety_core::units::{InclineHalfPct, SpeedTenths};
use safety_core::FixedStr;

/// Intervals a program may hold.
///
/// NOT A ROUND NUMBER — it is derived. A submitted program must fit in one
/// `reqbudget` slot (2048 bytes), because admission refuses a body larger than
/// a slot BEFORE parsing, and the worst case is every interval present with a
/// maximum-length name and every number at its widest. See
/// [`max_program_json_bytes`], which computes that worst case and is asserted
/// against the slot size by a test in this file and by a `const` assertion in
/// the firmware. Raising this without re-deriving it makes the largest
/// legitimate program un-submittable, which is a silent 413 rather than a
/// crash — hence the test.
pub const MAX_INTERVALS: usize = 24;

/// Bytes in an interval's name.
pub const MAX_NAME: usize = 20;

/// Bytes in the program's name. Python's prompt caps it at 40 characters.
pub const MAX_PROGRAM_NAME: usize = 48;

/// `program_engine.MIN_DURATION`.
pub const MIN_DURATION_S: u32 = 10;

/// Seconds in one interval. Python has NO upper bound: `duration` is
/// `max(10, int(d))`, so a client can store `2**31 - 1` seconds (68 years) and
/// the UI renders a workout that never advances. 24 hours is longer than any
/// real interval and keeps `total_duration` well inside `i64` for the whole
/// program.
pub const MAX_DURATION_S: u32 = 24 * 60 * 60;

/// `program_engine.MIN_SPEED` = 0.5 mph, in tenths.
pub const MIN_SPEED_TENTHS: i32 = 5;
/// `program_engine.MAX_SPEED` = 12.0 mph, in tenths. Equal to
/// `SpeedTenths::MAX`, and the test below pins that equality — if the
/// hardware limit ever moves, this must be re-derived, not silently outrun.
pub const MAX_SPEED_TENTHS: i32 = 120;
/// `program_engine.MAX_INCLINE` = 15%, in half-percent units. Equal to
/// `InclineHalfPct::APP_MAX`.
pub const MAX_INCLINE_HALF: i32 = 30;

/// One interval. `Copy`, no indirection, no allocation.
#[derive(Clone, Copy, Debug)]
pub struct Interval {
    pub name: FixedStr<MAX_NAME>,
    /// Always inside `MIN_DURATION_S..=MAX_DURATION_S` — the constructors and
    /// the mutators are the only ways to set it and all of them clamp.
    duration_s: u32,
    pub speed: SpeedTenths,
    pub incline: InclineHalfPct,
}

impl Interval {
    /// Build an interval with every field clamped, exactly as
    /// `program_engine.validate_interval` does.
    ///
    /// Takes ALREADY-DECODED integers (tenths, half-percent, seconds) rather
    /// than floats: the float boundary lives in [`crate::json`], so this
    /// function is total and has no rounding of its own.
    pub fn new(name: &str, duration_s: u32, speed: SpeedTenths, incline: InclineHalfPct) -> Self {
        Interval {
            name: FixedStr::from_str_truncating(name),
            duration_s: duration_s.clamp(MIN_DURATION_S, MAX_DURATION_S),
            speed: SpeedTenths::new(speed.get().clamp(MIN_SPEED_TENTHS, MAX_SPEED_TENTHS)),
            incline: InclineHalfPct::new(incline.get().clamp(0, MAX_INCLINE_HALF)),
        }
    }

    pub fn duration_s(&self) -> u32 {
        self.duration_s
    }

    /// Set the duration, clamped. The ONLY mutator — `extend`/`adjust-duration`
    /// both come through here, so neither can produce a 0-second interval that
    /// the tick loop would spin through, nor a 68-year one.
    pub fn set_duration_s(&mut self, seconds: i64) {
        self.duration_s = seconds.clamp(MIN_DURATION_S as i64, MAX_DURATION_S as i64) as u32;
    }
}

/// A whole workout. ~1 KB, `Copy`, no heap anywhere.
#[derive(Clone, Copy, Debug)]
pub struct Program {
    pub name: FixedStr<MAX_PROGRAM_NAME>,
    /// `program["manual"]` — a free-run session rather than a designed
    /// workout. `adjust-duration` is manual-only, exactly as in Python.
    pub manual: bool,
    intervals: [Interval; MAX_INTERVALS],
    len: usize,
}

impl Program {
    pub fn new(name: &str, manual: bool) -> Self {
        Program {
            name: FixedStr::from_str_truncating(name),
            manual,
            intervals: [Interval::new("", MIN_DURATION_S, SpeedTenths::ZERO, InclineHalfPct::ZERO);
                MAX_INTERVALS],
            len: 0,
        }
    }

    /// Append. Returns `false` when full — REJECT rather than grow.
    pub fn push(&mut self, iv: Interval) -> bool {
        if self.len >= MAX_INTERVALS {
            return false;
        }
        self.intervals[self.len] = iv;
        self.len += 1;
        true
    }

    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    pub fn intervals(&self) -> &[Interval] {
        &self.intervals[..self.len]
    }
    pub fn get(&self, i: usize) -> Option<&Interval> {
        self.intervals().get(i)
    }
    pub fn get_mut(&mut self, i: usize) -> Option<&mut Interval> {
        if i >= self.len {
            return None;
        }
        Some(&mut self.intervals[i])
    }
    pub fn last_mut(&mut self) -> Option<&mut Interval> {
        if self.len == 0 {
            return None;
        }
        Some(&mut self.intervals[self.len - 1])
    }

    /// `ProgramState.total_duration`. `i64` and saturating: 24 intervals of
    /// 24 hours is 2 073 600, nowhere near overflow, but the arithmetic is
    /// still total.
    pub fn total_duration_s(&self) -> i64 {
        let mut total: i64 = 0;
        for iv in self.intervals() {
            total = total.saturating_add(iv.duration_s() as i64);
        }
        total
    }

    /// `ProgramState._cumulative_at` — duration at the START of interval `idx`.
    pub fn cumulative_at(&self, idx: usize) -> i64 {
        let n = if idx > self.len { self.len } else { idx };
        let mut total: i64 = 0;
        for iv in &self.intervals()[..n] {
            total = total.saturating_add(iv.duration_s() as i64);
        }
        total
    }
}

/// Worst-case serialised size of a program, in bytes.
///
/// Computed rather than measured so it stays true when a bound moves. The
/// shape is exactly what [`crate::json::write_program`] emits:
///
/// ```text
/// {"name":"<48>","manual":false,"intervals":[ <iv>,<iv>,... ]}
/// {"name":"<20>","duration":86400,"speed":12.0,"incline":15.0},
/// ```
pub const fn max_program_json_bytes() -> usize {
    // {"name":"  +  name  +  ","manual":false,"intervals":[   ...   ]}
    let wrapper = 9 + MAX_PROGRAM_NAME + 31 + 2;
    // {"name":" + name + ","duration":  + 5 digits + ,"speed": + `120.0`.max
    //   -> "12.0" is 4 chars + ,"incline": + "15.0" is 4 chars + },
    let per_interval = 9 + MAX_NAME + 13 + 5 + 9 + 4 + 11 + 4 + 2;
    wrapper + per_interval * MAX_INTERVALS
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    #[test]
    fn a_maximal_program_fits_one_request_slot() {
        // THE DERIVATION. If this fails, the largest legitimate program can no
        // longer be submitted: `reqbudget::admit` refuses a body over
        // SLOT_BYTES before parsing, so the user would see a 413 on a workout
        // the device claims to support. Shrink MAX_INTERVALS or MAX_NAME.
        assert!(
            max_program_json_bytes() <= crate::MAX_PROGRAM_JSON_BYTES,
            "worst-case program is {} bytes, one request slot is {}",
            max_program_json_bytes(),
            crate::MAX_PROGRAM_JSON_BYTES
        );
    }

    #[test]
    fn application_limits_agree_with_the_safety_core_units() {
        assert_eq!(MAX_SPEED_TENTHS, SpeedTenths::MAX.get());
        assert_eq!(MAX_INCLINE_HALF, InclineHalfPct::APP_MAX.get());
    }

    #[test]
    fn interval_clamps_like_validate_interval() {
        // speed above MAX -> 12.0, below MIN -> 0.5 (program_engine.MIN_SPEED)
        let hot = Interval::new("x", 60, SpeedTenths::new(999), InclineHalfPct::new(99));
        assert_eq!(hot.speed.get(), 120);
        assert_eq!(hot.incline.get(), 30);
        let cold = Interval::new("x", 1, SpeedTenths::new(0), InclineHalfPct::new(-4));
        assert_eq!(cold.speed.get(), 5, "MIN_SPEED = 0.5 mph, as in Python");
        assert_eq!(cold.incline.get(), 0);
        assert_eq!(cold.duration_s(), MIN_DURATION_S);
    }

    #[test]
    fn duration_is_bounded_above_unlike_python() {
        let iv = Interval::new("x", u32::MAX, SpeedTenths::new(30), InclineHalfPct::ZERO);
        assert_eq!(iv.duration_s(), MAX_DURATION_S);
    }

    #[test]
    fn a_name_longer_than_the_field_is_truncated_not_overflowed() {
        let iv = Interval::new(
            "a name far longer than twenty bytes",
            60,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO,
        );
        assert_eq!(iv.name.as_str().len(), MAX_NAME);
    }

    #[test]
    fn push_refuses_rather_than_growing() {
        let mut p = Program::new("full", false);
        for _ in 0..MAX_INTERVALS {
            assert!(p.push(Interval::new(
                "i",
                60,
                SpeedTenths::new(30),
                InclineHalfPct::ZERO
            )));
        }
        assert!(!p.push(Interval::new(
            "one too many",
            60,
            SpeedTenths::new(30),
            InclineHalfPct::ZERO
        )));
        assert_eq!(p.len(), MAX_INTERVALS);
    }

    #[test]
    fn cumulative_and_total_match_the_python_properties() {
        // The canonical fixture from python/tests/helpers.py make_program().
        let p = fixture();
        assert_eq!(p.total_duration_s(), 240);
        assert_eq!(p.cumulative_at(0), 0);
        assert_eq!(p.cumulative_at(1), 60);
        assert_eq!(p.cumulative_at(2), 180);
        assert_eq!(p.cumulative_at(3), 240);
        // Out of range must saturate, not panic.
        assert_eq!(p.cumulative_at(99), 240);
    }

    /// `python/tests/helpers.py::make_program()` — Warmup(60s, 2.0, 0),
    /// Run(120s, 6.0, 3), Cooldown(60s, 2.0, 0).
    pub fn fixture() -> Program {
        let mut p = Program::new("Test Workout", false);
        p.push(Interval::new(
            "Warmup",
            60,
            SpeedTenths::new(20),
            InclineHalfPct::new(0),
        ));
        p.push(Interval::new(
            "Run",
            120,
            SpeedTenths::new(60),
            InclineHalfPct::new(6),
        ));
        p.push(Interval::new(
            "Cooldown",
            60,
            SpeedTenths::new(20),
            InclineHalfPct::new(0),
        ));
        p
    }
}
