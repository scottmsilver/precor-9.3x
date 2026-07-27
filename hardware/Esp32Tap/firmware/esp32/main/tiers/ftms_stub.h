/*
 * ftms_stub.h — FTMS BLE peripheral tier (DEFERRED).
 *
 * PLAN.md M4 gates this tier: "NimBLE FTMS peripheral ... 24 h+ WiFi/BLE
 * coex soak is a hard bench gate". Core 1, NimBLE GATT server porting
 * rust/ftms/src/protocol.rs encodings (Feature / Treadmill Data 1 Hz /
 * Ranges / Control Point / Machine Status). No BLE component is enabled
 * in the phase-1 build.
 */

#pragma once

namespace esp32tap::tiers {

struct FtmsTier {
    static void start() { /* TODO(M4): NimBLE FTMS peripheral, core 1 */ }
};

}  // namespace esp32tap::tiers
