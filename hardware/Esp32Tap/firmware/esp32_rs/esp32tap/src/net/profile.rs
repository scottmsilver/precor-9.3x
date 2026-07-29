//! The profile — one of them, and now a REAL one.
//!
//! # What changed and why it had to
//!
//! This surface used to be a single `const PROFILE_JSON` with an honest note
//! attached: a rename would be lost on reboot, so rename was not offered.
//! That was the right call while there was nowhere to put it. There is now, so
//! the note is discharged rather than repeated: `PUT /api/profiles/{id}`
//! exists, and what it writes survives a power cut.
//!
//! # NVS, not a fourth flash ring
//!
//! A profile is ~60 bytes. A `recstore` ring costs one 4 KB sector per record
//! by construction (NOR erase granularity — see `recstore::slot_is_sector_safe`),
//! so a ring for this would spend 4 KB to store 60 bytes and add a fourth
//! mount to the boot path. NVS is already initialised for the TLS identity,
//! already does wear levelling and atomic commits, and this reuses its
//! helpers rather than opening a second boundary to the same subsystem.
//!
//! # Still one profile, and still no avatars
//!
//! `POST /api/profiles` and `DELETE /api/profiles/{id}` are Pi-only
//! multi-profile features and are NOT implemented: building profile_id
//! plumbing for a device with one profile adds an id-confusion surface for no
//! user-visible benefit. `has_avatar` stays `false` — the Pi stores avatars as
//! BLOBs up to 1 MB and there is no image pipeline, no codec and no flash
//! budget for that here. Emitting `true` would be a promise nothing keeps.

use crate::net::api::{read_body, respond, MAX_CMD_BODY};
use crate::net::tls;
use esp_idf_sys as sys;
use safety_core::FixedStr;
use std::sync::Mutex;

const NVS_KEY: &core::ffi::CStr = c"profile";
const VERSION: u8 = 1;

/// `DEFAULT_WEIGHT_LBS` in `python/server.py`.
const DEFAULT_WEIGHT_LBS: u16 = 154;

const MAX_NAME: usize = 24;
const MAX_COLOR: usize = 8;
const MAX_INITIALS: usize = 3;

/// The stored profile. Every string is bounded; the app supplies all three.
#[derive(Clone, Copy)]
pub struct Profile {
    pub name: FixedStr<MAX_NAME>,
    pub color: FixedStr<MAX_COLOR>,
    pub initials: FixedStr<MAX_INITIALS>,
    /// Whole pounds, as `db.py` stores them (INTEGER columns). Emitted with a
    /// decimal point because the Kotlin model types them `Double`; its
    /// `LenientDoubleSerializer` would accept either, so this is the Pi's
    /// on-wire shape rather than a workaround.
    pub weight_lbs: u16,
    pub vest_lbs: u16,
}

impl Profile {
    const fn default_profile() -> Profile {
        Profile {
            name: FixedStr::new(),
            color: FixedStr::new(),
            initials: FixedStr::new(),
            weight_lbs: DEFAULT_WEIGHT_LBS,
            vest_lbs: 0,
        }
    }

    fn seeded() -> Profile {
        let mut p = Profile::default_profile();
        p.name = FixedStr::from_str_truncating("Runner");
        p.color = FixedStr::from_str_truncating("#d4c4a8");
        p.initials = FixedStr::from_str_truncating("R");
        p
    }

    /// Body mass for the ACSM calorie equation, in grams.
    ///
    /// `server.py::_user_weight_kg` — `weight_lbs + vest_lbs`, converted. This
    /// is the one place the profile is load-bearing rather than cosmetic: a
    /// rename that did not persist would be a nuisance, but a weight that did
    /// not persist would silently mis-count every run after a reboot.
    pub fn weight_grams(&self) -> u64 {
        let lbs = self.weight_lbs as u64 + self.vest_lbs as u64;
        lbs * 45359 / 100
    }
}

static PROFILE: Mutex<Profile> = Mutex::new(Profile::default_profile());

fn encode(p: &Profile, buf: &mut [u8]) -> Option<usize> {
    let mut n = 0usize;
    let mut put = |b: u8, n: &mut usize| -> Option<()> {
        *buf.get_mut(*n)? = b;
        *n += 1;
        Some(())
    };
    put(VERSION, &mut n)?;
    put((p.weight_lbs & 0xff) as u8, &mut n)?;
    put((p.weight_lbs >> 8) as u8, &mut n)?;
    put((p.vest_lbs & 0xff) as u8, &mut n)?;
    put((p.vest_lbs >> 8) as u8, &mut n)?;
    for s in [p.name.as_bytes(), p.color.as_bytes(), p.initials.as_bytes()] {
        put(s.len() as u8, &mut n)?;
        for &c in s {
            put(c, &mut n)?;
        }
    }
    Some(n)
}

fn decode(b: &[u8]) -> Option<Profile> {
    let mut i = 0usize;
    let next = |i: &mut usize| -> Option<u8> {
        let v = *b.get(*i)?;
        *i += 1;
        Some(v)
    };
    if next(&mut i)? != VERSION {
        return None;
    }
    let mut p = Profile::default_profile();
    p.weight_lbs = next(&mut i)? as u16 | ((next(&mut i)? as u16) << 8);
    p.vest_lbs = next(&mut i)? as u16 | ((next(&mut i)? as u16) << 8);
    for field in 0..3 {
        let len = next(&mut i)? as usize;
        if i + len > b.len() {
            return None;
        }
        for k in 0..len {
            let c = b[i + k];
            match field {
                0 => p.name.push_byte(c),
                1 => p.color.push_byte(c),
                _ => p.initials.push_byte(c),
            }
        }
        i += len;
    }
    Some(p)
}

/// Load the profile from NVS, or seed the built-in one. Call once at boot.
pub fn load() {
    let mut h: sys::nvs_handle_t = 0;
    let mut loaded: Option<Profile> = None;
    if tls::nvs_open_rw(&mut h) == sys::ESP_OK {
        let mut buf = [0u8; 128];
        if let Some(n) = tls::nvs_read(h, NVS_KEY, &mut buf) {
            loaded = decode(&buf[..n]);
        }
        tls::nvs_close(h);
    }
    *crate::context::lock(&PROFILE) = loaded.unwrap_or_else(Profile::seeded);
}

/// Persist the profile. The READ-BACK is the point, exactly as the TLS
/// identity does it: "nvs_set_blob returned ESP_OK" says the call was accepted,
/// not that the next boot will find it.
fn store(p: &Profile) -> bool {
    let mut buf = [0u8; 128];
    let Some(n) = encode(p, &mut buf) else {
        return false;
    };
    let mut h: sys::nvs_handle_t = 0;
    if tls::nvs_open_rw(&mut h) != sys::ESP_OK {
        return false;
    }
    let mut ok = tls::nvs_write(h, NVS_KEY, &buf[..n]) == sys::ESP_OK;
    if ok {
        ok = tls::nvs_commit(h) == sys::ESP_OK;
    }
    let mut back = [0u8; 128];
    if ok {
        ok = matches!(tls::nvs_read(h, NVS_KEY, &mut back), Some(m) if back[..m] == buf[..n]);
    }
    tls::nvs_close(h);
    ok
}

/// The current profile, copied out. `Profile` is `Copy`, so no caller holds
/// the lock while it renders.
pub fn current() -> Profile {
    *crate::context::lock(&PROFILE)
}

// ---------------------------------------------------------------------------
// Wire shape. Every field the Kotlin `Profile` model declares, all present and
// real. Ids are the constant `"local"`: there is one profile, and inventing an
// id space for it would be state to keep consistent for no benefit.
// ---------------------------------------------------------------------------

const PROFILE_BUF: usize = 256;

struct Buf {
    b: [u8; PROFILE_BUF],
    n: usize,
}

impl core::fmt::Write for Buf {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let x = s.as_bytes();
        if self.n + x.len() > PROFILE_BUF {
            return Err(core::fmt::Error);
        }
        self.b[self.n..self.n + x.len()].copy_from_slice(x);
        self.n += x.len();
        Ok(())
    }
}

fn render(lead: &str, tail: &str) -> Buf {
    use core::fmt::Write;
    let p = current();
    let mut b = Buf {
        b: [0u8; PROFILE_BUF],
        n: 0,
    };
    let _ = write!(
        b,
        concat!(
            r#"{}{{"id":"local","name":"{}","color":"{}","initials":"{}","#,
            r#""weight_lbs":{}.0,"vest_lbs":{}.0,"has_avatar":false}}{}"#
        ),
        lead,
        p.name.as_str(),
        p.color.as_str(),
        p.initials.as_str(),
        p.weight_lbs,
        p.vest_lbs,
        tail
    );
    b
}

/// GET /api/profiles.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn list_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let b = render("[", "]");
    respond(req, c"200 OK", &b.b[..b.n])
}

/// GET /api/profile/active.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn active_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let b = render(r#"{"guest_mode":false,"profile":"#, "}");
    respond(req, c"200 OK", &b.b[..b.n])
}

/// POST /api/profile/select — accept the selection of the only profile there
/// is. Deliberately does not honour an arbitrary id: there is nothing to
/// choose between, and pretending otherwise is a lie the storage tier would
/// have to keep.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn select_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    let b = render(r#"{"ok":true,"guest_mode":false,"profile":"#, "}");
    respond(req, c"200 OK", &b.b[..b.n])
}

/// PUT /api/profiles/{id} — rename, recolour, reweigh. THE endpoint that makes
/// this tier honest.
///
/// SAFETY: `req` is live for the call; nothing derived from it is retained.
unsafe extern "C" fn update_handler(req: *mut sys::httpd_req_t) -> sys::esp_err_t {
    // Bounded by the request's own lifetime, not by a caller-chosen one.
    let uri = core::ffi::CStr::from_ptr((*req).uri.as_ptr());
    update_impl(req, uri.to_str().unwrap_or(""))
}

fn update_impl(req: *mut sys::httpd_req_t, uri: &str) -> sys::esp_err_t {
    // THE ID IN THE PATH IS CHECKED. A wildcard route hands this handler
    // everything under `/api/profiles/`, so without this
    // `PUT /api/profiles/someone-elses-id` would silently rewrite the local
    // profile and answer as though it had updated the one that was asked for.
    let id = uri
        .strip_prefix("/api/profiles/")
        .map(|r| r.split('/').next().unwrap_or(""))
        .unwrap_or("");
    if id != "local" {
        return respond(
            req,
            c"404 Not Found",
            br#"{"ok":false,"error":"Not found"}"#,
        );
    }
    let mut body = [0u8; MAX_CMD_BODY];
    let Some(n) = read_body(req, &mut body) else {
        return sys::ESP_OK;
    };
    let body = &body[..n];

    let mut p = current();
    let mut name: FixedStr<MAX_NAME> = FixedStr::new();
    if crate::net::records::extract_str(body, "name", &mut name) && !name.is_empty() {
        p.name = name;
    }
    let mut color: FixedStr<MAX_COLOR> = FixedStr::new();
    if crate::net::records::extract_str(body, "color", &mut color) && !color.is_empty() {
        p.color = color;
    }
    let mut initials: FixedStr<MAX_INITIALS> = FixedStr::new();
    if crate::net::records::extract_str(body, "initials", &mut initials) {
        p.initials = initials;
    }
    // `Field(ge=0, le=500)` on the Pi. Clamped rather than refused: the app
    // has no client-side bound, and a refusal would lose a rename that came in
    // the same request.
    if let Some(v) = crate::net::api::parse_key_hundredths(body, b"weight_lbs") {
        p.weight_lbs = (v / 100).clamp(0, 500) as u16;
    }
    if let Some(v) = crate::net::api::parse_key_hundredths(body, b"vest_lbs") {
        p.vest_lbs = (v / 100).clamp(0, 500) as u16;
    }

    *crate::context::lock(&PROFILE) = p;
    if !store(&p) {
        // The in-RAM profile is already updated, so the app sees its change;
        // say plainly that it will not survive a reboot rather than reporting
        // a success that is half true.
        return respond(
            req,
            c"500 Internal Server Error",
            br#"{"ok":false,"error":"profile could not be persisted"}"#,
        );
    }
    let b = render(r#"{"ok":true,"profile":"#, "}");
    respond(req, c"200 OK", &b.b[..b.n])
}

/// Register the profile routes.
pub fn register(handle: sys::httpd_handle_t) -> Result<(), sys::esp_err_t> {
    // SAFETY: a type alias only. IDF handlers are `unsafe extern "C"` by
    // signature; naming that type lets the table below hold them uniformly and
    // introduces no unsafe operation of its own.
    type H = unsafe extern "C" fn(*mut sys::httpd_req_t) -> sys::esp_err_t;
    let routes: [(&core::ffi::CStr, u32, H); 4] = [
        (c"/api/profiles", sys::http_method_HTTP_GET, list_handler),
        (c"/api/profile/active", sys::http_method_HTTP_GET, active_handler),
        (c"/api/profile/select", sys::http_method_HTTP_POST, select_handler),
        (c"/api/profiles/*", sys::http_method_HTTP_PUT, update_handler),
    ];
    for (path, method, handler) in routes {
        let uri = sys::httpd_uri_t {
            uri: path.as_ptr(),
            method,
            handler: Some(handler),
            user_ctx: core::ptr::null_mut(),
            is_websocket: false,
            handle_ws_control_frames: false,
            supported_subprotocol: core::ptr::null(),
        };
        // SAFETY: `handle` is a live server; `uri` is read for the call and
        // IDF copies what it retains. The handler is a `'static` fn.
        let err = unsafe { sys::httpd_register_uri_handler(handle, &uri) };
        if err != sys::ESP_OK {
            return Err(err);
        }
    }
    Ok(())
}
