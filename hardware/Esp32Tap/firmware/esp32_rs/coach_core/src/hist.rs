//! The conversation, capped — and capped in the ONE place that matters.
//!
//! `server.py` keeps a module-level `chat_history` list and trims it to the
//! last 20 entries on the way OUT of a turn. That is fine on a Pi with a
//! gigabyte of RAM and wrong here for two reasons: the trim happens after the
//! list has already grown, and 20 turns of unbounded strings is unbounded
//! memory. Both are the same defect at different scales — memory that is a
//! function of how long somebody has been talking.
//!
//! So this is a RING of [`TURNS`] fixed slots. Pushing the (N+1)th turn
//! overwrites the oldest; nothing is ever allocated, moved or grown, and
//! `size_of::<History>()` is the same after ten thousand turns as after one
//! (asserted in `lib.rs`).
//!
//! # The cap, stated
//!
//! **6 turns of 160 bytes each = 960 bytes of conversation, ever.** Three
//! exchanges. That is short, and short is the honest trade on a device whose
//! whole request-path budget is 8 KB: a longer window costs resident RAM on
//! every device forever to make one conversation slightly less forgetful. The
//! model is TOLD the window is short in the system prompt, so it does not
//! pretend to remember what it cannot.
//!
//! A message longer than [`TURN_BYTES`] is TRUNCATED, not rejected. A truncated
//! question still produces a useful answer; a rejected one produces a user who
//! thinks the device is broken.

use safety_core::FixedStr;

/// Turns retained. 6 = three user/model exchanges.
pub const TURNS: usize = 6;

/// Bytes per turn.
pub const TURN_BYTES: usize = 160;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Role {
    User,
    Model,
}

impl Role {
    pub const fn wire(self) -> &'static str {
        match self {
            Role::User => "user",
            Role::Model => "model",
        }
    }
}

#[derive(Clone, Copy)]
pub struct Turn {
    pub role: Role,
    pub text: FixedStr<TURN_BYTES>,
}

pub struct History {
    slots: [Turn; TURNS],
    /// Total turns ever pushed. `len()` is this, saturated at `TURNS`.
    pushed: usize,
}

impl History {
    pub const fn new() -> Self {
        History {
            slots: [Turn {
                role: Role::User,
                text: FixedStr::new(),
            }; TURNS],
            pushed: 0,
        }
    }

    pub fn clear(&mut self) {
        self.pushed = 0;
    }

    pub fn turns(&self) -> usize {
        if self.pushed > TURNS {
            TURNS
        } else {
            self.pushed
        }
    }

    pub fn is_empty(&self) -> bool {
        self.pushed == 0
    }

    /// Append, sanitising and truncating. Overwrites the oldest turn once the
    /// ring is full — the ring never grows and never reallocates.
    pub fn push(&mut self, role: Role, text: &str) {
        let idx = self.pushed % TURNS;
        let slot = &mut self.slots[idx];
        slot.role = role;
        slot.text.clear();
        // SANITISED AT INGEST, the same rule `program_core::json` uses for
        // names: a byte below 0x20, a `"` or a `\` becomes `_`. That is what
        // lets `req::build` write every stored string between two quotes
        // VERBATIM, with no escape path anywhere that could be got wrong.
        for b in text.as_bytes() {
            if slot.text.len() >= TURN_BYTES {
                break;
            }
            slot.text.push_byte(crate::tool::sanitise(*b));
        }
        self.pushed = self.pushed.saturating_add(1);
    }

    /// Oldest retained turn first — the order the request body needs.
    pub fn iter(&self) -> impl Iterator<Item = &Turn> {
        let n = self.turns();
        let start = self.pushed.saturating_sub(n);
        (0..n).map(move |k| &self.slots[(start + k) % TURNS])
    }

    // THERE IS DELIBERATELY NO ROLLBACK.
    //
    // `server.py` rolls `chat_history` back to its pre-turn length when a turn
    // throws. That works on a Python list and CANNOT work on a ring: once the
    // ring is full, pushing the failed user message has already OVERWRITTEN the
    // oldest turn, and lowering a counter afterwards resurrects a slot that no
    // longer holds what it used to. It would look correct and quietly serve the
    // model somebody else's sentence.
    //
    // So nothing is pushed until the turn SUCCEEDS. `req::build` takes the
    // pending user message as a separate argument and appends it last, and the
    // caller commits both halves of the exchange only after a reply arrives. A
    // failed turn leaves the conversation exactly as it was, with no rollback
    // to get wrong.
}

impl Default for History {
    fn default() -> Self {
        Self::new()
    }
}
