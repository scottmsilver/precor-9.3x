/*
 * program_json.h — Program <-> rapidjson conversion + the
 * server.py _validate_program port. Cold path (heap use OK per repo
 * rules; all inputs are length-capped upstream by the 1 KB body limit
 * or the bounded store files).
 *
 * Shape parity with python: a program dict is
 *   {"name": str, "manual": true (only when manual),
 *    "intervals": [{"name","duration","speed","incline"}, ...]}
 * Every serialized interval carries all four keys (the Kotlin Interval
 * model has no defaults — all four are deserialization-mandatory).
 */

#pragma once

#include <string>

#include "rapidjson/document.h"

#include "program_model.h"

namespace esp32tap::exec {

// Tolerant parse (Postel): unknown keys ignored, missing name -> "",
// numeric fields accept int or double. Returns false only when
// "intervals" is absent/not an array or an element is not an object.
// Intervals beyond MAX_INTERVALS are dropped (documented degradation).
inline bool program_from_json(const rapidjson::Value& v, Program& out) {
    out = Program{};
    if (!v.IsObject()) return false;
    auto name = v.FindMember("name");
    if (name != v.MemberEnd() && name->value.IsString()) {
        out.name.set(std::string_view(name->value.GetString(),
                                      name->value.GetStringLength()));
    }
    auto manual = v.FindMember("manual");
    if (manual != v.MemberEnd() &&
        (manual->value.IsBool() ? manual->value.GetBool()
                                : !manual->value.IsNull())) {
        out.manual = manual->value.IsBool() ? manual->value.GetBool() : true;
    }
    auto ivs = v.FindMember("intervals");
    if (ivs == v.MemberEnd() || !ivs->value.IsArray()) return false;
    for (const auto& e : ivs->value.GetArray()) {
        if (!e.IsObject()) return false;
        if (out.count >= MAX_INTERVALS) break;
        Interval& iv = out.intervals.at(static_cast<size_t>(out.count));
        auto n = e.FindMember("name");
        if (n != e.MemberEnd() && n->value.IsString()) {
            iv.name.set(std::string_view(n->value.GetString(),
                                         n->value.GetStringLength()));
        }
        auto num = [&](const char* key, double dflt) {
            auto m = e.FindMember(key);
            if (m == e.MemberEnd() || !m->value.IsNumber()) return dflt;
            return m->value.GetDouble();
        };
        // Clamp in the double domain BEFORE the int conversion: an
        // out-of-range double->int cast is UB, and this value can come
        // from attacker-supplied or stale-flash JSON. 24 h bounds any
        // single interval; also keeps Program::total_duration (64
        // intervals) far from int overflow.
        double dur = num("duration", 0.0);
        if (!(dur >= 0.0)) dur = 0.0;  // NaN/negative -> 0
        if (dur > 86400.0) dur = 86400.0;
        iv.duration = static_cast<int>(dur);
        iv.speed = num("speed", 0.0);
        iv.incline = num("incline", 0.0);
        out.count++;
    }
    return true;
}

inline void program_to_json(const Program& p, rapidjson::Value& out,
                            rapidjson::Document::AllocatorType& a) {
    out.SetObject();
    auto nv = p.name.view();
    out.AddMember(
        "name",
        rapidjson::Value(nv.data(), static_cast<rapidjson::SizeType>(nv.size()), a),
        a);
    if (p.manual) out.AddMember("manual", true, a);
    rapidjson::Value arr(rapidjson::kArrayType);
    for (int i = 0; i < p.count; i++) {
        const Interval& iv = p.intervals.at(static_cast<size_t>(i));
        rapidjson::Value o(rapidjson::kObjectType);
        auto in = iv.name.view();
        o.AddMember("name",
                    rapidjson::Value(
                        in.data(), static_cast<rapidjson::SizeType>(in.size()), a),
                    a);
        o.AddMember("duration", iv.duration, a);
        o.AddMember("speed", iv.speed, a);
        o.AddMember("incline", iv.incline, a);
        arr.PushBack(o, a);
    }
    out.AddMember("intervals", arr, a);
}

// server.py _validate_program parity: "" == valid, else the error text
// (tests key off the word "intervals" being present in the message).
inline std::string validate_program_json(const rapidjson::Value& v) {
    if (!v.IsObject()) return "program must be a dict";
    auto ivs = v.FindMember("intervals");
    if (ivs == v.MemberEnd() || !ivs->value.IsArray()) {
        return "program must have an intervals list";
    }
    int i = 0;
    for (const auto& e : ivs->value.GetArray()) {
        if (!e.IsObject()) {
            return "interval " + fmt_int(i) + " must be a dict";
        }
        auto d = e.FindMember("duration");
        if (d == e.MemberEnd() || !d->value.IsNumber()) {
            return "interval " + fmt_int(i) + " must have a numeric duration";
        }
        i++;
    }
    return "";
}

}  // namespace esp32tap::exec
