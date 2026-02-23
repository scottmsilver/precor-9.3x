/*
 * test_ipc_protocol.cpp — Tests for JSON command parsing and event building
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "ipc_protocol.h"
#include <string>

// ── Command parsing tests ───────────────────────────────────────────

TEST_CASE("parse speed command") {
    auto cmd = parse_command("{\"cmd\":\"speed\",\"value\":1.2}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Speed);
    CHECK(cmd->float_value == doctest::Approx(1.2));
}

TEST_CASE("parse speed command with int value") {
    auto cmd = parse_command("{\"cmd\":\"speed\",\"value\":5}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Speed);
    CHECK(cmd->float_value == doctest::Approx(5.0));
}

TEST_CASE("parse incline command (int percent -> half-pct)") {
    auto cmd = parse_command("{\"cmd\":\"incline\",\"value\":5}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Incline);
    CHECK(cmd->int_value == 10);  // 5% * 2 = 10 half-pct
}

TEST_CASE("parse incline command with float value (half-pct conversion)") {
    auto cmd = parse_command("{\"cmd\":\"incline\",\"value\":3.5}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Incline);
    CHECK(cmd->int_value == 7);  // 3.5% * 2 = 7 half-pct
}

TEST_CASE("parse incline command with 5.5% (half-pct conversion)") {
    auto cmd = parse_command("{\"cmd\":\"incline\",\"value\":5.5}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Incline);
    CHECK(cmd->int_value == 11);  // 5.5% * 2 = 11 half-pct
}

TEST_CASE("parse emulate enable") {
    auto cmd = parse_command("{\"cmd\":\"emulate\",\"enabled\":true}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Emulate);
    CHECK(cmd->bool_value == true);
}

TEST_CASE("parse emulate disable") {
    auto cmd = parse_command("{\"cmd\":\"emulate\",\"enabled\":false}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Emulate);
    CHECK(cmd->bool_value == false);
}

TEST_CASE("parse proxy enable") {
    auto cmd = parse_command("{\"cmd\":\"proxy\",\"enabled\":true}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Proxy);
    CHECK(cmd->bool_value == true);
}

TEST_CASE("parse proxy disable") {
    auto cmd = parse_command("{\"cmd\":\"proxy\",\"enabled\":false}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Proxy);
    CHECK(cmd->bool_value == false);
}

TEST_CASE("parse status command") {
    auto cmd = parse_command("{\"cmd\":\"status\"}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Status);
}

TEST_CASE("parse heartbeat command") {
    auto cmd = parse_command("{\"cmd\":\"heartbeat\"}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Heartbeat);
}

TEST_CASE("parse quit command") {
    auto cmd = parse_command("{\"cmd\":\"quit\"}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Quit);
}

TEST_CASE("parse unknown command") {
    CHECK_FALSE(parse_command("{\"cmd\":\"foobar\"}").has_value());
}

TEST_CASE("parse missing cmd field") {
    CHECK_FALSE(parse_command("{\"value\":123}").has_value());
}

TEST_CASE("parse empty object") {
    CHECK_FALSE(parse_command("{}").has_value());
}

TEST_CASE("parse malformed JSON") {
    CHECK_FALSE(parse_command("not json at all").has_value());
}

TEST_CASE("parse empty string") {
    CHECK_FALSE(parse_command("").has_value());
}

TEST_CASE("parse speed without value field") {
    auto cmd = parse_command("{\"cmd\":\"speed\"}");
    CHECK(cmd.has_value());
    CHECK(cmd->type == CmdType::Speed);
    CHECK(cmd->float_value == doctest::Approx(0.0));
}

// ── Event building tests ────────────────────────────────────────────

TEST_CASE("build KV event") {
    KvEvent ev{"console", "hmph", "78", 1.23};
    auto result = build_kv_event(ev);

    CHECK(!result.empty());
    CHECK(result.find("\"type\":\"kv\"") != std::string::npos);
    CHECK(result.find("\"source\":\"console\"") != std::string::npos);
    CHECK(result.find("\"key\":\"hmph\"") != std::string::npos);
    CHECK(result.find("\"value\":\"78\"") != std::string::npos);
    CHECK(result.find("\"ts\":") != std::string::npos);
    CHECK(result.back() == '\n');  // newline terminated
}

TEST_CASE("build status event") {
    // emu_incline and bus_incline are in half-pct units
    StatusEvent ev{true, false, 12, 10, 42, 14, 1234, 567};
    auto result = build_status_event(ev);

    CHECK(!result.empty());
    CHECK(result.find("\"type\":\"status\"") != std::string::npos);
    CHECK(result.find("\"proxy\":true") != std::string::npos);
    CHECK(result.find("\"emulate\":false") != std::string::npos);
    CHECK(result.find("\"emu_speed\":12") != std::string::npos);
    CHECK(result.find("\"emu_incline\":10") != std::string::npos);
    CHECK(result.find("\"bus_speed\":42") != std::string::npos);
    CHECK(result.find("\"bus_incline\":14") != std::string::npos);
    CHECK(result.find("\"console_bytes\":1234") != std::string::npos);
    CHECK(result.find("\"motor_bytes\":567") != std::string::npos);
    CHECK(result.back() == '\n');
}

TEST_CASE("build error event") {
    auto result = build_error_event("too many clients");

    CHECK(!result.empty());
    CHECK(result.find("\"type\":\"error\"") != std::string::npos);
    CHECK(result.find("\"msg\":\"too many clients\"") != std::string::npos);
    CHECK(result.back() == '\n');
}
