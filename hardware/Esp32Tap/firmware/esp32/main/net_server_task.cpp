/*
 * net_server_task.cpp — see net_server_task.h.
 */

#include "net_server_task.h"

#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

#include "api_call.h"
#include "cert_manager.h"
#include "littlefs_mount.h"
#include "mdns_tier.h"
#include "netif_start.h"
#include "transport_httpd.h"

namespace esp32tap {

namespace {

const char* TAG = "esp32tap";

// WDT status (PLAN watchdog matrix): storage_task is deliberately NOT
// task-WDT-subscribed — it blocks indefinitely on the persist queue.
// It must nonetheless never starve the WDT-checked core-1 IDLE task
// (CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1=y), or a legitimate long
// LittleFS operation panics the device mid-run.
//
// Three mechanisms, split by what each one actually covers:
//   (a) 4 KB write chunks with a 1-tick yield between them (PosixFs
//       yield_between_chunks) — covers the fwrite loop only;
//   (b) the task DROPS ITS OWN PRIORITY to tskIDLE_PRIORITY for the
//       duration of each file write and restores it afterwards. At
//       idle priority, FreeRTOS time-slicing (configUSE_TIME_SLICING=1,
//       1 ms tick) round-robins core-1 idle in no matter what the task
//       is inside — which is what covers fclose(), rename() and
//       LittleFS garbage collection, none of which offer a yield point.
//   (c) OUTSIDE the write it runs at STORAGE_TASK_PRIO (above idle), so
//       a queued persist is picked up on the next scheduler decision
//       rather than waiting for a tick-driven round-robin slot against
//       the idle task. Running the WHOLE task at priority 0 (the
//       previous shape) made drain latency depend on emulated/loaded
//       tick delivery and left crash-durability writes unflushed for
//       tens of seconds.
// net_server_task self-deletes after bring-up.
constexpr UBaseType_t STORAGE_TASK_PRIO = 2;

void storage_task(void* arg) {
    auto* sctx = static_cast<ServerContext*>(arg);
    std::string path, content;
    for (;;) {
        uint8_t token = 0;
        // Wake on a token, but always drain by asking the coalescing
        // queue: tokens and pending slots are only loosely coupled (a
        // superseded write consumes no token), so the queue — not the
        // token count — is the source of truth.
        xQueueReceive(sctx->persist_queue, &token, portMAX_DELAY);
        while (sctx->persist.take(path, content)) {
            vTaskPrioritySet(nullptr, tskIDLE_PRIORITY);  // (b)
            bool ok = sctx->fs.write_file_atomic(path, content);
            vTaskPrioritySet(nullptr, STORAGE_TASK_PRIO);
            if (!ok) ESP_LOGW(TAG, "persist failed: %s", path.c_str());
            content.clear();
            content.shrink_to_fit();
            // Breather between back-to-back store rewrites.
            vTaskDelay(1);
        }
    }
}

void net_server_task(void* arg) {
    auto* sctx = static_cast<ServerContext*>(arg);

    // M5 measured-RAM gate input: heap before/after net bring-up.
    ESP_LOGI(TAG, "heap before net bring-up: %u free",
             static_cast<unsigned>(
                 heap_caps_get_free_size(MALLOC_CAP_DEFAULT)));

    // (1) /data + stores + crash recovery, then open the executor's
    // gate. Store init is single-threaded here; the executor only
    // touches stores after `ready` (release/acquire pair).
    bool fs_ok = storage::mount_data_partition();
    if (fs_ok) {
        sctx->history.init(sctx->fs, sctx->persist, "program_history.json");
        sctx->workouts.init(sctx->fs, sctx->persist, "saved_workouts.json");
        sctx->runs.init(sctx->fs, sctx->persist, "run_history.json");
        sctx->profiles.init_with_state(sctx->fs, sctx->persist,
                                       "profiles.json",
                                       "profile_state.json");
        int recovered = sctx->core.boot_recover_runs();
        if (recovered > 0) {
            ESP_LOGW(TAG, "recovered %d in-progress run(s) -> disconnect",
                     recovered);
        }
    } else {
        ESP_LOGE(TAG, "/data unavailable — serving with RAM-only stores");
        // Stores stay empty in-RAM; persist() writes will fail loudly.
        sctx->history.init(sctx->fs, sctx->persist, "program_history.json");
        sctx->workouts.init(sctx->fs, sctx->persist, "saved_workouts.json");
        sctx->runs.init(sctx->fs, sctx->persist, "run_history.json");
        sctx->profiles.init_with_state(sctx->fs, sctx->persist,
                                       "profiles.json",
                                       "profile_state.json");
    }
    sctx->ready.store(true, std::memory_order_release);

    // (2) Network. Failure at any tier leaves the belt fully
    // functional; the API tier simply stays down.
    if (!net::start_netif()) {
        ESP_LOGW(TAG, "netif down — server tier disabled this boot");
        vTaskDelete(nullptr);
        return;
    }

    // (3) TLS identity (EC P-256, first boot only), then httpd + mDNS.
    std::string cert_pem, key_pem;
    if (!net::ensure_device_cert(sctx->fs, cert_pem, key_pem)) {
        ESP_LOGE(TAG, "no TLS identity — server tier disabled this boot");
        vTaskDelete(nullptr);
        return;
    }
    if (!net::start_httpd(cert_pem, key_pem, sctx->api_queue)) {
        vTaskDelete(nullptr);
        return;
    }
    net::start_mdns();
    // Wall clock. Without this, std::time() is seconds-since-boot and
    // every created_at/updated_at/last_used/started_at/ended_at renders
    // as a 1970 date that RESTARTS on each boot. SNTP is fire-and-
    // forget (async, never blocks the API tier, harmless offline); it
    // fixes the DISPLAYED timestamps. Store ORDERING never depended on
    // them being right — JsonArrayStore stamps a persisted monotonic
    // "seq" and the stores sort on that, so an offline device with no
    // clock at all still orders correctly across reboots.
    net::start_sntp();

    ESP_LOGI(TAG, "heap after net bring-up: %u free",
             static_cast<unsigned>(
                 heap_caps_get_free_size(MALLOC_CAP_DEFAULT)));
    ESP_LOGI(TAG, "native server tier up (https://treadmill.local:8000)");
    vTaskDelete(nullptr);
}

}  // namespace

void start_net_server_task(ServerContext* sctx) {
    sctx->api_queue = xQueueCreate(8, sizeof(net::ApiCall*));
    // Wake tokens only — one slot per coalescing PersistQueue slot, so
    // posting a token can never fail and never needs a fallback.
    sctx->persist_queue =
        xQueueCreate(storage::PersistQueue::MAX_SLOTS, sizeof(uint8_t));
    sctx->persist.queue = sctx->persist_queue;
    // Storage: core 1 (see STORAGE_TASK_PRIO — above idle to drain
    // promptly, dropped to idle priority around each flash write).
    xTaskCreatePinnedToCore(storage_task, "storage", 6144, sctx,
                            STORAGE_TASK_PRIO, nullptr, 1);
    xTaskCreatePinnedToCore(net_server_task, "net_server", 8192, sctx, 4,
                            nullptr, 1);
}

}  // namespace esp32tap
