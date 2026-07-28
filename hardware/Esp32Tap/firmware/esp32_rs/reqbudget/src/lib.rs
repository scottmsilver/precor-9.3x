#![no_std]

//! Fixed per-request memory budget.
//!
//! THE RULE (owner mandate): "Memory allocation should be fixed per request and
//! we should not be using arenas in memory limited devices. The request scope
//! should own the allocator and the allocator should prevent this nonsense."
//!
//! WHY THIS SHAPE. The C++ attempt at the server tier bound a rapidjson
//! `MemoryPoolAllocator` to each STORE, so it lived for the whole boot and grew
//! monotonically — pools never free. Its guard against that was dead code, and
//! ~15 unauthenticated requests could exhaust the heap. Under `-fno-exceptions`
//! (and equally under Rust's default `alloc`) exhaustion aborts, which reboots
//! the device, drops the relay and interrupts a run. Heap exhaustion is a belt
//! availability event, so it must be impossible by construction rather than
//! unlikely by review.
//!
//! HOW THIS MAKES IT IMPOSSIBLE:
//!
//! * The pool is `static` — every byte the request path can use is reserved at
//!   image link time. There is no runtime allocation to fail.
//! * A request must LEASE a slot. `SLOTS` of them exist; the (N+1)th concurrent
//!   request is REFUSED (503), it does not allocate.
//! * A slot is a fixed `SLOT_BYTES` buffer. A body that will not fit is
//!   REFUSED (413) at admission — before a byte is parsed — so a handler can
//!   never be part-way through and out of room.
//! * Release is `Drop`, not a call. Success, rejection, early return and panic
//!   unwinding all reclaim identically, because reclamation is not a code path.
//!
//! Total request-path memory is therefore exactly `SLOTS * SLOT_BYTES`, visible
//! in one line and checked by `budget_bytes_is_visible_in_one_line`.
//!
//! WHY IT LIVES IN THE NETWORK TIER, NOT `safety_core`. Two reasons, both
//! hard: `safety_core` is `#![forbid(unsafe_code)]` and this needs a static
//! pool; and `safety_core` is the crate the differential compares against the
//! C++ core op-for-op, so adding state with no C++ counterpart would diverge
//! the compared stream — which is precisely how the exit-ordering audit event
//! broke it earlier (event_count 18 vs 17).
//!
//! WHAT THIS DELIBERATELY DOES NOT DO. It does not sub-allocate: a slot is one
//! contiguous buffer a handler uses directly. Sub-allocation is what turns a
//! bounded region back into an allocator with lifetime questions, which is the
//! bug we are designing away.

use core::sync::atomic::{AtomicU32, Ordering};

/// Concurrent in-flight requests that can hold a buffer.
///
/// Sized to the server's own socket budget rather than guessed: IDF runs ONE
/// worker, so slots beyond the number of sockets that can be mid-request are
/// dead memory. Raising this raises `budget_bytes()` linearly and must be a
/// deliberate act — the test below pins the arithmetic.
pub const SLOTS: usize = 4;

/// Bytes per slot. Must cover the largest legitimate request body plus the
/// largest response a handler builds in place.
pub const SLOT_BYTES: usize = 2048;

/// Every byte the request path may use, reserved at link time.
pub const fn budget_bytes() -> usize {
    SLOTS * SLOT_BYTES
}

/// Why a request could not be admitted. Maps 1:1 onto a status code so the
/// caller cannot invent a third outcome such as "allocate more".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    /// All slots are in use — 503. Retryable.
    Busy,
    /// The declared body exceeds `SLOT_BYTES` — 413. Not retryable.
    TooLarge,
}

impl Refusal {
    pub const fn status(self) -> u16 {
        match self {
            Refusal::Busy => 503,
            Refusal::TooLarge => 413,
        }
    }
}

/// The pool. One bit per slot; 0 = free.
struct Pool {
    in_use: AtomicU32,
    buffers: [[u8; SLOT_BYTES]; SLOTS],
}

// SAFETY-BY-CONSTRUCTION NOTE: `buffers` is only ever reached through a
// `Lease`, and a `Lease` is only ever handed out by winning the CAS on the
// corresponding bit, so two leases can never alias the same buffer.
static mut POOL: Pool = Pool {
    in_use: AtomicU32::new(0),
    buffers: [[0u8; SLOT_BYTES]; SLOTS],
};

const _: () = assert!(SLOTS <= 32, "in_use is a u32 bitmap");

/// An exclusive borrow of one slot, released on drop.
pub struct Lease {
    idx: usize,
    len: usize,
}

impl Lease {
    /// The slot's buffer, truncated to the admitted length.
    pub fn buf(&mut self) -> &mut [u8] {
        // SAFETY: this lease owns bit `idx` until Drop clears it, and leases
        // are the only path to `POOL.buffers`, so no other reference to this
        // buffer can exist. `len <= SLOT_BYTES` is enforced at admission.
        unsafe {
            let bufs = core::ptr::addr_of_mut!(POOL.buffers);
            let slot = (*bufs).as_mut_ptr().add(self.idx);
            core::slice::from_raw_parts_mut(slot as *mut u8, self.len)
        }
    }

    pub fn capacity(&self) -> usize {
        SLOT_BYTES
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        // SAFETY: reading a static atomic; clearing our own bit. This is the
        // ONLY release path, which is why a handler cannot leak a slot.
        let in_use = unsafe { &(*core::ptr::addr_of!(POOL)).in_use };
        in_use.fetch_and(!(1u32 << self.idx), Ordering::Release);
    }
}

/// Admit a request that declares `body_len` bytes, or refuse it.
///
/// Call this BEFORE reading or parsing anything. On `Ok` the handler has a
/// buffer big enough for the whole body; on `Err` it must answer the status in
/// [`Refusal::status`] and read nothing.
pub fn admit(body_len: usize) -> Result<Lease, Refusal> {
    if body_len > SLOT_BYTES {
        return Err(Refusal::TooLarge);
    }
    // SAFETY: reading a static atomic.
    let in_use = unsafe { &(*core::ptr::addr_of!(POOL)).in_use };
    loop {
        let cur = in_use.load(Ordering::Acquire);
        let free = (!cur) & ((1u32 << SLOTS) - 1);
        if free == 0 {
            return Err(Refusal::Busy);
        }
        let idx = free.trailing_zeros() as usize;
        let bit = 1u32 << idx;
        if in_use
            .compare_exchange_weak(cur, cur | bit, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            return Ok(Lease { idx, len: body_len });
        }
    }
}

/// Slots currently leased. Diagnostics only.
pub fn in_flight() -> u32 {
    // SAFETY: reading a static atomic.
    let in_use = unsafe { &(*core::ptr::addr_of!(POOL)).in_use };
    in_use.load(Ordering::Acquire).count_ones()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budget_bytes_is_visible_in_one_line() {
        // The whole point: the bound is arithmetic, not an emergent property.
        assert_eq!(budget_bytes(), 4 * 2048);
        assert_eq!(budget_bytes(), 8192);
    }

    #[test]
    fn oversized_body_is_refused_before_anything_is_read() {
        assert_eq!(admit(SLOT_BYTES + 1).err(), Some(Refusal::TooLarge));
        assert_eq!(Refusal::TooLarge.status(), 413);
        assert_eq!(in_flight(), 0, "a refused request must not hold a slot");
    }

    #[test]
    fn exhaustion_refuses_it_does_not_grow() {
        let mut held = alloc_all();
        assert_eq!(in_flight(), SLOTS as u32);
        assert_eq!(admit(1).err(), Some(Refusal::Busy));
        assert_eq!(Refusal::Busy.status(), 503);
        held.clear();
        assert_eq!(in_flight(), 0);
    }

    #[test]
    fn drop_reclaims_on_every_path_including_early_return() {
        fn rejected_midway() -> Result<(), ()> {
            let _lease = admit(64).map_err(|_| ())?;
            Err(()) // early return with the lease live
        }
        for _ in 0..1000 {
            let _ = rejected_midway();
        }
        // The C++ bug was exactly this: a rejected request kept its memory.
        assert_eq!(in_flight(), 0);
    }

    #[test]
    fn repeated_requests_do_not_accumulate() {
        // 100k accepted+rejected cycles must leave the pool exactly as found —
        // this is the property whose absence let ~15 requests reboot the C++
        // device.
        for i in 0..100_000 {
            match admit(if i % 3 == 0 { SLOT_BYTES + 1 } else { 128 }) {
                Ok(mut l) => {
                    l.buf()[0] = 1;
                }
                Err(Refusal::TooLarge) => {}
                Err(Refusal::Busy) => panic!("serial use cannot exhaust"),
            }
        }
        assert_eq!(in_flight(), 0);
    }

    #[test]
    fn leases_do_not_alias() {
        let mut a = admit(16).unwrap();
        let mut b = admit(16).unwrap();
        a.buf()[0] = 0xAA;
        b.buf()[0] = 0xBB;
        assert_eq!(a.buf()[0], 0xAA, "b must not have written a's buffer");
        assert_eq!(b.buf()[0], 0xBB);
    }

    extern crate std;
    use std::vec::Vec;
    fn alloc_all() -> Vec<Lease> {
        (0..SLOTS).map(|_| admit(1).unwrap()).collect()
    }
}
