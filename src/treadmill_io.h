/*
 * treadmill_io.h — TreadmillController: top-level wiring
 *
 * Owns all components: readers, writer, emulation engine, IPC server,
 * mode state machine, and ring buffer. Thread functions are methods.
 * Templated on GpioPort for testability.
 */

#pragma once

#include <cstdio>
#include <cstdint>
#include <ctime>
#include <csignal>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <atomic>

#include "ring_buffer.h"
#include "mode_state.h"
#include "serial_io.h"
#include "emulation_engine.h"
#include "ipc_server.h"
#include "ipc_protocol.h"
#include "kv_protocol.h"
#include "config.h"

// Heartbeat watchdog timeout: if emulating and no command received
// for this long, safety-reset and return to proxy.
constexpr int HEARTBEAT_TIMEOUT_SEC = 4;

template <typename Port>
class TreadmillController {
public:
    TreadmillController(Port& port, const GpioConfig& cfg)
        : port_(port)
        , cfg_(cfg)
        , console_reader_(port, cfg.console_read)
        , motor_reader_(port, cfg.motor_read)
        , motor_writer_(port, cfg.motor_write)
        , emu_engine_(motor_writer_, mode_)
        , ipc_(ring_)
    {
        clock_gettime(CLOCK_MONOTONIC, &start_ts_);
        last_cmd_time_ = start_ts_;
    }

    // Wire up all callbacks and start threads
    bool start() {
        // Mode state machine callback: start/stop emulate engine
        mode_.set_emulate_callback([this](bool start) {
            if (start) {
                emu_engine_.start();
            } else {
                emu_engine_.stop();
            }
        });

        // Emulation engine: push KV events to ring
        emu_engine_.on_kv_event([this](std::string_view key, std::string_view value) {
            push_kv_event("emulate", key, value);
        });

        // Console reader: proxy + parse + auto-detect
        console_reader_.on_raw([this](std::span<const uint8_t> data) {
            mode_.add_console_bytes(static_cast<uint32_t>(data.size()));
            // Proxy: forward raw bytes to motor (low latency)
            if (mode_.is_proxy() && !mode_.is_emulating()) {
                motor_writer_.write_bytes(data);
            }
        });

        console_reader_.on_kv([this](const KvPair& kv) {
            auto key = kv.key_view();
            auto value = kv.value_view();
            push_kv_event("console", key, value);

            // Auto-detect: console change while emulating -> switch to proxy
            if (key == "hmph") {
                auto result = mode_.auto_proxy_on_console_change(
                    key, last_console_hmph_, value);
                if (result.changed) {
                    std::fprintf(stderr, "[auto] console hmph changed %s -> %s, switching to proxy\n",
                                 last_console_hmph_.c_str(), std::string(value).c_str());
                    push_status();
                }
                last_console_hmph_ = value;
            } else if (key == "inc") {
                auto result = mode_.auto_proxy_on_console_change(
                    key, last_console_inc_, value);
                if (result.changed) {
                    std::fprintf(stderr, "[auto] console inc changed %s -> %s, switching to proxy\n",
                                 last_console_inc_.c_str(), std::string(value).c_str());
                    push_status();
                }
                last_console_inc_ = value;
            }
        });

        // Motor reader: parse only
        motor_reader_.on_raw([this](std::span<const uint8_t> data) {
            mode_.add_motor_bytes(static_cast<uint32_t>(data.size()));
        });

        motor_reader_.on_kv([this](const KvPair& kv) {
            auto key = kv.key_view();
            auto value = kv.value_view();
            // Decode motor bus values
            if (key == "hmph") {
                int decoded = decode_speed_hex(value);
                if (decoded >= 0) bus_speed_tenths_.store(decoded, std::memory_order_relaxed);
            } else if (key == "inc") {
                int decoded = decode_incline_hex(value);
                if (decoded >= 0) bus_incline_pct_.store(decoded, std::memory_order_relaxed);
            }
            push_kv_event("motor", key, value);
        });

        // IPC: dispatch commands
        ipc_.on_command([this](const IpcCommand& cmd) {
            handle_command(cmd);
        });

        // IPC: client disconnect watchdog (Layer 1)
        ipc_.on_client_disconnect([this](int remaining) {
            if (remaining == 0 && mode_.is_emulating()) {
                std::fprintf(stderr, "[watchdog] all clients disconnected — exiting emulate, returning to proxy\n");
                watchdog_reset();
            }
        });

        // Open serial readers
        if (!console_reader_.open()) {
            std::fprintf(stderr, "[console] serial read open failed\n");
            return false;
        }
        if (!motor_reader_.open()) {
            std::fprintf(stderr, "[motor] serial read open failed\n");
            return false;
        }

        // Create IPC socket
        if (!ipc_.create()) {
            std::fprintf(stderr, "Failed to create server socket\n");
            return false;
        }

        std::fprintf(stderr, "[ipc] listening on %s\n", SOCK_PATH);

        // Push initial status
        push_status();

        // Start threads
        running_.store(true, std::memory_order_relaxed);
        console_thread_ = std::thread(&TreadmillController::console_read_loop, this);
        motor_thread_ = std::thread(&TreadmillController::motor_read_loop, this);
        ipc_thread_ = std::thread(&TreadmillController::ipc_loop, this);

        return true;
    }

    // Signal shutdown and join all threads
    void stop() {
        running_.store(false, std::memory_order_relaxed);
        emu_engine_.stop();

        if (console_thread_.joinable()) console_thread_.join();
        if (motor_thread_.joinable()) motor_thread_.join();
        if (ipc_thread_.joinable()) ipc_thread_.join();

        console_reader_.close();
        motor_reader_.close();
        ipc_.shutdown();
    }

    bool is_running() const { return running_.load(std::memory_order_relaxed); }
    void request_shutdown() { running_.store(false, std::memory_order_relaxed); }

    // Expose for testing
    ModeStateMachine& mode() { return mode_; }
    RingBuffer<>& ring() { return ring_; }

private:
    static void sleep_ms(int ms) {
        struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
        nanosleep(&ts, nullptr);
    }

    double elapsed_sec() const {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        return (now.tv_sec - start_ts_.tv_sec) +
               (now.tv_nsec - start_ts_.tv_nsec) / 1e9;
    }

    void push_kv_event(std::string_view source, std::string_view key, std::string_view value) {
        KvEvent ev{source, key, value, elapsed_sec()};
        ring_.push(build_kv_event(ev));
    }

    void push_status() {
        auto snap = mode_.snapshot();
        StatusEvent ev{};
        ev.proxy = snap.proxy_enabled;
        ev.emulate = snap.emulate_enabled;
        ev.emu_speed = snap.speed_tenths;
        ev.emu_incline = snap.incline;
        ev.bus_speed = bus_speed_tenths_.load(std::memory_order_relaxed);
        ev.bus_incline = bus_incline_pct_.load(std::memory_order_relaxed);
        ev.console_bytes = mode_.console_bytes();
        ev.motor_bytes = mode_.motor_bytes();
        ring_.push(build_status_event(ev));
    }

    void handle_command(const IpcCommand& cmd) {
        // Every command is an implicit heartbeat
        clock_gettime(CLOCK_MONOTONIC, &last_cmd_time_);

        switch (cmd.type) {
            case CmdType::Proxy:
                mode_.request_proxy(cmd.bool_value);
                push_status();
                break;
            case CmdType::Emulate:
                mode_.request_emulate(cmd.bool_value);
                push_status();
                break;
            case CmdType::Speed:
                mode_.set_speed_mph(cmd.float_value);
                push_status();
                break;
            case CmdType::Incline:
                mode_.set_incline(cmd.int_value);
                push_status();
                break;
            case CmdType::Status:
                push_status();
                break;
            case CmdType::Heartbeat:
                // Timestamp already updated above; no further action needed
                break;
            case CmdType::Quit:
                running_.store(false, std::memory_order_relaxed);
                break;
            case CmdType::Unknown:
                break;
        }
    }

    void console_read_loop() {
        while (running_.load(std::memory_order_relaxed)) {
            if (console_reader_.poll() == 0) {
                sleep_ms(5);
            }
        }
    }

    void motor_read_loop() {
        while (running_.load(std::memory_order_relaxed)) {
            if (motor_reader_.poll() == 0) {
                sleep_ms(5);
            }
        }
    }

    void watchdog_reset() {
        // Use watchdog_reset_to_proxy() instead of request_emulate(false)
        // to avoid firing the emulate callback (which would join the emulate
        // thread from the IPC thread — racing with the main thread's stop()).
        // The emulate thread will exit naturally when it sees is_emulating()==false.
        mode_.watchdog_reset_to_proxy();
        push_status();
    }

    void ipc_loop() {
        while (running_.load(std::memory_order_relaxed)) {
            ipc_.poll();

            // Layer 2: heartbeat timeout watchdog
            if (mode_.is_emulating()) {
                struct timespec now;
                clock_gettime(CLOCK_MONOTONIC, &now);
                double since_cmd = (now.tv_sec - last_cmd_time_.tv_sec) +
                                   (now.tv_nsec - last_cmd_time_.tv_nsec) / 1e9;
                if (since_cmd > HEARTBEAT_TIMEOUT_SEC) {
                    std::fprintf(stderr, "[watchdog] heartbeat timeout (%.1fs) — exiting emulate, returning to proxy\n", since_cmd);
                    watchdog_reset();
                }
            }
        }
    }

    Port& port_;
    GpioConfig cfg_;
    struct timespec start_ts_{};
    struct timespec last_cmd_time_{};

    RingBuffer<> ring_;
    ModeStateMachine mode_;
    SerialReader<Port> console_reader_;
    SerialReader<Port> motor_reader_;
    SerialWriter<Port> motor_writer_;
    EmulationEngine<Port> emu_engine_;
    IpcServer ipc_;

    std::atomic<bool> running_{false};
    std::atomic<int> bus_speed_tenths_{-1};   // -1 = not yet received
    std::atomic<int> bus_incline_pct_{-1};    // -1 = not yet received
    std::thread console_thread_;
    std::thread motor_thread_;
    std::thread ipc_thread_;

    std::string last_console_hmph_;
    std::string last_console_inc_;
};
