/*
 * test_safety_controller.cpp — host vectors for the SafetyController port.
 *
 * Each case names its counterpart in
 * hardware/Esp32Tap/tests/test_firmware_safety_model.py; the numeric
 * vectors are the same, expressed in integer microseconds (PLAN D4).
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <string>
#include <vector>

#include "safety/safety_controller.h"

using namespace esp32tap::safety;

namespace {

constexpr int64_t MS = 1000;
constexpr int64_t S = 1'000'000;

ConnectionIdentity identity(Transport t = Transport::WSS, int32_t handle = 100,
                            int64_t gen = 1) {
    return ConnectionIdentity{t, handle, gen};
}

std::span<const uint8_t> bytes(std::string_view s) {
    // reinterpret_cast: char -> uint8_t aliasing (standard-allowed)
    return std::span<const uint8_t>(
        reinterpret_cast<const uint8_t*>(s.data()), s.size());
}

SafetyController connected_controller(const ConnectionIdentity& owner) {
    SafetyController c;
    c.observe_relay_feedback(false, true, 0);  // BYPASS sample
    REQUIRE(c.connect(owner));
    REQUIRE(c.acquire(owner, 0));
    return c;
}

void enter_emulate(SafetyController& c, const ConnectionIdentity& owner,
                   int64_t now = 0) {
    c.observe_console_bytes(bytes("[hmph:0000]"), now);
    REQUIRE(c.request_emulate(owner, now, true));
    REQUIRE(c.mode() == SafeMode::ENTRY_WAIT_GAP);
    REQUIRE(c.observe_interframe_gap(now + 100 * MS));
    REQUIRE(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.observe_relay_feedback(true, false, now + 105 * MS);
    c.observe_relay_feedback(true, false, now + 106 * MS);
    REQUIRE(c.mode() == SafeMode::EMULATING);
}

std::string last_event(const SafetyController& c) {
    if (c.event_count() == 0) return {};
    return std::string(c.event_at(c.event_count() - 1));
}

std::vector<std::string> last_events(const SafetyController& c, int n) {
    std::vector<std::string> out;
    uint64_t count = c.event_count();
    uint64_t start =
        count > static_cast<uint64_t>(n) ? count - static_cast<uint64_t>(n) : 0;
    for (uint64_t i = start; i < count; i++) {
        out.emplace_back(c.event_at(i));
    }
    return out;
}

bool has_event(const SafetyController& c, std::string_view text,
               uint64_t start = 0) {
    for (uint64_t i = start; i < c.event_count(); i++) {
        if (c.event_at(i) == text) return true;
    }
    return false;
}

}  // namespace

// ── Lease identity, generation, supersession ────────────────────────

// py: test_lease_uses_transport_handle_and_generation
TEST_CASE("lease uses transport, handle and generation") {
    for (Transport t : {Transport::WSS, Transport::BLE}) {
        auto owner = identity(t, 42, 7);
        auto c = connected_controller(owner);
        CHECK(c.owner().has_value());
        CHECK(*c.owner() == owner);
        CHECK_FALSE(c.command_motion(identity(t, 42, 6), 20, 2, 1 * S));
        CHECK_FALSE(c.command_motion(identity(t, 42, 8), 20, 2, 1 * S));
        CHECK(c.speed_tenths() == 0);
        CHECK(c.incline_half_percent() == 0);
    }
}

// py: test_only_owner_mutates_or_renews_the_single_four_second_lease
TEST_CASE("only owner mutates or renews the single 4 s lease") {
    auto owner = identity();
    auto other = identity(Transport::WSS, 101, 1);
    auto c = connected_controller(owner);
    REQUIRE(c.connect(other));

    CHECK(c.command_motion(owner, 30, 4, 1 * S));
    REQUIRE(c.lease_expires_at().has_value());
    CHECK(*c.lease_expires_at() == 5 * S);
    CHECK_FALSE(c.command_motion(other, 90, 8, 2 * S));
    CHECK_FALSE(c.heartbeat(other, 3'900 * MS));
    CHECK(*c.lease_expires_at() == 5 * S);
    CHECK(c.heartbeat(owner, 4 * S));
    CHECK(*c.lease_expires_at() == 8 * S);

    c.tick(7'999 * MS);
    CHECK(c.owner().has_value());
    c.tick(8 * S);
    CHECK_FALSE(c.owner().has_value());
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.speed_tenths() == 0);
    CHECK(c.incline_half_percent() == 0);
    CHECK_FALSE(c.relay_cmd());
}

// py: test_manual_lease_cannot_be_renewed_at_or_after_its_deadline
TEST_CASE("manual lease cannot be renewed at or after its deadline") {
    auto owner = identity();
    auto c = connected_controller(owner);

    CHECK_FALSE(c.heartbeat(owner, 4 * S));  // exact deadline loses
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.lease_expires_at().has_value());
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(last_event(c) == "emergency:lease_expired");
}

// py: test_unrelated_transport_drop_still_enforces_exact_lease_deadline
TEST_CASE("unrelated transport drop still enforces exact lease deadline") {
    auto owner = identity(Transport::BLE, 23, 1);
    auto c = connected_controller(owner);
    enter_emulate(c, owner);
    for (int64_t now : {1'400 * MS, 2'800 * MS, 3'900 * MS}) {
        c.observe_console_bytes(bytes("[loop:5550]"), now);
    }

    CHECK_FALSE(c.disconnect_transport(Transport::WSS, 4 * S));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.relay_cmd());
    CHECK(has_event(c, "emergency:lease_expired"));
}

// py: test_owner_disconnect_is_immediate_but_non_owner_disconnect_is_ignored
TEST_CASE("owner disconnect immediate, non-owner disconnect ignored") {
    auto owner = identity();
    auto other = identity(Transport::WSS, 101, 1);
    auto c = connected_controller(owner);
    REQUIRE(c.connect(other));
    enter_emulate(c, owner);

    CHECK_FALSE(c.disconnect(other, 500 * MS));
    CHECK(c.mode() == SafeMode::EMULATING);
    CHECK(c.disconnect(owner, 600 * MS));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK(last_event(c) == "emergency:owner_disconnect");
}

// py: test_reconnect_and_handle_reuse_cannot_inherit_an_old_lease
TEST_CASE("reconnect and handle reuse cannot inherit an old lease") {
    auto old = identity(Transport::BLE, 23, 10);
    auto c = connected_controller(old);
    CHECK(c.disconnect(old, 100 * MS));

    CHECK_FALSE(c.connect(old));  // stale generation
    auto reused = identity(Transport::BLE, 23, 11);
    CHECK(c.connect(reused));
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.command_motion(reused, 10, 0, 200 * MS));
    CHECK(c.acquire(reused, 300 * MS));
    REQUIRE(c.owner().has_value());
    CHECK(*c.owner() == reused);
    CHECK_FALSE(c.heartbeat(old, 400 * MS));
}

// py: test_new_generation_invalidates_and_safely_stops_superseded_owner
TEST_CASE("new generation invalidates and safely stops superseded owner") {
    auto old = identity(Transport::BLE, 23, 10);
    auto c = connected_controller(old);
    enter_emulate(c, old);

    auto fresh = identity(Transport::BLE, 23, 11);
    CHECK(c.connect(fresh));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK_FALSE(c.acquire(old, 200 * MS));
    CHECK(c.acquire(fresh, 200 * MS));
}

// py: test_executor_owns_locally_and_network_loss_does_not_renew_or_end_it
TEST_CASE("executor owns locally; network loss does not renew or end it") {
    auto executor = identity(Transport::EXECUTOR, 17, 3);
    auto wss = identity();
    auto c = connected_controller(executor);
    REQUIRE(c.connect(wss));
    enter_emulate(c, executor);

    CHECK_FALSE(c.lease_expires_at().has_value());
    CHECK_FALSE(c.heartbeat(wss, 500 * MS));
    CHECK_FALSE(c.disconnect(wss, 600 * MS));
    c.observe_console_bytes(bytes("[loop:5550]"), 700 * MS);
    c.tick(800 * MS);
    REQUIRE(c.owner().has_value());
    CHECK(*c.owner() == executor);
    CHECK(c.mode() == SafeMode::EMULATING);
}

// py: test_network_failure_matrix
TEST_CASE("network failure matrix") {
    struct Row {
        Transport source;
        const char* failure;
        bool must_proxy;
    };
    const Row rows[] = {
        {Transport::WSS, "silence", true},
        {Transport::WSS, "wss_drop", true},
        {Transport::WSS, "ble_drop", false},
        {Transport::BLE, "silence", true},
        {Transport::BLE, "wss_drop", false},
        {Transport::BLE, "ble_drop", true},
        {Transport::EXECUTOR, "silence", false},
        {Transport::EXECUTOR, "wss_drop", false},
        {Transport::EXECUTOR, "ble_drop", false},
    };
    for (const auto& row : rows) {
        CAPTURE(row.failure);
        auto owner = identity(row.source, 17, 1);
        auto c = connected_controller(owner);
        enter_emulate(c, owner);

        std::string_view failure(row.failure);
        if (failure == "silence") {
            for (int64_t now : {1'400 * MS, 2'800 * MS, 3'900 * MS}) {
                c.observe_console_bytes(bytes("[loop:5550]"), now);
            }
            c.tick(4 * S);
        } else if (failure == "wss_drop") {
            c.disconnect_transport(Transport::WSS, 1 * S);
        } else {
            c.disconnect_transport(Transport::BLE, 1 * S);
        }

        CHECK((c.mode() == SafeMode::PROXY) == row.must_proxy);
        CHECK(c.relay_cmd() == !row.must_proxy);
    }
}

// py: test_reset_and_watchdog_matrix_always_returns_hardware_to_proxy
TEST_CASE("reset and watchdog matrix always returns hardware to proxy") {
    for (Transport source :
         {Transport::WSS, Transport::BLE, Transport::EXECUTOR}) {
        for (bool watchdog : {false, true}) {
            auto owner = identity(source, 17, 1);
            auto c = connected_controller(owner);
            enter_emulate(c, owner);

            if (watchdog) {
                c.watchdog_stall(1 * S);
            } else {
                c.reset(1 * S, "brownout");
            }

            CHECK(c.mode() == SafeMode::PROXY);
            CHECK_FALSE(c.owner().has_value());
            CHECK_FALSE(c.relay_cmd());
            CHECK_FALSE(c.tx_enable());
        }
    }
}

// py: test_reset_class_failures_invalidate_pre_reset_connections
TEST_CASE("reset-class failures invalidate pre-reset connections") {
    for (bool watchdog : {false, true}) {
        auto old = identity(Transport::WSS, 7, 1);
        auto c = connected_controller(old);
        if (watchdog) {
            c.watchdog_stall(1 * S);
        } else {
            c.reset(1 * S);
        }

        CHECK_FALSE(c.acquire(old, 1'100 * MS));
        CHECK_FALSE(c.connect(old));  // generation map survives reset
        auto fresh = identity(Transport::WSS, 7, 2);
        CHECK(c.connect(fresh));
        CHECK(c.acquire(fresh, 1'200 * MS));
        REQUIRE(c.owner().has_value());
        CHECK(*c.owner() == fresh);
    }
}

// py: test_console_source_is_hardware_bridge_and_network_failures_do_nothing
TEST_CASE("console bridge: network failures do nothing") {
    SafetyController c;
    c.disconnect_transport(Transport::WSS, 1 * S);
    c.disconnect_transport(Transport::BLE, 2 * S);
    c.tick(100 * S);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.feedback() == Feedback::UNKNOWN);
    CHECK_FALSE(c.relay_cmd());
}

// ── Motion clamps ───────────────────────────────────────────────────

// py: (clamps per PLAN; accept 0/120/0/30, reject 121/-1/31)
TEST_CASE("motion clamps accept boundary values and reject outside") {
    auto owner = identity();
    auto c = connected_controller(owner);

    CHECK(c.command_motion(owner, 0, 0, 100 * MS));
    CHECK(c.command_motion(owner, 120, 30, 200 * MS));
    CHECK(c.speed_tenths() == 120);
    CHECK(c.incline_half_percent() == 30);

    CHECK_FALSE(c.command_motion(owner, 121, 0, 300 * MS));
    CHECK(last_event(c) == "motion_rejected:speed_range");
    CHECK_FALSE(c.command_motion(owner, -1, 0, 300 * MS));
    CHECK_FALSE(c.command_motion(owner, 0, 31, 300 * MS));
    CHECK(last_event(c) == "motion_rejected:incline_range");
    CHECK_FALSE(c.command_motion(owner, 0, -1, 300 * MS));
    CHECK(c.speed_tenths() == 120);
    CHECK(c.incline_half_percent() == 30);
}

// ── Console freshness ───────────────────────────────────────────────

// py: test_console_freshness_requires_a_complete_valid_frame
TEST_CASE("console freshness requires a complete valid frame") {
    SafetyController c;
    c.observe_console_bytes(bytes("[hmph:0000"), 0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    c.observe_console_bytes(bytes("]"), 250 * MS);
    REQUIRE(c.last_complete_console_frame_at().has_value());
    CHECK(*c.last_complete_console_frame_at() == 250 * MS);

    c.observe_console_bytes(bytes("\xff[bad frame]\x00"), 500 * MS);
    CHECK(*c.last_complete_console_frame_at() == 250 * MS);
    c.observe_console_bytes(bytes("[inc:0000]"), 1 * S);
    CHECK(*c.last_complete_console_frame_at() == 1 * S);
}

// py: (partial/corrupt/oversized never refresh)
TEST_CASE("partial, corrupt and oversized frames never refresh") {
    SafetyController c;
    // Corrupt: non-printable byte clears the candidate
    uint8_t corrupt[] = {'[', 'k', ':', 0x01, ']'};
    c.observe_console_bytes(std::span<const uint8_t>(corrupt, sizeof(corrupt)),
                            0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    // Key must start with a letter
    c.observe_console_bytes(bytes("[9key:1]"), 0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    // Oversized: >100 bytes in the candidate is discarded
    std::string oversized = "[k:";
    oversized.append(120, 'x');
    oversized += "]";
    c.observe_console_bytes(bytes(oversized), 0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    // Value longer than 64 rejected by the frame pattern
    std::string longval = "[k:";
    longval.append(65, 'v');
    longval += "]";
    c.observe_console_bytes(bytes(longval), 0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    // A valid frame still parses after all that
    c.observe_console_bytes(bytes("[hmph:78]"), 10 * MS);
    REQUIRE(c.last_complete_console_frame_at().has_value());
    CHECK(*c.last_complete_console_frame_at() == 10 * MS);
}

// py: test_late_console_frame_cannot_overwrite_missed_freshness_deadline
TEST_CASE("late console frame cannot overwrite missed freshness deadline") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);

    CHECK(c.observe_console_bytes(bytes("[loop:5550]"), 1'500 * MS) == 0);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    REQUIRE(c.last_complete_console_frame_at().has_value());
    CHECK(*c.last_complete_console_frame_at() == 0);
    CHECK(last_event(c) == "emergency:console_stale");
}

// py: test_stale_console_forces_immediate_zero_and_bypass
TEST_CASE("stale console forces immediate zero and bypass") {
    for (int64_t age : {int64_t{1'500'001}, 20 * S}) {
        auto owner = age < MANUAL_LEASE_US
                         ? identity()
                         : identity(Transport::EXECUTOR, 55, 1);
        auto c = connected_controller(owner);
        enter_emulate(c, owner, 0);

        c.tick(age);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK(last_event(c) == "emergency:console_stale");
    }
}

// py: test_exactly_one_point_five_seconds_is_stale
TEST_CASE("exactly 1.5 s is stale") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner, 0);

    c.tick(1'499'999);
    CHECK(c.mode() == SafeMode::EMULATING);
    c.tick(1'500'000);
    CHECK(c.mode() == SafeMode::PROXY);
}

// py: test_motion_command_at_console_deadline_cannot_refresh_or_mutate
TEST_CASE("motion command at console deadline cannot refresh or mutate") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);

    CHECK_FALSE(c.command_motion(owner, 60, 10, 1'500 * MS));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK(c.speed_tenths() == 0);
    CHECK(c.incline_half_percent() == 0);
    CHECK(last_event(c) == "emergency:console_stale");
}

// ── Emulate entry ───────────────────────────────────────────────────

// py: test_entry_order_and_first_zero_frame_follow_settled_transfer
TEST_CASE("entry order and first zero frame follow settled transfer") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);

    CHECK(c.request_emulate(owner, 0, true));
    CHECK(last_events(c, 5) == std::vector<std::string>{
                                   "command_zero",
                                   "configure_inverted_uart",
                                   "verify_physical_idle_low",
                                   "tx_enable_on",
                                   "wait_entry_gap",
                               });
    CHECK_FALSE(c.relay_cmd());
    CHECK(c.observe_interframe_gap(200 * MS));
    CHECK(last_event(c) == "relay_cmd_on");
    CHECK_FALSE(has_event(c, "send_first_complete_zero_frame"));

    c.observe_relay_feedback(true, false, 205 * MS);
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.observe_relay_feedback(true, false, 206 * MS);
    CHECK(last_events(c, 2) == std::vector<std::string>{
                                   "feedback_emulate_stable",
                                   "send_first_complete_zero_frame",
                               });
}

// py: test_entry_preconditions (adapted: state reached through the public
// API instead of attribute pokes)
TEST_CASE("entry rejected when not owner") {
    auto owner = identity();
    auto other = identity(Transport::WSS, 101, 1);
    auto c = connected_controller(owner);
    REQUIRE(c.connect(other));
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    CHECK_FALSE(c.request_emulate(other, 100 * MS, true));
    CHECK(last_event(c) == "entry_rejected:not_owner");
}

TEST_CASE("entry rejected when not proxy") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 200 * MS);
    CHECK_FALSE(c.request_emulate(owner, 200 * MS, true));
    CHECK(last_event(c) == "entry_rejected:not_proxy");
}

TEST_CASE("entry rejected when tread not ok") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    c.set_tread_ok(false, 100 * MS);
    CHECK_FALSE(c.request_emulate(owner, 500 * MS, true));
    CHECK(last_event(c) == "entry_rejected:tread_not_ok");
    CHECK_FALSE(c.relay_cmd());
}

TEST_CASE("entry rejected when feedback is not bypass (boot UNKNOWN)") {
    SafetyController c;  // boot: feedback UNKNOWN, never sampled
    auto owner = identity();
    REQUIRE(c.connect(owner));
    REQUIRE(c.acquire(owner, 0));
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    CHECK_FALSE(c.request_emulate(owner, 500 * MS, true));
    CHECK(last_event(c) == "entry_rejected:feedback_not_bypass");
    CHECK_FALSE(c.relay_cmd());
}

TEST_CASE("entry rejected when console unknown or stale") {
    auto owner = identity();
    auto c = connected_controller(owner);
    // Unknown: no frame ever observed
    CHECK_FALSE(c.request_emulate(owner, 500 * MS, true));
    CHECK(last_event(c) == "entry_rejected:console_not_fresh");

    // Stale: frame at 0, entry at 2.0 s
    auto c2 = connected_controller(owner);
    c2.observe_console_bytes(bytes("[hmph:0000]"), 0);
    CHECK_FALSE(c2.request_emulate(owner, 2 * S, true));
    CHECK(last_event(c2) == "entry_rejected:console_not_fresh");
    CHECK_FALSE(c2.relay_cmd());
}

TEST_CASE("entry rejected when fault latched") {
    auto owner = identity();
    auto c = connected_controller(owner);
    // Latch a fault via BOTH_CLOSED, then restore bypass feedback
    c.observe_relay_feedback(false, false, 100 * MS);
    CHECK(c.fault_latched());
    c.observe_relay_feedback(false, true, 200 * MS);
    // Owner lease died with the emergency stop; reacquire
    REQUIRE(c.acquire(owner, 250 * MS));
    c.observe_console_bytes(bytes("[hmph:0000]"), 300 * MS);
    CHECK_FALSE(c.request_emulate(owner, 400 * MS, true));
    CHECK(last_event(c) == "entry_rejected:fault_latched");
}

TEST_CASE("entry rejected when uart not idle low") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    CHECK_FALSE(c.request_emulate(owner, 500 * MS, false));
    CHECK(last_event(c) == "entry_rejected:uart_not_idle_low");
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
}

// py: test_entry_gap_timeout_aborts_without_moving_relay
TEST_CASE("entry gap timeout aborts without moving relay") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));

    c.tick(1 * S);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(has_event(c, "relay_cmd_on"));
    CHECK(last_event(c) == "entry_abort:no_gap");
}

// py: test_gap_event_at_entry_deadline_cannot_leave_tx_enabled
TEST_CASE("gap event at entry deadline cannot leave tx enabled") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    c.observe_console_bytes(bytes("[loop:5550]"), 990 * MS);

    CHECK_FALSE(c.observe_interframe_gap(1 * S));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
}

// py: test_reentrant_entry_request_cannot_rewind_an_active_transfer
TEST_CASE("reentrant entry request cannot rewind an active transfer") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    CHECK(c.relay_cmd());

    CHECK_FALSE(c.request_emulate(owner, 105 * MS, true));
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.tick(110 * MS);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
}

// py: test_reentrant_entry_request_enforces_feedback_deadline_first
TEST_CASE("reentrant entry request enforces feedback deadline first") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));

    CHECK_FALSE(c.request_emulate(owner, 110 * MS, true));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK(c.fault_latched());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK(last_event(c) == "emergency:entry_feedback_timeout");
}

// py: test_entry_feedback_timeout_releases_and_latches_fault
TEST_CASE("entry feedback timeout releases and latches fault") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(200 * MS));

    c.tick(210 * MS);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.fault_latched());
    CHECK_FALSE(c.relay_cmd());
    CHECK(last_event(c) == "emergency:entry_feedback_timeout");
}

// py: test_entry_feedback_mismatch_releases_and_latches_fault
TEST_CASE("entry feedback mismatch releases and latches fault") {
    struct Row { bool nc_high; bool no_high; };
    for (Row row : {Row{false, true}, Row{true, true}}) {
        auto owner = identity();
        auto c = connected_controller(owner);
        c.observe_console_bytes(bytes("[hmph:0000]"), 0);
        REQUIRE(c.request_emulate(owner, 0, true));
        REQUIRE(c.observe_interframe_gap(200 * MS));

        c.observe_relay_feedback(row.nc_high, row.no_high, 205 * MS);
        c.tick(210 * MS);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK(c.fault_latched());
        CHECK_FALSE(c.relay_cmd());
        CHECK(last_event(c) == "emergency:entry_feedback_timeout");
    }
}

// ── Normal exit ─────────────────────────────────────────────────────

// py: test_complete_normal_exit_order
TEST_CASE("complete normal exit order") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);

    CHECK(c.request_normal_exit(owner, 500 * MS));
    CHECK(last_events(c, 2) == std::vector<std::string>{
                                   "send_and_finish_complete_zero_frame",
                                   "wait_exit_gap",
                               });
    CHECK(c.relay_cmd());
    CHECK(c.observe_interframe_gap(700 * MS));
    CHECK(last_event(c) == "relay_cmd_off");
    CHECK(c.tx_enable());

    c.observe_relay_feedback(false, true, 705 * MS);
    CHECK(c.mode() == SafeMode::EXIT_WAIT_FEEDBACK);
    c.observe_relay_feedback(false, true, 706 * MS);
    CHECK(last_events(c, 3) == std::vector<std::string>{
                                   "feedback_bypass_stable",
                                   "tx_enable_off",
                                   "lease_released",
                               });
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
}

// py: test_normal_exit_gap_timeout_bypasses_immediately_then_checks_feedback
TEST_CASE("normal exit gap timeout bypasses immediately, then checks feedback") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);
    REQUIRE(c.request_normal_exit(owner, 500 * MS));
    c.observe_console_bytes(bytes("[loop:5550]"), 1'490 * MS);

    c.tick(1'500 * MS);
    CHECK_FALSE(c.relay_cmd());
    CHECK(c.mode() == SafeMode::EXIT_WAIT_FEEDBACK);
    CHECK(last_events(c, 2) == std::vector<std::string>{
                                   "exit_gap_timeout",
                                   "relay_cmd_off",
                               });
}

// py: test_gap_event_at_exit_deadline_cannot_leave_relay_energized
TEST_CASE("gap event at exit deadline cannot leave relay energized") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);
    REQUIRE(c.request_normal_exit(owner, 500 * MS));
    c.observe_console_bytes(bytes("[loop:5550]"), 1'490 * MS);

    CHECK_FALSE(c.observe_interframe_gap(1'500 * MS));
    CHECK(c.mode() == SafeMode::EXIT_WAIT_FEEDBACK);
    CHECK_FALSE(c.relay_cmd());
    CHECK(c.tx_enable());
}

// py: test_exit_feedback_mismatch_releases_and_latches_fault
TEST_CASE("exit feedback mismatch releases and latches fault") {
    struct Row { bool nc_high; bool no_high; };
    for (Row row : {Row{true, false}, Row{true, true}}) {
        auto owner = identity();
        auto c = connected_controller(owner);
        enter_emulate(c, owner);
        REQUIRE(c.request_normal_exit(owner, 500 * MS));
        REQUIRE(c.observe_interframe_gap(700 * MS));

        c.observe_relay_feedback(row.nc_high, row.no_high, 705 * MS);
        c.tick(710 * MS);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK(c.fault_latched());
        CHECK_FALSE(c.relay_cmd());
        CHECK(last_event(c) == "emergency:exit_feedback_timeout");
    }
}

// py: test_exit_feedback_timeout_latches_fault
TEST_CASE("exit feedback timeout latches fault") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);
    REQUIRE(c.request_normal_exit(owner, 500 * MS));
    REQUIRE(c.observe_interframe_gap(700 * MS));

    c.tick(710 * MS);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.fault_latched());
    CHECK(last_event(c) == "emergency:exit_feedback_timeout");
}

// py: test_stale_console_cannot_be_raced_by_a_gap_observation
TEST_CASE("stale console cannot be raced by a gap observation") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 1'490 * MS, true));

    CHECK_FALSE(c.observe_interframe_gap(1'500 * MS));
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK(last_event(c) == "emergency:console_stale");
}

// ── Feedback qualification ──────────────────────────────────────────

// py: test_matching_feedback_requires_temporal_stability
TEST_CASE("matching feedback requires temporal stability") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));

    c.observe_relay_feedback(true, false, 101 * MS);
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    CHECK_FALSE(has_event(c, "send_first_complete_zero_frame"));
    c.tick(101'999);
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.tick(102 * MS);  // timer tick alone never qualifies
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.observe_relay_feedback(true, false, 102 * MS);
    CHECK(c.mode() == SafeMode::EMULATING);
    CHECK(last_events(c, 2) == std::vector<std::string>{
                                   "feedback_emulate_stable",
                                   "send_first_complete_zero_frame",
                               });
}

// py: test_transition_feedback_may_pass_through_both_open_before_settling
TEST_CASE("transition feedback may pass through BOTH_OPEN before settling") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));

    c.observe_relay_feedback(true, true, 101 * MS);  // break-before-make
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    CHECK_FALSE(c.fault_latched());
    c.observe_relay_feedback(true, false, 105 * MS);
    c.observe_relay_feedback(true, false, 106 * MS);
    CHECK(c.mode() == SafeMode::EMULATING);
    CHECK_FALSE(c.fault_latched());
}

// py: test_both_closed_feedback_faults_immediately_during_transfer
TEST_CASE("BOTH_CLOSED feedback faults immediately during transfer") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));

    c.observe_relay_feedback(false, false, 101 * MS);

    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.owner().has_value());
    CHECK(c.fault_latched());
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK(last_event(c) == "emergency:relay_feedback_both_closed");
}

// py: (BOTH_CLOSED is a latched fault in every mode)
TEST_CASE("BOTH_CLOSED latches a fault and releases in every mode") {
    // PROXY
    {
        SafetyController c;
        c.observe_relay_feedback(false, false, 0);
        CHECK(c.fault_latched());
        CHECK_FALSE(c.relay_cmd());
        CHECK(last_event(c) == "emergency:relay_feedback_both_closed");
    }
    // EMULATING
    {
        auto owner = identity();
        auto c = connected_controller(owner);
        enter_emulate(c, owner);
        c.observe_relay_feedback(false, false, 500 * MS);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK(c.fault_latched());
        CHECK_FALSE(c.relay_cmd());
        CHECK_FALSE(c.tx_enable());
    }
}

// py: test_feedback_qualification_requires_a_sample_at_stability_time
TEST_CASE("feedback qualification requires a sample at stability time") {
    auto owner = identity();
    auto c = connected_controller(owner);
    c.observe_console_bytes(bytes("[hmph:0000]"), 0);
    REQUIRE(c.request_emulate(owner, 0, true));
    REQUIRE(c.observe_interframe_gap(100 * MS));
    c.observe_relay_feedback(true, false, 105 * MS);

    c.tick(106 * MS);
    CHECK(c.mode() == SafeMode::ENTRY_WAIT_FEEDBACK);
    c.observe_relay_feedback(true, false, 106 * MS);
    CHECK(c.mode() == SafeMode::EMULATING);
}

// py: test_feedback_at_exact_deadline_always_fails_closed
TEST_CASE("feedback at exact 10 ms deadline always fails closed") {
    for (bool via_tick : {true, false}) {
        auto owner = identity();
        auto c = connected_controller(owner);
        c.observe_console_bytes(bytes("[hmph:0000]"), 0);
        REQUIRE(c.request_emulate(owner, 0, true));
        REQUIRE(c.observe_interframe_gap(100 * MS));
        c.observe_relay_feedback(true, false, 108 * MS);

        if (via_tick) {
            c.tick(110 * MS);
        } else {
            c.observe_relay_feedback(true, false, 110 * MS);
        }

        CHECK(c.mode() == SafeMode::PROXY);
        CHECK_FALSE(c.owner().has_value());
        CHECK(c.fault_latched());
        CHECK_FALSE(c.relay_cmd());
        CHECK_FALSE(c.tx_enable());
    }
}

// py: test_console_staleness_never_waits_for_a_transition_deadline
TEST_CASE("console staleness never waits for a transition deadline") {
    for (SafeMode target :
         {SafeMode::ENTRY_WAIT_GAP, SafeMode::ENTRY_WAIT_FEEDBACK,
          SafeMode::EXIT_WAIT_GAP, SafeMode::EXIT_WAIT_FEEDBACK}) {
        auto owner = identity();
        auto c = connected_controller(owner);
        c.observe_console_bytes(bytes("[hmph:0000]"), 0);
        bool entry_mode = target == SafeMode::ENTRY_WAIT_GAP ||
                          target == SafeMode::ENTRY_WAIT_FEEDBACK;
        int64_t entry_time = entry_mode ? 1'490 * MS : 0;
        REQUIRE(c.request_emulate(owner, entry_time, true));
        if (target == SafeMode::ENTRY_WAIT_FEEDBACK) {
            REQUIRE(c.observe_interframe_gap(1'495 * MS));
        }
        if (target == SafeMode::EXIT_WAIT_GAP ||
            target == SafeMode::EXIT_WAIT_FEEDBACK) {
            REQUIRE(c.observe_interframe_gap(100 * MS));
            c.observe_relay_feedback(true, false, 105 * MS);
            c.observe_relay_feedback(true, false, 106 * MS);
            REQUIRE(c.request_normal_exit(owner, 600 * MS));
        }
        if (target == SafeMode::EXIT_WAIT_FEEDBACK) {
            REQUIRE(c.observe_interframe_gap(1'495 * MS));
        }
        REQUIRE(c.mode() == target);

        c.tick(1'500 * MS);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK_FALSE(c.owner().has_value());
        CHECK_FALSE(c.relay_cmd());
        CHECK_FALSE(c.tx_enable());
        CHECK(last_event(c) == "emergency:console_stale");
    }
}

// ── Emergency / feedback decode / tread_ok / USB / boot ─────────────

// py: test_console_bridge_failure_matrix_remains_hardware_proxy
TEST_CASE("console bridge failure matrix remains hardware proxy") {
    for (bool watchdog : {false, true}) {
        SafetyController c;
        if (watchdog) {
            c.watchdog_stall(1 * S);
        } else {
            c.reset(1 * S, "brownout");
        }
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK_FALSE(c.owner().has_value());
        CHECK_FALSE(c.relay_cmd());
        CHECK_FALSE(c.tx_enable());
    }
}

// py: test_emergency_paths_never_wait_for_a_gap
TEST_CASE("emergency paths never wait for a gap") {
    const char* reasons[] = {
        "tread_not_ok", "console_stale", "lease_expired",
        "explicit_emergency_stop", "brownout", "reset", "watchdog",
    };
    for (const char* reason : reasons) {
        auto owner = identity();
        auto c = connected_controller(owner);
        enter_emulate(c, owner);
        uint64_t before = c.event_count();

        c.emergency_stop(reason, 500 * MS);

        CHECK(c.mode() == SafeMode::PROXY);
        CHECK_FALSE(c.relay_cmd());
        CHECK_FALSE(c.tx_enable());
        for (uint64_t i = before; i < c.event_count(); i++) {
            CHECK(std::string(c.event_at(i)).find("wait") == std::string::npos);
        }
    }
}

// py: test_all_four_relay_feedback_states_are_decoded
TEST_CASE("all four relay feedback states are decoded") {
    CHECK(feedback_from_gpio(false, true) == Feedback::BYPASS);
    CHECK(feedback_from_gpio(true, false) == Feedback::EMULATE);
    CHECK(feedback_from_gpio(false, false) == Feedback::BOTH_CLOSED);
    CHECK(feedback_from_gpio(true, true) == Feedback::BOTH_OPEN);
}

// py: test_any_non_emulate_feedback_while_emulating_is_a_fault
TEST_CASE("any non-emulate feedback while emulating is a fault") {
    struct Row { bool nc_high; bool no_high; };
    for (Row row : {Row{false, true}, Row{false, false}, Row{true, true}}) {
        auto owner = identity();
        auto c = connected_controller(owner);
        enter_emulate(c, owner);

        c.observe_relay_feedback(row.nc_high, row.no_high, 500 * MS);
        CHECK(c.mode() == SafeMode::PROXY);
        CHECK(c.fault_latched());
    }
}

// py: test_tread_ok_loss_is_hardware_permission_loss_and_immediate
TEST_CASE("tread_ok loss is hardware permission loss and immediate") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);

    c.set_tread_ok(false, 500 * MS);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK_FALSE(c.relay_cmd());
    CHECK(last_event(c) == "emergency:tread_not_ok");
}

// py: test_native_usb_attach_is_active_low_and_defaults_detached
TEST_CASE("native USB attach is active-low and defaults detached") {
    SafetyController c;
    CHECK_FALSE(c.usb_pullup_enabled());

    c.set_vbus_present_n(true);
    CHECK_FALSE(c.usb_pullup_enabled());
    c.set_vbus_present_n(false);
    CHECK(c.usb_pullup_enabled());
    c.set_vbus_present_n(true);
    CHECK_FALSE(c.usb_pullup_enabled());
}

// py: test_reset_requires_an_actual_bypass_feedback_sample_before_entry
TEST_CASE("reset requires an actual bypass feedback sample before entry") {
    auto old = identity();
    auto c = connected_controller(old);
    enter_emulate(c, old);
    c.reset(500 * MS);

    CHECK(c.feedback() == Feedback::UNKNOWN);
    auto fresh = identity(Transport::WSS, 100, 2);
    CHECK(c.connect(fresh));
    CHECK(c.acquire(fresh, 600 * MS));
    c.observe_console_bytes(bytes("[hmph:0000]"), 600 * MS);
    CHECK_FALSE(c.request_emulate(fresh, 600 * MS, true));
    c.observe_relay_feedback(false, true, 700 * MS);
    CHECK(c.request_emulate(fresh, 700 * MS, true));
}

// py: test_model_constants_are_the_normative_deadlines
TEST_CASE("model constants are the normative deadlines") {
    CHECK(MANUAL_LEASE_US == 4'000'000);
    CHECK(CONSOLE_FRESH_US == 1'500'000);
    CHECK(TRANSFER_GAP_DEADLINE_US == 1'000'000);
    CHECK(RELAY_FEEDBACK_DEADLINE_US == 10'000);
    CHECK(RELAY_FEEDBACK_STABLE_US == 1'000);
    CHECK(WDT_US == 2'000'000);
    CHECK(TREAD_OK_TO_NC_MAX_US == 10'000);
    CHECK(SOFTWARE_TO_NC_MAX_US == 250'000);
    CHECK(WDT_TO_NC_MAX_US == 2'250'000);
    CHECK(NORMAL_TRANSITION_ACCEPTANCE_CYCLES == 1'000);
    CHECK(SPEED_MAX_TENTHS == 120);
    CHECK(INCLINE_APP_MAX_HALF == 30);
    CHECK(INCLINE_ABS_MAX_HALF == 198);
}

// Boot state (PLAN: boot = proxy, feedback unknown, no bypass assumption)
TEST_CASE("boot state is proxy with unknown feedback and no outputs") {
    SafetyController c;
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.feedback() == Feedback::UNKNOWN);
    CHECK_FALSE(c.relay_cmd());
    CHECK_FALSE(c.tx_enable());
    CHECK_FALSE(c.fault_latched());
    CHECK_FALSE(c.owner().has_value());
    CHECK(c.speed_tenths() == 0);
    CHECK(c.incline_half_percent() == 0);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
}

// py: (watchdog_stall clears console timestamp and feedback)
TEST_CASE("watchdog stall clears connections, console and feedback state") {
    auto owner = identity();
    auto c = connected_controller(owner);
    enter_emulate(c, owner);

    c.watchdog_stall(500 * MS);
    CHECK(c.mode() == SafeMode::PROXY);
    CHECK(c.feedback() == Feedback::UNKNOWN);
    CHECK_FALSE(c.last_complete_console_frame_at().has_value());
    CHECK_FALSE(c.usb_pullup_enabled());
    // Pre-stall connection is gone: acquire requires reconnect
    CHECK_FALSE(c.acquire(owner, 600 * MS));
}
