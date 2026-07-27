idf_component_register(
    SRCS
        "esp32_uart_port.cpp"
        "esp32_safety_io.cpp"
        "esp32_time.cpp"
        "wdt.cpp"
    INCLUDE_DIRS "."
    REQUIRES portable_core esp_driver_uart esp_driver_gpio esp_timer
)
