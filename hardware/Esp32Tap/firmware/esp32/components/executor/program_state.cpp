/*
 * program_state.cpp — see program_state.h. Port of
 * python/program_engine.py ProgramState; behavior anchored by the host
 * tests transliterated from python/tests/test_program_engine.py.
 */

#include "program_state.h"

#include <array>
#include <string_view>

#include "json_fmt.h"

namespace esp32tap::exec {

namespace {

constexpr int64_t US = 1000000;

// Every in-place duration write goes through here: the arithmetic is
// done in int64 and the result forced into [MIN_DURATION_S,
// MAX_DURATION_S] — the same invariant program_from_json applies to
// parsed input. Without the ceiling, repeated extend/adjust calls walk
// a persisted int off the end of its range (signed overflow == UB).
int clamp_duration(int64_t v) {
    if (v < MIN_DURATION_S) return MIN_DURATION_S;
    if (v > MAX_DURATION_S) return MAX_DURATION_S;
    return static_cast<int>(v);
}

constexpr std::array<std::string_view, 8> kEncouragement = {
    "Keep it up! You're doing great!",
    "Strong effort! Stay with it!",
    "Looking good! Keep pushing!",
    "You've got this! Stay strong!",
    "Nice work! Keep that pace!",
    "Crushing it! Don't stop now!",
    "Great form! Keep going!",
    "Almost there! Stay focused!",
};

struct Milestone {
    int pct;
    uint8_t bit;
    std::string_view msg;
};

constexpr std::array<Milestone, 3> kMilestones = {{
    {25, 1, "Quarter of the way done — strong start!"},
    {50, 2, "Halfway there! You're killing it!"},
    {75, 4, "Three quarters done — the finish line is in sight!"},
}};

}  // namespace

const Interval* ProgramState::current_iv() const {
    if (!has_program_ || current_interval_ >= program_.count) return nullptr;
    return &program_.intervals.at(static_cast<size_t>(current_interval_));
}

void ProgramState::clear_runtime() {
    running_ = false;
    paused_ = false;
    completed_ = false;
    current_interval_ = 0;
    interval_elapsed_ = 0;
    total_elapsed_ = 0;
    milestones_hit_ = 0;
    last_encouragement_interval_ = -3;
    pending_encouragement_.clear();
    loop_start_us_ = 0;
    pause_accumulated_us_ = 0;
    pause_start_us_ = 0;
    pause_start_valid_ = false;
    interval_start_elapsed_ = 0;
}

void ProgramState::load(const Program& p) {
    program_ = p;
    has_program_ = true;
    clear_runtime();
}

void ProgramState::apply_change(double speed, double incline) {
    if (events_) events_->on_change(speed, incline);
}

void ProgramState::broadcast() {
    if (events_) {
        events_->on_update();
        drain_encouragement();
    }
}

void ProgramState::start(int resume_interval, int resume_elapsed) {
    stop();  // python parity: start() awaits stop() first
    if (!has_program_) return;
    running_ = true;
    paused_ = false;
    completed_ = false;
    current_interval_ = resume_interval;
    total_elapsed_ = resume_elapsed;
    interval_elapsed_ =
        resume_elapsed - program_.cumulative_at(resume_interval);
    milestones_hit_ = 0;
    last_encouragement_interval_ = -3;
    pending_encouragement_.clear();
    loop_start_us_ = ts_.now_us() - static_cast<int64_t>(resume_elapsed) * US;
    pause_accumulated_us_ = 0;
    pause_start_us_ = 0;
    pause_start_valid_ = false;
    interval_start_elapsed_ = program_.cumulative_at(resume_interval);
    // Pre-mark milestones already passed on resume.
    int td = total_duration();
    if (td > 0) {
        double pct = (static_cast<double>(resume_elapsed) / td) * 100.0;
        for (const auto& m : kMilestones) {
            if (pct >= m.pct) milestones_hit_ |= m.bit;
        }
    }
    const Interval* iv = current_iv();
    if (iv != nullptr) {
        apply_change(iv->speed, iv->incline);
    }
    broadcast();
}

void ProgramState::stop() {
    bool was_running = running_;
    running_ = false;
    paused_ = false;
    if (was_running) {
        apply_change(0, 0);
    }
    broadcast();
}

void ProgramState::reset() {
    bool was_running = running_;
    program_ = Program{};
    has_program_ = false;
    clear_runtime();
    if (was_running) {
        apply_change(0, 0);
    }
    broadcast();
}

void ProgramState::toggle_pause() {
    if (!paused_) {
        paused_ = true;
        pause_start_us_ = ts_.now_us();
        pause_start_valid_ = true;
    } else {
        paused_ = false;
        if (pause_start_valid_) {
            pause_accumulated_us_ += ts_.now_us() - pause_start_us_;
            pause_start_valid_ = false;
        }
        if (running_) {
            const Interval* iv = current_iv();
            if (iv != nullptr) {
                apply_change(iv->speed, iv->incline);
            }
        }
    }
    broadcast();
}

int64_t ProgramState::effective_pause_us() const {
    int64_t total = pause_accumulated_us_;
    if (paused_ && pause_start_valid_) {
        total += ts_.now_us() - pause_start_us_;
    }
    return total;
}

void ProgramState::skip() {
    if (!running_) return;
    current_interval_++;
    const Interval* iv = current_iv();
    if (iv != nullptr) {
        int target = program_.cumulative_at(current_interval_);
        loop_start_us_ = ts_.now_us() - effective_pause_us() -
                         static_cast<int64_t>(target) * US;
        interval_start_elapsed_ = target;
        total_elapsed_ = target;
        interval_elapsed_ = 0;
        apply_change(iv->speed, iv->incline);
    } else {
        finish();
    }
    broadcast();
}

void ProgramState::prev() {
    if (!running_) return;
    if (current_interval_ > 0) current_interval_--;
    int target = program_.cumulative_at(current_interval_);
    loop_start_us_ = ts_.now_us() - effective_pause_us() -
                     static_cast<int64_t>(target) * US;
    interval_start_elapsed_ = target;
    total_elapsed_ = target;
    interval_elapsed_ = 0;
    const Interval* iv = current_iv();
    if (iv != nullptr) {
        apply_change(iv->speed, iv->incline);
    }
    broadcast();
}

bool ProgramState::extend_current(int seconds) {
    if (!running_ || current_iv() == nullptr) return false;
    Interval& iv = program_.intervals.at(static_cast<size_t>(current_interval_));
    iv.duration = clamp_duration(static_cast<int64_t>(iv.duration) + seconds);
    broadcast();
    return true;
}

bool ProgramState::split_for_manual(double speed, double incline) {
    if (!running_ || !is_manual() || current_iv() == nullptr) return false;
    Interval& iv = program_.intervals.at(static_cast<size_t>(current_interval_));
    int elapsed = interval_elapsed_ > 1 ? interval_elapsed_ : 1;
    int remaining = iv.duration - elapsed;
    if (remaining < 1) return false;
    double ds = iv.speed - speed;
    double di = iv.incline - incline;
    if ((ds < 0.05 && ds > -0.05) && (di < 0.05 && di > -0.05)) return false;

    if (program_.count >= MAX_INTERVALS) {
        // Fixed-capacity degradation: update in place instead of split.
        iv.speed = speed;
        iv.incline = incline;
        broadcast();
        return true;
    }
    // Trim current interval to what's been completed.
    iv.duration = elapsed;
    int seg_num = current_interval_ + 2;
    Interval next{};
    next.name.set("Seg " + fmt_int(seg_num));
    next.duration = remaining;
    next.speed = speed;
    next.incline = incline;
    // Insert after current: shift the tail right.
    for (int i = program_.count; i > current_interval_ + 1; i--) {
        program_.intervals.at(static_cast<size_t>(i)) =
            program_.intervals.at(static_cast<size_t>(i - 1));
    }
    program_.intervals.at(static_cast<size_t>(current_interval_ + 1)) = next;
    program_.count++;
    current_interval_++;
    interval_elapsed_ = 0;
    broadcast();
    return true;
}

bool ProgramState::adjust_duration(int delta_seconds) {
    if (!running_ || !is_manual() || !has_program_) return false;
    if (program_.count == 0) return false;
    Interval& last = program_.intervals.at(static_cast<size_t>(program_.count - 1));
    last.duration =
        clamp_duration(static_cast<int64_t>(last.duration) + delta_seconds);
    broadcast();
    return true;
}

void ProgramState::finish() {
    running_ = false;
    paused_ = false;
    completed_ = true;
    apply_change(0, 0);
    broadcast();
}

void ProgramState::check_encouragement() {
    if (!has_program_ || !running_) return;
    int td = total_duration();
    if (td <= 0) return;

    // Milestones (25/50/75%) — highest priority.
    double pct = (static_cast<double>(total_elapsed_) / td) * 100.0;
    for (const auto& m : kMilestones) {
        if (pct >= m.pct && (milestones_hit_ & m.bit) == 0) {
            milestones_hit_ |= m.bit;
            pending_encouragement_.assign(m.msg.data(), m.msg.size());
            return;
        }
    }

    // Every 3 intervals (rotating pick instead of random.choice).
    if ((current_interval_ - last_encouragement_interval_) >= 3 &&
        interval_elapsed_ == 0 && current_interval_ > 0) {
        last_encouragement_interval_ = current_interval_;
        std::string_view msg =
            kEncouragement.at(encouragement_rr_ % kEncouragement.size());
        encouragement_rr_++;
        pending_encouragement_.assign(msg.data(), msg.size());
        return;
    }

    // Interval countdown (structured programs only).
    if (is_manual()) return;
    const Interval* iv = current_iv();
    if (iv == nullptr) return;
    int remaining = iv->duration - interval_elapsed_;
    bool is_last = current_interval_ == program_.count - 1;

    std::string suffix;
    if (is_last) {
        suffix = "til the end";
    } else {
        const Interval& next =
            program_.intervals.at(static_cast<size_t>(current_interval_ + 1));
        std::string parts;
        if (next.speed != iv->speed) {
            parts += fmt_g(next.speed) + "mph";
        }
        if (next.incline != iv->incline) {
            if (!parts.empty()) parts += " and ";
            parts += fmt_g(next.incline) + "%";
        }
        suffix = parts.empty() ? "til next section" : "til " + parts;
    }

    if (remaining <= 30) {
        if (remaining >= 1) {
            pending_encouragement_ =
                "<<" + fmt_int(remaining) + ">>s " + suffix;
        }
    } else if (remaining <= 610) {
        int adjusted = remaining - 10;
        if (adjusted > 0 && adjusted % 60 == 0) {
            int minutes = adjusted / 60;
            pending_encouragement_ =
                "<<" + fmt_int(minutes) + ">> " +
                (minutes == 1 ? "minute" : "minutes") + " " + suffix;
        }
    }
}

void ProgramState::tick() {
    if (!running_) return;
    if (paused_) {
        broadcast();
        return;
    }

    int64_t real_elapsed_us =
        ts_.now_us() - loop_start_us_ - pause_accumulated_us_;
    if (real_elapsed_us < 0) real_elapsed_us = 0;
    total_elapsed_ = static_cast<int>(real_elapsed_us / US);
    interval_elapsed_ = total_elapsed_ - interval_start_elapsed_;

    const Interval* iv = current_iv();
    if (iv == nullptr) {
        finish();
        return;
    }

    if (interval_elapsed_ >= iv->duration) {
        current_interval_++;
        interval_start_elapsed_ = total_elapsed_;
        interval_elapsed_ = 0;
        const Interval* nxt = current_iv();
        if (nxt != nullptr) {
            apply_change(nxt->speed, nxt->incline);
        } else {
            finish();
            return;
        }
    }

    check_encouragement();
    broadcast();
}

}  // namespace esp32tap::exec
