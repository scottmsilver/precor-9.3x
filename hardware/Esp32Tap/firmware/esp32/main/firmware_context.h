/*
 * firmware_context.h — shared state wired up by app_main and consumed by
 * the three supervised tasks. All controller access goes through
 * controller_mu (held only around controller method calls, all sub-ms).
 */

#pragma once

#include <array>
#include <mutex>
#include <string_view>

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

// Last-seen motor-tap KV values (raw hex strings) for the native
// server tier's status "motor" dict + live speed/incline fallback.
// Written by the serial engine (under controller_mu, zero-alloc fixed
// slots); snapshotted by the executor's DeviceModel under the same
// mutex.
class MotorKvCache {
public:
    static constexpr int MAX = 16;

    void put(std::string_view key, std::string_view value) {
        if (key.empty() || key.size() > 7 || value.size() > 15) return;
        for (int i = 0; i < count_; i++) {
            auto& e = entries_.at(static_cast<size_t>(i));
            if (key == std::string_view(e.key.data())) {
                size_t n = value.copy(e.val.data(), e.val.size() - 1);
                e.val.at(n) = '\0';
                return;
            }
        }
        if (count_ >= MAX) return;
        auto& e = entries_.at(static_cast<size_t>(count_));
        size_t nk = key.copy(e.key.data(), e.key.size() - 1);
        e.key.at(nk) = '\0';
        size_t nv = value.copy(e.val.data(), e.val.size() - 1);
        e.val.at(nv) = '\0';
        count_++;
    }

    template <typename Fn>
    void snapshot(Fn&& fn) const {
        for (int i = 0; i < count_; i++) {
            const auto& e = entries_.at(static_cast<size_t>(i));
            fn(std::string_view(e.key.data()), std::string_view(e.val.data()));
        }
    }

private:
    struct Entry {
        std::array<char, 8> key{};
        std::array<char, 16> val{};
    };
    std::array<Entry, MAX> entries_{};
    int count_ = 0;
};

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

    // Bus KV caches for the native server tier (see MotorKvCache).
    // server.py forwards a {"type":"kv"} WS event for EVERY source the
    // C++ binary decodes, and the app's Debug log columns on it, so all
    // three are cached, not just the motor tap:
    //   motor_kv   — motor -> console tap (also feeds status "motor")
    //   console_kv — console -> motor tap (the inbound command stream)
    //   emulate_kv — the frames this device synthesizes while emulating
    MotorKvCache motor_kv;
    MotorKvCache console_kv;
    MotorKvCache emulate_kv;

    // Apply controller output state to the hardware. Caller holds
    // controller_mu.
    void apply_outputs_locked() {
        safety_io.set_tx_enable(controller.tx_enable());
        safety_io.set_relay_cmd(controller.relay_cmd());
    }
};

}  // namespace esp32tap
