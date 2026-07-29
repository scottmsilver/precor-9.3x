/*
 * json_fmt.h — small formatting helpers shared by the native server tier
 * (python-parity float formatting, iso8601 parsing/relative time).
 * Cold path: std::string allowed per repo rules.
 */

#pragma once

#include <array>
#include <charconv>
#include <cstdint>
#include <string>
#include <string_view>

namespace esp32tap::exec {

// Shortest round-trip decimal (like printf %g for our 1-decimal values):
// 6.0 -> "6", 5.5 -> "5.5".
inline std::string fmt_g(double v) {
    std::array<char, 32> buf{};
    auto [p, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), v,
                                 std::chars_format::general);
    if (ec != std::errc{}) return "0";
    return std::string(buf.data(), p);
}

// Python str(float) parity for fingerprints: integral floats keep ".0"
// (str(3.0) == "3.0", str(5.5) == "5.5").
inline std::string fmt_py_float(double v) {
    std::string s = fmt_g(v);
    if (s.find('.') == std::string::npos &&
        s.find('e') == std::string::npos &&
        s.find("inf") == std::string::npos &&
        s.find("nan") == std::string::npos) {
        s += ".0";
    }
    return s;
}

inline std::string fmt_int(int64_t v) {
    std::array<char, 24> buf{};
    auto [p, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), v);
    if (ec != std::errc{}) return "0";
    return std::string(buf.data(), p);
}

// --- iso8601 ("%Y-%m-%dT%H:%M:%S") minutes-since-epoch, for relative
// time strings. Proleptic Gregorian (Howard Hinnant days_from_civil).
inline int64_t days_from_civil(int y, int m, int d) {
    y -= m <= 2;
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const int64_t yoe = y - era * 400;
    const int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

// Parse "YYYY-MM-DDTHH:MM:SS" (extra trailing chars ignored). Returns
// false on malformed input.
inline bool parse_iso_minutes(std::string_view s, int64_t& minutes_out) {
    auto num = [&](size_t pos, size_t len, int& out) {
        if (pos + len > s.size()) return false;
        auto [p, ec] =
            std::from_chars(s.data() + pos, s.data() + pos + len, out);
        return ec == std::errc{} && p == s.data() + pos + len;
    };
    int y = 0, mo = 0, d = 0, h = 0, mi = 0, sec = 0;
    if (!num(0, 4, y) || !num(5, 2, mo) || !num(8, 2, d) || !num(11, 2, h) ||
        !num(14, 2, mi) || !num(17, 2, sec)) {
        return false;
    }
    if (s.size() < 19 || s.at(4) != '-' || s.at(7) != '-' ||
        (s.at(10) != 'T' && s.at(10) != ' ') || s.at(13) != ':' ||
        s.at(16) != ':') {
        return false;
    }
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return false;
    minutes_out = days_from_civil(y, mo, d) * 1440 + h * 60 + mi;
    return true;
}

// server.py _relative_time parity ("just now", "5m ago", "3h ago",
// "yesterday", "4d ago", "2mo ago"); "" on malformed/empty input.
inline std::string relative_time(std::string_view then_iso,
                                 std::string_view now_iso) {
    int64_t then_min = 0, now_min = 0;
    if (then_iso.empty() || !parse_iso_minutes(then_iso, then_min) ||
        !parse_iso_minutes(now_iso, now_min)) {
        return "";
    }
    int64_t mins = now_min - then_min;
    if (mins < 1) return "just now";
    if (mins < 60) return fmt_int(mins) + "m ago";
    int64_t hours = mins / 60;
    if (hours < 24) return fmt_int(hours) + "h ago";
    int64_t days = hours / 24;
    if (days == 1) return "yesterday";
    if (days < 30) return fmt_int(days) + "d ago";
    return fmt_int(days / 30) + "mo ago";
}

// server.py _fmt_dur parity: "m:ss" or "h:mm:ss".
inline std::string fmt_dur(double secs) {
    int64_t s = secs > 0 ? static_cast<int64_t>(secs) : 0;
    int64_t m = s / 60;
    int64_t sec = s % 60;
    auto two = [](int64_t v) {
        std::string t = fmt_int(v);
        return t.size() < 2 ? "0" + t : t;
    };
    if (m >= 60) {
        return fmt_int(m / 60) + ":" + two(m % 60) + ":" + two(sec);
    }
    return fmt_int(m) + ":" + two(sec);
}

// Fixed-decimal rounding helper (python round(x, n) parity is
// round-half-even; banker's rounding differences at exact .5 boundaries
// are visually irrelevant here — documented delta).
inline double round_to(double v, int places) {
    double f = 1.0;
    for (int i = 0; i < places; i++) f *= 10.0;
    double scaled = v * f;
    double r = scaled >= 0 ? scaled + 0.5 : scaled - 0.5;
    int64_t t = static_cast<int64_t>(r);
    return static_cast<double>(t) / f;
}

// "{d.dd} mi" style fixed formatting for UI text (2 or 3 decimals).
inline std::string fmt_fixed(double v, int places) {
    double r = round_to(v, places);
    std::array<char, 48> buf{};
    auto [p, ec] = std::to_chars(buf.data(), buf.data() + buf.size(), r,
                                 std::chars_format::fixed, places);
    if (ec != std::errc{}) return "0";
    return std::string(buf.data(), p);
}

}  // namespace esp32tap::exec
