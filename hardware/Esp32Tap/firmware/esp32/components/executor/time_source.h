/*
 * time_source.h — injected clock for the executor/server tier.
 * Device impl: esp_timer (monotonic µs) + time() (wall iso). Host tests
 * drive both deterministically.
 */

#pragma once

#include <cstdint>
#include <string>

namespace esp32tap::exec {

class TimeSource {
public:
    virtual ~TimeSource() = default;
    // Monotonic microseconds (guest clock).
    virtual int64_t now_us() = 0;
    // Wall clock as "%Y-%m-%dT%H:%M:%S" (server.py convention).
    virtual std::string now_iso() = 0;
};

}  // namespace esp32tap::exec
