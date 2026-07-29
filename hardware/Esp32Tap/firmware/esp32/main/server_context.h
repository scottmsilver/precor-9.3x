/*
 * server_context.h — device wiring for the native server tier: the
 * executor-owned ServerCore + stores, the RPC/persist queues, and the
 * DeviceModel bridging ServerCore's hardware port to the
 * SafetyController (under controller_mu — single-writer: only the
 * interval executor task calls into DeviceModel).
 */

#pragma once

#include <array>
#include <atomic>
#include <ctime>
#include <mutex>
#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include "esp_timer.h"

#include "freertos/task.h"

#include "esp_log.h"

#include "fs_api.h"
#include "history_store.h"
#include "json_store.h"
#include "persist_queue.h"
#include "profile_store.h"
#include "run_store.h"
#include "server_core.h"
#include "time_source.h"
#include "transport_httpd.h"
#include "workout_store.h"

#include "firmware_context.h"

namespace esp32tap {

class DeviceTime : public exec::TimeSource {
public:
    int64_t now_us() override { return esp_timer_get_time(); }
    std::string now_iso() override {
        // Wall clock: std::time(). net_server_task starts SNTP as soon
        // as the netif is up, so a device that can reach an NTP server
        // renders real dates. OFFLINE the clock is seconds-since-boot
        // rendered as 1970 and RESTARTS every boot — which is why store
        // ORDERING never depends on these strings: JsonArrayStore stamps
        // every entry with a persisted monotonic "seq" and the stores
        // sort on that (see json_store.h). The ISO strings remain purely
        // display data for the app.
        std::time_t t = std::time(nullptr);
        std::tm tm_buf{};
        localtime_r(&t, &tm_buf);
        std::array<char, 24> buf{};
        std::strftime(buf.data(), buf.size(), "%Y-%m-%dT%H:%M:%S", &tm_buf);
        return std::string(buf.data());
    }
};

// PersistSink that hands writes to the core-1 storage task so the
// executor never blocks on flash.
//
// DURABILITY. The old implementation was a FIFO of heap items with
// drop-on-full, justified by "next save rewrites the file". That is
// false for a write-once store: program_history.json is written exactly
// once when a workout is loaded, so one drop lost the entry forever
// (the N6 crash-durability scenario). Writes are now COALESCED PER
// PATH by storage::PersistQueue — one slot per store file, a newer
// serialization supersedes an older pending one, and an unrelated
// store's traffic can never evict another store's only write. The
// wake-token queue is sized to the slot count, so posting a token can
// itself never fail.
class QueuePersist : public storage::PersistSink {
public:
    QueueHandle_t queue = nullptr;  // wake tokens (uint8_t), depth MAX_SLOTS

    void persist(const std::string& path, std::string&& content) override {
        if (queue == nullptr) return;
        storage::PersistQueue::Stage r;
        {
            std::lock_guard<std::mutex> lk(mu_);
            r = q_.stage(path, std::move(content));
        }
        if (r == storage::PersistQueue::Stage::DROPPED) {
            // Unreachable while distinct store paths <= MAX_SLOTS; kept
            // loud so adding a sixth store cannot silently lose writes.
            ESP_LOGE("esp32tap", "persist slots exhausted: %s", path.c_str());
            return;
        }
        if (r == storage::PersistQueue::Stage::QUEUED_NEW) {
            uint8_t token = 0;
            xQueueSend(queue, &token, 0);
        }
    }

    // Storage task side: pull the oldest pending write.
    bool take(std::string& path, std::string& content) {
        std::lock_guard<std::mutex> lk(mu_);
        return q_.take(path, content);
    }

private:
    std::mutex mu_;
    storage::PersistQueue q_;
};

class DeviceModel : public api::ServerModel {
public:
    DeviceModel(FirmwareContext& fw, DeviceTime& time)
        : fw_(fw), time_(time) {}

    api::StatusSnapshot status() override {
        api::StatusSnapshot st;
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        using safety::SafeMode;
        SafeMode m = fw_.controller.mode();
        st.emulate = m == SafeMode::EMULATING ||
                     m == SafeMode::ENTRY_WAIT_GAP ||
                     m == SafeMode::ENTRY_WAIT_FEEDBACK;
        st.proxy = !st.emulate;
        st.emu_speed_tenths = fw_.controller.speed_tenths();
        st.emu_incline_half = fw_.controller.incline_half_percent();
        st.bus_speed_tenths = -1;  // decoded via motor KV fallback
        st.bus_incline_half = -1;
        st.treadmill_connected = true;  // we ARE the treadmill I/O
        st.kv_count = 0;
        fw_.motor_kv.snapshot([&st](std::string_view k, std::string_view v) {
            if (st.kv_count >= api::StatusSnapshot::MAX_KV) return;
            auto& slot = st.kv.at(static_cast<size_t>(st.kv_count));
            k.copy(slot.key.data(), slot.key.size() - 1);
            v.copy(slot.val.data(), slot.val.size() - 1);
            st.kv_count++;
        });
        return st;
    }

    bool hw_set_speed(double mph) override {
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        int64_t now = fw_.clock.now_us();
        // Clamp in the DOUBLE domain first: an out-of-range double ->
        // int conversion is UB, and the clamp must never depend on it.
        double m = mph;
        if (!(m > 0.0)) m = 0.0;  // NaN/negative -> 0
        if (m > 12.0) m = 12.0;   // python clamp parity (120 tenths)
        int tenths = static_cast<int>(m * 10.0 + 0.5);
        if (tenths > safety::SPEED_MAX_TENTHS) {
            tenths = safety::SPEED_MAX_TENTHS;
        }
        bool ok = false;
        if (ensure_lease_locked(now)) {
            // Snapshot the OTHER axis BEFORE requesting Emulate entry:
            // SafetyController::request_emulate zeroes both axes on
            // success, so reading it afterwards would silently command
            // 0 incline instead of the value the server had set.
            int keep_incline = fw_.controller.incline_half_percent();
            if (tenths > 0) request_emulate_locked(now);
            ok = fw_.controller.command_motion(owner_, tenths, keep_incline,
                                               now);
        }
        if (!ok && tenths == 0) {
            // The STOP path must be unconditional: if motion authority
            // is refused (lease held by another owner, stale identity,
            // latched fault), escalate — emergency_stop zeroes motion,
            // releases the lease, and returns to Proxy. Strictly
            // monotonic toward safe (it can only stop the belt), so
            // reporting success is correct: the belt IS stopped.
            fw_.controller.emergency_stop("server_stop_refused", now);
            ok = true;
        }
        fw_.apply_outputs_locked();
        return ok;
    }

    bool hw_set_incline(double pct) override {
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        int64_t now = fw_.clock.now_us();
        double p = pct;
        if (!(p > 0.0)) p = 0.0;  // NaN/negative -> 0
        if (p > 15.0) p = 15.0;   // app-layer clamp parity
        int half = static_cast<int>(p * 2.0 + 0.5);
        if (half > safety::INCLINE_APP_MAX_HALF) {
            half = safety::INCLINE_APP_MAX_HALF;
        }
        bool ok = false;
        if (ensure_lease_locked(now)) {
            // Snapshot the OTHER axis BEFORE requesting Emulate entry
            // (see hw_set_speed): request_emulate zeroes speed_tenths_
            // on success, so reading it after would enter EMULATING at
            // 0 mph while ProgramState still believes the interval is
            // running at its commanded speed — the belt silently stops
            // for a whole interval with a 200 on the wire.
            int keep_speed = fw_.controller.speed_tenths();
            if (half > 0) request_emulate_locked(now);
            ok = fw_.controller.command_motion(owner_, keep_speed, half, now);
        }
        // Unconditional, like every other path here: ensure_lease_locked
        // can itself mutate controller state (connect() may trip
        // emergency_stop), so returning early would leave the hardware
        // outputs out of sync with the controller.
        fw_.apply_outputs_locked();
        return ok;
    }

    bool set_emulate(bool enabled) override {
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        int64_t now = fw_.clock.now_us();
        bool ok = ensure_lease_locked(now);
        if (ok) {
            if (enabled) {
                // Entry preconditions (console fresh, UART idle, gap)
                // may legitimately defer entry — not a failure.
                request_emulate_locked(now);
            } else {
                note_server_exit_locked(now);
                fw_.controller.request_normal_exit(owner_, now);
            }
        }
        fw_.apply_outputs_locked();
        return ok;
    }

    bool set_proxy(bool enabled) override {
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        int64_t now = fw_.clock.now_us();
        bool ok = ensure_lease_locked(now);
        if (ok && enabled) {
            note_server_exit_locked(now);
            fw_.controller.request_normal_exit(owner_, now);
        }
        // enabled=false: python clears only its proxy flag; on-device
        // the flag is derived from the mode — nothing to do.
        fw_.apply_outputs_locked();
        return ok;
    }

    // True when the Emulate->Proxy edge the executor just observed was
    // asked for by this server (POST /api/emulate {"enabled":false} or
    // POST /api/proxy {"enabled":true}) rather than by the hardware.
    // Consuming: a later genuine console takeover is never masked.
    // Single-threaded — only the executor task calls DeviceModel.
    bool consume_server_initiated_exit(int64_t now) {
        if (server_exit_at_us_ == 0) return false;
        bool fresh = (now - server_exit_at_us_) < SERVER_EXIT_WINDOW_US;
        server_exit_at_us_ = 0;
        return fresh;
    }

    exec::TimeSource& time_source() override { return time_; }

    void kv_snapshot(api::KvSink& sink) override {
        std::lock_guard<std::mutex> lk(fw_.controller_mu);
        auto feed = [&sink](const char* src, const MotorKvCache& cache) {
            cache.snapshot([&sink, src](std::string_view k,
                                        std::string_view v) {
                sink.kv(src, k, v);
            });
        };
        feed("motor", fw_.motor_kv);
        feed("console", fw_.console_kv);
        feed("emulate", fw_.emulate_kv);
    }

    void ws_broadcast(std::string&& json, api::WsKind kind) override {
        net::ws_send(std::move(json), kind);
    }

private:
    // Lazy lease: keeps the boot audit stream identical to phase 1 (the
    // QEMU behavioral scenarios script their own EXECUTOR lease).
    // Every (re)connect uses a FRESH generation: connect() rejects
    // generation <= highest-seen, so a constant generation would lock
    // this identity out forever after any reset-class stop cleared the
    // active table — the server tier would keep answering 200 while
    // permanently powerless (verifier critical). false == we do not
    // hold motion authority; the caller must not report success.
    bool ensure_lease_locked(int64_t now) {
        auto o = fw_.controller.owner();
        if (o.has_value() && o->same_connection(owner_)) {
            owner_.generation = o->generation;
            return true;
        }
        owner_.generation = ++last_generation_;
        if (!fw_.controller.connect(owner_)) return false;
        return fw_.controller.acquire(owner_, now);
    }

    // A server-requested normal exit walks EXIT_WAIT_GAP -> PROXY over
    // some milliseconds, so the edge lands on a later executor wake:
    // remember when we asked, and treat only a recent request as ours.
    static constexpr int64_t SERVER_EXIT_WINDOW_US = 5'000'000;
    void note_server_exit_locked(int64_t now) { server_exit_at_us_ = now; }

    void request_emulate_locked(int64_t now) {
        using safety::SafeMode;
        if (fw_.controller.mode() != SafeMode::PROXY) return;
        if (fw_.controller.relay_cmd() || fw_.controller.tx_enable()) return;
        fw_.controller.request_emulate(owner_, now,
                                       fw_.console_uart.tx_idle_low());
    }

    FirmwareContext& fw_;
    DeviceTime& time_;
    // handle 2: the QEMU shim owns EXECUTOR handle 1. generation is
    // rewritten by ensure_lease_locked on every (re)connect.
    safety::ConnectionIdentity owner_{safety::Transport::EXECUTOR, 2, 0};
    int64_t last_generation_ = 0;
    int64_t server_exit_at_us_ = 0;
};

// Between 4 KB write chunks on the core-1 storage task: one tick of
// delay so the (WDT-supervised) core-1 idle task always runs during a
// long LittleFS rewrite — see PosixFs yield_between_chunks.
inline void storage_write_yield() { vTaskDelay(1); }

struct ServerContext {
    DeviceTime time;
    storage::PosixFs fs{"/data", &storage_write_yield};
    QueuePersist persist;
    storage::HistoryStore history;
    storage::WorkoutStore workouts;
    storage::RunStore runs;
    storage::ProfileStore profiles;
    DeviceModel model;
    api::ServerCore core;
    QueueHandle_t api_queue = nullptr;
    QueueHandle_t persist_queue = nullptr;
    std::atomic<bool> ready{false};

    explicit ServerContext(FirmwareContext& fw)
        : model(fw, time),
          core(model, time, history, workouts, runs, profiles) {}
};

}  // namespace esp32tap
