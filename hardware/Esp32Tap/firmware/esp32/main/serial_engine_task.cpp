/*
 * serial_engine_task.cpp — UART drain + safety controller update loop.
 *
 * Coarse cadence is 5 ms (vTaskDelay); relay transfers get a dedicated
 * sub-ms sampling window (portable_core safety/feedback_window.h) because
 * the 10 ms feedback deadline + 1 ms continuous-stable requirement is
 * unsatisfiable at 5 ms sampling.
 *
 * RX is polled, not event-queue driven (PLAN lists the UART event queue
 * as the M2 target): at 9600 baud the hardware 128-byte FIFO plus the
 * 1024-byte driver ring buffer hold >1 s of traffic, so a 5 ms poll can
 * never drop bytes; silence on both taps (bench rig idle, QEMU) is a
 * normal Proxy condition and simply yields zero-byte reads. Moving to
 * uart_driver_install's event queue is deliberate M2 work, gated with
 * the bench-capture gap qualification (GAP_QUALIFY_US).
 */

#include "serial_engine_task.h"

#include <array>
#include <optional>
#include <string_view>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_system.h"

#include "engine/key_cache.h"
#include "safety/feedback_window.h"
#include "safety/safety_constants.h"
#include "wdt.h"

namespace esp32tap {

namespace {

const char* TAG = "esp32tap";

constexpr TickType_t kLoopDelayTicks = pdMS_TO_TICKS(5);
// CONFIG_FREERTOS_HZ must be high enough that the 5 ms loop delay does
// not truncate to 0 ticks: vTaskDelay(0) is a busy spin, and this
// prio-10 core-0 task would then starve app_main (the other supervised
// tasks are never created) and the core-0 idle task (2 s task-WDT panic,
// silent reboot, forever). sdkconfig.defaults sets CONFIG_FREERTOS_HZ=1000;
// this guard makes a regression a compile error instead of a boot loop.
static_assert(kLoopDelayTicks > 0,
              "CONFIG_FREERTOS_HZ too low: pdMS_TO_TICKS(5) == 0 turns the "
              "serial engine loop into a busy spin (set CONFIG_FREERTOS_HZ=1000)");

}  // namespace

void serial_engine_task(void* arg) {
    auto* ctx = static_cast<FirmwareContext*>(arg);
    if (!esp_hal::wdt_subscribe_current_task()) {
        // PLAN normative WDT matrix: this task must be supervised. Refuse
        // to run unsupervised — abort -> panic -> silent reboot -> GPIO21
        // Hi-Z -> R23 pull-down -> relay released (fail loud, matching
        // app_main's init failures).
        esp_system_abort("serial_engine: task WDT subscribe failed");
    }
    ESP_LOGI(TAG, "serial_engine task started (WDT-supervised)");

    static KeyCache key_cache;  // static: keeps task stack small

    // VBUS_PRESENT_N edge detector: the safety model's
    // set_vbus_present_n is edge-driven (its harness calls it on level
    // changes) and logs an audit event per call. Calling it every 5 ms
    // iteration would push 200 events/s and wrap the 256-slot audit ring
    // in ~1.3 s, evicting emergency/fault events. std::nullopt forces
    // one call on the first sample to establish the real level.
    std::optional<bool> last_vbus_level_n;

    ctx->console_reader.on_raw([ctx](std::span<const uint8_t> raw) {
        // Called from poll() below while controller_mu is held.
        int64_t now = ctx->clock.now_us();
        ctx->last_console_rx_us = now;
        ctx->controller.observe_console_bytes(raw, now);
        ctx->mode.add_console_bytes(static_cast<uint32_t>(raw.size()));
    });
    ctx->console_reader.on_kv([ctx](const KvPair& kv) {
        // prev_buf is caller-owned so the view returned by exchange()
        // stays valid for the whole auto_proxy call (KeyCache lifetime
        // contract — see key_cache.h).
        std::array<char, KV_FIELD_SIZE> prev_buf;
        std::string_view prev =
            key_cache.exchange(kv.key_view(), kv.value_view(), prev_buf);
        auto result = ctx->mode.auto_proxy_on_console_change(
            kv.key_view(), prev, kv.value_view());
        if (result.emulate_stopped) {
            // Console button pressed while emulating: the user takes
            // over. Immediate stop is never less safe than staying in
            // Emulate. TODO(M3): use the gap-safe normal exit when the
            // console is healthy and an owner is present.
            ctx->controller.emergency_stop("console_takeover",
                                           ctx->clock.now_us());
        }
    });
    ctx->motor_reader.on_raw([ctx](std::span<const uint8_t> raw) {
        ctx->mode.add_motor_bytes(static_cast<uint32_t>(raw.size()));
    });

    for (;;) {
        esp_hal::wdt_feed();
        {
            std::lock_guard<std::mutex> lk(ctx->controller_mu);
            int64_t now = ctx->clock.now_us();

            // Hardware permission + relay feedback samples first.
            ctx->controller.set_tread_ok(ctx->safety_io.tread_ok(), now);
            ctx->controller.observe_relay_feedback(
                ctx->safety_io.k1_nc_high(), ctx->safety_io.k1_no_high(),
                now);
            bool vbus_level_n = !ctx->safety_io.vbus_present();
            if (!last_vbus_level_n.has_value() ||
                *last_vbus_level_n != vbus_level_n) {
                ctx->controller.set_vbus_present_n(vbus_level_n);
                last_vbus_level_n = vbus_level_n;
            }

            // Drain both taps (raw/kv callbacks update the controller).
            ctx->console_reader.poll();
            ctx->motor_reader.poll();

            // Console inter-frame gap qualification for relay transfers.
            // TODO(M2): GAP_QUALIFY_US is a placeholder pending bench
            // capture qualification.
            now = ctx->clock.now_us();
            using safety::SafeMode;
            auto m = ctx->controller.mode();
            if ((m == SafeMode::ENTRY_WAIT_GAP ||
                 m == SafeMode::EXIT_WAIT_GAP) &&
                now - ctx->last_console_rx_us >= safety::GAP_QUALIFY_US) {
                ctx->controller.observe_interframe_gap(now);
            }

            ctx->controller.tick(now);
            ctx->apply_outputs_locked();

            // Relay transfer in flight: the 10 ms feedback deadline
            // cannot be met at the 5 ms cadence, so run the dedicated
            // sub-ms sampling window until the controller either
            // qualifies the transfer or fails it closed at its own
            // deadline (see safety/feedback_window.h). Bounded to
            // ~RELAY_FEEDBACK_DEADLINE_US, well under the 2 s task WDT.
            if (safety::in_feedback_wait(ctx->controller)) {
                safety::run_feedback_window(
                    ctx->controller,
                    [ctx] { return ctx->clock.now_us(); },
                    [ctx] { return ctx->safety_io.k1_nc_high(); },
                    [ctx] { return ctx->safety_io.k1_no_high(); },
                    [ctx] { ctx->apply_outputs_locked(); },
                    [] {
                        esp_rom_delay_us(
                            static_cast<uint32_t>(safety::FEEDBACK_POLL_US));
                    });
            }

            // Keep the cycle parameter engine consistent with the
            // authoritative safety controller.
            if (ctx->controller.mode() != SafeMode::EMULATING &&
                ctx->mode.is_emulating()) {
                ctx->mode.watchdog_reset_to_proxy();
            }
        }
        vTaskDelay(kLoopDelayTicks);
    }
}

}  // namespace esp32tap
