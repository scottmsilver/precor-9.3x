/*
 * serial_io.h — SerialReader and SerialWriter templates
 *
 * SerialReader: manages parse buffer, reads raw GPIO serial data,
 * feeds KV pairs to a callback. Exposes raw bytes for proxy forwarding.
 *
 * SerialWriter: inverted RS-485 DMA waveform generation. Internal
 * mutex serializes wave output.
 *
 * Both are templated on the GpioPort type for compile-time polymorphism.
 */

#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <array>
#include <algorithm>
#include <mutex>
#include <functional>
#include "kv_protocol.h"

// gpioPulse_t: provided by pigpio.h (production) or gpio_mock.h (test).
// Define a compatible struct only if neither has been included yet.
#if !defined(PIGPIO_H)
#  if !defined(GPIO_MOCK_PULSE_DEFINED)
struct gpioPulse_t { uint32_t gpioOn; uint32_t gpioOff; uint32_t usDelay; };
#    define GPIO_MOCK_PULSE_DEFINED
#  endif
#endif

constexpr int BAUD = 9600;
constexpr int BIT_US = 1000000 / BAUD;  // ~104 us per bit

template <typename Port>
class SerialReader {
public:
    using KvCallback = std::function<void(const KvPair&)>;
    using RawCallback = std::function<void(std::span<const uint8_t>)>;

    SerialReader(Port& port, int gpio_pin)
        : port_(port), pin_(gpio_pin), parse_len_(0) {}

    bool open() {
        int rc = port_.serial_read_open(pin_, BAUD, 8);
        if (rc < 0) return false;
        port_.serial_read_invert(pin_, 1);  // RS-485 inverted polarity
        return true;
    }

    void close() {
        port_.serial_read_close(pin_);
    }

    // Set callback for parsed KV pairs
    void on_kv(KvCallback cb) { kv_cb_ = std::move(cb); }

    // Set callback for raw bytes (called before parsing, for proxy forwarding)
    void on_raw(RawCallback cb) { raw_cb_ = std::move(cb); }

    // Poll for new data. Returns number of raw bytes read.
    // Calls raw callback first, then parses and calls kv callback.
    int poll() {
        uint8_t rawbuf[512];
        // port_.serial_read takes void* — pigpio C API boundary
        int count = port_.serial_read(pin_, rawbuf, sizeof(rawbuf));
        if (count <= 0) return 0;

        // Fire raw callback before parsing (low-latency proxy path)
        if (raw_cb_) {
            raw_cb_(std::span<const uint8_t>(rawbuf, static_cast<size_t>(count)));
        }

        // Append to parse buffer
        int space = static_cast<int>(parsebuf_.size()) - parse_len_;
        if (count > space) count = space;
        std::copy_n(rawbuf, count, parsebuf_.data() + parse_len_);
        parse_len_ += count;

        // Parse KV pairs
        KvPair pairs[32];
        int consumed = 0;
        int n = kv_parse(std::span<const uint8_t>(parsebuf_.data(), static_cast<size_t>(parse_len_)),
                         pairs, 32, &consumed);

        if (kv_cb_) {
            for (int i = 0; i < n; i++) {
                kv_cb_(pairs[i]);
            }
        }

        // Shift unconsumed bytes to front (dst < src, so std::copy is safe)
        if (consumed > 0 && consumed < parse_len_) {
            std::copy(parsebuf_.data() + consumed,
                      parsebuf_.data() + parse_len_,
                      parsebuf_.data());
        }
        parse_len_ -= consumed;

        return count;
    }

private:
    Port& port_;
    int pin_;
    std::array<uint8_t, 4096> parsebuf_{};
    int parse_len_;
    KvCallback kv_cb_;
    RawCallback raw_cb_;
};


template <typename Port>
class SerialWriter {
public:
    SerialWriter(Port& port, int gpio_pin)
        : port_(port), pin_(gpio_pin) {}

    // Write bytes using inverted RS-485 DMA waveforms.
    // Thread-safe: serialized by internal mutex.
    void write_bytes(std::span<const uint8_t> data) {
        if (data.empty()) return;

        int len = static_cast<int>(data.size());
        uint32_t mask = 1u << pin_;

        // VLA for pulse array — hot path, no heap allocation
        gpioPulse_t pulses[len * 10 + 1];
        int np = 0;

        for (int b = 0; b < len; b++) {
            uint8_t byte_val = data[b];
            // Start bit: HIGH (inverted)
            pulses[np].gpioOn  = mask;
            pulses[np].gpioOff = 0;
            pulses[np].usDelay = BIT_US;
            np++;
            // 8 data bits, LSB first, INVERTED
            for (int bit = 0; bit < 8; bit++) {
                if ((byte_val >> bit) & 1) {
                    pulses[np].gpioOn  = 0;
                    pulses[np].gpioOff = mask;  // 1 -> LOW
                } else {
                    pulses[np].gpioOn  = mask;
                    pulses[np].gpioOff = 0;     // 0 -> HIGH
                }
                pulses[np].usDelay = BIT_US;
                np++;
            }
            // Stop bit: LOW (inverted idle)
            pulses[np].gpioOn  = 0;
            pulses[np].gpioOff = mask;
            pulses[np].usDelay = BIT_US;
            np++;
        }

        std::lock_guard<std::mutex> lk(write_mu_);

        while (port_.wave_tx_busy()) {
            // Busy-wait with 1ms sleep
            struct timespec ts = { 0, 1000000L };
            nanosleep(&ts, nullptr);
        }

        port_.wave_clear();
        port_.wave_add_generic(np, pulses);
        int wid = port_.wave_create();
        if (wid >= 0) {
            port_.wave_tx_send(wid, PORT_WAVE_MODE_ONE_SHOT);
            while (port_.wave_tx_busy()) {
                struct timespec ts = { 0, 1000000L };
                nanosleep(&ts, nullptr);
            }
            port_.wave_delete(wid);
        }
    }

    void write_kv(std::string_view key, std::string_view value = {}) {
        auto cmd = kv_build(key, value);
        // reinterpret_cast: char -> uint8_t aliasing (standard-allowed wire boundary)
        write_bytes(std::span<const uint8_t>(
            reinterpret_cast<const uint8_t*>(cmd.data()), cmd.size()));
    }

private:
    Port& port_;
    int pin_;
    std::mutex write_mu_;
};
