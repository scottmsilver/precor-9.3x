//! Repairing a workout the model ran out of tokens inside.
//!
//! `program_engine.generate_program` does this on the Pi: `json.loads` raises,
//! and rather than throwing the whole workout away it attempts a brace repair
//! of the truncated object. The failure it handles is REAL and common — the
//! model is given a token budget, a long workout hits it, and the reply ends
//! mid-interval:
//!
//! ```text
//! {"name":"Hills","intervals":[{"name":"Warm","duration":300,"speed":3,
//!  "incline":0},{"name":"Climb","duration":180,"speed":4,"incl
//! ```
//!
//! # This is a TRUNCATION repair and nothing else
//!
//! It cuts back to the last interval that CLOSED and re-closes the array and
//! the object. It never invents a field, never completes a partial number, and
//! never guesses at what the model was about to write. A workout that has no
//! complete interval is not salvageable and is refused — the user gets an
//! honest failure instead of a one-interval workout they did not ask for, which
//! on a treadmill is the difference between "try again" and a belt running at
//! whatever the warm-up speed happened to be for the next hour.
//!
//! Everything it produces still goes through `program_core::json::parse_program`
//! and therefore through `Interval::new`'s clamps. Salvage cannot widen a
//! bound; the worst it can do is produce a SHORTER workout than the model
//! intended, which is exactly what a truncation is.

use safety_core::FixedStr;

/// Trim `text` back to the last complete interval and re-close the JSON.
///
/// Returns false, leaving `text` untouched, when there is nothing to salvage.
///
/// Total by construction: one forward pass with an explicit depth counter, no
/// recursion, and a truncation point that is always an index the scan itself
/// produced.
pub fn repair_program<const N: usize>(text: &mut FixedStr<N>) -> bool {
    let bytes = text.as_bytes();
    // Depth of the last `}` that closed an INTERVAL — i.e. one that returned
    // the scan to depth 2 (`{` object, `[` intervals array). Recorded as the
    // index just past it.
    let mut cut: Option<usize> = None;
    let mut depth: i32 = 0;
    let mut in_str = false;
    let mut esc = false;
    let mut saw_array = false;
    for (i, b) in bytes.iter().enumerate() {
        if in_str {
            if esc {
                esc = false;
            } else if *b == b'\\' {
                esc = true;
            } else if *b == b'"' {
                in_str = false;
            }
            continue;
        }
        match *b {
            b'"' => in_str = true,
            b'{' | b'[' => {
                if *b == b'[' && depth == 1 {
                    saw_array = true;
                }
                depth += 1;
            }
            b'}' | b']' => {
                depth -= 1;
                if depth < 0 {
                    return false; // not the shape we repair
                }
                if depth == 2 && *b == b'}' {
                    cut = Some(i + 1);
                }
            }
            _ => {}
        }
    }
    // Already well-formed (or empty): nothing to do, and saying so is not a
    // failure — the caller tries the parse FIRST and only lands here when it
    // failed, so a balanced-but-unparseable body is not a truncation.
    if depth == 0 || !saw_array {
        return false;
    }
    let Some(cut) = cut else {
        return false; // no interval ever closed
    };

    // Rebuild in place: keep `..cut`, then close the array and the object. The
    // array may itself be nested one deeper than the object if the model wrapped
    // it, but `cut` is at depth 2 by construction, so exactly `]}` is needed.
    let mut out: FixedStr<N> = FixedStr::new();
    for b in &bytes[..cut] {
        out.push_byte(*b);
    }
    out.push_byte(b']');
    out.push_byte(b'}');
    if out.len() != cut + 2 {
        return false; // did not fit — refuse rather than emit half a repair
    }
    *text = out;
    true
}
