/*
 * test_integration.cpp — Full controller integration tests with MockGpioPort
 *
 * Tests the complete command -> serial output path without hardware.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "gpio_mock.h"
#include "treadmill_io.h"
#include <thread>
#include <chrono>
#include <cstring>

TEST_CASE("controller starts and stops cleanly") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg;
    cfg.console_read = 27;
    cfg.motor_write = 22;
    cfg.motor_read = 17;

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    CHECK(ctrl.start());

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    CHECK(ctrl.is_running());

    ctrl.stop();
    CHECK_FALSE(ctrl.is_running());
}

TEST_CASE("controller initial state is proxy") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg{27, 22, 17};
    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();

    auto snap = ctrl.mode().snapshot();
    CHECK(snap.proxy_enabled == true);
    CHECK(snap.emulate_enabled == false);

    ctrl.stop();
}

TEST_CASE("speed command enables emulate and sets speed") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg{27, 22, 17};
    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();

    // Simulate what would happen via IPC command
    ctrl.mode().set_emulate_callback([](bool) {});  // override for test
    ctrl.mode().set_speed_mph(3.5);

    auto snap = ctrl.mode().snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.speed_tenths == 35);

    ctrl.stop();
}

TEST_CASE("console reader parses injected KV data") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg{27, 22, 17};
    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();

    // Inject KV data as if console sent it
    const char* kv_data = "[hmph:78]\xff[inc:5]\xff";
    port.inject_serial_data(kv_data);

    // Wait for reader to process
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Check that events were pushed to ring
    auto snap = ctrl.ring().snapshot();
    // Should have at least the initial status + 2 KV events
    CHECK(snap.count >= 3);

    ctrl.stop();
}

TEST_CASE("motor reader parses injected KV data") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg{27, 22, 17};
    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();

    // Inject data on pin 17 (motor read)
    // Note: MockGpioPort serial_read returns data regardless of pin
    port.inject_serial_data("[belt:0]");

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    auto snap = ctrl.ring().snapshot();
    CHECK(snap.count >= 2);  // initial status + kv event

    ctrl.stop();
}

TEST_CASE("mode transitions preserve safety invariants") {
    MockGpioPort port;
    port.initialise();

    GpioConfig cfg{27, 22, 17};
    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();

    auto& mode = ctrl.mode();
    mode.set_emulate_callback([](bool) {});

    // Set speed to 50 (5.0 mph)
    mode.set_speed(50);
    CHECK(mode.speed_tenths() == 50);

    // Switch to proxy — should stop emulate
    mode.request_proxy(true);
    auto snap = mode.snapshot();
    CHECK(snap.proxy_enabled == true);
    CHECK(snap.emulate_enabled == false);

    // Re-enable emulate — speed should be zeroed (safety invariant)
    mode.request_emulate(true);
    snap = mode.snapshot();
    CHECK(snap.speed_tenths == 0);
    CHECK(snap.incline == 0);

    ctrl.stop();
}
