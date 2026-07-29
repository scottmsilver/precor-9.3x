# storage — JSON-array-file stores over an injected FsApi. Pure logic
# (host-testable); the device LittleFS mount lives in net_server's
# device bindings. Header-only except for the littlefs mount helper.
idf_component_register(
    SRCS "littlefs_mount.cpp"
    INCLUDE_DIRS "."
    REQUIRES ""
    PRIV_REQUIRES joltwallet__littlefs
)
target_include_directories(${COMPONENT_LIB} SYSTEM PUBLIC
    "${CMAKE_CURRENT_LIST_DIR}/../../third_party/rapidjson")

# rapidjson MemoryPoolAllocator default chunk is 64 KB — far too coarse
# for the no-PSRAM S3 heap (a handful of live Documents would exhaust
# it). 4 KB chunks keep JSON heap use proportional to content.
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    RAPIDJSON_ALLOCATOR_DEFAULT_CHUNK_CAPACITY=4096)
