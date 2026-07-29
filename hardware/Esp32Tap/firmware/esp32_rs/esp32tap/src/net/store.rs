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

use esp_idf_sys as sys;
use recstore::{Flash, Ring, SECTOR};

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
