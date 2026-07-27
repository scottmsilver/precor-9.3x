/*
 * firmware_context.h — shared state wired up by app_main and consumed by
 * the three supervised tasks. All controller access goes through
 * controller_mu (held only around controller method calls, all sub-ms).
 */

#pragma once

#include <mutex>

#include "engine/emulation_cycle.h"
#include "engine/mode_state.h"
#include "engine/serial_io.h"
#include "esp32_safety_io.h"
#include "esp32_time.h"
#include "esp32_uart_port.h"
#include "safety/safety_controller.h"

namespace esp32tap {

struct FirmwareContext {
    esp_hal::Esp32Clock clock;
    esp_hal::Esp32SafetyIo safety_io;
    esp_hal::ConsoleMotorUart console_uart;  // UART1: console RX + motor TX
    esp_hal::MotorTapUart motor_tap;         // UART2: motor RX tap

    std::mutex controller_mu;
    safety::SafetyController controller;

    ModeStateMachine mode;
    SerialReader<esp_hal::ConsoleMotorUart> console_reader{console_uart};
    SerialReader<esp_hal::MotorTapUart> motor_reader{motor_tap};
    SerialWriter<esp_hal::ConsoleMotorUart> motor_writer{console_uart};
    EmulationCycle<esp_hal::ConsoleMotorUart> emulate_cycle{motor_writer, mode};

    // Timestamp of the last console RX byte (for gap qualification).
    int64_t last_console_rx_us = 0;

    // Apply controller output state to the hardware. Caller holds
    // controller_mu.
    void apply_outputs_locked() {
        safety_io.set_tx_enable(controller.tx_enable());
        safety_io.set_relay_cmd(controller.relay_cmd());
    }
};

}  // namespace esp32tap
