# rapidjson (firmware-local vendored copy)

Copy of the repo-root `third_party/rapidjson` (MIT, Tencent/Milo Yip)
kept **inside** the esp32 firmware tree so the documented Docker build
(`docker run -v "$(pwd)":/project` with cwd = this firmware's `esp32/`
directory) is self-contained — component CMakeLists must not reference
paths that escape the container mount.

Two local patches on top of the repo-root copy (the shared copy is
deliberately left untouched so `cpp/` desktop builds are unaffected):

1. `allocators.h` — `kDefaultChunkCapacity` is overridable via
   `RAPIDJSON_ALLOCATOR_DEFAULT_CHUNK_CAPACITY` (upstream-parity with
   rapidjson master). The firmware builds define it to 4 KB: the 64 KB
   default would exhaust the no-PSRAM ESP32-S3 heap.
2. `document.h` — deleted the ill-formed `GenericStringRef`
   copy-assignment operator (upstream issue #1252); xtensa g++14 at
   `-std=gnu++2b` hard-errors on it.
