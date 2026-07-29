//! The persistence tier — LittleFS files over the real `storage` partition.
//!
//! WHAT LIVES HERE, and the caps, mirroring what `python/db.py` owns:
//!   * program history — the last few loaded/generated programs, newest first
//!   * saved workouts  — favourites the user keeps deliberately
//!   * run records     — one per session, checkpointed while it runs
//!
//! # Why a filesystem, after a hand-rolled store was written and deleted
//!
//! This tier used to be `recstore`: fixed-size slots in raw flash, with a
//! magic, a monotonic sequence, a length and a CRC32 per slot. It was ~200
//! lines and it produced TWO REAL DEFECTS WITHIN AN HOUR, both caught only by
//! its own torn-write test:
//!
//!   * slots packed 16 to a 4 KB sector meant erasing one destroyed the other
//!     15, because NOR erase granularity is the sector and not the slot;
//!   * a torn header left the erased `0xFFFFFFFF` sequence reading as the
//!     NEWEST valid record, with an unchecked `seq + 1` behind it that would
//!     overflow — and this build is `panic = abort`, so that reboots the
//!     device and DROPS THE RELAY.
//!
//! LittleFS has neither by construction: it owns erase granularity, and a
//! commit is atomic or it did not happen. The recorded reason for not using it
//! was never a design argument, only plumbing — esp-idf-sys generates no
//! symbols for a third-party component without a `bindings_header`, and the
//! first attempt at declaring one put the key on the
//! `[package.metadata.esp-idf-sys]` TABLE, where it does not exist, instead of
//! on an `extra_components` ENTRY, where it does. See `bindings/littlefs.h`
//! and the Cargo.toml note. `recstore` is deleted, not kept beside this:
//! deleted code has no bugs.
//!
//! # What this module still owns, and why it is not a second store
//!
//! A filesystem has no notion of "the third-newest record", and record order
//! is what every list endpoint is built on. So each record file carries a
//! 4-byte little-endian SEQUENCE NUMBER ahead of its payload, and this module
//! keeps those sequences — and nothing else — in RAM. That is ordering
//! metadata a filesystem genuinely does not provide. It is emphatically NOT a
//! second attempt at the parts littlefs already does: there is no magic, no
//! length field, no CRC and no torn-write recovery here, because integrity is
//! the filesystem's job now.
//!
//! # One atomic rename per write — strictly safer than what it replaces
//!
//! Every write goes to a temp file, is closed, and is then RENAMED over the
//! destination. `lfs_rename` is a single metadata commit that both installs
//! the new name and removes the old one, so a power cut leaves the slot at its
//! PREVIOUS content — never half-written, never absent, never ambiguous.
//! `recstore` could not offer that: its update path erased the slot first, so
//! a cut lost the record being updated (it documented this as an acceptable
//! bounded loss). There is no such window here.
//!
//! # MEMORY IS THE DESIGN, not a consideration
//!
//! No parsed document is ever retained across a request. That is the property
//! whose absence killed the C++ tier, where documents were held per store and
//! ~15 unauthenticated requests could exhaust the heap and reboot the device
//! mid-run. A record is read into the caller's `reqbudget` slot, decoded into
//! a value, and the slot is released.
//!
//! WHAT A FILESYSTEM COSTS, STATED IN BYTES RATHER THAN WAVED AT. The three
//! `recstore` rings held 200 bytes between them. The three indexes below hold
//! [`INDEX_BYTES`] — the same array of sequence numbers, so the same order of
//! magnitude — but the MOUNT is not free: littlefs keeps a read cache, a
//! program cache and a lookahead bitmap, plus `lfs_t` and the esp_littlefs
//! wrapper, for the life of the mount. Those buffers are sized explicitly in
//! `sdkconfig.defaults` (128 + 128 + 32 bytes) rather than defaulted, and the
//! REST of it is measured rather than estimated: `mount_once` samples the free
//! heap either side of `esp_vfs_littlefs_register` and latches the difference,
//! so `Stores::resident_bytes()` — what `QT store_stat` reports and what
//! `test_records.py` asserts does not move — is the real figure for this
//! device rather than an arithmetic hope. It is a CONSTANT: it is paid once at
//! boot and does not grow with stored volume, record size or request count.
//!
//! ## Divergences from `python/db.py`, all deliberate
//!
//! * **Order is write order, not `created_at`/`last_used_at`.** The sequence
//!   knows write order; there is no clock here to sort by (and deliberately no
//!   `mtime` — see the sdkconfig note). Updating a record makes it newest, so
//!   "most recently touched first" is what a client sees.
//! * **Caps are the indexes'.** History's 20 matches `db.MAX_HISTORY`;
//!   workouts are capped at 20 where the Pi has no cap, and runs at 4 where
//!   the Pi keeps 200.
//! * **One profile**, so every ownership check in `server.py` is vacuous and
//!   none is ported.

use esp_idf_sys as sys;
use program_core::record::{self, Entry, Run};
use safety_core::FixedStr;
use std::fs;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

/// Records per index. Program history matches python/db.py's MAX_HISTORY (20).
pub const HISTORY_SLOTS: usize = 20;
pub const WORKOUT_SLOTS: usize = 20;
/// Run records: fewer than the Pi keeps (it has a real disk).
pub const RUN_SLOTS: usize = 4;

/// The VFS mount point. Short on purpose: it is a prefix on every path this
/// module builds into a fixed-size stack buffer.
const BASE: &str = "/rec";
/// The C form of [`BASE`], for the one FFI call that needs it.
const BASE_C: &core::ffi::CStr = c"/rec";
/// The partition declared in partitions_esp32tap.csv. `esp_littlefs` looks it
/// up with `SUBTYPE_ANY`, so the `spiffs` subtype in the CSV is not a problem.
const PARTITION_C: &core::ffi::CStr = c"storage";

/// The staging file every write passes through. ONE fixed name, reused: it is
/// only ever the source of a rename, so a crash leaves it as garbage that the
/// next write truncates. It is not a slot name, so no scan can mistake it for
/// a record.
const TMP: &str = "/rec/t";

/// Bytes of ordering metadata this module prepends to a record.
const SEQ: usize = 4;

/// Longest path this module builds: `/rec/` + tag + two digits.
const MAX_PATH: usize = 16;

/// Bytes the three indexes keep in RAM, independent of what is stored.
pub const INDEX_BYTES: usize = core::mem::size_of::<Index<HISTORY_SLOTS>>()
    + core::mem::size_of::<Index<WORKOUT_SLOTS>>()
    + core::mem::size_of::<Index<RUN_SLOTS>>();

/// What `esp_vfs_littlefs_register` cost, measured at mount. Latched once and
/// never updated, so [`Stores::resident_bytes`] is constant for the life of
/// the boot — which is exactly what `test_records.py` asserts.
static FS_BYTES: AtomicUsize = AtomicUsize::new(0);

// ---------------------------------------------------------------------------
// Paths and file primitives.
//
// EVERY OPERATION OPENS, ACTS AND CLOSES. Nothing holds a descriptor between
// calls: an open file costs littlefs a `cache_size` buffer plus its own state,
// and "resident memory does not grow" is easier to keep than to prove.
// ---------------------------------------------------------------------------

/// `/rec/<tag><slot>` — the file backing one record slot.
fn path_of(tag: u8, slot: usize) -> FixedStr<MAX_PATH> {
    let mut s: FixedStr<MAX_PATH> = FixedStr::new();
    s.push_str(BASE);
    s.push_byte(b'/');
    s.push_byte(tag);
    s.push_i64(slot as i64);
    s
}

/// The sequence number stored at the head of a slot file, or `None` if the
/// slot is empty, unreadable, or too short to carry one.
///
/// A zero sequence is treated as absent: 0 is this module's "no record", so a
/// file that somehow claims it is not a record.
fn read_seq(path: &str) -> Option<u32> {
    let mut f = fs::File::open(path).ok()?;
    let mut hdr = [0u8; SEQ];
    f.read_exact(&mut hdr).ok()?;
    match u32::from_le_bytes(hdr) {
        0 => None,
        seq => Some(seq),
    }
}

/// THE ONE WRITE PATH. Stage into [`TMP`], close it, then rename it over
/// `path`.
///
/// The rename is what makes this safe, and it is the reason this module is
/// smaller than the store it replaced: `lfs_rename` is a single atomic
/// metadata commit, so the slot is either the old record or the new one at
/// every instant. There is no window in which the record is missing (which is
/// what `recstore`'s erase-then-write update had) and none in which two files
/// claim it (which is what the opposite ordering would have).
///
/// The file is CLOSED before the rename, not merely dropped at end of scope:
/// `vfs_littlefs_rename` refuses outright if either name is open.
fn write_slot(path: &str, seq: u32, payload: &[u8]) -> bool {
    {
        let Ok(mut f) = fs::File::create(TMP) else {
            return false;
        };
        if f.write_all(&seq.to_le_bytes()).is_err() || f.write_all(payload).is_err() {
            return false;
        }
    }
    fs::rename(TMP, path).is_ok()
}

/// Read a slot's payload (the bytes after the sequence) into `buf`.
///
/// A record LONGER than the caller's buffer reads as absent rather than as a
/// truncated record: half a record decodes to nonsense or, worse, to something
/// plausible.
fn read_slot(path: &str, buf: &mut [u8]) -> Option<usize> {
    let mut f = fs::File::open(path).ok()?;
    let mut hdr = [0u8; SEQ];
    f.read_exact(&mut hdr).ok()?;
    let mut n = 0usize;
    while n < buf.len() {
        match f.read(&mut buf[n..]) {
            Ok(0) => return Some(n),
            Ok(k) => n += k,
            Err(_) => return None,
        }
    }
    // The buffer filled exactly; one more byte decides whether that was the
    // whole record or the start of one that does not fit.
    let mut over = [0u8; 1];
    match f.read(&mut over) {
        Ok(0) => Some(n),
        _ => None,
    }
}

/// Whether a slot already holds EXACTLY `payload`.
///
/// EXISTS TO SKIP A WRITE, and that is a wear bound rather than a performance
/// one. Every write here is a file rewrite plus a rename, and both endpoints
/// that reach it are unauthenticated: one client POSTing one identical program
/// in a loop would otherwise spend flash endurance one request at a time, and
/// an app retry loop produces the same shape by accident.
///
/// Compares through a fixed 64-byte window rather than reading the record into
/// a second buffer — needing a record-sized buffer to discover that a write
/// can be skipped would cost more memory than the write saves.
fn slot_equals(path: &str, payload: &[u8]) -> bool {
    let Ok(mut f) = fs::File::open(path) else {
        return false;
    };
    let mut hdr = [0u8; SEQ];
    if f.read_exact(&mut hdr).is_err() {
        return false;
    }
    let mut win = [0u8; 64];
    let mut i = 0usize;
    while i < payload.len() {
        let take = core::cmp::min(win.len(), payload.len() - i);
        if f.read_exact(&mut win[..take]).is_err() {
            return false;
        }
        if win[..take] != payload[i..i + take] {
            return false;
        }
        i += take;
    }
    // A stored record that merely STARTS with `payload` is not equal to it.
    matches!(f.read(&mut win[..1]), Ok(0))
}

// ---------------------------------------------------------------------------
// The index: which slots hold records, and in what order.
// ---------------------------------------------------------------------------

/// `SLOTS` record files under one tag, ordered by sequence.
///
/// THIS IS THE ENTIRE RESIDENT FOOTPRINT of a record set. It does not grow
/// with what is stored, with record size, or with request count.
///
/// Why an index at all, when the filesystem knows its own directory: every
/// by-position operation would otherwise be a directory scan, and `find_pos`
/// performs one per slot — so a single list request would cost `SLOTS`
/// squared directory reads. This array is the directory listing, cached, at
/// four bytes per slot.
pub struct Index<const SLOTS: usize> {
    /// Sequence number per slot; 0 means the slot holds no record.
    seqs: [u32; SLOTS],
    next_seq: u32,
    /// The filename character that distinguishes this set from the others.
    tag: u8,
}

impl<const SLOTS: usize> Index<SLOTS> {
    /// Rebuild the index by reading each slot file's 4-byte head.
    ///
    /// `SLOTS` opens of 4 bytes each — 44 across the whole tier — so boot cost
    /// does not grow with what is stored. Slots whose file is absent or
    /// unreadable stay 0 and are the first reused.
    fn mount(tag: u8) -> Index<SLOTS> {
        let mut ix = Index {
            seqs: [0; SLOTS],
            next_seq: 1,
            tag,
        };
        for i in 0..SLOTS {
            if let Some(seq) = read_seq(path_of(tag, i).as_str()) {
                ix.seqs[i] = seq;
                if seq >= ix.next_seq {
                    // Saturating, not `+ 1`: this build is panic=abort, so an
                    // overflow here would reboot the device and drop the relay.
                    ix.next_seq = seq.saturating_add(1);
                }
            }
        }
        ix
    }

    /// The sequence the NEXT write will be given.
    ///
    /// Exposed so a caller can mint a record id BEFORE the record is written —
    /// an id has to be inside the payload, so it cannot come from the return
    /// value of `append`. It is rebuilt from flash at mount, so ids stay
    /// unique across reboots without a second counter to keep in step.
    fn next_seq(&self) -> u32 {
        self.next_seq
    }

    /// Number of records held.
    fn len(&self) -> usize {
        self.seqs.iter().filter(|s| **s != 0).count()
    }

    /// Slot holding the record with the nth-highest sequence (0 = newest).
    ///
    /// ONE ordering, used by every by-position operation. `read_nth` and
    /// `erase_nth` disagreeing about what "the third record" means would
    /// delete a different record than the one the caller just read — the kind
    /// of defect that only shows up once a user has data.
    ///
    /// A selection scan rather than a sort: sequences are unique within an
    /// index (they are minted monotonically), `SLOTS` is 20, and a sort would
    /// need a scratch array of pairs on a stack that has already overflowed
    /// once in this firmware.
    fn nth_slot(&self, n: usize) -> Option<usize> {
        if n >= SLOTS {
            return None;
        }
        let mut ceiling = u32::MAX;
        let mut found = None;
        for _ in 0..=n {
            let mut best = 0u32;
            found = None;
            for i in 0..SLOTS {
                let s = self.seqs[i];
                if s != 0 && s < ceiling && s > best {
                    best = s;
                    found = Some(i);
                }
            }
            found?;
            ceiling = best;
        }
        found
    }

    /// Slot holding the record with the LOWEST sequence — the eviction victim.
    fn oldest_slot(&self) -> Option<usize> {
        let mut best = u32::MAX;
        let mut found = None;
        for i in 0..SLOTS {
            let s = self.seqs[i];
            if s != 0 && s <= best {
                best = s;
                found = Some(i);
            }
        }
        found
    }

    fn read_nth(&self, n: usize, buf: &mut [u8]) -> Option<usize> {
        let slot = self.nth_slot(n)?;
        read_slot(path_of(self.tag, slot).as_str(), buf)
    }

    fn nth_equals(&self, n: usize, payload: &[u8]) -> bool {
        match self.nth_slot(n) {
            Some(slot) => slot_equals(path_of(self.tag, slot).as_str(), payload),
            None => false,
        }
    }

    /// Write `payload` into `slot`, giving it a fresh (newest) sequence.
    ///
    /// The ONE write path — `append` and `replace_nth` differ only in how they
    /// choose the slot.
    fn write_at(&mut self, slot: usize, payload: &[u8]) -> Option<u32> {
        if slot >= SLOTS {
            return None;
        }
        // SEQUENCE EXHAUSTION IS REFUSED, NOT WRAPPED. Ordering IS the
        // sequence, so wrapping back to 1 would make every new record sort
        // OLDER than every pre-wrap one, and a full index would then evict the
        // record it had just written, forever — a silent, permanent corruption
        // of the newest-first contract. At one write every 30 s this is ~4000
        // years away; refusing costs nothing and makes the code total.
        if self.next_seq >= u32::MAX - 1 {
            return None;
        }
        let seq = self.next_seq;
        if !write_slot(path_of(self.tag, slot).as_str(), seq, payload) {
            return None;
        }
        self.seqs[slot] = seq;
        self.next_seq = seq + 1;
        Some(seq)
    }

    /// Append a record, evicting the OLDEST if every slot is taken.
    fn append(&mut self, payload: &[u8]) -> Option<u32> {
        let slot = match self.seqs.iter().position(|s| *s == 0) {
            Some(i) => i,
            // The victim's file is not deleted first: `write_at` renames the
            // new record over it in one commit, so the eviction and the
            // insertion are the same atomic act.
            None => self.oldest_slot()?,
        };
        self.write_at(slot, payload)
    }

    /// Overwrite the record with the nth-highest sequence, IN ITS OWN SLOT.
    ///
    /// It becomes the newest record: this tier has no notion of created-at,
    /// only of write order, and "most recently touched first" is what the
    /// lobby's recent list shows.
    fn replace_nth(&mut self, n: usize, payload: &[u8]) -> bool {
        match self.nth_slot(n) {
            Some(slot) => self.write_at(slot, payload).is_some(),
            None => false,
        }
    }

    /// Remove the record with the nth-highest sequence. Returns whether one
    /// was there.
    ///
    /// DELETE-BY-POSITION IS THE ONLY DELETE. A record's identity (an id, a
    /// name) is the caller's concern; this type knows only sequences.
    fn erase_nth(&mut self, n: usize) -> bool {
        let Some(slot) = self.nth_slot(n) else {
            return false;
        };
        // The index entry is cleared whether or not the unlink reported
        // success: an entry that survives a failed delete would be read back
        // through a path that is no longer there on the next boot anyway, and
        // a stale "this slot has a record" is the state that hides a slot from
        // reuse forever.
        let removed = fs::remove_file(path_of(self.tag, slot).as_str()).is_ok();
        self.seqs[slot] = 0;
        removed
    }
}

// ---------------------------------------------------------------------------
// The mounted store.
// ---------------------------------------------------------------------------

/// All three record sets, mounted once at boot.
pub struct Stores {
    pub history: Index<HISTORY_SLOTS>,
    pub workouts: Index<WORKOUT_SLOTS>,
    pub runs: Index<RUN_SLOTS>,
}

/// Bring LittleFS up on the `storage` partition, and MEASURE what it cost.
///
/// `format_if_mount_failed` is what makes a blank part (and a part still
/// carrying the deleted `recstore` layout) usable: the mount fails, littlefs
/// formats, and the device comes up with an empty store rather than with no
/// store. The alternative — refusing to run — is strictly worse on a treadmill
/// whose belt works and whose history does not.
fn register_fs() -> bool {
    // SAFETY: `esp_vfs_littlefs_conf_t` is a C POD of pointers, a struct
    // pointer and a bitfield unit; all-zero is a valid initial value for every
    // field (null pointers, all flags clear) and is exactly what the C
    // examples achieve with `= { 0 }`. Zeroing avoids naming bindgen's
    // internal bitfield members, which are not part of any stable contract.
    let mut conf: sys::littlefs::esp_vfs_littlefs_conf_t = unsafe { core::mem::zeroed() };
    conf.base_path = BASE_C.as_ptr();
    conf.partition_label = PARTITION_C.as_ptr();
    conf.set_format_if_mount_failed(1);

    // SAFETY: three argument-free IDF accessors and one registration call.
    // `conf` is a live local read for the duration of the call — esp_littlefs
    // copies what it retains — and the two `CStr`s it points at are `'static`.
    // The heap samples either side are what turn the resident cost of a
    // filesystem from an estimate into a measurement.
    let (before, rc, after) = unsafe {
        let before = sys::esp_get_free_heap_size();
        let rc = sys::littlefs::esp_vfs_littlefs_register(&conf);
        (before, rc, sys::esp_get_free_heap_size())
    };
    if rc != sys::ESP_OK {
        return false;
    }
    FS_BYTES.store(before.saturating_sub(after) as usize, Ordering::Relaxed);
    true
}

impl Stores {
    fn mount() -> Option<Stores> {
        if !register_fs() {
            return None;
        }
        Some(Stores {
            history: Index::mount(b'h'),
            workouts: Index::mount(b'w'),
            runs: Index::mount(b'r'),
        })
    }

    /// Resident bytes for the whole tier: the sequence indexes (a compile-time
    /// constant) plus what the mount actually cost (measured once, at boot).
    ///
    /// CONSTANT BY CONSTRUCTION, which is the property `test_records.py`
    /// asserts and the property whose absence let ~15 requests exhaust the C++
    /// tier's heap. Neither term moves with stored volume, record size or
    /// request count: this module opens exactly one file at a time and closes
    /// it before returning, so littlefs's per-open-file buffer is transient
    /// and its four-entry descriptor cache never has to grow.
    pub fn resident_bytes() -> usize {
        INDEX_BYTES + FS_BYTES.load(Ordering::Relaxed)
    }
}

// ONE INSTANCE, MOUNTED ONCE, BEHIND ONE LOCK. Two mounts would hold two
// independent indexes, so a write through one would be invisible to the other
// and both would eventually choose the same slot for different records.
//
// The lock is taken by HTTP handlers and by the session recorder. Neither is
// on the belt path: the serial engine, the emulate cycle and the interval
// executor cannot reach this module at all (they do not name it, and `net` is
// behind a cargo feature they do not depend on), so a slow flash erase can
// never stall the belt.
static STORES: Mutex<Option<Stores>> = Mutex::new(None);

/// Mount the store. Call once, at boot, before the server starts.
///
/// Returns false if the partition is missing or unmountable — the device then
/// runs with NO persistence rather than refusing to run.
pub fn mount_once() -> bool {
    let mut g = crate::context::lock(&STORES);
    if g.is_some() {
        return true;
    }
    *g = Stores::mount();
    g.is_some()
}

/// Run `f` against the mounted store, or return `None` if there is none.
///
/// Every store access in the firmware goes through here, so "is it mounted?"
/// is answered in one place and a handler cannot forget to ask.
pub fn with<R>(f: impl FnOnce(&mut Stores) -> R) -> Option<R> {
    let mut g = crate::context::lock(&STORES);
    g.as_mut().map(f)
}

/// Which record set an operation addresses. An enum rather than three copies
/// of every helper: the two entry sets differ only in their slot count, and a
/// generic function per set would be two monomorphisations of identical code
/// in a firmware image with a size budget.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Which {
    History,
    Workouts,
}

/// Slots in the addressed set — the upper bound of any scan over it.
pub const fn slots(w: Which) -> usize {
    match w {
        Which::History => HISTORY_SLOTS,
        Which::Workouts => WORKOUT_SLOTS,
    }
}

impl Stores {
    fn read_at(&self, w: Which, n: usize, buf: &mut [u8]) -> Option<usize> {
        match w {
            Which::History => self.history.read_nth(n, buf),
            Which::Workouts => self.workouts.read_nth(n, buf),
        }
    }

    /// Decode the nth-newest entry of a set, or `None` if the slot is empty
    /// or unreadable. `scratch` is the caller's `reqbudget` slot.
    pub fn entry_at(&self, w: Which, n: usize, scratch: &mut [u8]) -> Option<Entry> {
        let len = self.read_at(w, n, scratch)?;
        record::decode_entry(&scratch[..len])
    }

    /// The identifying head of the nth-newest entry — id and name only.
    pub fn head_at(&self, w: Which, n: usize, scratch: &mut [u8]) -> Option<record::Head> {
        let len = self.read_at(w, n, scratch)?;
        record::peek_entry(&scratch[..len])
    }

    /// Position of the entry matching `pred`, by head alone.
    ///
    /// A LINEAR SCAN over 20 slots, and it costs ONE `Head` (~80 bytes) rather
    /// than one `Entry` (~1 KB): decoding 24 intervals to compare an id is
    /// both wasteful and — nested inside an HTTP handler that already holds an
    /// entry — enough to overflow the httpd task's stack and reboot the
    /// device. An index instead of a scan would have to be kept in step with
    /// every write; more state to be wrong, for a saving nobody can perceive
    /// at this size.
    pub fn find_pos(
        &self,
        w: Which,
        scratch: &mut [u8],
        pred: impl Fn(&record::Head) -> bool,
    ) -> Option<(usize, record::Head)> {
        for n in 0..slots(w) {
            if let Some(h) = self.head_at(w, n, scratch) {
                if pred(&h) {
                    return Some((n, h));
                }
            }
        }
        None
    }

    /// The id of the entry at `n`, if its program is EXACTLY `program`.
    ///
    /// One slot read, answering both "is this the one?" and "what is its id?".
    /// Names are deliberately not compared — see `record::fingerprint`: the
    /// join this serves must survive a rename.
    pub fn match_at(
        &self,
        w: Which,
        n: usize,
        program: &program_core::Program,
        scratch: &mut [u8],
    ) -> Option<FixedStr<{ record::MAX_ID }>> {
        let len = self.read_at(w, n, scratch)?;
        if !record::entry_matches_program(&scratch[..len], program) {
            return None;
        }
        record::peek_entry(&scratch[..len]).map(|h| h.id)
    }

    /// Position of the entry with this id.
    pub fn find_by_id(&self, w: Which, id: &str, scratch: &mut [u8]) -> Option<usize> {
        self.find_pos(w, scratch, |h| h.id.as_str() == id)
            .map(|(n, _)| n)
    }

    /// Position and value of the entry with this id. Decodes ONE entry, and
    /// only after the scan has already found it.
    pub fn find(&self, w: Which, id: &str, scratch: &mut [u8]) -> Option<(usize, Entry)> {
        let n = self.find_by_id(w, id, scratch)?;
        Some((n, self.entry_at(w, n, scratch)?))
    }

    /// The id the next record written to this set will be given.
    pub fn next_id(&self, w: Which) -> FixedStr<{ record::MAX_ID }> {
        let (tag, seq) = match w {
            Which::History => ('h', self.history.next_seq()),
            Which::Workouts => ('w', self.workouts.next_seq()),
        };
        let mut s = FixedStr::new();
        s.push_byte(tag as u8);
        s.push_i64(seq as i64);
        s
    }

    /// Whether an in-place write would change nothing on flash.
    ///
    /// See [`slot_equals`]. Only the in-place path is checked: appending is by
    /// definition a new record in a different slot.
    ///
    /// THE COST IS ORDERING, and it is stated rather than hidden: a re-load of
    /// an unchanged program no longer re-sequences its history entry to the
    /// top of the lobby's recent list. Anything the user can perceive a
    /// difference in — a new speed, a checkpointed elapsed time, a use count —
    /// changes the bytes and therefore still writes.
    fn unchanged(&self, w: Which, n: usize, payload: &[u8]) -> bool {
        match w {
            Which::History => self.history.nth_equals(n, payload),
            Which::Workouts => self.workouts.nth_equals(n, payload),
        }
    }

    fn put_at(&mut self, w: Which, n: Option<usize>, payload: &[u8]) -> bool {
        if let Some(n) = n {
            if self.unchanged(w, n, payload) {
                return true;
            }
        }
        match (w, n) {
            (Which::History, None) => self.history.append(payload).is_some(),
            (Which::History, Some(n)) => self.history.replace_nth(n, payload),
            (Which::Workouts, None) => self.workouts.append(payload).is_some(),
            (Which::Workouts, Some(n)) => self.workouts.replace_nth(n, payload),
        }
    }

    /// Write `e` into the set. `at` selects update-in-place (the record keeps
    /// its slot) versus append (evicting the oldest when full).
    pub fn put(&mut self, w: Which, at: Option<usize>, e: &Entry, scratch: &mut [u8]) -> bool {
        match record::encode_entry(e, scratch) {
            // The encoded bytes go straight to flash out of the caller's slot
            // and are not retained anywhere.
            Some(n) => self.put_at(w, at, &scratch[..n]),
            None => false,
        }
    }

    pub fn erase(&mut self, w: Which, n: usize) -> bool {
        match w {
            Which::History => self.history.erase_nth(n),
            Which::Workouts => self.workouts.erase_nth(n),
        }
    }

    /// `db.add_to_history`'s dedup: a program with the same name REPLACES the
    /// existing entry rather than sitting beside it. The Pi deletes and
    /// re-inserts; here the record is written into the same slot, which keeps
    /// occupancy stable and costs one commit instead of two.
    pub fn add_history(&mut self, e: &mut Entry, scratch: &mut [u8]) -> bool {
        let name = e.program.name;
        let existing = self.find_pos(Which::History, scratch, |h| h.name.as_str() == name.as_str());
        match existing {
            Some((n, old)) => {
                // Keep the id so a client holding it still resolves — the Pi
                // mints a new one here, and a stale id 404s on its next tap.
                e.id = old.id;
                self.put(Which::History, Some(n), e, scratch)
            }
            None => {
                e.id = self.next_id(Which::History);
                self.put(Which::History, None, e, scratch)
            }
        }
    }

    // --- runs -------------------------------------------------------------

    pub fn run_at(&self, n: usize, scratch: &mut [u8]) -> Option<Run> {
        let len = self.runs.read_nth(n, scratch)?;
        record::decode_run(&scratch[..len])
    }

    pub fn find_run(&self, id: &str, scratch: &mut [u8]) -> Option<usize> {
        for n in 0..RUN_SLOTS {
            if let Some(r) = self.run_at(n, scratch) {
                if r.id.as_str() == id {
                    return Some(n);
                }
            }
        }
        None
    }

    pub fn next_run_id(&self) -> FixedStr<{ record::MAX_ID }> {
        let mut s = FixedStr::new();
        s.push_byte(b'r');
        s.push_i64(self.runs.next_seq() as i64);
        s
    }

    /// Write a run record. `at` is `Some` for a checkpoint or a finalisation —
    /// the SAME slot is rewritten, so a 30-second checkpoint cadence cannot
    /// evict the other three runs (it would empty the set in two minutes).
    pub fn put_run(&mut self, at: Option<usize>, r: &Run, scratch: &mut [u8]) -> bool {
        let Some(n) = record::encode_run(r, scratch) else {
            return false;
        };
        match at {
            None => self.runs.append(&scratch[..n]).is_some(),
            // A checkpoint carrying the same numbers as the last one — a
            // paused session, a belt at zero under a running program — would
            // rewrite a sector for nothing. See `Stores::unchanged`.
            Some(pos) if self.runs.nth_equals(pos, &scratch[..n]) => true,
            Some(pos) => self.runs.replace_nth(pos, &scratch[..n]),
        }
    }

    // --- the QEMU harness's probes ----------------------------------------
    //
    // The behavioural harness must be able to write a record, reboot the SoC
    // and read it back, and it must do so through THE MOUNTED STORE rather
    // than a private mount of its own. These three are the whole surface it
    // needs; they are here, next to the index, rather than reaching into the
    // index's internals from a test module.

    /// Append raw bytes to a set, bypassing the record codec.
    pub fn raw_append(&mut self, w: Which, payload: &[u8]) -> Option<u32> {
        match w {
            Which::History => self.history.append(payload),
            Which::Workouts => self.workouts.append(payload),
        }
    }

    /// Read the nth-newest raw record of a set.
    pub fn raw_read(&self, w: Which, n: usize, buf: &mut [u8]) -> Option<usize> {
        self.read_at(w, n, buf)
    }

    /// How many records each set holds.
    pub fn counts(&self) -> (usize, usize, usize) {
        (self.history.len(), self.workouts.len(), self.runs.len())
    }
}
