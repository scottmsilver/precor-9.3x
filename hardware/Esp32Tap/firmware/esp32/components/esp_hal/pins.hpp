/*
 * pins.hpp — ESP32-S3 GPIO constants for the Esp32Tap Rev E board.
 *
 * SOURCE OF TRUTH: hardware/Esp32Tap/tools/design.py (NETS + COMPONENTS
 * U1 pad map) — verified by firmware/esp32/tools/check_pins.py, which
 * re-derives every value below from design.py and fails the host test
 * run on any mismatch. Never edit these numbers by hand without running
 * the checker.
 *
 * Format required by check_pins.py:  kNetName = GPIO<n>;
 */

#pragma once

#include <cstdint>

namespace esp32tap::pins {

// net CONS_RX — console pin-6 passive tap, UART1 RX (via R7 10k series)
inline constexpr int kConsRx = 18;        // CONS_RX = GPIO18

// net ESP_TX — UART1 TX into U7 SN74LVC1G126 tri-state buffer
inline constexpr int kEspTx = 17;         // ESP_TX = GPIO17

// net PIN3_RX — motor pin-3 passive tap, UART2 RX (via R8 10k series)
inline constexpr int kPin3Rx = 16;        // PIN3_RX = GPIO16

// net TREAD_OK_MCU — TPS3700 window-supervisor permission sense through
// R32 4.7k isolation. INPUT-ONLY: firmware must never drive this pin
// (R32 exists so a misconfigured push-pull GPIO cannot override the
// hardware interlock — NETLIST.md finding B2).
inline constexpr int kTreadOkMcu = 6;     // TREAD_OK_MCU = GPIO6

// net RELAY_CMD — relay-energize request into U6 AND gate (1A);
// hardware-gated by TREAD_OK; R23 10k pull-down fails safe.
inline constexpr int kRelayCmd = 21;      // RELAY_CMD = GPIO21

// net TX_ENABLE — motor-TX permission into U6 AND gate (2A);
// hardware-gated by TREAD_OK; R27 10k pull-down fails safe.
inline constexpr int kTxEnable = 15;      // TX_ENABLE = GPIO15

// net K1_NC_FB — K1 pole-B normally-closed dry-contact feedback
// (R25 10k pull-up: HIGH = NC contact open)
inline constexpr int kK1NcFb = 4;         // K1_NC_FB = GPIO4

// net K1_NO_FB — K1 pole-B normally-open dry-contact feedback
// (R26 10k pull-up: HIGH = NO contact open)
inline constexpr int kK1NoFb = 5;         // K1_NO_FB = GPIO5

// net VBUS_PRESENT_N — ACTIVE-LOW USB VBUS presence (Q2 open-drain pulls
// low when VBUS present; R30 10k pull-up). Invert in software.
inline constexpr int kVbusPresentN = 7;   // VBUS_PRESENT_N = GPIO7

// net STATUS_LED — green LED1 via R11 330R
inline constexpr int kStatusLed = 38;     // STATUS_LED = GPIO38

}  // namespace esp32tap::pins
