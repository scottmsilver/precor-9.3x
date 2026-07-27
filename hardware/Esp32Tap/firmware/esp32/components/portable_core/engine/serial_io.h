/*
 * serial_io.h — SerialReader and SerialWriter templates
 *
 * SerialReader: manages parse buffer, reads raw serial data from a Port,
 * feeds KV pairs to a callback. Exposes raw bytes for telemetry.
 *
 * SerialWriter: whole-message TX through a Port. Internal mutex
 * serializes output.
 *
 * Both are templated on the Port type for compile-time polymorphism.
 *
 * ESP32TAP FORK of cpp/engine/serial_io.h — see PROVENANCE.md.
 * The pigpio bb_serial open/invert/close calls and the DMA-wave
 * pulse building are deleted; the Port supplies raw inverted-UART
 * RX/TX (hardware uart_set_line_inverse on target, scripted bytes
 * on the host fake). Parse-buffer logic is unchanged.
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
#include "protocol/kv_protocol.h"

constexpr int BAUD = 9600;
constexpr int BIT_US = 1000000 / BAUD;  // ~104 us per bit

template <typename Port>
class SerialReader {
public:
    using KvCallback = std::function<void(const KvPair&)>;
    using RawCallback = std::function<void(std::span<const uint8_t>)>;

    explicit SerialReader(Port& port)
        : port_(port), parse_len_(0) {}

    // Set callback for parsed KV pairs
    void on_kv(KvCallback cb) { kv_cb_ = std::move(cb); }

    // Set callback for raw bytes (called before parsing, for telemetry)
    void on_raw(RawCallback cb) { raw_cb_ = std::move(cb); }

    // Poll for new data. Returns number of raw bytes read.
    // Calls raw callback first, then parses and calls kv callback.
    int poll() {
        // rawbuf_/pairs_ are members, not stack locals: KvPair[32] is 4 KB
        // and PLAN.md's QEMU-validated stack constraint forbids large
        // on-stack parser buffers in the serial engine task.
        int count = static_cast<int>(port_.read(std::span<uint8_t>(rawbuf_)));
        if (count <= 0) return 0;

        // Fire raw callback before parsing (low-latency telemetry path)
        if (raw_cb_) {
            raw_cb_(std::span<const uint8_t>(rawbuf_.data(), static_cast<size_t>(count)));
        }

        // Append to parse buffer
        int space = static_cast<int>(parsebuf_.size()) - parse_len_;
        if (count > space) count = space;
        std::copy_n(rawbuf_.data(), count, parsebuf_.data() + parse_len_);
        parse_len_ += count;

        // Parse KV pairs
        int consumed = 0;
        int n = kv_parse(std::span<const uint8_t>(parsebuf_.data(), static_cast<size_t>(parse_len_)),
                         pairs_.data(), 32, &consumed);

        if (kv_cb_) {
            for (int i = 0; i < n; i++) {
                kv_cb_(pairs_.at(static_cast<size_t>(i)));
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
    std::array<uint8_t, 512> rawbuf_{};
    std::array<uint8_t, 4096> parsebuf_{};
    std::array<KvPair, 32> pairs_{};
    int parse_len_;
    KvCallback kv_cb_;
    RawCallback raw_cb_;
};


template <typename Port>
class SerialWriter {
public:
    explicit SerialWriter(Port& port)
        : port_(port) {}

    // Max bytes per write — KV commands are short (e.g. "[hmph:78]\xff").
    static constexpr int MAX_WRITE_BYTES = 50;

    // Write bytes as one whole message through the Port (hardware-inverted
    // UART on target; the S3's 128-byte TX FIFO keeps a <=50-byte KV
    // message hardware-contiguous). Thread-safe: serialized by internal mutex.
    void write_bytes(std::span<const uint8_t> data) {
        if (data.empty()) return;
        if (static_cast<int>(data.size()) > MAX_WRITE_BYTES) return;  // reject oversized writes

        std::lock_guard<std::mutex> lk(write_mu_);
        port_.write(data);
    }

    void write_kv(std::string_view key, std::string_view value = {}) {
        auto cmd = kv_build(key, value);
        // reinterpret_cast: char -> uint8_t aliasing (standard-allowed wire boundary)
        write_bytes(std::span<const uint8_t>(
            reinterpret_cast<const uint8_t*>(cmd.data()), cmd.size()));
    }

private:
    Port& port_;
    std::mutex write_mu_;
};
