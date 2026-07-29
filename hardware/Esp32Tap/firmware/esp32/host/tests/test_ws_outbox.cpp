/*
 * test_ws_outbox.cpp — the WS transmit buffer's two load-bearing
 * properties: it is bounded in BYTES (not items) on a no-PSRAM part,
 * and a newer whole-state snapshot can never be dropped in favour of a
 * stale one.
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <string>

#include "ws_outbox.h"

using namespace esp32tap::api;

namespace {

WsOutItem frame(WsKind kind, std::string json) {
    WsOutItem it;
    it.kind = kind;
    it.json = std::move(json);
    return it;
}

}  // namespace

TEST_CASE("outbox is bounded in BYTES, not item count") {
    // A worst-case program frame is ~7 KB and ProgramState broadcasts
    // one every second while running, so an item-count bound of 16 is
    // really a ~115 KB bound — on the part whose stated binding
    // constraint is heap.
    WsOutbox box;
    const std::string big(6 * 1024, 'x');
    for (int i = 0; i < 40; i++) {
        box.post(frame(WsKind::KV, big));
        CHECK(box.bytes() <= WsOutbox::MAX_BYTES + big.size());
    }
    CHECK(box.size() <= WsOutbox::MAX_ITEMS);

    WsOutItem out;
    size_t total = 0;
    while (box.take(out)) total += out.json.size();
    CHECK(total <= WsOutbox::MAX_BYTES + big.size());
    CHECK(box.bytes() == 0);
}

TEST_CASE("snapshot frames coalesce: the newest state always wins") {
    // status is broadcast ONLY on state change, never periodically. If
    // the frame carrying an EMULATING->PROXY transition were dropped,
    // the app would render a stale belt state indefinitely (it has no
    // polling fallback and only reconnects on close/failure).
    WsOutbox box;
    box.post(frame(WsKind::STATUS, "status-old"));
    box.post(frame(WsKind::SESSION, "session-1"));
    box.post(frame(WsKind::STATUS, "status-NEW"));
    CHECK(box.size() == 2);  // superseded in place, no growth

    WsOutItem out;
    REQUIRE(box.take(out));
    CHECK(out.kind == WsKind::STATUS);
    CHECK(out.json == "status-NEW");   // newest state
    CHECK(box.take(out));
    CHECK(out.json == "session-1");    // FIFO position preserved
    CHECK(box.take(out) == false);
}

TEST_CASE("pressure evicts incremental frames before state snapshots") {
    WsOutbox box;
    box.post(frame(WsKind::STATUS, "the-latest-status"));
    const std::string big(4 * 1024, 'k');
    for (int i = 0; i < 30; i++) box.post(frame(WsKind::KV, big));

    bool saw_status = false;
    WsOutItem out;
    while (box.take(out)) {
        if (out.kind == WsKind::STATUS) {
            saw_status = true;
            CHECK(out.json == "the-latest-status");
        }
    }
    CHECK(saw_status);
}

TEST_CASE("a hello keeps its target identity through the queue") {
    WsOutbox box;
    WsOutItem hello;
    hello.kind = WsKind::HELLO;
    hello.fd = 42;
    hello.session = 7;
    hello.frames = {"status", "program"};
    box.post(std::move(hello));

    WsOutItem out;
    REQUIRE(box.take(out));
    CHECK(out.kind == WsKind::HELLO);
    CHECK(out.fd == 42);
    CHECK(out.session == 7u);
    CHECK(out.frames.size() == 2);
}

TEST_CASE("hellos are never coalesced with each other") {
    // Two clients connecting back to back must both get their own
    // ordered triple-send.
    WsOutbox box;
    for (int fd = 1; fd <= 2; fd++) {
        WsOutItem h;
        h.kind = WsKind::HELLO;
        h.fd = fd;
        h.session = static_cast<uint32_t>(fd);
        h.frames = {"status"};
        box.post(std::move(h));
    }
    CHECK(box.size() == 2);
}
