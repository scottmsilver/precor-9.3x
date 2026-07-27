# portable_core — OS-free fork of cpp/protocol + cpp/engine plus the new
# safety controller. Compiles for esp32s3 (this component) and for the
# host test build (host/Makefile). No FreeRTOS/ESP-IDF includes.
idf_component_register(
    SRCS
        "protocol/kv_protocol.cpp"
        "engine/mode_state.cpp"
        "safety/safety_controller.cpp"
    INCLUDE_DIRS "."
    REQUIRES ""
)
