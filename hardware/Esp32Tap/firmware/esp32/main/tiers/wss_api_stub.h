/*
 * wss_api_stub.h — HTTPS/WSS control API tier (DEFERRED).
 *
 * PLAN.md M5 + the Security section gate this tier: WSS/REST with the
 * newline-JSON vocabulary plus HRM verbs, per-device cert + bearer token
 * with client TOFU pinning — "must land before the WSS port is ever
 * enabled". 1 KB command cap, malformed JSON ignored. No WiFi component
 * is enabled in the phase-1 build.
 */

#pragma once

namespace esp32tap::tiers {

struct WssApiTier {
    static void start() { /* TODO(M5): esp_https_server WSS/REST, core 1 */ }
};

}  // namespace esp32tap::tiers
