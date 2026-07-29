//! Turning what the model ASKED for into what the device will DO.
//!
//! # The rule this file exists to enforce
//!
//! A tool call is untrusted input. It arrived from a public endpoint, its
//! arguments were written by a model that is documented to round and to invent
//! numbers, and the transport in between is reachable by anyone on the LAN. So
//! there is exactly one function ([`validate`]) between a `ToolCall` and an
//! [`Action`], and it is TOTAL: every argument is parsed by a bounded scanner
//! that cannot overflow, every number is clamped into `safety_core`'s newtypes,
//! and anything it does not recognise is [`Reject`]ed rather than defaulted.
//!
//! An `Action` is deliberately NOT a command. It names a verb and carries
//! already-clamped values in the units the belt path takes; the firmware then
//! feeds it through `control::command` and `ProgramState` — the SAME calls an
//! HTTP request and the interval executor make. There is no second path to the
//! belt and this crate could not build one if it tried: it cannot name the
//! hardware.
//!
//! # Clamp, do not refuse
//!
//! `set_speed(999)` becomes 12.0 mph and SAYS SO. Refusing would be defensible
//! and is worse in practice: the model is told the result, so a refusal makes
//! it retry with another out-of-range number, while a clamp ends the exchange
//! and gives the user a true sentence. The Pi behaves the same way
//! (`validate_interval` clamps; `server.py` clamps). What is NOT negotiable is
//! that the clamp happens before anything downstream sees the value, and that
//! the downstream clamps still run — they are the authority, this is the
//! answer.
//!
//! # The vocabulary is what the DEVICE can do
//!
//! Nine tools, not the Pi's twelve. `query_workout_data` is absent because
//! there is no SQL engine on this device and a stub that answered nothing would
//! look exactly like one that worked; `load_workout`/`add_time` are absent
//! because they need the Pi's uncapped workout library. Both are stated in the
//! system prompt so the model is not left guessing, and both are named here so
//! the omission is findable.

use crate::scan::ToolCall;
use program_core::model::{MAX_INCLINE_HALF, MAX_SPEED_TENTHS};
use program_core::{MAX_DURATION_S, MIN_DURATION_S};
use safety_core::units::{InclineHalfPct, SpeedTenths};
use safety_core::FixedStr;

/// Longest workout description we will carry into the generation call.
pub const DESC_BYTES: usize = 128;

/// What the firmware should do, in the units the belt path takes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Action {
    /// Already clamped to `0..=MAX_SPEED_TENTHS`.
    SetSpeed(SpeedTenths),
    /// Already clamped to `0..=MAX_INCLINE_HALF`.
    SetIncline(InclineHalfPct),
    StartWorkout,
    StopTreadmill,
    PauseProgram,
    ResumeProgram,
    SkipInterval,
    /// Already clamped to `-3600..=3600`, as `POST /api/program/extend` is.
    ExtendInterval(i32),
    /// Ask for a workout to be generated. The description is sanitised and
    /// bounded; the generation itself is a second model call.
    GenerateWorkout(FixedStr<DESC_BYTES>),
}

/// Why a call was not turned into an action. Every variant is reported to the
/// model as a `functionResponse`, so it can correct itself on the next turn
/// rather than repeating the same broken call.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reject {
    /// Not one of the nine declared tools.
    UnknownTool,
    /// The required argument was absent, or was not a number/string.
    MissingArg,
    /// The argument object did not survive transport intact (too large, or the
    /// stream ended inside it).
    Damaged,
}

impl Reject {
    /// A sentence for the user and for the model. Deliberately says nothing
    /// about the endpoint, the key, or the transport.
    pub const fn message(self) -> &'static str {
        match self {
            Reject::UnknownTool => "that tool does not exist on this device",
            Reject::MissingArg => "the tool call was missing a required value",
            Reject::Damaged => "the tool call did not arrive intact and was ignored",
        }
    }
}

/// The one gate between a model's tool call and the belt.
pub fn validate(call: &ToolCall) -> Result<Action, Reject> {
    if !call.is_intact() {
        return Err(Reject::Damaged);
    }
    let args = call.args.as_bytes();
    match call.name.as_str() {
        "set_speed" => {
            let h = number(args, b"mph").ok_or(Reject::MissingArg)?;
            // Hundredths -> tenths, truncating toward zero, then clamped.
            // `/ 10` before the clamp cannot overflow: `number` already
            // saturates inside i32.
            Ok(Action::SetSpeed(SpeedTenths::new(
                (h / 10).clamp(0, MAX_SPEED_TENTHS),
            )))
        }
        "set_incline" => {
            let h = number(args, b"incline").ok_or(Reject::MissingArg)?;
            // Hundredths -> half-percent. `/ 50`, never `* 2 / 100`: the
            // multiply wraps to a NEGATIVE incline at the top of i32, and a
            // wrap here would be a belt command derived from an overflow.
            Ok(Action::SetIncline(InclineHalfPct::new(
                (h / 50).clamp(0, MAX_INCLINE_HALF),
            )))
        }
        "start_workout" => Ok(Action::StartWorkout),
        "stop_treadmill" => Ok(Action::StopTreadmill),
        "pause_program" => Ok(Action::PauseProgram),
        "resume_program" => Ok(Action::ResumeProgram),
        "skip_interval" => Ok(Action::SkipInterval),
        "extend_interval" => {
            let h = number(args, b"seconds").ok_or(Reject::MissingArg)?;
            // The Pi's own bound (`Field(ge=-3600, le=3600)`), so a hostile
            // value cannot ask for a year.
            Ok(Action::ExtendInterval((h / 100).clamp(-3600, 3600)))
        }
        "generate_workout" => {
            let mut desc: FixedStr<DESC_BYTES> = FixedStr::new();
            if !string(args, b"description", &mut desc) {
                return Err(Reject::MissingArg);
            }
            Ok(Action::GenerateWorkout(desc))
        }
        _ => Err(Reject::UnknownTool),
    }
}

/// A rendered, human-readable result for an accepted action.
///
/// This is the `result` string the app shows next to the action and the
/// `functionResponse` the model is given on the next turn — one rendering, so
/// the two can never disagree about what happened.
pub fn describe(action: &Action, out: &mut FixedStr<96>) {
    out.clear();
    match action {
        Action::SetSpeed(s) => {
            out.push_str("speed set to ");
            push_decimal(out, s.get(), 10);
            out.push_str(" mph");
        }
        Action::SetIncline(i) => {
            out.push_str("incline set to ");
            push_decimal(out, i.get() * 5, 10);
            out.push_str("%");
        }
        Action::StartWorkout => out.push_str("workout started"),
        Action::StopTreadmill => out.push_str("treadmill stopped"),
        Action::PauseProgram => out.push_str("program paused"),
        Action::ResumeProgram => out.push_str("program resumed"),
        Action::SkipInterval => out.push_str("skipped to the next interval"),
        Action::ExtendInterval(s) => {
            out.push_str("interval adjusted by ");
            out.push_i64(*s as i64);
            out.push_str("s");
        }
        Action::GenerateWorkout(_) => out.push_str("building a workout"),
    }
}

/// `123` with `scale = 10` renders `12.3`. No floating point anywhere on this
/// device's number path — the units are integers and stay integers.
fn push_decimal(out: &mut FixedStr<96>, v: i32, scale: i32) {
    let neg = v < 0;
    let a = (v as i64).unsigned_abs() as i64;
    if neg {
        out.push_byte(b'-');
    }
    out.push_i64(a / scale as i64);
    out.push_byte(b'.');
    out.push_i64(a % scale as i64);
}

/// The clamped duration bounds a generated interval is held to, restated here
/// only so the prompt can quote them. `program_core::Interval::new` is what
/// ENFORCES them.
pub const GEN_MIN_DURATION_S: u32 = MIN_DURATION_S;
pub const GEN_MAX_DURATION_S: u32 = MAX_DURATION_S;

// ---------------------------------------------------------------------------
// Bounded argument scanning.
//
// A separate, smaller scanner than `program_core::json`, and deliberately so:
// this one reads ONE flat object of at most `ARGS_BYTES`, it is total, and it
// never recurses. It is the same anchored-member discipline `net::api`'s
// parsers use — a key is matched as `"key"` followed by a colon, never as bare
// bytes, because bare bytes match inside a VALUE (`{"note":"mph","x":9}` set
// the speed once, on the Pi's own endpoint shape).
// ---------------------------------------------------------------------------

/// Byte offset just past `"key"` and its colon, if present as a MEMBER.
fn member(body: &[u8], key: &[u8]) -> Option<usize> {
    if key.is_empty() || key.len() + 2 > 32 {
        return None;
    }
    let mut pat = [0u8; 32];
    pat[0] = b'"';
    pat[1..1 + key.len()].copy_from_slice(key);
    pat[1 + key.len()] = b'"';
    let pat = &pat[..key.len() + 2];
    if pat.len() > body.len() {
        return None;
    }
    let pos = body.windows(pat.len()).position(|w| w == pat)?;
    let mut i = pos + pat.len();
    while i < body.len() && (body[i] == b' ' || body[i] == b'\t') {
        i += 1;
    }
    if i >= body.len() || body[i] != b':' {
        return None;
    }
    Some(i + 1)
}

/// The number at `"key"`, in hundredths.
///
/// Accepts `3`, `3.5`, `3.50`, `-1`, and — because a model that was told
/// "number" quite often sends `"3.5"` anyway — a quoted number. REFUSES
/// anything it cannot represent rather than wrapping: `9e99`, a 40-digit
/// integer and `NaN` all yield `None`, and `None` becomes a MissingArg the
/// model is told about. Exponent notation is refused too: `1e3` is a shape this
/// device has never needed, and silently reading it as `1` would be worse than
/// saying no.
fn number(body: &[u8], key: &[u8]) -> Option<i32> {
    let mut i = member(body, key)?;
    while i < body.len() && (body[i] == b' ' || body[i] == b'\t') {
        i += 1;
    }
    // A model that ignores the declared type sends "3.5". Take it.
    let quoted = i < body.len() && body[i] == b'"';
    if quoted {
        i += 1;
    }
    let neg = i < body.len() && body[i] == b'-';
    if neg {
        i += 1;
    }
    let mut whole: i32 = 0;
    let mut digits = 0u32;
    while i < body.len() && body[i].is_ascii_digit() {
        whole = whole.checked_mul(10)?.checked_add((body[i] - b'0') as i32)?;
        digits += 1;
        i += 1;
    }
    if digits == 0 {
        return None;
    }
    let mut frac: i32 = 0;
    let mut scale = 0;
    if i < body.len() && body[i] == b'.' {
        i += 1;
        let mut seen = 0;
        while i < body.len() && body[i].is_ascii_digit() {
            if scale < 2 {
                frac = frac * 10 + (body[i] - b'0') as i32;
                scale += 1;
            }
            seen += 1;
            i += 1;
        }
        if seen == 0 {
            return None; // `3.` is not a number
        }
    }
    // Exponents, hex, `NaN`, `Infinity`: refuse rather than misread the prefix.
    if i < body.len() {
        let t = body[i];
        if t == b'e' || t == b'E' || t.is_ascii_alphabetic() {
            return None;
        }
    }
    while scale < 2 {
        frac = frac.checked_mul(10)?;
        scale += 1;
    }
    let v = whole.checked_mul(100)?.checked_add(frac)?;
    Some(if neg { -v } else { v })
}

/// The string at `"key"`, sanitised into `out`. Returns false if absent.
///
/// SANITISED AT INGEST, exactly as `program_core::json` stores names: any byte
/// below 0x20, plus `"` and `\`, becomes `_`. One rule applied once at the
/// boundary means every string this crate later EMITS into a request body is
/// already safe to write verbatim, so the request builder has no escape path
/// that could be got wrong.
fn string<const N: usize>(body: &[u8], key: &[u8], out: &mut FixedStr<N>) -> bool {
    let Some(mut i) = member(body, key) else {
        return false;
    };
    while i < body.len() && (body[i] == b' ' || body[i] == b'\t') {
        i += 1;
    }
    if i >= body.len() || body[i] != b'"' {
        return false;
    }
    i += 1;
    out.clear();
    while i < body.len() {
        let b = body[i];
        if b == b'"' {
            return true;
        }
        if b == b'\\' {
            // The value came out of a JSON string that `scan` captured RAW, so
            // escapes are still escaped. Take the next byte literally after
            // sanitising; `\"` therefore ends nothing and becomes `_`.
            i += 1;
            if i >= body.len() {
                return false;
            }
        }
        out.push_byte(sanitise(body[i]));
        i += 1;
    }
    false // unterminated
}

pub(crate) const fn sanitise(b: u8) -> u8 {
    if b < 0x20 || b == b'"' || b == b'\\' || b == 0x7f {
        b'_'
    } else {
        b
    }
}
