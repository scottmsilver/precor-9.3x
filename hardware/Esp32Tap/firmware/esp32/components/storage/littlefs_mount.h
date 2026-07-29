/*
 * littlefs_mount.h — device-only /data mount (see littlefs_mount.cpp).
 */

#pragma once

namespace esp32tap::storage {

// Mount the "storage" partition at /data (formatting on first boot).
// Returns false on unrecoverable flash failure — callers degrade to
// RAM-only stores (the belt never depends on /data).
bool mount_data_partition();

}  // namespace esp32tap::storage
