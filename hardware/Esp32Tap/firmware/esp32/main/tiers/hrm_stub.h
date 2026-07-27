/*
 * hrm_stub.h — HRM BLE central tier (DEFERRED).
 *
 * PLAN.md M4 gates this tier: "HRM central: 0x180D scan, 0x2A37
 * subscribe, NVS persistence, mock-HR debug hook", scans duty-cycled and
 * stopped once the saved strap connects. No BLE component is enabled in
 * the phase-1 build.
 */

#pragma once

namespace esp32tap::tiers {

struct HrmTier {
    static void start() { /* TODO(M4): NimBLE HRM central, core 1 */ }
};

}  // namespace esp32tap::tiers
