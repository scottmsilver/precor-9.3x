/*
 * emulation_engine.h — EmulationEngine: 14-key cycle, safety timeout
 *
 * Replaces the console by sending a synthesized KV command cycle
 * to the motor. Owns the emulate thread lifecycle (RAII: destructor
 * joins). Reads params from ModeStateMachine::snapshot().
 */

#pragma once

#include <cstdio>
#include <ctime>
#include <string>
#include <string_view>
#include <thread>
#include <atomic>
#include <functional>
#include "kv_protocol.h"
#include "mode_state.h"
#include "serial_io.h"

constexpr int EMU_TIMEOUT_SEC = 3 * 3600;  // 3 hours

// 14-key cycle entry
struct KvCycleEntry {
    const char* key;
    bool has_value;  // true = dynamic value, false = bare [key] command
};

static constexpr KvCycleEntry KV_CYCLE[14] = {
    { "inc",  true  },   //  0: incline (decimal int)
    { "hmph", true  },   //  1: speed (mph*100, uppercase hex)
    { "amps", false },   //  2
    { "err",  false },   //  3
    { "belt", false },   //  4
    { "vbus", false },   //  5
    { "lift", false },   //  6
    { "lfts", false },   //  7
    { "lftg", false },   //  8
    { "part", true  },   //  9: always "6"
    { "ver",  false },   // 10
    { "type", false },   // 11
    { "diag", true  },   // 12: always "0"
    { "loop", true  },   // 13: always "5550"
};

// Which KV_CYCLE indices belong to each burst (-1 = end)
static constexpr int BURSTS[5][4] = {
    { 0, 1, -1, -1 },       // inc, hmph
    { 2, 3, 4, -1 },        // amps, err, belt
    { 5, 6, 7, 8 },         // vbus, lift, lfts, lftg
    { 9, 10, 11, -1 },      // part, ver, type
    { 12, 13, -1, -1 },     // diag, loop
};

template <typename Port>
class EmulationEngine {
public:
    using KvEventCallback = std::function<void(std::string_view key, std::string_view value)>;

    EmulationEngine(SerialWriter<Port>& writer, ModeStateMachine& mode)
        : writer_(writer), mode_(mode) {}

    ~EmulationEngine() {
        stop();
    }

    // Set callback for emitted KV events (pushed to ring buffer)
    void on_kv_event(KvEventCallback cb) { kv_cb_ = std::move(cb); }

    // Start the emulate thread
    void start() {
        stop();  // join any existing thread first
        running_.store(true, std::memory_order_relaxed);
        thread_ = std::thread(&EmulationEngine::thread_fn, this);
    }

    // Stop the emulate thread and wait for it to exit
    void stop() {
        running_.store(false, std::memory_order_relaxed);
        if (thread_.joinable()) {
            thread_.join();
        }
    }

    bool is_running() const { return running_.load(std::memory_order_relaxed); }

private:
    static void sleep_ms(int ms) {
        struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
        nanosleep(&ts, nullptr);
    }

    std::string value_for(int idx, const StateSnapshot& snap) {
        switch (idx) {
            case 0:  return std::to_string(snap.incline);      // inc
            case 1:  return encode_speed_hex(snap.speed_tenths); // hmph
            case 9:  return "6";     // part
            case 12: return "0";     // diag
            case 13: return "5550";  // loop
            default: return {};
        }
    }

    void thread_fn() {
        struct timespec start_ts;
        clock_gettime(CLOCK_MONOTONIC, &start_ts);

        while (running_.load(std::memory_order_relaxed) && mode_.is_emulating()) {
            // Safety timeout: reset speed/incline to 0 after 3 hours
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            double elapsed = (now.tv_sec - start_ts.tv_sec) +
                             (now.tv_nsec - start_ts.tv_nsec) / 1e9;

            if (elapsed >= EMU_TIMEOUT_SEC) {
                auto snap = mode_.snapshot();
                if (snap.speed_tenths != 0 || snap.incline != 0) {
                    mode_.safety_timeout_reset();
                    std::fprintf(stderr, "[emulate] 3-hour safety timeout — speed/incline reset to 0\n");
                }
            }

            StateSnapshot snap = mode_.snapshot();

            for (int burst = 0; burst < 5; burst++) {
                if (!running_.load(std::memory_order_relaxed) || !mode_.is_emulating()) goto done;

                for (int slot = 0; slot < 4; slot++) {
                    int idx = BURSTS[burst][slot];
                    if (idx < 0) break;
                    if (!running_.load(std::memory_order_relaxed) || !mode_.is_emulating()) goto done;

                    std::string_view key(KV_CYCLE[idx].key);
                    std::string value;
                    if (KV_CYCLE[idx].has_value) {
                        value = value_for(idx, snap);
                    }

                    writer_.write_kv(key, value);

                    if (kv_cb_) {
                        kv_cb_(key, value);
                    }
                }
                sleep_ms(100);  // ~100ms gap between bursts
            }
        }
    done:
        running_.store(false, std::memory_order_relaxed);
    }

    SerialWriter<Port>& writer_;
    ModeStateMachine& mode_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    KvEventCallback kv_cb_;
};
