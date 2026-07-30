//! The only `unsafe` on the PRODUCTION path (`hal/` + `log.rs`); the
//! test-image-only `qemu_test/` module has its own, separately budgeted.
//!
//! CORRECTED 2026-07-28. This header used to claim that the crate root's
//! `#![deny(unsafe_code)]` made the containment compiler-enforced and that
//! `src/qemu_test/` therefore could not contain an unsafe block. Both halves
//! were false: `qemu_test/mod.rs` DOES contain one (main.rs grants it
//! `#[allow(unsafe_code)]`), and `deny` is a lint level that ANY module can
//! lift for itself with an inner `#[allow(unsafe_code)]` — so a new module
//! could take unsafe silently and nothing would fail.
//!
//! What is actually enforced now:
//!
//!  * `safety_core` carries `#![forbid(unsafe_code)]` — compiler-enforced, and
//!    `forbid` CANNOT be lifted by an inner `allow` (that is a hard error).
//!  * `src/tasks/`, `src/context.rs` and `src/pins.rs` each carry their own
//!    module-level `#![forbid(unsafe_code)]`, so for those the containment is
//!    genuinely compiler-enforced, not a convention.
//!  * `main.rs` keeps `#![deny(unsafe_code)]` (it must grant `allow` to the
//!    three unsafe-bearing modules, and `forbid` at the root would make that
//!    impossible), and `tools/check_unsafe_budget.py` — a REQUIRED build gate
//!    in `tools/build.sh` — asserts the allowlist of unsafe-bearing files, the
//!    allowlist of `allow(unsafe_code)` sites, a `// SAFETY:` comment on every
//!    unsafe block, and the exact production unsafe-line budget. Adding an
//!    unsafe block or an `allow` anywhere else fails the build.
//!
//! Every unsafe block below has a `// SAFETY:` comment stating the invariant
//! it upholds. Two ESP-IDF facilities are deliberately NOT used through their
//! safe esp-idf-hal wrappers, because those wrappers are wrong for a safety
//! core — see `gpio.rs` (PinDriver) and `wdt.rs` (TWDTDriver).

pub mod clock;
pub mod delay;
pub mod gpio;
pub mod uart;
pub mod wdt;

pub use clock::Esp32Clock;
pub use gpio::Esp32SafetyIo;
pub use uart::{ConsoleMotorUart, MotorTapUart};
