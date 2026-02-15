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

std::optional<IpcCommand> parse_command(std::string_view json) {
    if (json.empty() || json.size() > MAX_IPC_COMMAND_LEN) return std::nullopt;

    // RapidJSON in-situ needs a mutable, null-terminated buffer
    std::string buf(json);

    rapidjson::Document doc;
    doc.ParseInsitu(buf.data());
    if (doc.HasParseError() || !doc.IsObject()) return std::nullopt;

    // Extract "cmd" field
    auto cmd_it = doc.FindMember("cmd");
    if (cmd_it == doc.MemberEnd() || !cmd_it->value.IsString()) return std::nullopt;

    std::string_view cmd(cmd_it->value.GetString(), cmd_it->value.GetStringLength());

    IpcCommand out{};

    if (cmd == "speed") {
        out.type = CmdType::Speed;
        auto val_it = doc.FindMember("value");
        if (val_it != doc.MemberEnd()) {
            if (val_it->value.IsDouble())
                out.float_value = val_it->value.GetDouble();
            else if (val_it->value.IsInt())
                out.float_value = static_cast<double>(val_it->value.GetInt());
            else if (val_it->value.IsUint())
                out.float_value = static_cast<double>(val_it->value.GetUint());
        }
        return out;
    }
    else if (cmd == "incline") {
        out.type = CmdType::Incline;
        auto val_it = doc.FindMember("value");
        if (val_it != doc.MemberEnd()) {
            if (val_it->value.IsInt())
                out.int_value = val_it->value.GetInt();
            else if (val_it->value.IsUint())
                out.int_value = static_cast<int>(val_it->value.GetUint());
            else if (val_it->value.IsDouble())
                out.int_value = static_cast<int>(val_it->value.GetDouble());
        }
        return out;
    }
    else if (cmd == "emulate") {
        out.type = CmdType::Emulate;
        auto val_it = doc.FindMember("enabled");
        if (val_it != doc.MemberEnd() && val_it->value.IsBool())
            out.bool_value = val_it->value.GetBool();
        return out;
    }
    else if (cmd == "proxy") {
        out.type = CmdType::Proxy;
        auto val_it = doc.FindMember("enabled");
        if (val_it != doc.MemberEnd() && val_it->value.IsBool())
            out.bool_value = val_it->value.GetBool();
        return out;
    }
    else if (cmd == "status") {
        out.type = CmdType::Status;
        return out;
    }
    else if (cmd == "heartbeat") {
        out.type = CmdType::Heartbeat;
        return out;
    }
    else if (cmd == "quit") {
        out.type = CmdType::Quit;
        return out;
    }

    return std::nullopt;
}

static std::string rj_to_string(rapidjson::StringBuffer& sb) {
    std::string result(sb.GetString(), sb.GetSize());
    result += '\n';
    return result;
}

std::string build_kv_event(const KvEvent& ev) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);

    w.StartObject();
    w.Key("type"); w.String("kv");
    w.Key("ts"); w.Double(ev.ts);
    w.Key("source"); w.String(ev.source.data(), static_cast<unsigned>(ev.source.size()));
    w.Key("key"); w.String(ev.key.data(), static_cast<unsigned>(ev.key.size()));
    w.Key("value"); w.String(ev.value.data(), static_cast<unsigned>(ev.value.size()));
    w.EndObject();

    return rj_to_string(sb);
}

std::string build_status_event(const StatusEvent& ev) {
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

    return rj_to_string(sb);
}

std::string build_error_event(std::string_view msg) {
    rapidjson::StringBuffer sb;
    rapidjson::Writer<rapidjson::StringBuffer> w(sb);

    w.StartObject();
    w.Key("type"); w.String("error");
    w.Key("msg"); w.String(msg.data(), static_cast<unsigned>(msg.size()));
    w.EndObject();

    return rj_to_string(sb);
}
