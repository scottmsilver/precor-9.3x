/*
 * app_main.cpp — Esp32Tap firmware entry point (phase 1: safety core).
 *
 * Boot order (PLAN normative-safe):
 *   1. Safety IO init — RELAY_CMD/TX_ENABLE driven LOW first, feedback /
 *      TREAD_OK inputs configured; safety controller constructed in
 *      PROXY with Feedback::UNKNOWN (boot feedback is unknown until the
 *      first real GPIO sample).
 *   2. UARTs configured inverted 9600 8N1.
 *   3. Task WDT is system-initialized (CONFIG_ESP_TASK_WDT_INIT=y, 2 s,
 *      panic) before app_main runs.
 *   4. Three supervised tasks created, each esp_task_wdt_add()s itself
 *      and feeds every loop: serial engine, emulate cycle, interval
 *      executor.
 *
 * A stall in any supervised task panics -> silent reboot -> GPIO21 goes
 * Hi-Z -> R23 pull-down -> relay released. The hardware completes the
 * guarantee; there is deliberately no software "WDT handler".
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "emulate_cycle_task.h"
#include "firmware_context.h"
#include "interval_executor_task.h"
#include "net_server_task.h"
#include "serial_engine_task.h"
#include "server_context.h"
#include "tiers/ftms_stub.h"
#include "tiers/hrm_stub.h"

namespace {

const char* TAG = "esp32tap";

// Static, not on the main-task stack: FirmwareContext embeds multi-KB
// parse buffers (PLAN's QEMU-validated stack constraint).
esp32tap::FirmwareContext g_ctx;

// Native server tier state (executor-owned ServerCore + stores + RPC
// queues). Static for the same stack reason.
esp32tap::ServerContext g_server_ctx{g_ctx};

// BLE tiers (FTMS/HRM) are compiled but disabled — separate workflow.
constexpr bool kBleTiersEnabled = false;

}  // namespace

extern "C" void app_main(void) {
    // (1) Safety outputs low before anything else.
    if (!g_ctx.safety_io.init()) {
        // Outputs are pulled down in hardware; refuse to start the
        // engine on a half-configured board.
        ESP_LOGE(TAG, "safety IO init failed — halting in Proxy");
        for (;;) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }

    // (2) Inverted UARTs.
    bool uarts_ok = g_ctx.console_uart.init();
    uarts_ok = g_ctx.motor_tap.init() && uarts_ok;
    if (!uarts_ok) {
        ESP_LOGE(TAG, "UART init failed — halting in Proxy");
        for (;;) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }

    // Seed the controller with a first real feedback/permission sample.
    {
        std::lock_guard<std::mutex> lk(g_ctx.controller_mu);
        int64_t now = g_ctx.clock.now_us();
        g_ctx.controller.set_tread_ok(g_ctx.safety_io.tread_ok(), now);
        g_ctx.controller.observe_relay_feedback(
            g_ctx.safety_io.k1_nc_high(), g_ctx.safety_io.k1_no_high(), now);
        g_ctx.apply_outputs_locked();
        // Boot-state audit line (asserted by tools/qemu_smoke.sh): boot
        // must be Proxy with the relay released and TX disabled.
        ESP_LOGI(TAG, "boot state: mode=%s relay=%s tx_enable=%d fault=%d",
                 g_ctx.controller.mode() == esp32tap::safety::SafeMode::PROXY
                     ? "PROXY"
                     : "NOT_PROXY",
                 g_ctx.controller.relay_cmd() ? "energized" : "released",
                 g_ctx.controller.tx_enable() ? 1 : 0,
                 g_ctx.controller.fault_latched() ? 1 : 0);
    }

    // (4) Supervised tasks — all pinned to core 0 (core 1 belongs to
    // the network tiers per PLAN's task layout). The executor gets the
    // server context first (RPC queue + ServerCore single writer);
    // 8 KB stack: rapidjson serialization happens on this task.
#if defined(ESP32TAP_NET)
    esp32tap::set_executor_server_context(&g_server_ctx);
#endif
    xTaskCreatePinnedToCore(esp32tap::serial_engine_task, "serial_engine",
                            8192, &g_ctx, 10, nullptr, 0);
    xTaskCreatePinnedToCore(esp32tap::emulate_cycle_task, "emulate_cycle",
                            6144, &g_ctx, 9, nullptr, 0);
    xTaskCreatePinnedToCore(esp32tap::interval_executor_task,
                            "interval_exec", 16384, &g_ctx, 5, nullptr, 0);

    // (5) Native server tier: core-1 bring-up (storage -> netif -> TLS
    // -> httpd:8000 -> mDNS). Never blocks or gates the safety tasks.
    //
    // ESP32TAP_NET gates the tier at RUNTIME (the code is still compiled
    // and still string-gated by test_default_build.py). Two reasons:
    //  1. The behavioral image (tools/qemu_harness S1-S7) measures
    //     MICROSECOND-scale safety deadlines — RELAY_FEEDBACK_DEADLINE_US
    //     is 10 ms. WiFi/lwIP/mbedTLS/mDNS tasks sharing the emulated SoC
    //     steal guest CPU inside that window; the safety core must be
    //     measured on its own, exactly as the committed harness assumes.
    //  2. Without the tier there is no LittleFS "storage" partition to
    //     mount, so the behavioral image fits the stock 2 MB / single-app
    //     flash layout the committed harness pads to.
    // The network scenarios build their own image with ESP32TAP_NET=1
    // (tools/build_images.sh build_qemu_net).
#if defined(ESP32TAP_NET)
    esp32tap::start_net_server_task(&g_server_ctx);
#endif

#if defined(ESP32TAP_QEMU_TEST)
    // Behavioral-harness shim task (tools/qemu_harness). The banner makes
    // a test image unmistakable: scripted safety IO, motor tap on UART0.
    ESP_LOGW(TAG, "esp32tap QEMU-TEST build (never flash to hardware)");
    esp32tap::qemu_test::start_qemu_test_task(&g_ctx);
#endif

    if (kBleTiersEnabled) {
        esp32tap::tiers::FtmsTier::start();
        esp32tap::tiers::HrmTier::start();
    }

    ESP_LOGI(TAG, "esp32tap phase-1 safety core started (Proxy)");
}
