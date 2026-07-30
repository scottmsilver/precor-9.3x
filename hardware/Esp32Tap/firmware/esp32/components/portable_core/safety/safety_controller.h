/*
 * safety_controller.h — C++ port of firmware/safety_model.py Controller.
 *
 * Line-faithful port of the executable host reference contract (PLAN.md
 * "Executable safety contract"). Same mode/feedback enums, same event
 * strings, same _enforce_due_safety ordering ("advance all due deadlines
 * before consuming timed input"; an input at an exact deadline loses to
 * the deadline). Float-epsilon time becomes exact int64 microseconds
 * (PLAN D4):
 *   - a deadline is due when now >= deadline;
 *   - feedback qualifies only when since + STABLE_US <= now AND
 *     since + STABLE_US < deadline (a sample at the exact 10 ms boundary
 *     fails closed).
 *
 * Phase-1 lease transports (PLAN D5): EXECUTOR plus LOCAL_TEST integer
 * handles standing in for WSS/BLE connection objects. The generation /
 * supersession machinery is fully implemented; only the real WSS/BLE
 * plumbing is deferred.
 *
 * Events are recorded into a fixed-capacity ring (cold path only) for
 * host-test assertion against the Python model's event sequences.
 */

#pragma once

#include <cstdint>
#include <array>
#include <optional>
#include <span>
#include <string_view>

#include "safety/safety_constants.h"

namespace esp32tap::safety {

enum class Transport : uint8_t { WSS, BLE, EXECUTOR };

enum class SafeMode : uint8_t {
    PROXY,
    ENTRY_WAIT_GAP,
    ENTRY_WAIT_FEEDBACK,
    EMULATING,
    EXIT_WAIT_GAP,
    EXIT_WAIT_FEEDBACK,
};

// Decoded state of K1's grounded dry-contact feedback pole.
enum class Feedback : uint8_t {
    UNKNOWN,
    BYPASS,       // NC_CLOSED_NO_OPEN
    EMULATE,      // NC_OPEN_NO_CLOSED
    BOTH_CLOSED,  // NC_CLOSED_NO_CLOSED — latched fault in every mode
    BOTH_OPEN,    // NC_OPEN_NO_OPEN — break-before-make transit only
};

// safety_model.py Feedback.from_gpio: NC/NO pulled up (R25/R26), a HIGH
// line means the contact is OPEN.
Feedback feedback_from_gpio(bool nc_high, bool no_high);

// A connection handle plus a non-reusable generation (PLAN D5: integer
// handles stand in for WSS socket objects / BLE conn_handles in phase 1).
struct ConnectionIdentity {
    Transport transport{Transport::EXECUTOR};
    int32_t handle{0};
    int64_t generation{0};  // must be >= 0

    bool operator==(const ConnectionIdentity& o) const {
        return transport == o.transport && handle == o.handle &&
               generation == o.generation;
    }
    bool same_connection(const ConnectionIdentity& o) const {
        return transport == o.transport && handle == o.handle;
    }
};

class SafetyController {
public:
    static constexpr int MAX_ACTIVE_CONNECTIONS = 8;
    static constexpr int MAX_TRACKED_GENERATIONS = 16;
    static constexpr int EVENT_CAPACITY = 256;
    static constexpr int EVENT_MAX_LEN = 95;

    SafetyController() = default;

    // --- observable state (mirrors safety_model.py attributes) ---
    SafeMode mode() const { return mode_; }
    int speed_tenths() const { return speed_tenths_; }
    int incline_half_percent() const { return incline_half_percent_; }
    bool tread_ok() const { return tread_ok_; }
    Feedback feedback() const { return feedback_; }
    bool fault_latched() const { return fault_latched_; }
    bool relay_cmd() const { return relay_cmd_; }
    bool tx_enable() const { return tx_enable_; }
    bool usb_pullup_enabled() const { return usb_pullup_enabled_; }
    std::optional<int64_t> last_complete_console_frame_at() const {
        return last_frame_at_;
    }
    std::optional<ConnectionIdentity> owner() const {
        if (!lease_valid_) return std::nullopt;
        return lease_owner_;
    }
    std::optional<int64_t> lease_expires_at() const {
        return std::nullopt;
    }

    // --- event ring (cold path, for tests and audit) ---
    // Total number of events ever appended (ring keeps the newest
    // EVENT_CAPACITY of them). uint64_t: a plain int would be signed
    // overflow UB on long uptimes.
    uint64_t event_count() const { return event_total_; }
    // Absolute-indexed event text; empty view if evicted/out of range.
    std::string_view event_at(uint64_t index) const;

    // --- public operations (same names as safety_model.py) ---
    bool connect(const ConnectionIdentity& connection);
    bool acquire(const ConnectionIdentity& connection, int64_t now);
    bool heartbeat(const ConnectionIdentity& connection, int64_t now);
    bool command_motion(const ConnectionIdentity& connection,
                        int speed_tenths, int incline_half_percent,
                        int64_t now);
    bool disconnect(const ConnectionIdentity& connection, int64_t now);
    bool disconnect_transport(Transport transport, int64_t now);
    // Consume bytes and timestamp only syntactically complete KV frames.
    // Input length is unbounded upstream; each byte is validated and the
    // in-progress candidate is capped at 100 bytes exactly like the model.
    int observe_console_bytes(std::span<const uint8_t> data, int64_t now);
    bool request_emulate(const ConnectionIdentity& connection, int64_t now,
                         bool uart_idle_low);
    bool request_emulate_recovering(const ConnectionIdentity& connection,
                                    int64_t now, bool uart_idle_low);
    bool observe_interframe_gap(int64_t now);
    Feedback observe_relay_feedback(bool nc_high, bool no_high, int64_t now);
    bool request_normal_exit(const ConnectionIdentity& connection, int64_t now);
    void set_tread_ok(bool value, int64_t now);
    void set_vbus_present_n(bool level_high);
    void tick(int64_t now);
    // FORK EXTENSION — not in safety_model.py (documented in
    // PROVENANCE.md): zero commanded motion without touching mode,
    // lease, relay, or feedback state. Called when EmulationCycle's
    // 3-hour inactivity timeout fires (that timeout lives in the cycle
    // engine, Pi parity with cpp/emulation_engine.h) so the
    // authoritative state can never keep reporting stale nonzero motion
    // after the wire has been zeroed. Strictly monotonic toward safe:
    // it only ever lowers motion to zero.
    void safety_timeout_zero_motion(int64_t now);
    void emergency_stop(std::string_view reason, int64_t now);
    void watchdog_stall(int64_t now);
    void reset(int64_t now, std::string_view reason = "reset");

private:
    bool console_is_fresh(int64_t now) const;
    bool is_owner(const ConnectionIdentity& connection) const;
    bool authorize_owner(const ConnectionIdentity& connection, int64_t now,
                         std::string_view ignored_event);
    void begin_emulate_entry(int64_t now);
    std::optional<Feedback> feedback_expected() const;
    void finish_feedback_transfer();
    bool qualify_feedback(int64_t now);
    bool enforce_due_safety(int64_t now);
    void release_lease(bool log);
    void reset_class_stop(std::string_view reason, int64_t now);

    void push_event(std::string_view text);
    void push_event2(std::string_view prefix, std::string_view reason);
    void push_connection_event(std::string_view prefix,
                               const ConnectionIdentity& connection);
    int64_t highest_generation_for(const ConnectionIdentity& c) const;
    bool set_highest_generation(const ConnectionIdentity& c);
    bool is_active(const ConnectionIdentity& c) const;
    void remove_active_same_connection(const ConnectionIdentity& c);
    void remove_active_exact(const ConnectionIdentity& c);

    // --- state ---
    SafeMode mode_ = SafeMode::PROXY;
    int speed_tenths_ = 0;
    int incline_half_percent_ = 0;
    bool tread_ok_ = true;
    Feedback feedback_ = Feedback::UNKNOWN;
    bool fault_latched_ = false;
    bool relay_cmd_ = false;
    bool tx_enable_ = false;
    bool usb_pullup_enabled_ = false;
    std::optional<int64_t> last_frame_at_{};

    bool lease_valid_ = false;
    ConnectionIdentity lease_owner_{};

    std::array<ConnectionIdentity, MAX_ACTIVE_CONNECTIONS> active_{};
    int active_count_ = 0;

    struct GenerationEntry {
        Transport transport;
        int32_t handle;
        int64_t highest;
    };
    std::array<GenerationEntry, MAX_TRACKED_GENERATIONS> generations_{};
    int generation_count_ = 0;

    // Console-frame candidate scanner (max 100 bytes, like the model)
    std::array<uint8_t, 101> candidate_{};
    int candidate_len_ = 0;

    std::optional<int64_t> phase_deadline_{};
    std::optional<int64_t> feedback_candidate_since_{};
    std::optional<int64_t> bypass_since_{};
    bool bypass_qualified_ = false;

    std::array<std::array<char, EVENT_MAX_LEN + 1>, EVENT_CAPACITY> events_{};
    uint64_t event_total_ = 0;
};

}  // namespace esp32tap::safety
