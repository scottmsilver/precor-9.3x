/*
 * ipc_protocol.h — Typed IPC command/event structs with JSON parsing
 *
 * Replaces all ad-hoc strstr()/sscanf() JSON parsing with typed
 * structs and RapidJSON. Used only on the IPC path (cold relative
 * to serial I/O).
 */

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

// --- Inbound commands (Python -> C++) ---

enum class CmdType : uint8_t {
    Speed,
    Incline,
    Emulate,
    Proxy,
    Status,
    Heartbeat,
    Quit,
    Unknown
};

struct IpcCommand {
    CmdType type = CmdType::Unknown;
    double float_value = 0.0;   // speed in mph
    int int_value = 0;          // incline value
    bool bool_value = false;    // emulate/proxy enabled
};

static constexpr size_t MAX_IPC_COMMAND_LEN = 1024;

/*
 * Parse a JSON command string into a typed IpcCommand.
 * Returns the parsed command, or std::nullopt on failure.
 */
std::optional<IpcCommand> parse_command(std::string_view json);

// --- Outbound events (C++ -> Python) ---

struct KvEvent {
    std::string_view source;  // "console", "motor", or "emulate"
    std::string_view key;
    std::string_view value;
    double ts;
};

struct StatusEvent {
    bool proxy;
    bool emulate;
    int emu_speed;
    int emu_incline;
    uint32_t console_bytes;
    uint32_t motor_bytes;
};

/*
 * Build JSON event strings into a std::string.
 */
std::string build_kv_event(const KvEvent& ev);
std::string build_status_event(const StatusEvent& ev);
std::string build_error_event(std::string_view msg);
