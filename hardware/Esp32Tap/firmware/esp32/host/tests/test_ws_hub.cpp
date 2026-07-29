/*
 * test_ws_hub.cpp — WsHub client registry + fan-out semantics.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <string>
#include <vector>

#include "ws_hub.h"

using namespace esp32tap::api;

namespace {

struct RecordingSink : public FrameSink {
    std::vector<std::pair<int, std::string>> sent;
    int dead_client = -1;
    bool send_text(int client_id, std::string_view json) override {
        if (client_id == dead_client) return false;
        sent.emplace_back(client_id, std::string(json));
        return true;
    }
};

}  // namespace

TEST_CASE("registry caps at MAX_CLIENTS and de-registers") {
    WsHub hub;
    CHECK(hub.add_client(10));
    CHECK(hub.add_client(11));
    CHECK_FALSE(hub.add_client(12));  // cap 2 (PLAN concurrency cap)
    CHECK(hub.client_count() == 2);
    CHECK(hub.has_client(10));
    hub.remove_client(10);
    CHECK_FALSE(hub.has_client(10));
    CHECK(hub.add_client(12));
    hub.remove_client(99);  // unknown: no-op
    CHECK(hub.client_count() == 2);
}

TEST_CASE("broadcast fans out and drops dead clients") {
    WsHub hub;
    RecordingSink sink;
    hub.set_sink(&sink);
    hub.add_client(1);
    hub.add_client(2);

    hub.broadcast("{\"type\":\"status\"}");
    REQUIRE(sink.sent.size() == 2);
    CHECK(sink.sent[0].first == 1);
    CHECK(sink.sent[1].first == 2);

    sink.dead_client = 1;
    hub.broadcast("{\"type\":\"session\"}");
    CHECK(hub.client_count() == 1);
    CHECK_FALSE(hub.has_client(1));
    // Client 2 still got the frame
    CHECK(sink.sent.back().first == 2);
    CHECK(sink.sent.back().second == "{\"type\":\"session\"}");

    // No sink: safe no-op
    WsHub bare;
    bare.add_client(5);
    bare.broadcast("x");
}

TEST_CASE("a registered client is NEVER silently skipped forever") {
    // Regression: registration used to happen inside the hello-delivery
    // callback, so a saturated pump left a client that had completed
    // its 101 handshake permanently unregistered — the app reported
    // "connected", received nothing for the life of the socket, never
    // reconnected (it only reconnects on onClosed/onFailure), and the
    // executor's dead-man eventually paused a running program.
    // Registration is now unconditional; the hello only gets a BOUNDED
    // hold on the broadcast stream.
    WsHub hub;
    RecordingSink sink;
    hub.set_sink(&sink);
    REQUIRE(hub.add_client(7, /*session=*/1, WsHub::DEFAULT_HELLO_HOLD));
    CHECK(hub.client_count() == 1);

    // Hello never arrives (dropped outbox item). The hold expires.
    for (int i = 0; i < WsHub::DEFAULT_HELLO_HOLD; i++) {
        hub.broadcast("{\"type\":\"status\"}");
    }
    CHECK(sink.sent.empty());  // held back so far
    hub.broadcast("{\"type\":\"status\"}");
    REQUIRE(sink.sent.size() == 1);
    CHECK(sink.sent[0].first == 7);
}

TEST_CASE("release_hold lets the stream through immediately after hello") {
    WsHub hub;
    RecordingSink sink;
    hub.set_sink(&sink);
    hub.add_client(9, 1, WsHub::DEFAULT_HELLO_HOLD);
    hub.release_hold(9);
    hub.broadcast("{\"type\":\"status\"}");
    REQUIRE(sink.sent.size() == 1);
    CHECK(sink.sent[0].first == 9);
    hub.release_hold(1234);  // unknown fd: no-op
}

TEST_CASE("session identity guards fd reuse") {
    // httpd fds are reused: a client can close and a brand-new accept
    // can land on the SAME fd before a queued hello reaches the httpd
    // task. Delivering the stale hello would send another client's
    // frames and release the wrong client's hold.
    WsHub hub;
    hub.add_client(4, /*session=*/11, WsHub::DEFAULT_HELLO_HOLD);
    CHECK(hub.is_session(4, 11));
    CHECK_FALSE(hub.is_session(4, 10));
    CHECK(hub.session_of(4) == 11u);

    hub.remove_client(4);
    CHECK_FALSE(hub.is_session(4, 11));  // closed: nothing to deliver to
    CHECK(hub.session_of(4) == 0u);

    hub.add_client(4, /*session=*/12, WsHub::DEFAULT_HELLO_HOLD);
    CHECK_FALSE(hub.is_session(4, 11));  // stale hello must not match
    CHECK(hub.is_session(4, 12));
}

TEST_CASE("a dead client is dropped even while another still holds") {
    WsHub hub;
    RecordingSink sink;
    sink.dead_client = 2;
    hub.set_sink(&sink);
    hub.add_client(1, 1, 0);
    hub.add_client(2, 2, 0);
    hub.broadcast("{\"type\":\"status\"}");
    CHECK(hub.client_count() == 1);
    CHECK(hub.has_client(1));
}
