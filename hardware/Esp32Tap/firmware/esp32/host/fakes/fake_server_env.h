/*
 * fake_server_env.h — host-test doubles for the native server tier:
 * FakeTime (controllable monotonic + wall clock), FakeModel (recording
 * ServerModel with device-parity clamp/auto-emulate semantics), and a
 * ready-wired TestServer bundle (stores in a temp dir + ServerCore).
 */

#pragma once

#include <cstdlib>
#include <string>
#include <vector>

#include "fs_api.h"
#include "history_store.h"
#include "json_fmt.h"
#include "json_store.h"
#include "profile_store.h"
#include "run_store.h"
#include "server_core.h"
#include "server_model.h"
#include "time_source.h"
#include "workout_store.h"

namespace esp32tap::test {

class FakeTime : public exec::TimeSource {
public:
    int64_t now = 1000000;                 // µs
    int64_t wall_minutes = 29579040;       // arbitrary 2026 epoch-minutes
    int64_t now_us() override { return now; }
    std::string now_iso() override {
        // Deterministic wall clock derived from epoch-minutes.
        int64_t days = wall_minutes / 1440;
        int64_t mins = wall_minutes % 1440;
        // Rough civil conversion is unnecessary — stores only need
        // lexicographic ordering + relative_time parsing. Fabricate a
        // fixed date with minutes advancing.
        int64_t hh = mins / 60, mm = mins % 60;
        auto two = [](int64_t v) {
            std::string s = exec::fmt_int(v);
            return s.size() < 2 ? "0" + s : s;
        };
        return "2026-07-" + two(1 + (days % 28)) + "T" + two(hh) + ":" +
               two(mm) + ":00";
    }
    void advance_s(int64_t s) {
        now += s * 1000000;
        wall_minutes += s / 60;
    }
};

class FakeModel : public api::ServerModel {
public:
    explicit FakeModel(exec::TimeSource& ts) : ts_(ts) {}

    // device-parity state
    bool proxy = true;
    bool emulate = false;
    int emu_speed_tenths = 0;
    int emu_incline_half = 0;
    int bus_speed_tenths = -1;
    int bus_incline_half = -1;
    bool treadmill_connected = true;
    // Device parity: motion authority refused (lease held elsewhere).
    // hw_set_* return false — EXCEPT zero-speed, which the device
    // escalates to emergency_stop and reports success.
    bool refuse_motion = false;
    std::vector<std::pair<std::string, std::string>> motor_kv;
    std::vector<std::pair<std::string, std::string>> console_kv;
    std::vector<std::pair<std::string, std::string>> emulate_kv;

    // recordings
    std::vector<double> hw_speeds;
    std::vector<double> hw_inclines;
    std::vector<std::string> frames;
    std::vector<api::WsKind> frame_kinds;

    api::StatusSnapshot status() override {
        api::StatusSnapshot st;
        st.proxy = proxy;
        st.emulate = emulate;
        st.emu_speed_tenths = emu_speed_tenths;
        st.emu_incline_half = emu_incline_half;
        st.bus_speed_tenths = bus_speed_tenths;
        st.bus_incline_half = bus_incline_half;
        st.treadmill_connected = treadmill_connected;
        st.kv_count = 0;
        for (const auto& [k, v] : motor_kv) {
            if (st.kv_count >= api::StatusSnapshot::MAX_KV) break;
            auto& slot = st.kv.at(static_cast<size_t>(st.kv_count));
            k.copy(slot.key.data(), slot.key.size() - 1);
            v.copy(slot.val.data(), slot.val.size() - 1);
            st.kv_count++;
        }
        return st;
    }

    bool hw_set_speed(double mph) override {
        if (!treadmill_connected) return false;
        if (refuse_motion) {
            if (mph > 0) return false;
            emu_speed_tenths = 0;  // device escalation: stop always lands
            emulate = false;
            proxy = true;
            return true;
        }
        hw_speeds.push_back(mph);
        int tenths = static_cast<int>(mph * 10);
        emu_speed_tenths = tenths < 0 ? 0 : (tenths > 120 ? 120 : tenths);
        if (mph > 0) {
            emulate = true;
            proxy = false;
        }
        return true;
    }

    bool hw_set_incline(double pct) override {
        if (!treadmill_connected) return false;
        if (refuse_motion) return false;
        hw_inclines.push_back(pct);
        int half = static_cast<int>(pct * 2);
        emu_incline_half = half < 0 ? 0 : (half > 30 ? 30 : half);
        if (pct > 0) {
            emulate = true;
            proxy = false;
        }
        return true;
    }

    bool set_emulate(bool enabled) override {
        if (!treadmill_connected) return false;
        if (enabled) {
            emulate = true;
            proxy = false;
        } else {
            emulate = false;
        }
        return true;
    }

    bool set_proxy(bool enabled) override {
        if (!treadmill_connected) return false;
        if (enabled) {
            proxy = true;
            emulate = false;
        } else {
            proxy = false;
        }
        return true;
    }

    exec::TimeSource& time_source() override { return ts_; }

    // All three bus sources (device parity: motor tap, console tap and
    // the frames the device synthesizes while emulating).
    void kv_snapshot(api::KvSink& sink) override {
        for (const auto& [k, v] : motor_kv) sink.kv("motor", k, v);
        for (const auto& [k, v] : console_kv) sink.kv("console", k, v);
        for (const auto& [k, v] : emulate_kv) sink.kv("emulate", k, v);
    }

    void ws_broadcast(std::string&& json, api::WsKind kind) override {
        frame_kinds.push_back(kind);
        frames.push_back(std::move(json));
    }

private:
    exec::TimeSource& ts_;
};

// One fully wired core over a fresh temp directory.
struct TestServer {
    FakeTime time;
    FakeModel model{time};
    std::string dir;
    storage::PosixFs fs;
    storage::DirectPersist persist{fs};
    storage::HistoryStore history;
    storage::WorkoutStore workouts;
    storage::RunStore runs;
    storage::ProfileStore profiles;
    api::ServerCore core{model, time, history, workouts, runs, profiles};

    TestServer() : dir(make_tmp_dir()), fs(dir) {
        history.init(fs, persist, "program_history.json");
        workouts.init(fs, persist, "saved_workouts.json");
        runs.init(fs, persist, "run_history.json");
        profiles.init_with_state(fs, persist, "profiles.json",
                                 "profile_state.json");
    }

    static std::string make_tmp_dir() {
        std::string tmpl = "/tmp/esp32tap_store_test_XXXXXX";
        std::vector<char> buf(tmpl.begin(), tmpl.end());
        buf.push_back('\0');
        char* got = mkdtemp(buf.data());
        return got != nullptr ? std::string(got) : std::string("/tmp");
    }

    api::ApiResponse req(std::string_view method, std::string_view path,
                         std::string_view body = "") {
        return api::handle_request(core, method, path, body);
    }
};

}  // namespace esp32tap::test
