/*
 * config.h — GPIO config loader
 *
 * Reads gpio.json into a typed GpioConfig struct.
 * Validates all required fields. Testable in isolation.
 */

#pragma once

#include <cstdio>
#include <string>
#include <string_view>

// Suppress RapidJSON internal asserts (we check errors after parse)
#define RAPIDJSON_ASSERT(x) ((void)(x))
#define RAPIDJSON_HAS_CXX11_NOEXCEPT 1
#include <rapidjson/document.h>

struct GpioConfig {
    int console_read = -1;
    int motor_write  = -1;
    int motor_read   = -1;
};

struct ConfigResult {
    bool ok = false;
    std::string error;
};

static constexpr size_t MAX_CONFIG_SIZE = 4096;

// Parse a gpio config from a JSON string.
// Pure function — no I/O, fully testable.
inline ConfigResult parse_gpio_config(std::string_view json, GpioConfig* cfg) {
    ConfigResult result;
    *cfg = GpioConfig{};

    if (json.size() > MAX_CONFIG_SIZE) {
        result.error = "config exceeds maximum size";
        return result;
    }

    // RapidJSON needs null-terminated mutable buffer
    std::string buf(json);

    rapidjson::Document doc;
    doc.Parse(buf.c_str());
    if (doc.HasParseError() || !doc.IsObject()) {
        result.error = "invalid JSON";
        return result;
    }

    struct { const char* name; int* dest; } pins[] = {
        {"console_read", &cfg->console_read},
        {"motor_write",  &cfg->motor_write},
        {"motor_read",   &cfg->motor_read},
    };

    for (auto& pin : pins) {
        auto it = doc.FindMember(pin.name);
        if (it == doc.MemberEnd() || !it->value.IsObject()) {
            result.error = std::string("missing or invalid \"") + pin.name + "\" section";
            return result;
        }
        auto gpio_it = it->value.FindMember("gpio");
        if (gpio_it == it->value.MemberEnd() || !gpio_it->value.IsInt()) {
            result.error = std::string("missing or invalid \"gpio\" in \"") + pin.name + "\"";
            return result;
        }
        int val = gpio_it->value.GetInt();
        if (val < 0 || val > 53) {
            result.error = std::string("gpio ") + std::to_string(val) +
                           " out of range [0-53] in \"" + pin.name + "\"";
            return result;
        }
        *pin.dest = val;
    }

    result.ok = true;
    return result;
}

// Load gpio config from a file path. Thin I/O wrapper around parse_gpio_config.
inline ConfigResult load_gpio_config(std::string_view path, GpioConfig* cfg) {
    std::string path_str(path);  // fopen needs null-terminated
    FILE* f = std::fopen(path_str.c_str(), "r");
    if (!f) {
        return {false, std::string("cannot open ") + path_str};
    }
    char buf[MAX_CONFIG_SIZE];
    size_t n = std::fread(buf, 1, sizeof(buf) - 1, f);
    std::fclose(f);
    buf[n] = '\0';
    return parse_gpio_config(std::string_view(buf, n), cfg);
}
