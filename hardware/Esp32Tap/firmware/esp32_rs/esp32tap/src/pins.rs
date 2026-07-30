//! ESP32-S3 GPIO constants for the Esp32Tap Rev E board.
//!
//! SOURCE OF TRUTH: `hardware/Esp32Tap/tools/design.py` (NETS + COMPONENTS U1
//! pad map) — verified by `firmware/esp32/tools/check_pins.py`, which
//! re-derives every value below from design.py and FAILS the build on any
//! mismatch. Never edit these numbers by hand without running the checker.
//!
//! Format required by check_pins.py:  `pub const K_NET_NAME: u32 = <n>;`

// COMPILER-ENFORCED unsafe containment for this module and every module
// below it. `forbid` (unlike the crate root's `deny`) CANNOT be lifted by an
// inner `#[allow(unsafe_code)]` — that is a hard error, not a warning — so
// this is a guarantee rather than a convention. Added 2026-07-28 after a
// reviewer disproved the "deny contains it" claim by counterexample.
#![forbid(unsafe_code)]


/// net CONS_RX — console pin-6 passive tap, UART1 RX (via R7 10k series).
pub const K_CONS_RX: u32 = 18;

/// net ESP_TX — UART1 TX into the U7 SN74LVC1G126 tri-state buffer.
pub const K_ESP_TX: u32 = 17;

/// net PIN3_RX — motor pin-3 passive tap, UART2 RX (via R8 10k series).
pub const K_PIN3_RX: u32 = 16;

/// net TREAD_OK_MCU — TPS3700 window-supervisor permission sense through R32
/// 4.7k isolation.
///
/// INPUT-ONLY: the firmware must NEVER drive this pin. R32 exists so a
/// misconfigured push-pull GPIO cannot override the hardware interlock
/// (NETLIST.md finding B2). `hal::gpio` never passes this constant to an
/// output function anywhere in the file.
pub const K_TREAD_OK_MCU: u32 = 6;

/// net RELAY_CMD — relay-energize request into the U6 AND gate (1A);
/// hardware-gated by TREAD_OK; R23 10k pull-down fails safe.
pub const K_RELAY_CMD: u32 = 21;

/// net TX_ENABLE — motor-TX permission into the U6 AND gate (2A);
/// hardware-gated by TREAD_OK; R27 10k pull-down fails safe.
pub const K_TX_ENABLE: u32 = 15;

/// net K1_NC_FB — K1 pole-B normally-closed dry-contact feedback
/// (R25 10k pull-up: HIGH = NC contact open).
pub const K_K1_NC_FB: u32 = 4;

/// net K1_NO_FB — K1 pole-B normally-open dry-contact feedback
/// (R26 10k pull-up: HIGH = NO contact open).
pub const K_K1_NO_FB: u32 = 5;

/// net VBUS_PRESENT_N — ACTIVE-LOW USB VBUS presence (Q2 open-drain pulls low
/// when VBUS is present; R30 10k pull-up). Inverted exactly once, in the HAL.
pub const K_VBUS_PRESENT_N: u32 = 7;

/// net STATUS_LED — green LED1 via R11 330R.
pub const K_STATUS_LED: u32 = 38;
