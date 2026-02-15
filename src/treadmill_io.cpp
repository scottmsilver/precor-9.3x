/*
 * treadmill_io.cpp — main() + gpio.json loader
 *
 * Production binary instantiates TreadmillController<PigpioPort>.
 * Links libpigpio. Must run as root.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <unistd.h>
#include <ctime>

#include "gpio_pigpio.h"
#include "treadmill_io.h"

// RapidJSON for gpio.json parsing
#define RAPIDJSON_ASSERT(x) ((void)0)
#include <rapidjson/document.h>

static volatile sig_atomic_t g_running = 1;

static void sig_handler(int /*sig*/) {
    g_running = 0;
}

bool load_gpio_config(const char* path, GpioConfig* cfg) {
    FILE* f = std::fopen(path, "r");
    if (!f) {
        std::fprintf(stderr, "Error: cannot open %s\n", path);
        return false;
    }
    char buf[2048];
    size_t n = std::fread(buf, 1, sizeof(buf) - 1, f);
    std::fclose(f);
    buf[n] = '\0';

    rapidjson::Document doc;
    doc.Parse(buf);
    if (doc.HasParseError() || !doc.IsObject()) {
        std::fprintf(stderr, "Error: invalid JSON in %s\n", path);
        return false;
    }

    auto get_gpio = [&](const char* name) -> int {
        auto it = doc.FindMember(name);
        if (it == doc.MemberEnd() || !it->value.IsObject()) return -1;
        auto gpio_it = it->value.FindMember("gpio");
        if (gpio_it == it->value.MemberEnd() || !gpio_it->value.IsInt()) return -1;
        return gpio_it->value.GetInt();
    };

    cfg->console_read = get_gpio("console_read");
    cfg->motor_write  = get_gpio("motor_write");
    cfg->motor_read   = get_gpio("motor_read");

    if (cfg->console_read < 0 || cfg->motor_write < 0 || cfg->motor_read < 0) {
        std::fprintf(stderr, "Error: missing pins in %s (console_read=%d, motor_write=%d, motor_read=%d)\n",
                     path, cfg->console_read, cfg->motor_write, cfg->motor_read);
        return false;
    }
    return true;
}

int main() {
    if (geteuid() != 0) {
        std::fprintf(stderr, "Error: must run as root (sudo ./treadmill_io)\n");
        return 1;
    }

    std::fprintf(stderr, "treadmill_io starting...\n");

    GpioConfig cfg;
    if (!load_gpio_config("gpio.json", &cfg)) return 1;

    std::fprintf(stderr, "  Console read: GPIO %d\n", cfg.console_read);
    std::fprintf(stderr, "  Motor write:  GPIO %d\n", cfg.motor_write);
    std::fprintf(stderr, "  Motor read:   GPIO %d\n", cfg.motor_read);
    std::fprintf(stderr, "  Baud:         %d\n", BAUD);

    PigpioPort port;
    if (port.initialise() < 0) {
        std::fprintf(stderr, "Failed to initialize pigpio (is pigpiod running? kill it first)\n");
        return 1;
    }

    // Motor write pin: output, idle LOW (inverted RS-485)
    port.set_mode(cfg.motor_write, PORT_OUTPUT);
    port.write(cfg.motor_write, 0);

    std::signal(SIGINT, sig_handler);
    std::signal(SIGTERM, sig_handler);
    std::signal(SIGPIPE, SIG_IGN);

    TreadmillController<PigpioPort> controller(port, cfg);

    if (!controller.start()) {
        port.terminate();
        return 1;
    }

    std::fprintf(stderr, "treadmill_io ready (proxy=on)\n");

    while (g_running && controller.is_running()) {
        struct timespec ts = { 0, 200000000L };  // 200ms
        nanosleep(&ts, nullptr);
    }

    std::fprintf(stderr, "\nShutting down...\n");

    controller.stop();

    port.write(cfg.motor_write, 0);
    port.set_mode(cfg.motor_write, PORT_INPUT);
    port.terminate();

    std::fprintf(stderr, "treadmill_io stopped.\n");
    return 0;
}
