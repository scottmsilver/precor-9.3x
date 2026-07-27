/*
 * esp32_safety_io.cpp — safety GPIO HAL implementation (ESP-IDF).
 */

#include "esp32_safety_io.h"

#include "driver/gpio.h"

#include "pins.hpp"

namespace esp32tap::esp_hal {

namespace {

gpio_num_t pin(int n) { return static_cast<gpio_num_t>(n); }

// Value-init then assign (not designated init): IDF adds gpio_config_t
// fields across versions and -Werror=missing-field-initializers is on.
bool configure_output_low(int io) {
    // Level first, then direction: the pad goes push-pull already low.
    if (gpio_set_level(pin(io), 0) != ESP_OK) return false;
    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << io;
    cfg.mode = GPIO_MODE_OUTPUT;
    cfg.pull_up_en = GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    if (gpio_config(&cfg) != ESP_OK) return false;
    return gpio_set_level(pin(io), 0) == ESP_OK;
}

bool configure_input(int io) {
    // No internal pulls: every input has its board-level resistor
    // (R25/R26/R30 pull-ups, R21/R22 on TREAD_OK, R7/R8 series taps).
    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << io;
    cfg.mode = GPIO_MODE_INPUT;
    cfg.pull_up_en = GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&cfg) == ESP_OK;
}

}  // namespace

bool Esp32SafetyIo::init() {
    // Outputs low BEFORE anything else (boot = Proxy, relay released).
    bool ok = configure_output_low(pins::kRelayCmd);
    ok = configure_output_low(pins::kTxEnable) && ok;
    ok = configure_output_low(pins::kStatusLed) && ok;
    // Inputs. TREAD_OK_MCU is deliberately never given an output mode
    // anywhere in this file — input-only by construction.
    ok = configure_input(pins::kTreadOkMcu) && ok;
    ok = configure_input(pins::kK1NcFb) && ok;
    ok = configure_input(pins::kK1NoFb) && ok;
    ok = configure_input(pins::kVbusPresentN) && ok;
    ready_ = ok;
    return ok;
}

void Esp32SafetyIo::set_relay_cmd(bool on) {
    gpio_set_level(pin(pins::kRelayCmd), on ? 1 : 0);
}

void Esp32SafetyIo::set_tx_enable(bool on) {
    gpio_set_level(pin(pins::kTxEnable), on ? 1 : 0);
}

bool Esp32SafetyIo::tread_ok() {
    return gpio_get_level(pin(pins::kTreadOkMcu)) != 0;
}

bool Esp32SafetyIo::k1_nc_high() {
    return gpio_get_level(pin(pins::kK1NcFb)) != 0;
}

bool Esp32SafetyIo::k1_no_high() {
    return gpio_get_level(pin(pins::kK1NoFb)) != 0;
}

bool Esp32SafetyIo::vbus_present() {
    // GPIO7 is active-low: LOW means VBUS present (PLAN "USB attach").
    return gpio_get_level(pin(pins::kVbusPresentN)) == 0;
}

void Esp32SafetyIo::set_status_led(bool on) {
    gpio_set_level(pin(pins::kStatusLed), on ? 1 : 0);
}

}  // namespace esp32tap::esp_hal
