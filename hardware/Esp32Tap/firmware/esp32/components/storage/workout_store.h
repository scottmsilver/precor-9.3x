/*
 * workout_store.h — saved workouts (python/db.py saved_workouts parity):
 * ordered last_used DESC then created_at DESC (nulls last).
 * File: /data/saved_workouts.json.
 *
 * Deliberate device delta: python applies no cap, but every entry lives
 * in RAM on a no-PSRAM part, so saves are REFUSED (never silently
 * dropped — these are user favorites) once MAX_WORKOUTS is reached.
 *
 * Deliberate delta from the python reference: the JSON key is
 * "last_used" (what the Kotlin SavedWorkout model actually expects),
 * not db.py's "last_used_at" (a latent python bug that left the app's
 * lastUsed forever null — see PLAN.md note).
 */

#pragma once

#include <algorithm>
#include <string>
#include <string_view>
#include <vector>

#include "json_store.h"

namespace esp32tap::storage {

inline constexpr int MAX_WORKOUTS = 20;

class WorkoutStore : public JsonArrayStore {
public:
    // Returns the new workout's id, or "" when the store is full —
    // either on ENTRY COUNT (MAX_WORKOUTS) or on TOTAL BYTES
    // (JsonArrayStore::max_bytes). The byte limit is the one that
    // actually binds: a worst-case 64-interval program is ~7 KB, so
    // two of them already fill a 16 KB store while the count cap is
    // nowhere near. Refusing (never evicting) is the documented
    // contract for this store — the caller surfaces
    // {"ok":false,"error":"Workout limit reached ..."}.
    std::string save_workout(const rapidjson::Value& program,
                             std::string_view source, std::string_view prompt,
                             std::string_view now_iso, int64_t now_us) {
        if (size() >= MAX_WORKOUTS) return "";
        auto& a = doc_.GetAllocator();
        std::string id = make_id(now_us);
        std::string name = program_name_of(program);
        rapidjson::Value e(rapidjson::kObjectType);
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(v.data(),
                                    static_cast<rapidjson::SizeType>(v.size()), a);
        };
        e.AddMember("id", s(id), a);
        e.AddMember("name", s(name), a);
        rapidjson::Value prog_copy(program, a);
        e.AddMember("program", prog_copy, a);
        e.AddMember("source", s(source), a);
        e.AddMember("prompt", s(prompt), a);
        e.AddMember("times_used", 0, a);
        e.AddMember("last_used", rapidjson::Value(), a);  // null
        e.AddMember("created_at", s(now_iso), a);
        e.AddMember("updated_at", s(now_iso), a);
        e.AddMember("total_duration", program_total_duration(program), a);
        e.AddMember("used_seq", 0, a);  // never used
        stamp_seq(e);
        push_front_capped(std::move(e), MAX_WORKOUTS);
        if (!fits_byte_cap()) {
            // Over the RAM budget: undo the insert and refuse. The
            // alternative — letting enforce_byte_cap() evict the tail —
            // would delete favorites the user explicitly saved while
            // still answering 200 {"ok":true}. push_front_capped never
            // dropped anything here (the count check above guarantees
            // room), so erasing the new front restores the exact prior
            // contents and nothing needs persisting.
            doc_.Erase(doc_.Begin());
            return "";
        }
        save();
        return id;
    }

    // Entries ordered most-recently-used first, then most-recently
    // created (SQLite "ORDER BY last_used DESC, created_at DESC, nulls
    // last" parity).
    //
    // Sorted on the MONOTONIC SEQUENCES, not the ISO strings: the
    // device clock restarts at the epoch on every boot unless SNTP
    // reaches a server, so lexicographic string ordering INVERTS across
    // a reboot (1970 timestamps sort below 2026 ones and vice versa).
    // "used_seq"/"seq" come from JsonArrayStore's persisted counter and
    // are correct with no clock at all.
    std::vector<rapidjson::Value*> ordered() {
        std::vector<rapidjson::Value*> out;
        for (auto& e : doc_.GetArray()) {
            if (e.IsObject()) out.push_back(&e);
        }
        auto num = [](const rapidjson::Value* v, const char* k) -> int64_t {
            auto m = v->FindMember(k);
            if (m == v->MemberEnd() || !m->value.IsNumber()) return 0;
            return m->value.GetInt64();
        };
        std::stable_sort(
            out.begin(), out.end(),
            [&](const rapidjson::Value* x, const rapidjson::Value* y) {
                int64_t ux = num(x, "used_seq");
                int64_t uy = num(y, "used_seq");
                if (ux != uy) return ux > uy;
                return num(x, "seq") > num(y, "seq");
            });
        return out;
    }

    // Rename updates both the row name and program.name (db.py parity).
    // Returns false when the id is unknown.
    bool rename(std::string_view id, std::string_view name,
                std::string_view now_iso) {
        rapidjson::Value* e = find_by_id(id);
        if (e == nullptr) return false;
        auto& a = doc_.GetAllocator();
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(v.data(),
                                    static_cast<rapidjson::SizeType>(v.size()), a);
        };
        (*e)["name"] = s(name);
        auto p = e->FindMember("program");
        if (p != e->MemberEnd() && p->value.IsObject()) {
            auto n = p->value.FindMember("name");
            if (n != p->value.MemberEnd()) {
                n->value = s(name);
            } else {
                p->value.AddMember("name", s(name), a);
            }
        }
        (*e)["updated_at"] = s(now_iso);
        save();
        return true;
    }

    bool remove_by_id(std::string_view id) {
        for (rapidjson::SizeType i = 0; i < doc_.Size(); i++) {
            auto& arr = doc_;
            if (!arr[i].IsObject()) continue;
            auto m = arr[i].FindMember("id");
            if (m != arr[i].MemberEnd() && m->value.IsString() &&
                id == std::string_view(m->value.GetString(),
                                       m->value.GetStringLength())) {
                doc_.Erase(doc_.Begin() + i);
                save();
                return true;
            }
        }
        return false;
    }

    // db.py update_workout_usage parity.
    void bump_usage(std::string_view id, std::string_view now_iso) {
        rapidjson::Value* e = find_by_id(id);
        if (e == nullptr) return;
        auto& a = doc_.GetAllocator();
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(v.data(),
                                    static_cast<rapidjson::SizeType>(v.size()), a);
        };
        auto t = e->FindMember("times_used");
        int times = (t != e->MemberEnd() && t->value.IsInt()) ? t->value.GetInt() : 0;
        (*e)["times_used"].SetInt(times + 1);
        (*e)["last_used"] = s(now_iso);
        (*e)["updated_at"] = s(now_iso);
        // Clock-independent recency (see ordered()).
        int64_t seq = next_seq();
        auto u = e->FindMember("used_seq");
        if (u != e->MemberEnd()) {
            u->value.SetInt64(seq);
        } else {
            e->AddMember("used_seq", seq, a);
        }
        save();
    }

protected:
    // These are user favorites: never evict to make room (see header).
    // save_workout() refuses the write instead, and rename/bump_usage
    // cannot grow the store past the cap by more than a name.
    bool evicts_on_overflow() const override { return false; }

    // Members rename()/bump_usage() index with operator[].
    bool entry_valid(const rapidjson::Value& e) const override {
        return e.IsObject() && has_str(e, "id") && has_str(e, "name") &&
               has_num(e, "times_used") && has_key(e, "last_used") &&
               has_str(e, "updated_at");
    }
};

}  // namespace esp32tap::storage
