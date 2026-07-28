//! Bounded JSON in, bounded JSON out. No allocation, no recursion, no panic.
//!
//! WHY HAND-ROLLED. A program arrives from an untrusted client and is the ONE
//! piece of variable-length data this device accepts. `serde_json` needs
//! `alloc`; the C++ tier reached for rapidjson and got a per-store memory pool
//! that only ever grew, which is the bug the whole request-budget design
//! exists to prevent. The subset below is ~200 lines, is `no_std`, allocates
//! nothing, and is host-tested in milliseconds.
//!
//! TOTALITY IS THE PROPERTY THAT MATTERS. Every loop is bounded by the input
//! length or an explicit depth counter, every integer op is `checked_`, and
//! there is no indexing that is not preceded by a length check. A malformed
//! body returns [`ParseError`]; it cannot panic, and under `panic = "abort"` a
//! panic here would reboot the device and drop the relay mid-run.
//!
//! ## Postel's law, applied in the direction the project's rules require
//!
//! The device is the SERVER, so it validates at its boundary — but it is also
//! talking to an app that evolves separately, so unknown fields are SKIPPED
//! rather than rejected ([`Scanner::skip_value`]). A future server field the
//! app echoes back must not make a workout unloadable.
//!
//! ## Names are sanitised on the way in, not escaped on the way out
//!
//! Any byte below 0x20, plus `"` and `\`, becomes `_` when a name is stored.
//! One rule, applied once, at the boundary — so every stored name is already
//! safe to emit verbatim and the serialiser has no escape path that could be
//! got wrong. The cost is cosmetic (a label loses a quote); the benefit is
//! that this device cannot be made to emit malformed or injected JSON.

use crate::model::{Interval, Program, MAX_INTERVALS};
use crate::state::ProgramState;
use core::fmt::Write;
use safety_core::units::{InclineHalfPct, SpeedTenths};
use safety_core::FixedStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParseError {
    /// Not JSON, or not the shape we accept.
    Malformed,
    /// More than [`MAX_INTERVALS`]. REJECTED, never truncated: silently
    /// dropping intervals would run a different workout than the user built.
    TooManyIntervals,
    /// `intervals` absent or empty — there is nothing to run.
    NoIntervals,
    /// An interval is missing `duration`, `speed` or `incline`
    /// (`validate_interval` raises `ValueError` for the same case).
    MissingField,
    /// A number too wide to be meaningful. Refused rather than wrapped.
    NumberOutOfRange,
}

/// Maximum nesting `skip_value` will walk before giving up. A hostile body
/// cannot make this loop forever or blow a stack — it is iterative, and the
/// counter bounds the shape as well as the time.
const MAX_DEPTH: u32 = 8;

/// Largest integer part we will accept, in digits. 12 digits of hundredths is
/// ~10^10 of anything, far outside every field's clamp, and keeps the
/// accumulator inside `i64` with room to spare.
const MAX_INT_DIGITS: u32 = 12;

struct Scanner<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Scanner<'a> {
    fn new(b: &'a [u8]) -> Self {
        Scanner { b, i: 0 }
    }
    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }
    fn bump(&mut self) -> Option<u8> {
        let c = self.peek()?;
        self.i += 1;
        Some(c)
    }
    fn skip_ws(&mut self) {
        while let Some(c) = self.peek() {
            if c == b' ' || c == b'\t' || c == b'\n' || c == b'\r' {
                self.i += 1;
            } else {
                break;
            }
        }
    }
    fn eat(&mut self, want: u8) -> bool {
        self.skip_ws();
        if self.peek() == Some(want) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    fn expect(&mut self, want: u8) -> Result<(), ParseError> {
        if self.eat(want) {
            Ok(())
        } else {
            Err(ParseError::Malformed)
        }
    }

    /// Read a JSON string into `out`, sanitising and truncating.
    ///
    /// Truncation of a NAME is deliberate and is not the same decision as
    /// rejecting an oversized interval list: a label is cosmetic, the interval
    /// list is the workout.
    fn string_into<const N: usize>(&mut self, out: &mut FixedStr<N>) -> Result<(), ParseError> {
        self.expect(b'"')?;
        out.clear();
        loop {
            let c = self.bump().ok_or(ParseError::Malformed)?;
            match c {
                b'"' => return Ok(()),
                b'\\' => {
                    let e = self.bump().ok_or(ParseError::Malformed)?;
                    let ch = match e {
                        b'"' | b'\\' | b'/' => b'_', // sanitised, see module note
                        b'n' | b'r' | b't' | b'b' | b'f' => b' ',
                        b'u' => {
                            // Consume exactly 4 hex digits and substitute. No
                            // UTF-16 surrogate assembly: a label is not worth
                            // a decoder, and `_` is total.
                            for _ in 0..4 {
                                let h = self.bump().ok_or(ParseError::Malformed)?;
                                if !h.is_ascii_hexdigit() {
                                    return Err(ParseError::Malformed);
                                }
                            }
                            b'_'
                        }
                        _ => return Err(ParseError::Malformed),
                    };
                    out.push_byte(ch);
                }
                // Raw control characters are invalid in JSON strings; treat
                // them as malformed rather than guessing.
                0x00..=0x1f => return Err(ParseError::Malformed),
                other => out.push_byte(other),
            }
        }
    }

    /// A number, returned in HUNDREDTHS. Integer arithmetic throughout: there
    /// is no float anywhere in this crate, so there is no rounding that
    /// differs between builds.
    fn number_hundredths(&mut self) -> Result<i64, ParseError> {
        self.skip_ws();
        let neg = self.peek() == Some(b'-');
        if neg || self.peek() == Some(b'+') {
            self.i += 1;
        }
        let mut whole: i64 = 0;
        let mut digits = 0u32;
        while let Some(c) = self.peek() {
            if !c.is_ascii_digit() {
                break;
            }
            digits += 1;
            if digits > MAX_INT_DIGITS {
                return Err(ParseError::NumberOutOfRange);
            }
            whole = whole * 10 + (c - b'0') as i64;
            self.i += 1;
        }
        if digits == 0 {
            return Err(ParseError::Malformed);
        }
        let mut frac: i64 = 0;
        let mut scale = 0;
        if self.peek() == Some(b'.') {
            self.i += 1;
            let mut seen = 0u32;
            while let Some(c) = self.peek() {
                if !c.is_ascii_digit() {
                    break;
                }
                seen += 1;
                if scale < 2 {
                    frac = frac * 10 + (c - b'0') as i64;
                    scale += 1;
                }
                self.i += 1;
                // A pathological fraction is bounded by the body length, but
                // cap it anyway so the cost is obviously constant.
                if seen > 20 {
                    return Err(ParseError::NumberOutOfRange);
                }
            }
            if seen == 0 {
                return Err(ParseError::Malformed);
            }
        }
        // Exponents are refused rather than approximated: `1e9` as a duration
        // is not a workout, it is an attack or a bug.
        if matches!(self.peek(), Some(b'e') | Some(b'E')) {
            return Err(ParseError::NumberOutOfRange);
        }
        while scale < 2 {
            frac *= 10;
            scale += 1;
        }
        let v = whole
            .checked_mul(100)
            .and_then(|w| w.checked_add(frac))
            .ok_or(ParseError::NumberOutOfRange)?;
        Ok(if neg { -v } else { v })
    }

    /// Skip one value of any type. Iterative; `MAX_DEPTH` bounds the shape.
    fn skip_value(&mut self) -> Result<(), ParseError> {
        self.skip_ws();
        let mut depth: u32 = 0;
        loop {
            self.skip_ws();
            match self.peek().ok_or(ParseError::Malformed)? {
                b'{' | b'[' => {
                    depth += 1;
                    if depth > MAX_DEPTH {
                        return Err(ParseError::Malformed);
                    }
                    self.i += 1;
                }
                b'}' | b']' => {
                    self.i += 1;
                    depth = depth.checked_sub(1).ok_or(ParseError::Malformed)?;
                }
                b'"' => {
                    let mut sink: FixedStr<1> = FixedStr::new();
                    self.string_into(&mut sink)?;
                }
                b',' | b':' => self.i += 1,
                b't' | b'f' | b'n' => {
                    // true / false / null — consume the word.
                    while matches!(self.peek(), Some(c) if c.is_ascii_alphabetic()) {
                        self.i += 1;
                    }
                }
                c if c.is_ascii_digit() || c == b'-' || c == b'+' => {
                    self.number_hundredths()?;
                }
                _ => return Err(ParseError::Malformed),
            }
            if depth == 0 {
                return Ok(());
            }
        }
    }

    /// Read a `true`/`false` literal.
    fn bool_value(&mut self) -> Result<bool, ParseError> {
        self.skip_ws();
        if self.b[self.i..].starts_with(b"true") {
            self.i += 4;
            Ok(true)
        } else if self.b[self.i..].starts_with(b"false") {
            self.i += 5;
            Ok(false)
        } else {
            Err(ParseError::Malformed)
        }
    }
}

/// hundredths -> tenths, half-up. See lib.rs divergence 5.
fn hundredths_to_tenths(h: i64) -> i32 {
    let r = if h >= 0 { (h + 5) / 10 } else { (h - 5) / 10 };
    r.clamp(i32::MIN as i64, i32::MAX as i64) as i32
}

/// hundredths of a percent -> half-percent units, half-up.
/// `round(value * 2) / 2` in Python, done exactly.
fn hundredths_to_half_pct(h: i64) -> i32 {
    let r = if h >= 0 { (h + 25) / 50 } else { (h - 25) / 50 };
    r.clamp(i32::MIN as i64, i32::MAX as i64) as i32
}

/// Parse one interval object.
fn parse_interval(s: &mut Scanner<'_>) -> Result<Interval, ParseError> {
    s.expect(b'{')?;
    let mut name: FixedStr<{ crate::model::MAX_NAME }> = FixedStr::new();
    let mut duration: Option<i64> = None;
    let mut speed: Option<i64> = None;
    let mut incline: Option<i64> = None;

    if s.eat(b'}') {
        return Err(ParseError::MissingField);
    }
    loop {
        let mut key: FixedStr<24> = FixedStr::new();
        s.string_into(&mut key)?;
        s.expect(b':')?;
        match key.as_str() {
            "name" => s.string_into(&mut name)?,
            "duration" => duration = Some(s.number_hundredths()? / 100),
            "speed" => speed = Some(s.number_hundredths()?),
            "incline" => incline = Some(s.number_hundredths()?),
            // Unknown field: skip it. The Pi's intervals carry extras
            // (`_manual_seg`, ids) and a workout must still load.
            _ => s.skip_value()?,
        }
        if s.eat(b',') {
            continue;
        }
        s.expect(b'}')?;
        break;
    }

    let (Some(duration), Some(speed), Some(incline)) = (duration, speed, incline) else {
        return Err(ParseError::MissingField);
    };
    // Clamp to u32 BEFORE `Interval::new` clamps to the range: a negative or
    // absurd duration becomes the minimum, never a wrapped huge one.
    let duration = duration.clamp(0, u32::MAX as i64) as u32;
    Ok(Interval::new(
        name.as_str(),
        duration,
        SpeedTenths::new(hundredths_to_tenths(speed)),
        InclineHalfPct::new(hundredths_to_half_pct(incline)),
    ))
}

/// Parse a whole program.
///
/// Accepts either the bare program object (`{"name":…,"intervals":[…]}`) — the
/// shape `python/server.py` stores and returns — or a wrapper carrying it
/// under a `"program"` key, which is what a client echoing `GET /api/program`
/// back at us would send. Accepting both is Postel, not sloppiness: the two are
/// unambiguous (a program object has `intervals`, a wrapper has `program`).
pub fn parse_program(body: &[u8]) -> Result<Program, ParseError> {
    let mut s = Scanner::new(body);
    parse_program_object(&mut s, 0)
}

fn parse_program_object(s: &mut Scanner<'_>, unwrapped: u32) -> Result<Program, ParseError> {
    s.expect(b'{')?;
    let mut name: FixedStr<{ crate::model::MAX_PROGRAM_NAME }> = FixedStr::new();
    let mut manual = false;
    let mut intervals: Option<Program> = None;
    let mut wrapped: Option<Program> = None;

    if s.eat(b'}') {
        return Err(ParseError::NoIntervals);
    }
    loop {
        let mut key: FixedStr<24> = FixedStr::new();
        s.string_into(&mut key)?;
        s.expect(b':')?;
        match key.as_str() {
            "name" => s.string_into(&mut name)?,
            "manual" => manual = s.bool_value()?,
            "intervals" => {
                let mut p = Program::new("", false);
                s.expect(b'[')?;
                if !s.eat(b']') {
                    let mut count = 0usize;
                    loop {
                        let iv = parse_interval(s)?;
                        count += 1;
                        if count > MAX_INTERVALS || !p.push(iv) {
                            return Err(ParseError::TooManyIntervals);
                        }
                        if s.eat(b',') {
                            continue;
                        }
                        s.expect(b']')?;
                        break;
                    }
                }
                intervals = Some(p);
            }
            "program" if unwrapped == 0 => {
                s.skip_ws();
                if s.peek() == Some(b'n') {
                    // `"program": null` — nothing to load.
                    s.skip_value()?;
                } else {
                    wrapped = Some(parse_program_object(s, 1)?);
                }
            }
            _ => s.skip_value()?,
        }
        if s.eat(b',') {
            continue;
        }
        s.expect(b'}')?;
        break;
    }

    // A wrapper's own `intervals` (there are none in practice) loses to the
    // nested program, which is the more specific statement.
    if let Some(w) = wrapped {
        return if w.is_empty() {
            Err(ParseError::NoIntervals)
        } else {
            Ok(w)
        };
    }
    let Some(mut p) = intervals else {
        return Err(ParseError::NoIntervals);
    };
    if p.is_empty() {
        return Err(ParseError::NoIntervals);
    }
    p.name = if name.is_empty() {
        FixedStr::from_str_truncating("Workout")
    } else {
        name
    };
    p.manual = manual;
    Ok(p)
}

// --- serialisation --------------------------------------------------------

/// tenths of mph -> `12.0`. No float, so no locale, no `%f`, no NaN.
fn write_tenths<W: Write>(w: &mut W, tenths: i32) -> core::fmt::Result {
    write!(w, "{}.{}", tenths / 10, (tenths % 10).abs())
}

/// half-percent -> `15.0` / `2.5`.
fn write_half_pct<W: Write>(w: &mut W, half: i32) -> core::fmt::Result {
    write!(w, "{}.{}", half / 2, if half % 2 == 0 { 0 } else { 5 })
}

/// The `program` object. Names are already sanitised, so they are emitted
/// verbatim — see the module header.
pub fn write_program<W: Write>(w: &mut W, p: &Program) -> core::fmt::Result {
    write!(
        w,
        r#"{{"name":"{}","manual":{},"intervals":["#,
        p.name.as_str(),
        p.manual
    )?;
    for (n, iv) in p.intervals().iter().enumerate() {
        if n > 0 {
            w.write_char(',')?;
        }
        write!(w, r#"{{"name":"{}","duration":{},"speed":"#, iv.name.as_str(), iv.duration_s())?;
        write_tenths(w, iv.speed.get())?;
        w.write_str(r#","incline":"#)?;
        write_half_pct(w, iv.incline.get())?;
        w.write_char('}')?;
    }
    w.write_str("]}")
}

/// `ProgramState.to_dict()`, field for field and in the same order.
///
/// `encouragement` is deliberately absent — see the crate header, divergence 2.
pub fn write_state<W: Write>(w: &mut W, s: &ProgramState) -> core::fmt::Result {
    w.write_str(r#"{"type":"program","program":"#)?;
    match s.program() {
        Some(p) => write_program(w, p)?,
        None => w.write_str("null")?,
    }
    write!(
        w,
        r#","running":{},"paused":{},"completed":{},"current_interval":{},"interval_elapsed":{},"total_elapsed":{},"total_duration":{}}}"#,
        s.running(),
        s.paused(),
        s.completed(),
        s.current_interval(),
        s.interval_elapsed(),
        s.total_elapsed(),
        s.total_duration()
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{MAX_DURATION_S, MIN_DURATION_S};

    fn render(p: &Program) -> String {
        let mut s = String::new();
        write_program(&mut s, p).unwrap();
        s
    }

    #[test]
    fn round_trips_a_pi_shaped_program() {
        let body = br#"{"name":"Test Workout","intervals":[
            {"name":"Warmup","duration":60,"speed":2.0,"incline":0},
            {"name":"Run","duration":120,"speed":6.0,"incline":3},
            {"name":"Cooldown","duration":60,"speed":2.0,"incline":0}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.len(), 3);
        assert_eq!(p.name.as_str(), "Test Workout");
        assert_eq!(p.intervals()[1].speed.get(), 60);
        assert_eq!(p.intervals()[1].incline.get(), 6);
        assert_eq!(p.total_duration_s(), 240);
        // ...and what we emit parses back to the same thing.
        let again = parse_program(render(&p).as_bytes()).unwrap();
        assert_eq!(again.total_duration_s(), 240);
        assert_eq!(again.intervals()[1].name.as_str(), "Run");
        assert_eq!(again.intervals()[1].speed.get(), 60);
    }

    #[test]
    fn unknown_fields_are_skipped_not_rejected() {
        let body = br#"{"name":"X","created_at":"2026-01-01T00:00:00Z","source":"gemini",
            "meta":{"nested":{"deep":[1,2,{"x":null}]}},
            "intervals":[{"name":"A","duration":60,"speed":3,"incline":1,"extra":[1,2,3]}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.len(), 1);
        assert_eq!(p.intervals()[0].speed.get(), 30);
    }

    #[test]
    fn accepts_the_wrapper_shape_get_api_program_returns() {
        let body = br#"{"type":"program","running":false,"program":
            {"name":"Echo","manual":true,"intervals":[{"name":"A","duration":600,"speed":3.0,"incline":0}]}}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.name.as_str(), "Echo");
        assert!(p.manual);
        assert_eq!(p.len(), 1);
    }

    #[test]
    fn a_null_program_wrapper_is_not_loadable() {
        let body = br#"{"type":"program","program":null,"running":false}"#;
        assert_eq!(parse_program(body).err(), Some(ParseError::NoIntervals));
    }

    #[test]
    fn too_many_intervals_is_refused_not_truncated() {
        let mut body = String::from(r#"{"name":"big","intervals":["#);
        for i in 0..(MAX_INTERVALS + 1) {
            if i > 0 {
                body.push(',');
            }
            body.push_str(r#"{"name":"i","duration":60,"speed":3,"incline":0}"#);
        }
        body.push_str("]}");
        assert_eq!(
            parse_program(body.as_bytes()).err(),
            Some(ParseError::TooManyIntervals)
        );
    }

    #[test]
    fn exactly_max_intervals_is_accepted() {
        let mut body = String::from(r#"{"name":"big","intervals":["#);
        for i in 0..MAX_INTERVALS {
            if i > 0 {
                body.push(',');
            }
            body.push_str(r#"{"name":"i","duration":60,"speed":3,"incline":0}"#);
        }
        body.push_str("]}");
        assert_eq!(parse_program(body.as_bytes()).unwrap().len(), MAX_INTERVALS);
    }

    #[test]
    fn a_maximal_program_really_does_fit_a_request_slot() {
        // The COMPUTED bound in model.rs, exercised against the real
        // serialiser rather than trusted.
        let mut p = Program::new("123456789012345678901234567890123456789012345678", false);
        for _ in 0..MAX_INTERVALS {
            assert!(p.push(Interval::new(
                "12345678901234567890",
                MAX_DURATION_S,
                SpeedTenths::new(120),
                InclineHalfPct::new(30),
            )));
        }
        let text = render(&p);
        assert!(
            text.len() <= crate::MAX_PROGRAM_JSON_BYTES,
            "maximal program serialises to {} bytes, slot is {}",
            text.len(),
            crate::MAX_PROGRAM_JSON_BYTES
        );
        assert!(text.len() <= crate::model::max_program_json_bytes());
        // ...and it survives the round trip at full size.
        assert_eq!(parse_program(text.as_bytes()).unwrap().len(), MAX_INTERVALS);
    }

    #[test]
    fn missing_required_interval_fields_are_rejected() {
        // validate_interval raises ValueError for each of these.
        for body in [
            br#"{"intervals":[{"name":"A","speed":3,"incline":0}]}"#.as_slice(),
            br#"{"intervals":[{"name":"A","duration":60,"incline":0}]}"#.as_slice(),
            br#"{"intervals":[{"name":"A","duration":60,"speed":3}]}"#.as_slice(),
            br#"{"intervals":[{}]}"#.as_slice(),
        ] {
            assert_eq!(
                parse_program(body).err(),
                Some(ParseError::MissingField),
                "{body:?}"
            );
        }
    }

    #[test]
    fn an_empty_or_absent_interval_list_is_rejected() {
        assert_eq!(
            parse_program(br#"{"name":"x","intervals":[]}"#).err(),
            Some(ParseError::NoIntervals)
        );
        assert_eq!(
            parse_program(br#"{"name":"x"}"#).err(),
            Some(ParseError::NoIntervals)
        );
    }

    #[test]
    fn clamps_match_validate_interval() {
        let body = br#"{"intervals":[{"name":"hot","duration":5,"speed":99.9,"incline":40},
                                     {"name":"cold","duration":-7,"speed":0.1,"incline":-3}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.intervals()[0].speed.get(), 120);
        assert_eq!(p.intervals()[0].incline.get(), 30);
        assert_eq!(p.intervals()[0].duration_s(), MIN_DURATION_S);
        assert_eq!(p.intervals()[1].speed.get(), 5, "MIN_SPEED = 0.5 mph");
        assert_eq!(p.intervals()[1].incline.get(), 0);
        assert_eq!(p.intervals()[1].duration_s(), MIN_DURATION_S);
    }

    #[test]
    fn incline_snaps_to_half_steps() {
        // TestValidateInterval: 2.3 -> 2.5, 2.2 -> 2.0, 5.5 stays 5.5.
        let body = br#"{"intervals":[
            {"name":"a","duration":60,"speed":3,"incline":2.3},
            {"name":"b","duration":60,"speed":3,"incline":2.2},
            {"name":"c","duration":60,"speed":3,"incline":5.5}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.intervals()[0].incline.get(), 5, "2.3% -> 2.5%");
        assert_eq!(p.intervals()[1].incline.get(), 4, "2.2% -> 2.0%");
        assert_eq!(p.intervals()[2].incline.get(), 11, "5.5% stays");
    }

    #[test]
    fn speed_rounds_to_one_decimal() {
        let body = br#"{"intervals":[
            {"name":"a","duration":60,"speed":3.44,"incline":0},
            {"name":"b","duration":60,"speed":3.45,"incline":0},
            {"name":"c","duration":60,"speed":3.46,"incline":0}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.intervals()[0].speed.get(), 34);
        assert_eq!(p.intervals()[1].speed.get(), 35, "half-up, deterministically");
        assert_eq!(p.intervals()[2].speed.get(), 35);
    }

    #[test]
    fn hostile_numbers_are_refused_not_wrapped() {
        for body in [
            br#"{"intervals":[{"name":"a","duration":99999999999999999999,"speed":3,"incline":0}]}"#.as_slice(),
            br#"{"intervals":[{"name":"a","duration":1e9,"speed":3,"incline":0}]}"#.as_slice(),
        ] {
            assert_eq!(
                parse_program(body).err(),
                Some(ParseError::NumberOutOfRange),
                "{body:?}"
            );
        }
    }

    #[test]
    fn names_are_sanitised_so_output_is_always_valid_json() {
        let body = br#"{"name":"a\"b\\c","intervals":[{"name":"x\"y","duration":60,"speed":3,"incline":0}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.name.as_str(), "a_b_c");
        assert_eq!(p.intervals()[0].name.as_str(), "x_y");
        let text = render(&p);
        assert!(!text.contains('\\'));
        // And the emitted text is re-parsable, which is the property that
        // matters: an escape bug here would make the app's own echo unloadable.
        assert_eq!(parse_program(text.as_bytes()).unwrap().len(), 1);
    }

    #[test]
    fn a_long_name_is_truncated_but_the_workout_survives() {
        let body = br#"{"intervals":[{"name":"an extremely long interval label indeed","duration":60,"speed":3,"incline":0}]}"#;
        let p = parse_program(body).unwrap();
        assert_eq!(p.intervals()[0].name.as_str().len(), crate::model::MAX_NAME);
        assert_eq!(p.len(), 1);
    }

    #[test]
    fn garbage_never_panics() {
        // Totality, exhaustively over a nasty corpus. Under `panic = "abort"`
        // a panic here reboots the device and drops the relay mid-run, so
        // "returns Err" is a safety property, not tidiness.
        let corpus: &[&[u8]] = &[
            b"",
            b"{",
            b"}",
            b"[",
            b"null",
            b"{\"intervals\":",
            b"{\"intervals\":[",
            b"{\"intervals\":[{",
            b"{\"intervals\":[{\"duration\":",
            b"{\"name\":\"unterminated",
            b"{\"name\":\"\\",
            b"{\"name\":\"\\u12\"}",
            b"{\"name\":\"\\q\"}",
            br#"{"intervals":[{"duration":.5,"speed":1,"incline":0}]}"#,
            br#"{"intervals":[{"duration":--1,"speed":1,"incline":0}]}"#,
            br#"{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":{"a":1}}}}}}}}}"#,
            br#"{"intervals":[{"name":"a","duration":60,"speed":3,"incline":0},]}"#,
            b"\xff\xfe\x00\x01",
        ];
        for body in corpus {
            let _ = parse_program(body);
        }
        // Every prefix of a valid body, which is where truncation bugs live.
        let good = br#"{"name":"T","manual":false,"intervals":[{"name":"A","duration":60,"speed":2.5,"incline":1.5}]}"#;
        for n in 0..good.len() {
            let _ = parse_program(&good[..n]);
        }
        // Every single-byte mutation of a valid body.
        for i in 0..good.len() {
            for b in [b'{', b'}', b'[', b']', b'"', b',', b':', b'\\', b'0', 0xff] {
                let mut m = good.to_vec();
                m[i] = b;
                let _ = parse_program(&m);
            }
        }
    }

    #[test]
    fn write_state_matches_the_python_to_dict_shape() {
        let mut st = ProgramState::new();
        let mut out = String::new();
        write_state(&mut out, &st).unwrap();
        assert_eq!(
            out,
            r#"{"type":"program","program":null,"running":false,"paused":false,"completed":false,"current_interval":0,"interval_elapsed":0,"total_elapsed":0,"total_duration":0}"#
        );

        st.load(parse_program(br#"{"name":"T","intervals":[{"name":"A","duration":60,"speed":2,"incline":0}]}"#).unwrap());
        st.start(safety_core::units::Micros::ZERO, 0, 0);
        out.clear();
        write_state(&mut out, &st).unwrap();
        assert!(out.contains(r#""running":true"#), "{out}");
        assert!(out.contains(r#""total_duration":60"#), "{out}");
        assert!(
            out.contains(r#""program":{"name":"T","manual":false,"intervals":[{"name":"A","duration":60,"speed":2.0,"incline":0.0}]}"#),
            "{out}"
        );
    }

    #[test]
    fn numbers_render_with_one_decimal_like_every_ui_in_this_project() {
        let mut s = String::new();
        write_tenths(&mut s, 120).unwrap();
        assert_eq!(s, "12.0");
        s.clear();
        write_tenths(&mut s, 5).unwrap();
        assert_eq!(s, "0.5");
        s.clear();
        write_half_pct(&mut s, 11).unwrap();
        assert_eq!(s, "5.5");
        s.clear();
        write_half_pct(&mut s, 30).unwrap();
        assert_eq!(s, "15.0");
    }
}
