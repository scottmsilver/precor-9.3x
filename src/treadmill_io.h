/*
 * treadmill_io.h — TreadmillController: top-level wiring
 *
 * Owns all components: readers, writer, emulation engine, IPC server,
 * mode state machine, and ring buffer. Thread functions are methods.
 * Templated on GpioPort for testability.
 */

#pragma once

#include <cstdio>
#include <cstring>
#include <ctime>
#include <csignal>
#include <thread>
#include <atomic>

#include "ring_buffer.h"
#include "mode_state.h"
#include "serial_io.h"
#include "emulation_engine.h"
#include "ipc_server.h"
#include "ipc_protocol.h"
#include "kv_protocol.h"

// GPIO pin config loaded from gpio.json
struct GpioConfig {
    int console_read = -1;
    int motor_write = -1;
    int motor_read = -1;
};

// Parse gpio.json and fill GpioConfig. Returns true on success.
bool load_gpio_config(const char* path, GpioConfig* cfg);

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
        emu_engine_.on_kv_event([this](const char* key, const char* value) {
            push_kv_event("emulate", key, value);
        });

        // Console reader: proxy + parse + auto-detect
        console_reader_.on_raw([this](const uint8_t* data, int len) {
            mode_.add_console_bytes(len);
            // Proxy: forward raw bytes to motor (low latency)
            if (mode_.is_proxy() && !mode_.is_emulating()) {
                motor_writer_.write_bytes(data, len);
            }
        });

        console_reader_.on_kv([this](const KvPair& kv) {
            push_kv_event("console", kv.key, kv.value);

            // Auto-detect: console change while emulating -> switch to proxy
            if (std::strcmp(kv.key, "hmph") == 0) {
                auto result = mode_.auto_proxy_on_console_change(
                    kv.key, last_console_hmph_, kv.value);
                if (result.changed) {
                    std::fprintf(stderr, "[auto] console hmph changed %s -> %s, switching to proxy\n",
                                 last_console_hmph_, kv.value);
                    push_status();
                }
                std::snprintf(last_console_hmph_, sizeof(last_console_hmph_), "%s", kv.value);
            } else if (std::strcmp(kv.key, "inc") == 0) {
                auto result = mode_.auto_proxy_on_console_change(
                    kv.key, last_console_inc_, kv.value);
                if (result.changed) {
                    std::fprintf(stderr, "[auto] console inc changed %s -> %s, switching to proxy\n",
                                 last_console_inc_, kv.value);
                    push_status();
                }
                std::snprintf(last_console_inc_, sizeof(last_console_inc_), "%s", kv.value);
            }
        });

        // Motor reader: parse only
        motor_reader_.on_raw([this](const uint8_t* /*data*/, int len) {
            mode_.add_motor_bytes(len);
        });

        motor_reader_.on_kv([this](const KvPair& kv) {
            push_kv_event("motor", kv.key, kv.value);
        });

        // IPC: dispatch commands
        ipc_.on_command([this](const IpcCommand& cmd) {
            handle_command(cmd);
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

    void push_kv_event(const char* source, const char* key, const char* value) {
        char msg[256];
        KvEvent ev{source, key, value, elapsed_sec()};
        build_kv_event(ev, msg, sizeof(msg));
        ring_.push(msg);
    }

    void push_status() {
        auto snap = mode_.snapshot();
        StatusEvent ev{};
        ev.proxy = snap.proxy_enabled;
        ev.emulate = snap.emulate_enabled;
        ev.emu_speed = snap.speed_tenths;
        ev.emu_incline = snap.incline;
        ev.console_bytes = mode_.console_bytes();
        ev.motor_bytes = mode_.motor_bytes();
        char msg[256];
        build_status_event(ev, msg, sizeof(msg));
        ring_.push(msg);
    }

    void handle_command(const IpcCommand& cmd) {
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

    void ipc_loop() {
        while (running_.load(std::memory_order_relaxed)) {
            ipc_.poll();
        }
    }

    Port& port_;
    GpioConfig cfg_;
    struct timespec start_ts_{};

    RingBuffer<> ring_;
    ModeStateMachine mode_;
    SerialReader<Port> console_reader_;
    SerialReader<Port> motor_reader_;
    SerialWriter<Port> motor_writer_;
    EmulationEngine<Port> emu_engine_;
    IpcServer ipc_;

    std::atomic<bool> running_{false};
    std::thread console_thread_;
    std::thread motor_thread_;
    std::thread ipc_thread_;

    char last_console_hmph_[32]{};
    char last_console_inc_[32]{};
};
