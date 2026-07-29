/*
 * history_store.h — program history (python/db.py program_history
 * parity): cap 20, dedup-by-name on insert, newest-first. File:
 * /data/program_history.json. Enrichment (saved / saved_workout_id /
 * last_run / last_run_text) happens at read time in the handlers, not
 * here.
 */

#pragma once

#include <string>
#include <string_view>

#include "json_store.h"

namespace esp32tap::storage {

inline constexpr int MAX_HISTORY = 20;  // python/db.py MAX_HISTORY (live
                                        // value; root CLAUDE.md's "10" is
                                        // stale — see PLAN.md note)

class HistoryStore : public JsonArrayStore {
public:
    // Add a program (dedup by name, cap, newest first). source_null
    // mirrors python's source=None for plain history adds. Returns the
    // new entry's id.
    std::string add(const rapidjson::Value& program, std::string_view prompt,
                    std::string_view created_at_iso, int64_t now_us,
                    bool source_null = true, std::string_view source = "") {
        auto& a = doc_.GetAllocator();
        std::string name = program_name_of(program);

        // Dedup: remove any existing entry with the same name.
        for (rapidjson::SizeType i = 0; i < doc_.Size();) {
            auto& el = doc_[i];
            bool match = false;
            if (el.IsObject()) {
                auto n = el.FindMember("name");
                match = n != el.MemberEnd() && n->value.IsString() &&
                        name == n->value.GetString();
            }
            if (match) {
                doc_.Erase(doc_.Begin() + i);
            } else {
                i++;
            }
        }

        std::string id = make_id(now_us);
        rapidjson::Value e(rapidjson::kObjectType);
        e.AddMember("id",
                    rapidjson::Value(id.c_str(),
                                     static_cast<rapidjson::SizeType>(id.size()), a),
                    a);
        e.AddMember("name",
                    rapidjson::Value(name.c_str(),
                                     static_cast<rapidjson::SizeType>(name.size()), a),
                    a);
        rapidjson::Value prog_copy(program, a);
        e.AddMember("program", prog_copy, a);
        if (source_null) {
            e.AddMember("source", rapidjson::Value(), a);  // null
        } else {
            e.AddMember(
                "source",
                rapidjson::Value(source.data(),
                                 static_cast<rapidjson::SizeType>(source.size()), a),
                a);
        }
        e.AddMember(
            "prompt",
            rapidjson::Value(prompt.data(),
                             static_cast<rapidjson::SizeType>(prompt.size()), a),
            a);
        e.AddMember("total_duration", program_total_duration(program), a);
        e.AddMember("completed", false, a);
        e.AddMember("last_interval", 0, a);
        e.AddMember("last_elapsed", 0, a);
        e.AddMember("created_at",
                    rapidjson::Value(
                        created_at_iso.data(),
                        static_cast<rapidjson::SizeType>(created_at_iso.size()), a),
                    a);

        stamp_seq(e);
        push_front_capped(std::move(e), MAX_HISTORY);
        save();
        return id;
    }

    // server.py _update_history_position parity: first entry whose
    // program.name matches gets position/completed updated.
    void update_position(std::string_view program_name, int interval,
                         int elapsed, bool completed) {
        for (auto& e : doc_.GetArray()) {
            if (!e.IsObject()) continue;
            auto p = e.FindMember("program");
            if (p == e.MemberEnd() || !p->value.IsObject()) continue;
            auto n = p->value.FindMember("name");
            if (n == p->value.MemberEnd() || !n->value.IsString()) continue;
            if (program_name !=
                std::string_view(n->value.GetString(),
                                 n->value.GetStringLength())) {
                continue;
            }
            e["completed"].SetBool(completed);
            e["last_interval"].SetInt(interval);
            e["last_elapsed"].SetInt(elapsed);
            save();
            return;
        }
    }

protected:
    // Members update_position() indexes with operator[].
    bool entry_valid(const rapidjson::Value& e) const override {
        return e.IsObject() && has_str(e, "id") && has_bool(e, "completed") &&
               has_num(e, "last_interval") && has_num(e, "last_elapsed");
    }
};

}  // namespace esp32tap::storage
