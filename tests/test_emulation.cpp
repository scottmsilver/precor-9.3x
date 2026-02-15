/*
 * test_emulation.cpp — Tests for EmulationEngine with MockGpioPort
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "gpio_mock.h"
#include "serial_io.h"
#include "emulation_engine.h"
#include <thread>
#include <chrono>
#include <vector>
#include <string>

TEST_CASE("emulation engine sends 14-key cycle") {
    MockGpioPort port;
    port.initialise();

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});  // no-op, we manage engine directly
    mode.request_emulate(true);

    SerialWriter<MockGpioPort> writer(port, 22);
    EmulationEngine<MockGpioPort> engine(writer, mode);

    std::vector<std::string> keys_sent;
    engine.on_kv_event([&](const char* key, const char* /*value*/) {
        keys_sent.push_back(key);
    });

    engine.start();

    // Wait for at least one full cycle (5 bursts * 100ms = ~500ms)
    std::this_thread::sleep_for(std::chrono::milliseconds(700));

    engine.stop();

    // Should have sent at least 14 keys (one full cycle)
    CHECK(keys_sent.size() >= 14);

    // Verify the first 14 keys match the cycle order
    if (keys_sent.size() >= 14) {
        CHECK(keys_sent[0] == "inc");
        CHECK(keys_sent[1] == "hmph");
        CHECK(keys_sent[2] == "amps");
        CHECK(keys_sent[3] == "err");
        CHECK(keys_sent[4] == "belt");
        CHECK(keys_sent[5] == "vbus");
        CHECK(keys_sent[6] == "lift");
        CHECK(keys_sent[7] == "lfts");
        CHECK(keys_sent[8] == "lftg");
        CHECK(keys_sent[9] == "part");
        CHECK(keys_sent[10] == "ver");
        CHECK(keys_sent[11] == "type");
        CHECK(keys_sent[12] == "diag");
        CHECK(keys_sent[13] == "loop");
    }
}

TEST_CASE("emulation engine applies speed and incline") {
    MockGpioPort port;
    port.initialise();

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    // Set speed to 5.0 mph (50 tenths) and incline to 7
    // Do this after emulate is enabled (which zeros values)
    mode.set_speed(50);
    mode.set_incline(7);

    SerialWriter<MockGpioPort> writer(port, 22);
    EmulationEngine<MockGpioPort> engine(writer, mode);

    std::vector<std::pair<std::string, std::string>> kv_events;
    engine.on_kv_event([&](const char* key, const char* value) {
        kv_events.emplace_back(key, value);
    });

    engine.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(700));
    engine.stop();

    // Find inc and hmph events
    bool found_inc = false, found_hmph = false;
    for (auto& [k, v] : kv_events) {
        if (k == "inc" && v == "7") found_inc = true;
        if (k == "hmph") {
            // 50 tenths = 500 hundredths = 0x1F4
            if (v == "1F4") found_hmph = true;
        }
    }
    CHECK(found_inc);
    CHECK(found_hmph);
}

TEST_CASE("emulation engine stops when mode changes") {
    MockGpioPort port;
    port.initialise();

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    SerialWriter<MockGpioPort> writer(port, 22);
    EmulationEngine<MockGpioPort> engine(writer, mode);

    engine.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    // Switch to proxy mode (disables emulate)
    mode.request_proxy(true);

    // Engine should stop on its own since mode_.is_emulating() is false
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    CHECK_FALSE(engine.is_running());
    // Clean up
    engine.stop();
}

TEST_CASE("emulation engine start/stop lifecycle") {
    MockGpioPort port;
    port.initialise();

    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    SerialWriter<MockGpioPort> writer(port, 22);
    EmulationEngine<MockGpioPort> engine(writer, mode);

    // Start and stop multiple times
    engine.start();
    CHECK(engine.is_running());
    engine.stop();

    engine.start();
    CHECK(engine.is_running());
    engine.stop();

    // Destructor should also handle stop gracefully
}
