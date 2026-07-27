/*
 * wdt.h — esp_task_wdt helpers.
 *
 * The task WDT itself is initialized by the system at startup
 * (CONFIG_ESP_TASK_WDT_INIT=y, 2 s, CONFIG_ESP_TASK_WDT_PANIC=y —
 * PLAN.md normative). A stall in any subscribed task panics -> silent
 * reboot -> GPIO21 goes Hi-Z -> R23 pull-down releases the relay. The
 * firmware never needs (and must not have) a "WDT handler".
 */

#pragma once

namespace esp32tap::esp_hal {

// Subscribe the CALLING task to the task WDT. Returns false on error.
bool wdt_subscribe_current_task();

// Feed the WDT for the calling task. Call once per loop iteration.
void wdt_feed();

}  // namespace esp32tap::esp_hal
