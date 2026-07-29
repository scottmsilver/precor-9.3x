/*
 * ws_outbox.h — the bounded, coalescing buffer between the producers of
 * WebSocket frames (the WDT-supervised interval executor, which must
 * never touch lwIP) and the single httpd worker that actually sends
 * them.
 *
 * Two properties the transport depends on, both testable on the host:
 *
 *  (1) BOUNDED IN BYTES, not items. A worst-case program frame is ~7 KB
 *      (64 intervals x 48-char names) and ProgramState broadcasts one
 *      every second while running, so a queue bounded at "16 items"
 *      is really bounded at ~115 KB — on a part whose stated binding
 *      constraint is heap. The budget here is in bytes.
 *
 *  (2) COALESCED BY KIND. status / session / program frames are WHOLE
 *      SNAPSHOTS of their state: a newer one strictly supersedes a
 *      queued older one. Coalescing them (a) stops them accumulating at
 *      all and (b) removes the failure the naive drop-on-full had —
 *      status is broadcast ONLY on state change, never periodically, so
 *      dropping the frame that carried an EMULATING->PROXY transition
 *      left the app rendering a stale belt state indefinitely. A newer
 *      snapshot now replaces the older one in place (keeping its FIFO
 *      position), and eviction prefers incremental frames (kv) over
 *      snapshots, so the latest state can never be dropped in favour of
 *      a stale one.
 *
 * Pure/portable: the device wraps it in a mutex (transport_httpd.cpp);
 * host tests drive it directly.
 */

#pragma once

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

namespace esp32tap::api {

// What a queued frame IS, which decides whether a newer one supersedes
// it and how eagerly it may be evicted under pressure.
enum class WsKind {
    OTHER,    // anything not classified (never coalesced)
    STATUS,   // whole-state snapshot: newest wins
    SESSION,  // whole-state snapshot: newest wins
    PROGRAM,  // whole-state snapshot: newest wins
    KV,       // incremental log line: evicted first
    HELLO,    // per-client ordered triple-send (has a target fd)
};

inline bool ws_kind_is_snapshot(WsKind k) {
    return k == WsKind::STATUS || k == WsKind::SESSION ||
           k == WsKind::PROGRAM;
}

struct WsOutItem {
    WsKind kind = WsKind::OTHER;
    int fd = -1;               // HELLO only: target socket
    uint32_t session = 0;      // HELLO only: fd-reuse guard
    std::string json;          // broadcast payload
    std::vector<std::string> frames;  // HELLO payloads

    size_t bytes() const {
        size_t n = json.size();
        for (const std::string& f : frames) n += f.size();
        return n + 32;  // per-item bookkeeping
    }
};

class WsOutbox {
public:
    // Total queued payload budget. Sized to absorb one full 1 Hz burst
    // (status + session + program + a handful of kv frames) even when
    // the program frame is worst-case, without ever becoming a
    // six-figure allocation.
    static constexpr size_t MAX_BYTES = 24 * 1024;
    // Belt-and-braces item ceiling (kv frames are small; without this a
    // pathological stream of tiny frames would still cost a lot of
    // std::string headers).
    static constexpr int MAX_ITEMS = 24;

    // Enqueue. Returns false if the item itself could not be kept.
    bool post(WsOutItem&& item) {
        if (ws_kind_is_snapshot(item.kind)) {
            for (auto& q : items_) {
                if (q.kind == item.kind) {
                    bytes_ -= q.bytes();
                    q.json = std::move(item.json);
                    bytes_ += q.bytes();
                    return true;  // superseded in place, FIFO slot kept
                }
            }
        }
        bytes_ += item.bytes();
        items_.push_back(std::move(item));
        return evict_to_budget();
    }

    // FIFO. False when empty.
    bool take(WsOutItem& out) {
        if (items_.empty()) return false;
        out = std::move(items_.front());
        items_.erase(items_.begin());
        size_t b = out.bytes();
        bytes_ = b > bytes_ ? 0 : bytes_ - b;
        return true;
    }

    bool empty() const { return items_.empty(); }
    int size() const { return static_cast<int>(items_.size()); }
    size_t bytes() const { return bytes_; }

private:
    // Evict until the budget holds. Preference order: oldest KV/OTHER
    // first (incremental data the app can miss), then HELLO, and only
    // then a snapshot — and never the item just posted (index back()).
    bool evict_to_budget() {
        while ((bytes_ > MAX_BYTES ||
                static_cast<int>(items_.size()) > MAX_ITEMS) &&
               items_.size() > 1) {
            size_t victim = items_.size();  // none
            for (int pass = 0; pass < 3 && victim == items_.size(); pass++) {
                for (size_t i = 0; i + 1 < items_.size(); i++) {
                    WsKind k = items_[i].kind;
                    bool match = (pass == 0 && (k == WsKind::KV ||
                                                k == WsKind::OTHER)) ||
                                 (pass == 1 && k == WsKind::HELLO) ||
                                 (pass == 2);
                    if (match) {
                        victim = i;
                        break;
                    }
                }
            }
            if (victim == items_.size()) break;
            size_t b = items_[victim].bytes();
            bytes_ = b > bytes_ ? 0 : bytes_ - b;
            items_.erase(items_.begin() + static_cast<long>(victim));
        }
        // A single item larger than the whole budget is still delivered
        // (dropping the only frame would be worse than one transient
        // over-budget allocation that is already bounded upstream by
        // MAX_INTERVALS).
        return true;
    }

    std::vector<WsOutItem> items_;
    size_t bytes_ = 0;
};

}  // namespace esp32tap::api
