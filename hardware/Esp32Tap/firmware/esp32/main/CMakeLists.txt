idf_component_register(
    SRCS
        "app_main.cpp"
        "serial_engine_task.cpp"
        "emulate_cycle_task.cpp"
        "interval_executor_task.cpp"
    INCLUDE_DIRS "."
    REQUIRES portable_core esp_hal
)
