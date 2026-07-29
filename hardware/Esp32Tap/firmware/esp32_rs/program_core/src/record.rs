//! What a stored program looks like on flash, and what it looks like on the
//! wire.
//!
//! # Why this is here and not in the storage tier
//!
//! `esp32tap::net::store` is bytes: files, sequences, a filesystem, no
//! knowledge of what it holds. A stored entry is *a `Program` plus the handful
//! of fields the app shows next to it*, so it can only live somewhere that
//! already knows what a `Program` is. Putting it here gets this codec into the
//! host test suite that already runs in 0.00 s, instead of proving a byte
//! format in QEMU.
//!
//! # Binary on flash, JSON on the wire — and why not JSON on both
//!
//! The worst-case program alone serialises to 1938 JSON bytes
//! (`model::max_program_json_bytes`), so storing the served JSON verbatim
//! would fit flash but NOT the 2048-byte `reqbudget` slot a request path is
//! allowed to use — the record could not be read without a second, larger
//! buffer whose whole purpose would be to exist per request. The binary form
//! below is ~900 bytes worst case, so a record is read into one request slot,
//! rendered, and forgotten. That is the property this tier exists to keep.
//!
//! # No wall clock, so no timestamps that pretend to be one
//!
//! This device has no RTC and no SNTP. `created_at`, `last_used`, `started_at`
//! and `ended_at` are therefore emitted as `""`/`null` rather than as a
//! plausible-looking date computed from uptime. Both are shapes the Kotlin
//! models already accept (`created_at: String = ""`, the rest `String?`), and
//! the app renders `usage_text`/`last_run_text` rather than raw timestamps —
//! so nothing is lost that the user can see, and nothing is invented.
//!
//! # One name, not two
//!
//! `python/db.py` keeps a workout's `name` column AND the `name` inside its
//! stored program JSON, and `rename_workout` must write both or they desync.
//! Here there is only `program.name`. A rename cannot half-apply.

use crate::model::{Program, MAX_PROGRAM_NAME};
use core::fmt::Write;
use safety_core::FixedStr;

/// Record ids. Short on purpose: they are opaque to the app, which only ever
/// echoes them back in a URL.
pub const MAX_ID: usize = 12;

/// The prompt that produced a program. The Pi allows 5000 characters (a Gemini
/// prompt); there is no Gemini here, the field is only ever echoed, and it
/// shares a slot with the program — so it is bounded to something a human
/// label fits in and truncated, not rejected.
pub const MAX_PROMPT: usize = 64;

/// Where a stored program came from. A CLOSED set: the Pi validates the same
/// three values on its write path, and an unknown string cannot be stored
/// because there is nothing here to store it in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Source {
    Manual,
    Generated,
    Gpx,
}

impl Source {
    pub const fn as_str(self) -> &'static str {
        match self {
            Source::Manual => "manual",
            Source::Generated => "generated",
            Source::Gpx => "gpx",
        }
    }
    pub fn parse(s: &str) -> Option<Source> {
        match s {
            "manual" => Some(Source::Manual),
            "generated" => Some(Source::Generated),
            "gpx" => Some(Source::Gpx),
            _ => None,
        }
    }
    const fn code(self) -> u8 {
        match self {
            Source::Manual => 0,
            Source::Generated => 1,
            Source::Gpx => 2,
        }
    }
    const fn from_code(c: u8) -> Source {
        match c {
            1 => Source::Generated,
            2 => Source::Gpx,
            _ => Source::Manual,
        }
    }
}

/// One stored program — a program-history entry or a saved workout. The two
/// differ only in which ring they live in and which fields the wire shape
/// shows, which is why they share a record rather than duplicating one.
#[derive(Clone, Copy, Debug)]
pub struct Entry {
    pub id: FixedStr<MAX_ID>,
    pub prompt: FixedStr<MAX_PROMPT>,
    pub source: Source,
    /// The program ran to the end. Gates `/resume`, exactly as `db.py`'s
    /// `completed` column does.
    pub completed: bool,
    pub last_interval: u32,
    pub last_elapsed_s: u32,
    /// Saved workouts only; a history entry leaves it 0.
    pub times_used: u32,
    pub program: Program,
}

impl Entry {
    pub fn new(id: &str, program: Program) -> Entry {
        Entry {
            id: FixedStr::from_str_truncating(id),
            prompt: FixedStr::new(),
            source: Source::Manual,
            completed: false,
            last_interval: 0,
            last_elapsed_s: 0,
            times_used: 0,
            program,
        }
    }

    pub fn name(&self) -> &str {
        self.program.name.as_str()
    }
}

/// Why a session ended. `in_progress` is the state a checkpointed run is left
/// in until it is finalised, so it is a variant here rather than an absence.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EndReason {
    InProgress,
    UserStop,
    ProgramComplete,
    Disconnect,
}

impl EndReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            EndReason::InProgress => "in_progress",
            EndReason::UserStop => "user_stop",
            EndReason::ProgramComplete => "program_complete",
            EndReason::Disconnect => "disconnect",
        }
    }
    const fn code(self) -> u8 {
        match self {
            EndReason::InProgress => 0,
            EndReason::UserStop => 1,
            EndReason::ProgramComplete => 2,
            EndReason::Disconnect => 3,
        }
    }
    const fn from_code(c: u8) -> EndReason {
        match c {
            1 => EndReason::UserStop,
            2 => EndReason::ProgramComplete,
            3 => EndReason::Disconnect,
            _ => EndReason::InProgress,
        }
    }
}

/// A program's identity for JOINING, ignoring its name.
///
/// `python/server.py::_program_fingerprint` is `"|".join(f"{speed},{incline},
/// {duration}")` with a docstring that says, in as many words, "ignoring name".
/// It is what links a run to the program it ran and a history entry to the
/// workout that saved it — and keying either of those on the NAME instead was a
/// real defect here: renaming a saved workout desynced the history row's heart
/// icon, and the app's next tap created a DUPLICATE workout (measured: two
/// records for one program, the older unreachable from the row that made it).
///
/// A 64-bit FNV-1a over the same three numbers per interval. Not the Pi's
/// string, because a string of 24 intervals does not belong in a fixed record
/// — and a hash is only ever a FILTER here: every caller verifies a hit
/// exactly with [`entry_matches_program`] before acting on it.
pub fn fingerprint(p: &Program) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for iv in p.intervals() {
        fold_interval(
            &mut h,
            iv.speed.get() as i64,
            iv.incline.get() as i64,
            iv.duration_s() as i64,
        );
    }
    h
}

/// One session. Integers throughout, in the smallest unit each field is shown
/// in, so there is no float anywhere on the storage path.
#[derive(Clone, Copy, Debug)]
pub struct Run {
    pub id: FixedStr<MAX_ID>,
    pub program_name: FixedStr<MAX_PROGRAM_NAME>,
    /// [`fingerprint`] of the program this run ran, so a history entry or a
    /// saved workout can find it. `program_fingerprint` on the Pi.
    pub fp: u64,
    pub elapsed_s: u32,
    /// Miles x 1000 — `db.py` rounds distance to 3 decimals.
    pub distance_milli: u32,
    /// Feet x 10 — rounded to 1 decimal, as `db.py` does.
    pub vert_feet_tenths: u32,
    /// kcal x 10.
    pub calories_tenths: u32,
    pub end_reason: EndReason,
    pub program_completed: bool,
    pub is_manual: bool,
}

impl Run {
    pub fn new(id: &str, program_name: &str, is_manual: bool) -> Run {
        Run {
            id: FixedStr::from_str_truncating(id),
            program_name: FixedStr::from_str_truncating(program_name),
            fp: 0,
            elapsed_s: 0,
            distance_milli: 0,
            vert_feet_tenths: 0,
            calories_tenths: 0,
            end_reason: EndReason::InProgress,
            program_completed: false,
            is_manual,
        }
    }
}

// ---------------------------------------------------------------------------
// Byte codec. Total in both directions: encode refuses rather than truncating,
// decode returns None for anything it does not fully understand. A record it
// cannot read is a record that is ABSENT — the same answer the storage tier
// gives for a slot it cannot open — never a partially-populated value.
// ---------------------------------------------------------------------------

/// Format version. Bumping it makes older records unreadable (they decode as
/// `None` and their slots are reused), which is the correct behaviour for
/// a device whose store is a cache of the user's recent work, not an archive.
const V_ENTRY: u8 = 1;
/// 2 -> 3 when `Run` gained its program fingerprint. Records written by an
/// older build decode as absent and their slots are reused, which is the
/// documented behaviour for a store that is a cache of recent work.
const V_RUN: u8 = 3;

struct Enc<'a> {
    buf: &'a mut [u8],
    n: usize,
}

impl Enc<'_> {
    fn u8(&mut self, v: u8) -> Option<()> {
        *self.buf.get_mut(self.n)? = v;
        self.n += 1;
        Some(())
    }
    fn u32(&mut self, v: u32) -> Option<()> {
        for b in v.to_le_bytes() {
            self.u8(b)?;
        }
        Some(())
    }
    fn u64(&mut self, v: u64) -> Option<()> {
        for b in v.to_le_bytes() {
            self.u8(b)?;
        }
        Some(())
    }
    fn i32(&mut self, v: i32) -> Option<()> {
        self.u32(v as u32)
    }
    /// A length-prefixed string. The length is one byte, so every string field
    /// in this format is bounded to 255 by the encoding itself.
    fn str(&mut self, s: &str) -> Option<()> {
        let b = s.as_bytes();
        if b.len() > u8::MAX as usize {
            return None;
        }
        self.u8(b.len() as u8)?;
        for &c in b {
            self.u8(c)?;
        }
        Some(())
    }
}

struct Dec<'a> {
    b: &'a [u8],
    i: usize,
}

impl Dec<'_> {
    fn u8(&mut self) -> Option<u8> {
        let v = *self.b.get(self.i)?;
        self.i += 1;
        Some(v)
    }
    fn u32(&mut self) -> Option<u32> {
        let mut a = [0u8; 4];
        for slot in a.iter_mut() {
            *slot = self.u8()?;
        }
        Some(u32::from_le_bytes(a))
    }
    fn i32(&mut self) -> Option<i32> {
        Some(self.u32()? as i32)
    }
    fn u64(&mut self) -> Option<u64> {
        let mut a = [0u8; 8];
        for slot in a.iter_mut() {
            *slot = self.u8()?;
        }
        Some(u64::from_le_bytes(a))
    }
    fn bool(&mut self) -> Option<bool> {
        Some(self.u8()? != 0)
    }
    /// Step over a length-prefixed string without materialising it.
    ///
    /// The scans that walk a record's INTERVALS — the fingerprint peek and the
    /// exact comparator — care only about the three numbers per interval, and
    /// interval names are the bulk of the bytes. Skipping them keeps those
    /// walks free of a `FixedStr` on the httpd task's stack.
    fn skip_str(&mut self) -> Option<()> {
        let len = self.u8()? as usize;
        if self.i + len > self.b.len() {
            return None;
        }
        self.i += len;
        Some(())
    }
    /// Read a length-prefixed string into a bounded destination. A string
    /// longer than the destination is TRUNCATED, not refused: the field is a
    /// label, and refusing would lose a whole workout to a cosmetic overrun
    /// after a bound was tightened.
    fn str<const N: usize>(&mut self, out: &mut FixedStr<N>) -> Option<()> {
        let len = self.u8()? as usize;
        if self.i + len > self.b.len() {
            return None;
        }
        out.clear();
        for k in 0..len {
            let c = self.b[self.i + k];
            if out.len() < N {
                out.push_byte(c);
            }
        }
        self.i += len;
        Some(())
    }
}

/// Worst-case encoded size of an [`Entry`], in bytes.
///
/// Computed, not measured, so it stays true when a bound moves — and asserted
/// against one `reqbudget` slot by a test below, because the request path
/// reads a record into exactly that.
pub const fn max_entry_bytes() -> usize {
    let header = 1 + 1 + 1 + 4 + 4 + 4; // version, source, flags, 3 x u32
    let ids = (1 + MAX_ID) + (1 + MAX_PROMPT);
    let program = 1 + 1 + (1 + MAX_PROGRAM_NAME); // manual, count, name
    let per_interval = (1 + crate::model::MAX_NAME) + 4 + 4 + 4;
    header + ids + program + per_interval * crate::model::MAX_INTERVALS
}

/// Encode an entry. Returns the length written, or `None` if `buf` is too
/// small.
pub fn encode_entry(e: &Entry, buf: &mut [u8]) -> Option<usize> {
    let mut w = Enc { buf, n: 0 };
    w.u8(V_ENTRY)?;
    w.u8(e.source.code())?;
    w.u8(u8::from(e.completed))?;
    w.u32(e.last_interval)?;
    w.u32(e.last_elapsed_s)?;
    w.u32(e.times_used)?;
    w.str(e.id.as_str())?;
    w.str(e.prompt.as_str())?;
    w.u8(u8::from(e.program.manual))?;
    w.u8(e.program.len() as u8)?;
    w.str(e.program.name.as_str())?;
    for iv in e.program.intervals() {
        w.str(iv.name.as_str())?;
        w.u32(iv.duration_s())?;
        w.i32(iv.speed.get())?;
        w.i32(iv.incline.get())?;
    }
    Some(w.n)
}

/// The identifying head of a record: everything needed to ANSWER "is this the
/// one?" and nothing else.
///
/// WHY THIS EXISTS. Finding a record by id or by name used to decode the whole
/// [`Entry`], which is ~1 KB of `Program` — and a caller that already held one
/// then had two live at once. Nested that way inside an HTTP handler it
/// overflowed the httpd task's stack and rebooted the device, which drops the
/// relay. A `Head` is ~80 bytes, so a scan over a full ring costs one of them
/// at a time and no interval is ever decoded to compare a name.
#[derive(Clone, Copy, Debug)]
pub struct Head {
    pub id: FixedStr<MAX_ID>,
    pub name: FixedStr<MAX_PROGRAM_NAME>,
    /// [`fingerprint`] of the stored program, computed while walking past the
    /// intervals rather than by decoding them into one. This is what a name
    /// CANNOT do: it is stable across a rename, which is why the saved/last-run
    /// joins key on it.
    pub fp: u64,
}

/// Read only the head of an encoded entry: identity, and nothing that costs a
/// `Program`.
///
/// It DOES walk the intervals now, to fold the fingerprint — but it holds one
/// `u64` while doing it, not a `[Interval; 24]`. That distinction is the whole
/// reason this function exists (see the type's note).
pub fn peek_entry(b: &[u8]) -> Option<Head> {
    let mut r = Dec { b, i: 0 };
    if r.u8()? != V_ENTRY {
        return None;
    }
    r.u8()?; // source
    r.bool()?; // completed
    r.u32()?; // last_interval
    r.u32()?; // last_elapsed_s
    r.u32()?; // times_used
    let mut id: FixedStr<MAX_ID> = FixedStr::new();
    let mut prompt: FixedStr<MAX_PROMPT> = FixedStr::new();
    r.str(&mut id)?;
    r.str(&mut prompt)?;
    r.bool()?; // manual
    let count = r.u8()? as usize;
    if count == 0 || count > crate::model::MAX_INTERVALS {
        return None;
    }
    let mut name: FixedStr<MAX_PROGRAM_NAME> = FixedStr::new();
    r.str(&mut name)?;
    let mut fp: u64 = 0xcbf2_9ce4_8422_2325;
    for _ in 0..count {
        r.skip_str()?; // interval name — not part of the fingerprint
        let duration = r.u32()?;
        let speed = r.i32()?;
        let incline = r.i32()?;
        fold_interval(&mut fp, speed as i64, incline as i64, duration as i64);
    }
    Some(Head { id, name, fp })
}

/// One interval's contribution to a fingerprint. ONE definition, used by both
/// [`fingerprint`] (over a live `Program`) and [`peek_entry`] (over the encoded
/// bytes) — two copies of this that drifted would make every join silently
/// stop matching.
fn fold_interval(h: &mut u64, speed: i64, incline: i64, duration: i64) {
    for v in [speed, incline, duration] {
        for b in v.to_le_bytes() {
            *h ^= b as u64;
            *h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
}

/// Whether an encoded entry's program has EXACTLY these intervals.
///
/// THE EXACT HALF of every fingerprint join. A 64-bit hash makes a collision
/// vanishingly unlikely, not impossible, and the consequence of one is not
/// cosmetic: the app would be handed the id of a DIFFERENT workout, which its
/// unsave button then deletes. So a hash hit is confirmed here, against the
/// bytes, before anything acts on it — the same shape the name-keyed version
/// had (hash to filter, string compare to confirm), with a key that survives a
/// rename.
///
/// "EXACTLY" IS OVER THE FINGERPRINT'S DOMAIN, and that is the contract rather
/// than a shortcut: duration, speed and incline per interval, in order. The
/// program's name, the `manual` flag and the interval names are deliberately
/// NOT compared, because `python/server.py::_program_fingerprint` does not
/// compare them either — two programs with the same numbers ARE the same
/// workout to the Pi, and matching the Pi is the whole point of this join. A
/// stricter comparison here would make the device disagree with the server the
/// app was written against, which is the failure this replaced.
pub fn entry_matches_program(b: &[u8], p: &Program) -> bool {
    let mut r = Dec { b, i: 0 };
    if r.u8() != Some(V_ENTRY) {
        return false;
    }
    let ok = (|| {
        r.u8()?; // source
        r.bool()?; // completed
        r.u32()?; // last_interval
        r.u32()?; // last_elapsed_s
        r.u32()?; // times_used
        r.skip_str()?; // id
        r.skip_str()?; // prompt
        r.bool()?; // manual
        let count = r.u8()? as usize;
        if count != p.len() {
            return Some(false);
        }
        r.skip_str()?; // program name — deliberately not compared
        for iv in p.intervals() {
            r.skip_str()?;
            if r.u32()? != iv.duration_s() {
                return Some(false);
            }
            if r.i32()? != iv.speed.get() {
                return Some(false);
            }
            if r.i32()? != iv.incline.get() {
                return Some(false);
            }
        }
        Some(true)
    })();
    ok == Some(true)
}

/// Decode an entry, or `None` for anything not fully understood.
pub fn decode_entry(b: &[u8]) -> Option<Entry> {
    let mut r = Dec { b, i: 0 };
    if r.u8()? != V_ENTRY {
        return None;
    }
    let source = Source::from_code(r.u8()?);
    let completed = r.bool()?;
    let last_interval = r.u32()?;
    let last_elapsed_s = r.u32()?;
    let times_used = r.u32()?;
    let mut id: FixedStr<MAX_ID> = FixedStr::new();
    let mut prompt: FixedStr<MAX_PROMPT> = FixedStr::new();
    r.str(&mut id)?;
    r.str(&mut prompt)?;
    let manual = r.bool()?;
    let count = r.u8()? as usize;
    if count > crate::model::MAX_INTERVALS {
        return None;
    }
    let mut name: FixedStr<MAX_PROGRAM_NAME> = FixedStr::new();
    r.str(&mut name)?;
    // The intervals are pushed straight into the entry's own program rather
    // than into a local that is then moved: a second `Program` on the stack is
    // ~900 bytes, and this decoder runs on the httpd task.
    let mut e = Entry {
        id,
        prompt,
        source,
        completed,
        last_interval,
        last_elapsed_s,
        times_used,
        program: Program::new(name.as_str(), manual),
    };
    for _ in 0..count {
        let mut iv_name: FixedStr<{ crate::model::MAX_NAME }> = FixedStr::new();
        r.str(&mut iv_name)?;
        let duration = r.u32()?;
        let speed = r.i32()?;
        let incline = r.i32()?;
        if !e.program.push(crate::Interval::new(
            iv_name.as_str(),
            duration,
            safety_core::units::SpeedTenths::new(speed),
            safety_core::units::InclineHalfPct::new(incline),
        )) {
            return None;
        }
    }
    // A record with no intervals is not a program; refuse it rather than hand
    // back something that renders as an empty workout in the app.
    if e.program.is_empty() {
        return None;
    }
    Some(e)
}

pub fn encode_run(rn: &Run, buf: &mut [u8]) -> Option<usize> {
    let mut w = Enc { buf, n: 0 };
    w.u8(V_RUN)?;
    w.u8(rn.end_reason.code())?;
    w.u8(u8::from(rn.program_completed))?;
    w.u8(u8::from(rn.is_manual))?;
    w.u32(rn.elapsed_s)?;
    w.u32(rn.distance_milli)?;
    w.u32(rn.vert_feet_tenths)?;
    w.u32(rn.calories_tenths)?;
    w.u64(rn.fp)?;
    w.str(rn.id.as_str())?;
    w.str(rn.program_name.as_str())?;
    Some(w.n)
}

pub fn decode_run(b: &[u8]) -> Option<Run> {
    let mut r = Dec { b, i: 0 };
    if r.u8()? != V_RUN {
        return None;
    }
    let end_reason = EndReason::from_code(r.u8()?);
    let program_completed = r.bool()?;
    let is_manual = r.bool()?;
    let elapsed_s = r.u32()?;
    let distance_milli = r.u32()?;
    let vert_feet_tenths = r.u32()?;
    let calories_tenths = r.u32()?;
    let fp = r.u64()?;
    let mut id: FixedStr<MAX_ID> = FixedStr::new();
    let mut program_name: FixedStr<MAX_PROGRAM_NAME> = FixedStr::new();
    r.str(&mut id)?;
    r.str(&mut program_name)?;
    Some(Run {
        id,
        program_name,
        fp,
        elapsed_s,
        distance_milli,
        vert_feet_tenths,
        calories_tenths,
        end_reason,
        program_completed,
        is_manual,
    })
}

// ---------------------------------------------------------------------------
// Wire shapes. Kotlin decodes these; the types are what matter.
//
// THE "EVERY FIELD HAS A DEFAULT" RULE IS TRUE OF THE THREE MODELS BELOW —
// `HistoryEntry`, `SavedWorkout`, `RunRecord` — AND OF NOTHING ELSE. For those,
// an OMITTED field passes silently while a WRONG TYPE throws. It is FALSE of
// `Program` (`name`, `intervals`), `Interval` (all four), `ProgramMessage`,
// `StatusMessage` and `SessionMessage`, each of which declares fields with no
// default at all: `coerceInputValues` rewrites an explicit null into a default
// that EXISTS, it cannot invent one, so an omission there is a thrown
// MissingFieldException. This header stated the rule universally, which is the
// dangerous direction to be wrong in — `json::write_state_with_lead` and
// `net::api::format_status` both get it right and say so, and a reader who
// trusted this version would believe dropping a field from either was safe.
//
// `program` and `last_run` are object-shaped and are emitted whole or as
// `null`, never as a scalar — a scalar there throws in every model.
// ---------------------------------------------------------------------------

/// `x / 10` rendered with one decimal, without floats.
fn write_tenths<W: Write>(w: &mut W, tenths: u32) -> core::fmt::Result {
    write!(w, "{}.{}", tenths / 10, tenths % 10)
}

/// `x / 1000` rendered with three decimals.
fn write_milli<W: Write>(w: &mut W, milli: u32) -> core::fmt::Result {
    write!(w, "{}.{:03}", milli / 1000, milli % 1000)
}

/// `m:ss` / `h:mm:ss` — `server.py::_fmt_dur`.
fn write_dur<W: Write>(w: &mut W, secs: u32) -> core::fmt::Result {
    let m = secs / 60;
    let s = secs % 60;
    if m >= 60 {
        write!(w, "{}:{:02}:{:02}", m / 60, m % 60, s)
    } else {
        write!(w, "{m}:{s:02}")
    }
}

/// `server.py::_last_run_text`, MINUS the relative-time half.
///
/// The Pi renders `Last run: 2d ago · 24:30 · 1.20 mi`; it composes the first
/// part from `ended_at` and a wall clock, and this device has neither. It emits
/// the other two parts rather than inventing an elapsed-since from uptime —
/// the Pi's own function drops any part that is blank and joins what is left,
/// so `Last run: 24:30 · 1.20 mi` is a shape its own formatter produces.
///
/// Returns whether anything was written, because `usage_text` composes on it.
fn write_last_run_text<W: Write>(w: &mut W, run: Option<&Run>) -> Result<bool, core::fmt::Error> {
    let Some(r) = run else {
        return Ok(false);
    };
    w.write_str("Last run: ")?;
    write_dur(w, r.elapsed_s)?;
    // `>= 0.01 mi` on the Pi, and two decimals there rather than the three
    // `distance` itself carries.
    if r.distance_milli >= 10 {
        write!(w, " \u{b7} {}.{:02} mi", r.distance_milli / 1000, (r.distance_milli % 1000) / 10)?;
    }
    Ok(true)
}

/// `HistoryEntry` — `python/server.py::api_get_history`'s element shape.
///
/// `saved`/`saved_workout_id` and `last_run` are both computed by the CALLER,
/// against the workouts and runs rings, and both are keyed on
/// [`fingerprint`] exactly as the Pi keys them — never on the program's name.
/// A name key meant a rename desynced the heart icon and the app's next tap
/// created a duplicate workout.
pub fn write_history_entry<W: Write>(
    w: &mut W,
    e: &Entry,
    saved_id: Option<&str>,
    last_run: Option<&Run>,
) -> core::fmt::Result {
    write!(w, r#"{{"id":"{}","prompt":"{}","program":"#, e.id.as_str(), e.prompt.as_str())?;
    crate::json::write_program(w, &e.program)?;
    write!(
        w,
        r#","created_at":"","total_duration":{},"completed":{},"last_interval":{},"last_elapsed":"#,
        e.program.total_duration_s(),
        e.completed,
        e.last_interval
    )?;
    write!(w, "{}.0", e.last_elapsed_s)?;
    match saved_id {
        Some(id) => write!(w, r#","saved":true,"saved_workout_id":"{id}""#)?,
        None => w.write_str(r#","saved":false,"saved_workout_id":null"#)?,
    }
    // OBJECT-SHAPED OR `null`, never a scalar: `HistoryEntry.lastRun` is a
    // `RunRecord?`, and a number here would throw rather than degrade.
    w.write_str(r#","last_run":"#)?;
    match last_run {
        Some(r) => write_run(w, r)?,
        None => w.write_str("null")?,
    }
    w.write_str(r#","last_run_text":""#)?;
    write_last_run_text(w, last_run)?;
    w.write_str(r#""}"#)
}

/// `SavedWorkout` — `python/server.py::api_get_workouts`'s element shape.
pub fn write_saved_workout<W: Write>(
    w: &mut W,
    e: &Entry,
    last_run: Option<&Run>,
) -> core::fmt::Result {
    write!(
        w,
        r#"{{"id":"{}","name":"{}","program":"#,
        e.id.as_str(),
        e.program.name.as_str()
    )?;
    crate::json::write_program(w, &e.program)?;
    write!(
        w,
        r#","created_at":"","source":"{}","prompt":"{}","times_used":{},"last_used":null,"total_duration":{}"#,
        e.source.as_str(),
        e.prompt.as_str(),
        e.times_used,
        e.program.total_duration_s()
    )?;
    w.write_str(r#","last_run":"#)?;
    match last_run {
        Some(r) => write_run(w, r)?,
        None => w.write_str("null")?,
    }
    w.write_str(r#","last_run_text":""#)?;
    write_last_run_text(w, last_run)?;
    w.write_str(r#"","usage_text":""#)?;
    write_usage_text(w, e.times_used, last_run)?;
    w.write_str(r#""}"#)
}

/// `server.py::_usage_text`, minus the relative-time half it composes from a
/// clock this device does not have.
///
/// The Pi prefers the run text and appends the total count; only with no run to
/// show does it fall back to "Used N times". This device could not do the first
/// half at all until runs were joined to programs by fingerprint — it emitted
/// the count and nothing else, so a saved workout the user had run ten times
/// still said nothing about any of them.
///
/// Blank for an unused, never-run workout, which is what makes
/// `WorkoutList.kt`'s `usageText.ifBlank { "Never used" }` render.
fn write_usage_text<W: Write>(
    w: &mut W,
    times_used: u32,
    last_run: Option<&Run>,
) -> core::fmt::Result {
    if write_last_run_text(w, last_run)? {
        if times_used > 1 {
            write!(w, " \u{b7} {times_used} runs total")?;
        }
        return Ok(());
    }
    match times_used {
        0 => Ok(()),
        1 => w.write_str("Used once"),
        n => write!(w, "Used {n} times"),
    }
}

/// `RunRecord` — `db.get_runs`'s row shape.
pub fn write_run<W: Write>(w: &mut W, r: &Run) -> core::fmt::Result {
    write!(w, r#"{{"id":"{}","started_at":null,"ended_at":null,"elapsed":"#, r.id.as_str())?;
    write!(w, "{}.0", r.elapsed_s)?;
    w.write_str(r#","distance":"#)?;
    write_milli(w, r.distance_milli)?;
    w.write_str(r#","vert_feet":"#)?;
    write_tenths(w, r.vert_feet_tenths)?;
    w.write_str(r#","calories":"#)?;
    write_tenths(w, r.calories_tenths)?;
    write!(
        w,
        r#","end_reason":"{}","program_name":"{}","program_completed":{},"is_manual":{}}}"#,
        r.end_reason.as_str(),
        r.program_name.as_str(),
        r.program_completed,
        r.is_manual
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::tests::fixture;

    fn render(f: impl Fn(&mut String) -> core::fmt::Result) -> String {
        let mut s = String::new();
        f(&mut s).unwrap();
        s
    }

    #[test]
    fn a_worst_case_entry_fits_one_request_slot() {
        // THE DERIVATION that lets a record be read into a `reqbudget` slot.
        // If this fails, the request path needs a buffer of its own per
        // request, which is the shape this whole tier exists to avoid.
        assert!(
            max_entry_bytes() <= crate::MAX_PROGRAM_JSON_BYTES,
            "worst-case entry is {} bytes, one request slot is {}",
            max_entry_bytes(),
            crate::MAX_PROGRAM_JSON_BYTES
        );
    }

    #[test]
    fn an_entry_round_trips_through_the_codec() {
        let mut e = Entry::new("h1", fixture());
        e.prompt = FixedStr::from_str_truncating("a 20 minute hill run");
        e.source = Source::Gpx;
        e.completed = true;
        e.last_interval = 2;
        e.last_elapsed_s = 195;
        e.times_used = 7;

        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        let back = decode_entry(&buf[..n]).unwrap();

        assert_eq!(back.id.as_str(), "h1");
        assert_eq!(back.prompt.as_str(), "a 20 minute hill run");
        assert_eq!(back.source, Source::Gpx);
        assert!(back.completed);
        assert_eq!(back.last_interval, 2);
        assert_eq!(back.last_elapsed_s, 195);
        assert_eq!(back.times_used, 7);
        assert_eq!(back.program.name.as_str(), "Test Workout");
        assert_eq!(back.program.len(), 3);
        assert_eq!(back.program.intervals()[1].speed.get(), 60);
        assert_eq!(back.program.intervals()[1].incline.get(), 6);
        assert_eq!(back.program.intervals()[1].name.as_str(), "Run");
        assert_eq!(back.program.total_duration_s(), 240);
    }

    #[test]
    fn a_maximal_entry_encodes_inside_the_computed_bound() {
        let mut p = Program::new(&"P".repeat(MAX_PROGRAM_NAME), true);
        for _ in 0..crate::model::MAX_INTERVALS {
            p.push(crate::Interval::new(
                &"n".repeat(crate::model::MAX_NAME),
                crate::model::MAX_DURATION_S,
                safety_core::units::SpeedTenths::new(120),
                safety_core::units::InclineHalfPct::new(30),
            ));
        }
        let mut e = Entry::new(&"i".repeat(MAX_ID), p);
        e.prompt = FixedStr::from_str_truncating(&"p".repeat(MAX_PROMPT));
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        assert!(n <= max_entry_bytes(), "{n} > {}", max_entry_bytes());
        let back = decode_entry(&buf[..n]).unwrap();
        assert_eq!(back.program.len(), crate::model::MAX_INTERVALS);
    }

    #[test]
    fn peek_agrees_with_decode_and_is_far_smaller() {
        let mut e = Entry::new("h7", fixture());
        e.prompt = FixedStr::from_str_truncating("hills");
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        let head = peek_entry(&buf[..n]).unwrap();
        let full = decode_entry(&buf[..n]).unwrap();
        assert_eq!(head.id.as_str(), full.id.as_str());
        assert_eq!(head.name.as_str(), full.name());
        // THE POINT: a scan that only needs identity must not carry a program.
        assert!(
            core::mem::size_of::<Head>() * 8 < core::mem::size_of::<Entry>(),
            "Head is {} bytes against Entry's {} — the saving that keeps a ring \
             scan off the httpd task's stack has gone",
            core::mem::size_of::<Head>(),
            core::mem::size_of::<Entry>()
        );
    }

    #[test]
    fn peek_refuses_the_same_records_decode_refuses() {
        let e = Entry::new("h1", fixture());
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        for cut in 0..n {
            if peek_entry(&buf[..cut]).is_some() {
                // A head may be readable before the whole record is, but it
                // must never be readable where the version says otherwise.
                assert_eq!(buf[0], V_ENTRY);
            }
        }
        buf[0] = 99;
        assert!(peek_entry(&buf[..n]).is_none());
    }

    #[test]
    fn a_truncated_record_decodes_as_absent_not_as_a_partial_entry() {
        let e = Entry::new("h1", fixture());
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        for cut in 0..n {
            assert!(
                decode_entry(&buf[..cut]).is_none(),
                "a {cut}-byte prefix decoded as a valid entry"
            );
        }
    }

    #[test]
    fn a_foreign_version_decodes_as_absent() {
        let e = Entry::new("h1", fixture());
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        buf[0] = 99;
        assert!(decode_entry(&buf[..n]).is_none());
        // ...and an entry is not a run, in either direction.
        buf[0] = V_ENTRY;
        assert!(decode_run(&buf[..n]).is_none());
    }

    #[test]
    fn encoding_refuses_a_buffer_that_is_too_small() {
        let e = Entry::new("h1", fixture());
        let mut small = [0u8; 16];
        assert!(encode_entry(&e, &mut small).is_none());
    }

    #[test]
    fn a_run_round_trips_and_carries_its_reason() {
        let mut r = Run::new("r1", "Test Workout", false);
        r.elapsed_s = 1234;
        r.distance_milli = 2075;
        r.vert_feet_tenths = 431;
        r.calories_tenths = 1502;
        r.end_reason = EndReason::ProgramComplete;
        r.program_completed = true;
        let mut buf = [0u8; 512];
        let n = encode_run(&r, &mut buf).unwrap();
        let back = decode_run(&buf[..n]).unwrap();
        assert_eq!(back.end_reason, EndReason::ProgramComplete);
        assert_eq!(back.distance_milli, 2075);
        assert!(back.program_completed);
        assert!(!back.is_manual);
        assert_eq!(back.program_name.as_str(), "Test Workout");
    }

    // --- wire shapes ------------------------------------------------------
    //
    // These assert TYPES, not merely presence. Every field of the THREE models
    // rendered here (`HistoryEntry`, `SavedWorkout`, `RunRecord`) has a kotlinx
    // default, so an omission degrades silently while a wrong scalar type on an
    // object-shaped field (`program`, `last_run`) throws and breaks the screen.
    // The rule stops at those three — see the section header above.

    #[test]
    fn a_history_entry_carries_every_field_the_app_declares() {
        let mut e = Entry::new("h1", fixture());
        e.last_elapsed_s = 42;
        let s = render(|w| write_history_entry(w, &e, None, None));
        assert!(s.starts_with(r#"{"id":"h1","prompt":"","program":{"name":"Test Workout""#), "{s}");
        assert!(s.contains(r#""total_duration":240"#), "{s}");
        assert!(s.contains(r#""completed":false"#), "{s}");
        assert!(s.contains(r#""last_interval":0"#), "{s}");
        assert!(s.contains(r#""last_elapsed":42.0"#), "{s}");
        assert!(s.contains(r#""saved":false,"saved_workout_id":null"#), "{s}");
        assert!(s.contains(r#""last_run":null,"last_run_text":""#), "{s}");
        assert!(s.contains(r#""created_at":"""#), "{s}");
        // `intervals` must be present INSIDE program: it has no kotlinx
        // default, so dropping it throws MissingFieldException.
        assert!(s.contains(r#""intervals":[{"name":"Warmup""#), "{s}");
    }

    #[test]
    fn a_fingerprint_survives_a_rename_and_changes_with_the_intervals() {
        // THE WHOLE POINT. Keying the saved/last-run joins on the NAME meant a
        // rename desynced the history heart icon and the app's next tap created
        // a duplicate workout.
        let mut a = fixture();
        let fp = fingerprint(&a);
        a.name = FixedStr::from_str_truncating("Renamed Entirely");
        assert_eq!(fingerprint(&a), fp, "a rename moved the fingerprint");

        // ...and it is not a constant: a changed interval must NOT match.
        let mut b = Program::new("Test Workout", false);
        for (i, iv) in fixture().intervals().iter().enumerate() {
            let mut iv = *iv;
            if i == 1 {
                iv.speed = safety_core::units::SpeedTenths::new(999);
            }
            b.push(iv);
        }
        assert_ne!(fingerprint(&b), fp);

        // The encoded form agrees with the live form, in both directions.
        let e = Entry::new("h1", a);
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();
        assert_eq!(peek_entry(&buf[..n]).unwrap().fp, fp);
        assert!(entry_matches_program(&buf[..n], &fixture()));
        assert!(!entry_matches_program(&buf[..n], &b));
    }

    #[test]
    fn the_exact_comparator_refuses_a_shorter_or_longer_program() {
        // A hash hit is CONFIRMED against the bytes, so the comparator has to
        // be right about length too — a prefix match would hand the app the id
        // of a different workout, which its unsave button then deletes.
        let e = Entry::new("w1", fixture());
        let mut buf = [0u8; 4080];
        let n = encode_entry(&e, &mut buf).unwrap();

        let mut shorter = Program::new("x", false);
        shorter.push(fixture().intervals()[0]);
        assert!(!entry_matches_program(&buf[..n], &shorter));

        let mut longer = fixture();
        longer.push(crate::Interval::new(
            "extra",
            60,
            safety_core::units::SpeedTenths::new(30),
            safety_core::units::InclineHalfPct::new(0),
        ));
        assert!(!entry_matches_program(&buf[..n], &longer));

        // Garbage in is `false`, never a panic and never a match.
        assert!(!entry_matches_program(&[], &fixture()));
        assert!(!entry_matches_program(&buf[..3], &fixture()));
    }

    #[test]
    fn a_history_entry_carries_the_run_that_ran_it() {
        let e = Entry::new("h1", fixture());
        let mut r = Run::new("r7", "Test Workout", false);
        r.elapsed_s = 1470; // 24:30
        r.distance_milli = 1204; // 1.20 mi
        r.end_reason = EndReason::UserStop;
        let s = render(|w| write_history_entry(w, &e, None, Some(&r)));
        // OBJECT-shaped, because `HistoryEntry.lastRun` is a `RunRecord?` and a
        // scalar would throw rather than degrade.
        assert!(s.contains(r#""last_run":{"id":"r7""#), "{s}");
        // No relative time — this device has no clock and does not invent one.
        assert!(s.contains(r#""last_run_text":"Last run: 24:30 · 1.20 mi""#.replace("\\u00b7", "\u{b7}").as_str()), "{s}");
        // ...and `null`/`""` when there is no run, which is what the app's
        // `if (entry.lastRunText.isNotBlank())` keys on.
        let s = render(|w| write_history_entry(w, &e, None, None));
        assert!(s.contains(r#""last_run":null,"last_run_text":""}"#), "{s}");
    }

    #[test]
    fn usage_text_prefers_the_run_and_keeps_the_total() {
        let mut e = Entry::new("w1", fixture());
        e.times_used = 3;
        let mut r = Run::new("r1", "Test Workout", false);
        r.elapsed_s = 605; // 10:05
        r.distance_milli = 5; // under 0.01 mi — the Pi drops the distance part
        let s = render(|w| write_saved_workout(w, &e, Some(&r)));
        assert!(s.contains(r#""usage_text":"Last run: 10:05 \u{b7} 3 runs total""#.replace("\\u{b7}", "\u{b7}").as_str()), "{s}");
        // One run: no "N runs total" tail, exactly as `server.py::_usage_text`.
        e.times_used = 1;
        let s = render(|w| write_saved_workout(w, &e, Some(&r)));
        assert!(s.contains(r#""usage_text":"Last run: 10:05""#), "{s}");
        // Never run: the count, and blank when there is not even one — which
        // is what makes `WorkoutList.kt` render its own "Never used".
        e.times_used = 0;
        assert!(render(|w| write_saved_workout(w, &e, None)).contains(r#""usage_text":""#));
    }

    #[test]
    fn a_run_round_trips_its_fingerprint() {
        let mut r = Run::new("r1", "Hills", true);
        r.fp = fingerprint(&fixture());
        let mut buf = [0u8; 512];
        let n = encode_run(&r, &mut buf).unwrap();
        assert_eq!(decode_run(&buf[..n]).unwrap().fp, r.fp);
        // A run written by the pre-fingerprint build decodes as ABSENT rather
        // than as a run with a plausible-looking zero fingerprint, which would
        // join itself to every program that happens to hash to 0.
        buf[0] = 2;
        assert!(decode_run(&buf[..n]).is_none());
    }

    #[test]
    fn a_saved_history_entry_names_the_workout_that_saved_it() {
        let e = Entry::new("h1", fixture());
        let s = render(|w| write_history_entry(w, &e, Some("w9"), None));
        assert!(s.contains(r#""saved":true,"saved_workout_id":"w9""#), "{s}");
    }

    #[test]
    fn a_saved_workout_carries_every_field_the_app_declares() {
        let mut e = Entry::new("w1", fixture());
        e.source = Source::Generated;
        e.times_used = 3;
        let s = render(|w| write_saved_workout(w, &e, None));
        assert!(s.contains(r#""name":"Test Workout""#), "{s}");
        assert!(s.contains(r#""source":"generated""#), "{s}");
        assert!(s.contains(r#""times_used":3"#), "{s}");
        assert!(s.contains(r#""last_used":null"#), "{s}");
        assert!(s.contains(r#""total_duration":240"#), "{s}");
        assert!(s.contains(r#""usage_text":"Used 3 times""#), "{s}");
        // The app renders "Never used" itself when this is blank.
        e.times_used = 0;
        assert!(render(|w| write_saved_workout(w, &e, None)).contains(r#""usage_text":""#));
        e.times_used = 1;
        assert!(render(|w| write_saved_workout(w, &e, None)).contains(r#""usage_text":"Used once""#));
    }

    #[test]
    fn a_run_renders_the_decimal_shapes_the_pi_writes() {
        let mut r = Run::new("r1", "Hills", true);
        r.elapsed_s = 1234;
        r.distance_milli = 2075; // 2.075 miles, 3 decimals like db.py
        r.vert_feet_tenths = 431; // 43.1 feet, 1 decimal
        r.calories_tenths = 1502;
        r.end_reason = EndReason::UserStop;
        let s = render(|w| write_run(w, &r));
        assert!(s.contains(r#""elapsed":1234.0"#), "{s}");
        assert!(s.contains(r#""distance":2.075"#), "{s}");
        assert!(s.contains(r#""vert_feet":43.1"#), "{s}");
        assert!(s.contains(r#""calories":150.2"#), "{s}");
        assert!(s.contains(r#""end_reason":"user_stop""#), "{s}");
        assert!(s.contains(r#""program_name":"Hills""#), "{s}");
        assert!(s.contains(r#""program_completed":false,"is_manual":true"#), "{s}");
        assert!(s.contains(r#""started_at":null,"ended_at":null"#), "{s}");
        // A sub-mile distance must not render as `0.75` — three decimals,
        // zero-padded, or the app reads 75 thousandths as 750.
        r.distance_milli = 75;
        assert!(render(|w| write_run(w, &r)).contains(r#""distance":0.075"#));
    }

    #[test]
    fn every_stored_string_is_already_json_safe() {
        // Names are sanitised by `json::parse_program` on the way in, and ids
        // are minted here — so nothing on this path can emit a raw quote. This
        // test is the standing check on that claim.
        let mut p = crate::json::parse_program(
            br#"{"name":"He said \"hi\"","intervals":[{"name":"a\\b","duration":60,"speed":3,"incline":0}]}"#,
        )
        .unwrap();
        p.manual = true;
        let e = Entry::new("h1", p);
        let s = render(|w| write_saved_workout(w, &e, None));
        assert_eq!(s.matches('"').count() % 2, 0, "unbalanced quotes: {s}");
        assert!(!s.contains(r#"said ""#), "{s}");
    }
}
