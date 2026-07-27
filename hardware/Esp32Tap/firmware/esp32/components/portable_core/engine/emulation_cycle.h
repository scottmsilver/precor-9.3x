/*
 * emulation_cycle.h — EmulationCycle: 14-key cycle, safety timeout
 *
 * Replaces the console by sending a synthesized KV command cycle
 * to the motor. Reads params from ModeStateMachine::snapshot().
 *
 * ESP32TAP FORK of cpp/engine/emulation_engine.h — see PROVENANCE.md.
 * The std::thread lifecycle (start/stop/join, thread_fn loop) is
 * removed; the run-loop body becomes tick(now_us) driven by the
 * owning FreeRTOS task (target) or the test harness (host), which
 * also owns the 100 ms inter-burst gap and the task-WDT feed.
 * Time is injected as monotonic microseconds instead of
 * clock_gettime(CLOCK_MONOTONIC). Cycle table, burst layout,
 * part=6/diag=0/loop=5550, and the 3-hour timeout are unchanged.
 */

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <functional>
#include "protocol/kv_protocol.h"
#include "mode_state.h"
#include "serial_io.h"

constexpr int EMU_TIMEOUT_SEC = 3 * 3600;  // 3 hours
constexpr int64_t EMU_TIMEOUT_US = static_cast<int64_t>(EMU_TIMEOUT_SEC) * 1000000;
constexpr int EMU_BURST_GAP_MS = 100;      // ~100ms gap between bursts (owned by caller)

// 14-key cycle entry
struct KvCycleEntry {
    const char* key;
    bool has_value;  // true = dynamic value, false = bare [key] command
};

static constexpr KvCycleEntry KV_CYCLE[14] = {
    { "inc",  true  },   //  0: incline (half-pct, uppercase hex)
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
class EmulationCycle {
public:
    using KvEventCallback = std::function<void(std::string_view key, std::string_view value)>;

    EmulationCycle(SerialWriter<Port>& writer, ModeStateMachine& mode)
        : writer_(writer), mode_(mode) {}

    // Set callback for emitted KV events
    void on_kv_event(KvEventCallback cb) { kv_cb_ = std::move(cb); }

    // Re-arm cycle + 3-hour timer (call when emulate starts)
    void reset(int64_t now_us) {
        burst_ = 0;
        last_activity_us_ = now_us;
        prev_speed_ = -1;
        prev_incline_ = -1;
        timeout_fired_ = false;
    }

    // Returns true exactly once after the 3-hour safety timeout zeroed
    // the mode machine, so the owning task can also zero the
    // authoritative safety controller (which this engine does not know
    // about) before the next controller->mode mirror. Fork extension vs
    // cpp/emulation_engine.h, where a single authoritative state makes
    // this unnecessary — see PROVENANCE.md.
    bool consume_safety_timeout() {
        bool fired = timeout_fired_;
        timeout_fired_ = false;
        return fired;
    }

    // Send one burst of the 14-key cycle if emulating. The caller sleeps
    // EMU_BURST_GAP_MS between calls. Returns true if a burst was sent.
    bool tick(int64_t now_us) {
        if (!mode_.is_emulating()) {
            burst_ = 0;
            return false;
        }

        // Reset 3-hour timer whenever speed or incline changes
        auto snap_check = mode_.snapshot();
        if (snap_check.speed_tenths != prev_speed_ || snap_check.incline != prev_incline_) {
            last_activity_us_ = now_us;
            prev_speed_ = snap_check.speed_tenths;
            prev_incline_ = snap_check.incline;
        }

        // Safety timeout: reset speed/incline to 0 after 3 hours of no changes
        if (now_us - last_activity_us_ >= EMU_TIMEOUT_US) {
            if (snap_check.speed_tenths != 0 || snap_check.incline != 0) {
                mode_.safety_timeout_reset();
                timeout_fired_ = true;
            }
        }

        StateSnapshot snap = mode_.snapshot();

        for (int slot = 0; slot < 4; slot++) {
            int idx = BURSTS[burst_][slot];
            if (idx < 0) break;
            if (!mode_.is_emulating()) return false;

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
        burst_ = (burst_ + 1) % 5;
        return true;
    }

private:
    std::string value_for(int idx, const StateSnapshot& snap) {
        switch (idx) {
            case 0:  return encode_incline_hex(snap.incline);   // inc
            case 1:  return encode_speed_hex(snap.speed_tenths); // hmph
            case 9:  return "6";     // part
            case 12: return "0";     // diag
            case 13: return "5550";  // loop
            default: return {};
        }
    }

    SerialWriter<Port>& writer_;
    ModeStateMachine& mode_;
    int burst_ = 0;
    int64_t last_activity_us_ = 0;
    int prev_speed_ = -1;
    int prev_incline_ = -1;
    bool timeout_fired_ = false;
    KvEventCallback kv_cb_;
};
