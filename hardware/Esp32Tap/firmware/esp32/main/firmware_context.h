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

#if defined(ESP32TAP_QEMU_TEST)
#include "qemu_test/qemu_test_shim.h"
#endif

namespace esp32tap {

#if defined(ESP32TAP_QEMU_TEST)
// QEMU behavioral-test image only (tools/qemu_harness): scripted K1
// feedback / TREAD_OK / VBUS, motor tap remapped to UART0 RX (UART2 has
// no chardev in the pinned QEMU). Never flash to hardware.
using SafetyIoImpl = qemu_test::QemuTestSafetyIo;
using MotorTapImpl = qemu_test::QemuTestMotorTap;
#else
using SafetyIoImpl = esp_hal::Esp32SafetyIo;
using MotorTapImpl = esp_hal::MotorTapUart;
#endif

struct FirmwareContext {
    esp_hal::Esp32Clock clock;
    SafetyIoImpl safety_io;
    esp_hal::ConsoleMotorUart console_uart;  // UART1: console RX + motor TX
    MotorTapImpl motor_tap;                  // UART2: motor RX tap

    std::mutex controller_mu;
    safety::SafetyController controller;

    ModeStateMachine mode;
    SerialReader<esp_hal::ConsoleMotorUart> console_reader{console_uart};
    SerialReader<MotorTapImpl> motor_reader{motor_tap};
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
