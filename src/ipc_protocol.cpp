/*
 * ipc_protocol.cpp — JSON command parsing + event building with RapidJSON
 */

#include "ipc_protocol.h"

// RapidJSON config: no exceptions, assert is a no-op (we check errors after parse)
#define RAPIDJSON_ASSERT(x) ((void)(x))
#define RAPIDJSON_HAS_CXX11_NOEXCEPT 1

#include <rapidjson/document.h>
#include <rapidjson/writer.h>
#include <rapidjson/stringbuffer.h>
#include <cstring>
#include <cstdio>

bool parse_command(char* json, int /*len*/, IpcCommand* out) {
    *out = IpcCommand{};

    rapidjson::Document doc;
    // In-situ parsing: modifies the input buffer (zero allocation)
    doc.ParseInsitu(json);
    if (doc.HasParseError() || !doc.IsObject()) return false;

    // Extract "cmd" field
    auto cmd_it = doc.FindMember("cmd");
    if (cmd_it == doc.MemberEnd() || !cmd_it->value.IsString()) return false;

    const char* cmd = cmd_it->value.GetString();

    if (std::strcmp(cmd, "speed") == 0) {
        out->type = CmdType::Speed;
        auto val_it = doc.FindMember("value");
        if (val_it != doc.MemberEnd()) {
            if (val_it->value.IsDouble())
                out->float_value = val_it->value.GetDouble();
            else if (val_it->value.IsInt())
                out->float_value = static_cast<double>(val_it->value.GetInt());
            else if (val_it->value.IsUint())
                out->float_value = static_cast<double>(val_it->value.GetUint());
        }
        return true;
    }
    else if (std::strcmp(cmd, "incline") == 0) {
        out->type = CmdType::Incline;
        auto val_it = doc.FindMember("value");
        if (val_it != doc.MemberEnd()) {
            if (val_it->value.IsInt())
                out->int_value = val_it->value.GetInt();
            else if (val_it->value.IsUint())
                out->int_value = static_cast<int>(val_it->value.GetUint());
            else if (val_it->value.IsDouble())
                out->int_value = static_cast<int>(val_it->value.GetDouble());
        }
        return true;
    }
    else if (std::strcmp(cmd, "emulate") == 0) {
        out->type = CmdType::Emulate;
        auto val_it = doc.FindMember("enabled");
        if (val_it != doc.MemberEnd() && val_it->value.IsBool())
            out->bool_value = val_it->value.GetBool();
        return true;
    }
    else if (std::strcmp(cmd, "proxy") == 0) {
        out->type = CmdType::Proxy;
        auto val_it = doc.FindMember("enabled");
        if (val_it != doc.MemberEnd() && val_it->value.IsBool())
            out->bool_value = val_it->value.GetBool();
        return true;
    }
    else if (std::strcmp(cmd, "status") == 0) {
        out->type = CmdType::Status;
        return true;
    }
    else if (std::strcmp(cmd, "quit") == 0) {
        out->type = CmdType::Quit;
        return true;
    }

    return false;
}

int build_kv_event(const KvEvent& ev, char* out, int out_len) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);

    w.StartObject();
    w.Key("type"); w.String("kv");
    w.Key("ts"); w.Double(ev.ts);
    w.Key("source"); w.String(ev.source);
    w.Key("key"); w.String(ev.key);
    w.Key("value"); w.String(ev.value);
    w.EndObject();

    int written = std::snprintf(out, out_len, "%s\n", sb.GetString());
    return written < out_len ? written : out_len - 1;
}

int build_status_event(const StatusEvent& ev, char* out, int out_len) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);

    w.StartObject();
    w.Key("type"); w.String("status");
    w.Key("proxy"); w.Bool(ev.proxy);
    w.Key("emulate"); w.Bool(ev.emulate);
    w.Key("emu_speed"); w.Int(ev.emu_speed);
    w.Key("emu_incline"); w.Int(ev.emu_incline);
    w.Key("console_bytes"); w.Uint(ev.console_bytes);
    w.Key("motor_bytes"); w.Uint(ev.motor_bytes);
    w.EndObject();

    int written = std::snprintf(out, out_len, "%s\n", sb.GetString());
    return written < out_len ? written : out_len - 1;
}

int build_error_event(const char* msg, char* out, int out_len) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);

    w.StartObject();
    w.Key("type"); w.String("error");
    w.Key("msg"); w.String(msg);
    w.EndObject();

    int written = std::snprintf(out, out_len, "%s\n", sb.GetString());
    return written < out_len ? written : out_len - 1;
}
