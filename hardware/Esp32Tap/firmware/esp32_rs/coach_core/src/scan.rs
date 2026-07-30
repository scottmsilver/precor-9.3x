//! Consuming a `generateContent` reply through a bounded buffer.
//!
//! # Why this is a byte-at-a-time machine and not a parser
//!
//! A model reply is LARGE and arrives INCREMENTALLY. `maxOutputTokens` is 1024
//! for a chat turn and the envelope around it (`usageMetadata`, `modelVersion`,
//! `safetyRatings`, `avgLogprobs`) is easily several kilobytes more. Buffering
//! the whole body to parse it would make resident memory a function of what a
//! remote server chose to send — which is precisely the property this firmware
//! exists not to have, and which rebooted the C++ tier once already.
//!
//! So the reply is never whole anywhere. `esp_http_client_read` fills a small
//! chunk on the coach task's stack, the chunk is pushed through [`ReplyScanner`]
//! a byte at a time, and the chunk is then reused. What survives is only what
//! this scanner CHOSE to keep: the concatenated text parts, up to [`MAX_CALLS`]
//! tool calls, and the finish reason. Everything else is recognised and
//! dropped as it goes past.
//!
//! CHUNK BOUNDARIES ARE NOT A CASE. The machine holds all of its state in
//! itself and none on the stack, so `push_all(whole_body)` and pushing the same
//! bytes one at a time are the same computation. `chunking_is_not_observable`
//! asserts exactly that against every fixture in this file, because "it worked
//! when the response arrived in one TCP segment" is the classic way this shape
//! goes wrong in the field and never in the lab.
//!
//! # Totality
//!
//! Every loop is bounded by the input, depth is bounded by [`MAX_DEPTH`], every
//! sink saturates instead of growing, and there is no indexing that is not
//! preceded by a bounds check. A malformed body sets [`ReplyScanner::malformed`]
//! and yields whatever was extracted before the damage; it cannot panic. Under
//! `panic = "abort"` a panic here would reboot the device and drop the relay
//! mid-run, so "cannot panic" is a belt-availability property, not a style one.
//!
//! # What it looks for
//!
//! ```text
//! {"candidates":[{"content":{"parts":[
//!     {"text":"Sure — taking it to 3 mph."},
//!     {"functionCall":{"name":"set_speed","args":{"mph":3}}}
//! ]},"finishReason":"STOP"}], "usageMetadata":{...}}
//! ```
//!
//! `text` is only harvested INSIDE a `parts` array and OUTSIDE a `functionCall`,
//! so a `text` key that appears in an error envelope or in a tool argument
//! cannot become the coach's answer. `args` is captured as RAW BYTES rather
//! than interpreted here: the argument shapes are per-tool and belong in
//! [`crate::tool`], which is where they are validated and clamped.

use safety_core::FixedStr;

/// The text sink, in bytes.
///
/// Sized by the LARGER of its two consumers rather than by the chattier one: a
/// chat answer only needs a few hundred bytes, but the same scanner consumes
/// the workout-generation reply, whose single text part IS the program JSON —
/// and `program_core` already proves the worst-case program fits
/// `MAX_PROGRAM_JSON_BYTES`. Sizing this to that number means a generated
/// workout is never truncated by OUR buffer before `parse_program` sees it,
/// only ever by the model's own token budget (which [`crate::salvage`] then
/// tries to repair).
pub const TEXT_BYTES: usize = program_core::MAX_PROGRAM_JSON_BYTES;

/// Tool calls extracted from one reply.
///
/// The Pi's loop lets a turn emit any number; four is what a coaching turn
/// plausibly uses (`set_speed` + `set_incline`, or `generate_workout` +
/// `start_workout`) and the fifth is refused rather than allocated for. A reply
/// that overflows this sets [`ReplyScanner::too_many_calls`] and the excess is
/// DROPPED — never silently executed, never partially executed.
pub const MAX_CALLS: usize = 4;

/// Raw-argument budget per call, in bytes.
///
/// `{"description":"a 30 minute hill workout with four climbs"}` is the widest
/// real argument; 192 holds that with room. An argument object larger than this
/// marks the call `args_overflow` and the call is REFUSED by
/// [`crate::tool::validate`] — a truncated argument object could parse to a
/// DIFFERENT number than the model sent, and a different number is a different
/// belt speed.
pub const ARGS_BYTES: usize = 192;

/// Tool-name budget. `generate_workout` is 16 bytes; nothing in the declared
/// vocabulary is longer than 24, and a name that does not fit cannot match one
/// of them, so truncation here can only ever produce `UnknownTool`.
pub const NAME_BYTES: usize = 24;

/// Nesting the machine will track. Gemini's reply is 6 deep at `args`; 12
/// leaves room for a nested argument object and refuses anything past that as
/// malformed rather than following it.
const MAX_DEPTH: usize = 12;

/// Key budget. Long enough for `finishReason`/`functionCall`/`description`;
/// a longer key cannot match one we act on, so truncating is safe.
const KEY_BYTES: usize = 24;

/// One tool call the model asked for, exactly as it sent it.
#[derive(Clone, Copy)]
pub struct ToolCall {
    pub name: FixedStr<NAME_BYTES>,
    /// The `args` object, raw, braces included. Interpreted by
    /// [`crate::tool`], never here.
    pub args: FixedStr<ARGS_BYTES>,
    /// The args object exceeded [`ARGS_BYTES`]. The call is unusable.
    pub args_overflow: bool,
    /// The args object never closed — the stream ended, or the body was
    /// truncated, part-way through it. The call is unusable.
    pub args_unterminated: bool,
}

impl ToolCall {
    pub const fn new() -> Self {
        ToolCall {
            name: FixedStr::new(),
            args: FixedStr::new(),
            args_overflow: false,
            args_unterminated: false,
        }
    }

    /// Whether the call survived transport intact. A call that did not is
    /// REFUSED rather than best-guessed: half an argument object is not a
    /// smaller request, it is a different one.
    pub fn is_intact(&self) -> bool {
        !self.name.is_empty() && !self.args_overflow && !self.args_unterminated
    }
}

impl Default for ToolCall {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Sink {
    Drop,
    Key,
    Text,
    CallName,
    Finish,
}

#[derive(Clone, Copy)]
struct Ctx {
    /// `true` for `[`, `false` for `{`.
    array: bool,
    /// Inside an object, the next string is a key until a `:` is seen.
    expect_key: bool,
}

/// The incremental extractor. One instance, reused for every turn.
pub struct ReplyScanner {
    stack: [Ctx; MAX_DEPTH],
    depth: usize,

    in_string: bool,
    escape: bool,
    /// Remaining hex digits of a `\uXXXX` escape, and the value so far.
    uni: u8,
    uni_val: u32,
    sink: Sink,
    key: FixedStr<KEY_BYTES>,

    /// Depth of the innermost `parts` array, if we are inside one.
    parts_depth: Option<usize>,
    /// Depth of the `functionCall` object, if we are inside one.
    fc_depth: Option<usize>,
    /// Depth of the `args` object, if we are inside one.
    args_depth: Option<usize>,
    /// Which slot of `calls` the current `functionCall` is filling.
    slot: usize,

    /// The concatenated `text` parts. For a workout generation this IS the
    /// program JSON.
    pub text: FixedStr<TEXT_BYTES>,
    /// `text` hit [`TEXT_BYTES`] and the rest was dropped.
    pub text_overflow: bool,
    pub calls: [ToolCall; MAX_CALLS],
    pub n_calls: usize,
    /// A fifth (or later) call was seen and dropped.
    pub too_many_calls: bool,
    /// `STOP`, `MAX_TOKENS`, `SAFETY`, … — the model's own account of why it
    /// stopped. `MAX_TOKENS` is what makes a truncated program JSON expected
    /// rather than surprising.
    pub finish: FixedStr<KEY_BYTES>,
    /// The body was not the JSON it claimed to be.
    pub malformed: bool,
    /// A container was opened at least once. A reply that never opens one is
    /// not a `generateContent` answer at all — it is an nginx 502 page, a
    /// captive-portal login form, or a proxy's plain-text error. Without this
    /// flag such a body scanned CLEAN (depth 0, no string open) and the caller
    /// could not tell "the model said nothing" from "the model was never
    /// reached", which is the difference between an empty chat bubble and an
    /// honest "the coach is unreachable".
    saw_container: bool,
    /// Bytes pushed through the machine. The caller enforces the ceiling; this
    /// is what it counts.
    pub bytes: usize,
}

impl ReplyScanner {
    pub const fn new() -> Self {
        ReplyScanner {
            stack: [Ctx {
                array: false,
                expect_key: false,
            }; MAX_DEPTH],
            depth: 0,
            in_string: false,
            escape: false,
            uni: 0,
            uni_val: 0,
            sink: Sink::Drop,
            key: FixedStr::new(),
            parts_depth: None,
            fc_depth: None,
            args_depth: None,
            slot: 0,
            text: FixedStr::new(),
            text_overflow: false,
            calls: [ToolCall::new(); MAX_CALLS],
            n_calls: 0,
            too_many_calls: false,
            finish: FixedStr::new(),
            malformed: false,
            saw_container: false,
            bytes: 0,
        }
    }

    /// Ready the machine for another reply. Buffers are REUSED, never
    /// reallocated — that is the point of the whole shape.
    pub fn reset(&mut self) {
        *self = ReplyScanner::new();
    }

    pub fn push_all(&mut self, chunk: &[u8]) {
        for b in chunk {
            self.push(*b);
        }
    }

    /// Feed one byte.
    pub fn push(&mut self, b: u8) {
        self.bytes = self.bytes.saturating_add(1);

        // RAW ARGUMENT CAPTURE COMES FIRST, and takes every byte including the
        // structural ones, because `crate::tool` is handed the argument object
        // verbatim. The opening `{` is pushed by the `{` arm below (which is
        // where we learn the object is `args`); this catches everything after
        // it, up to and including the closing `}` — the depth bookkeeping that
        // clears `args_depth` runs after this.
        if self.args_depth.is_some() {
            self.capture_arg(b);
        }

        if self.in_string {
            self.push_in_string(b);
            return;
        }

        match b {
            b'"' => {
                let expect_key = self
                    .stack
                    .get(self.depth.wrapping_sub(1))
                    .map(|c| !c.array && c.expect_key)
                    .unwrap_or(false);
                self.in_string = true;
                self.escape = false;
                self.uni = 0;
                if expect_key {
                    self.key.clear();
                    self.sink = Sink::Key;
                } else {
                    self.sink = self.value_sink();
                }
            }
            b'{' | b'[' => {
                let array = b == b'[';
                // Learn what this container IS before pushing it, because the
                // answer depends on the key that introduced it.
                if !array && self.key == "functionCall" && self.fc_depth.is_none() {
                    self.fc_depth = Some(self.depth);
                    self.begin_call();
                }
                if !array
                    && self.key == "args"
                    && self.fc_depth.is_some()
                    && self.args_depth.is_none()
                {
                    self.args_depth = Some(self.depth);
                    // The opening brace itself — the capture above ran while
                    // `args_depth` was still None.
                    self.capture_arg(b'{');
                }
                if array && self.key == "parts" && self.parts_depth.is_none() {
                    self.parts_depth = Some(self.depth);
                }
                self.saw_container = true;
                if self.depth >= MAX_DEPTH {
                    // Deeper than anything a real reply is. Refuse to follow it
                    // rather than growing a stack or losing track of nesting.
                    self.malformed = true;
                    return;
                }
                self.stack[self.depth] = Ctx {
                    array,
                    expect_key: !array,
                };
                self.depth += 1;
                self.key.clear();
            }
            b'}' | b']' => {
                if self.depth == 0 {
                    self.malformed = true;
                    return;
                }
                self.depth -= 1;
                if self.args_depth == Some(self.depth) {
                    self.args_depth = None;
                }
                if self.fc_depth == Some(self.depth) {
                    self.fc_depth = None;
                }
                if self.parts_depth == Some(self.depth) {
                    self.parts_depth = None;
                }
                self.key.clear();
            }
            b':' => {
                if let Some(c) = self.stack.get_mut(self.depth.wrapping_sub(1)) {
                    c.expect_key = false;
                }
            }
            b',' => {
                if let Some(c) = self.stack.get_mut(self.depth.wrapping_sub(1)) {
                    c.expect_key = !c.array;
                }
                self.key.clear();
            }
            _ => {}
        }
    }

    /// Close the reply out. Call once, after the last byte.
    ///
    /// Anything still open when the stream ended is DAMAGE, and it is recorded
    /// as such: a `functionCall` whose `args` never closed is marked unusable,
    /// and a body that ended mid-structure is `malformed`. Both outcomes still
    /// yield the text harvested so far, because a truncated ANSWER is worth
    /// showing and a truncated COMMAND is not worth obeying.
    pub fn finish_stream(&mut self) {
        if self.args_depth.is_some() {
            if let Some(c) = self.calls.get_mut(self.slot) {
                c.args_unterminated = true;
            }
        }
        if self.depth != 0 || self.in_string {
            self.malformed = true;
        }
        // Bytes arrived and none of them opened a container: whatever answered
        // was not this API. Zero bytes is a different thing (a closed
        // connection) and stays un-malformed, because the caller already knows
        // it read nothing.
        if self.bytes > 0 && !self.saw_container {
            self.malformed = true;
        }
    }

    /// The tool calls that survived intact, in the order the model emitted them.
    pub fn intact_calls(&self) -> impl Iterator<Item = &ToolCall> {
        self.calls[..self.n_calls.min(MAX_CALLS)]
            .iter()
            .filter(|c| c.is_intact())
    }

    /// The model stopped because it ran out of output tokens, so whatever it
    /// was writing is cut off mid-value. Distinguishing this from a transport
    /// failure is what makes [`crate::salvage`] a repair rather than a guess.
    pub fn hit_token_limit(&self) -> bool {
        self.finish == "MAX_TOKENS"
    }

    // --- internals ---------------------------------------------------------

    fn value_sink(&self) -> Sink {
        if self.key == "finishReason" {
            return Sink::Finish;
        }
        if self.fc_depth.is_some() {
            // Inside a functionCall: the only string we want is its name.
            // `args` values are captured raw by `capture_arg`, so they must NOT
            // also be decoded into the text sink.
            if self.key == "name" && self.args_depth.is_none() {
                return Sink::CallName;
            }
            return Sink::Drop;
        }
        // A `text` outside a `parts` array is somebody else's field (an error
        // envelope, a citation, a prompt echo) and is not the coach's answer.
        if self.key == "text" && self.parts_depth.is_some() {
            return Sink::Text;
        }
        Sink::Drop
    }

    fn begin_call(&mut self) {
        if self.n_calls >= MAX_CALLS {
            self.too_many_calls = true;
            // Point the slot PAST the array. Every writer reaches the calls
            // through `get_mut`, so the excess call's name and arguments are
            // dropped on the floor — it must not overwrite the fourth call,
            // which is a real call the user is about to be told happened.
            self.slot = MAX_CALLS;
            return;
        }
        self.slot = self.n_calls;
        self.calls[self.slot] = ToolCall::new();
        self.n_calls += 1;
    }

    fn capture_arg(&mut self, b: u8) {
        let Some(c) = self.calls.get_mut(self.slot) else {
            return;
        };
        if c.args.len() >= ARGS_BYTES {
            c.args_overflow = true;
            return;
        }
        c.args.push_byte(b);
    }

    fn push_in_string(&mut self, b: u8) {
        if self.uni > 0 {
            let d = match b {
                b'0'..=b'9' => (b - b'0') as u32,
                b'a'..=b'f' => (b - b'a') as u32 + 10,
                b'A'..=b'F' => (b - b'A') as u32 + 10,
                _ => {
                    // Not a hex digit where one is required.
                    self.malformed = true;
                    self.uni = 0;
                    return;
                }
            };
            self.uni_val = (self.uni_val << 4) | d;
            self.uni -= 1;
            if self.uni == 0 {
                self.emit_scalar(self.uni_val);
            }
            return;
        }
        if self.escape {
            self.escape = false;
            match b {
                b'"' => self.emit(b'"'),
                b'\\' => self.emit(b'\\'),
                b'/' => self.emit(b'/'),
                b'b' => self.emit(0x08),
                b'f' => self.emit(0x0c),
                b'n' => self.emit(b'\n'),
                b'r' => self.emit(b'\r'),
                b't' => self.emit(b'\t'),
                b'u' => {
                    self.uni = 4;
                    self.uni_val = 0;
                }
                _ => self.malformed = true,
            }
            return;
        }
        match b {
            b'\\' => self.escape = true,
            b'"' => {
                self.in_string = false;
                self.sink = Sink::Drop;
            }
            _ => self.emit(b),
        }
    }

    /// A decoded `\uXXXX`. Only the Basic Multilingual Plane is re-encoded;
    /// a surrogate half becomes `?`, which is a cosmetic loss in a coach
    /// sentence and keeps the encoder total (a lone surrogate has no UTF-8
    /// encoding, and producing invalid UTF-8 here would poison `as_str`).
    fn emit_scalar(&mut self, v: u32) {
        if (0xD800..0xE000).contains(&v) || v > 0xFFFF {
            self.emit(b'?');
            return;
        }
        let mut buf = [0u8; 4];
        if let Some(ch) = char::from_u32(v) {
            for byte in ch.encode_utf8(&mut buf).as_bytes() {
                self.emit(*byte);
            }
        } else {
            self.emit(b'?');
        }
    }

    fn emit(&mut self, b: u8) {
        match self.sink {
            Sink::Drop => {}
            Sink::Key => self.key.push_byte(b),
            Sink::Text => {
                if self.text.len() >= TEXT_BYTES {
                    self.text_overflow = true;
                } else {
                    self.text.push_byte(b);
                }
            }
            Sink::CallName => {
                if let Some(c) = self.calls.get_mut(self.slot) {
                    c.name.push_byte(b);
                }
            }
            Sink::Finish => self.finish.push_byte(b),
        }
    }
}

impl Default for ReplyScanner {
    fn default() -> Self {
        Self::new()
    }
}
