/*
 * test_ipc_protocol.cpp — Tests for JSON command parsing and event building
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "ipc_protocol.h"
#include <cstring>
#include <cstdio>

// Helper: parse a command from a string literal (copies to mutable buffer)
static bool parse(const char* json, IpcCommand* cmd) {
    char buf[1024];
    int len = std::snprintf(buf, sizeof(buf), "%s", json);
    return parse_command(buf, len, cmd);
}

// ── Command parsing tests ───────────────────────────────────────────

TEST_CASE("parse speed command") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"speed\",\"value\":1.2}", &cmd));
    CHECK(cmd.type == CmdType::Speed);
    CHECK(cmd.float_value == doctest::Approx(1.2));
}

TEST_CASE("parse speed command with int value") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"speed\",\"value\":5}", &cmd));
    CHECK(cmd.type == CmdType::Speed);
    CHECK(cmd.float_value == doctest::Approx(5.0));
}

TEST_CASE("parse incline command") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"incline\",\"value\":5}", &cmd));
    CHECK(cmd.type == CmdType::Incline);
    CHECK(cmd.int_value == 5);
}

TEST_CASE("parse incline command with float value truncates") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"incline\",\"value\":3.7}", &cmd));
    CHECK(cmd.type == CmdType::Incline);
    CHECK(cmd.int_value == 3);  // truncated (matches C sscanf %d behavior)
}

TEST_CASE("parse emulate enable") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"emulate\",\"enabled\":true}", &cmd));
    CHECK(cmd.type == CmdType::Emulate);
    CHECK(cmd.bool_value == true);
}

TEST_CASE("parse emulate disable") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"emulate\",\"enabled\":false}", &cmd));
    CHECK(cmd.type == CmdType::Emulate);
    CHECK(cmd.bool_value == false);
}

TEST_CASE("parse proxy enable") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"proxy\",\"enabled\":true}", &cmd));
    CHECK(cmd.type == CmdType::Proxy);
    CHECK(cmd.bool_value == true);
}

TEST_CASE("parse proxy disable") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"proxy\",\"enabled\":false}", &cmd));
    CHECK(cmd.type == CmdType::Proxy);
    CHECK(cmd.bool_value == false);
}

TEST_CASE("parse status command") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"status\"}", &cmd));
    CHECK(cmd.type == CmdType::Status);
}

TEST_CASE("parse quit command") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"quit\"}", &cmd));
    CHECK(cmd.type == CmdType::Quit);
}

TEST_CASE("parse unknown command") {
    IpcCommand cmd;
    CHECK_FALSE(parse("{\"cmd\":\"foobar\"}", &cmd));
}

TEST_CASE("parse missing cmd field") {
    IpcCommand cmd;
    CHECK_FALSE(parse("{\"value\":123}", &cmd));
}

TEST_CASE("parse empty object") {
    IpcCommand cmd;
    CHECK_FALSE(parse("{}", &cmd));
}

TEST_CASE("parse malformed JSON") {
    IpcCommand cmd;
    CHECK_FALSE(parse("not json at all", &cmd));
}

TEST_CASE("parse empty string") {
    IpcCommand cmd;
    CHECK_FALSE(parse("", &cmd));
}

TEST_CASE("parse speed without value field") {
    IpcCommand cmd;
    CHECK(parse("{\"cmd\":\"speed\"}", &cmd));
    CHECK(cmd.type == CmdType::Speed);
    CHECK(cmd.float_value == doctest::Approx(0.0));
}

// ── Event building tests ────────────────────────────────────────────

TEST_CASE("build KV event") {
    char buf[512];
    KvEvent ev{"console", "hmph", "78", 1.23};
    int len = build_kv_event(ev, buf, sizeof(buf));

    CHECK(len > 0);
    CHECK(std::strstr(buf, "\"type\":\"kv\"") != nullptr);
    CHECK(std::strstr(buf, "\"source\":\"console\"") != nullptr);
    CHECK(std::strstr(buf, "\"key\":\"hmph\"") != nullptr);
    CHECK(std::strstr(buf, "\"value\":\"78\"") != nullptr);
    CHECK(std::strstr(buf, "\"ts\":") != nullptr);
    CHECK(buf[len - 1] == '\n');  // newline terminated
}

TEST_CASE("build status event") {
    char buf[512];
    StatusEvent ev{true, false, 12, 5, 1234, 567};
    int len = build_status_event(ev, buf, sizeof(buf));

    CHECK(len > 0);
    CHECK(std::strstr(buf, "\"type\":\"status\"") != nullptr);
    CHECK(std::strstr(buf, "\"proxy\":true") != nullptr);
    CHECK(std::strstr(buf, "\"emulate\":false") != nullptr);
    CHECK(std::strstr(buf, "\"emu_speed\":12") != nullptr);
    CHECK(std::strstr(buf, "\"emu_incline\":5") != nullptr);
    CHECK(std::strstr(buf, "\"console_bytes\":1234") != nullptr);
    CHECK(std::strstr(buf, "\"motor_bytes\":567") != nullptr);
    CHECK(buf[len - 1] == '\n');
}

TEST_CASE("build error event") {
    char buf[512];
    int len = build_error_event("too many clients", buf, sizeof(buf));

    CHECK(len > 0);
    CHECK(std::strstr(buf, "\"type\":\"error\"") != nullptr);
    CHECK(std::strstr(buf, "\"msg\":\"too many clients\"") != nullptr);
    CHECK(buf[len - 1] == '\n');
}
