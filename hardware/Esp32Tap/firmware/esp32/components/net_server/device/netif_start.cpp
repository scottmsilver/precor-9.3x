/*
 * netif_start.cpp — see netif_start.h.
 */

#include "netif_start.h"

#include <array>
#include <cstring>
#include <string>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "nvs_flash.h"

#if defined(ESP32TAP_QEMU_TEST)
#include "esp_eth.h"
#include "esp_eth_mac_openeth.h"
#include "esp_eth_phy.h"
#else
#include "esp_wifi.h"
#endif

namespace esp32tap::net {

namespace {

const char* TAG = "esp32tap";

bool init_common() {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs init failed");
        return false;
    }
    if (esp_netif_init() != ESP_OK) return false;
    if (esp_event_loop_create_default() != ESP_OK) return false;
    return true;
}

}  // namespace

void start_sntp() {
    static bool started = false;
    if (started) return;
    // Fire-and-forget: esp_netif_sntp_init spawns lwIP's SNTP client
    // and returns immediately. We deliberately do NOT wait for sync —
    // the API tier must come up with or without a reachable NTP server
    // (an offline treadmill is a supported configuration).
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    cfg.start = true;
    cfg.server_from_dhcp = true;  // prefer the LAN's own NTP server
    cfg.renew_servers_after_new_IP = true;
    if (esp_netif_sntp_init(&cfg) != ESP_OK) {
        ESP_LOGW(TAG, "sntp init failed — timestamps stay boot-relative");
        return;
    }
    started = true;
    ESP_LOGI(TAG, "sntp started (wall-clock timestamps are best-effort)");
}

#if defined(ESP32TAP_QEMU_TEST)

// QEMU: openeth + dp83848 + DHCP (slirp hands out 10.0.2.15 in ~1 s).
bool start_netif() {
    if (!init_common()) return false;
    esp_netif_config_t netif_cfg = ESP_NETIF_DEFAULT_ETH();
    esp_netif_t* netif = esp_netif_new(&netif_cfg);
    if (netif == nullptr) return false;

    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    esp_eth_mac_t* mac = esp_eth_mac_new_openeth(&mac_config);
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    phy_config.phy_addr = 1;
    phy_config.autonego_timeout_ms = 100;
    phy_config.reset_gpio_num = -1;
    esp_eth_phy_t* phy = esp_eth_phy_new_dp83848(&phy_config);
    if (mac == nullptr || phy == nullptr) return false;

    esp_eth_config_t eth_config = ETH_DEFAULT_CONFIG(mac, phy);
    esp_eth_handle_t eth = nullptr;
    if (esp_eth_driver_install(&eth_config, &eth) != ESP_OK) {
        ESP_LOGE(TAG, "openeth driver install failed");
        return false;
    }
    if (esp_netif_attach(netif, esp_eth_new_netif_glue(eth)) != ESP_OK) {
        return false;
    }
    if (esp_eth_start(eth) != ESP_OK) return false;
    ESP_LOGI(TAG, "netif up: openeth (QEMU) + DHCP");
    return true;
}

#else  // production: esp_wifi STA

bool start_netif() {
    if (!init_common()) return false;

    // Credentials: NVS "net" namespace overrides the Kconfig bring-up
    // fallback.
    std::array<char, 33> ssid{};
    std::array<char, 65> pass{};
#if defined(CONFIG_ESP32TAP_WIFI_SSID)
    std::string_view kconfig_ssid = CONFIG_ESP32TAP_WIFI_SSID;
    std::string_view kconfig_pass = CONFIG_ESP32TAP_WIFI_PASS;
    kconfig_ssid.copy(ssid.data(), ssid.size() - 1);
    kconfig_pass.copy(pass.data(), pass.size() - 1);
#endif
    nvs_handle_t nvs;
    if (nvs_open("net", NVS_READONLY, &nvs) == ESP_OK) {
        size_t len = ssid.size();
        nvs_get_str(nvs, "ssid", ssid.data(), &len);
        len = pass.size();
        nvs_get_str(nvs, "pass", pass.data(), &len);
        nvs_close(nvs);
    }
    if (ssid.at(0) == '\0') {
        ESP_LOGW(TAG,
                 "no WiFi credentials (NVS net/ssid or Kconfig) — network "
                 "tier disabled");
        return false;
    }

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init_cfg) != ESP_OK) {
        ESP_LOGE(TAG, "wifi init failed");
        return false;
    }
    // Auto-reconnect on disconnect.
    esp_event_handler_register(
        WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
        [](void*, esp_event_base_t, int32_t, void*) { esp_wifi_connect(); },
        nullptr);
    esp_event_handler_register(
        WIFI_EVENT, WIFI_EVENT_STA_START,
        [](void*, esp_event_base_t, int32_t, void*) { esp_wifi_connect(); },
        nullptr);

    wifi_config_t wifi_cfg = {};
    std::memcpy(wifi_cfg.sta.ssid, ssid.data(),
                std::char_traits<char>::length(ssid.data()));
    std::memcpy(wifi_cfg.sta.password, pass.data(),
                std::char_traits<char>::length(pass.data()));
    if (esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK) return false;
    if (esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg) != ESP_OK) return false;
    if (esp_wifi_start() != ESP_OK) {
        ESP_LOGE(TAG, "wifi start failed");
        return false;
    }
    esp_wifi_set_ps(WIFI_PS_MIN_MODEM);  // PLAN RAM/power policy
    ESP_LOGI(TAG, "netif up: wifi STA \"%s\" (DHCP)", ssid.data());
    return true;
}

#endif

}  // namespace esp32tap::net
