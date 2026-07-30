/*
 * test_key_cache.cpp — KeyCache exchange semantics + the console-takeover
 * (auto-proxy) wiring that consumes it.
 *
 * Regression coverage for the dangling-string_view bug: exchange() must
 * return a view over the CALLER-owned buffer (valid across the following
 * auto_proxy_on_console_change call), never over a local or over the
 * internal slot it just overwrote.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <array>
#include <string>
#include <string_view>

#include "engine/key_cache.h"
#include "engine/mode_state.h"

using esp32tap::KeyCache;

TEST_CASE("first sighting of a tracked key returns empty and stores it") {
    KeyCache cache;
    std::array<char, KV_FIELD_SIZE> buf;

    std::string_view prev = cache.exchange("hmph", "78", buf);
    CHECK(prev.empty());

    prev = cache.exchange("hmph", "78", buf);
    CHECK(prev == "78");
}

TEST_CASE("exchange returns the previous value in the caller's buffer") {
    KeyCache cache;
    std::array<char, KV_FIELD_SIZE> buf;

    (void)cache.exchange("hmph", "78", buf);
    std::string_view prev = cache.exchange("hmph", "A0", buf);
    CHECK(prev == "78");
    // The view must alias the caller-owned buffer — that is the lifetime
    // contract that keeps it valid across the subsequent auto-proxy call.
    CHECK(prev.data() == buf.data());

    prev = cache.exchange("hmph", "B4", buf);
    CHECK(prev == "A0");
}

TEST_CASE("hmph and inc are tracked independently") {
    KeyCache cache;
    std::array<char, KV_FIELD_SIZE> buf;

    (void)cache.exchange("hmph", "78", buf);
    (void)cache.exchange("inc", "A", buf);

    CHECK(cache.exchange("hmph", "78", buf) == "78");
    CHECK(cache.exchange("inc", "1E", buf) == "A");
    CHECK(cache.exchange("inc", "1E", buf) == "1E");
}

TEST_CASE("untracked keys return empty and do not disturb the cache") {
    KeyCache cache;
    std::array<char, KV_FIELD_SIZE> buf;

    (void)cache.exchange("hmph", "78", buf);
    CHECK(cache.exchange("belt", "1", buf).empty());
    CHECK(cache.exchange("loop", "5550", buf).empty());
    CHECK(cache.exchange("", "", buf).empty());
    // hmph is untouched by the untracked exchanges above.
    CHECK(cache.exchange("hmph", "79", buf) == "78");
}

TEST_CASE("oversized values are truncated to KV_FIELD_SIZE - 1") {
    KeyCache cache;
    std::array<char, KV_FIELD_SIZE> buf;

    std::string big(200, 'X');
    (void)cache.exchange("hmph", big, buf);
    std::string_view prev = cache.exchange("hmph", "0", buf);
    CHECK(prev.size() == static_cast<size_t>(KV_FIELD_SIZE) - 1);
    CHECK(prev == std::string(static_cast<size_t>(KV_FIELD_SIZE) - 1, 'X'));
}

// Mirrors the serial engine task's on_kv handler wiring: exchange into a
// caller-owned buffer, then feed prev/new into the mode machine. This is
// the normative console-takeover path (console button press while
// emulating must stop emulation).
namespace {

struct TakeoverHarness {
    KeyCache cache;
    ModeStateMachine mode;

    TakeoverHarness() { mode.set_emulate_callback([](bool) {}); }

    TransitionResult feed(std::string_view key, std::string_view value) {
        std::array<char, KV_FIELD_SIZE> prev_buf;
        std::string_view prev = cache.exchange(key, value, prev_buf);
        return mode.auto_proxy_on_console_change(key, prev, value);
    }
};

}  // namespace

TEST_CASE("console takeover: hmph change while emulating stops emulation") {
    TakeoverHarness h;

    // Baseline value observed while still in proxy.
    auto r = h.feed("hmph", "78");
    CHECK_FALSE(r.emulate_stopped);

    h.mode.request_emulate(true);
    REQUIRE(h.mode.is_emulating());

    // Same value repeated: not a button press, stay emulating.
    r = h.feed("hmph", "78");
    CHECK_FALSE(r.emulate_stopped);
    CHECK(h.mode.is_emulating());

    // Value change: console button press — emulation must stop.
    r = h.feed("hmph", "A0");
    CHECK(r.emulate_stopped);
    CHECK_FALSE(h.mode.is_emulating());
}

TEST_CASE("console takeover: inc change while emulating stops emulation") {
    TakeoverHarness h;
    (void)h.feed("inc", "A");
    h.mode.request_emulate(true);
    REQUIRE(h.mode.is_emulating());

    auto r = h.feed("inc", "B");
    CHECK(r.emulate_stopped);
    CHECK_FALSE(h.mode.is_emulating());
}

TEST_CASE("console takeover: first-ever value while emulating does not trigger") {
    TakeoverHarness h;
    h.mode.request_emulate(true);
    REQUIRE(h.mode.is_emulating());

    // No previous value cached: mode_state treats empty old_val as
    // "first value", not a change.
    auto r = h.feed("hmph", "78");
    CHECK_FALSE(r.emulate_stopped);
    CHECK(h.mode.is_emulating());

    // Untracked key changes never trigger either.
    r = h.feed("belt", "1");
    CHECK_FALSE(r.emulate_stopped);
    CHECK(h.mode.is_emulating());
}
