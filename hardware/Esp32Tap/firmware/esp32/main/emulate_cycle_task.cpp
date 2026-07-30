/*
 * emulate_cycle_task.cpp — 14-key emulate burst loop.
 *
 * Per-iteration decisions (arm / force-proxy / mirror / send) live in
 * portable_core engine/emulate_task_policy.h so the host suite tests the
 * exact logic this task executes — in particular PLAN entry step 6: the
 * first transmitted burst after emulate entry encodes hmph=0/inc=0 even
 * if the owner commanded motion during the entry window.
 */

#include "emulate_cycle_task.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"

#include "engine/emulate_task_policy.h"
#include "wdt.h"

namespace esp32tap {

namespace {

const char* TAG = "esp32tap";

constexpr TickType_t kBurstGapTicks = pdMS_TO_TICKS(EMU_BURST_GAP_MS);
static_assert(kBurstGapTicks > 0,
              "CONFIG_FREERTOS_HZ too low: pdMS_TO_TICKS(EMU_BURST_GAP_MS) "
              "== 0 turns the emulate cycle loop into a busy spin");

}  // namespace

void emulate_cycle_task(void* arg) {
    auto* ctx = static_cast<FirmwareContext*>(arg);
    if (!esp_hal::wdt_subscribe_current_task()) {
        // PLAN normative WDT matrix: this task must be supervised. Refuse
        // to run unsupervised — abort -> panic -> silent reboot -> GPIO21
        // Hi-Z -> R23 pull-down -> relay released (fail loud, matching
        // app_main's init failures).
        esp_system_abort("emulate_cycle: task WDT subscribe failed");
    }
    ESP_LOGI(TAG, "emulate_cycle task started (WDT-supervised)");

    EmulateTaskPolicy policy;

    for (;;) {
        esp_hal::wdt_feed();
        EmulateTaskPolicy::Decision d;
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            using safety::SafeMode;
            bool controller_emulating =
                ctx->controller.mode() == SafeMode::EMULATING;
            d = policy.step(controller_emulating, ctx->mode.is_emulating());
            if (d.arm) {
                // Controller finished the gap-safe entry: arm the cycle
                // engine at zero (enter_emulate_locked zeroes motion —
                // the first burst is the entry zero frame, PLAN entry
                // step 6).
                ctx->mode.request_emulate(true);
                ctx->emulate_cycle.reset(ctx->clock.now_us());
            } else if (d.force_proxy) {
                ctx->mode.watchdog_reset_to_proxy();
            }
            if (d.mirror) {
                // Owner-commanded motion lives in the safety controller;
                // mirror it into the cycle parameter engine (clamped
                // again by ModeStateMachine). Deferred by the policy
                // until the first post-entry zero burst went out.
                ctx->mode.set_speed(ctx->controller.speed_tenths());
                ctx->mode.set_incline(
                    ctx->controller.incline_half_percent());
            }
        }
        if (d.send_burst) {
            // TX happens outside controller_mu (uart_wait_tx_done can
            // block ~50 ms at 9600 baud); the writer has its own mutex.
            bool sent = ctx->emulate_cycle.tick(ctx->clock.now_us());
            if (sent) {
                policy.on_burst_sent();
            }
            if (ctx->emulate_cycle.consume_safety_timeout()) {
                // The 3-hour inactivity timeout zeroed the cycle engine
                // (wire frames are already zero). Zero the authoritative
                // controller too, BEFORE the next iteration's mirror, so
                // status/FTMS never report stale motion and the mirror
                // cannot re-instate it (Pi parity: cpp/ zeroes its single
                // authoritative state).
                std::lock_guard<std::mutex> lk(ctx->controller_mu);
                ctx->controller.safety_timeout_zero_motion(
                    ctx->clock.now_us());
            }
        }
        vTaskDelay(kBurstGapTicks);
    }
}

}  // namespace esp32tap
