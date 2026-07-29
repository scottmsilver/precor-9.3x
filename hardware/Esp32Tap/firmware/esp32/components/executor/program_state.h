/*
 * program_state.h — line-faithful port of python/program_engine.py
 * ProgramState (interval program execution) for the native server tier.
 *
 * Differences from the asyncio original (documented in PLAN.md note):
 *  - the 1 s asyncio tick loop becomes tick(), called at 1 Hz by the
 *    interval executor task; wall-clock math uses int64 microseconds
 *    from the injected TimeSource instead of time.monotonic() floats;
 *  - on_change/on_update callbacks become a ProgramEvents interface set
 *    once by the executor (python passes them per-start);
 *  - the every-3-intervals encouragement uses a rotating index instead
 *    of random.choice (deterministic on-device);
 *  - fixed-capacity Program (MAX_INTERVALS): when a manual split would
 *    exceed the cap the current interval is updated in place.
 */

#pragma once

#include <cstdint>
#include <string>

#include "program_model.h"
#include "time_source.h"

namespace esp32tap::exec {

class ProgramEvents {
public:
    virtual ~ProgramEvents() = default;
    // Apply interval targets to the hardware (mph / percent).
    virtual void on_change(double speed_mph, double incline_pct) = 0;
    // Broadcast the program state (caller serializes via to_json before
    // ProgramState drains the pending encouragement).
    virtual void on_update() = 0;
};

class ProgramState {
public:
    explicit ProgramState(TimeSource& ts) : ts_(ts) {}

    void set_events(ProgramEvents* ev) { events_ = ev; }

    // --- observable state (python attribute parity) ---
    bool has_program() const { return has_program_; }
    const Program& program() const { return program_; }
    Program& mutable_program() { return program_; }
    bool running() const { return running_; }
    bool paused() const { return paused_; }
    bool completed() const { return completed_; }
    int current_interval() const { return current_interval_; }
    int interval_elapsed() const { return interval_elapsed_; }
    int total_elapsed() const { return total_elapsed_; }
    int total_duration() const {
        return has_program_ ? program_.total_duration() : 0;
    }
    bool is_manual() const { return has_program_ && program_.manual; }
    const Interval* current_iv() const;
    const std::string& pending_encouragement() const {
        return pending_encouragement_;
    }

    // --- operations (python method parity) ---
    void load(const Program& p);
    void start(int resume_interval = 0, int resume_elapsed = 0);
    void stop();
    void reset();
    void toggle_pause();
    void skip();
    void prev();
    bool extend_current(int seconds);
    bool split_for_manual(double speed, double incline);
    bool adjust_duration(int delta_seconds);
    // One 1 s scheduler tick (the _tick_loop body). No-op unless running.
    void tick();

    // server.py _handle_auto_proxy parity: set paused directly (no
    // on_change re-apply, no broadcast) — emulate is already off.
    void pause_silently() {
        if (paused_) return;
        paused_ = true;
        pause_start_us_ = ts_.now_us();
        pause_start_valid_ = true;
    }

    // Bounce injection (auto-proxy pause messages) + milestone helpers.
    void set_pending_encouragement(std::string_view msg) {
        pending_encouragement_.assign(msg.data(), msg.size());
    }
    void drain_encouragement() { pending_encouragement_.clear(); }

private:
    void clear_runtime();
    void finish();
    void broadcast();
    void check_encouragement();
    int64_t effective_pause_us() const;
    void apply_change(double speed, double incline);

    TimeSource& ts_;
    ProgramEvents* events_ = nullptr;

    Program program_{};
    bool has_program_ = false;
    bool running_ = false;
    bool paused_ = false;
    bool completed_ = false;
    int current_interval_ = 0;
    int interval_elapsed_ = 0;
    int total_elapsed_ = 0;

    // Milestones: bit i set == milestone (25/50/75) already fired.
    uint8_t milestones_hit_ = 0;
    int last_encouragement_interval_ = -3;
    std::string pending_encouragement_;
    uint32_t encouragement_rr_ = 0;

    int64_t loop_start_us_ = 0;
    int64_t pause_accumulated_us_ = 0;
    int64_t pause_start_us_ = 0;
    bool pause_start_valid_ = false;
    int interval_start_elapsed_ = 0;
};

}  // namespace esp32tap::exec
