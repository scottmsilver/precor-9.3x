/*
 * fs_api.h — filesystem abstraction for the native server tier stores.
 *
 * One POSIX implementation serves both worlds: host tests point it at a
 * temp dir; the device points it at the LittleFS VFS mount (/data).
 * Writes are atomic: temp file + rename (LittleFS rename is atomic).
 */

#pragma once

#include <array>
#include <cstdio>
#include <string>
#include <string_view>

namespace esp32tap::storage {

// Default ceiling on a single read. Callers that know their own bound
// (the JSON stores know their byte cap) pass a tighter one: the read
// buffer plus the document parsed from it are BOTH resident at once on
// a 512 KB no-PSRAM part, so the read cap — not the store cap, which is
// only applied after parsing — is what actually bounds the boot-time
// peak.
inline constexpr size_t DEFAULT_MAX_FILE_BYTES = 16 * 1024;

class FsApi {
public:
    virtual ~FsApi() = default;
    virtual bool read_file(const std::string& path, std::string& out,
                           size_t max_bytes = DEFAULT_MAX_FILE_BYTES) = 0;
    virtual bool write_file_atomic(const std::string& path,
                                   std::string_view data) = 0;
    virtual bool exists(const std::string& path) = 0;
    virtual bool remove_file(const std::string& path) = 0;
};

class PosixFs : public FsApi {
public:
    // root: directory prefix (e.g. "/data" on device, a tmp dir in host
    // tests). Paths passed to the API are joined as root + "/" + path.
    //
    // yield_between_chunks (device): called between 4 KB write chunks.
    // LittleFS flash erase/program is CPU-bound with the cache disabled;
    // an unbroken multi-KB fwrite on the prio-3 core-1 storage task
    // could starve the core-1 idle task past the 2 s task-WDT
    // (CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1=y) and panic the
    // device. The device wires a 1-tick vTaskDelay here so idle runs
    // between chunks; host tests pass nullptr.
    using YieldFn = void (*)();
    explicit PosixFs(std::string root, YieldFn yield_between_chunks = nullptr)
        : root_(std::move(root)), yield_(yield_between_chunks) {}

    bool read_file(const std::string& path, std::string& out,
                   size_t max_bytes = DEFAULT_MAX_FILE_BYTES) override {
        std::FILE* f = std::fopen(full(path).c_str(), "rb");
        if (f == nullptr) return false;
        out.clear();
        std::array<char, 512> buf{};
        size_t n = 0;
        while ((n = std::fread(buf.data(), 1, buf.size(), f)) > 0) {
            out.append(buf.data(), n);
            // Length-validated input, and the ONLY bound that limits the
            // boot-time heap peak: refuse (rather than truncate) so a
            // store file written by a build with a bigger cap degrades
            // to "empty store", never to "half a JSON array".
            if (out.size() > max_bytes) {
                std::fclose(f);
                out.clear();
                out.shrink_to_fit();
                return false;
            }
        }
        std::fclose(f);
        return true;
    }

    bool write_file_atomic(const std::string& path,
                           std::string_view data) override {
        std::string tmp = full(path) + ".tmp";
        std::FILE* f = std::fopen(tmp.c_str(), "wb");
        if (f == nullptr) return false;
        constexpr size_t CHUNK = 4 * 1024;
        bool ok = true;
        size_t off = 0;
        while (ok && off < data.size()) {
            size_t n = data.size() - off;
            if (n > CHUNK) n = CHUNK;
            ok = std::fwrite(data.data() + off, 1, n, f) == n;
            off += n;
            if (ok && off < data.size() && yield_ != nullptr) yield_();
        }
        ok = std::fclose(f) == 0 && ok;
        if (!ok) {
            std::remove(tmp.c_str());
            return false;
        }
        return std::rename(tmp.c_str(), full(path).c_str()) == 0;
    }

    bool exists(const std::string& path) override {
        std::FILE* f = std::fopen(full(path).c_str(), "rb");
        if (f == nullptr) return false;
        std::fclose(f);
        return true;
    }

    bool remove_file(const std::string& path) override {
        return std::remove(full(path).c_str()) == 0;
    }

private:
    std::string full(const std::string& path) const {
        return root_ + "/" + path;
    }
    std::string root_;
    YieldFn yield_ = nullptr;
};

}  // namespace esp32tap::storage
