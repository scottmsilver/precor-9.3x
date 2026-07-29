/*
 * cert_manager.h — per-device self-signed TLS identity: EC P-256 key +
 * X.509 cert generated at first boot, persisted on /data, regenerated
 * only if absent. Clients are trust-all (Android trustAllTls / iOS
 * TrustAllDelegate), so a self-signed ECDSA cert negotiates fine
 * (proven under QEMU: TLSv1.2 ECDHE-ECDSA-AES256-GCM-SHA384).
 */

#pragma once

#include <string>

#include "fs_api.h"

namespace esp32tap::net {

// Load cert.pem/key.pem from fs, generating + persisting them when
// absent. Returns false on crypto/persist failure (caller skips the
// HTTPS tier — the belt never depends on it).
bool ensure_device_cert(storage::FsApi& fs, std::string& cert_pem,
                        std::string& key_pem);

}  // namespace esp32tap::net
