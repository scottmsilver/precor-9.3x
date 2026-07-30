/*
 * test_safety_boot_envelope.cpp — integration-style safety envelope tests
 * through fake_hal: output edge ORDER on emulate entry (tx_enable before
 * relay_cmd; first TX bytes only after feedback qualification), zero-frame
 * content on entry via the forked codecs, boot output state, and the
 * 3-hour timeout through EmulationCycle with a fake clock.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <string>
#include <vector>

#include "fakes/fake_hal.h"
#include "safety/safety_controller.h"
#include "safety/feedback_window.h"
#include "engine/emulate_task_policy.h"
#include "engine/serial_io.h"
#include "engine/emulation_cycle.h"
#include "protocol/kv_protocol.h"

using namespace esp32tap::safety;
using esp32tap::fake::FakeClock;
using esp32tap::fake::FakePort;
using esp32tap::fake::FakeSafetyIo;

namespace {

constexpr int64_t MS = 1000;

std::span<const uint8_t> bytes(std::string_view s) {
    // reinterpret_cast: char -> uint8_t aliasing (standard-allowed)
    return std::span<const uint8_t>(
        reinterpret_cast<const uint8_t*>(s.data()), s.size());
}

// Minimal bridge mirroring the firmware tasks: sample HAL inputs into the
// controller, then apply controller outputs back to the HAL. This is the
// same order app_main's serial engine uses.
struct Bridge {
    SafetyController controller;
    FakeClock clock;
    FakeSafetyIo io;
    FakePort port;

    Bridge() { io.clock = &clock; port.out.clock = &clock; }

    void sample_feedback() {
        controller.observe_relay_feedback(io.k1_nc_high(), io.k1_no_high(),
                                          clock.now_us());
        apply_outputs();
    }
    void apply_outputs() {
        io.set_tx_enable(controller.tx_enable());
        io.set_relay_cmd(controller.relay_cmd());
    }
    // Emit the entry zero frame the way the emulate task does after the
    // controller reports EMULATING.
    void send_zero_frame() {
        SerialWriter<FakePort> writer(port);
        writer.write_kv("inc", encode_incline_hex(0));
        writer.write_kv("hmph", encode_speed_hex(0));
    }
};

}  // namespace

TEST_CASE("boot: outputs low, proxy, unknown feedback, no TX") {
    Bridge b;
    b.apply_outputs();
    CHECK_FALSE(b.io.relay_cmd);
    CHECK_FALSE(b.io.tx_en);
    CHECK(b.controller.mode() == SafeMode::PROXY);
    CHECK(b.controller.feedback() == Feedback::UNKNOWN);
    CHECK(b.port.out.writes.empty());
}

TEST_CASE("entry edge order: tx_enable on before relay_cmd on, TX only after qualification") {
    Bridge b;
    auto owner = ConnectionIdentity{Transport::WSS, 1, 1};

    b.io.set_feedback_bypass();
    b.sample_feedback();
    REQUIRE(b.controller.connect(owner));
    REQUIRE(b.controller.acquire(owner, b.clock.now_us()));
    b.controller.observe_console_bytes(bytes("[hmph:0000]"), b.clock.now_us());

    REQUIRE(b.controller.request_emulate(owner, b.clock.now_us(),
                                         b.port.tx_idle_low()));
    b.apply_outputs();
    CHECK(b.io.tx_en);
    CHECK_FALSE(b.io.relay_cmd);  // relay must NOT move before the gap
    CHECK(b.port.out.writes.empty());  // no byte sent while waiting

    // Gap observed at t=100ms
    b.clock.advance_ms(100);
    REQUIRE(b.controller.observe_interframe_gap(b.clock.now_us()));
    b.apply_outputs();
    CHECK(b.io.relay_cmd);
    CHECK(b.port.out.writes.empty());  // still no TX before qualification

    // Relay feedback settles to EMULATE; two samples 1 ms apart qualify
    b.io.set_feedback_emulate();
    b.clock.advance_ms(5);
    b.sample_feedback();
    b.clock.advance_ms(1);
    b.sample_feedback();
    REQUIRE(b.controller.mode() == SafeMode::EMULATING);

    // Only now does the emulate task send the first complete zero frame
    b.send_zero_frame();

    // Assert the ordered edge log: tx_enable:1 strictly before relay_cmd:1,
    // and the first TX timestamp is at/after the relay edge.
    std::vector<std::string> order;
    for (const auto& e : b.io.edges) order.push_back(e.what);
    REQUIRE(order.size() >= 2);
    CHECK(order.at(0) == "tx_enable:1");
    CHECK(order.at(1) == "relay_cmd:1");
    REQUIRE_FALSE(b.port.out.writes.empty());
    CHECK(b.port.out.writes.front().at_us >= b.io.edges.at(1).at_us);
}

TEST_CASE("entry zero frame content uses the forked codecs") {
    Bridge b;
    b.send_zero_frame();
    std::string written = b.port.out.all_written();
    // "[inc:0]\xff[hmph:0]\xff"
    CHECK(written == std::string("[inc:0]\xff[hmph:0]\xff"));
    CHECK(encode_speed_hex(0) == "0");
    CHECK(encode_incline_hex(0) == "0");
}

TEST_CASE("watchdog stall path releases outputs through the HAL") {
    Bridge b;
    auto owner = ConnectionIdentity{Transport::WSS, 1, 1};
    b.io.set_feedback_bypass();
    b.sample_feedback();
    REQUIRE(b.controller.connect(owner));
    REQUIRE(b.controller.acquire(owner, b.clock.now_us()));
    b.controller.observe_console_bytes(bytes("[hmph:0000]"), b.clock.now_us());
    REQUIRE(b.controller.request_emulate(owner, b.clock.now_us(), true));
    b.apply_outputs();
    b.clock.advance_ms(100);
    REQUIRE(b.controller.observe_interframe_gap(b.clock.now_us()));
    b.apply_outputs();
    b.io.set_feedback_emulate();
    b.clock.advance_ms(5);
    b.sample_feedback();
    b.clock.advance_ms(1);
    b.sample_feedback();
    REQUIRE(b.controller.mode() == SafeMode::EMULATING);
    REQUIRE(b.io.relay_cmd);

    // Supervisor detects a stall: the model's watchdog action.
    // (On hardware the panic reset itself releases GPIO21 via pull-down;
    // this asserts the controller-side contract for the same event.)
    b.controller.watchdog_stall(b.clock.now_us());
    b.apply_outputs();
    CHECK_FALSE(b.io.relay_cmd);
    CHECK_FALSE(b.io.tx_en);
    CHECK(b.controller.mode() == SafeMode::PROXY);
    CHECK(b.controller.feedback() == Feedback::UNKNOWN);
}

TEST_CASE("tread_ok loss releases outputs immediately") {
    Bridge b;
    auto owner = ConnectionIdentity{Transport::EXECUTOR, 3, 1};
    b.io.set_feedback_bypass();
    b.sample_feedback();
    REQUIRE(b.controller.connect(owner));
    REQUIRE(b.controller.acquire(owner, b.clock.now_us()));
    b.controller.observe_console_bytes(bytes("[hmph:0000]"), b.clock.now_us());
    REQUIRE(b.controller.request_emulate(owner, b.clock.now_us(), true));
    b.apply_outputs();
    CHECK(b.io.tx_en);

    b.io.tread_ok_level = false;
    b.controller.set_tread_ok(b.io.tread_ok(), b.clock.now_us());
    b.apply_outputs();
    CHECK_FALSE(b.io.relay_cmd);
    CHECK_FALSE(b.io.tx_en);
    CHECK(b.controller.mode() == SafeMode::PROXY);
}

namespace {

// Replicates emulate_cycle_task's per-iteration body against the
// authoritative SafetyController, routing arm / force-proxy / mirror /
// send decisions through the SAME EmulateTaskPolicy the firmware task
// uses (engine/emulate_task_policy.h — first-burst-zero gate included).
// `mirror_first` selects the real task ordering (mirror under the lock,
// tick after); false simulates a future reorder (tick before mirror) —
// the safety outcome must not depend on it.
struct EmuTaskHarness {
    Bridge b;
    ModeStateMachine mode;
    SerialWriter<FakePort> writer{b.port};
    EmulationCycle<FakePort> cycle{writer, mode};
    esp32tap::EmulateTaskPolicy policy;

    EmuTaskHarness() { mode.set_emulate_callback([](bool) {}); }

    void mirror() {
        mode.set_speed(b.controller.speed_tenths());
        mode.set_incline(b.controller.incline_half_percent());
    }
    void tick_and_consume() {
        bool sent = cycle.tick(b.clock.now_us());
        if (sent) policy.on_burst_sent();
        if (cycle.consume_safety_timeout()) {
            b.controller.safety_timeout_zero_motion(b.clock.now_us());
        }
    }
    void iterate(bool mirror_first = true) {
        bool emulating = b.controller.mode() == SafeMode::EMULATING;
        auto d = policy.step(emulating, mode.is_emulating());
        if (d.arm) {
            mode.request_emulate(true);
            cycle.reset(b.clock.now_us());
        } else if (d.force_proxy) {
            mode.watchdog_reset_to_proxy();
        }
        if (!d.send_burst) return;
        if (mirror_first) {
            if (d.mirror) mirror();
            tick_and_consume();
        } else {
            tick_and_consume();
            if (d.mirror) mirror();
        }
    }

    // Drive the controller into EMULATING with owner motion 50/10.
    void enter_emulating_at_speed() {
        auto owner = ConnectionIdentity{Transport::EXECUTOR, 3, 1};
        b.io.set_feedback_bypass();
        b.sample_feedback();
        REQUIRE(b.controller.connect(owner));
        REQUIRE(b.controller.acquire(owner, b.clock.now_us()));
        b.controller.observe_console_bytes(bytes("[hmph:0000]"),
                                           b.clock.now_us());
        REQUIRE(b.controller.request_emulate(owner, b.clock.now_us(), true));
        b.apply_outputs();
        b.clock.advance_ms(100);
        REQUIRE(b.controller.observe_interframe_gap(b.clock.now_us()));
        b.apply_outputs();
        b.io.set_feedback_emulate();
        b.clock.advance_ms(5);
        b.sample_feedback();
        b.clock.advance_ms(1);
        b.sample_feedback();
        REQUIRE(b.controller.mode() == SafeMode::EMULATING);
        REQUIRE(b.controller.command_motion(owner, 50, 10, b.clock.now_us()));
    }

    bool controller_has_event(std::string_view text) const {
        for (uint64_t i = 0; i < b.controller.event_count(); i++) {
            if (b.controller.event_at(i) == text) return true;
        }
        return false;
    }
};

}  // namespace

TEST_CASE("3-hour timeout zeroes the authoritative controller too (task iteration order)") {
    EmuTaskHarness h;
    h.enter_emulating_at_speed();

    // Normal iterations: after the entry zero burst, motion is mirrored
    // and encoded nonzero when the motion burst (burst 0) comes around
    // again (10 iterations = two full 5-burst cycles).
    for (int i = 0; i < 10; i++) {
        h.iterate();
        h.b.clock.advance_ms(100);
    }
    CHECK(h.b.controller.speed_tenths() == 50);
    CHECK(h.mode.speed_tenths() == 50);
    // 50 tenths = 5.0 mph -> hmph = 500 = 0x1F4
    CHECK(h.b.port.out.all_written().find("[hmph:1F4]") != std::string::npos);

    // 3 hours of no changes.
    h.b.clock.advance_us(EMULATE_TIMEOUT_US);
    h.b.port.out.writes.clear();
    h.iterate();

    // Same iteration: mode zeroed by the cycle engine AND the
    // authoritative controller zeroed via consume_safety_timeout() —
    // no split-brain window into the next mirror.
    CHECK(h.mode.speed_tenths() == 0);
    CHECK(h.mode.incline() == 0);
    CHECK(h.b.controller.speed_tenths() == 0);
    CHECK(h.b.controller.incline_half_percent() == 0);
    CHECK(h.controller_has_event("safety_timeout_zero_motion"));
    // Timeout does not change mode/lease/relay/TX: still emulating at zero
    // (Pi parity: cpp/ stays in emulate with zeroed motion).
    auto owner = ConnectionIdentity{Transport::EXECUTOR, 3, 1};
    REQUIRE(h.b.controller.owner().has_value());
    CHECK(*h.b.controller.owner() == owner);
    CHECK(h.b.controller.mode() == SafeMode::EMULATING);
    CHECK(h.b.controller.relay_cmd());
    CHECK(h.b.controller.tx_enable());
    CHECK(h.b.io.relay_cmd);
    CHECK(h.b.io.tx_en);

    // The wire only ever carries zero motion from now on.
    for (int i = 0; i < 5; i++) {
        h.b.clock.advance_ms(100);
        h.iterate();
    }
    std::string wire = h.b.port.out.all_written();
    CHECK(wire.find("[inc:0]\xff") != std::string::npos);
    CHECK(wire.find("[hmph:0]\xff") != std::string::npos);
    CHECK(wire.find("[hmph:1F4]") == std::string::npos);
}

TEST_CASE("3-hour timeout: a reordered mirror cannot resurrect stale motion") {
    EmuTaskHarness h;
    h.enter_emulating_at_speed();
    for (int i = 0; i < 5; i++) {
        h.iterate(/*mirror_first=*/false);
        h.b.clock.advance_ms(100);
    }
    CHECK(h.mode.speed_tenths() == 50);

    h.b.clock.advance_us(EMULATE_TIMEOUT_US);
    h.iterate(/*mirror_first=*/false);
    // tick fired the timeout and the controller was zeroed before the
    // (reordered) mirror ran, so the mirror writes zeros — not 50.
    CHECK(h.b.controller.speed_tenths() == 0);
    CHECK(h.mode.speed_tenths() == 0);

    h.b.port.out.writes.clear();
    for (int i = 0; i < 5; i++) {
        h.b.clock.advance_ms(100);
        h.iterate(/*mirror_first=*/false);
    }
    std::string wire = h.b.port.out.all_written();
    CHECK(wire.find("[hmph:0]\xff") != std::string::npos);
    CHECK(wire.find("[hmph:1F4]") == std::string::npos);
}

TEST_CASE("PLAN entry step 6: first transmitted burst is zero despite motion commanded during the entry window") {
    // The controller accepts owner command_motion during ENTRY_WAIT_*
    // (faithful to safety_model.py), so it can reach EMULATING already
    // holding nonzero motion. The task layer must still transmit the
    // first post-entry burst as the zero frame.
    EmuTaskHarness h;
    auto owner = ConnectionIdentity{Transport::WSS, 7, 1};
    h.b.io.set_feedback_bypass();
    h.b.sample_feedback();
    REQUIRE(h.b.controller.connect(owner));
    REQUIRE(h.b.controller.acquire(owner, h.b.clock.now_us()));
    h.b.controller.observe_console_bytes(bytes("[hmph:0000]"),
                                         h.b.clock.now_us());
    REQUIRE(h.b.controller.request_emulate(owner, h.b.clock.now_us(), true));
    REQUIRE(h.b.controller.mode() == SafeMode::ENTRY_WAIT_GAP);
    // Owner commands motion while the entry is still in flight.
    REQUIRE(h.b.controller.command_motion(owner, 50, 10, h.b.clock.now_us()));
    h.b.apply_outputs();
    h.b.clock.advance_ms(100);
    REQUIRE(h.b.controller.observe_interframe_gap(h.b.clock.now_us()));
    h.b.apply_outputs();
    h.b.io.set_feedback_emulate();
    h.b.clock.advance_ms(5);
    h.b.sample_feedback();
    h.b.clock.advance_ms(1);
    h.b.sample_feedback();
    REQUIRE(h.b.controller.mode() == SafeMode::EMULATING);
    // Nonzero owner motion is already present at EMULATING onset.
    REQUIRE(h.b.controller.speed_tenths() == 50);
    REQUIRE(h.b.controller.incline_half_percent() == 10);

    // First task iteration after entry: arm + transmit the first burst.
    h.iterate();
    REQUIRE(h.b.port.out.writes.size() >= 2);
    auto write_str = [&](size_t i) {
        const auto& w = h.b.port.out.writes.at(i);
        return std::string(w.bytes.begin(), w.bytes.end());
    };
    // Burst 0 = inc, hmph — the first complete frame MUST encode zero.
    CHECK(write_str(0) == std::string("[inc:0]\xff"));
    CHECK(write_str(1) == std::string("[hmph:0]\xff"));

    // After the zero burst went out, the owner motion is mirrored and
    // reaches the wire when the motion burst comes around again.
    for (int i = 0; i < 6; i++) {
        h.b.clock.advance_ms(100);
        h.iterate();
    }
    CHECK(h.mode.speed_tenths() == 50);
    CHECK(h.mode.incline() == 10);
    std::string wire = h.b.port.out.all_written();
    // 50 tenths = 5.0 mph -> hmph = 500 = 0x1F4; incline 10 half-pct = 0xA
    CHECK(wire.find("[hmph:1F4]") != std::string::npos);
    CHECK(wire.find("[inc:A]") != std::string::npos);
    // The very first frames on the wire were the zero frames.
    CHECK(wire.rfind("[inc:0]\xff[hmph:0]\xff", 0) == 0);
}

namespace {

// Drives the controller exactly like main/serial_engine_task.cpp: 5 ms
// coarse iterations (top-of-loop tread_ok + feedback samples, console
// gap qualification after GAP_QUALIFY_US of RX silence, tick, apply),
// then the dedicated sub-ms feedback window while a relay transfer is
// in flight. The fake relay follows RELAY_CMD instantly: feedback GPIO
// reads derive from the commanded coil state, so qualification timing
// is purely the software cadence under test.
struct SerialCadenceSim {
    Bridge& b;
    int64_t last_console_rx_us = 0;

    bool nc_high() const { return b.io.relay_cmd; }   // energized -> NC open
    bool no_high() const { return !b.io.relay_cmd; }  // energized -> NO closed

    void iteration() {
        int64_t now = b.clock.now_us();
        b.controller.set_tread_ok(b.io.tread_ok(), now);
        b.controller.observe_relay_feedback(nc_high(), no_high(), now);
        auto m = b.controller.mode();
        if ((m == SafeMode::ENTRY_WAIT_GAP || m == SafeMode::EXIT_WAIT_GAP) &&
            now - last_console_rx_us >= GAP_QUALIFY_US) {
            b.controller.observe_interframe_gap(now);
        }
        b.controller.tick(now);
        b.apply_outputs();
        if (in_feedback_wait(b.controller)) {
            run_feedback_window(
                b.controller, [this] { return b.clock.now_us(); },
                [this] { return nc_high(); }, [this] { return no_high(); },
                [this] { b.apply_outputs(); },
                [this] { b.clock.advance_us(FEEDBACK_POLL_US); });
        }
        b.clock.advance_ms(5);
    }
};

// Entry driven purely by the real task cadence (no hand-advanced
// qualification samples). Returns after the controller reaches
// EMULATING or the iteration budget is spent.
void drive_entry_at_task_cadence(Bridge& b, SerialCadenceSim& sim,
                                 const ConnectionIdentity& owner) {
    b.controller.observe_console_bytes(bytes("[hmph:0000]"),
                                       b.clock.now_us());
    sim.last_console_rx_us = b.clock.now_us();
    sim.iteration();  // establishes the real BYPASS feedback sample
    REQUIRE(b.controller.connect(owner));
    REQUIRE(b.controller.acquire(owner, b.clock.now_us()));
    REQUIRE(b.controller.request_emulate(owner, b.clock.now_us(),
                                         b.port.tx_idle_low()));
    for (int i = 0;
         i < 20 && b.controller.mode() != SafeMode::EMULATING; i++) {
        sim.iteration();
    }
}

}  // namespace

TEST_CASE("gap-safe ENTRY completes at the real task cadence (5 ms loop + sub-ms feedback window)") {
    // Regression for the unsatisfiable-10ms-qualification bug: at a pure
    // 5 ms sampling cadence the first feedback sample lands ~+5 ms after
    // relay_cmd and the next at ~+10 ms — exactly the fail-closed
    // deadline — so every entry latched entry_feedback_timeout. The
    // dedicated feedback window must complete the transfer instead.
    Bridge b;
    SerialCadenceSim sim{b};
    auto owner = ConnectionIdentity{Transport::WSS, 1, 1};
    drive_entry_at_task_cadence(b, sim, owner);

    CHECK(b.controller.mode() == SafeMode::EMULATING);
    CHECK_FALSE(b.controller.fault_latched());
    CHECK(b.io.relay_cmd);
    CHECK(b.io.tx_en);
    CHECK(b.controller.owner().has_value());
}

TEST_CASE("gap-safe EXIT completes at the real task cadence (5 ms loop + sub-ms feedback window)") {
    Bridge b;
    SerialCadenceSim sim{b};
    auto owner = ConnectionIdentity{Transport::WSS, 1, 1};
    drive_entry_at_task_cadence(b, sim, owner);
    REQUIRE(b.controller.mode() == SafeMode::EMULATING);
    REQUIRE_FALSE(b.controller.fault_latched());

    REQUIRE(b.controller.request_normal_exit(owner, b.clock.now_us()));
    for (int i = 0; i < 20 && b.controller.mode() != SafeMode::PROXY; i++) {
        sim.iteration();
    }

    CHECK(b.controller.mode() == SafeMode::PROXY);
    CHECK_FALSE(b.controller.fault_latched());
    CHECK_FALSE(b.io.relay_cmd);
    CHECK_FALSE(b.io.tx_en);
    // Normal exit releases ownership (PLAN exit step 5).
    CHECK_FALSE(b.controller.owner().has_value());
}

TEST_CASE("3-hour timeout zeros motion via the emulate cycle") {
    FakeClock clock;
    FakePort port;
    ModeStateMachine mode;
    mode.set_emulate_callback([](bool) {});
    mode.request_emulate(true);
    mode.set_speed(50);
    mode.set_incline(10);

    SerialWriter<FakePort> writer(port);
    EmulationCycle<FakePort> cycle(writer, mode);
    cycle.reset(clock.now_us());
    cycle.tick(clock.now_us());  // observes the speed change at t=0
    CHECK(mode.speed_tenths() == 50);

    clock.advance_us(EMULATE_TIMEOUT_US - 1);
    cycle.tick(clock.now_us());
    CHECK(mode.speed_tenths() == 50);

    clock.advance_us(1);  // exactly 3 hours of no changes
    cycle.tick(clock.now_us());
    CHECK(mode.speed_tenths() == 0);
    CHECK(mode.incline() == 0);
}
