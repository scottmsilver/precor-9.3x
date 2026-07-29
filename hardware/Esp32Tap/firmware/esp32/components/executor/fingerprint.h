/*
 * fingerprint.h — server.py _program_fingerprint parity: pipe-joined
 * "speed,incline,duration" triples, ignoring names. Values format like
 * python str(): floats keep ".0" when integral, durations are ints.
 */

#pragma once

#include <string>

#include "json_fmt.h"
#include "program_model.h"

namespace esp32tap::exec {

inline std::string program_fingerprint(const Program& p) {
    std::string out;
    for (int i = 0; i < p.count; i++) {
        const Interval& iv = p.intervals.at(static_cast<size_t>(i));
        if (i > 0) out += "|";
        out += fmt_py_float(iv.speed);
        out += ",";
        out += fmt_py_float(iv.incline);
        out += ",";
        out += fmt_int(iv.duration);
    }
    return out;
}

}  // namespace esp32tap::exec
