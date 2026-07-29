/*
 * workout_session.h — port of python/workout_session.py WorkoutSession:
 * session lifecycle + ACSM calorie/distance/vert accrual. Owns the
 * ProgramState (invariant: a program only runs within an active
 * session; all program starts go through start_program()).
 */

#pragma once

#include <cstdint>
#include <string>

#include "program_state.h"
#include "time_source.h"

namespace esp32tap::exec {

class WorkoutSession {
public:
    WorkoutSession(TimeSource& ts, ProgramState& prog)
        : ts_(ts), prog_(prog) {}

    ProgramState& prog() { return prog_; }
    const ProgramState& prog() const { return prog_; }

    bool active() const { return active_; }
    double elapsed() const { return elapsed_; }
    double distance() const { return distance_; }
    double vert_feet() const { return vert_feet_; }
    double calories() const { return calories_; }
    const std::string& wall_started_at() const { return wall_started_at_; }
    // "" == null (python None) for JSON purposes.
    const std::string& end_reason() const { return end_reason_; }
    bool has_end_reason() const { return end_reason_valid_; }

    // Begin session. Idempotent if already active.
    void start();
    // Ensure session active, then start the loaded program.
    void start_program(int resume_interval = 0, int resume_elapsed = 0);
    // Auto-create + start a manual program if none running.
    void ensure_manual(double speed = 3.0, double incline = 0,
                       int duration_minutes = 60);
    void end(std::string_view reason);
    void pause();
    void resume();
    void reset();
    // Compute elapsed/distance/vert/calories (1 Hz). No-op while paused
    // or inactive.
    void tick(double speed_mph, double incline, double weight_kg = 70.0);

private:
    TimeSource& ts_;
    ProgramState& prog_;

    bool active_ = false;
    int64_t started_at_us_ = 0;
    std::string wall_started_at_;
    int64_t paused_at_us_ = 0;  // 0 == not paused (python parity)
    int64_t total_paused_us_ = 0;
    double elapsed_ = 0.0;
    double distance_ = 0.0;
    double vert_feet_ = 0.0;
    double calories_ = 0.0;
    int64_t last_tick_us_ = 0;
    std::string end_reason_;
    bool end_reason_valid_ = false;
};

}  // namespace esp32tap::exec
