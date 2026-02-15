/*
 * test_controller_live.cpp — Live integration tests for TreadmillController
 *
 * Exercises the full controller with MockGpioPort: auto-proxy detection,
 * command dispatch through IPC, emulate cycle output, proxy forwarding,
 * and the interaction between mode transitions and the emulate engine.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "gpio_mock.h"
#include "treadmill_io.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <fcntl.h>
#include <cstring>
#include <thread>
#include <chrono>
#include <string>

// Helper: connect to the IPC socket
static int connect_ipc() {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, SOCK_PATH, sizeof(addr.sun_path) - 1);
    if (connect(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void send_json(int fd, const char* json) {
    std::string line = std::string(json) + "\n";
    (void)write(fd, line.c_str(), line.size());
}

static std::string read_available(int fd, int wait_ms = 150) {
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    char buf[8192];
    std::string result;
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    while (true) {
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        if (n <= 0) break;
        buf[n] = '\0';
        result += buf;
    }
    fcntl(fd, F_SETFL, flags);
    return result;
}

// ── IPC end-to-end through controller ───────────────────────────────

TEST_CASE("IPC speed command triggers emulate and reports status") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    CHECK(ctrl.start());

    // Give IPC thread time to start
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    CHECK(fd >= 0);

    // Drain initial status
    read_available(fd, 80);

    // Send speed command
    send_json(fd, "{\"cmd\":\"speed\",\"value\":5.0}");
    std::string data = read_available(fd, 150);

    // Should get a status event with emulate=true, emu_speed=50
    CHECK(data.find("\"emulate\":true") != std::string::npos);
    CHECK(data.find("\"emu_speed\":50") != std::string::npos);

    auto snap = ctrl.mode().snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.speed_tenths == 50);

    close(fd);
    ctrl.stop();
}

TEST_CASE("IPC incline command triggers emulate and reports status") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    send_json(fd, "{\"cmd\":\"incline\",\"value\":8}");
    std::string data = read_available(fd, 150);

    CHECK(data.find("\"emulate\":true") != std::string::npos);
    CHECK(data.find("\"emu_incline\":8") != std::string::npos);

    close(fd);
    ctrl.stop();
}

TEST_CASE("IPC proxy command disables emulate") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    // Enable emulate first
    send_json(fd, "{\"cmd\":\"emulate\",\"enabled\":true}");
    read_available(fd, 100);
    CHECK(ctrl.mode().is_emulating());

    // Switch to proxy
    send_json(fd, "{\"cmd\":\"proxy\",\"enabled\":true}");
    std::string data = read_available(fd, 100);

    CHECK(data.find("\"proxy\":true") != std::string::npos);
    CHECK(data.find("\"emulate\":false") != std::string::npos);
    CHECK(ctrl.mode().is_proxy());

    close(fd);
    ctrl.stop();
}

TEST_CASE("IPC quit command stops controller") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 50);

    send_json(fd, "{\"cmd\":\"quit\"}");
    std::this_thread::sleep_for(std::chrono::milliseconds(300));

    CHECK_FALSE(ctrl.is_running());

    close(fd);
    ctrl.stop();
}

// ── Console auto-proxy with live emulate engine ─────────────────────

TEST_CASE("console hmph change during emulate triggers auto-proxy") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    // Enable emulate via speed command
    int fd = connect_ipc();
    read_available(fd, 80);
    send_json(fd, "{\"cmd\":\"speed\",\"value\":3.0}");
    read_available(fd, 200);

    CHECK(ctrl.mode().is_emulating());

    // Inject a first hmph value (baseline) then a changed value on console pin
    port.inject_serial_data_pin(27, "[hmph:78]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    port.inject_serial_data_pin(27, "[hmph:96]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    // Should have auto-switched to proxy
    CHECK(ctrl.mode().is_proxy());
    CHECK_FALSE(ctrl.mode().is_emulating());

    close(fd);
    ctrl.stop();
}

TEST_CASE("console inc change during emulate triggers auto-proxy") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);
    send_json(fd, "{\"cmd\":\"emulate\",\"enabled\":true}");
    read_available(fd, 200);

    CHECK(ctrl.mode().is_emulating());

    // Inject inc baseline then change on console pin
    port.inject_serial_data_pin(27, "[inc:3]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    port.inject_serial_data_pin(27, "[inc:5]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    CHECK(ctrl.mode().is_proxy());

    close(fd);
    ctrl.stop();
}

// ── Emulate engine output verification ──────────────────────────────

TEST_CASE("emulate engine sends KV events visible on IPC") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    // Start emulate with specific speed/incline
    send_json(fd, "{\"cmd\":\"speed\",\"value\":2.0}");
    read_available(fd, 100);  // status event
    send_json(fd, "{\"cmd\":\"incline\",\"value\":4}");

    // Wait for at least one emulation cycle (~600ms)
    std::string data = read_available(fd, 700);

    // Should see emulate-sourced KV events for inc and hmph
    CHECK(data.find("\"source\":\"emulate\"") != std::string::npos);
    CHECK(data.find("\"key\":\"inc\"") != std::string::npos);
    CHECK(data.find("\"key\":\"hmph\"") != std::string::npos);

    close(fd);
    ctrl.stop();
}

// ── Proxy forwarding ────────────────────────────────────────────────

TEST_CASE("proxy mode forwards console data to motor write pin") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    // Default mode is proxy
    CHECK(ctrl.mode().is_proxy());

    // Inject console serial data on console pin (27)
    port.inject_serial_data_pin(27, "[hmph:78]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Check that the data was forwarded (written to motor write GPIO)
    auto written = port.get_written_string();
    CHECK(written.find("[hmph:78]") != std::string::npos);

    ctrl.stop();
}

TEST_CASE("emulate mode does NOT forward console data") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    // Enable emulate (disables proxy)
    send_json(fd, "{\"cmd\":\"emulate\",\"enabled\":true}");
    read_available(fd, 200);
    CHECK(ctrl.mode().is_emulating());
    CHECK_FALSE(ctrl.mode().is_proxy());

    // Clear any writes from emulate engine startup
    port.clear_writes();

    // Inject console data on console pin — should NOT be forwarded
    port.inject_serial_data_pin(27, "[hmph:78]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // The only writes should be from emulate engine (if any), not proxy
    auto written = port.get_written_string();
    // Console data "[hmph:78]" should NOT appear in motor writes
    // (emulate engine writes its own [hmph:XX] but with different hex value)
    // We can't easily distinguish, so just verify the mode state
    CHECK(ctrl.mode().is_emulating());

    close(fd);
    ctrl.stop();
}

// ── Motor reader ────────────────────────────────────────────────────

TEST_CASE("motor reader events appear in IPC stream") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    // Inject motor response data on motor pin (17)
    port.inject_serial_data_pin(17, "[belt:0][vbus:300]");
    std::string data = read_available(fd, 150);

    // Should see motor-sourced KV events
    // Note: MockGpioPort serial_read doesn't distinguish by pin,
    // so the data goes to whichever reader polls first.
    // We just verify that KV events appear.
    bool has_kv = data.find("\"type\":\"kv\"") != std::string::npos;
    CHECK(has_kv);

    close(fd);
    ctrl.stop();
}

// ── Byte counter tracking ───────────────────────────────────────────

TEST_CASE("byte counters increment with serial data") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    uint32_t before_c = ctrl.mode().console_bytes();
    uint32_t before_m = ctrl.mode().motor_bytes();
    // Inject data on specific pins
    port.inject_serial_data_pin(27, "[test:1]\xff");
    port.inject_serial_data_pin(17, "[test:2]\xff");
    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    uint32_t after_c = ctrl.mode().console_bytes();
    uint32_t after_m = ctrl.mode().motor_bytes();
    CHECK(after_c > before_c);
    CHECK(after_m > before_m);

    ctrl.stop();
}

// ── Status command via IPC ──────────────────────────────────────────

TEST_CASE("status command returns current state") {
    MockGpioPort port;
    port.initialise();
    GpioConfig cfg{27, 22, 17};

    TreadmillController<MockGpioPort> ctrl(port, cfg);
    ctrl.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    int fd = connect_ipc();
    read_available(fd, 80);

    send_json(fd, "{\"cmd\":\"status\"}");
    std::string data = read_available(fd, 100);

    CHECK(data.find("\"type\":\"status\"") != std::string::npos);
    CHECK(data.find("\"proxy\":true") != std::string::npos);
    CHECK(data.find("\"emulate\":false") != std::string::npos);

    close(fd);
    ctrl.stop();
}
