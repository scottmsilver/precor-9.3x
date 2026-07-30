/*
 * interval_executor_task.cpp — 1 s WDT-supervised executor stub.
 */

#include "interval_executor_task.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"

#include "wdt.h"

namespace esp32tap {

namespace {
const char* TAG = "esp32tap";
}  // namespace

void interval_executor_task(void* arg) {
    auto* ctx = static_cast<FirmwareContext*>(arg);
    (void)ctx;
    if (!esp_hal::wdt_subscribe_current_task()) {
        // PLAN normative WDT matrix: this task must be supervised. Refuse
        // to run unsupervised — abort -> panic -> silent reboot -> GPIO21
        // Hi-Z -> R23 pull-down -> relay released (fail loud, matching
        // app_main's init failures).
        esp_system_abort("interval_exec: task WDT subscribe failed");
    }
    ESP_LOGI(TAG, "interval_executor task started (WDT-supervised)");

    uint32_t seconds = 0;
    for (;;) {
        esp_hal::wdt_feed();
        // TODO(M5): interval program execution (EXECUTOR-lease owner,
        // ProgramState port). The task is created and WDT-fed now so the
        // watchdog supervision matrix has its final shape: a stall here
        // panics -> reset -> GPIO21 Hi-Z -> pull-down -> relay released.
        vTaskDelay(pdMS_TO_TICKS(1000));
        seconds++;
        if (seconds % 5 == 0) {
            // Cold-path liveness heartbeat on the debug console (UART0,
            // never the treadmill bus). tools/qemu_smoke.sh uses the log
            // timestamps to prove >=15 s of panic-free guest uptime.
            ESP_LOGI(TAG, "heartbeat uptime=%us", static_cast<unsigned>(seconds));
        }
    }
}

}  // namespace esp32tap
