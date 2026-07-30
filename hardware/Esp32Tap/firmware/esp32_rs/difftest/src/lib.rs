//! Differential harness: drive the Rust safety core and the COMMITTED C++
//! safety core with identical inputs, in-process, and diff the observables.
//!
//! Mandate item 3: "prove equivalence rather than asserting it."
//!
//! All `unsafe` in this crate is FFI marshalling into the C++ shim. It is
//! TEST-HARNESS ONLY — this crate never ships to the device, and its unsafe
//! line count is reported separately from the firmware's.

#![allow(clippy::missing_safety_doc)]

pub mod cpp;
pub mod gen;

pub use cpp::{CppController, CppMode};
