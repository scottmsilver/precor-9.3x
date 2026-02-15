/*
 * mode_state.h — Single authority on proxy/emulate mode transitions
 *
 * Replaces the scattered volatile ints with a single state machine
 * that enforces mutual exclusion by construction (Mode enum = one value,
 * not two bools). All safety invariants (zero-on-emulate-start, clamping)
 * live here.
 */

#pragma once

#include <cstdint>
#include <string_view>
#include <mutex>
#include <atomic>
#include <functional>

// Speed/incline limits (mirrored in treadmill_client.py)
constexpr int MAX_SPEED_TENTHS = 120;   // 12.0 mph max
constexpr int MAX_INCLINE      = 99;

enum class Mode : uint8_t {
    Idle,       // Neither proxy nor emulate active
    Proxy,      // Forwarding console commands to motor
    Emulating   // Sending synthesized cycle to motor
};

// Lock-free snapshot for data plane reads
struct StateSnapshot {
    Mode mode;
    int speed_tenths;       // 0-120
    int speed_raw;          // speed_tenths * 10 (hundredths for hex encoding)
    int incline;            // 0-99
    bool proxy_enabled;
    bool emulate_enabled;
};

// Result of a mode transition request
struct TransitionResult {
    bool changed;           // true if mode actually changed
    bool emulate_started;   // true if emulate was just enabled
    bool emulate_stopped;   // true if emulate was just stopped
};

class ModeStateMachine {
public:
    using EmulateCallback = std::function<void(bool start)>;

    ModeStateMachine();

    // Set callback for emulate start/stop (controller starts/stops the thread)
    void set_emulate_callback(EmulateCallback cb);

    // --- Control plane operations (mutex-protected) ---

    TransitionResult request_proxy(bool enabled);
    TransitionResult request_emulate(bool enabled);

    // Set speed (auto-enables emulate, clamps 0-MAX_SPEED_TENTHS)
    TransitionResult set_speed(int tenths);
    // Same but from mph float (as received from IPC)
    TransitionResult set_speed_mph(double mph);

    // Set incline (auto-enables emulate, clamps 0-MAX_INCLINE)
    TransitionResult set_incline(int val);

    // Called from console read thread when hmph/inc value changes
    // while in emulate mode — switches back to proxy
    TransitionResult auto_proxy_on_console_change(std::string_view key,
                                                   std::string_view old_val,
                                                   std::string_view new_val);

    // Safety timeout: zeros speed/incline (called from emulate thread)
    void safety_timeout_reset();

    // Watchdog reset: zero speed/incline and exit emulate to proxy.
    // Does NOT fire the emulate callback — the emulate thread will
    // exit on its own when it checks is_emulating(). This is safe to
    // call from any thread (avoids double-join on emulate thread).
    void watchdog_reset_to_proxy();

    // Byte counters (not mode-related but shared state)
    void add_console_bytes(uint32_t n);
    void add_motor_bytes(uint32_t n);

    // --- Data plane reads (lock-free via atomic snapshot) ---

    StateSnapshot snapshot() const;

    // Individual atomic reads for hot paths
    bool is_proxy() const { return snap_.proxy_enabled; }
    bool is_emulating() const { return snap_.emulate_enabled; }
    int speed_tenths() const { return snap_.speed_tenths; }
    int speed_raw() const { return snap_.speed_raw; }
    int incline() const { return snap_.incline; }

    uint32_t console_bytes() const { return console_bytes_.load(std::memory_order_relaxed); }
    uint32_t motor_bytes() const { return motor_bytes_.load(std::memory_order_relaxed); }

private:
    void enter_emulate_locked();  // zeros speed/incline, sets mode
    void exit_emulate_locked();   // clears mode

    mutable std::mutex mu_;
    Mode mode_ = Mode::Proxy;
    int speed_tenths_ = 0;
    int speed_raw_ = 0;
    int incline_ = 0;

    // Atomic snapshot for lock-free data plane reads
    // Updated under mu_ after every state change
    struct alignas(64) AtomicSnap {
        std::atomic<bool> proxy_enabled{true};
        std::atomic<bool> emulate_enabled{false};
        std::atomic<int> speed_tenths{0};
        std::atomic<int> speed_raw{0};
        std::atomic<int> incline{0};
    } snap_;

    void update_snap_locked();

    std::atomic<uint32_t> console_bytes_{0};
    std::atomic<uint32_t> motor_bytes_{0};

    EmulateCallback emulate_cb_;
};
