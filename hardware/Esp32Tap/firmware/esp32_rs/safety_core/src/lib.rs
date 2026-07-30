/*!
 * safety_core — portable Esp32Tap safety core, Rust port of
 * `firmware/esp32/components/portable_core/` (itself a line-faithful port of
 * `firmware/safety_model.py`, the normative executable contract).
 *
 * Structural guarantees this crate carries (the point of the port):
 *
 *  * `#![no_std]` outside `cfg(test)` and `alloc` is never named, so a heap
 *    allocation anywhere in the serial read path or the emulate cycle is a
 *    COMPILE ERROR rather than a code-review catch. The C++ original relies on
 *    reviewer discipline for the same property and in fact allocates:
 *    `kv_build` and `EmulationCycle::value_for` both return `std::string` on
 *    the emulate-cycle path.
 *  * `#![forbid(unsafe_code)]` — every invariant here is checked.
 *  * Newtyped units (`SpeedTenths` vs `SpeedHundredths` vs `Mph`,
 *    `InclineHalfPct`, `Micros` vs `Millis`) so unit confusion cannot compile.
 *  * `Phase`/`TransferPhase`/`Lease` sum types so a stale transfer deadline or
 *    a half-cleared lease is unrepresentable rather than "remember to clear it".
 *
 * What Rust does NOT give, and what therefore still rests on the model, the
 * 148 host cases and the QEMU harness: relay entry/exit ordering, the 10 ms
 * feedback qualification window, fail-closed on unknown feedback, the exact
 * deadlines, and the clamps. See README.md §"honest limits".
 */

#![cfg_attr(not(test), no_std)]
#![forbid(unsafe_code)]
#![allow(clippy::needless_range_loop)]

pub mod cycle;
pub mod emulate_policy;
pub mod fixed_str;
pub mod hal;
pub mod key_cache;
pub mod kv;
pub mod mode;
pub mod parse_buf;
pub mod ring;
pub mod safety;
pub mod units;

pub use fixed_str::FixedStr;
pub use units::*;
