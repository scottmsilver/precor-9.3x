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

    mode.set_incline(5);
    auto snap = mode.snapshot();
    CHECK(snap.emulate_enabled == true);
    CHECK(snap.incline == 5);
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

TEST_CASE("incline clamped to MAX_INCLINE") {
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});

    mode.set_incline(200);
    CHECK(mode.incline() == MAX_INCLINE);
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
    mode.set_incline(5);

    CHECK(mode.speed_tenths() == 50);
    CHECK(mode.incline() == 5);

    mode.safety_timeout_reset();
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);
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
