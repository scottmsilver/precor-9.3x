# executor — pure C++ port of python/program_engine.py ProgramState +
# python/workout_session.py (no FreeRTOS/IDF deps; host-testable via
# host/Makefile). rapidjson comes from the firmware-local third_party.
idf_component_register(
    SRCS
        "program_state.cpp"
        "workout_session.cpp"
    INCLUDE_DIRS "."
    REQUIRES ""
)
# Vendored rapidjson as a SYSTEM include: the 2016-era header trips
# modern-GCC warnings (-Werror in this tree); host/Makefile does the
# same with -isystem.
target_include_directories(${COMPONENT_LIB} SYSTEM PUBLIC
    "${CMAKE_CURRENT_LIST_DIR}/../../third_party/rapidjson")

# rapidjson MemoryPoolAllocator default chunk is 64 KB — far too coarse
# for the no-PSRAM S3 heap (a handful of live Documents would exhaust
# it). 4 KB chunks keep JSON heap use proportional to content.
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    RAPIDJSON_ALLOCATOR_DEFAULT_CHUNK_CAPACITY=4096)
