/*
 * netif_start.h — network interface bring-up.
 *
 * Production: esp_wifi STA. Credentials come from NVS namespace "net"
 * (keys "ssid"/"pass"), with the Kconfig ESP32TAP_WIFI_SSID/PASS
 * bring-up fallback. No creds -> returns false and the whole net tier
 * stays down (this is also what keeps the production image safe under
 * QEMU, which cannot emulate S3 WiFi). SoftAP provisioning is a
 * follow-up (beads issue).
 *
 * ESP32TAP_QEMU_TEST: openeth NIC (QEMU -nic user,model=open_eth) +
 * dp83848 PHY (100 ms autonego) + DHCP — everything from esp_netif up
 * is identical to production.
 */

#pragma once

namespace esp32tap::net {

// Returns true when a netif is up and has (or will get) an address.
bool start_netif();

// Start SNTP (async, non-blocking, best effort). Until it lands — and
// forever on an offline device — std::time() is seconds since boot, so
// every stored ISO timestamp renders as a 1970 date that restarts each
// boot. Nothing ORDERS on those strings (JsonArrayStore's persisted
// monotonic "seq" does that); this only fixes what the app displays.
// Safe to call once, after start_netif() returns true.
void start_sntp();

}  // namespace esp32tap::net
