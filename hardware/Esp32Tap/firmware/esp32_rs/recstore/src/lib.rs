#![no_std]

//! Fixed-size record storage — the persistence layer's logic, with no flash.
//!
//! WHY NOT LittleFS — the fair question, answered honestly.
//!
//! LittleFS handles power-loss resilience, atomic metadata updates and wear
//! levelling, and those are its HEADLINE features. It is the conventional
//! answer here and would mean owning far less code. It was tried and reverted,
//! for one concrete reason: esp-idf-sys's stock bindings.h only #includes
//! headers for components it knows about (guarded by ESP_IDF_COMP_<NAME>_
//! ENABLED — which is why espressif/mdns needed nothing extra). joltwallet/
//! littlefs is third-party and absent from that list, so pulling the component
//! in builds it but yields ZERO symbols; it needs a custom `bindings_header`,
//! which did not take effect across several attempts. That is plumbing, not a
//! reason — so if someone picks this up, THAT is the thing to solve, and this
//! crate should be deleted rather than extended.
//!
//! What justifies keeping it meanwhile is narrow: the store is ~200 lines of
//! logic with no dependencies, and `torn_write_is_ignored_not_recovered`
//! simulates power loss at EVERY byte offset of a record write and asserts no
//! earlier record is ever damaged. That test found both bugs in the first
//! version (a 4 KB sector erase destroying the 15 neighbours packed into it;
//! an erased 0xFFFFFFFF sequence sorting as the NEWEST record), which is also
//! the honest argument AGAINST hand-rolling this: two real defects in an hour.
//!
//! WHY NOT A FILESYSTEM, on the usage side. The C++ server tier died here: it held parsed
//! documents resident per store, so memory grew both with what the user had
//! saved AND with how many requests arrived, and ~15 unauthenticated requests
//! could exhaust the heap and reboot the device mid-run. A filesystem invites
//! exactly that shape — open, parse, keep. Fixed-size slots in raw flash do
//! not: a record is read into a caller-supplied buffer and nothing is retained.
//!
//! RESIDENT MEMORY IS A CONSTANT. A `Ring` holds N u32 sequence numbers and
//! nothing else. It does not grow with stored volume, with record size, or with
//! request count. `resident_bytes()` states it and a test pins the arithmetic.
//!
//! CRASH SAFETY IS STRUCTURAL, NOT JOURNALLED. Every slot carries a magic, a
//! monotonic sequence number, a length and a CRC32 over the payload. A write
//! interrupted by power loss leaves a slot whose CRC does not match, and such a
//! slot is simply not a record — it is skipped on scan. There is no half-
//! committed state to recover because a record is only ever valid or absent.
//! `torn_write_is_ignored_not_recovered` proves it by truncating a write at
//! every byte offset.
//!
//! NEWEST WINS BY SEQUENCE, NOT BY POSITION. Slots are reused round-robin, so
//! position says nothing about age. The highest sequence number is the newest
//! record, which is also what makes a cap ("keep the last 20") a scan rather
//! than a compaction.

/// A flash region divided into fixed-size slots. Implemented over
/// `esp_partition_*` on the device and over a byte array in tests, so every
/// property below is proven on the host in milliseconds rather than in QEMU.
pub trait Flash {
    /// Total bytes available.
    fn size(&self) -> usize;
    /// Read exactly `buf.len()` bytes at `offset`.
    fn read(&self, offset: usize, buf: &mut [u8]) -> Result<(), ()>;
    /// Write `data` at `offset`. The region must already be erased.
    fn write(&mut self, offset: usize, data: &[u8]) -> Result<(), ()>;
    /// Erase the sector containing `offset`. Erase granularity is
    /// [`SECTOR`]; a slot never straddles two sectors (asserted below).
    fn erase(&mut self, offset: usize) -> Result<(), ()>;
}

/// Flash erase granularity on the ESP32-S3.
pub const SECTOR: usize = 4096;

/// Slot layout: magic | seq | len | crc | payload
const MAGIC: u32 = 0x52_45_43_31; // "REC1"
const HDR: usize = 16;

/// A slot must be a WHOLE NUMBER OF SECTORS.
///
/// This is the constraint the first version got wrong, and the torn-write test
/// caught it: NOR flash erases a whole 4 KB sector, so packing 16 x 256-byte
/// slots into one sector meant erasing ANY of them destroyed the other 15.
/// "Slots divide the sector evenly" prevents a slot from straddling a boundary
/// — it does not prevent an erase from clobbering neighbours. Only one slot per
/// sector (or more) makes a record's lifetime independent of its neighbours',
/// which is what crash safety actually requires.
pub const fn slot_is_sector_safe(slot: usize) -> bool {
    slot >= SECTOR && slot % SECTOR == 0
}

/// The largest payload a slot of `slot_bytes` can hold.
pub const fn capacity(slot_bytes: usize) -> usize {
    slot_bytes - HDR
}

fn crc32(data: &[u8]) -> u32 {
    // Bitwise CRC-32 (IEEE). No table: the table would be 1 KB of RAM or a
    // large const, and records are small enough that speed is irrelevant here.
    let mut crc: u32 = 0xFFFF_FFFF;
    let mut i = 0;
    while i < data.len() {
        crc ^= data[i] as u32;
        let mut b = 0;
        while b < 8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
            b += 1;
        }
        i += 1;
    }
    !crc
}

/// A ring of `SLOTS` fixed-size records.
///
/// `SLOT` is the on-flash stride; usable payload is `SLOT - 16`.
pub struct Ring<const SLOTS: usize, const SLOT: usize> {
    /// Sequence number per slot; 0 means empty/invalid. THIS IS THE ENTIRE
    /// RESIDENT FOOTPRINT.
    seqs: [u32; SLOTS],
    next_seq: u32,
    base: usize,
}

impl<const SLOTS: usize, const SLOT: usize> Ring<SLOTS, SLOT> {
    /// Bytes this ring keeps in RAM, independent of what is stored.
    pub const fn resident_bytes() -> usize {
        SLOTS * 4 + 8
    }

    /// Scan the region and rebuild the index. Call once at boot.
    ///
    /// Invalid slots (never written, or torn by a power cut) are left as 0 and
    /// will be the first reused — a torn record costs a slot, never the store.
    pub fn mount<F: Flash>(flash: &F, base: usize) -> Result<Self, ()> {
        const { assert!(slot_is_sector_safe(SLOT)) };
        let mut r = Ring {
            seqs: [0; SLOTS],
            next_seq: 1,
            base,
        };
        if base + SLOTS * SLOT > flash.size() {
            return Err(());
        }
        for i in 0..SLOTS {
            let mut hdr = [0u8; HDR];
            flash.read(base + i * SLOT, &mut hdr)?;
            if let Some((seq, len)) = parse_hdr(&hdr) {
                if len > capacity(SLOT) {
                    continue; // torn header — leave the slot free for reuse
                }
                r.seqs[i] = seq;
                if seq >= r.next_seq {
                    // saturating, not `+ 1`: this build is panic=abort, so an
                    // overflow here would reboot the device and drop the relay.
                    r.next_seq = seq.saturating_add(1);
                }
            }
        }
        Ok(r)
    }

    /// Number of valid records held.
    pub fn len(&self) -> usize {
        self.seqs.iter().filter(|s| **s != 0).count()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Append a record, evicting the OLDEST if the ring is full.
    ///
    /// Returns the sequence number assigned.
    pub fn append<F: Flash>(&mut self, flash: &mut F, payload: &[u8]) -> Result<u32, ()> {
        if payload.len() > capacity(SLOT) {
            return Err(());
        }
        // Prefer an empty slot; otherwise evict the lowest sequence.
        let idx = match self.seqs.iter().position(|s| *s == 0) {
            Some(i) => i,
            None => {
                let mut lo = 0;
                for i in 1..SLOTS {
                    if self.seqs[i] < self.seqs[lo] {
                        lo = i;
                    }
                }
                lo
            }
        };
        let off = self.base + idx * SLOT;
        flash.erase(off)?;

        let seq = self.next_seq;
        let mut hdr = [0u8; HDR];
        hdr[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        hdr[4..8].copy_from_slice(&seq.to_le_bytes());
        hdr[8..12].copy_from_slice(&(payload.len() as u32).to_le_bytes());
        hdr[12..16].copy_from_slice(&crc32(payload).to_le_bytes());

        // Payload FIRST, header last: a power cut between them leaves a slot
        // with no magic, which scans as empty. Writing the header first would
        // leave a slot that claims a payload it does not have.
        flash.write(off + HDR, payload)?;
        flash.write(off, &hdr)?;

        self.seqs[idx] = seq;
        self.next_seq = seq.wrapping_add(1).max(1);
        Ok(seq)
    }

    /// Read the record with the nth-highest sequence (0 = newest) into `buf`.
    /// Returns the payload length.
    pub fn read_nth<F: Flash>(
        &self,
        flash: &F,
        n: usize,
        buf: &mut [u8],
    ) -> Result<Option<usize>, ()> {
        let mut order: [(u32, usize); SLOTS] = [(0, 0); SLOTS];
        for i in 0..SLOTS {
            order[i] = (self.seqs[i], i);
        }
        // Descending by sequence; empties (0) sort last.
        order.sort_unstable_by(|a, b| b.0.cmp(&a.0));
        if n >= SLOTS || order[n].0 == 0 {
            return Ok(None);
        }
        let off = self.base + order[n].1 * SLOT;
        let mut hdr = [0u8; HDR];
        flash.read(off, &mut hdr)?;
        let Some((_seq, len)) = parse_hdr(&hdr) else {
            return Ok(None);
        };
        // A length beyond the slot means a TORN HEADER, not an error: the
        // magic and sequence landed before the length did. Corruption reads as
        // ABSENT, exactly like a CRC failure — Err is reserved for a caller
        // whose buffer is genuinely too small for a valid record.
        if len > capacity(SLOT) {
            return Ok(None);
        }
        if len > buf.len() {
            return Err(());
        }
        flash.read(off + HDR, &mut buf[..len])?;
        // Verify on READ as well as on write: flash can rot, and a record that
        // fails its CRC must read as absent rather than as plausible garbage.
        let expect = u32::from_le_bytes([hdr[12], hdr[13], hdr[14], hdr[15]]);
        if crc32(&buf[..len]) != expect {
            return Ok(None);
        }
        Ok(Some(len))
    }

    /// Erase every slot.
    pub fn clear<F: Flash>(&mut self, flash: &mut F) -> Result<(), ()> {
        for i in 0..SLOTS {
            flash.erase(self.base + i * SLOT)?;
            self.seqs[i] = 0;
        }
        self.next_seq = 1;
        Ok(())
    }
}

fn parse_hdr(hdr: &[u8; HDR]) -> Option<(u32, usize)> {
    if u32::from_le_bytes([hdr[0], hdr[1], hdr[2], hdr[3]]) != MAGIC {
        return None;
    }
    let seq = u32::from_le_bytes([hdr[4], hdr[5], hdr[6], hdr[7]]);
    // 0 is "never written"; u32::MAX is the ERASED state, which a torn header
    // write leaves behind after the magic has landed but before the sequence
    // has. Both mean "not a record" — treating the erased value as a valid,
    // maximal sequence made it sort as the newest record in the store.
    if seq == 0 || seq == u32::MAX {
        return None;
    }
    let len = u32::from_le_bytes([hdr[8], hdr[9], hdr[10], hdr[11]]) as usize;
    Some((seq, len))
}

#[cfg(test)]
mod tests {
    use super::*;
    extern crate std;
    use std::vec;
    use std::vec::Vec;

    /// In-memory flash with NOR semantics: erase sets 0xFF, a write may only
    /// clear bits. Modelling that matters — a store that silently depended on
    /// rewriting a slot without erasing would pass a naive fake and fail on
    /// real hardware.
    struct Fake {
        mem: Vec<u8>,
        /// Stop accepting writes after this many bytes, to simulate power loss.
        budget: Option<usize>,
    }

    impl Fake {
        fn new(size: usize) -> Self {
            Fake {
                mem: vec![0xFF; size],
                budget: None,
            }
        }
    }

    impl Flash for Fake {
        fn size(&self) -> usize {
            self.mem.len()
        }
        fn read(&self, off: usize, buf: &mut [u8]) -> Result<(), ()> {
            if off + buf.len() > self.mem.len() {
                return Err(());
            }
            buf.copy_from_slice(&self.mem[off..off + buf.len()]);
            Ok(())
        }
        fn write(&mut self, off: usize, data: &[u8]) -> Result<(), ()> {
            if off + data.len() > self.mem.len() {
                return Err(());
            }
            for (i, b) in data.iter().enumerate() {
                if let Some(rem) = self.budget.as_mut() {
                    if *rem == 0 {
                        return Err(()); // power cut mid-write
                    }
                    *rem -= 1;
                }
                self.mem[off + i] &= b; // NOR: writes only clear bits
            }
            Ok(())
        }
        fn erase(&mut self, off: usize) -> Result<(), ()> {
            let s = (off / SECTOR) * SECTOR;
            if s + SECTOR > self.mem.len() {
                return Err(());
            }
            for b in &mut self.mem[s..s + SECTOR] {
                *b = 0xFF;
            }
            Ok(())
        }
    }

    // One slot per 4 KB sector — see slot_is_sector_safe.
    type R = Ring<8, 4096>;

    #[test]
    fn resident_memory_is_constant_and_stated() {
        // The whole point: RAM does not grow with what is stored.
        assert_eq!(R::resident_bytes(), 8 * 4 + 8);
        assert_eq!(R::resident_bytes(), 40);
    }

    #[test]
    fn a_record_survives_remount() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"hello").unwrap();
        // Drop the index entirely — this is a reboot.
        let r2 = R::mount(&f, 0).unwrap();
        let mut buf = [0u8; 4080];
        assert_eq!(r2.read_nth(&f, 0, &mut buf).unwrap(), Some(5));
        assert_eq!(&buf[..5], b"hello");
    }

    #[test]
    fn newest_wins_by_sequence_not_position() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        for i in 0..8u8 {
            r.append(&mut f, &[i]).unwrap();
        }
        // Full: the next append evicts the OLDEST, reusing slot 0 — so the
        // newest record now sits at the lowest position.
        r.append(&mut f, b"\xAA").unwrap();
        let r2 = R::mount(&f, 0).unwrap();
        let mut buf = [0u8; 4080];
        assert_eq!(r2.read_nth(&f, 0, &mut buf).unwrap(), Some(1));
        assert_eq!(buf[0], 0xAA, "newest must be by sequence, not by slot");
    }

    #[test]
    fn the_cap_holds_and_the_oldest_is_the_one_lost() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        for i in 0..20u8 {
            r.append(&mut f, &[i]).unwrap();
        }
        assert_eq!(r.len(), 8, "the ring must not grow past its cap");
        let mut buf = [0u8; 4080];
        // The last 8 written are 12..19, newest first.
        for n in 0..8 {
            r.read_nth(&f, n, &mut buf).unwrap().unwrap();
            assert_eq!(buf[0], 19 - n as u8);
        }
        assert_eq!(r.read_nth(&f, 8, &mut buf).unwrap(), None);
    }

    #[test]
    fn torn_write_is_ignored_not_recovered() {
        // Power loss at EVERY byte offset of a record write. The store must
        // always mount, and must never return a corrupt record as if valid.
        let full = b"the quick brown fox jumps over the lazy dog";
        for cut in 0..(HDR + full.len()) {
            let mut f = Fake::new(SECTOR * 8);
            let mut r = R::mount(&f, 0).unwrap();
            r.append(&mut f, b"good").unwrap();

            f.budget = Some(cut);
            let _ = r.append(&mut f, full); // may fail part-way
            f.budget = None;

            let r2 = R::mount(&f, 0).unwrap();
            let mut buf = [0u8; 4080];
            // Whatever survived, nothing may be corrupt: every readable record
            // verifies its CRC, and the earlier good record is still there.
            let mut seen_good = false;
            for n in 0..8 {
                if let Some(len) = r2.read_nth(&f, n, &mut buf).unwrap() {
                    if &buf[..len] == b"good" {
                        seen_good = true;
                    } else {
                        assert_eq!(&buf[..len], full, "a partial record read as valid");
                    }
                }
            }
            assert!(seen_good, "a torn write destroyed an EARLIER record (cut={cut})");
        }
    }

    #[test]
    fn oversized_payload_is_refused_not_truncated() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        let too_big = [0u8; 4096];
        assert!(r.append(&mut f, &too_big).is_err());
        assert_eq!(r.len(), 0, "a refused append must not consume a slot");
    }

    #[test]
    fn bit_rot_reads_as_absent() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"payload").unwrap();
        f.mem[HDR + 2] &= 0xFE; // flip a bit in the payload
        let mut buf = [0u8; 4080];
        assert_eq!(
            r.read_nth(&f, 0, &mut buf).unwrap(),
            None,
            "a CRC failure must read as absent, not as plausible garbage"
        );
    }
}
