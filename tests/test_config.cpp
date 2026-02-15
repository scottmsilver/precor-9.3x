/*
 * test_config.cpp — Tests for GPIO config parsing
 */

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#define DOCTEST_CONFIG_NO_EXCEPTIONS
#include <doctest.h>
#include "config.h"

// ── Valid config ────────────────────────────────────────────────────

TEST_CASE("parse valid gpio.json") {
    const char* json = R"({
        "console_read": {"gpio": 27, "physical_pin": 13, "description": "test", "direction": "in"},
        "motor_write":  {"gpio": 22, "physical_pin": 15, "description": "test", "direction": "out"},
        "motor_read":   {"gpio": 17, "physical_pin": 11, "description": "test", "direction": "in"}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK(result.ok);
    CHECK(cfg.console_read == 27);
    CHECK(cfg.motor_write == 22);
    CHECK(cfg.motor_read == 17);
}

TEST_CASE("parse minimal config (only gpio fields)") {
    const char* json = R"({
        "console_read": {"gpio": 4},
        "motor_write":  {"gpio": 5},
        "motor_read":   {"gpio": 6}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK(result.ok);
    CHECK(cfg.console_read == 4);
    CHECK(cfg.motor_write == 5);
    CHECK(cfg.motor_read == 6);
}

// ── Missing sections ────────────────────────────────────────────────

TEST_CASE("missing console_read section") {
    const char* json = R"({
        "motor_write": {"gpio": 22},
        "motor_read":  {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "console_read") != nullptr);
}

TEST_CASE("missing motor_write section") {
    const char* json = R"({
        "console_read": {"gpio": 27},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "motor_write") != nullptr);
}

TEST_CASE("missing motor_read section") {
    const char* json = R"({
        "console_read": {"gpio": 27},
        "motor_write":  {"gpio": 22}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "motor_read") != nullptr);
}

// ── Missing gpio field ──────────────────────────────────────────────

TEST_CASE("section exists but no gpio field") {
    const char* json = R"({
        "console_read": {"physical_pin": 13},
        "motor_write":  {"gpio": 22},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "gpio") != nullptr);
    CHECK(std::strstr(result.error, "console_read") != nullptr);
}

// ── Invalid values ──────────────────────────────────────────────────

TEST_CASE("gpio value out of range (negative)") {
    const char* json = R"({
        "console_read": {"gpio": -1},
        "motor_write":  {"gpio": 22},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "out of range") != nullptr);
}

TEST_CASE("gpio value out of range (too high)") {
    const char* json = R"({
        "console_read": {"gpio": 27},
        "motor_write":  {"gpio": 100},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "out of range") != nullptr);
}

TEST_CASE("gpio value is string not int") {
    const char* json = R"({
        "console_read": {"gpio": "27"},
        "motor_write":  {"gpio": 22},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "gpio") != nullptr);
}

TEST_CASE("section is not an object") {
    const char* json = R"({
        "console_read": 27,
        "motor_write":  {"gpio": 22},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "console_read") != nullptr);
}

// ── Malformed JSON ──────────────────────────────────────────────────

TEST_CASE("empty string") {
    GpioConfig cfg;
    auto result = parse_gpio_config("", &cfg);
    CHECK_FALSE(result.ok);
    CHECK(std::strstr(result.error, "invalid JSON") != nullptr);
}

TEST_CASE("not JSON") {
    GpioConfig cfg;
    auto result = parse_gpio_config("hello world", &cfg);
    CHECK_FALSE(result.ok);
}

TEST_CASE("empty object") {
    GpioConfig cfg;
    auto result = parse_gpio_config("{}", &cfg);
    CHECK_FALSE(result.ok);
}

TEST_CASE("JSON array instead of object") {
    GpioConfig cfg;
    auto result = parse_gpio_config("[1,2,3]", &cfg);
    CHECK_FALSE(result.ok);
}

// ── Boundary values ─────────────────────────────────────────────────

TEST_CASE("gpio 0 is valid") {
    const char* json = R"({
        "console_read": {"gpio": 0},
        "motor_write":  {"gpio": 1},
        "motor_read":   {"gpio": 2}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK(result.ok);
    CHECK(cfg.console_read == 0);
}

TEST_CASE("gpio 53 is valid (max Pi pin)") {
    const char* json = R"({
        "console_read": {"gpio": 53},
        "motor_write":  {"gpio": 22},
        "motor_read":   {"gpio": 17}
    })";
    GpioConfig cfg;
    auto result = parse_gpio_config(json, &cfg);
    CHECK(result.ok);
    CHECK(cfg.console_read == 53);
}
