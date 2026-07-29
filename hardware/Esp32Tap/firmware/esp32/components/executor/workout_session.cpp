/*
 * workout_session.cpp — see workout_session.h.
 */

#include "workout_session.h"

#include "json_fmt.h"

namespace esp32tap::exec {

namespace {
constexpr double US_F = 1000000.0;
}

void WorkoutSession::start() {
    if (active_) {
        paused_at_us_ = 0;
        return;
    }
    active_ = true;
    started_at_us_ = ts_.now_us();
    wall_started_at_ = ts_.now_iso();
    paused_at_us_ = 0;
    total_paused_us_ = 0;
    elapsed_ = 0.0;
    distance_ = 0.0;
    vert_feet_ = 0.0;
    calories_ = 0.0;
    last_tick_us_ = ts_.now_us();
    end_reason_.clear();
    end_reason_valid_ = false;
}

void WorkoutSession::start_program(int resume_interval, int resume_elapsed) {
    start();
    prog_.start(resume_interval, resume_elapsed);
}

void WorkoutSession::ensure_manual(double speed, double incline,
                                   int duration_minutes) {
    if (prog_.running()) return;
    Program p{};
    p.name.set(fmt_int(duration_minutes) + "-Min Manual");
    p.manual = true;
    p.count = 1;
    Interval& iv = p.intervals.at(0);
    iv.name.set("Seg 1");
    iv.duration = duration_minutes * 60;
    iv.speed = speed;
    iv.incline = incline;
    prog_.load(p);
    start_program();
}

void WorkoutSession::end(std::string_view reason) {
    if (!active_) return;
    tick(0, 0);  // final elapsed update
    active_ = false;
    end_reason_.assign(reason.data(), reason.size());
    end_reason_valid_ = true;
}

void WorkoutSession::pause() {
    if (active_ && paused_at_us_ == 0) {
        paused_at_us_ = ts_.now_us();
    }
}

void WorkoutSession::resume() {
    if (active_ && paused_at_us_ > 0) {
        total_paused_us_ += ts_.now_us() - paused_at_us_;
        paused_at_us_ = 0;
    }
}

void WorkoutSession::reset() {
    prog_.reset();
    active_ = false;
    started_at_us_ = 0;
    wall_started_at_.clear();
    paused_at_us_ = 0;
    total_paused_us_ = 0;
    elapsed_ = 0.0;
    distance_ = 0.0;
    vert_feet_ = 0.0;
    calories_ = 0.0;
    last_tick_us_ = 0;
    end_reason_.clear();
    end_reason_valid_ = false;
}

void WorkoutSession::tick(double speed_mph, double incline, double weight_kg) {
    if (!active_ || paused_at_us_ > 0) return;
    int64_t now = ts_.now_us();
    double e = static_cast<double>(now - started_at_us_ - total_paused_us_) / US_F;
    elapsed_ = e > 0.0 ? e : 0.0;
    double dt = last_tick_us_ > 0
                    ? static_cast<double>(now - last_tick_us_) / US_F
                    : 1.0;
    last_tick_us_ = now;
    if (speed_mph > 0) {
        double miles_this_tick = (speed_mph / 3600.0) * dt;
        distance_ += miles_this_tick;
        if (incline > 0) {
            vert_feet_ += miles_this_tick * (incline / 100.0) * 5280.0;
        }
        // ACSM metabolic equation (VO2 in mL/kg/min); walking < 4.5 mph.
        double speed_m_min = speed_mph * 26.8224;
        double grade = incline / 100.0;
        double vo2 = 0.0;
        if (speed_mph < 4.5) {
            vo2 = 3.5 + 0.1 * speed_m_min + 1.8 * speed_m_min * grade;
        } else {
            vo2 = 3.5 + 0.2 * speed_m_min + 0.9 * speed_m_min * grade;
        }
        double kcal_per_min = vo2 * weight_kg / 1000.0 * 5.0;
        calories_ += kcal_per_min * (dt / 60.0);
    }
}

}  // namespace esp32tap::exec
