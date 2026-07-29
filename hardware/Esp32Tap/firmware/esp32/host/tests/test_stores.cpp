/*
 * test_stores.cpp — PosixFs atomic writes + history/workout/run store
 * behavior (cap, dedup, order, lifecycle incl. boot_recover).
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS_BUT_WITH_ALL_ASSERTS
#include <doctest.h>

#include <cstdlib>
#include <string>
#include <vector>

#include "rapidjson/document.h"

#include "fs_api.h"
#include "history_store.h"
#include "json_store.h"
#include "profile_store.h"
#include "run_store.h"
#include "persist_queue.h"
#include "workout_store.h"

using namespace esp32tap::storage;

namespace {

std::string make_tmp_dir() {
    std::string tmpl = "/tmp/esp32tap_store_test_XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    char* got = mkdtemp(buf.data());
    REQUIRE(got != nullptr);
    return std::string(got);
}

rapidjson::Document make_program(const char* name, double speed = 3.0) {
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("name", rapidjson::Value(name, a), a);
    rapidjson::Value ivs(rapidjson::kArrayType);
    rapidjson::Value iv(rapidjson::kObjectType);
    iv.AddMember("name", "Seg 1", a);
    iv.AddMember("duration", 600, a);
    iv.AddMember("speed", speed, a);
    iv.AddMember("incline", 0.0, a);
    ivs.PushBack(iv, a);
    d.AddMember("intervals", ivs, a);
    return d;
}

}  // namespace

TEST_CASE("PosixFs atomic write + read + remove") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    CHECK_FALSE(fs.exists("a.json"));
    CHECK(fs.write_file_atomic("a.json", "hello"));
    CHECK(fs.exists("a.json"));
    std::string out;
    CHECK(fs.read_file("a.json", out));
    CHECK(out == "hello");
    // Overwrite is atomic-replace
    CHECK(fs.write_file_atomic("a.json", "world"));
    CHECK(fs.read_file("a.json", out));
    CHECK(out == "world");
    CHECK(fs.remove_file("a.json"));
    CHECK_FALSE(fs.exists("a.json"));
}

TEST_CASE("HistoryStore: dedup by name, cap 20, newest first, persist") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    HistoryStore h;
    h.init(fs, sink, "program_history.json");

    for (int i = 0; i < 25; i++) {
        auto p = make_program(("Prog " + std::to_string(i)).c_str());
        h.add(p, "prompt", "2026-07-01T10:00:00", 1000 + i);
    }
    CHECK(h.size() == 20);
    // Newest first
    CHECK(std::string(h.doc()[0]["name"].GetString()) == "Prog 24");

    // Dedup: re-adding "Prog 24" replaces, size unchanged
    auto p = make_program("Prog 24", 5.0);
    h.add(p, "", "2026-07-01T11:00:00", 5000);
    CHECK(h.size() == 20);
    CHECK(std::string(h.doc()[0]["name"].GetString()) == "Prog 24");
    CHECK(h.doc()[0]["program"]["intervals"][0]["speed"].GetDouble() ==
          doctest::Approx(5.0));

    // Position update by program name
    h.update_position("Prog 24", 3, 450, true);
    CHECK(h.doc()[0]["last_interval"].GetInt() == 3);
    CHECK(h.doc()[0]["last_elapsed"].GetInt() == 450);
    CHECK(h.doc()[0]["completed"].GetBool());

    // Entry shape
    auto& e = h.doc()[0];
    for (const char* key :
         {"id", "name", "program", "source", "prompt", "total_duration",
          "completed", "last_interval", "last_elapsed", "created_at"}) {
        CHECK(e.HasMember(key));
    }
    CHECK(e["total_duration"].GetInt() == 600);
    CHECK(e["source"].IsNull());

    // Reload from disk — same content survives
    HistoryStore h2;
    h2.init(fs, sink, "program_history.json");
    CHECK(h2.size() == 20);
    CHECK(std::string(h2.doc()[0]["name"].GetString()) == "Prog 24");
}

TEST_CASE("WorkoutStore: order, rename updates program.name, usage") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    WorkoutStore w;
    w.init(fs, sink, "saved_workouts.json");

    auto p1 = make_program("First");
    auto p2 = make_program("Second");
    std::string id1 = w.save_workout(p1, "generated", "", "2026-07-01T10:00:00", 100);
    std::string id2 = w.save_workout(p2, "manual", "", "2026-07-01T10:05:00", 200);

    // Neither used: created_at DESC -> Second first
    auto ordered = w.ordered();
    REQUIRE(ordered.size() == 2);
    CHECK(std::string((*ordered[0])["name"].GetString()) == "Second");
    CHECK((*ordered[0])["last_used"].IsNull());
    CHECK((*ordered[0])["times_used"].GetInt() == 0);

    // Use the older one -> it sorts first (last_used DESC, nulls last)
    w.bump_usage(id1, "2026-07-02T09:00:00");
    ordered = w.ordered();
    CHECK(std::string((*ordered[0])["name"].GetString()) == "First");
    CHECK((*ordered[0])["times_used"].GetInt() == 1);
    CHECK(std::string((*ordered[0])["last_used"].GetString()) ==
          "2026-07-02T09:00:00");

    // Rename updates both row name and program.name (db.py parity)
    CHECK(w.rename(id2, "Renamed", "2026-07-02T10:00:00"));
    rapidjson::Value* e = w.find_by_id(id2);
    REQUIRE(e != nullptr);
    CHECK(std::string((*e)["name"].GetString()) == "Renamed");
    CHECK(std::string((*e)["program"]["name"].GetString()) == "Renamed");
    CHECK_FALSE(w.rename("nope", "X", "2026-07-02T10:00:00"));

    CHECK(w.remove_by_id(id2));
    CHECK_FALSE(w.remove_by_id(id2));
    CHECK(w.size() == 1);
}

TEST_CASE("RunStore: cap 40 (device delta), checkpoint, finalize, boot_recover") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    RunStore r;
    r.init(fs, sink, "run_history.json");

    auto make_run = [&](const std::string& id, const char* reason) {
        rapidjson::Value rec(rapidjson::kObjectType);
        auto& a = r.doc().GetAllocator();
        rec.AddMember("id", rapidjson::Value(id.c_str(), a), a);
        rec.AddMember("started_at", "2026-07-01T10:00:00", a);
        rec.AddMember("ended_at", rapidjson::Value(), a);
        rec.AddMember("elapsed", 10.0, a);
        rec.AddMember("distance", 0.01, a);
        rec.AddMember("vert_feet", 0.0, a);
        rec.AddMember("calories", 1.0, a);
        rec.AddMember("end_reason", rapidjson::Value(reason, a), a);
        rec.AddMember("program_name", "P", a);
        rec.AddMember("program_fingerprint", "3.0,0.0,600", a);
        rec.AddMember("program_completed", false, a);
        rec.AddMember("is_manual", true, a);
        return rec;
    };

    for (int i = 0; i < 45; i++) {
        r.insert(make_run("run" + std::to_string(i), "user_stop"));
    }
    CHECK(r.size() == MAX_RUNS);  // device cap 40 (python keeps 200)
    CHECK(std::string(r.doc()[0]["id"].GetString()) == "run44");

    r.insert(make_run("live", "in_progress"));
    r.update_metrics("live", 42.5, 0.05, 3.0, 5.5);
    CHECK(r.doc()[0]["elapsed"].GetDouble() == doctest::Approx(42.5));

    // Fingerprint lookup: newest-first, first-seen wins
    const rapidjson::Value* run = r.last_run_for_fingerprint("3.0,0.0,600");
    REQUIRE(run != nullptr);
    CHECK(std::string((*run)["id"].GetString()) == "live");
    CHECK(r.last_run_for_fingerprint("") == nullptr);
    CHECK(r.last_run_for_fingerprint("nope") == nullptr);

    // Finalize
    r.finalize("live", "2026-07-01T10:30:00", 60.0, 0.1, 5.0, 8.0,
               "user_stop", false);
    CHECK(std::string(r.doc()[0]["end_reason"].GetString()) == "user_stop");
    CHECK(std::string(r.doc()[0]["ended_at"].GetString()) ==
          "2026-07-01T10:30:00");

    // boot_recover flips in_progress -> disconnect
    r.insert(make_run("crashed", "in_progress"));
    RunStore r2;
    r2.init(fs, sink, "run_history.json");
    CHECK(r2.boot_recover("2026-07-01T11:00:00") == 1);
    CHECK(std::string(r2.doc()[0]["end_reason"].GetString()) == "disconnect");
    CHECK(std::string(r2.doc()[0]["ended_at"].GetString()) ==
          "2026-07-01T11:00:00");
    CHECK(r2.boot_recover("2026-07-01T11:00:01") == 0);
}

TEST_CASE("JsonArrayStore: malformed file starts empty") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    fs.write_file_atomic("bad.json", "{not json![");
    HistoryStore h;
    h.init(fs, sink, "bad.json");
    CHECK(h.size() == 0);
}

TEST_CASE("shape-mismatched entries are dropped at load, not aborted on") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    // A run file written by a hypothetical older firmware: one good
    // record, one missing the metric members the 30 s checkpoint
    // mutators index with operator[], one non-object.
    fs.write_file_atomic(
        "run_history.json",
        "[{\"id\":\"good\",\"elapsed\":1.0,\"distance\":0.0,"
        "\"vert_feet\":0.0,\"calories\":0.0,\"end_reason\":\"user_stop\","
        "\"ended_at\":null,\"program_completed\":false},"
        "{\"id\":\"legacy\",\"note\":\"no metrics\"},42]");
    RunStore r;
    r.init(fs, sink, "run_history.json");
    CHECK(r.size() == 1);
    CHECK(std::string(r.doc()[0]["id"].GetString()) == "good");
    // The surviving entry is fully mutable (the abort scenario this
    // guards against was update_metrics on a missing member).
    r.update_metrics("good", 99.0, 1.0, 2.0, 3.0);
    CHECK(r.doc()[0]["elapsed"].GetDouble() == doctest::Approx(99.0));

    // Same for workouts + history + profiles.
    fs.write_file_atomic("saved_workouts.json",
                         "[{\"id\":\"w\",\"nope\":true}]");
    WorkoutStore w;
    w.init(fs, sink, "saved_workouts.json");
    CHECK(w.size() == 0);
    fs.write_file_atomic("program_history.json", "[{\"id\":\"h\"}]");
    HistoryStore h;
    h.init(fs, sink, "program_history.json");
    CHECK(h.size() == 0);
}

TEST_CASE("WorkoutStore: save refused at the device cap (never dropped)") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    WorkoutStore w;
    w.init(fs, sink, "saved_workouts.json");
    for (int i = 0; i < MAX_WORKOUTS; i++) {
        auto p = make_program(("W" + std::to_string(i)).c_str());
        CHECK_FALSE(
            w.save_workout(p, "generated", "", "2026-07-01T10:00:00", i)
                .empty());
    }
    CHECK(w.size() == MAX_WORKOUTS);
    auto extra = make_program("Overflow");
    CHECK(w.save_workout(extra, "generated", "", "2026-07-01T11:00:00", 999)
              .empty());
    CHECK(w.size() == MAX_WORKOUTS);  // nothing evicted
    // Frees a slot -> save succeeds again
    std::string victim(w.doc()[0]["id"].GetString());
    CHECK(w.remove_by_id(victim));
    CHECK_FALSE(
        w.save_workout(extra, "generated", "", "2026-07-01T11:01:00", 1000)
            .empty());
}

TEST_CASE("ProfileStore: create/update/remove + active-id persistence") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    ProfileStore p;
    p.init_with_state(fs, sink, "profiles.json", "profile_state.json");
    CHECK(p.active_id().empty());

    std::string id =
        p.create("alice", "#112233", 150, 10, "2026-07-01T10:00:00", 1);
    REQUIRE_FALSE(id.empty());
    rapidjson::Value* e = p.find_by_id(id);
    REQUIRE(e != nullptr);
    CHECK(std::string((*e)["initials"].GetString()) == "A");
    CHECK((*e)["weight_lbs"].GetInt() == 150);
    CHECK_FALSE((*e)["has_avatar"].GetBool());

    // created_at order (python ORDER BY created_at ASC)
    p.create("bob", "#445566", 200, 0, "2026-07-01T10:05:00", 2);
    CHECK(std::string(p.doc()[0]["name"].GetString()) == "alice");

    p.set_active(id);
    std::string name = "carol";
    CHECK(p.update(id, &name, nullptr, 160, -1, "2026-07-01T11:00:00"));
    e = p.find_by_id(id);
    CHECK(std::string((*e)["name"].GetString()) == "carol");
    CHECK(std::string((*e)["initials"].GetString()) == "C");
    CHECK((*e)["weight_lbs"].GetInt() == 160);
    CHECK((*e)["vest_lbs"].GetInt() == 10);  // unchanged

    // Reload: profiles AND active id survive
    ProfileStore p2;
    p2.init_with_state(fs, sink, "profiles.json", "profile_state.json");
    CHECK(p2.size() == 2);
    CHECK(p2.active_id() == id);
    CHECK(p2.remove(id));
    CHECK_FALSE(p2.remove(id));

    // Cap: creation refused when full
    ProfileStore p3;
    p3.init_with_state(fs, sink, "profiles3.json", "profile_state3.json");
    for (int i = 0; i < MAX_PROFILES; i++) {
        CHECK_FALSE(p3.create("u" + std::to_string(i), "#000000", 154, 0,
                              "2026-07-01T10:00:00", 100 + i)
                        .empty());
    }
    CHECK(p3.create("overflow", "#000000", 154, 0, "2026-07-01T10:00:00",
                    999)
              .empty());
}

TEST_CASE("save() compacts a bloated allocator pool (bounded slack)") {
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    WorkoutStore w;
    w.init(fs, sink, "saved_workouts.json");
    auto p = make_program("Churn");
    std::string id =
        w.save_workout(p, "generated", "", "2026-07-01T10:00:00", 1);
    REQUIRE_FALSE(id.empty());
    // Hammer the mutators the way a /load loop would: each bump_usage
    // rewrites strings in the pool allocator, which never frees.
    for (int i = 0; i < 5000; i++) {
        w.bump_usage(id, "2026-07-02T09:00:00");
    }
    // Without compaction the pool grows ~unbounded (5000 * ~100 B).
    // With compaction the dead space stays under the slack bound.
    auto& a = w.doc().GetAllocator();
    CHECK(a.Capacity() - a.Size() <= 32 * 1024);
    CHECK(w.doc()[0]["times_used"].GetInt() == 5000);
}

namespace {

// A program big enough that the ENTRY COUNT cap is not a memory bound.
rapidjson::Document make_big_program(const char* name, int intervals = 64) {
    rapidjson::Document d(rapidjson::kObjectType);
    auto& a = d.GetAllocator();
    d.AddMember("name", rapidjson::Value(name, a), a);
    rapidjson::Value ivs(rapidjson::kArrayType);
    for (int i = 0; i < intervals; i++) {
        rapidjson::Value iv(rapidjson::kObjectType);
        iv.AddMember("name", "Interval with a fairly long display name", a);
        iv.AddMember("duration", 300, a);
        iv.AddMember("speed", 5.5, a);
        iv.AddMember("incline", 2.5, a);
        ivs.PushBack(iv, a);
    }
    d.AddMember("intervals", ivs, a);
    return d;
}

}  // namespace

TEST_CASE("stores are bounded in BYTES, not just entry count") {
    // MAX_HISTORY caps the number of entries, but a single entry can
    // hold a 64-interval program — 20 of those is ~100 KB resident on a
    // part with no PSRAM, and get_history() copies + serializes it all
    // again. The byte cap must evict oldest entries.
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    HistoryStore h;
    h.init(fs, sink, "program_history.json");

    for (int i = 0; i < MAX_HISTORY; i++) {
        auto p = make_big_program(("Big " + std::to_string(i)).c_str());
        h.add(p, "", "2026-07-01T10:00:00", 1000 + i);
    }
    CHECK(h.size() < MAX_HISTORY);       // evicted for size
    CHECK(h.size() >= 1);                // never evicts everything
    CHECK(h.serialize().size() <= h.max_bytes());

    // The NEWEST entry always survives the eviction.
    auto& arr = h.doc();
    REQUIRE(arr.Size() > 0);
    CHECK(std::string(arr[0]["name"].GetString()) ==
          "Big " + std::to_string(MAX_HISTORY - 1));

    // Reload applies the same bound (a store file written by a build
    // with a bigger cap must not blow the heap at boot).
    HistoryStore h2;
    h2.init(fs, sink, "program_history.json");
    CHECK(h2.serialize().size() <= h2.max_bytes());
}

TEST_CASE("workout ordering survives a reboot with no wall clock") {
    // The device clock restarts at the epoch every boot unless SNTP
    // reaches a server, so ordering keyed on the ISO strings INVERTS
    // across a reboot: pre-SNTP "1970" timestamps sort below (or above)
    // the previous boot's. Ordering must key on the persisted monotonic
    // sequence instead.
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);

    {
        WorkoutStore w;
        w.init(fs, sink, "saved_workouts.json");
        auto p1 = make_program("Boot1 First");
        auto p2 = make_program("Boot1 Second");
        // Boot 1 has a REAL clock (SNTP synced).
        w.save_workout(p1, "generated", "", "2026-07-01T10:00:00", 100);
        w.save_workout(p2, "generated", "", "2026-07-01T10:05:00", 200);
    }

    // Reboot: SNTP has not landed, so now_iso() is seconds-since-boot
    // rendered as 1970 — lexicographically BELOW everything from boot 1.
    WorkoutStore w2;
    w2.init(fs, sink, "saved_workouts.json");
    auto p3 = make_program("Boot2 Newest");
    std::string id3 =
        w2.save_workout(p3, "generated", "", "1970-01-01T00:00:03", 300);
    REQUIRE_FALSE(id3.empty());

    auto ordered = w2.ordered();
    REQUIRE(ordered.size() == 3);
    // Newest-created first even though its timestamp string is oldest.
    CHECK(std::string((*ordered[0])["name"].GetString()) == "Boot2 Newest");
    CHECK(std::string((*ordered[1])["name"].GetString()) == "Boot1 Second");

    // Using an old workout on the new boot still floats it to the top,
    // again with a 1970 timestamp.
    std::string id_first;
    for (auto& e : w2.doc().GetArray()) {
        if (std::string(e["name"].GetString()) == "Boot1 First") {
            id_first = e["id"].GetString();
        }
    }
    REQUIRE_FALSE(id_first.empty());
    w2.bump_usage(id_first, "1970-01-01T00:00:04");
    ordered = w2.ordered();
    CHECK(std::string((*ordered[0])["name"].GetString()) == "Boot1 First");
}

// --- regression: byte cap must never silently delete user favorites ---

TEST_CASE("WorkoutStore REFUSES an over-cap save instead of evicting") {
    // The byte cap binds long before MAX_WORKOUTS: a worst-case
    // 64-interval program serializes to ~7 KB, so two of them already
    // fill a 16 KB store while the count cap (20) is nowhere near.
    // Evicting the tail to make room would silently delete favorites
    // the user explicitly asked to keep while still answering 200.
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    WorkoutStore w;
    w.init(fs, sink, "saved_workouts.json");

    auto first = make_big_program("Favourite 0");
    std::string first_id =
        w.save_workout(first, "generated", "", "2026-07-01T10:00:00", 1000);
    REQUIRE(!first_id.empty());

    int accepted = 1;
    int refused = 0;
    for (int i = 1; i < MAX_WORKOUTS; i++) {
        auto p = make_big_program(("Favourite " + std::to_string(i)).c_str());
        std::string id = w.save_workout(p, "generated", "",
                                        "2026-07-01T10:00:00", 1000 + i);
        if (id.empty()) {
            refused++;
        } else {
            accepted++;
        }
    }
    CHECK(refused > 0);  // the cap really was reached
    CHECK(w.size() == accepted);
    CHECK(w.serialize().size() <= w.max_bytes());
    // The point of the whole test: the first favourite is STILL THERE.
    CHECK(w.find_by_id(first_id) != nullptr);

    // And it survives a reload.
    WorkoutStore w2;
    w2.init(fs, sink, "saved_workouts.json");
    CHECK(w2.find_by_id(first_id) != nullptr);
    CHECK(w2.size() == accepted);
}

TEST_CASE("ProfileStore is oldest-FIRST: the cap must not evict the newest") {
    // Every other store is newest-first, so the base class evicts the
    // array tail. ProfileStore appends (python ORDER BY created_at), so
    // its tail is the profile the user just created. It therefore never
    // evicts at all — create() refuses — and states index 0 as oldest.
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);
    ProfileStore p;
    p.init_with_state(fs, sink, "profiles.json", "profile_state.json");

    std::string oldest =
        p.create("Alice", "#ff0000", 150, 0, "2026-07-01T10:00:00", 1);
    std::string newest =
        p.create("Bob", "#00ff00", 160, 0, "2026-07-01T10:01:00", 2);
    REQUIRE(!oldest.empty());
    REQUIRE(!newest.empty());
    // Array order is creation order (oldest first).
    CHECK(std::string(p.doc()[0]["name"].GetString()) == "Alice");
    CHECK(std::string(p.doc()[1]["name"].GetString()) == "Bob");
    // Both survive a save (no eviction path may run on this store).
    p.update(oldest, nullptr, nullptr, 155, -1, "2026-07-01T10:02:00");
    CHECK(p.find_by_id(oldest) != nullptr);
    CHECK(p.find_by_id(newest) != nullptr);
}

TEST_CASE("store load refuses an over-cap file instead of parsing it") {
    // HEAP PEAK regression. The read cap — not the store cap, which is
    // only applied after parsing — is what bounds boot-time memory: the
    // raw text and the parsed document are resident simultaneously on a
    // 512 KB no-PSRAM part. A file written by a build with a bigger cap
    // must be refused before it is ever read into RAM.
    std::string dir = make_tmp_dir();
    PosixFs fs(dir);
    DirectPersist sink(fs);

    HistoryStore probe;
    const size_t cap = probe.max_bytes();

    // A syntactically valid array, comfortably over the cap.
    std::string big = "[";
    while (big.size() < cap * 2) {
        if (big.size() > 1) big += ",";
        big +=
            "{\"id\":\"x\",\"completed\":false,\"last_interval\":0,"
            "\"last_elapsed\":0,\"name\":\"" +
            std::string(200, 'p') + "\"}";
    }
    big += "]";
    CHECK(big.size() > cap);
    REQUIRE(fs.write_file_atomic("program_history.json", big));

    // read_file with the store's own cap refuses (does not truncate).
    std::string raw;
    CHECK(fs.read_file("program_history.json", raw, cap) == false);
    CHECK(raw.empty());

    HistoryStore h;
    h.init(fs, sink, "program_history.json");
    CHECK(h.size() == 0);  // degraded to empty, never a half-parsed array
    CHECK(h.serialize().size() <= h.max_bytes());
}

// --- regression: the persist queue must never drop a write-once store --

TEST_CASE("PersistQueue coalesces per path and never drops another store") {
    PersistQueue q;
    // Same path twice: the second supersedes, no second wake token.
    CHECK(q.stage("a.json", "v1") == PersistQueue::Stage::QUEUED_NEW);
    CHECK(q.stage("a.json", "v2") == PersistQueue::Stage::COALESCED);
    CHECK(q.pending() == 1);
    std::string path, content;
    REQUIRE(q.take(path, content));
    CHECK(path == "a.json");
    CHECK(content == "v2");  // latest wins — supersede is lossless
    CHECK(q.take(path, content) == false);

    // The N6 shape: a WRITE-ONCE store's single write is issued once and
    // then a different store is written repeatedly. The write-once entry
    // must still be there.
    CHECK(q.stage("program_history.json", "history") ==
          PersistQueue::Stage::QUEUED_NEW);
    for (int i = 0; i < 100; i++) {
        q.stage("run_history.json", "run" + std::to_string(i));
    }
    CHECK(q.pending() == 2);
    bool saw_history = false;
    while (q.take(path, content)) {
        if (path == "program_history.json") {
            saw_history = true;
            CHECK(content == "history");
        }
    }
    CHECK(saw_history);

    // FIFO across paths.
    q.stage("one", "1");
    q.stage("two", "2");
    REQUIRE(q.take(path, content));
    CHECK(path == "one");
    REQUIRE(q.take(path, content));
    CHECK(path == "two");
}
