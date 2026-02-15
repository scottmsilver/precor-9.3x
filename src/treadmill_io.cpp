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
#include "config.h"

static volatile sig_atomic_t g_running = 1;

static void sig_handler(int /*sig*/) {
    g_running = 0;
}

int main() {
    if (geteuid() != 0) {
        std::fprintf(stderr, "Error: must run as root (sudo ./treadmill_io)\n");
        return 1;
    }

    std::fprintf(stderr, "treadmill_io starting...\n");

    GpioConfig cfg;
    auto conf = load_gpio_config("gpio.json", &cfg);
    if (!conf.ok) {
        std::fprintf(stderr, "Error: %s\n", conf.error.c_str());
        return 1;
    }

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
