set(esp32tap_main_srcs
    "app_main.cpp"
    "serial_engine_task.cpp"
    "emulate_cycle_task.cpp"
    "interval_executor_task.cpp"
    "net_server_task.cpp")

# ESP32TAP_QEMU_TEST=1 (CMake cache var, e.g.
#   idf.py -B build_qemu_test -DESP32TAP_QEMU_TEST=1 build
# ) selects the QEMU behavioral-harness test image: scripted safety IO,
# motor tap remapped to UART0, QT command shim + QTAUDIT/QTSTATE output
# (main/qemu_test/). OFF by default — the production image contains none
# of this code (tools/qemu_harness asserts that on the built binary).
if(ESP32TAP_QEMU_TEST)
    list(APPEND esp32tap_main_srcs "qemu_test/qemu_test_shim.cpp")
endif()

idf_component_register(
    SRCS ${esp32tap_main_srcs}
    INCLUDE_DIRS "."
    REQUIRES portable_core esp_hal executor storage net_server
)

if(ESP32TAP_QEMU_TEST)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC ESP32TAP_QEMU_TEST=1)
endif()

# ESP32TAP_NET gates STARTING the native server tier (app_main step 5).
# Default: ON for production images, OFF for the QEMU behavioral image —
# S1..S7 measure microsecond-scale safety deadlines and must not share
# the emulated SoC with WiFi/lwIP/mbedTLS/mDNS. The network scenarios
# build a third image with -DESP32TAP_NET=1 (tools/build_images.sh).
if(NOT DEFINED ESP32TAP_NET)
    if(ESP32TAP_QEMU_TEST)
        set(ESP32TAP_NET 0)
    else()
        set(ESP32TAP_NET 1)
    endif()
endif()
if(ESP32TAP_NET)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC ESP32TAP_NET=1)
endif()
