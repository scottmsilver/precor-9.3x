/*
 * mdns_stub.h — mDNS advertisement tier (DEFERRED).
 *
 * PLAN.md M5 gates this tier: mDNS `_treadmill._tcp` with
 * `scheme=https`; discovery via mDNS only, no hardcoded URLs anywhere.
 * No WiFi component is enabled in the phase-1 build.
 */

#pragma once

namespace esp32tap::tiers {

struct MdnsTier {
    static void start() { /* TODO(M5): mDNS _treadmill._tcp scheme=https */ }
};

}  // namespace esp32tap::tiers
