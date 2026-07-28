//! The authoritative safety layer: constants, the controller, and the
//! sub-millisecond relay-feedback poll window.

pub mod constants;
pub mod controller;
pub mod feedback_window;

pub use constants::*;
pub use controller::{
    feedback_from_gpio, ConnectionIdentity, Feedback, OutputIntent, SafeMode, SafetyController,
    SafetyTimeoutFired, Transport, EVENT_CAPACITY, EVENT_MAX_LEN, MAX_ACTIVE_CONNECTIONS,
    MAX_TRACKED_GENERATIONS,
};
pub use feedback_window::{in_feedback_wait, run_feedback_window, FeedbackWindowIo, MAX_WINDOW_POLLS};
