/*
 * littlefs_mount.cpp — device-only: mount (format-if-needed) the
 * "storage" partition at /data. After the mount, plain POSIX stdio via
 * the VFS works, so the same PosixFs implementation serves device and
 * host tests.
 */

#if defined(ESP_PLATFORM)

#include "littlefs_mount.h"

#include "esp_littlefs.h"
#include "esp_log.h"

namespace esp32tap::storage {

namespace {
const char* TAG = "esp32tap";
}

bool mount_data_partition() {
    esp_vfs_littlefs_conf_t conf = {};
    conf.base_path = "/data";
    conf.partition_label = "storage";
    conf.format_if_mount_failed = true;
    conf.dont_mount = false;
    esp_err_t err = esp_vfs_littlefs_register(&conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "littlefs mount failed: %s", esp_err_to_name(err));
        return false;
    }
    size_t total = 0, used = 0;
    if (esp_littlefs_info("storage", &total, &used) == ESP_OK) {
        ESP_LOGI(TAG, "littlefs /data mounted: %u/%u bytes used",
                 static_cast<unsigned>(used), static_cast<unsigned>(total));
    }
    return true;
}

}  // namespace esp32tap::storage

#endif  // ESP_PLATFORM
