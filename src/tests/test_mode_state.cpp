/*
 * test_mode_state.cpp — Tests for ModeStateMachine
 *
 * Most important test file: verifies all mode transitions, mutual
 * exclusion, auto-proxy, auto-emulate, safety timeout, and clamping.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "mode_state.h"
#include <cstring>

// ── Initial state ───────────────────────────────────────────────────

TEST_CASE("initial state is proxy mode") {
    ModeStateMachine mode;
    auto snap = mode.snapshot();
    CHECK(snap.proxy_enabled == true);
    CHECK(snap.emulate_enabled == false);
    CHECK(snap.speed_tenths == 0);
    CHECK(snap.incline == 0);
    CHECK(snap.mode == Mode::Proxy);
}

// ── Proxy transitions ───────────────────────────────────────────────

TEST_CASE("request proxy on (already on)") {
    ModeStateMachine mode;
    auto result = mode.request_proxy(true);
    CHECK(result.changed == true);
    CHECK(mode.is_proxy() == true);
}

TEST_CASE("request proxy off") {
    ModeStateMachine mode;
    auto result = mode.request_proxy(false);
    CHECK(result.changed == true);
    auto snap = mode.snapshot();
    CHECK(snap.proxy_enabled == false);
    CHECK(snap.mode == Mode::Idle);
}

// ── Emulate transitions ─────────────────────────────────────────────

TEST_CASE("enable emulate stops proxy") {
    ModeStateMachine mode;
    bool emulate_started = false;
    mode.set_emulate_callback([&](bool start) { emulate_started = start; });

    auto result = mode.request_emulate(true);
    CHECK(result.changed == true);
    CHECK(result.emulate_started == true);
    CHECK(emulate_started == true);

    auto snap = mode.snapshot();
    CHECK(snap.proxy_enabled == false);
    CHECK(snap.emulate_enabled == true);
    // Safety: speed/incline zeroed on emulate start
    CHECK(snap.speed_tenths == 0);
    CHECK(snap.incline == 0);
}

TEST_CASE("disable emulate") {
    ModeStateMachine mode;
    bool emulate_stopped = false;
    mode.set_emulate_callback([&](bool start) {
        if (!start) emulate_stopped = true;
    });

    mode.request_emulate(true);
    auto result = mode.request_emulate(false);
    CHECK(result.emulate_stopped == true);
    CHECK(emulate_stopped == true);

    auto snap = mode.snapshot();
    CHECK(snap.emulate_enabled == false);
}

TEST_CASE("enable emulate while already emulating is no-op") {
    ModeStateMachine mode;
    int callback_count = 0;
    mode.set_emulate_callback([&](bool) { callback_count++; });

    mode.request_emulate(true);
    CHECK(callback_count == 1);

    auto result = mode.request_emulate(true);
    CHECK(result.changed == false);
    CHECK(callback_count == 1);  // no additional callback
}

// ── Mutual exclusion ────────────────────────────────────────────────

TEST_CASE("proxy and emulate are mutually exclusive") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.request_emulate(true);
    auto snap1 = mode.snapshot();
    CHECK(snap1.proxy_enabled == false);
    CHECK(snap1.emulate_enabled == true);

    mode.request_proxy(true);
    auto snap2 = mode.snapshot();
    CHECK(snap2.proxy_enabled == true);
    CHECK(snap2.emulate_enabled == false);
}

// ── Speed/incline auto-emulate ──────────────────────────────────────

TEST_CASE("set_speed auto-enables emulate") {
    ModeStateMachine mode;
    bool emulate_started = false;
    mode.set_emulate_callback([&](bool start) { emulate_started = start; });

    auto result = mode.set_speed(50);
    CHECK(result.emulate_started == true);
    CHECK(emulate_started == true);

    auto snap = mode.snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.proxy_enabled == false);
    // Note: set_speed auto-enables emulate which zeros, then sets speed
    // But the implementation sets speed AFTER enter_emulate_locked
    CHECK(snap.speed_tenths == 50);
}

TEST_CASE("set_speed_mph auto-enables emulate") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_speed_mph(1.2);
    auto snap = mode.snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.speed_tenths == 12);
    CHECK(snap.speed_raw == 120);
}

TEST_CASE("set_incline auto-enables emulate") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_incline(10);  // 10 half-pct = 5%
    auto snap = mode.snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.incline == 10);
}

// ── Clamping ────────────────────────────────────────────────────────

TEST_CASE("speed clamped to MAX_SPEED_TENTHS") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_speed(200);
    CHECK(mode.speed_tenths() == MAX_SPEED_TENTHS);
}

TEST_CASE("speed clamped to 0") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_speed(-10);
    CHECK(mode.speed_tenths() == 0);
}

TEST_CASE("incline clamped to MAX_INCLINE (198 half-pct)") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_incline(300);
    CHECK(mode.incline() == MAX_INCLINE);  // 198 half-pct
}

TEST_CASE("incline clamped to 0") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_incline(-5);
    CHECK(mode.incline() == 0);
}

// ── Auto-proxy on console change ────────────────────────────────────

TEST_CASE("auto_proxy triggers on hmph change while emulating") {
    ModeStateMachine mode;
    bool emulate_stopped = false;
    mode.set_emulate_callback([&](bool start) {
        if (!start) emulate_stopped = true;
    });

    mode.request_emulate(true);
    emulate_stopped = false;

    auto result = mode.auto_proxy_on_console_change("hmph", "78", "96");
    CHECK(result.changed == true);
    CHECK(result.emulate_stopped == true);
    CHECK(emulate_stopped == true);

    auto snap = mode.snapshot();
    CHECK(snap.proxy_enabled == true);
    CHECK(snap.emulate_enabled == false);
}

TEST_CASE("auto_proxy triggers on inc change while emulating") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.request_emulate(true);
    auto result = mode.auto_proxy_on_console_change("inc", "5", "7");
    CHECK(result.changed == true);
    CHECK(mode.is_proxy() == true);
}

TEST_CASE("auto_proxy does nothing if not emulating") {
    ModeStateMachine mode;
    auto result = mode.auto_proxy_on_console_change("hmph", "78", "96");
    CHECK(result.changed == false);
}

TEST_CASE("auto_proxy does nothing if same value") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    auto result = mode.auto_proxy_on_console_change("hmph", "78", "78");
    CHECK(result.changed == false);
}

TEST_CASE("auto_proxy does nothing if first value (empty old)") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    auto result = mode.auto_proxy_on_console_change("hmph", "", "78");
    CHECK(result.changed == false);
}

TEST_CASE("auto_proxy ignores non-hmph/inc keys") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);

    auto result = mode.auto_proxy_on_console_change("belt", "0", "1");
    CHECK(result.changed == false);
}

// ── Safety timeout ──────────────────────────────────────────────────

TEST_CASE("safety_timeout_reset zeros speed and incline") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_speed(50);
    mode.set_incline(10);  // 10 half-pct = 5%

    CHECK(mode.speed_tenths() == 50);
    CHECK(mode.incline() == 10);

    mode.safety_timeout_reset();
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);
}

// ── Watchdog reset to proxy ──────────────────────────────────────────

TEST_CASE("watchdog_reset_to_proxy zeros speed/incline and returns to proxy") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    // Set up: emulating at speed 50 tenths, incline 14 half-pct (7%)
    mode.request_emulate(true);
    mode.set_speed(50);
    mode.set_incline(14);

    CHECK(mode.is_emulating() == true);
    CHECK(mode.speed_tenths() == 50);
    CHECK(mode.incline() == 14);

    // Watchdog fires
    mode.watchdog_reset_to_proxy();

    // Belt stopped: speed and incline zeroed
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);

    // Mode switched to proxy (console regains control)
    CHECK(mode.is_proxy() == true);
    CHECK(mode.is_emulating() == false);

    auto snap = mode.snapshot();
    CHECK(snap.mode == Mode::Proxy);
    CHECK(snap.speed_tenths == 0);
    CHECK(snap.speed_raw == 0);
    CHECK(snap.incline == 0);
}

TEST_CASE("watchdog_reset_to_proxy does NOT fire emulate callback") {
    ModeStateMachine mode;
    int callback_count = 0;
    mode.set_emulate_callback([&](bool) { callback_count++; });

    mode.request_emulate(true);
    CHECK(callback_count == 1);  // start callback

    // Watchdog fires — must NOT fire stop callback
    // (avoids double thread::join between IPC thread and main thread)
    mode.watchdog_reset_to_proxy();
    CHECK(callback_count == 1);  // still 1 — no stop callback fired

    CHECK(mode.is_proxy() == true);
    CHECK(mode.is_emulating() == false);
}

TEST_CASE("watchdog_reset_to_proxy is safe when already in proxy") {
    ModeStateMachine mode;
    int callback_count = 0;
    mode.set_emulate_callback([&](bool) { callback_count++; });

    // Already in proxy mode (default), watchdog fires anyway
    CHECK(mode.is_proxy() == true);
    mode.watchdog_reset_to_proxy();

    // No crash, no callback, still in proxy
    CHECK(callback_count == 0);
    CHECK(mode.is_proxy() == true);
    CHECK(mode.speed_tenths() == 0);
}

TEST_CASE("watchdog_reset_to_proxy is safe when in idle mode") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    // Put in idle mode
    mode.request_proxy(false);
    CHECK(mode.is_proxy() == false);
    CHECK(mode.is_emulating() == false);

    mode.watchdog_reset_to_proxy();

    // Switched to proxy (safe default), no crash
    CHECK(mode.is_proxy() == true);
}

TEST_CASE("emulate can be re-enabled after watchdog reset") {
    ModeStateMachine mode;
    bool emulate_started = false;
    mode.set_emulate_callback([&](bool start) {
        if (start) emulate_started = true;
    });

    // Emulate → watchdog reset → emulate again
    mode.request_emulate(true);
    CHECK(emulate_started == true);
    emulate_started = false;

    mode.watchdog_reset_to_proxy();
    CHECK(mode.is_proxy() == true);

    // Re-enable emulate — should work normally
    mode.request_emulate(true);
    CHECK(emulate_started == true);
    CHECK(mode.is_emulating() == true);
}

// ── Byte counters ───────────────────────────────────────────────────

TEST_CASE("byte counters") {
    ModeStateMachine mode;
    CHECK(mode.console_bytes() == 0);
    CHECK(mode.motor_bytes() == 0);

    mode.add_console_bytes(100);
    mode.add_motor_bytes(50);
    CHECK(mode.console_bytes() == 100);
    CHECK(mode.motor_bytes() == 50);

    mode.add_console_bytes(200);
    CHECK(mode.console_bytes() == 300);
}
