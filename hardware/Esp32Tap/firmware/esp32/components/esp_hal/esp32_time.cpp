#include "esp32_time.h"

#include "esp_timer.h"

namespace esp32tap::esp_hal {

int64_t Esp32Clock::now_us() { return esp_timer_get_time(); }

}  // namespace esp32tap::esp_hal
