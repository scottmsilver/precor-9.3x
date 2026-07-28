//! Slice 3 — the device's own TLS identity, generated once and PERSISTED.
//!
//! HOW THE RECORDED BLOCKER WAS ACTUALLY FIXED (it was not what it looked
//! like). The note here used to say `esp_https_server` was "absent from the
//! esp-idf-sys bindings" and that the fix needed a custom bindings header. Both
//! halves were wrong, and the reason the earlier attempt could not find it is
//! that it probed the build instead of reading esp-idf-sys's own docs:
//!
//!   * The component was being BUILT the whole time. The build script emits
//!     `esp_idf_comp_esp_https_server_enabled`, which it only does for
//!     components it compiled.
//!   * esp-idf-sys's stock `src/include/esp-idf/bindings.h` ALREADY has
//!     `#ifdef CONFIG_ESP_HTTPS_SERVER_ENABLE / #include "esp_https_server.h"`.
//!     The gate is the KCONFIG SYMBOL, not the component list, and the
//!     generated sdkconfig read `# CONFIG_ESP_HTTPS_SERVER_ENABLE is not set`.
//!
//! So a custom `bindings_header` was never needed and `esp_idf_components`
//! (an exclusive whitelist that TRIMS the build) was the opposite of the right
//! lever. The whole fix is one line in `sdkconfig.defaults`.
//!
//! PERSISTENCE. The identity is stored in NVS and reloaded on the next boot,
//! so a client that pinned the certificate keeps working across reboots —
//! without that, trust-on-first-use is not merely weak, it is meaningless,
//! because "first use" would be every power cycle. The private key lives in the
//! `nvs` partition in the clear: this part has no flash encryption enabled and
//! claiming otherwise would be worse than saying so. Anyone with physical
//! access to the flash has the key, which is the same exposure as the Pi's
//! `key.pem` on its SD card.
//!
//! WHY P-256 AND NOT RSA: keygen happens on the boot path. An RSA-2048
//! generation on this part is seconds of blocking work with no upper bound
//! worth trusting; P-256 is milliseconds and is what the C++ attempt used too.
//!
//! EVERY mbedtls SIGNATURE HERE WAS READ FROM THE GENERATED BINDINGS, not from
//! the C headers or memory — the two disagree often enough (const vs mut, the
//! `mbedtls_f_rng_t` alias, serial as raw bytes rather than an mpi) that
//! guessing costs a build cycle each time.

use crate::logi;
use esp_idf_sys as sys;

/// PEM output sizes. Fixed buffers, not allocations: a P-256 cert/key pair is
/// comfortably inside these, and a bound is what lets the NVS read be a single
/// fixed-size call with no "ask for the length, then allocate" dance.
const CERT_PEM_MAX: usize = 1024;
const KEY_PEM_MAX: usize = 512;

/// NVS namespace and keys. NVS key names are capped at 15 characters.
const NVS_NAMESPACE: &core::ffi::CStr = c"esp32tap";
const NVS_KEY_CERT: &core::ffi::CStr = c"tls_cert";
const NVS_KEY_KEY: &core::ffi::CStr = c"tls_key";

/// A device identity, owned for the lifetime of the server.
///
/// Held as fixed arrays so the pointers handed to `httpd_ssl_config_t` stay
/// valid for as long as the server runs — the server task outlives the frame
/// that starts it, so borrowing from the stack would be a use-after-free.
pub struct Identity {
    cert_pem: [u8; CERT_PEM_MAX],
    cert_len: usize,
    key_pem: [u8; KEY_PEM_MAX],
    key_len: usize,
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

    /// Certificate PEM, INCLUDING the terminating NUL — the length
    /// `esp_https_server` expects.
    pub fn cert(&self) -> &[u8] {
        &self.cert_pem[..self.cert_len]
    }

    /// Private-key PEM, INCLUDING the terminating NUL.
    pub fn key(&self) -> &[u8] {
        &self.key_pem[..self.key_len]
    }

    /// A PEM pair is only usable if both halves are NUL-terminated text of a
    /// plausible size. Checked on the way OUT of NVS as well as on the way in,
    /// because a truncated or half-written blob must send us back to keygen
    /// rather than into mbedtls with garbage.
    fn is_plausible(&self) -> bool {
        self.cert_len > 64
            && self.cert_len <= CERT_PEM_MAX
            && self.key_len > 64
            && self.key_len <= KEY_PEM_MAX
            && self.cert_pem[self.cert_len - 1] == 0
            && self.key_pem[self.key_len - 1] == 0
            && self.cert_pem.starts_with(b"-----BEGIN")
            && self.key_pem.starts_with(b"-----BEGIN")
    }
}

/// Where the identity in use came from. Logged, and asserted by the scenarios:
/// a second boot that still says `Generated` means persistence silently broke.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Origin {
    /// Reloaded from NVS — a client that pinned the cert last boot still works.
    Nvs,
    /// Freshly generated this boot (first boot, or NVS was unusable).
    Generated,
}

// --- NVS boundary -----------------------------------------------------------
//
// One `unsafe` per C call, each with the invariant it depends on. All of it
// runs once, on the main task, before the server exists.

fn nvs_init() -> sys::esp_err_t {
    // SAFETY: no arguments; idempotent IDF init returning esp_err_t.
    let rc = unsafe { sys::nvs_flash_init() };
    if rc == sys::ESP_ERR_NVS_NO_FREE_PAGES || rc == sys::ESP_ERR_NVS_NEW_VERSION_FOUND {
        // A partition written by another firmware generation, or one that ran
        // out of pages. Erasing costs us the stored identity (clients re-pin)
        // and is strictly better than running with no NVS at all.
        // SAFETY: no arguments; erases the default `nvs` partition.
        unsafe { sys::nvs_flash_erase() };
        // SAFETY: as above.
        return unsafe { sys::nvs_flash_init() };
    }
    rc
}

fn nvs_open_rw(out: &mut sys::nvs_handle_t) -> sys::esp_err_t {
    // SAFETY: the namespace is a `'static` NUL-terminated literal read for the
    // call; `out` is a live exclusive borrow written once on success.
    unsafe {
        sys::nvs_open(
            NVS_NAMESPACE.as_ptr(),
            sys::nvs_open_mode_t_NVS_READWRITE,
            out,
        )
    }
}

/// Read one blob into `buf`, returning the byte count actually read.
fn nvs_read(h: sys::nvs_handle_t, key: &core::ffi::CStr, buf: &mut [u8]) -> Option<usize> {
    let mut len = buf.len();
    // SAFETY: `h` is a live handle; `key` is a `'static` literal; `buf` is a
    // live exclusive borrow and `len` tells the callee its exact capacity, so
    // the write is bounded by the slice. `len` is updated to the real size.
    let rc = unsafe {
        sys::nvs_get_blob(
            h,
            key.as_ptr(),
            buf.as_mut_ptr() as *mut core::ffi::c_void,
            &mut len,
        )
    };
    if rc == sys::ESP_OK {
        Some(len)
    } else {
        None
    }
}

fn nvs_write(h: sys::nvs_handle_t, key: &core::ffi::CStr, data: &[u8]) -> sys::esp_err_t {
    // SAFETY: `h` is live; `key` is a `'static` literal; `data` is a live
    // shared borrow read for exactly `data.len()` bytes.
    unsafe {
        sys::nvs_set_blob(
            h,
            key.as_ptr(),
            data.as_ptr() as *const core::ffi::c_void,
            data.len(),
        )
    }
}

fn nvs_commit(h: sys::nvs_handle_t) -> sys::esp_err_t {
    // SAFETY: `h` is a live handle.
    unsafe { sys::nvs_commit(h) }
}

fn nvs_close(h: sys::nvs_handle_t) {
    // SAFETY: `h` is a live handle and is never used again after this.
    unsafe { sys::nvs_close(h) }
}

/// Fill `id` from NVS. Returns false (leaving `id` unusable) on any problem —
/// a missing key, a short read, or a blob that does not look like PEM.
fn load(id: &mut Identity) -> bool {
    let mut h: sys::nvs_handle_t = 0;
    if nvs_open_rw(&mut h) != sys::ESP_OK {
        return false;
    }
    let cert = nvs_read(h, NVS_KEY_CERT, &mut id.cert_pem);
    let key = nvs_read(h, NVS_KEY_KEY, &mut id.key_pem);
    nvs_close(h);
    match (cert, key) {
        (Some(c), Some(k)) => {
            id.cert_len = c;
            id.key_len = k;
            id.is_plausible()
        }
        _ => false,
    }
}

/// Persist `id`, then READ IT BACK and compare.
///
/// The read-back is the point. "nvs_set_blob returned ESP_OK" says the call was
/// accepted, not that the bytes are retrievable; the property this feature
/// actually needs is that the next boot gets the same key back, and the
/// round-trip is the closest thing to that which can be checked here.
fn store(id: &Identity) -> Result<(), sys::esp_err_t> {
    let mut h: sys::nvs_handle_t = 0;
    let rc = nvs_open_rw(&mut h);
    if rc != sys::ESP_OK {
        return Err(rc);
    }
    let mut rc = nvs_write(h, NVS_KEY_CERT, id.cert());
    if rc == sys::ESP_OK {
        rc = nvs_write(h, NVS_KEY_KEY, id.key());
    }
    if rc == sys::ESP_OK {
        rc = nvs_commit(h);
    }
    nvs_close(h);
    if rc != sys::ESP_OK {
        return Err(rc);
    }

    let mut back = Identity::empty();
    if !load(&mut back) || back.cert() != id.cert() || back.key() != id.key() {
        return Err(sys::ESP_FAIL);
    }
    Ok(())
}

// --- identity ---------------------------------------------------------------

/// Load the persisted identity, or generate and persist a new one.
///
/// The returned reference is `'static` because `httpd_ssl_start` keeps the PEM
/// pointers for the life of the server, which outlives every frame here. The
/// leak is ONE fixed 1.5 KB allocation at boot, not a per-request one — the
/// budget discipline in `reqbudget` is about request scope and is untouched.
///
/// `Identity` is built behind a `Box` rather than returned by value on purpose:
/// `mbedtls_x509write_crt_pem` already parks a 4 KB DER buffer on the caller's
/// stack, and adding a 1.5 KB move of our own on the main task is how you get a
/// stack overflow that only reproduces on the boot path.
pub fn identity() -> Result<(&'static Identity, Origin), i32> {
    let rc = nvs_init();
    if rc != sys::ESP_OK {
        // Without NVS there is no persistence, but there can still be TLS.
        // Say so rather than failing the whole server.
        logi!("tls: nvs unavailable (err {}) — identity will not persist", rc);
    }

    let mut id = Box::new(Identity::empty());

    if rc == sys::ESP_OK && load(&mut id) {
        return Ok((Box::leak(id), Origin::Nvs));
    }

    *id = Identity::empty();
    generate_into(&mut id)?;

    if rc == sys::ESP_OK {
        match store(&id) {
            Ok(()) => logi!("tls: identity persisted to NVS (readback ok)"),
            // Deliberately not fatal: an unwritable NVS costs persistence, not
            // the ability to serve. The next boot regenerates.
            Err(e) => logi!("tls: NVS store failed (err {}) — identity is boot-local", e),
        }
    }
    Ok((Box::leak(id), Origin::Generated))
}

/// Generate a fresh self-signed P-256 identity into `id`.
///
/// Returns the mbedtls error code on failure so the caller can log the real
/// reason instead of a generic "TLS failed".
fn generate_into(id: &mut Identity) -> Result<(), i32> {
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
    if !id.is_plausible() {
        return Err(-1);
    }
    Ok(())
}

fn nul_len(buf: &[u8]) -> usize {
    match buf.iter().position(|&b| b == 0) {
        Some(i) => i + 1,
        None => 0,
    }
}
