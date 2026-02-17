/*
 * test_ring_buffer.cpp — Tests for RingBuffer template
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "ring_buffer.h"
#include <thread>
#include <cstdio>
#include <string>

TEST_CASE("empty ring buffer") {
    RingBuffer<> ring;
    auto snap = ring.snapshot();
    CHECK(snap.head == 0);
    CHECK(snap.count == 0);
}

TEST_CASE("push and snapshot") {
    RingBuffer<> ring;
    ring.push("hello\n");

    auto snap = ring.snapshot();
    CHECK(snap.head == 1);
    CHECK(snap.count == 1);
    CHECK(ring.at(0) == "hello\n");
}

TEST_CASE("multiple pushes") {
    RingBuffer<> ring;
    ring.push("msg1\n");
    ring.push("msg2\n");
    ring.push("msg3\n");

    auto snap = ring.snapshot();
    CHECK(snap.head == 3);
    CHECK(snap.count == 3);
    CHECK(ring.at(0) == "msg1\n");
    CHECK(ring.at(1) == "msg2\n");
    CHECK(ring.at(2) == "msg3\n");
}

TEST_CASE("wrap-around") {
    RingBuffer<4, 64> ring;  // tiny ring for wrap testing
    ring.push("a\n");
    ring.push("b\n");
    ring.push("c\n");
    ring.push("d\n");  // fills ring
    ring.push("e\n");  // wraps, overwrites "a"

    auto snap = ring.snapshot();
    CHECK(snap.head == 1);  // wrapped around to index 1
    CHECK(snap.count == 5);
    CHECK(ring.at(0) == "e\n");  // index 0 was overwritten
    CHECK(ring.at(1) == "b\n");
}

TEST_CASE("message truncation") {
    RingBuffer<4, 8> ring;
    ring.push("this is a very long message that exceeds the buffer");

    // Should be truncated to 7 chars + null
    auto msg = ring.at(0);
    CHECK(msg.size() <= 7);
}

TEST_CASE("concurrent push and snapshot") {
    RingBuffer<> ring;
    constexpr int N = 1000;

    std::thread writer([&ring]() {
        char msg[64];
        for (int i = 0; i < N; i++) {
            std::snprintf(msg, sizeof(msg), "msg%d\n", i);
            ring.push(msg);
        }
    });

    // Read snapshots concurrently
    int max_count = 0;
    for (int i = 0; i < 100; i++) {
        auto snap = ring.snapshot();
        if (static_cast<int>(snap.count) > max_count) {
            max_count = snap.count;
        }
        // Accessing at() while writing — should not crash
        if (snap.count > 0) {
            (void)ring.at(0);
        }
    }

    writer.join();

    auto final_snap = ring.snapshot();
    CHECK(final_snap.count == N);
}
