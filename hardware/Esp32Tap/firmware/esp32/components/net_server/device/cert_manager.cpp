/*
 * cert_manager.cpp — see cert_manager.h. mbedtls 3.x API
 * (mbedtls_x509write_crt_set_serial_raw). Runs once on the core-1 net
 * bring-up task (8 KB stack: EC keygen needs far less than RSA's
 * Miller-Rabin recursion; RSA is deliberately not used).
 */

#include "cert_manager.h"

#include <array>
#include <string>

#include "esp_log.h"

#include "mbedtls/ctr_drbg.h"
#include "mbedtls/ecp.h"
#include "mbedtls/entropy.h"
#include "mbedtls/pk.h"
#include "mbedtls/x509_crt.h"
#include "mbedtls/x509_csr.h"

namespace esp32tap::net {

namespace {

const char* TAG = "esp32tap";
constexpr const char* CERT_PATH = "cert.pem";
constexpr const char* KEY_PATH = "key.pem";

}  // namespace

bool ensure_device_cert(storage::FsApi& fs, std::string& cert_pem,
                        std::string& key_pem) {
    if (fs.read_file(CERT_PATH, cert_pem) && fs.read_file(KEY_PATH, key_pem) &&
        !cert_pem.empty() && !key_pem.empty()) {
        ESP_LOGI(TAG, "TLS identity loaded from /data");
        return true;
    }

    ESP_LOGI(TAG, "generating per-device TLS identity (EC P-256) ...");
    mbedtls_pk_context key;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_x509write_cert crt;
    mbedtls_pk_init(&key);
    mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&drbg);
    mbedtls_x509write_crt_init(&crt);

    // Static buffers: PEM outputs are ~1-2 KB; keep them off the task
    // stack.
    static std::array<unsigned char, 2048> key_buf;
    static std::array<unsigned char, 2048> crt_buf;

    bool ok = false;
    const char* pers = "esp32tap-cert";
    do {
        if (mbedtls_ctr_drbg_seed(
                &drbg, mbedtls_entropy_func, &entropy,
                reinterpret_cast<const unsigned char*>(pers),
                std::char_traits<char>::length(pers)) != 0) {
            break;
        }
        if (mbedtls_pk_setup(&key,
                             mbedtls_pk_info_from_type(MBEDTLS_PK_ECKEY)) !=
            0) {
            break;
        }
        if (mbedtls_ecp_gen_key(MBEDTLS_ECP_DP_SECP256R1,
                                mbedtls_pk_ec(key), mbedtls_ctr_drbg_random,
                                &drbg) != 0) {
            break;
        }
        if (mbedtls_pk_write_key_pem(&key, key_buf.data(), key_buf.size()) !=
            0) {
            break;
        }

        mbedtls_x509write_crt_set_version(&crt, MBEDTLS_X509_CRT_VERSION_3);
        mbedtls_x509write_crt_set_md_alg(&crt, MBEDTLS_MD_SHA256);
        // Serial: 8 random bytes.
        unsigned char serial[8];
        if (mbedtls_ctr_drbg_random(&drbg, serial, sizeof(serial)) != 0) {
            break;
        }
        serial[0] &= 0x7f;  // positive
        if (mbedtls_x509write_crt_set_serial_raw(&crt, serial,
                                                 sizeof(serial)) != 0) {
            break;
        }
        if (mbedtls_x509write_crt_set_subject_name(&crt, "CN=treadmill") !=
                0 ||
            mbedtls_x509write_crt_set_issuer_name(&crt, "CN=treadmill") !=
                0) {
            break;
        }
        mbedtls_x509write_crt_set_subject_key(&crt, &key);
        mbedtls_x509write_crt_set_issuer_key(&crt, &key);
        // Long validity: the device has no reliable wall clock and
        // clients are trust-all anyway.
        if (mbedtls_x509write_crt_set_validity(&crt, "20250101000000",
                                               "20550101000000") != 0) {
            break;
        }
        if (mbedtls_x509write_crt_pem(&crt, crt_buf.data(), crt_buf.size(),
                                      mbedtls_ctr_drbg_random, &drbg) != 0) {
            break;
        }

        cert_pem.assign(reinterpret_cast<const char*>(crt_buf.data()));
        key_pem.assign(reinterpret_cast<const char*>(key_buf.data()));
        if (cert_pem.empty() || key_pem.empty()) break;
        if (!fs.write_file_atomic(CERT_PATH, cert_pem) ||
            !fs.write_file_atomic(KEY_PATH, key_pem)) {
            // Persist failure: still serve this boot's identity.
            ESP_LOGW(TAG, "TLS identity persist failed (serving ephemeral)");
        }
        ok = true;
    } while (false);

    mbedtls_x509write_crt_free(&crt);
    mbedtls_ctr_drbg_free(&drbg);
    mbedtls_entropy_free(&entropy);
    mbedtls_pk_free(&key);
    if (!ok) {
        ESP_LOGE(TAG, "TLS identity generation FAILED");
    } else {
        ESP_LOGI(TAG, "TLS identity ready (CN=treadmill, EC P-256)");
    }
    return ok;
}

}  // namespace esp32tap::net
