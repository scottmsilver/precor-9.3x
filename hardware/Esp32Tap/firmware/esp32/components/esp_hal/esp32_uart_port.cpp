/*
 * esp32_uart_port.cpp — inverted-UART HAL implementation (ESP-IDF).
 */

#include "esp32_uart_port.h"

#include "driver/gpio.h"

#include "pins.hpp"

namespace esp32tap::esp_hal {

namespace {

constexpr uart_port_t kConsoleUart = UART_NUM_1;
constexpr uart_port_t kMotorTapUart = UART_NUM_2;
constexpr int kRxBufBytes = 1024;  // >=128-byte FIFO; ~1s of 9600-baud RX

bool configure_uart(uart_port_t port, int tx_pin, int rx_pin,
                    uint32_t inverse_mask) {
    // Value-init then assign: designated init trips
    // -Werror=missing-field-initializers as IDF adds uart_config_t fields
    // across versions.
    uart_config_t cfg = {};
    cfg.baud_rate = 9600;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.rx_flow_ctrl_thresh = 0;
    cfg.source_clk = UART_SCLK_DEFAULT;
    if (uart_driver_install(port, kRxBufBytes, 0, 0, nullptr, 0) != ESP_OK) {
        return false;
    }
    if (uart_param_config(port, &cfg) != ESP_OK) return false;
    if (uart_set_pin(port, tx_pin, rx_pin, UART_PIN_NO_CHANGE,
                     UART_PIN_NO_CHANGE) != ESP_OK) {
        return false;
    }
    // RS-485-style idle-LOW bus: hardware inversion on both directions
    // (PLAN.md: uart_set_line_inverse replaces pigpio bb_serial_invert).
    if (uart_set_line_inverse(port, inverse_mask) != ESP_OK) return false;
    return true;
}

}  // namespace

bool ConsoleMotorUart::init() {
    if (!configure_uart(kConsoleUart, pins::kEspTx, pins::kConsRx,
                        UART_SIGNAL_RXD_INV | UART_SIGNAL_TXD_INV)) {
        return false;
    }
    // Enable the input path on the TX pad so tx_idle_low() can read the
    // physical line level for emulate-entry step 3.
    gpio_input_enable(static_cast<gpio_num_t>(pins::kEspTx));
    ready_ = true;
    return true;
}

size_t ConsoleMotorUart::read(std::span<uint8_t> out) {
    if (!ready_ || out.empty()) return 0;
    int n = uart_read_bytes(kConsoleUart, out.data(), out.size(), 0);
    return n > 0 ? static_cast<size_t>(n) : 0;
}

bool ConsoleMotorUart::write(std::span<const uint8_t> bytes) {
    if (!ready_ || bytes.empty()) return false;
    std::lock_guard<std::mutex> lk(tx_mu_);
    // reinterpret_cast: uint8_t -> char aliasing at the IDF C API boundary
    int written = uart_write_bytes(
        kConsoleUart, reinterpret_cast<const char*>(bytes.data()),
        bytes.size());
    if (written < 0 || static_cast<size_t>(written) != bytes.size()) {
        return false;
    }
    return uart_wait_tx_done(kConsoleUart, pdMS_TO_TICKS(100)) == ESP_OK;
}

bool ConsoleMotorUart::tx_idle_low() {
    if (!ready_) return false;
    return gpio_get_level(static_cast<gpio_num_t>(pins::kEspTx)) == 0;
}

bool MotorTapUart::init() {
    if (!configure_uart(kMotorTapUart, UART_PIN_NO_CHANGE, pins::kPin3Rx,
                        UART_SIGNAL_RXD_INV)) {
        return false;
    }
    ready_ = true;
    return true;
}

size_t MotorTapUart::read(std::span<uint8_t> out) {
    if (!ready_ || out.empty()) return 0;
    int n = uart_read_bytes(kMotorTapUart, out.data(), out.size(), 0);
    return n > 0 ? static_cast<size_t>(n) : 0;
}

}  // namespace esp32tap::esp_hal
