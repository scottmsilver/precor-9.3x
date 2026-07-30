//! What the model is told, and what it is told it cannot do.
//!
//! Adapted from `python/program_engine.py`'s `CHAT_SYSTEM_PROMPT` and
//! `SYSTEM_PROMPT`, with the differences stated to the model rather than left
//! for it to discover:
//!
//! * **No clock.** The device has no RTC and no SNTP. It cannot answer "when
//!   did I last run" and must not pretend to. The only time it has is the
//!   current session's own elapsed tick.
//! * **No workout library and no SQL.** The Pi hands Gemini a read-only SQLite
//!   surface over runs, saved workouts and history. This device stores records
//!   in a bounded flash ring with no query engine, so `query_workout_data`,
//!   `load_workout` and `add_time` do not exist here. Saying so is what stops
//!   the model inventing an answer from a tool it thinks it has.
//! * **A short memory.** `hist::TURNS` turns. Told, so it does not claim to
//!   remember more.
//! * **The device clamps.** Speed 0–12 mph, incline 0–15% in half steps. The
//!   model is told the device is the authority so a clamped result reads as
//!   expected behaviour rather than a malfunction.
//!
//! Both constants are `&'static str` in flash, not built at runtime: they never
//! change, and a prompt assembled per turn would be per-turn memory.

/// The chat prompt. Kept terse — every byte is a byte of the request budget.
pub const CHAT_SYSTEM: &str = concat!(
    "You are a warm, concise treadmill coach built into the machine itself. ",
    "Reply in one or two short sentences; never use markdown, lists or emoji. ",
    "Use a tool when the user asks for a change; do not narrate the tool call. ",
    "THE DEVICE IS THE AUTHORITY: it clamps speed to 0-12 mph and incline to ",
    "0-15% in 0.5 steps, and its answer is what actually happened. ",
    "THIS DEVICE HAS NO CLOCK AND NO CALENDAR: never state a date, a time of ",
    "day, or how long ago something happened. The only time you have is the ",
    "current session's elapsed time, which is given to you below when a ",
    "workout is running. ",
    "You have NO access to past runs, saved workouts or any query tool. If you ",
    "are asked about workout history, say plainly that this machine does not ",
    "keep a searchable history. ",
    "Your memory is the last few messages only. ",
    "Before starting a workout, confirm with the user."
);

/// The workout-generation prompt. Structured-output mode: the reply must be
/// one JSON object and nothing else.
///
/// The bounds quoted here are `program_core`'s own and are re-clamped by
/// `Interval::new` on the way in, so a model that ignores them produces a
/// clamped workout rather than a rejected one.
/// NOTE THE ABSENCE OF QUOTE CHARACTERS, which is a constraint rather than a
/// style: both prompts are written into the request body VERBATIM by
/// `req::Builder::raw`, because they are compile-time constants this crate
/// owns. A `"` or a `\` in either would produce a malformed body, so the shape
/// is described in prose instead of shown as literal JSON, and
/// `prompts_need_no_escaping` fails the build's test gate if one ever creeps
/// back in.
pub const PROGRAM_SYSTEM: &str = concat!(
    "You design treadmill interval workouts. Reply with ONE JSON object and ",
    "nothing else. It has a name field (a short title) and an intervals array. ",
    "Each interval has: name (under 16 characters), duration (whole seconds, ",
    "at least 10), speed (mph, 0 to 12, one decimal) and incline (percent, ",
    "0 to 15, in 0.5 steps). ",
    "Use at most 24 intervals. Always begin with a warm-up interval and end ",
    "with a cool-down. Emit no prose, no code fence and no trailing text."
);

#[cfg(test)]
mod prompt_tests {
    use super::*;

    #[test]
    fn prompts_need_no_escaping() {
        // `req::Builder::raw` writes these between two quotes without an
        // escape pass. Anything here that would need one is a malformed
        // request body, discovered a round trip later as an opaque HTTP 400.
        for (what, s) in [
            ("CHAT_SYSTEM", CHAT_SYSTEM),
            ("PROGRAM_SYSTEM", PROGRAM_SYSTEM),
        ] {
            for b in s.as_bytes() {
                assert!(
                    *b >= 0x20 && *b != b'"' && *b != b'\\' && *b != 0x7f,
                    "{what} contains a byte that must be escaped: {b:#04x}"
                );
            }
        }
    }

    #[test]
    fn tool_declarations_are_one_json_value() {
        // Not a parser — a balance check. An unbalanced declaration block makes
        // every turn fail with an opaque 400 and nothing on the device says so.
        let mut depth = 0i32;
        let mut in_str = false;
        let mut esc = false;
        for b in TOOL_DECLARATIONS.bytes() {
            if in_str {
                if esc {
                    esc = false;
                } else if b == b'\\' {
                    esc = true;
                } else if b == b'"' {
                    in_str = false;
                }
                continue;
            }
            match b {
                b'"' => in_str = true,
                b'{' | b'[' => depth += 1,
                b'}' | b']' => depth -= 1,
                _ => {}
            }
            assert!(depth >= 0, "TOOL_DECLARATIONS closes a container it never opened");
        }
        assert_eq!(depth, 0, "TOOL_DECLARATIONS is unbalanced");
        assert!(!in_str, "TOOL_DECLARATIONS ends inside a string");
    }
}

/// The declared vocabulary, verbatim on the wire.
///
/// NINE tools, and the list is the DEVICE's capability rather than the Pi's:
/// `query_workout_data`, `load_workout` and `add_time` are absent because
/// nothing on this device could honour them, and a declared tool that silently
/// does nothing is worse than an absent one — the model would keep calling it.
///
/// Written as one flat string rather than assembled, because it is a constant
/// and assembling a constant per request is per-request memory.
pub const TOOL_DECLARATIONS: &str = concat!(
    r#"{"functionDeclarations":[
{"name":"set_speed","description":"Set belt speed in mph (0-12).","parameters":{"type":"object","properties":{"mph":{"type":"number","description":"Speed in mph"}},"required":["mph"]}},
{"name":"set_incline","description":"Set incline percent (0-15, 0.5 steps).","parameters":{"type":"object","properties":{"incline":{"type":"number","description":"Incline percent"}},"required":["incline"]}},
{"name":"generate_workout","description":"Design an interval workout from a description and load it. Does not start it.","parameters":{"type":"object","properties":{"description":{"type":"string","description":"What the workout should be"}},"required":["description"]}},
{"name":"start_workout","description":"Start the loaded workout.","parameters":{"type":"object","properties":{}}},
{"name":"stop_treadmill","description":"Stop the belt and end the workout.","parameters":{"type":"object","properties":{}}},
{"name":"pause_program","description":"Pause the running workout.","parameters":{"type":"object","properties":{}}},
{"name":"resume_program","description":"Resume the paused workout.","parameters":{"type":"object","properties":{}}},
{"name":"skip_interval","description":"Skip to the next interval.","parameters":{"type":"object","properties":{}}},
{"name":"extend_interval","description":"Add or remove seconds from the current interval.","parameters":{"type":"object","properties":{"seconds":{"type":"number","description":"Seconds to add, negative to shorten"}},"required":["seconds"]}}
]}"#
);
