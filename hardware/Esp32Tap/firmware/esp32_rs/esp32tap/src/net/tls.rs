//! Slice 3 — on-device TLS identity. **NOT YET WIRED — see BLOCKER.**
//!
//! BLOCKER (found by experiment, recorded so the next attempt starts here):
//! `esp_https_server` is absent from the esp-idf-sys bindings — `httpd_ssl_*`
//! generates zero symbols. The obvious fix,
//! `[package.metadata.esp-idf-sys] esp_idf_components = [...]`, is an
//! EXCLUSIVE whitelist: naming five components dropped every other one and the
//! CMake build failed outright. The correct mechanism is a custom bindings
//! header that `#include`s esp_https_server.h, which needs esp-idf-sys's
//! actual documented metadata read once rather than probed a rebuild at a time.
//!
//! DEFERRED DELIBERATELY. This code compiles and is unit-testable, but nothing
//! calls it. TLS buys little today — no board exists, both clients trust-all
//! (`TrustAllDelegate`, `trustAllTls`), and every QEMU proof runs over HTTP —
//! while the remaining Slice 3 work (mDNS, profile endpoints) does not depend
//! on it. Finish this when a board exists or when the bearer-token/TOFU
//! decision lands, whichever comes first.
//!
//! Generates a self-signed EC P-256 certificate at boot so the device has its
//! own identity rather than a key baked into the image (which every unit would
//! then share, and which anyone with the firmware would hold).
//!
//! CURRENT LIMITATION, DELIBERATE AND RECORDED: the key is generated fresh on
//! every boot and NOT persisted. Clients in this project trust-all
//! (`TrustAllDelegate` on iOS, `trustAllTls` on Android), so a changing
//! identity is tolerable today — but it defeats trust-on-first-use, which is
//! exactly what the pending bearer-token/TOFU decision would rely on. Persisting
//! to NVS is the follow-up, and TOFU cannot be honestly claimed until it lands.
//!
//! WHY P-256 AND NOT RSA: keygen happens on the boot path. An RSA-2048
//! generation on this part is seconds of blocking work with no upper bound
//! worth trusting; P-256 is milliseconds and is what the C++ attempt used too.
//!
//! EVERY mbedtls SIGNATURE HERE WAS READ FROM THE GENERATED BINDINGS, not from
//! the C headers or memory — the two disagree often enough (const vs mut, the
//! `mbedtls_f_rng_t` alias, serial as raw bytes rather than an mpi) that
//! guessing costs a build cycle each time.

use esp_idf_sys as sys;

/// PEM output sizes. Fixed buffers, not allocations: this runs once at boot and
/// a P-256 cert/key pair is comfortably inside these.
const CERT_PEM_MAX: usize = 1024;
const KEY_PEM_MAX: usize = 512;

/// A generated identity, owned for the lifetime of the server.
///
/// Held as fixed arrays so the pointers handed to `httpd_ssl_config_t` stay
/// valid for as long as the server runs — the server task outlives the frame
/// that starts it, so borrowing from the stack would be a use-after-free.
pub struct Identity {
    pub cert_pem: [u8; CERT_PEM_MAX],
    pub cert_len: usize,
    pub key_pem: [u8; KEY_PEM_MAX],
    pub key_len: usize,
}

impl Identity {
    const fn empty() -> Self {
        Identity {
            cert_pem: [0; CERT_PEM_MAX],
            cert_len: 0,
            key_pem: [0; KEY_PEM_MAX],
            key_len: 0,
        }
    }
}

/// Generate a fresh self-signed P-256 identity.
///
/// Returns the mbedtls error code on failure so the caller can log the real
/// reason instead of a generic "TLS failed".
pub fn generate() -> Result<Identity, i32> {
    let mut id = Identity::empty();

    // SAFETY: every context below is stack-owned, initialised by its
    // `_init` before use and released by its `_free` on every exit path
    // (including the early returns, which is why they are grouped in one
    // block with a single cleanup label rather than scattered `?`).
    unsafe {
        let mut entropy: sys::mbedtls_entropy_context = core::mem::zeroed();
        let mut drbg: sys::mbedtls_ctr_drbg_context = core::mem::zeroed();
        let mut key: sys::mbedtls_pk_context = core::mem::zeroed();
        let mut crt: sys::mbedtls_x509write_cert = core::mem::zeroed();

        sys::mbedtls_entropy_init(&mut entropy);
        sys::mbedtls_ctr_drbg_init(&mut drbg);
        sys::mbedtls_pk_init(&mut key);
        sys::mbedtls_x509write_crt_init(&mut crt);

        let cleanup = |crt: &mut sys::mbedtls_x509write_cert,
                       key: &mut sys::mbedtls_pk_context,
                       drbg: &mut sys::mbedtls_ctr_drbg_context,
                       entropy: &mut sys::mbedtls_entropy_context| {
            sys::mbedtls_x509write_crt_free(crt);
            sys::mbedtls_pk_free(key);
            sys::mbedtls_ctr_drbg_free(drbg);
            sys::mbedtls_entropy_free(entropy);
        };

        let seed = c"esp32tap";
        let rc = sys::mbedtls_ctr_drbg_seed(
            &mut drbg,
            Some(sys::mbedtls_entropy_func),
            &mut entropy as *mut _ as *mut core::ffi::c_void,
            seed.as_ptr() as *const u8,
            seed.count_bytes(),
        );
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }

        let info = sys::mbedtls_pk_info_from_type(sys::mbedtls_pk_type_t_MBEDTLS_PK_ECKEY);
        let rc = sys::mbedtls_pk_setup(&mut key, info);
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }
        let rc = sys::mbedtls_ecp_gen_key(
            sys::mbedtls_ecp_group_id_MBEDTLS_ECP_DP_SECP256R1,
            // `mbedtls_pk_ec` is a C inline and does not survive bindgen.
            // For an ECKEY context, `private_pk_ctx` IS the ecp_keypair — the
            // same thing that macro returns.
            key.private_pk_ctx as *mut sys::mbedtls_ecp_keypair,
            Some(sys::mbedtls_ctr_drbg_random),
            &mut drbg as *mut _ as *mut core::ffi::c_void,
        );
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }

        // Self-signed: subject and issuer are the same key and the same name.
        let name = c"CN=esp32tap,O=precor-treadmill";
        sys::mbedtls_x509write_crt_set_subject_key(&mut crt, &mut key);
        sys::mbedtls_x509write_crt_set_issuer_key(&mut crt, &mut key);
        let rc = sys::mbedtls_x509write_crt_set_subject_name(&mut crt, name.as_ptr());
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }
        let rc = sys::mbedtls_x509write_crt_set_issuer_name(&mut crt, name.as_ptr());
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }
        sys::mbedtls_x509write_crt_set_version(&mut crt, sys::MBEDTLS_X509_CRT_VERSION_3 as i32);
        sys::mbedtls_x509write_crt_set_md_alg(&mut crt, sys::mbedtls_md_type_t_MBEDTLS_MD_SHA256);

        let mut serial = [0x01u8];
        let rc = sys::mbedtls_x509write_crt_set_serial_raw(&mut crt, serial.as_mut_ptr(), 1);
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }
        // The device has no wall clock at boot (no SNTP yet), so the validity
        // window is fixed and wide rather than derived from a time we do not
        // have. A client that checked dates would still accept it; ours do not
        // check at all.
        let rc = sys::mbedtls_x509write_crt_set_validity(
            &mut crt,
            c"20240101000000".as_ptr(),
            c"20440101000000".as_ptr(),
        );
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }

        // PEM writers fill from the END of the buffer and return 0 on success,
        // so the length is measured by finding the NUL — not by the return.
        let rc = sys::mbedtls_x509write_crt_pem(
            &mut crt,
            id.cert_pem.as_mut_ptr(),
            CERT_PEM_MAX,
            Some(sys::mbedtls_ctr_drbg_random),
            &mut drbg as *mut _ as *mut core::ffi::c_void,
        );
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }
        let rc = sys::mbedtls_pk_write_key_pem(&key, id.key_pem.as_mut_ptr(), KEY_PEM_MAX);
        if rc != 0 {
            cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
            return Err(rc);
        }

        cleanup(&mut crt, &mut key, &mut drbg, &mut entropy);
    }

    // esp_https_server wants the PEM length INCLUDING the terminating NUL.
    id.cert_len = nul_len(&id.cert_pem);
    id.key_len = nul_len(&id.key_pem);
    if id.cert_len == 0 || id.key_len == 0 {
        return Err(-1);
    }
    Ok(id)
}

fn nul_len(buf: &[u8]) -> usize {
    match buf.iter().position(|&b| b == 0) {
        Some(i) => i + 1,
        None => 0,
    }
}
