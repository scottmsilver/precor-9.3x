/*
 * safety_controller.cpp — C++ port of firmware/safety_model.py Controller.
 *
 * Every method mirrors its Python namesake; comments cite the model where
 * the port is non-obvious. Event strings are byte-identical to the model
 * so host tests can assert the same sequences.
 */

#include "safety/safety_controller.h"

#include <charconv>

namespace esp32tap::safety {

namespace {

std::string_view transport_upper(Transport t) {
    switch (t) {
        case Transport::WSS: return "WSS";
        case Transport::BLE: return "BLE";
        case Transport::EXECUTOR: return "EXECUTOR";
    }
    return "?";
}

std::string_view transport_lower(Transport t) {
    switch (t) {
        case Transport::WSS: return "wss";
        case Transport::BLE: return "ble";
        case Transport::EXECUTOR: return "executor";
    }
    return "?";
}

// Append src to a bounded char buffer; returns new length.
int append_bounded(std::span<char> dst, int len, std::string_view src) {
    for (char c : src) {
        if (len >= static_cast<int>(dst.size())) break;
        dst[static_cast<size_t>(len)] = c;
        len++;
    }
    return len;
}

int append_number(std::span<char> dst, int len, int64_t value) {
    std::array<char, 24> buf{};
    auto [ptr, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), value);
    if (ec != std::errc{}) return len;
    return append_bounded(dst, len,
                          std::string_view(buf.data(),
                                           static_cast<size_t>(ptr - buf.data())));
}

}  // namespace

Feedback feedback_from_gpio(bool nc_high, bool no_high) {
    // safety_model.py Feedback.from_gpio:
    //   (False, True)  -> BYPASS
    //   (True, False)  -> EMULATE
    //   (False, False) -> BOTH_CLOSED
    //   (True, True)   -> BOTH_OPEN
    if (!nc_high && no_high) return Feedback::BYPASS;
    if (nc_high && !no_high) return Feedback::EMULATE;
    if (!nc_high && !no_high) return Feedback::BOTH_CLOSED;
    return Feedback::BOTH_OPEN;
}

// --- event ring ---------------------------------------------------------

void SafetyController::push_event(std::string_view text) {
    auto& slot = events_.at(static_cast<size_t>(
        event_total_ % static_cast<uint64_t>(EVENT_CAPACITY)));
    int len = append_bounded(std::span<char>(slot.data(), EVENT_MAX_LEN), 0, text);
    slot.at(static_cast<size_t>(len)) = '\0';
    event_total_++;
}

void SafetyController::push_event2(std::string_view prefix,
                                   std::string_view reason) {
    std::array<char, EVENT_MAX_LEN + 1> buf{};
    int len = 0;
    len = append_bounded(std::span<char>(buf.data(), EVENT_MAX_LEN), len, prefix);
    len = append_bounded(std::span<char>(buf.data(), EVENT_MAX_LEN), len, reason);
    buf.at(static_cast<size_t>(len)) = '\0';
    push_event(std::string_view(buf.data(), static_cast<size_t>(len)));
}

void SafetyController::push_connection_event(
    std::string_view prefix, const ConnectionIdentity& connection) {
    // Model format: f"{prefix}:{transport}:{handle}:{generation}"
    std::array<char, EVENT_MAX_LEN + 1> buf{};
    std::span<char> dst(buf.data(), EVENT_MAX_LEN);
    int len = 0;
    len = append_bounded(dst, len, prefix);
    len = append_bounded(dst, len, ":");
    len = append_bounded(dst, len, transport_upper(connection.transport));
    len = append_bounded(dst, len, ":");
    len = append_number(dst, len, connection.handle);
    len = append_bounded(dst, len, ":");
    len = append_number(dst, len, connection.generation);
    buf.at(static_cast<size_t>(len)) = '\0';
    push_event(std::string_view(buf.data(), static_cast<size_t>(len)));
}

std::string_view SafetyController::event_at(uint64_t index) const {
    if (index >= event_total_) return {};
    if (event_total_ - index > static_cast<uint64_t>(EVENT_CAPACITY)) {
        return {};  // evicted
    }
    const auto& slot = events_.at(static_cast<size_t>(
        index % static_cast<uint64_t>(EVENT_CAPACITY)));
    return std::string_view(slot.data());
}

// --- connection bookkeeping --------------------------------------------

int64_t SafetyController::highest_generation_for(
    const ConnectionIdentity& c) const {
    for (int i = 0; i < generation_count_; i++) {
        const auto& e = generations_.at(static_cast<size_t>(i));
        if (e.transport == c.transport && e.handle == c.handle) return e.highest;
    }
    return -1;
}

bool SafetyController::set_highest_generation(const ConnectionIdentity& c) {
    for (int i = 0; i < generation_count_; i++) {
        auto& e = generations_.at(static_cast<size_t>(i));
        if (e.transport == c.transport && e.handle == c.handle) {
            e.highest = c.generation;
            return true;
        }
    }
    if (generation_count_ >= MAX_TRACKED_GENERATIONS) return false;
    generations_.at(static_cast<size_t>(generation_count_)) =
        GenerationEntry{c.transport, c.handle, c.generation};
    generation_count_++;
    return true;
}

bool SafetyController::is_active(const ConnectionIdentity& c) const {
    for (int i = 0; i < active_count_; i++) {
        if (active_.at(static_cast<size_t>(i)) == c) return true;
    }
    return false;
}

void SafetyController::remove_active_same_connection(
    const ConnectionIdentity& c) {
    int w = 0;
    for (int i = 0; i < active_count_; i++) {
        const auto& a = active_.at(static_cast<size_t>(i));
        if (!a.same_connection(c)) {
            active_.at(static_cast<size_t>(w)) = a;
            w++;
        }
    }
    active_count_ = w;
}

void SafetyController::remove_active_exact(const ConnectionIdentity& c) {
    int w = 0;
    for (int i = 0; i < active_count_; i++) {
        const auto& a = active_.at(static_cast<size_t>(i));
        if (!(a == c)) {
            active_.at(static_cast<size_t>(w)) = a;
            w++;
        }
    }
    active_count_ = w;
}

// --- public operations --------------------------------------------------

bool SafetyController::connect(const ConnectionIdentity& connection) {
    if (connection.generation < 0) {
        // Model raises in ConnectionIdentity.__post_init__; here invalid
        // identities are rejected at the boundary (no exceptions).
        push_event("connection_rejected:invalid_identity");
        return false;
    }
    int64_t highest = highest_generation_for(connection);
    if (connection.generation <= highest) {
        push_event("connection_rejected:stale_generation");
        return false;
    }
    remove_active_same_connection(connection);
    if (lease_valid_ && lease_owner_.same_connection(connection) &&
        lease_owner_.generation < connection.generation) {
        // Model: self.emergency_stop(reason="owner_superseded", now=0.0)
        emergency_stop("owner_superseded", 0);
    }
    if (active_count_ >= MAX_ACTIVE_CONNECTIONS ||
        !set_highest_generation(connection)) {
        // Fixed-capacity deviation from the unbounded Python model:
        // refusing a connection is fail-safe (no lease can be taken).
        push_event("connection_rejected:capacity");
        return false;
    }
    active_.at(static_cast<size_t>(active_count_)) = connection;
    active_count_++;
    push_connection_event("connected", connection);
    return true;
}

bool SafetyController::acquire(const ConnectionIdentity& connection,
                               int64_t now) {
    enforce_due_safety(now);
    if (lease_valid_) {
        push_event("lease_rejected:already_owned");
        return false;
    }
    if (!is_active(connection)) {
        push_event("lease_rejected:not_connected");
        return false;
    }
    lease_valid_ = true;
    lease_owner_ = connection;
    push_connection_event("lease_acquired", connection);
    return true;
}

bool SafetyController::is_owner(const ConnectionIdentity& connection) const {
    return lease_valid_ && lease_owner_ == connection;
}

bool SafetyController::authorize_owner(const ConnectionIdentity& connection,
                                       int64_t now,
                                       std::string_view ignored_event) {
    if (enforce_due_safety(now)) return false;
    if (!is_owner(connection)) {
        push_event(ignored_event);
        return false;
    }
    return true;
}

bool SafetyController::heartbeat(const ConnectionIdentity& connection,
                                 int64_t now) {
    if (!authorize_owner(connection, now, "ignored_non_owner_heartbeat")) {
        return false;
    }
    push_event("owner_heartbeat");
    return true;
}

bool SafetyController::command_motion(const ConnectionIdentity& connection,
                                      int speed_tenths,
                                      int incline_half_percent, int64_t now) {
    if (!authorize_owner(connection, now, "ignored_non_owner_motion")) {
        return false;
    }
    if (speed_tenths < 0 || speed_tenths > SPEED_MAX_TENTHS) {
        push_event("motion_rejected:speed_range");
        return false;
    }
    if (incline_half_percent < 0 || incline_half_percent > INCLINE_APP_MAX_HALF) {
        push_event("motion_rejected:incline_range");
        return false;
    }
    speed_tenths_ = speed_tenths;
    incline_half_percent_ = incline_half_percent;
    push_event("owner_motion");
    return true;
}

bool SafetyController::disconnect(const ConnectionIdentity& connection,
                                  int64_t now) {
    enforce_due_safety(now);
    remove_active_exact(connection);
    if (!is_owner(connection)) {
        push_event("ignored_non_owner_disconnect");
        return false;
    }
    emergency_stop("owner_disconnect", now);
    return true;
}

bool SafetyController::disconnect_transport(Transport transport, int64_t now) {
    enforce_due_safety(now);
    int w = 0;
    for (int i = 0; i < active_count_; i++) {
        const auto& a = active_.at(static_cast<size_t>(i));
        if (a.transport != transport) {
            active_.at(static_cast<size_t>(w)) = a;
            w++;
        }
    }
    active_count_ = w;
    if (!lease_valid_ || lease_owner_.transport != transport) {
        // Model event is f"ignored_{transport}_drop"
        std::array<char, EVENT_MAX_LEN + 1> buf{};
        std::span<char> dst(buf.data(), EVENT_MAX_LEN);
        int len = 0;
        len = append_bounded(dst, len, "ignored_");
        len = append_bounded(dst, len, transport_lower(transport));
        len = append_bounded(dst, len, "_drop");
        buf.at(static_cast<size_t>(len)) = '\0';
        push_event(std::string_view(buf.data(), static_cast<size_t>(len)));
        return false;
    }
    std::array<char, EVENT_MAX_LEN + 1> buf{};
    std::span<char> dst(buf.data(), EVENT_MAX_LEN);
    int len = 0;
    len = append_bounded(dst, len, transport_lower(transport));
    len = append_bounded(dst, len, "_disconnect");
    emergency_stop(std::string_view(buf.data(), static_cast<size_t>(len)), now);
    return true;
}

int SafetyController::observe_console_bytes(std::span<const uint8_t> data,
                                            int64_t now) {
    if (enforce_due_safety(now)) return 0;
    int complete = 0;
    for (uint8_t byte : data) {
        if (byte == static_cast<uint8_t>('[')) {
            candidate_.at(0) = byte;
            candidate_len_ = 1;
            continue;
        }
        if (candidate_len_ == 0) continue;
        if (byte < 0x20 || byte > 0x7E) {
            candidate_len_ = 0;
            continue;
        }
        candidate_.at(static_cast<size_t>(candidate_len_)) = byte;
        candidate_len_++;
        if (candidate_len_ > 100) {
            candidate_len_ = 0;
            continue;
        }
        if (byte != static_cast<uint8_t>(']')) continue;

        // Full-match of the model's frame pattern:
        //   \[[A-Za-z][A-Za-z0-9_]{0,31}:[\x20-\x7e]{0,64}\]
        // candidate_[0]=='[' and last byte==']' by construction; interior
        // bytes are printable by the scanner above.
        std::string_view content(
            // reinterpret_cast: uint8_t -> char aliasing (standard-allowed)
            reinterpret_cast<const char*>(candidate_.data()) + 1,
            static_cast<size_t>(candidate_len_ - 2));
        candidate_len_ = 0;

        auto colon = content.find(':');
        if (colon == std::string_view::npos) continue;
        std::string_view key = content.substr(0, colon);
        std::string_view value = content.substr(colon + 1);
        if (key.empty() || key.size() > 32) continue;
        char first = key.at(0);
        bool first_alpha = (first >= 'A' && first <= 'Z') ||
                           (first >= 'a' && first <= 'z');
        if (!first_alpha) continue;
        bool key_ok = true;
        for (size_t i = 1; i < key.size(); i++) {
            char c = key.at(i);
            bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                      (c >= '0' && c <= '9') || c == '_';
            if (!ok) { key_ok = false; break; }
        }
        if (!key_ok) continue;
        if (value.size() > 64) continue;
        // Value bytes are printable by construction; the model's value
        // class [\x20-\x7e] adds no further constraint.

        last_frame_at_ = now;
        complete++;
        push_event("complete_console_frame");
    }
    return complete;
}

bool SafetyController::console_is_fresh(int64_t now) const {
    // Model: timestamp is not None and 0.0 <= now - ts < CONSOLE_FRESH.
    // A frame at exact age CONSOLE_FRESH_US is stale.
    if (!last_frame_at_.has_value()) return false;
    int64_t age = now - *last_frame_at_;
    return age >= 0 && age < CONSOLE_FRESH_US;
}

bool SafetyController::request_emulate(const ConnectionIdentity& connection,
                                       int64_t now, bool uart_idle_low) {
    if (!authorize_owner(connection, now, "entry_rejected:not_owner")) {
        return false;
    }
    if (mode_ != SafeMode::PROXY || relay_cmd_ || tx_enable_) {
        push_event("entry_rejected:not_proxy");
        return false;
    }
    if (fault_latched_) {
        push_event("entry_rejected:fault_latched");
        return false;
    }
    if (!tread_ok_) {
        push_event("entry_rejected:tread_not_ok");
        return false;
    }
    if (feedback_ != Feedback::BYPASS) {
        push_event("entry_rejected:feedback_not_bypass");
        return false;
    }
    if (!console_is_fresh(now)) {
        push_event("entry_rejected:console_not_fresh");
        return false;
    }
    if (!uart_idle_low) {
        push_event("entry_rejected:uart_not_idle_low");
        return false;
    }

    speed_tenths_ = 0;
    incline_half_percent_ = 0;
    push_event("command_zero");
    push_event("configure_inverted_uart");
    push_event("verify_physical_idle_low");
    push_event("tx_enable_on");
    push_event("wait_entry_gap");
    tx_enable_ = true;
    mode_ = SafeMode::ENTRY_WAIT_GAP;
    phase_deadline_ = now + TRANSFER_GAP_DEADLINE_US;
    feedback_candidate_since_.reset();
    return true;
}

bool SafetyController::observe_interframe_gap(int64_t now) {
    if (enforce_due_safety(now)) return false;
    if (!phase_deadline_.has_value()) return false;
    if (mode_ == SafeMode::ENTRY_WAIT_GAP) {
        if (feedback_ != Feedback::BYPASS) {
            fault_latched_ = true;
            emergency_stop("entry_feedback_changed_before_transfer", now);
            return false;
        }
        relay_cmd_ = true;
        mode_ = SafeMode::ENTRY_WAIT_FEEDBACK;
        phase_deadline_ = now + RELAY_FEEDBACK_DEADLINE_US;
        feedback_candidate_since_.reset();
        push_event("relay_cmd_on");
        return true;
    }
    if (mode_ == SafeMode::EXIT_WAIT_GAP) {
        if (feedback_ != Feedback::EMULATE) {
            fault_latched_ = true;
            emergency_stop("exit_feedback_changed_before_transfer", now);
            return false;
        }
        relay_cmd_ = false;
        mode_ = SafeMode::EXIT_WAIT_FEEDBACK;
        phase_deadline_ = now + RELAY_FEEDBACK_DEADLINE_US;
        feedback_candidate_since_.reset();
        push_event("relay_cmd_off");
        return true;
    }
    return false;
}

std::optional<Feedback> SafetyController::feedback_expected() const {
    if (mode_ == SafeMode::ENTRY_WAIT_FEEDBACK) return Feedback::EMULATE;
    if (mode_ == SafeMode::EXIT_WAIT_FEEDBACK) return Feedback::BYPASS;
    return std::nullopt;
}

void SafetyController::finish_feedback_transfer() {
    if (mode_ == SafeMode::ENTRY_WAIT_FEEDBACK) {
        mode_ = SafeMode::EMULATING;
        phase_deadline_.reset();
        feedback_candidate_since_.reset();
        push_event("feedback_emulate_stable");
        push_event("send_first_complete_zero_frame");
    } else if (mode_ == SafeMode::EXIT_WAIT_FEEDBACK) {
        mode_ = SafeMode::PROXY;
        phase_deadline_.reset();
        feedback_candidate_since_.reset();
        push_event("feedback_bypass_stable");
        push_event("tx_enable_off");
        tx_enable_ = false;
        release_lease(true);
    }
}

bool SafetyController::qualify_feedback(int64_t now) {
    auto expected = feedback_expected();
    if (!expected.has_value() || !phase_deadline_.has_value() ||
        !feedback_candidate_since_.has_value()) {
        return false;
    }
    // D4: qualification_time <= now AND qualification_time < deadline —
    // a sample at the exact 10 ms deadline fails closed, and a timer tick
    // alone never reaches here (only observe_relay_feedback samples do).
    int64_t qualification_time =
        *feedback_candidate_since_ + RELAY_FEEDBACK_STABLE_US;
    if (feedback_ == *expected && qualification_time <= now &&
        qualification_time < *phase_deadline_) {
        finish_feedback_transfer();
        return true;
    }
    return false;
}

Feedback SafetyController::observe_relay_feedback(bool nc_high, bool no_high,
                                                  int64_t now) {
    enforce_due_safety(now);
    Feedback feedback = feedback_from_gpio(nc_high, no_high);
    feedback_ = feedback;
    if (feedback == Feedback::BOTH_CLOSED) {
        fault_latched_ = true;
        emergency_stop("relay_feedback_both_closed", now);
        return feedback;
    }

    auto expected = feedback_expected();
    if (expected.has_value()) {
        if (feedback == *expected) {
            if (!feedback_candidate_since_.has_value()) {
                feedback_candidate_since_ = now;
                push_event("feedback_candidate");
            }
            qualify_feedback(now);
        } else {
            feedback_candidate_since_.reset();
            push_event("feedback_transition");
        }
    } else if (mode_ == SafeMode::ENTRY_WAIT_GAP &&
               feedback != Feedback::BYPASS) {
        fault_latched_ = true;
        emergency_stop("entry_feedback_changed_before_gap", now);
    } else if (mode_ == SafeMode::EXIT_WAIT_GAP &&
               feedback != Feedback::EMULATE) {
        fault_latched_ = true;
        emergency_stop("exit_feedback_changed_before_gap", now);
    } else if (mode_ == SafeMode::EMULATING && feedback != Feedback::EMULATE) {
        fault_latched_ = true;
        emergency_stop("relay_feedback_invalid", now);
    } else if (mode_ == SafeMode::PROXY && feedback != Feedback::BYPASS) {
        fault_latched_ = true;
        push_event("proxy_feedback_invalid");
    }
    return feedback;
}

bool SafetyController::request_normal_exit(
    const ConnectionIdentity& connection, int64_t now) {
    if (!authorize_owner(connection, now, "exit_rejected:not_owner")) {
        return false;
    }
    if (mode_ != SafeMode::EMULATING) {
        push_event("exit_rejected:not_emulating");
        return false;
    }
    push_event("send_and_finish_complete_zero_frame");
    push_event("wait_exit_gap");
    speed_tenths_ = 0;
    incline_half_percent_ = 0;
    mode_ = SafeMode::EXIT_WAIT_GAP;
    phase_deadline_ = now + TRANSFER_GAP_DEADLINE_US;
    feedback_candidate_since_.reset();
    return true;
}

void SafetyController::set_tread_ok(bool value, int64_t now) {
    enforce_due_safety(now);
    tread_ok_ = value;
    if (!tread_ok_ &&
        (mode_ != SafeMode::PROXY || relay_cmd_ || tx_enable_)) {
        emergency_stop("tread_not_ok", now);
    }
}

void SafetyController::set_vbus_present_n(bool level_high) {
    // Active-low GPIO7 semantics: LOW means VBUS present.
    usb_pullup_enabled_ = !level_high;
    push_event(usb_pullup_enabled_ ? "usb_attach" : "usb_detach");
}

void SafetyController::tick(int64_t now) { enforce_due_safety(now); }

bool SafetyController::enforce_due_safety(int64_t now) {
    // Model: advance every due safety deadline before accepting timed input.
    if (mode_ != SafeMode::PROXY) {
        if (!tread_ok_) {
            emergency_stop("tread_not_ok", now);
            return true;
        }
        if (!console_is_fresh(now)) {
            emergency_stop("console_stale", now);
            return true;
        }
    }
    // D4: a deadline is due when now >= deadline (exact deadline loses).
    if (!phase_deadline_.has_value() || now < *phase_deadline_) return false;
    switch (mode_) {
        case SafeMode::ENTRY_WAIT_GAP:
            emergency_stop("entry_no_gap", now);
            push_event("entry_abort:no_gap");
            return true;
        case SafeMode::ENTRY_WAIT_FEEDBACK:
            fault_latched_ = true;
            emergency_stop("entry_feedback_timeout", now);
            return true;
        case SafeMode::EXIT_WAIT_GAP:
            // At the exit-gap deadline, deassert RELAY_CMD immediately;
            // remaining in Emulate is less safe (PLAN normative).
            push_event("exit_gap_timeout");
            relay_cmd_ = false;
            mode_ = SafeMode::EXIT_WAIT_FEEDBACK;
            phase_deadline_ = now + RELAY_FEEDBACK_DEADLINE_US;
            feedback_candidate_since_.reset();
            push_event("relay_cmd_off");
            return false;
        case SafeMode::EXIT_WAIT_FEEDBACK:
            fault_latched_ = true;
            emergency_stop("exit_feedback_timeout", now);
            return true;
        default:
            return false;
    }
}

void SafetyController::release_lease(bool log) {
    lease_valid_ = false;
    if (log) push_event("lease_released");
}

void SafetyController::safety_timeout_zero_motion(int64_t now) {
    (void)now;  // Monotonic time supplied for call-site parity and audit.
    if (speed_tenths_ == 0 && incline_half_percent_ == 0) {
        return;
    }
    speed_tenths_ = 0;
    incline_half_percent_ = 0;
    push_event("safety_timeout_zero_motion");
}

void SafetyController::emergency_stop(std::string_view reason, int64_t now) {
    (void)now;  // The caller supplies monotonic time for parity and audit.
    speed_tenths_ = 0;
    incline_half_percent_ = 0;
    relay_cmd_ = false;
    tx_enable_ = false;
    mode_ = SafeMode::PROXY;
    phase_deadline_.reset();
    feedback_candidate_since_.reset();
    release_lease(false);
    push_event2("emergency:", reason);
}

void SafetyController::watchdog_stall(int64_t now) {
    reset_class_stop("watchdog", now);
}

void SafetyController::reset(int64_t now, std::string_view reason) {
    reset_class_stop(reason, now);
}

void SafetyController::reset_class_stop(std::string_view reason, int64_t now) {
    emergency_stop(reason, now);
    active_count_ = 0;
    candidate_len_ = 0;
    last_frame_at_.reset();
    feedback_ = Feedback::UNKNOWN;
    usb_pullup_enabled_ = false;
}

}  // namespace esp32tap::safety
