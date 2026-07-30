/*
 * esp32_time.h — monotonic microsecond clock (esp_timer_get_time).
 */

#pragma once

#include "hal/hal.h"

namespace esp32tap::esp_hal {

class Esp32Clock final : public hal::Clock {
public:
    int64_t now_us() override;
};

}  // namespace esp32tap::esp_hal
