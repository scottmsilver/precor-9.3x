/*
 * mdns_tier.h — DNS-SD advertisement per the device-discovery contract:
 * instance "treadmill", service _treadmill._tcp port 8000, TXT
 * scheme=https path=/.
 */

#pragma once

namespace esp32tap::net {

bool start_mdns();

}  // namespace esp32tap::net
