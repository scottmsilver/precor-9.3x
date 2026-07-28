/* CONTROL EXPERIMENT (C, not Rust): does the ESP-IDF task WDT panic
 * path fire at all under esp-QEMU?  Same shape as the Rust stall test:
 * a supervised task subscribes, feeds for 8 s, then stops forever.
 * CONFIG_ESP_TASK_WDT_TIMEOUT_S=2, so a working WDT must panic ~10 s in. */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"

static void stall_task(void *arg) {
    printf("wdtctl: stall_task subscribe=%d\n", esp_task_wdt_add(NULL) == ESP_OK);
    int64_t t0 = esp_timer_get_time();
    for (;;) {
        if (esp_timer_get_time() - t0 > 8000000) {
            printf("wdtctl: CEASING TO FEED NOW\n");
            for (;;) vTaskDelay(pdMS_TO_TICKS(3600000));
        }
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

static void heartbeat_task(void *arg) {
    int64_t t0 = esp_timer_get_time();
    for (;;) {
        printf("wdtctl: heartbeat uptime=%llds\n",
               (long long)((esp_timer_get_time() - t0) / 1000000));
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

void app_main(void) {
    printf("wdtctl: control experiment started\n");
    xTaskCreatePinnedToCore(stall_task, "stall", 4096, NULL, 10, NULL, 0);
    xTaskCreatePinnedToCore(heartbeat_task, "hb", 4096, NULL, 5, NULL, 0);
}
