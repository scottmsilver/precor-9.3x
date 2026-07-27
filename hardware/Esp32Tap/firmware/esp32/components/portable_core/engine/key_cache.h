/*
 * key_cache.h — last-seen console hmph/inc values for auto-proxy
 * detection (console-takeover). Mirrors the Pi controller's per-key
 * change tracking that feeds ModeStateMachine::auto_proxy_on_console_change.
 *
 * New portable file (no single cpp/ origin): extracted from the ESP32
 * serial engine task glue so the exchange semantics are host-testable.
 *
 * Lifetime contract: exchange() copies the PREVIOUS value into a
 * caller-owned buffer and returns a view over THAT buffer — never over
 * internal storage (which the same call overwrites) and never over a
 * local. Regression guard: an earlier version returned a view of a
 * function-local std::array — a dangling view in the normative
 * console-takeover safety path (see test_key_cache.cpp).
 */

#pragma once

#include <array>
#include <cstddef>
#include <string_view>

#include "protocol/kv_protocol.h"

namespace esp32tap {

class KeyCache {
public:
    // Copies the previous value for `key` (empty string if none seen yet)
    // into `prev_out` and stores `value` (truncated to KV_FIELD_SIZE - 1
    // bytes — same cap as KvPair fields). Returns a view into `prev_out`,
    // valid for as long as the caller keeps `prev_out` alive. Keys other
    // than hmph/inc are not tracked: they leave the cache untouched and
    // return an empty view.
    std::string_view exchange(std::string_view key, std::string_view value,
                              std::array<char, KV_FIELD_SIZE>& prev_out) {
        std::array<char, KV_FIELD_SIZE>* slot = nullptr;
        if (key == "hmph") {
            slot = &hmph_;
        } else if (key == "inc") {
            slot = &inc_;
        }
        if (slot == nullptr) {
            prev_out.at(0) = '\0';
            return {};
        }
        prev_out = *slot;
        constexpr size_t kMax = static_cast<size_t>(KV_FIELD_SIZE) - 1;
        size_t n = value.size() < kMax ? value.size() : kMax;
        value.copy(slot->data(), n);
        slot->at(n) = '\0';
        return std::string_view(prev_out.data());
    }

private:
    std::array<char, KV_FIELD_SIZE> hmph_{};
    std::array<char, KV_FIELD_SIZE> inc_{};
};

}  // namespace esp32tap
