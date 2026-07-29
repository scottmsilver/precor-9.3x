# net_server — native server tier. api/ is pure C++ (host-testable:
# router + ServerCore + ws_hub); device/ holds the ESP-IDF bindings
# (esp_https_server transport, cert manager, mDNS, netif bring-up).
set(NET_SERVER_SRCS
    "api/server_core.cpp"
    "api/router.cpp")

if(IDF_TARGET)
    list(APPEND NET_SERVER_SRCS
        "device/transport_httpd.cpp"
        "device/cert_manager.cpp"
        "device/mdns_tier.cpp"
        "device/netif_start.cpp")
endif()

idf_component_register(
    SRCS ${NET_SERVER_SRCS}
    INCLUDE_DIRS "api" "device"
    REQUIRES executor storage esp_http_server esp_https_server
    PRIV_REQUIRES mbedtls esp-tls espressif__mdns
                  esp_wifi esp_netif esp_eth esp_timer nvs_flash lwip
)
target_include_directories(${COMPONENT_LIB} SYSTEM PUBLIC
    "${CMAKE_CURRENT_LIST_DIR}/../../third_party/rapidjson")

if(ESP32TAP_QEMU_TEST)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE ESP32TAP_QEMU_TEST=1)
endif()

# rapidjson MemoryPoolAllocator default chunk is 64 KB — far too coarse
# for the no-PSRAM S3 heap (a handful of live Documents would exhaust
# it). 4 KB chunks keep JSON heap use proportional to content.
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    RAPIDJSON_ALLOCATOR_DEFAULT_CHUNK_CAPACITY=4096)
