/*
 * mdns_tier.cpp — see mdns_tier.h. Mirrors the Pi's static Avahi file
 * (deploy/treadmill.avahi-service): _treadmill._tcp 8000, TXT
 * scheme=https path=/.
 */

#include "mdns_tier.h"

#include "esp_log.h"
#include "mdns.h"

namespace esp32tap::net {

namespace {
const char* TAG = "esp32tap";
}

bool start_mdns() {
    if (mdns_init() != ESP_OK) {
        ESP_LOGE(TAG, "mdns init failed");
        return false;
    }
    mdns_hostname_set("treadmill");
    mdns_instance_name_set("treadmill");
    mdns_txt_item_t txt[] = {
        {"scheme", "https"},
        {"path", "/"},
    };
    if (mdns_service_add("treadmill", "_treadmill", "_tcp", 8000, txt, 2) !=
        ESP_OK) {
        ESP_LOGE(TAG, "mdns service add failed");
        return false;
    }
    ESP_LOGI(TAG, "mdns: _treadmill._tcp 8000 (scheme=https path=/)");
    return true;
}

}  // namespace esp32tap::net
