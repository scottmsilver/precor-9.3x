/*
 * test_emulation.cpp — Tests for EmulationCycle with FakePort
 *
 * ESP32TAP FORK of cpp/tests/test_emulation.cpp — see PROVENANCE.md.
 * The thread-based EmulationEngine seam (start/stop + sleeps) becomes
 * deterministic tick(now_us) calls on EmulationCycle; every original
 * assertion's intent is preserved (14-key order, speed/incline encoding,
 * stop on mode change, stop after watchdog reset). The 3-hour timeout,
 * previously untestable in real time, gains a fake-clock case.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "fakes/fake_hal.h"
#include "engine/serial_io.h"
#include "engine/emulation_cycle.h"
#include <vector>
#include <string>

using esp32tap::fake::FakePort;

namespace {

// Drive n bursts with 100 ms fake gaps; returns final time.
int64_t run_bursts(EmulationCycle<FakePort>& cycle, int n, int64_t t_us = 0) {
    for (int i = 0; i < n; i++) {
        cycle.tick(t_us);
        t_us += EMU_BURST_GAP_MS * 1000;
    }
    return t_us;
}

}  // namespace

TEST_CASE("emulation cycle sends 14-key cycle") {
    FakePort port;

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});  // no-op, we manage cycle directly
    mode.request_emulate(true);

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);

    std::vector<std::string> keys_sent;
    cycle.on_kv_event([&](std::string_view key, std::string_view /*value*/) {
        keys_sent.emplace_back(key);
    });

    cycle.reset(0);
    run_bursts(cycle, 5);  // one full cycle = 5 bursts

    // Should have sent at least 14 keys (one full cycle)
    CHECK(keys_sent.size() >= 14);

    // Verify the first 14 keys match the cycle order
    if (keys_sent.size() >= 14) {
        CHECK(keys_sent.at(0) == "inc");
        CHECK(keys_sent.at(1) == "hmph");
        CHECK(keys_sent.at(2) == "amps");
        CHECK(keys_sent.at(3) == "err");
        CHECK(keys_sent.at(4) == "belt");
        CHECK(keys_sent.at(5) == "vbus");
        CHECK(keys_sent.at(6) == "lift");
        CHECK(keys_sent.at(7) == "lfts");
        CHECK(keys_sent.at(8) == "lftg");
        CHECK(keys_sent.at(9) == "part");
        CHECK(keys_sent.at(10) == "ver");
        CHECK(keys_sent.at(11) == "type");
        CHECK(keys_sent.at(12) == "diag");
        CHECK(keys_sent.at(13) == "loop");
    }
}

TEST_CASE("emulation cycle applies speed and incline") {
    FakePort port;

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    // Set speed to 5.0 mph (50 tenths) and incline to 14 half-pct (7%)
    // Do this after emulate is enabled (which zeros values)
    mode.set_speed(50);
    mode.set_incline(14);

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);

    std::vector<std::pair<std::string, std::string>> kv_events;
    cycle.on_kv_event([&](std::string_view key, std::string_view value) {
        kv_events.emplace_back(std::string(key), std::string(value));
    });

    cycle.reset(0);
    run_bursts(cycle, 5);

    // Find inc and hmph events
    bool found_inc = false, found_hmph = false;
    for (auto& [k, v] : kv_events) {
        if (k == "inc" && v == "E") found_inc = true;
        if (k == "hmph") {
            // 50 tenths = 500 hundredths = 0x1F4
            if (v == "1F4") found_hmph = true;
        }
    }
    CHECK(found_inc);
    CHECK(found_hmph);
}

TEST_CASE("emulation cycle stops when mode changes") {
    FakePort port;

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);

    cycle.reset(0);
    CHECK(cycle.tick(0) == true);

    // Switch to proxy mode (disables emulate)
    mode.request_proxy(true);

    // Cycle must refuse to send anything more
    CHECK(cycle.tick(100'000) == false);
    CHECK(cycle.tick(200'000) == false);
}

TEST_CASE("emulation cycle stops after watchdog_reset_to_proxy") {
    FakePort port;

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);
    mode.set_speed(50);  // 5.0 mph — belt is running

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);

    int kv_count = 0;
    cycle.on_kv_event([&](std::string_view, std::string_view) {
        kv_count++;
    });

    cycle.reset(0);
    run_bursts(cycle, 2);
    CHECK(kv_count > 0);  // cycle is actively sending

    // Simulate watchdog trigger: controller lost, reset to proxy
    mode.watchdog_reset_to_proxy();

    // Cycle must not send after the reset
    int before = kv_count;
    CHECK(cycle.tick(300'000) == false);
    CHECK(kv_count == before);

    // Speed and incline are zeroed
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);
    CHECK(mode.is_proxy() == true);
}

TEST_CASE("emulation cycle 3-hour safety timeout zeros motion") {
    FakePort port;

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);
    mode.set_speed(50);
    mode.set_incline(10);

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);

    cycle.reset(0);
    // First tick observes the change and re-arms the timer at t=0.
    cycle.tick(0);
    CHECK(mode.speed_tenths() == 50);

    // Just before 3 hours of no changes: motion persists.
    cycle.tick(EMU_TIMEOUT_US - 1);
    CHECK(mode.speed_tenths() == 50);
    CHECK(mode.incline() == 10);

    // At/after 3 hours: safety_timeout_reset zeros speed and incline.
    cycle.tick(EMU_TIMEOUT_US);
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);
    // Still emulating (timeout zeroes motion, does not exit emulate).
    CHECK(mode.is_emulating() == true);
}
