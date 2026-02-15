/*
 * ipc_protocol.h — Typed IPC command/event structs with JSON parsing
 *
 * Replaces all ad-hoc strstr()/sscanf() JSON parsing with typed
 * structs and RapidJSON. Used only on the IPC path (cold relative
 * to serial I/O).
 */

#pragma once

#include <cstdint>

// --- Inbound commands (Python -> C++) ---

enum class CmdType : uint8_t {
    Speed,
    Incline,
    Emulate,
    Proxy,
    Status,
    Quit,
    Unknown
};

struct IpcCommand {
    CmdType type = CmdType::Unknown;
    double float_value = 0.0;   // speed in mph
    int int_value = 0;          // incline value
    bool bool_value = false;    // emulate/proxy enabled
};

/*
 * Parse a JSON command string into a typed IpcCommand.
 * Uses RapidJSON in-situ parsing (modifies input buffer).
 * Returns true if a valid command was parsed.
 */
bool parse_command(char* json, int len, IpcCommand* out);

// --- Outbound events (C++ -> Python) ---

struct KvEvent {
    const char* source;  // "console", "motor", or "emulate"
    const char* key;
    const char* value;
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
 * Build a JSON KV event string.
 * Returns the number of bytes written (including trailing \n).
 */
int build_kv_event(const KvEvent& ev, char* out, int out_len);

/*
 * Build a JSON status event string.
 * Returns the number of bytes written (including trailing \n).
 */
int build_status_event(const StatusEvent& ev, char* out, int out_len);

/*
 * Build an error event string.
 * Returns the number of bytes written (including trailing \n).
 */
int build_error_event(const char* msg, char* out, int out_len);
