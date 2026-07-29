/*
 * persist_queue.h — the buffer between the API/executor thread (which
 * must never block on flash) and the core-1 storage task (which does
 * the LittleFS writes).
 *
 * WHY IT COALESCES BY PATH. The obvious design — a FIFO of (path,
 * content) items, drop when full — loses data permanently for any
 * WRITE-ONCE store. program_history.json is written exactly once per
 * "load a workout" (add_history), so if that single item is dropped
 * because the queue happened to be full of run-checkpoint and
 * saved-workout writes, the history entry is gone forever: nothing ever
 * rewrites that file. The comment "next save rewrites the file" is only
 * true for stores that are saved repeatedly.
 *
 * Here every store gets its OWN slot holding the latest serialization
 * of that file. A second write to the same path overwrites the pending
 * content instead of consuming another slot, which means:
 *   - a write is never dropped in favour of an unrelated store's write;
 *   - the resident cost is bounded by (number of store files) x (store
 *     byte cap), not by queue depth x byte cap;
 *   - superseding is lossless: the newer serialization of a whole-file
 *     store already contains everything the older one did.
 *
 * Pure/portable (no FreeRTOS): the device wraps it in a mutex plus a
 * wake-token queue (server_context.h), host tests drive it directly.
 */

#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>

namespace esp32tap::storage {

class PersistQueue {
public:
    // One slot per store file. The device has five
    // (program_history / saved_workouts / run_history / profiles /
    // profile_state); the slack absorbs any future store without
    // reintroducing the drop path.
    static constexpr int MAX_SLOTS = 8;

    enum class Stage {
        QUEUED_NEW,  // slot went pending: the caller must post a wake
        COALESCED,   // superseded an already-pending write to this path
        DROPPED,     // no slot free — more distinct paths than MAX_SLOTS
    };

    Stage stage(std::string_view path, std::string&& content) {
        // (1) already pending for this path -> supersede in place.
        for (auto& s : slots_) {
            if (s.pending && s.path == path) {
                s.content = std::move(content);
                return Stage::COALESCED;
            }
        }
        // (2) prefer the slot that already names this path (keeps the
        //     path->slot mapping stable, so a store's string buffer is
        //     reused instead of reallocated on every save).
        for (auto& s : slots_) {
            if (!s.pending && s.path == path) {
                s.content = std::move(content);
                s.pending = true;
                s.order = next_order_++;
                return Stage::QUEUED_NEW;
            }
        }
        // (3) any free slot.
        for (auto& s : slots_) {
            if (!s.pending && s.path.empty()) {
                s.path.assign(path);
                s.content = std::move(content);
                s.pending = true;
                s.order = next_order_++;
                return Stage::QUEUED_NEW;
            }
        }
        // (4) recycle a settled slot belonging to another path.
        for (auto& s : slots_) {
            if (!s.pending) {
                s.path.assign(path);
                s.content = std::move(content);
                s.pending = true;
                s.order = next_order_++;
                return Stage::QUEUED_NEW;
            }
        }
        return Stage::DROPPED;
    }

    // Oldest pending write (FIFO across paths). False when idle.
    bool take(std::string& path, std::string& content) {
        Slot* best = nullptr;
        for (auto& s : slots_) {
            if (!s.pending) continue;
            if (best == nullptr || s.order < best->order) best = &s;
        }
        if (best == nullptr) return false;
        path = best->path;
        content = std::move(best->content);
        best->content.clear();
        best->content.shrink_to_fit();
        best->pending = false;
        return true;
    }

    int pending() const {
        int n = 0;
        for (const auto& s : slots_) {
            if (s.pending) n++;
        }
        return n;
    }

private:
    struct Slot {
        std::string path;
        std::string content;
        bool pending = false;
        uint64_t order = 0;
    };
    std::array<Slot, MAX_SLOTS> slots_{};
    uint64_t next_order_ = 1;
};

}  // namespace esp32tap::storage
