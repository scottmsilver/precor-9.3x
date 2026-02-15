/*
 * ring_buffer.h — Thread-safe circular message buffer
 *
 * Decouples GPIO read threads (producers) from the IPC thread (consumer).
 * Each entry is a fixed-size char buffer. If a consumer falls behind,
 * oldest messages are silently dropped — producers never block.
 */

#pragma once

#include <cstring>
#include <cstdio>
#include <mutex>

template <int Size = 2048, int MsgSize = 256>
class RingBuffer {
public:
    RingBuffer() {
        std::memset(msgs_, 0, sizeof(msgs_));
    }

    // Push a message into the ring. Thread-safe.
    void push(const char* msg) {
        std::lock_guard<std::mutex> lk(mu_);
        std::snprintf(msgs_[head_], MsgSize, "%s", msg);
        head_ = (head_ + 1) % Size;
        count_++;
    }

    // Snapshot of ring state for drain operations
    struct Snapshot {
        int head;
        unsigned int count;
    };

    Snapshot snapshot() const {
        std::lock_guard<std::mutex> lk(mu_);
        return { head_, count_ };
    }

    // Access a message by ring index (caller must hold no lock; message
    // content may be stale if ring wraps — acceptable for best-effort IPC)
    const char* at(int idx) const { return msgs_[idx % Size]; }

    static constexpr int size() { return Size; }
    static constexpr int msg_size() { return MsgSize; }

private:
    char msgs_[Size][MsgSize]{};
    int head_ = 0;
    unsigned int count_ = 0;
    mutable std::mutex mu_;
};
