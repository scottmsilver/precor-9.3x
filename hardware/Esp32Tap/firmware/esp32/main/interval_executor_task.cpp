/*
 * interval_executor_task.cpp — the on-device standing owner (EXECUTOR
 * lease) and single writer of the native server tier: drains the API
 * RPC queue in <= 250 ms slices (WDT fed every wake), runs the 1 s
 * program/session tick, and surfaces safety-controller arbitration
 * (auto-proxy bounce) to the app.
 *
 * A stall here panics -> reset -> GPIO21 Hi-Z -> pull-down -> relay
 * released (PLAN watchdog matrix unchanged).
 */

#include "interval_executor_task.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"

#include "api_call.h"
#include "safety/safety_constants.h"
#include "server_context.h"
#include "wdt.h"

namespace esp32tap {

namespace {
const char* TAG = "esp32tap";
ServerContext* g_server_ctx = nullptr;
}  // namespace

void set_executor_server_context(ServerContext* sctx) {
    g_server_ctx = sctx;
}

void interval_executor_task(void* arg) {
    auto* ctx = static_cast<FirmwareContext*>(arg);
    if (!esp_hal::wdt_subscribe_current_task()) {
        // PLAN normative WDT matrix: this task must be supervised. Refuse
        // to run unsupervised — abort -> panic -> silent reboot -> GPIO21
        // Hi-Z -> R23 pull-down -> relay released (fail loud, matching
        // app_main's init failures).
        esp_system_abort("interval_exec: task WDT subscribe failed");
    }
    ESP_LOGI(TAG, "interval_executor task started (WDT-supervised)");

    ServerContext* sctx = g_server_ctx;
    int64_t next_tick_us = ctx->clock.now_us() + 1'000'000;
    uint32_t seconds = 0;
    bool prev_emulate = false;
    bool ws_ever_attached = false;
    int64_t last_ws_seen_us = 0;

    for (;;) {
        esp_hal::wdt_feed();

        // (i) API RPC slice: block <= 250 ms (well under the 2 s WDT).
        if (sctx != nullptr && sctx->api_queue != nullptr) {
            net::ApiCall* call = nullptr;
            if (xQueueReceive(sctx->api_queue, &call, pdMS_TO_TICKS(250)) ==
                pdTRUE) {
                if (call->ws_connect) {
                    // Fire-and-forget (no semaphore): build the hello
                    // frames and hand them to the transport outbox.
                    if (sctx->ready.load(std::memory_order_acquire)) {
                        sctx->core.connect_frames(call->frames);
                    }
                    net::ws_send_hello(call->ws_fd, call->ws_session,
                                       std::move(call->frames));
                    net::ApiCall::release(call);
                } else {
                    if (!sctx->ready.load(std::memory_order_acquire)) {
                        call->resp = {
                            503, "{\"error\":\"treadmill_io disconnected\"}"};
                    } else {
                        call->resp = api::handle_request(
                            sctx->core, call->method, call->path, call->body);
                    }
                    // Signal FIRST, then drop our reference: the handler
                    // may have timed out and released its own, in which
                    // case this release is the one that frees the call.
                    xSemaphoreGive(call->done);
                    net::ApiCall::release(call);
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(250));
        }

        // (ii) Auto-proxy bounce detection — sampled EVERY wake
        // (~250 ms), not only at the 1 s tick: an EMULATING-family ->
        // PROXY edge while a session runs means the hardware/watchdog
        // took the belt (console stop button, heartbeat loss). The 1 Hz
        // tick would miss short-lived Emulate phases (python is
        // event-driven on every C++ status message — this is the
        // polled equivalent).
        if (sctx != nullptr && sctx->ready.load(std::memory_order_acquire)) {
            bool emulate_now = false;
            bool console_fresh = false;
            {
                std::lock_guard<std::mutex> lk(ctx->controller_mu);
                using safety::SafeMode;
                SafeMode m = ctx->controller.mode();
                emulate_now = m == SafeMode::EMULATING ||
                              m == SafeMode::ENTRY_WAIT_GAP ||
                              m == SafeMode::ENTRY_WAIT_FEEDBACK;
                auto last = ctx->controller.last_complete_console_frame_at();
                console_fresh =
                    last.has_value() &&
                    (ctx->clock.now_us() - *last) < safety::CONSOLE_FRESH_US;
            }
            if (prev_emulate && !emulate_now) {
                // POST /api/emulate {"enabled":false} and POST /api/proxy
                // {"enabled":true} both leave the EMULATING family via
                // request_normal_exit — the SAME edge a console takeover
                // produces. Reporting a server-initiated exit as a
                // hardware takeover would pause the user's program and
                // show "Console took over" for a change the app itself
                // made, so ask the model whose exit this was.
                if (sctx->model.consume_server_initiated_exit(
                        ctx->clock.now_us())) {
                    ESP_LOGI(TAG, "server-initiated proxy exit (no bounce)");
                } else {
                    ESP_LOGI(TAG, "auto-proxy bounce (console_fresh=%d)",
                             console_fresh ? 1 : 0);
                    sctx->core.handle_auto_proxy(console_fresh);
                }
            }
            prev_emulate = emulate_now;
        }

        // (iii) 1 s scheduler boundary.
        int64_t now = ctx->clock.now_us();
        if (now < next_tick_us) continue;
        next_tick_us += 1'000'000;
        if (now >= next_tick_us) next_tick_us = now + 1'000'000;  // catch up

        if (sctx != nullptr && sctx->ready.load(std::memory_order_acquire)) {
            sctx->core.program_tick();
            sctx->core.session_tick();
            // WS "kv" frames (server.py re-enqueues every kv event —
            // the app's only continuous WS traffic; feeds the Debug
            // screen and the incremental status.motor merge). Diffed
            // and capped per tick so the no-PSRAM part never fans out
            // more than a handful of small frames per second.
            sctx->core.kv_tick();

            // Network dead-man (PLAN failure-matrix "WSS drop" row for
            // the standalone HTTP surface): once ANY WS client has
            // attached this boot, a program left driving the belt with
            // every client gone for the grace period is paused (belt to
            // 0). REST-only operation (no client ever attached) is
            // unaffected. The console, TREAD_OK and the 3 h inactivity
            // zero remain the lower-layer backstops.
            constexpr int64_t WS_LOSS_GRACE_US = 10'000'000;  // 10 s
            if (net::ws_client_count() > 0) {
                ws_ever_attached = true;
                last_ws_seen_us = now;
            } else if (ws_ever_attached &&
                       now - last_ws_seen_us > WS_LOSS_GRACE_US) {
                sctx->core.handle_client_loss();  // no-op unless running
            }
        }

        seconds++;
        if (seconds % 5 == 0) {
            // Cold-path liveness heartbeat on the debug console (UART0,
            // never the treadmill bus). tools/qemu_smoke.sh uses the log
            // timestamps to prove >=15 s of panic-free guest uptime.
            ESP_LOGI(TAG, "heartbeat uptime=%us",
                     static_cast<unsigned>(seconds));
        }
    }
}

}  // namespace esp32tap
