/*
 * hal.h — portable hardware abstraction consumed by the safety core.
 *
 * Implemented by components/esp_hal (ESP-IDF target) and
 * host/fakes/fake_hal.h (host tests). Pure-virtual is acceptable with
 * -fno-rtti -fno-exceptions (no dynamic_cast/throw); every call is
 * cold-path or 100 ms-cadence — vtable cost is irrelevant at 9600 baud.
 */

#pragma once

#include <cstdint>
#include <cstddef>
#include <span>

namespace esp32tap::hal {

struct Clock {
    virtual int64_t now_us() = 0;  // monotonic microseconds
protected:
    ~Clock() = default;
};

struct SerialIn {
    // Non-blocking read of available bytes into out; returns count.
    virtual size_t read(std::span<uint8_t> out) = 0;
protected:
    ~SerialIn() = default;
};

struct SerialOut {
    // Blocking whole-message TX (uart_write_bytes + uart_wait_tx_done).
    virtual bool write(std::span<const uint8_t> bytes) = 0;
    // Physical idle check for emulate-entry step 3 (line rests low).
    virtual bool tx_idle_low() = 0;
protected:
    ~SerialOut() = default;
};

struct SafetyIo {
    virtual void set_relay_cmd(bool on) = 0;
    virtual void set_tx_enable(bool on) = 0;
    virtual bool tread_ok() = 0;       // read-only by construction (R32-isolated)
    virtual bool k1_nc_high() = 0;     // K1 pole-B NC feedback (10k pull-up)
    virtual bool k1_no_high() = 0;     // K1 pole-B NO feedback (10k pull-up)
    virtual bool vbus_present() = 0;   // already inverted (GPIO7 is active-low)
    virtual void set_status_led(bool on) = 0;
protected:
    ~SafetyIo() = default;
};

}  // namespace esp32tap::hal
