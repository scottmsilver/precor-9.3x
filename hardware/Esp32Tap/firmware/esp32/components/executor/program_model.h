/*
 * program_model.h — fixed-capacity Program/Interval structs for the
 * native server tier (port of the python dict shapes used by
 * python/program_engine.py ProgramState).
 *
 * Cold-path data model (1 Hz executor tick + REST handlers). Fixed
 * capacity instead of heap growth: MAX_INTERVALS=64; when manual splits
 * hit the cap, split_for_manual updates the current interval in place
 * instead of splitting (documented degradation, see PLAN.md note).
 */

#pragma once

#include <array>
#include <cstring>
#include <string_view>

namespace esp32tap::exec {

inline constexpr int MAX_INTERVALS = 64;
inline constexpr int NAME_CAP = 48;  // interval + program display names

// Application-level limits (python/program_engine.py) — the hardware /
// safety-controller clamps are wider and enforced again downstream.
inline constexpr double MIN_SPEED_MPH = 0.5;
inline constexpr double MAX_SPEED_MPH = 12.0;
inline constexpr double MAX_INCLINE_PCT = 15.0;
inline constexpr int MIN_DURATION_S = 10;
// Upper bound on ANY single interval's duration, enforced everywhere a
// duration is written: program_from_json clamps parsed input here to
// keep the double->int cast defined, and the in-place mutators
// (extend_current / adjust_duration) must enforce the SAME invariant —
// they add a bounded delta to an already-stored value, which without a
// ceiling is unbounded across repeated calls and eventually signed
// overflow (UB) on a field that gets persisted.
inline constexpr int MAX_DURATION_S = 86400;  // 24 h

struct FixedName {
    std::array<char, NAME_CAP> buf{};

    void set(std::string_view s) {
        size_t n = s.copy(buf.data(), buf.size() - 1);
        buf.at(n) = '\0';
    }
    std::string_view view() const {
        return std::string_view(buf.data(),
                                std::char_traits<char>::length(buf.data()));
    }
    bool operator==(const FixedName& o) const { return view() == o.view(); }
};

struct Interval {
    FixedName name;
    int duration = 0;        // seconds
    double speed = 0.0;      // mph
    double incline = 0.0;    // percent
};

struct Program {
    FixedName name;
    bool manual = false;
    int count = 0;
    std::array<Interval, MAX_INTERVALS> intervals{};

    int total_duration() const {
        int t = 0;
        for (int i = 0; i < count; i++) t += intervals.at(static_cast<size_t>(i)).duration;
        return t;
    }
    int cumulative_at(int idx) const {
        int t = 0;
        for (int i = 0; i < idx && i < count; i++) {
            t += intervals.at(static_cast<size_t>(i)).duration;
        }
        return t;
    }
};

}  // namespace esp32tap::exec
