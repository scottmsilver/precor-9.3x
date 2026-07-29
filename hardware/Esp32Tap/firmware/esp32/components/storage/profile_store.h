/*
 * profile_store.h — user profiles (python/db.py profiles-table parity,
 * JSON-array file instead of SQLite). File: /data/profiles.json plus a
 * tiny /data/profile_state.json holding the active profile id
 * (python's app_state row).
 *
 * The guest profile is NOT stored: it is synthesized by the caller
 * (ServerCore) exactly like python's fixed guest row ('Guest',
 * '#888888', 'G', 154 lbs). Avatars are not supported on-device
 * (has_avatar stays false; the avatar endpoints answer accordingly).
 *
 * Deliberate device delta: python is unbounded; profiles are capped at
 * MAX_PROFILES (RAM), creation refused when full.
 */

#pragma once

#include <string>
#include <string_view>

#include "json_store.h"

namespace esp32tap::storage {

inline constexpr std::string_view GUEST_PROFILE_ID =
    "00000000-0000-0000-0000-000000000000";
inline constexpr int DEFAULT_WEIGHT_LBS = 154;  // python/db.py
inline constexpr int MAX_PROFILES = 8;

class ProfileStore : public JsonArrayStore {
public:
    // Boot-time load of both the profile array and the active-id state.
    void init_with_state(FsApi& fs, PersistSink& sink, std::string path,
                         std::string state_path) {
        init(fs, sink, std::move(path));
        state_path_ = std::move(state_path);
        active_id_.clear();
        std::string raw;
        if (fs.read_file(state_path_, raw)) {
            rapidjson::Document d;
            d.Parse(raw.c_str(), raw.size());
            if (!d.HasParseError() && d.IsObject()) {
                auto m = d.FindMember("active_id");
                if (m != d.MemberEnd() && m->value.IsString()) {
                    active_id_.assign(m->value.GetString(),
                                      m->value.GetStringLength());
                }
            }
        }
    }

    // "" == none set (python get_active_profile_id() -> NULL).
    const std::string& active_id() const { return active_id_; }

    void set_active(std::string_view id) {
        active_id_.assign(id.data(), id.size());
        if (sink_ == nullptr) return;
        rapidjson::Document d(rapidjson::kObjectType);
        auto& a = d.GetAllocator();
        d.AddMember("active_id",
                    rapidjson::Value(
                        active_id_.c_str(),
                        static_cast<rapidjson::SizeType>(active_id_.size()), a),
                    a);
        rapidjson::StringBuffer sb;
        rapidjson::Writer<rapidjson::StringBuffer> w(sb);
        d.Accept(w);
        sink_->persist(state_path_, std::string(sb.GetString(), sb.GetSize()));
    }

    // Create a profile (python db.create_profile parity: initials from
    // the first character of name unless empty). Returns the new id, or
    // "" when the store is full.
    std::string create(std::string_view name, std::string_view color,
                       int weight_lbs, int vest_lbs, std::string_view now_iso,
                       int64_t now_us) {
        if (size() >= MAX_PROFILES) return "";
        auto& a = doc_.GetAllocator();
        std::string id = make_id(now_us);
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(
                v.data(), static_cast<rapidjson::SizeType>(v.size()), a);
        };
        rapidjson::Value e(rapidjson::kObjectType);
        e.AddMember("id", s(id), a);
        e.AddMember("name", s(name), a);
        e.AddMember("color", s(color), a);
        e.AddMember("initials", s(initials_of(name)), a);
        e.AddMember("has_avatar", false, a);
        e.AddMember("weight_lbs", weight_lbs, a);
        e.AddMember("vest_lbs", vest_lbs, a);
        e.AddMember("created_at", s(now_iso), a);
        e.AddMember("updated_at", s(now_iso), a);
        // python: ORDER BY created_at (oldest first) — append.
        doc_.PushBack(e, a);
        if (!fits_byte_cap()) {
            // User-owned rows: refuse rather than evict (the handler
            // surfaces the error). Undo the append and report full.
            doc_.Erase(doc_.End() - 1);
            return "";
        }
        save();
        return id;
    }

    // Update supported fields (nullptr / negative == leave unchanged);
    // name updates recompute initials (server.py endpoint parity).
    bool update(std::string_view id, const std::string* name,
                const std::string* color, int weight_lbs, int vest_lbs,
                std::string_view now_iso) {
        rapidjson::Value* e = find_by_id(id);
        if (e == nullptr) return false;
        auto& a = doc_.GetAllocator();
        auto s = [&a](std::string_view v) {
            return rapidjson::Value(
                v.data(), static_cast<rapidjson::SizeType>(v.size()), a);
        };
        if (name != nullptr) {
            (*e)["name"] = s(*name);
            (*e)["initials"] = s(initials_of(*name));
        }
        if (color != nullptr) (*e)["color"] = s(*color);
        if (weight_lbs >= 0) (*e)["weight_lbs"].SetInt(weight_lbs);
        if (vest_lbs >= 0) (*e)["vest_lbs"].SetInt(vest_lbs);
        (*e)["updated_at"] = s(now_iso);
        save();
        return true;
    }

    bool remove(std::string_view id) {
        for (rapidjson::SizeType i = 0; i < doc_.Size(); i++) {
            auto& el = doc_[i];
            if (!el.IsObject()) continue;
            auto m = el.FindMember("id");
            if (m != el.MemberEnd() && m->value.IsString() &&
                id == std::string_view(m->value.GetString(),
                                       m->value.GetStringLength())) {
                doc_.Erase(doc_.Begin() + i);
                save();
                return true;
            }
        }
        return false;
    }

    // First character of name, ASCII-uppercased ("?" when empty —
    // server.py parity).
    static std::string initials_of(std::string_view name) {
        if (name.empty()) return "?";
        char c = name.front();
        if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
        // Multibyte UTF-8 first char: keep the whole code point.
        if (static_cast<unsigned char>(c) < 0x80) return std::string(1, c);
        size_t len = 1;
        while (len < name.size() &&
               (static_cast<unsigned char>(name[len]) & 0xC0) == 0x80) {
            len++;
        }
        return std::string(name.substr(0, len));
    }

protected:
    // ORDERING EXCEPTION. Every other store is newest-first, so the
    // base class's default "evict the tail" drops the oldest entry.
    // This store appends (python: ORDER BY created_at, oldest first),
    // so its tail is the NEWEST profile — the one the user just
    // created. Both halves of the contract are stated explicitly:
    // profiles are user-owned and are never evicted at all (create()
    // refuses instead), and if that ever changes, index 0 is the oldest.
    bool evicts_on_overflow() const override { return false; }
    rapidjson::SizeType evict_index() const override { return 0; }

    // Members update() indexes with operator[].
    bool entry_valid(const rapidjson::Value& e) const override {
        return e.IsObject() && has_str(e, "id") && has_str(e, "name") &&
               has_str(e, "color") && has_str(e, "initials") &&
               has_num(e, "weight_lbs") && has_num(e, "vest_lbs") &&
               has_str(e, "created_at") && has_str(e, "updated_at");
    }

private:
    std::string state_path_;
    std::string active_id_;
};

}  // namespace esp32tap::storage
