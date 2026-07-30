/*
 * esp32_uart_port.h — inverted-UART HAL implementation (ESP-IDF).
 *
 * UART1: CONS_RX (GPIO18) RX tap + ESP_TX (GPIO17) TX to U7 buffer.
 * UART2: PIN3_RX (GPIO16) RX-only motor tap.
 * Both 9600 8N1 with uart_set_line_inverse — hardware inversion replaces
 * the Pi's pigpio bb_serial_invert / hand-built inverted waveforms
 * (PLAN.md platform section).
 */

#pragma once

#include <cstdint>
#include <span>
#include <mutex>

#include "driver/uart.h"

#include "hal/hal.h"

namespace esp32tap::esp_hal {

// Console pin-6 tap (UART1 RX) + motor pin-6 TX (UART1 TX via U7).
class ConsoleMotorUart final : public hal::SerialIn, public hal::SerialOut {
public:
    // Installs the driver, configures 9600 8N1, RXD+TXD inversion.
    // Returns false on any driver error.
    bool init();

    size_t read(std::span<uint8_t> out) override;

    // Whole-message TX: uart_write_bytes + uart_wait_tx_done behind a
    // mutex. The S3's 128-byte TX FIFO keeps a <=50-byte KV message
    // hardware-contiguous (no inter-byte gaps).
    bool write(std::span<const uint8_t> bytes) override;

    // Physical idle check for emulate-entry step 3: with TXD inversion,
    // UART idle drives the pad LOW, so a low pad level means idle.
    bool tx_idle_low() override;

private:
    std::mutex tx_mu_;
    bool ready_ = false;
};

// Motor pin-3 passive tap (UART2, RX only).
class MotorTapUart final : public hal::SerialIn {
public:
    bool init();
    size_t read(std::span<uint8_t> out) override;

private:
    bool ready_ = false;
};

}  // namespace esp32tap::esp_hal
