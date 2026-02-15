/*
 * gpio_mock.h — MockGpioPort: test double for GpioPort interface
 *
 * Records all GPIO calls and allows injecting serial read data.
 * Uses STL containers (test-only, not in production hot paths).
 */

#pragma once

#include <cstdint>
#include <span>
#include <string_view>
#include <vector>
#include <deque>
#include <mutex>
#include <algorithm>
#include <string>
#include "gpio_port.h"

// Pulse structure matching pigpio's gpioPulse_t
#ifndef GPIO_MOCK_PULSE_DEFINED
#define GPIO_MOCK_PULSE_DEFINED
struct gpioPulse_t {
    uint32_t gpioOn;
    uint32_t gpioOff;
    uint32_t usDelay;
};
#endif

struct MockGpioPort {
    // --- State tracking ---
    bool initialised = false;
    struct PinState {
        int mode = -1;
        int level = 0;
        bool serial_open = false;
        int serial_baud = 0;
        int serial_invert = 0;
    };
    PinState pins[64]{};

    // --- Serial read injection ---
    std::mutex inject_mu;
    std::deque<std::vector<uint8_t>> inject_data;  // legacy: any-pin queue
    // Per-pin inject queues (used when caller specifies a pin)
    std::deque<std::vector<uint8_t>> pin_inject_data[64];

    // Legacy: inject data that any serial_read call can consume
    void inject_serial_data(std::span<const uint8_t> data) {
        std::lock_guard<std::mutex> lk(inject_mu);
        inject_data.emplace_back(data.begin(), data.end());
    }

    void inject_serial_data(std::string_view str) {
        // reinterpret_cast: char -> uint8_t aliasing (standard-allowed)
        inject_serial_data(std::span<const uint8_t>(
            reinterpret_cast<const uint8_t*>(str.data()), str.size()));
    }

    // Per-pin: inject data only readable by serial_read(pin, ...)
    void inject_serial_data_pin(int pin, std::span<const uint8_t> data) {
        std::lock_guard<std::mutex> lk(inject_mu);
        if (pin >= 0 && pin < 64)
            pin_inject_data[pin].emplace_back(data.begin(), data.end());
    }

    void inject_serial_data_pin(int pin, std::string_view str) {
        // reinterpret_cast: char -> uint8_t aliasing (standard-allowed)
        inject_serial_data_pin(pin, std::span<const uint8_t>(
            reinterpret_cast<const uint8_t*>(str.data()), str.size()));
    }

    // --- Wave write recording ---
    std::mutex wave_mu;
    struct WaveRecord {
        int gpio;
        std::vector<uint8_t> bytes;  // decoded from pulses
    };
    std::vector<WaveRecord> wave_writes;
    std::vector<gpioPulse_t> pending_pulses;
    int next_wave_id = 0;
    int last_wave_gpio = -1;

    // --- GpioPort interface ---
    int initialise() { initialised = true; return 0; }
    void terminate() { initialised = false; }

    void set_mode(int pin, int mode) {
        if (pin >= 0 && pin < 64) pins[pin].mode = mode;
    }

    void write(int pin, int level) {
        if (pin >= 0 && pin < 64) pins[pin].level = level;
    }

    int serial_read_open(int pin, int baud, int bits) {
        (void)bits;
        if (pin >= 0 && pin < 64) {
            pins[pin].serial_open = true;
            pins[pin].serial_baud = baud;
        }
        return 0;
    }

    void serial_read_invert(int pin, int invert) {
        if (pin >= 0 && pin < 64) pins[pin].serial_invert = invert;
    }

    // serial_read: pigpio C API boundary — takes void*, returns data
    int serial_read(int pin, void* buf, int bufsize) {
        std::lock_guard<std::mutex> lk(inject_mu);
        auto* dst = static_cast<uint8_t*>(buf);
        // Check per-pin queue first
        if (pin >= 0 && pin < 64 && !pin_inject_data[pin].empty()) {
            auto& front = pin_inject_data[pin].front();
            int n = static_cast<int>(front.size());
            if (n > bufsize) n = bufsize;
            std::copy_n(front.data(), n, dst);
            pin_inject_data[pin].pop_front();
            return n;
        }
        // Fall back to legacy any-pin queue
        if (inject_data.empty()) return 0;
        auto& front = inject_data.front();
        int n = static_cast<int>(front.size());
        if (n > bufsize) n = bufsize;
        std::copy_n(front.data(), n, dst);
        inject_data.pop_front();
        return n;
    }

    void serial_read_close(int pin) {
        if (pin >= 0 && pin < 64) pins[pin].serial_open = false;
    }

    int wave_tx_busy() { return 0; }
    void wave_clear() { pending_pulses.clear(); }

    void wave_add_generic(int num_pulses, gpioPulse_t* pulses) {
        for (int i = 0; i < num_pulses; i++) {
            pending_pulses.push_back(pulses[i]);
            // Track which gpio pin is being written to
            if (pulses[i].gpioOn) {
                for (int b = 0; b < 32; b++) {
                    if (pulses[i].gpioOn & (1u << b)) last_wave_gpio = b;
                }
            }
            if (pulses[i].gpioOff) {
                for (int b = 0; b < 32; b++) {
                    if (pulses[i].gpioOff & (1u << b)) last_wave_gpio = b;
                }
            }
        }
    }

    int wave_create() { return next_wave_id++; }

    void wave_tx_send(int /*wid*/, int /*mode*/) {
        // Decode inverted RS-485 pulses back to bytes
        if (pending_pulses.empty()) return;
        WaveRecord rec;
        rec.gpio = last_wave_gpio;

        // 10 pulses per byte: start + 8 data + stop
        int npulses = static_cast<int>(pending_pulses.size());
        for (int i = 0; i + 9 < npulses; i += 10) {
            // Skip start bit (pulse i), decode 8 data bits (i+1..i+8)
            uint8_t byte_val = 0;
            for (int bit = 0; bit < 8; bit++) {
                auto& p = pending_pulses.at(static_cast<size_t>(i + 1 + bit));
                // Inverted: gpioOff means "1" (LOW = 1)
                if (p.gpioOff) byte_val |= (1u << bit);
            }
            rec.bytes.push_back(byte_val);
            // pulse i+9 is stop bit
        }

        std::lock_guard<std::mutex> lk(wave_mu);
        wave_writes.push_back(std::move(rec));
    }

    void wave_delete(int /*wid*/) {}

    // --- Test helpers ---
    std::vector<uint8_t> get_all_written_bytes() {
        std::lock_guard<std::mutex> lk(wave_mu);
        std::vector<uint8_t> all;
        for (auto& w : wave_writes) {
            all.insert(all.end(), w.bytes.begin(), w.bytes.end());
        }
        return all;
    }

    std::string get_written_string() {
        auto bytes = get_all_written_bytes();
        return std::string(bytes.begin(), bytes.end());
    }

    void clear_writes() {
        std::lock_guard<std::mutex> lk(wave_mu);
        wave_writes.clear();
    }
};
