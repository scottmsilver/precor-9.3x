/*
 * mode_state.cpp — Mode state machine implementation
 */

#include "mode_state.h"
#include <cstring>
#include <algorithm>

ModeStateMachine::ModeStateMachine() {}

void ModeStateMachine::set_emulate_callback(EmulateCallback cb) {
    emulate_cb_ = std::move(cb);
}

void ModeStateMachine::update_snap_locked() {
    snap_.proxy_enabled.store(mode_ == Mode::Proxy, std::memory_order_relaxed);
    snap_.emulate_enabled.store(mode_ == Mode::Emulating, std::memory_order_relaxed);
    snap_.speed_tenths.store(speed_tenths_, std::memory_order_relaxed);
    snap_.speed_raw.store(speed_raw_, std::memory_order_relaxed);
    snap_.incline.store(incline_, std::memory_order_relaxed);
}

void ModeStateMachine::enter_emulate_locked() {
    // Safety: always start emulate at 0 speed, 0 incline
    speed_tenths_ = 0;
    speed_raw_ = 0;
    incline_ = 0;
    mode_ = Mode::Emulating;
    update_snap_locked();
}

void ModeStateMachine::exit_emulate_locked() {
    mode_ = Mode::Idle;
    update_snap_locked();
}

TransitionResult ModeStateMachine::request_proxy(bool enabled) {
    std::lock_guard<std::mutex> lk(mu_);
    TransitionResult result{};

    if (enabled) {
        if (mode_ == Mode::Emulating) {
            exit_emulate_locked();
            result.emulate_stopped = true;
        }
        mode_ = Mode::Proxy;
        update_snap_locked();
        result.changed = true;
    } else {
        if (mode_ == Mode::Proxy) {
            mode_ = Mode::Idle;
            update_snap_locked();
            result.changed = true;
        }
    }

    // Fire callback outside the lock would be better, but the emulate
    // thread lifecycle is managed by the controller, so we fire here
    // and the callback must not re-enter mode_state.
    if (result.emulate_stopped && emulate_cb_) {
        emulate_cb_(false);
    }

    return result;
}

TransitionResult ModeStateMachine::request_emulate(bool enabled) {
    TransitionResult result{};

    {
        std::lock_guard<std::mutex> lk(mu_);
        if (enabled) {
            if (mode_ == Mode::Emulating) return result;  // already emulating
            mode_ = Mode::Idle;  // clear proxy first
            enter_emulate_locked();
            result.emulate_started = true;
            result.changed = true;
        } else {
            if (mode_ == Mode::Emulating) {
                exit_emulate_locked();
                result.emulate_stopped = true;
                result.changed = true;
            }
        }
    }

    if (result.emulate_started && emulate_cb_) {
        emulate_cb_(true);
    }
    if (result.emulate_stopped && emulate_cb_) {
        emulate_cb_(false);
    }

    return result;
}

TransitionResult ModeStateMachine::set_speed(int tenths) {
    TransitionResult result{};

    tenths = std::max(0, std::min(tenths, MAX_SPEED_TENTHS));

    {
        std::lock_guard<std::mutex> lk(mu_);
        // Auto-enable emulate when speed is set
        if (mode_ != Mode::Emulating) {
            mode_ = Mode::Idle;  // clear proxy
            enter_emulate_locked();
            result.emulate_started = true;
            result.changed = true;
        }
        speed_tenths_ = tenths;
        speed_raw_ = tenths * 10;
        update_snap_locked();
    }

    if (result.emulate_started && emulate_cb_) {
        emulate_cb_(true);
    }

    return result;
}

TransitionResult ModeStateMachine::set_speed_mph(double mph) {
    int tenths = static_cast<int>(mph * 10 + 0.5);
    return set_speed(tenths);
}

TransitionResult ModeStateMachine::set_incline(int val) {
    TransitionResult result{};

    val = std::max(0, std::min(val, MAX_INCLINE));

    {
        std::lock_guard<std::mutex> lk(mu_);
        // Auto-enable emulate when incline is set
        if (mode_ != Mode::Emulating) {
            mode_ = Mode::Idle;
            enter_emulate_locked();
            result.emulate_started = true;
            result.changed = true;
        }
        incline_ = val;
        update_snap_locked();
    }

    if (result.emulate_started && emulate_cb_) {
        emulate_cb_(true);
    }

    return result;
}

TransitionResult ModeStateMachine::auto_proxy_on_console_change(
    const char* key, const char* old_val, const char* new_val)
{
    TransitionResult result{};

    if (!old_val[0] || std::strcmp(old_val, new_val) == 0) {
        return result;  // no change or first value
    }

    // Only trigger for hmph and inc keys
    if (std::strcmp(key, "hmph") != 0 && std::strcmp(key, "inc") != 0) {
        return result;
    }

    {
        std::lock_guard<std::mutex> lk(mu_);
        if (mode_ != Mode::Emulating) return result;

        exit_emulate_locked();
        result.emulate_stopped = true;
        mode_ = Mode::Proxy;
        update_snap_locked();
        result.changed = true;
    }

    if (result.emulate_stopped && emulate_cb_) {
        emulate_cb_(false);
    }

    return result;
}

void ModeStateMachine::safety_timeout_reset() {
    std::lock_guard<std::mutex> lk(mu_);
    speed_tenths_ = 0;
    speed_raw_ = 0;
    incline_ = 0;
    update_snap_locked();
}

void ModeStateMachine::add_console_bytes(uint32_t n) {
    console_bytes_.fetch_add(n, std::memory_order_relaxed);
}

void ModeStateMachine::add_motor_bytes(uint32_t n) {
    motor_bytes_.fetch_add(n, std::memory_order_relaxed);
}

StateSnapshot ModeStateMachine::snapshot() const {
    StateSnapshot s{};
    s.proxy_enabled = snap_.proxy_enabled.load(std::memory_order_relaxed);
    s.emulate_enabled = snap_.emulate_enabled.load(std::memory_order_relaxed);
    s.speed_tenths = snap_.speed_tenths.load(std::memory_order_relaxed);
    s.speed_raw = snap_.speed_raw.load(std::memory_order_relaxed);
    s.incline = snap_.incline.load(std::memory_order_relaxed);
    s.mode = s.emulate_enabled ? Mode::Emulating
           : s.proxy_enabled   ? Mode::Proxy
           : Mode::Idle;
    return s;
}
