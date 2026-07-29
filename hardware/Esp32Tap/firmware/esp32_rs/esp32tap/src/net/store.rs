//! The persistence tier — `recstore` rings over the real `storage` partition.
//!
//! WHAT LIVES HERE, and the caps, mirroring what `python/db.py` owns:
//!   * program history — the last few loaded/generated programs, newest first
//!   * saved workouts  — favourites the user keeps deliberately
//!   * run records     — one per session, checkpointed while it runs
//!
//! MEMORY IS THE DESIGN, not a consideration. Each ring keeps `SLOTS*4+8` bytes
//! resident and nothing else: no parsed document is ever retained across a
//! request. That is the property whose absence killed the C++ tier, where
//! documents were held per store and ~15 unauthenticated requests could exhaust
//! the heap and reboot the device mid-run. A record is read into a
//! caller-supplied buffer, used, and forgotten.
//!
//! SIZING. The partition is 1 MB and a slot is one 4 KB sector (see
//! `recstore::slot_is_sector_safe` — NOR erase granularity means anything
//! smaller lets one record's erase destroy its neighbours). The three rings
//! below occupy 44 sectors = 176 KB, leaving the rest of the partition free for
//! the tiers still to come.

//! ## The record layer
//!
//! Below the rings sits one rule, and every operation in this file obeys it:
//! **a record is read into a `reqbudget` slot, decoded into a value, and the
//! slot is released.** Nothing parsed is retained between requests, and the
//! only per-request memory is the slot the request already had to lease. That
//! is why `program_core::record` is a binary codec rather than JSON — the
//! worst-case record is ~936 bytes, so it fits one 2048-byte slot with room
//! to spare (`record::max_entry_bytes`, asserted against the slot size by a
//! host test).
//!
//! ## Divergences from `python/db.py`, all deliberate
//!
//! * **Order is write order, not `created_at`/`last_used_at`.** The ring knows
//!   sequences; there is no clock here to sort by. Updating a record makes it
//!   newest, so "most recently touched first" is what a client sees — which is
//!   what the Pi's `ORDER BY last_used_at DESC` produces for workouts anyway,
//!   and differs for history only in that reloading an entry re-floats it.
//! * **Caps are the rings'.** History's 20 matches `db.MAX_HISTORY`; workouts
//!   are capped at 20 where the Pi has no cap, and runs at 4 where the Pi
//!   keeps 200. Stated in the module header above; not hidden.
//! * **One profile**, so every ownership check in `server.py` is vacuous and
//!   none is ported. Building `profile_id` plumbing for a device with one
//!   profile would add an id-confusion surface for no user-visible benefit.

use esp_idf_sys as sys;
use program_core::record::{self, Entry, Run};
use recstore::{Flash, Ring, SECTOR};
use safety_core::FixedStr;
use std::sync::Mutex;

/// Slots per ring. Program history matches python/db.py's MAX_HISTORY (20).
pub const HISTORY_SLOTS: usize = 20;
pub const WORKOUT_SLOTS: usize = 20;
/// Run records: fewer than the Pi keeps (it has a real disk), sized so the
/// three rings still fit comfortably with the partition mostly free.
pub const RUN_SLOTS: usize = 4;

/// One 4 KB sector per record — the smallest slot that is crash-safe.
pub const SLOT: usize = SECTOR;

/// Byte offsets within the partition. Explicit rather than computed so a later
/// resize cannot silently shift an existing ring on top of another's records.
const HISTORY_BASE: usize = 0;
const WORKOUT_BASE: usize = HISTORY_SLOTS * SLOT;
const RUN_BASE: usize = WORKOUT_BASE + WORKOUT_SLOTS * SLOT;

/// Total flash consumed. Checked against the partition at mount.
pub const USED_BYTES: usize = RUN_BASE + RUN_SLOTS * SLOT;

/// The `storage` partition, found once at boot.
pub struct Partition {
    inner: *const sys::esp_partition_t,
}

// SAFETY: `esp_partition_t` is owned by IDF, immutable for the life of the
// application (its own docs say the pointer stays valid), and we only pass it
// back into IDF's own thread-safe partition API.
unsafe impl Send for Partition {}

impl Partition {
    /// Locate the `storage` partition declared in partitions_esp32tap.csv.
    pub fn open() -> Option<Partition> {
        // SAFETY: a lookup by type/subtype/label; the returned pointer is
        // IDF-owned and valid for the application's lifetime, or null.
        let p = unsafe {
            sys::esp_partition_find_first(
                sys::esp_partition_type_t_ESP_PARTITION_TYPE_DATA,
                sys::esp_partition_subtype_t_ESP_PARTITION_SUBTYPE_DATA_SPIFFS,
                c"storage".as_ptr(),
            )
        };
        if p.is_null() {
            return None;
        }
        Some(Partition { inner: p })
    }
}

impl Flash for Partition {
    fn size(&self) -> usize {
        // SAFETY: `inner` is non-null (checked in `open`) and IDF-owned.
        unsafe { (*self.inner).size as usize }
    }

    fn read(&self, offset: usize, buf: &mut [u8]) -> Result<(), ()> {
        // SAFETY: `buf` is a live exclusive borrow of exactly the length passed;
        // IDF writes at most that many bytes and bounds-checks the offset.
        let rc = unsafe {
            sys::esp_partition_read(
                self.inner,
                offset,
                buf.as_mut_ptr() as *mut core::ffi::c_void,
                buf.len(),
            )
        };
        if rc == sys::ESP_OK {
            Ok(())
        } else {
            Err(())
        }
    }

    fn write(&mut self, offset: usize, data: &[u8]) -> Result<(), ()> {
        // SAFETY: `data` is a live borrow read for the duration of the call.
        let rc = unsafe {
            sys::esp_partition_write(
                self.inner,
                offset,
                data.as_ptr() as *const core::ffi::c_void,
                data.len(),
            )
        };
        if rc == sys::ESP_OK {
            Ok(())
        } else {
            Err(())
        }
    }

    fn erase(&mut self, offset: usize) -> Result<(), ()> {
        // Erase the sector CONTAINING `offset`, matching the trait contract.
        let base = (offset / SECTOR) * SECTOR;
        // SAFETY: an IDF call taking scalars; it bounds-checks against the
        // partition and refuses an unaligned range.
        let rc = unsafe { sys::esp_partition_erase_range(self.inner, base, SECTOR) };
        if rc == sys::ESP_OK {
            Ok(())
        } else {
            Err(())
        }
    }
}

/// All three rings, mounted once at boot.
pub struct Stores {
    pub flash: Partition,
    pub history: Ring<HISTORY_SLOTS, SLOT>,
    pub workouts: Ring<WORKOUT_SLOTS, SLOT>,
    pub runs: Ring<RUN_SLOTS, SLOT>,
}

impl Stores {
    /// Mount every ring. Scanning is header-only — 44 sector reads of 16 bytes,
    /// not 176 KB — so boot cost does not grow with what is stored.
    pub fn mount() -> Option<Stores> {
        let flash = Partition::open()?;
        if flash.size() < USED_BYTES {
            return None;
        }
        let history = Ring::mount(&flash, HISTORY_BASE).ok()?;
        let workouts = Ring::mount(&flash, WORKOUT_BASE).ok()?;
        let runs = Ring::mount(&flash, RUN_BASE).ok()?;
        Some(Stores {
            flash,
            history,
            workouts,
            runs,
        })
    }

    /// Resident bytes across all three rings. Constant by construction.
    pub const fn resident_bytes() -> usize {
        Ring::<HISTORY_SLOTS, SLOT>::resident_bytes()
            + Ring::<WORKOUT_SLOTS, SLOT>::resident_bytes()
            + Ring::<RUN_SLOTS, SLOT>::resident_bytes()
    }
}

// ---------------------------------------------------------------------------
// The mounted store.
//
// ONE INSTANCE, MOUNTED ONCE, BEHIND ONE LOCK. Mounting per request would
// re-scan 44 sector headers every time and — worse — two mounts of the same
// ring would hold two independent indexes, so an append through one would be
// invisible to the other until it re-mounted. `recstore` is a single-writer
// design; this is where that is enforced.
//
// The lock is taken by HTTP handlers and by the session recorder. Neither is
// on the belt path: the serial engine, the emulate cycle and the interval
// executor cannot reach this module at all (they do not name it, and `net` is
// behind a cargo feature they do not depend on), so a slow flash erase can
// never stall the belt.
// ---------------------------------------------------------------------------

static STORES: Mutex<Option<Stores>> = Mutex::new(None);

/// Mount the rings. Call once, at boot, before the server starts.
///
/// Returns false if the partition is missing or unreadable — the device then
/// runs with NO persistence rather than refusing to run, because a treadmill
/// whose belt works and whose history does not is strictly better than one
/// that will not start.
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

/// Which ring an operation addresses. An enum rather than three copies of
/// every helper: the two entry rings differ only in their slot count, and a
/// generic function per ring would be two monomorphisations of identical code
/// in a firmware image with a size budget.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Which {
    History,
    Workouts,
}

/// Slots in the addressed ring — the upper bound of any scan over it.
pub const fn slots(w: Which) -> usize {
    match w {
        Which::History => HISTORY_SLOTS,
        Which::Workouts => WORKOUT_SLOTS,
    }
}

impl Stores {
    fn read_at(&self, w: Which, n: usize, buf: &mut [u8]) -> Option<usize> {
        let r = match w {
            Which::History => self.history.read_nth(&self.flash, n, buf),
            Which::Workouts => self.workouts.read_nth(&self.flash, n, buf),
        };
        r.ok().flatten()
    }

    /// Decode the nth-newest entry of a ring, or `None` if the slot is empty
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
    /// device. An index instead of a scan would have to be rebuilt at mount
    /// and kept in step with every write; more state to be wrong, for a saving
    /// nobody can perceive at this size.
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

    /// The id the next record written to this ring will be given.
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
    /// A REPLACE THAT CHANGES NOTHING IS STILL A 4 KB SECTOR ERASE, and the
    /// dedup path aims every repeat at the same physical slot — so an
    /// unauthenticated client re-POSTing one identical program in a loop was
    /// spending the flash's endurance one request at a time. See
    /// `recstore::Ring::nth_equals`. Only the in-place path is checked:
    /// appending is by definition a new record in a different slot.
    ///
    /// THE COST IS ORDERING, and it is stated rather than hidden: a re-load of
    /// an unchanged program no longer re-sequences its history entry to the
    /// top of the lobby's recent list. Anything the user can perceive a
    /// difference in — a new speed, a checkpointed elapsed time, a use count —
    /// changes the bytes and therefore still writes.
    fn unchanged(&self, w: Which, n: usize, payload: &[u8]) -> bool {
        match w {
            Which::History => self.history.nth_equals(&self.flash, n, payload),
            Which::Workouts => self.workouts.nth_equals(&self.flash, n, payload),
        }
    }

    fn put_at(&mut self, w: Which, n: Option<usize>, payload: &[u8]) -> bool {
        if let Some(n) = n {
            if self.unchanged(w, n, payload) {
                return true;
            }
        }
        match (w, n) {
            (Which::History, None) => self.history.append(&mut self.flash, payload).is_ok(),
            (Which::History, Some(n)) => self
                .history
                .replace_nth(&mut self.flash, n, payload)
                .unwrap_or(false),
            (Which::Workouts, None) => self.workouts.append(&mut self.flash, payload).is_ok(),
            (Which::Workouts, Some(n)) => self
                .workouts
                .replace_nth(&mut self.flash, n, payload)
                .unwrap_or(false),
        }
    }

    /// Write `e` into the ring. `at` selects update-in-place (the record keeps
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
            Which::History => self.history.erase_nth(&mut self.flash, n),
            Which::Workouts => self.workouts.erase_nth(&mut self.flash, n),
        }
        .unwrap_or(false)
    }

    /// `db.add_to_history`'s dedup: a program with the same name REPLACES the
    /// existing entry rather than sitting beside it. The Pi deletes and
    /// re-inserts; here the record is written into the same slot, which costs
    /// one erase instead of two and keeps the ring's occupancy stable.
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
        let len = self.runs.read_nth(&self.flash, n, scratch).ok().flatten()?;
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
    /// evict the other three runs (it would empty the ring in two minutes).
    pub fn put_run(&mut self, at: Option<usize>, r: &Run, scratch: &mut [u8]) -> bool {
        let Some(n) = record::encode_run(r, scratch) else {
            return false;
        };
        match at {
            None => self.runs.append(&mut self.flash, &scratch[..n]).is_ok(),
            // A checkpoint that carries the same numbers as the last one —
            // a paused session, a belt at zero under a running program —
            // erases a sector for nothing. See `Stores::unchanged`.
            Some(pos) if self.runs.nth_equals(&self.flash, pos, &scratch[..n]) => true,
            Some(pos) => self
                .runs
                .replace_nth(&mut self.flash, pos, &scratch[..n])
                .unwrap_or(false),
        }
    }
}
