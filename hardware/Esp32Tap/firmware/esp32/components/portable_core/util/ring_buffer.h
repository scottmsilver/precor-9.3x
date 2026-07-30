/*
 * ring_buffer.h — Thread-safe circular message buffer
 *
 * Decouples GPIO read threads (producers) from the IPC thread (consumer).
 * Each entry is a fixed-size char buffer. If a consumer falls behind,
 * oldest messages are silently dropped — producers never block.
 */

#pragma once

#include <string>
#include <string_view>
#include <array>
#include <mutex>
#include <algorithm>

template <int Size = 2048, int MsgSize = 256>
class RingBuffer {
public:
    RingBuffer() = default;

    // Push a message into the ring. Thread-safe.
    void push(std::string_view msg) {
        std::lock_guard<std::mutex> lk(mu_);
        auto& slot = msgs_.at(head_);
        auto copy_len = std::min(static_cast<int>(msg.size()), MsgSize - 1);
        msg.copy(slot.data(), copy_len);
        slot.at(copy_len) = '\0';
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

    // Access a message by ring index. Returns a copy under the lock to
    // prevent reading torn data if the producer wraps the ring concurrently.
    std::string at(int idx) const {
        std::lock_guard<std::mutex> lk(mu_);
        // Safe modulo: C++ % can be negative for negative dividends
        int mod = idx % Size;
        if (mod < 0) mod += Size;
        return std::string(msgs_.at(mod).data());
    }

    static constexpr int size() { return Size; }
    static constexpr int msg_size() { return MsgSize; }

private:
    std::array<std::array<char, MsgSize>, Size> msgs_{};
    int head_ = 0;
    unsigned int count_ = 0;
    mutable std::mutex mu_;
};
