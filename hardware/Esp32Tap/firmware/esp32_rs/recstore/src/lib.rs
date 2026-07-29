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

    /// The sequence the NEXT write will be given.
    ///
    /// Exposed so a caller can mint a record id from it BEFORE the record is
    /// written — an id has to be inside the payload, so it cannot be derived
    /// from the return value of `append`. It is monotonic within the ring and
    /// is rebuilt from flash on mount, so ids stay unique across reboots
    /// without a second counter to keep in step.
    pub fn next_seq(&self) -> u32 {
        self.next_seq
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
        self.write_slot(flash, idx, payload)
    }

    /// Write `payload` into slot `idx`, erasing it first and assigning the next
    /// sequence number. The ONE write path — `append` and `replace_nth` differ
    /// only in how they choose the slot.
    fn write_slot<F: Flash>(
        &mut self,
        flash: &mut F,
        idx: usize,
        payload: &[u8],
    ) -> Result<u32, ()> {
        if payload.len() > capacity(SLOT) || idx >= SLOTS {
            return Err(());
        }
        let off = self.base + idx * SLOT;
        flash.erase(off)?;
        // The slot's old record is GONE from here on, so its index entry must
        // not survive a failure below — an erased slot that still claims a
        // sequence would be read back as a record whose CRC check never runs.
        self.seqs[idx] = 0;

        // SEQUENCE EXHAUSTION IS REFUSED, NOT WRAPPED. Ordering here IS the
        // sequence, and `u32::MAX` additionally means "erased". Wrapping back
        // to 1 would make every new record sort OLDER than every pre-wrap one,
        // so a full ring would evict the record it had just written, forever —
        // a silent, permanent corruption of the newest-first contract. At one
        // write every 30 s this is ~4000 years away; refusing costs nothing and
        // makes the code total. `clear()` resets the counter.
        if self.next_seq >= u32::MAX - 1 {
            return Err(());
        }
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
        self.next_seq = seq + 1;
        Ok(seq)
    }

    /// Slot index holding the record with the nth-highest sequence (0 = newest).
    ///
    /// ONE ordering, used by every by-position operation. `read_nth` and
    /// `erase_nth` disagreeing about what "the third record" means would delete
    /// a different record than the one the caller just read, which is the kind
    /// of defect that only shows up once a user has data.
    fn nth_slot(&self, n: usize) -> Option<usize> {
        let mut order: [(u32, usize); SLOTS] = [(0, 0); SLOTS];
        for i in 0..SLOTS {
            order[i] = (self.seqs[i], i);
        }
        // Descending by sequence; empties (0) sort last.
        order.sort_unstable_by(|a, b| b.0.cmp(&a.0));
        if n >= SLOTS || order[n].0 == 0 {
            return None;
        }
        Some(order[n].1)
    }

    /// Erase the record with the nth-highest sequence. Returns whether one was
    /// there.
    ///
    /// DELETE-BY-POSITION IS THE ONLY DELETE. A record's identity (an id, a
    /// name) is the caller's concern; this crate knows only sequences, so a
    /// caller deletes by finding the position first with [`Ring::read_nth`].
    pub fn erase_nth<F: Flash>(&mut self, flash: &mut F, n: usize) -> Result<bool, ()> {
        let Some(idx) = self.nth_slot(n) else {
            return Ok(false);
        };
        flash.erase(self.base + idx * SLOT)?;
        self.seqs[idx] = 0;
        Ok(true)
    }

    /// Overwrite the record with the nth-highest sequence, in its own slot.
    ///
    /// It becomes the NEWEST record (a fresh sequence), which is what an
    /// update means here: the ring has no notion of created-at, only of write
    /// order.
    ///
    /// THE CRASH WINDOW IS ONE RECORD WIDE AND THAT IS DELIBERATE. Erase then
    /// write touches only this record's own sector, so a power cut loses the
    /// record being updated and nothing else — never an earlier one. The
    /// alternative (write the new copy first, erase the old second) would
    /// leave TWO records claiming one identity after a crash, and every reader
    /// would then need de-duplication it could get wrong. A bounded, visible
    /// loss beats an ambiguous state.
    pub fn replace_nth<F: Flash>(
        &mut self,
        flash: &mut F,
        n: usize,
        payload: &[u8],
    ) -> Result<bool, ()> {
        let Some(idx) = self.nth_slot(n) else {
            return Ok(false);
        };
        self.write_slot(flash, idx, payload)?;
        Ok(true)
    }

    /// Whether the record with the nth-highest sequence already holds EXACTLY
    /// `payload`.
    ///
    /// EXISTS TO SKIP AN ERASE, and that is a wear bound rather than a
    /// performance one. Every write here begins with an unconditional 4 KB NOR
    /// sector erase ([`Ring::write_slot`]), and the dedup path makes a repeat
    /// worse rather than better: a replace re-sequences the record to newest,
    /// so the SAME physical slot is chosen again next time. One unauthenticated
    /// client POSTing one identical program in a loop therefore erased one
    /// sector per request — at ~10 req/s a 100k-cycle sector is gone in under
    /// three hours, permanently, with no reboot and no log line to notice it
    /// by. An app retry loop produces the same shape by accident.
    ///
    /// Reads through a fixed 64-byte window rather than into a caller buffer:
    /// needing a second record-sized buffer to discover that a write can be
    /// skipped would cost more memory than the erase saves, and this crate's
    /// whole contract is that resident memory does not grow.
    pub fn nth_equals<F: Flash>(&self, flash: &F, n: usize, payload: &[u8]) -> bool {
        let Some(slot) = self.nth_slot(n) else {
            return false;
        };
        let off = self.base + slot * SLOT;
        let mut hdr = [0u8; HDR];
        if flash.read(off, &mut hdr).is_err() {
            return false;
        }
        let Some((_seq, len)) = parse_hdr(&hdr) else {
            return false;
        };
        if len != payload.len() || len > capacity(SLOT) {
            return false;
        }
        // The CRC is a filter, not the answer: it is 32 bits over the same
        // bytes, so a mismatch is decisive but a match is not. The window
        // compare below is what makes this exact — skipping a write on a
        // collision would silently keep the OLD record under the new one's id.
        if u32::from_le_bytes([hdr[12], hdr[13], hdr[14], hdr[15]]) != crc32(payload) {
            return false;
        }
        let mut win = [0u8; 64];
        let mut i = 0;
        while i < len {
            let take = if len - i < 64 { len - i } else { 64 };
            if flash.read(off + HDR + i, &mut win[..take]).is_err() {
                return false;
            }
            if win[..take] != payload[i..i + take] {
                return false;
            }
            i += take;
        }
        true
    }

    /// Read the record with the nth-highest sequence (0 = newest) into `buf`.
    /// Returns the payload length.
    pub fn read_nth<F: Flash>(
        &self,
        flash: &F,
        n: usize,
        buf: &mut [u8],
    ) -> Result<Option<usize>, ()> {
        let Some(slot) = self.nth_slot(n) else {
            return Ok(None);
        };
        let off = self.base + slot * SLOT;
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
        /// Sector erases performed. COUNTED, because erases are the resource
        /// this store actually spends: a NOR sector has ~100k of them and
        /// nothing in the system reports when they are being burned.
        erases: usize,
    }

    impl Fake {
        fn new(size: usize) -> Self {
            Fake {
                mem: vec![0xFF; size],
                budget: None,
                erases: 0,
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
            self.erases += 1;
            Ok(())
        }
    }

    // One slot per 4 KB sector — see slot_is_sector_safe.
    type R = Ring<8, 4096>;

    #[test]
    fn an_identical_replace_is_recognised_and_a_changed_one_is_not() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"same-bytes").unwrap();
        assert!(r.nth_equals(&f, 0, b"same-bytes"));
        // Length, content and absence all read as NOT equal — a false positive
        // here would keep the OLD record under the new one's identity.
        assert!(!r.nth_equals(&f, 0, b"same-byte"));
        assert!(!r.nth_equals(&f, 0, b"same-byteS"));
        assert!(!r.nth_equals(&f, 0, b""));
        assert!(!r.nth_equals(&f, 1, b"same-bytes"));
        // ...and it survives a remount, because it reads flash rather than the
        // index.
        let r2 = R::mount(&f, 0).unwrap();
        assert!(r2.nth_equals(&f, 0, b"same-bytes"));
    }

    #[test]
    fn repeated_identical_writes_need_not_erase_a_sector() {
        // THE WEAR BOUND. `write_slot`'s first act is an unconditional 4 KB
        // erase, and a replace re-sequences the record to newest — so the same
        // physical slot is chosen again next time. Without a skip, one client
        // POSTing one identical body in a loop spends one erase per request.
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"payload").unwrap();
        let after_first = f.erases;

        for _ in 0..100 {
            if !r.nth_equals(&f, 0, b"payload") {
                r.replace_nth(&mut f, 0, b"payload").unwrap();
            }
        }
        assert_eq!(
            f.erases, after_first,
            "100 identical writes cost {} extra sector erases",
            f.erases - after_first
        );

        // A CHANGED record still writes — the skip must not be a mute button.
        if !r.nth_equals(&f, 0, b"different") {
            r.replace_nth(&mut f, 0, b"different").unwrap();
        }
        assert_eq!(f.erases, after_first + 1);
        let mut buf = [0u8; 4080];
        let n = r.read_nth(&f, 0, &mut buf).unwrap().unwrap();
        assert_eq!(&buf[..n], b"different");
    }

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
    fn erase_nth_removes_exactly_the_record_that_was_read() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        for i in 0..4u8 {
            r.append(&mut f, &[i]).unwrap();
        }
        let mut buf = [0u8; 4080];
        // n=1 is the second-newest: 2.
        assert_eq!(r.read_nth(&f, 1, &mut buf).unwrap(), Some(1));
        assert_eq!(buf[0], 2);
        assert!(r.erase_nth(&mut f, 1).unwrap());
        assert_eq!(r.len(), 3);
        // ...and it is 2 that is gone, not a neighbour.
        let mut seen = Vec::new();
        for n in 0..3 {
            r.read_nth(&f, n, &mut buf).unwrap().unwrap();
            seen.push(buf[0]);
        }
        assert_eq!(seen, vec![3, 1, 0]);
        // The freed slot is reused rather than leaked.
        r.append(&mut f, b"\x09").unwrap();
        assert_eq!(r.len(), 4);
        let r2 = R::mount(&f, 0).unwrap();
        assert_eq!(r2.len(), 4, "the deletion must survive a remount");
    }

    #[test]
    fn next_seq_is_monotonic_and_survives_a_remount() {
        // Record ids are minted from this, so a repeat after a reboot would
        // give two records the same id.
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        assert_eq!(r.next_seq(), 1);
        r.append(&mut f, b"a").unwrap();
        r.append(&mut f, b"b").unwrap();
        assert_eq!(r.next_seq(), 3);
        let r2 = R::mount(&f, 0).unwrap();
        assert_eq!(r2.next_seq(), 3, "the id counter must be rebuilt from flash");
    }

    #[test]
    fn erasing_nothing_is_not_an_error() {
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        assert!(!r.erase_nth(&mut f, 0).unwrap());
        assert!(!r.replace_nth(&mut f, 0, b"x").unwrap());
    }

    #[test]
    fn replace_nth_updates_in_place_and_does_not_consume_a_second_slot() {
        // The run-record checkpoint pattern: one record rewritten many times
        // must not evict the other runs. Appending instead would blow a
        // 4-slot ring away in two minutes of 30 s checkpoints.
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"older").unwrap();
        r.append(&mut f, b"run@0").unwrap();
        for i in 1..40u8 {
            let mut body = *b"run@0";
            body[4] = b'0' + (i % 10);
            assert!(r.replace_nth(&mut f, 0, &body).unwrap());
            assert_eq!(r.len(), 2, "a replace must not consume a slot");
        }
        let r2 = R::mount(&f, 0).unwrap();
        let mut buf = [0u8; 4080];
        r2.read_nth(&f, 0, &mut buf).unwrap().unwrap();
        assert_eq!(&buf[..5], b"run@9");
        r2.read_nth(&f, 1, &mut buf).unwrap().unwrap();
        assert_eq!(&buf[..5], b"older", "the neighbour must be untouched");
    }

    #[test]
    fn a_replace_torn_at_every_offset_never_damages_a_neighbour() {
        // Same guarantee as `torn_write_is_ignored_not_recovered`, for the
        // update path: the record being replaced may be lost, but the OTHER
        // records may not be, and the ring must always mount.
        let new = b"a replacement record of some length";
        for cut in 0..(HDR + new.len()) {
            let mut f = Fake::new(SECTOR * 8);
            let mut r = R::mount(&f, 0).unwrap();
            r.append(&mut f, b"keep-me").unwrap();
            r.append(&mut f, b"victim").unwrap();

            f.budget = Some(cut);
            let _ = r.replace_nth(&mut f, 0, new);
            f.budget = None;

            let r2 = R::mount(&f, 0).unwrap();
            let mut buf = [0u8; 4080];
            let mut seen_keep = false;
            for n in 0..8 {
                if let Some(len) = r2.read_nth(&f, n, &mut buf).unwrap() {
                    if &buf[..len] == b"keep-me" {
                        seen_keep = true;
                    } else {
                        assert_eq!(&buf[..len], new, "a partial record read as valid");
                    }
                }
            }
            assert!(seen_keep, "a torn replace destroyed a NEIGHBOUR (cut={cut})");
        }
    }

    #[test]
    fn sequence_exhaustion_is_refused_rather_than_wrapped() {
        // A wrap would make new records sort as the OLDEST and a full ring
        // would evict what it had just written, silently and forever.
        let mut f = Fake::new(SECTOR * 8);
        let mut r = R::mount(&f, 0).unwrap();
        r.append(&mut f, b"a").unwrap();
        // Fast-forward the counter rather than doing 4 billion writes.
        for _ in 0..3 {
            r.next_seq = u32::MAX - 2;
            assert!(r.append(&mut f, b"b").is_ok());
            assert!(r.append(&mut f, b"c").is_err(), "a wrapped sequence was accepted");
        }
        assert!(r.len() > 0, "the store is still readable after refusing");
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
