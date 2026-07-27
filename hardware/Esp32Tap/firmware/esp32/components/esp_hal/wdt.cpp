#include "wdt.h"

#include "esp_task_wdt.h"

namespace esp32tap::esp_hal {

bool wdt_subscribe_current_task() {
    return esp_task_wdt_add(nullptr) == ESP_OK;
}

void wdt_feed() { esp_task_wdt_reset(); }

}  // namespace esp32tap::esp_hal
