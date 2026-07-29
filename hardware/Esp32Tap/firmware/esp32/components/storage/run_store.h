/*
 * run_store.h — run records (server.py _build_run_record shapes): cap
 * 200, newest-first, 30 s checkpoint updates, in_progress lifecycle.
 * File: /data/run_history.json.
 *
 * boot_recover(): the on-device analogue of "runs survive server
 * crashes" — any record still marked in_progress at boot is finalized
 * with end_reason "disconnect" (its metrics are the last 30 s
 * checkpoint; ended_at is unknowable, set to the recovery wall time).
 */

#pragma once

#include <string>
#include <string_view>

#include "json_store.h"

namespace esp32tap::storage {

// Deliberate device delta from server.py's 200: run records are held in
// RAM (rapidjson document) and fully re-serialized on every 30 s
// checkpoint. 40 records with hashed fingerprints is ~10 KB — sized for
// the no-PSRAM S3 heap (PLAN.md note).
inline constexpr int MAX_RUNS = 40;

class RunStore : public JsonArrayStore {
public:
    // record must contain an "id" member (server.py builds the full
    // record dict; handlers own the shape).
    void insert(rapidjson::Value&& record) {
        rapidjson::Value rec(std::move(record));
        stamp_seq(rec);
        push_front_capped(std::move(rec), MAX_RUNS);
        save();
    }

    // 30 s checkpoint: metrics only.
    void update_metrics(std::string_view id, double elapsed, double distance,
                        double vert_feet, double calories) {
        rapidjson::Value* e = find_by_id(id);
        if (e == nullptr) return;
        (*e)["elapsed"].SetDouble(elapsed);
        (*e)["distance"].SetDouble(distance);
        (*e)["vert_feet"].SetDouble(vert_feet);
        (*e)["calories"].SetDouble(calories);
        save();
    }

    // Finalize (session end): metrics + ended_at + end_reason +
    // program_completed.
    void finalize(std::string_view id, std::string_view ended_at_iso,
                  double elapsed, double distance, double vert_feet,
                  double calories, std::string_view end_reason,
                  bool program_completed) {
        rapidjson::Value* e = find_by_id(id);
        if (e == nullptr) return;
        auto& a = doc_.GetAllocator();
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(v.data(),
                                    static_cast<rapidjson::SizeType>(v.size()), a);
        };
        (*e)["ended_at"] = s(ended_at_iso);
        (*e)["elapsed"].SetDouble(elapsed);
        (*e)["distance"].SetDouble(distance);
        (*e)["vert_feet"].SetDouble(vert_feet);
        (*e)["calories"].SetDouble(calories);
        (*e)["end_reason"] = s(end_reason);
        (*e)["program_completed"].SetBool(program_completed);
        save();
    }

    // Returns how many in_progress records were converted.
    int boot_recover(std::string_view now_iso) {
        int fixed = 0;
        auto& a = doc_.GetAllocator();
        for (auto& e : doc_.GetArray()) {
            if (!e.IsObject()) continue;
            auto r = e.FindMember("end_reason");
            if (r == e.MemberEnd() || !r->value.IsString()) continue;
            if (std::string_view(r->value.GetString(),
                                 r->value.GetStringLength()) != "in_progress") {
                continue;
            }
            r->value = rapidjson::Value("disconnect", a);
            auto ended = e.FindMember("ended_at");
            rapidjson::Value iso(
                now_iso.data(), static_cast<rapidjson::SizeType>(now_iso.size()), a);
            if (ended != e.MemberEnd()) {
                ended->value = iso;
            } else {
                e.AddMember("ended_at", iso, a);
            }
            fixed++;
        }
        if (fixed > 0) save();
        return fixed;
    }

    // Newest run per program fingerprint (server.py
    // _last_run_by_fingerprint: newest-first, first seen wins). Caller
    // iterates; provided as a helper for the handlers.
    const rapidjson::Value* last_run_for_fingerprint(std::string_view fp) const {
        if (fp.empty()) return nullptr;
        for (const auto& e : doc_.GetArray()) {
            if (!e.IsObject()) continue;
            auto m = e.FindMember("program_fingerprint");
            if (m != e.MemberEnd() && m->value.IsString() &&
                fp == std::string_view(m->value.GetString(),
                                       m->value.GetStringLength())) {
                return &e;
            }
        }
        return nullptr;
    }

protected:
    // Members the mutators above index with operator[]. Records not
    // matching this shape (older firmware, truncated flash) are dropped
    // at load instead of aborting on the next 30 s checkpoint.
    bool entry_valid(const rapidjson::Value& e) const override {
        return e.IsObject() && has_str(e, "id") && has_num(e, "elapsed") &&
               has_num(e, "distance") && has_num(e, "vert_feet") &&
               has_num(e, "calories") && has_str(e, "end_reason") &&
               has_key(e, "ended_at") && has_bool(e, "program_completed");
    }
};

}  // namespace esp32tap::storage
