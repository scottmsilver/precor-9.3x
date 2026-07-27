/*
 * fake_hal.h — host-test implementation of portable_core/hal/hal.h.
 *
 * Manually advanced clock, scriptable RX byte queues, captured TX bytes
 * with timestamps, settable tread_ok/feedback/vbus, and an ordered log of
 * relay/tx_enable edges (used to assert entry/exit output ordering).
 * Test-only: STL containers are fine here.
 */

#pragma once

#include <cstdint>
#include <deque>
#include <string>
#include <string_view>
#include <vector>
#include <algorithm>
#include <span>

#include "hal/hal.h"

namespace esp32tap::fake {

struct FakeClock final : hal::Clock {
    int64_t t_us = 0;
    int64_t now_us() override { return t_us; }
    void advance_us(int64_t d) { t_us += d; }
    void advance_ms(int64_t d) { t_us += d * 1000; }
};

struct FakeSerialIn final : hal::SerialIn {
    std::deque<std::vector<uint8_t>> queue;

    void inject(std::string_view s) {
        // reinterpret_cast: char -> uint8_t aliasing (standard-allowed)
        const uint8_t* p = reinterpret_cast<const uint8_t*>(s.data());
        queue.emplace_back(p, p + s.size());
    }
    void inject(std::span<const uint8_t> bytes) {
        queue.emplace_back(bytes.begin(), bytes.end());
    }

    size_t read(std::span<uint8_t> out) override {
        if (queue.empty()) return 0;
        auto& front = queue.front();
        size_t n = std::min(out.size(), front.size());
        std::copy_n(front.data(), n, out.data());
        if (n == front.size()) {
            queue.pop_front();
        } else {
            front.erase(front.begin(),
                        front.begin() + static_cast<long>(n));
        }
        return n;
    }
};

struct FakeSerialOut final : hal::SerialOut {
    struct Write {
        int64_t at_us;
        std::vector<uint8_t> bytes;
    };
    std::vector<Write> writes;
    bool idle_low = true;
    FakeClock* clock = nullptr;  // optional timestamp source

    bool write(std::span<const uint8_t> bytes) override {
        Write w;
        w.at_us = clock != nullptr ? clock->t_us : 0;
        w.bytes.assign(bytes.begin(), bytes.end());
        writes.push_back(std::move(w));
        return true;
    }
    bool tx_idle_low() override { return idle_low; }

    std::string all_written() const {
        std::string s;
        for (const auto& w : writes) {
            s.append(w.bytes.begin(), w.bytes.end());
        }
        return s;
    }
    void clear() { writes.clear(); }
};

struct FakeSafetyIo final : hal::SafetyIo {
    bool relay_cmd = false;
    bool tx_en = false;
    bool tread_ok_level = true;
    bool nc_high = false;  // BYPASS at rest: NC closed (low), NO open (high)
    bool no_high = true;
    bool vbus = false;
    bool led = false;

    // Ordered edge log: "relay_cmd:1", "tx_enable:0", ... with timestamps.
    struct Edge {
        std::string what;
        int64_t at_us;
    };
    std::vector<Edge> edges;
    FakeClock* clock = nullptr;

    void log(std::string_view what, bool on) {
        Edge e;
        e.what = std::string(what) + (on ? ":1" : ":0");
        e.at_us = clock != nullptr ? clock->t_us : 0;
        edges.push_back(std::move(e));
    }

    void set_relay_cmd(bool on) override {
        if (on != relay_cmd) log("relay_cmd", on);
        relay_cmd = on;
    }
    void set_tx_enable(bool on) override {
        if (on != tx_en) log("tx_enable", on);
        tx_en = on;
    }
    bool tread_ok() override { return tread_ok_level; }
    bool k1_nc_high() override { return nc_high; }
    bool k1_no_high() override { return no_high; }
    bool vbus_present() override { return vbus; }
    void set_status_led(bool on) override { led = on; }

    void set_feedback_bypass() { nc_high = false; no_high = true; }
    void set_feedback_emulate() { nc_high = true; no_high = false; }
    void set_feedback_both_closed() { nc_high = false; no_high = false; }
    void set_feedback_both_open() { nc_high = true; no_high = true; }
};

// Simple Port for SerialReader/SerialWriter templates over the fakes.
struct FakePort {
    FakeSerialIn in;
    FakeSerialOut out;
    size_t read(std::span<uint8_t> o) { return in.read(o); }
    bool write(std::span<const uint8_t> bytes) { return out.write(bytes); }
    bool tx_idle_low() { return out.tx_idle_low(); }
};

}  // namespace esp32tap::fake
