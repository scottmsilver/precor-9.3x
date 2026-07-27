/*
 * esp32_safety_io.h — safety GPIO HAL implementation (ESP-IDF).
 *
 * RELAY_CMD (GPIO21) and TX_ENABLE (GPIO15) are driven LOW before
 * anything else at boot; both are hardware-AND-gated with TREAD_OK by
 * U6, and both have 10 k pull-downs so reset/crash/power-loss releases
 * the relay with zero firmware involvement.
 *
 * TREAD_OK_MCU (GPIO6) is INPUT-ONLY: never configured as an output
 * (R32 isolation exists so a misconfigured GPIO cannot override the
 * hardware interlock).
 */

#pragma once

#include "hal/hal.h"

namespace esp32tap::esp_hal {

class Esp32SafetyIo final : public hal::SafetyIo {
public:
    // Drives RELAY_CMD/TX_ENABLE low FIRST, then configures inputs.
    // Must be the first hardware init call in app_main.
    bool init();

    void set_relay_cmd(bool on) override;
    void set_tx_enable(bool on) override;
    bool tread_ok() override;
    bool k1_nc_high() override;
    bool k1_no_high() override;
    bool vbus_present() override;  // inverts active-low VBUS_PRESENT_N
    void set_status_led(bool on) override;

private:
    bool ready_ = false;
};

}  // namespace esp32tap::esp_hal
