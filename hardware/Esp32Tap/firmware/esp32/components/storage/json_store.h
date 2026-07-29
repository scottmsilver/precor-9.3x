/*
 * json_store.h — shared base for the JSON-array-file stores
 * (program history / saved workouts / run history). Entries live in a
 * rapidjson Document (array, newest-first); mutations serialize the
 * whole array and hand it to a PersistSink (device: bounded queue
 * drained by the core-1 storage task; host tests: direct write).
 */

#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include "rapidjson/document.h"
#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"

#include "fs_api.h"

namespace esp32tap::storage {

class PersistSink {
public:
    virtual ~PersistSink() = default;
    virtual void persist(const std::string& path, std::string&& content) = 0;
};

class DirectPersist : public PersistSink {
public:
    explicit DirectPersist(FsApi& fs) : fs_(fs) {}
    void persist(const std::string& path, std::string&& content) override {
        fs_.write_file_atomic(path, content);
    }

private:
    FsApi& fs_;
};

class JsonArrayStore {
public:
    virtual ~JsonArrayStore() = default;

    // Boot-time load (single-threaded). Malformed/absent file -> empty
    // array (Postel: never brick on corrupt flash data). Elements that
    // fail the derived store's entry_valid() shape check are dropped
    // here so the mutators' operator[] indexing can never hit a missing
    // member (RAPIDJSON_ASSERT -> abort -> boot loop) on a store file
    // written by a different firmware revision.
    void init(FsApi& fs, PersistSink& sink, std::string path) {
        sink_ = &sink;
        path_ = std::move(path);
        doc_.SetArray();
        {
            // HEAP PEAK. This is the largest single allocation the
            // device makes at boot, and it is bounded HERE, not by
            // enforce_byte_cap() (which only runs after parsing). Two
            // rules keep the peak at ~2x the file instead of ~5x on a
            // 512 KB no-PSRAM part:
            //   (1) the read is capped at this store's own byte cap, so
            //       an over-large file from another firmware revision
            //       is refused before it is ever resident;
            //   (2) the file is parsed DIRECTLY into doc_ — the old
            //       parse-then-CopyFrom held a full second document
            //       (rapidjson pool allocators do not free) on top of
            //       the raw text.
            std::string raw;
            if (fs.read_file(path_, raw, max_bytes())) {
                doc_.Parse(raw.c_str(), raw.size());
                if (doc_.HasParseError() || !doc_.IsArray()) doc_.SetArray();
            }
            // raw destructs here: the parsed document never coexists
            // with a second copy of the text.
        }
        for (rapidjson::SizeType i = 0; i < doc_.Size();) {
            if (entry_valid(doc_[i])) {
                i++;
            } else {
                doc_.Erase(doc_.Begin() + i);
            }
        }
        // Resume the ordering sequence above every seq already on
        // flash, so ordering stays correct across reboots even with no
        // wall clock (see next_seq_).
        for (const auto& e : doc_.GetArray()) {
            if (!e.IsObject()) continue;
            auto m = e.FindMember("seq");
            if (m != e.MemberEnd() && m->value.IsInt64() &&
                m->value.GetInt64() >= next_seq_) {
                next_seq_ = m->value.GetInt64() + 1;
            }
        }
        if (evicts_on_overflow()) enforce_byte_cap();
    }

    rapidjson::Document& doc() { return doc_; }
    const rapidjson::Document& doc() const { return doc_; }

    int size() const {
        return static_cast<int>(doc_.GetArray().Size());
    }

    std::string serialize() const {
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        doc_.Accept(w);
        return std::string(sb.GetString(), sb.GetSize());
    }

    void save() {
        if (evicts_on_overflow()) enforce_byte_cap();
        compact_if_bloated();
        if (sink_ != nullptr) sink_->persist(path_, serialize());
    }

    // Whether an over-cap store may drop its oldest entry to fit.
    // False for stores whose entries the USER explicitly asked to keep
    // (saved workouts): those refuse the write instead, so the caller
    // can surface an honest error rather than reporting success while
    // deleting a favorite. See WorkoutStore.
    virtual bool evicts_on_overflow() const { return true; }

    // Monotonic, PERSISTED ordering key. The device wall clock restarts
    // at the epoch on every boot unless SNTP reaches a server, so any
    // ordering derived from the ISO timestamp strings inverts across a
    // reboot. Entries therefore carry "seq": a counter resumed at load
    // from the highest value already on flash, which is monotonic for
    // the life of the store file regardless of the clock.
    int64_t next_seq() { return next_seq_++; }

    // Total resident/serialized bytes this store may occupy. The entry
    // COUNT caps (MAX_HISTORY / MAX_WORKOUTS / MAX_RUNS) do not bound
    // memory on their own: one entry can hold a 64-interval program
    // (~4.5 KB), so 20 entries is ~90 KB resident on a no-PSRAM part —
    // and get_history() copies + serializes the whole thing again.
    // Oldest entries are dropped until the store fits.
    virtual size_t max_bytes() const { return 16 * 1024; }

    // Find an entry by its "id" member; nullptr when absent.
    rapidjson::Value* find_by_id(std::string_view id) {
        for (auto& e : doc_.GetArray()) {
            if (!e.IsObject()) continue;
            auto m = e.FindMember("id");
            if (m != e.MemberEnd() && m->value.IsString() &&
                std::string_view(m->value.GetString(),
                                 m->value.GetStringLength()) == id) {
                return &e;
            }
        }
        return nullptr;
    }

    static std::string make_id(int64_t now_us) {
        static uint32_t counter = 0;
        counter++;
        return std::to_string(now_us) + "-" + std::to_string(counter);
    }

protected:
    // Shape contract an entry must satisfy for the derived store's
    // mutators to be safe. Default: any JSON object.
    virtual bool entry_valid(const rapidjson::Value& e) const {
        return e.IsObject();
    }

    // entry_valid() helpers.
    static bool has_str(const rapidjson::Value& e, const char* k) {
        auto m = e.FindMember(k);
        return m != e.MemberEnd() && m->value.IsString();
    }
    static bool has_num(const rapidjson::Value& e, const char* k) {
        auto m = e.FindMember(k);
        return m != e.MemberEnd() && m->value.IsNumber();
    }
    static bool has_bool(const rapidjson::Value& e, const char* k) {
        auto m = e.FindMember(k);
        return m != e.MemberEnd() && m->value.IsBool();
    }
    static bool has_key(const rapidjson::Value& e, const char* k) {
        return e.FindMember(k) != e.MemberEnd();
    }

    // rapidjson's MemoryPoolAllocator never frees: every mutation that
    // rebuilds the array (push_front_capped, in-place string writes)
    // grows the pool monotonically for the life of the boot. When the
    // pool's dead space exceeds a bounded slack, rebuild the document
    // into a fresh allocator. NOTE: invalidates all Value* into doc_ —
    // callers must not hold entry pointers across save().
    void compact_if_bloated() {
        constexpr size_t SLACK_BYTES = 16 * 1024;
        auto& a = doc_.GetAllocator();
        if (a.Capacity() <= a.Size() + SLACK_BYTES) return;
        rapidjson::Document fresh;
        fresh.CopyFrom(doc_, fresh.GetAllocator());
        doc_ = std::move(fresh);
    }

    // Index of the entry the byte cap should evict first. NOT a fixed
    // "array tail": HistoryStore/WorkoutStore/RunStore are newest-first
    // (push_front_capped) so the tail is the oldest, but ProfileStore
    // appends oldest-first (python ORDER BY created_at) — evicting its
    // tail would delete the profile the user just created while the
    // handler still answered 200. Every store states its own answer.
    virtual rapidjson::SizeType evict_index() const {
        return doc_.Size() - 1;  // newest-first stores: tail == oldest
    }

    // Drop the eviction-ordered entries until the serialized store fits
    // max_bytes(). Always keeps at least one entry: a single oversized
    // entry is already bounded by canonicalize_program (MAX_INTERVALS)
    // and refusing to store the user's newest program would be worse
    // than exceeding the budget.
    //
    // Stores whose entries are USER-OWNED (saved workouts) must not be
    // silently evicted — they override this to refuse the write
    // instead; see WorkoutStore.
    void enforce_byte_cap() {
        const size_t cap = max_bytes();
        while (doc_.Size() > 1 && serialize().size() > cap) {
            doc_.Erase(doc_.Begin() + evict_index());
        }
    }

    // True when the store currently fits its byte cap. Used by stores
    // that REFUSE an over-cap write rather than evicting for it.
    bool fits_byte_cap() const { return serialize().size() <= max_bytes(); }

    // Stamp the monotonic ordering key on a new entry.
    void stamp_seq(rapidjson::Value& entry) {
        entry.AddMember("seq", next_seq(), doc_.GetAllocator());
    }

    // Insert at the front (newest-first) and enforce the cap. Elements
    // are MOVED (rapidjson operator= steals; same allocator throughout),
    // so this is O(n) pointer shuffling, not deep copies.
    void push_front_capped(rapidjson::Value&& entry, int cap) {
        auto& a = doc_.GetAllocator();
        rapidjson::Value arr(rapidjson::kArrayType);
        arr.PushBack(entry, a);
        for (auto& e : doc_.GetArray()) {
            if (static_cast<int>(arr.Size()) >= cap) break;
            rapidjson::Value moved;
            moved = e;  // move-steal
            arr.PushBack(moved, a);
        }
        doc_.SetArray();
        for (auto& e : arr.GetArray()) {
            rapidjson::Value moved;
            moved = e;
            doc_.PushBack(moved, doc_.GetAllocator());
        }
    }

    PersistSink* sink_ = nullptr;
    std::string path_;
    rapidjson::Document doc_;
    int64_t next_seq_ = 1;
};

// server.py parity helper: sum of interval durations from a program
// JSON value (0 for malformed input).
inline int program_total_duration(const rapidjson::Value& program) {
    if (!program.IsObject()) return 0;
    auto ivs = program.FindMember("intervals");
    if (ivs == program.MemberEnd() || !ivs->value.IsArray()) return 0;
    int total = 0;
    for (const auto& iv : ivs->value.GetArray()) {
        if (!iv.IsObject()) continue;
        auto d = iv.FindMember("duration");
        if (d != iv.MemberEnd() && d->value.IsNumber()) {
            // Clamp in the double domain before the int conversion:
            // out-of-range double->int is UB, and this can be
            // attacker-supplied or stale-flash JSON.
            double dur = d->value.GetDouble();
            if (!(dur >= 0.0)) dur = 0.0;  // NaN/negative -> 0
            if (dur > 86400.0) dur = 86400.0;
            total += static_cast<int>(dur);
        }
    }
    return total;
}

inline std::string program_name_of(const rapidjson::Value& program,
                                   const char* dflt = "Untitled") {
    if (program.IsObject()) {
        auto n = program.FindMember("name");
        if (n != program.MemberEnd() && n->value.IsString()) {
            return std::string(n->value.GetString(),
                               n->value.GetStringLength());
        }
    }
    return dflt;
}

}  // namespace esp32tap::storage
