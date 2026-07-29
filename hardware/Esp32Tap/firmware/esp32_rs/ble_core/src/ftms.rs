//! FTMS (Fitness Machine Service, 0x1826) wire encoding and Control Point
//! parsing — a `no_std`, allocation-free port of `rust/ftms/src/protocol.rs`
//! and the encoders in `rust/ftms/src/ftms_service.rs`.
//!
//! All multi-byte values are LITTLE-endian, per the GATT specification. FTMS
//! is metric: speed is km/h x 100, inclination is percent x 10. The belt is
//! imperial and coarser: tenths of mph, half-percent. The conversion is the
//! interesting part of this module and it is lossy in BOTH directions; see
//! [`mph_tenths_to_kmh_hundredths`].

use safety_core::units::{InclineHalfPct, SpeedTenths};

// --- UUIDs ----------------------------------------------------------------
//
// 16-bit SIG shorts, because NimBLE registers those directly
// (`BLE_UUID16_DECLARE`). The Pi daemon carries 128-bit `Uuid`s only because
// `bluer` demands them; `sig_uuid128` reproduces that expansion exactly and is
// pinned to the daemon's value in a test, so the two can be compared without
// taking the `uuid` crate.

/// Fitness Machine Service.
pub const SERVICE_FTMS: u16 = 0x1826;
/// Fitness Machine Feature (read).
pub const CHAR_FEATURE: u16 = 0x2ACC;
/// Treadmill Data (notify, 1 Hz).
pub const CHAR_TREADMILL_DATA: u16 = 0x2ACD;
/// Training Status (notify).
pub const CHAR_TRAINING_STATUS: u16 = 0x2AD3;
/// Supported Speed Range (read).
pub const CHAR_SPEED_RANGE: u16 = 0x2AD4;
/// Supported Inclination Range (read).
pub const CHAR_INCLINE_RANGE: u16 = 0x2AD5;
/// Fitness Machine Control Point (write + indicate).
pub const CHAR_CONTROL_POINT: u16 = 0x2AD9;
/// Fitness Machine Status (notify).
pub const CHAR_MACHINE_STATUS: u16 = 0x2ADA;

/// Expand a 16-bit SIG UUID into its 128-bit form, big-endian bytes:
/// `0000XXXX-0000-1000-8000-00805f9b34fb`.
///
/// Byte-identical to the daemon's `Uuid::from_u128(((short as u128) << 96) |
/// 0x0000_0000_0000_1000_8000_00805f9b34fb)`, which is what
/// `sig_uuid128_matches_the_pi_daemon_expansion` asserts.
pub const fn sig_uuid128(short: u16) -> [u8; 16] {
    [
        0x00,
        0x00,
        (short >> 8) as u8,
        (short & 0xFF) as u8,
        0x00,
        0x00,
        0x10,
        0x00,
        0x80,
        0x00,
        0x00,
        0x80,
        0x5F,
        0x9B,
        0x34,
        0xFB,
    ]
}

// --- Control Point result codes (FTMS spec Table 4.24) --------------------

pub const RESULT_SUCCESS: u8 = 0x01;
pub const RESULT_NOT_SUPPORTED: u8 = 0x02;
pub const RESULT_INVALID_PARAM: u8 = 0x03;
pub const RESULT_FAILED: u8 = 0x04;
/// First byte of every Control Point indication.
pub const RESPONSE_CODE: u8 = 0x80;

// --- Control Point opcodes ------------------------------------------------

pub const OP_REQUEST_CONTROL: u8 = 0x00;
pub const OP_SET_TARGET_SPEED: u8 = 0x02;
pub const OP_SET_TARGET_INCLINATION: u8 = 0x03;
pub const OP_START_OR_RESUME: u8 = 0x07;
pub const OP_STOP_OR_PAUSE: u8 = 0x08;

/// A Control Point write, already validated for LENGTH and opcode.
///
/// The payloads are still RAW FTMS units and still UNTRUSTED — `i16::MIN`
/// tenths of a percent is a well-formed `SetTargetInclination`. Turning one of
/// these into something the belt could act on goes through [`CpEffect`].
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ControlCommand {
    RequestControl,
    /// km/h x 100.
    SetTargetSpeed(u16),
    /// percent x 10, signed.
    SetTargetInclination(i16),
    StartOrResume,
    /// 1 = stop, 2 = pause.
    StopOrPause(u8),
}

impl ControlCommand {
    /// The opcode this command arrived as — the second byte of its response
    /// indication. Derived from the variant rather than remembered by the
    /// caller, so a response cannot answer the wrong request.
    pub const fn opcode(self) -> u8 {
        match self {
            ControlCommand::RequestControl => OP_REQUEST_CONTROL,
            ControlCommand::SetTargetSpeed(_) => OP_SET_TARGET_SPEED,
            ControlCommand::SetTargetInclination(_) => OP_SET_TARGET_INCLINATION,
            ControlCommand::StartOrResume => OP_START_OR_RESUME,
            ControlCommand::StopOrPause(_) => OP_STOP_OR_PAUSE,
        }
    }
}

/// Parse an FTMS Control Point write (0x2AD9).
///
/// UNTRUSTED INPUT. Returns `None` for an unknown opcode or a payload too
/// short for its opcode; it cannot panic on any byte string of any length —
/// `parse_never_panics_on_any_one_byte_input` and
/// `parse_never_panics_on_any_two_byte_input` walk all 256 and all 65 536.
/// Trailing bytes beyond what an opcode needs are IGNORED, matching the
/// daemon (some clients pad).
pub fn parse_control_point(bytes: &[u8]) -> Option<ControlCommand> {
    let opcode = *bytes.first()?;
    match opcode {
        OP_REQUEST_CONTROL => Some(ControlCommand::RequestControl),
        OP_SET_TARGET_SPEED => {
            if bytes.len() < 3 {
                return None;
            }
            Some(ControlCommand::SetTargetSpeed(u16::from_le_bytes([
                bytes[1], bytes[2],
            ])))
        }
        OP_SET_TARGET_INCLINATION => {
            if bytes.len() < 3 {
                return None;
            }
            Some(ControlCommand::SetTargetInclination(i16::from_le_bytes([
                bytes[1], bytes[2],
            ])))
        }
        OP_START_OR_RESUME => Some(ControlCommand::StartOrResume),
        OP_STOP_OR_PAUSE => {
            if bytes.len() < 2 {
                return None;
            }
            Some(ControlCommand::StopOrPause(bytes[1]))
        }
        _ => None,
    }
}

/// A Control Point response indication: `[0x80, request_opcode, result]`.
///
/// Fixed `[u8; 3]`, where the daemon returns a `Vec`. Same three bytes.
pub const fn encode_control_response(request_opcode: u8, result: u8) -> [u8; 3] {
    [RESPONSE_CODE, request_opcode, result]
}

// --- Treadmill Data (0x2ACD) ---------------------------------------------

/// Length of the Treadmill Data characteristic value, for the flags below.
pub const TREADMILL_DATA_LEN: usize = 13;

/// Treadmill Data flags, fixed at `0x040C`:
///   bit 0 = 0: Instantaneous Speed present
///   bit 2 = 1: Total Distance present
///   bit 3 = 1: Inclination and Ramp Angle present
///   bit 10 = 1: Elapsed Time present
pub const TREADMILL_DATA_FLAGS: u16 = 0x040C;

/// Encode Treadmill Data (0x2ACD): 13 bytes, always.
///
/// Layout: flags(2) speed(2) distance(3) inclination(2) ramp_angle(2)
/// elapsed(2).
///
/// TWO LOSSY FIELDS, both inherited deliberately so the bytes match the
/// daemon a phone may already have been paired with:
///  * Total Distance is a **uint24** — the top byte of `distance_meters` is
///    DROPPED. Wraps at 16 777 216 m (~10 400 miles), which one run cannot
///    reach; a lifetime odometer fed in here would.
///  * Ramp Angle Setting is always 0. The treadmill has no ramp-angle sensor,
///    and the field is mandatory once bit 3 is set.
pub fn encode_treadmill_data(
    speed_kmh_hundredths: u16,
    incline_tenths: i16,
    distance_meters: u32,
    elapsed_secs: u16,
) -> [u8; TREADMILL_DATA_LEN] {
    let mut buf = [0u8; TREADMILL_DATA_LEN];
    buf[0..2].copy_from_slice(&TREADMILL_DATA_FLAGS.to_le_bytes());
    buf[2..4].copy_from_slice(&speed_kmh_hundredths.to_le_bytes());
    let dist = distance_meters.to_le_bytes();
    buf[4] = dist[0];
    buf[5] = dist[1];
    buf[6] = dist[2];
    buf[7..9].copy_from_slice(&incline_tenths.to_le_bytes());
    buf[9..11].copy_from_slice(&0i16.to_le_bytes());
    buf[11..13].copy_from_slice(&elapsed_secs.to_le_bytes());
    buf
}

/// The speed substituted into Treadmill Data while the belt is stopped:
/// 0.50 km/h.
///
/// NOT cosmetic and NOT a lie about the belt. Kinomap on iOS (and others) read
/// speed passively and treat a machine notifying 0.00 as absent, so an
/// incline-only workout never starts. The belt is not moving and nothing here
/// can make it move — this is a liveness marker on a notify channel.
pub const IDLE_MIN_SPEED_KMH_HUNDREDTHS: u16 = 50;

/// What the device currently is, in BELT units, for the notify path.
///
/// Newtyped speed/incline rather than the daemon's bare `u16`/`i32`: this
/// struct is filled from the safety controller's state, and the ONE thing that
/// could go wrong silently is filling `incline` from a percent instead of a
/// half-percent.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct TreadmillSnapshot {
    pub speed: SpeedTenths,
    pub incline: InclineHalfPct,
    pub distance_meters: u32,
    /// Workout elapsed time reported by the application tier, 0 if no workout.
    pub elapsed_secs: u16,
}

/// Encode Treadmill Data with the idle alive-signal substitutions, port of
/// `ftms_service::encode_ftms_data_with_alive`.
///
///  * speed 0 -> [`IDLE_MIN_SPEED_KMH_HUNDREDTHS`]
///  * workout elapsed 0 -> `session_elapsed_secs` (the connection's uptime)
pub fn encode_treadmill_data_with_alive(
    snap: &TreadmillSnapshot,
    session_elapsed_secs: u16,
) -> [u8; TREADMILL_DATA_LEN] {
    let mut speed_kmh = speed_tenths_to_kmh_hundredths(snap.speed);
    if speed_kmh == 0 {
        speed_kmh = IDLE_MIN_SPEED_KMH_HUNDREDTHS;
    }
    let elapsed = if snap.elapsed_secs > 0 {
        snap.elapsed_secs
    } else {
        session_elapsed_secs
    };
    encode_treadmill_data(
        speed_kmh,
        incline_half_pct_to_ftms_tenths(snap.incline),
        snap.distance_meters,
        elapsed,
    )
}

// --- Static characteristics ----------------------------------------------

/// Fitness Machine Feature (0x2ACC): two u32 LE.
///
/// Machine Features `0x0000_100C` = Total Distance (bit 2), Inclination
/// (bit 3), Elapsed Time (bit 12) — exactly the fields Treadmill Data's flags
/// claim, which is the invariant `feature_bits_agree_with_treadmill_data_flags`
/// checks.
///
/// Target Setting Features `0x0000_0003` = Speed Target (bit 0), Inclination
/// Target (bit 1) — exactly the two Set opcodes [`parse_control_point`]
/// accepts.
pub const FEATURE_MACHINE: u32 = 0x0000_100C;
pub const FEATURE_TARGET_SETTING: u32 = 0x0000_0003;

pub const fn encode_feature() -> [u8; 8] {
    let m = FEATURE_MACHINE.to_le_bytes();
    let t = FEATURE_TARGET_SETTING.to_le_bytes();
    [m[0], m[1], m[2], m[3], t[0], t[1], t[2], t[3]]
}

/// Supported Speed Range (0x2AD4): min, max, step as 3x u16 LE, km/h x 100.
///
/// 80 = 0.5 mph, 1931 = 12.0 mph, 16 = 0.1 mph. NOTE the advertised max is
/// 1931 while `mph_tenths_to_kmh_hundredths(120)` reports 1930 — the daemon's
/// truncating divide. Kept, because changing it would move every speed the
/// phone sees by up to 0.01 km/h relative to a device the user may already
/// have ridden. Pinned by `speed_range_max_is_one_above_the_encoded_max`.
pub const SPEED_RANGE_MIN_KMH_HUNDREDTHS: u16 = 80;
pub const SPEED_RANGE_MAX_KMH_HUNDREDTHS: u16 = 1931;
pub const SPEED_RANGE_STEP_KMH_HUNDREDTHS: u16 = 16;

pub const fn encode_speed_range() -> [u8; 6] {
    let mn = SPEED_RANGE_MIN_KMH_HUNDREDTHS.to_le_bytes();
    let mx = SPEED_RANGE_MAX_KMH_HUNDREDTHS.to_le_bytes();
    let st = SPEED_RANGE_STEP_KMH_HUNDREDTHS.to_le_bytes();
    [mn[0], mn[1], mx[0], mx[1], st[0], st[1]]
}

/// Supported Inclination Range (0x2AD5): min, max, step as 3x i16 LE,
/// percent x 10. 0.0% .. 15.0% in 0.5% steps — the APPLICATION incline clamp
/// (`InclineHalfPct::APP_MAX` = 30 half-percent = 15.0%), not the hardware
/// one. Asserted against that constant so the two cannot drift.
pub const INCLINE_RANGE_MIN_TENTHS: i16 = 0;
pub const INCLINE_RANGE_MAX_TENTHS: i16 = 150;
pub const INCLINE_RANGE_STEP_TENTHS: i16 = 5;

pub const fn encode_incline_range() -> [u8; 6] {
    let mn = INCLINE_RANGE_MIN_TENTHS.to_le_bytes();
    let mx = INCLINE_RANGE_MAX_TENTHS.to_le_bytes();
    let st = INCLINE_RANGE_STEP_TENTHS.to_le_bytes();
    [mn[0], mn[1], mx[0], mx[1], st[0], st[1]]
}

// --- Notifications --------------------------------------------------------

/// A notification payload of at most [`Notification::MAX`] bytes.
///
/// Replaces the daemon's `Option<Vec<u8>>`. Fitness Machine Status is 1..=3
/// bytes and Training Status is exactly 2; a fixed buffer with a length is the
/// whole of what is needed, and it keeps the notify path allocation-free.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Notification {
    buf: [u8; Self::MAX],
    len: u8,
}

impl Notification {
    pub const MAX: usize = 3;

    const fn new(buf: [u8; Self::MAX], len: u8) -> Self {
        Notification { buf, len }
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.buf[..self.len as usize]
    }

    pub const fn len(&self) -> usize {
        self.len as usize
    }

    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }
}

/// Training Status (0x2AD3) for a start/stop, port of
/// `ftms_service::encode_training_status`: `[flags=0x00, status]` where status
/// is 0x0D Manual Mode (Quick Start) or 0x01 Idle. `None` for commands that do
/// not change training state.
pub fn encode_training_status(cmd: ControlCommand) -> Option<[u8; 2]> {
    match cmd {
        ControlCommand::StartOrResume => Some([0x00, 0x0D]),
        ControlCommand::StopOrPause(_) => Some([0x00, 0x01]),
        _ => None,
    }
}

/// Fitness Machine Status (0x2ADA), port of
/// `ftms_service::encode_status_notification`. Opcodes per FTMS Table 4.16:
/// 0x02 stopped/paused by user (param 1=stop, 2=pause), 0x04 started/resumed
/// by user, 0x05 target speed changed (u16 LE km/h x 100), 0x06 target incline
/// changed (i16 LE percent x 10).
///
/// The parameter echoes what the PEER ASKED FOR, not what the belt did — that
/// is the daemon's behaviour and it is what a client's own UI mirrors. What
/// the belt actually did shows up in the next Treadmill Data notification.
pub fn encode_status_notification(cmd: ControlCommand) -> Option<Notification> {
    match cmd {
        ControlCommand::SetTargetSpeed(kmh) => {
            let b = kmh.to_le_bytes();
            Some(Notification::new([0x05, b[0], b[1]], 3))
        }
        ControlCommand::SetTargetInclination(tenths) => {
            let b = tenths.to_le_bytes();
            Some(Notification::new([0x06, b[0], b[1]], 3))
        }
        ControlCommand::StartOrResume => Some(Notification::new([0x04, 0, 0], 1)),
        ControlCommand::StopOrPause(param) => Some(Notification::new([0x02, param, 0], 2)),
        ControlCommand::RequestControl => None,
    }
}

// --- Unit conversion ------------------------------------------------------

/// mph x 10 -> km/h x 100. `mph_tenths * 16.0934`, computed as
/// `mph_tenths * 1609 / 100`.
///
/// LOSSY, AND THE LOSS IS PINNED. 1.0 mph encodes as 160, not 161 (160.9
/// truncated); 12.0 mph encodes as 1930, not the 1931 the Speed Range
/// characteristic advertises. That is what the Pi daemon has shipped, so it is
/// what a phone paired with the Pi has seen, and matching it is the point of
/// this crate. `conversion_matches_daemon_vectors` and
/// `roundtrip_is_within_one_tenth_mph` hold it in place.
///
/// OVERFLOW: the intermediate is u32 (65535 * 1609 = 105 446 415, fits), and
/// the result is TRUNCATED into u16 by the cast — inherited verbatim, and
/// unreachable through [`speed_tenths_to_kmh_hundredths`], which saturates
/// first. Exercised at the boundary by `conversion_extremes_match_the_daemon`.
pub fn mph_tenths_to_kmh_hundredths(mph_tenths: u16) -> u16 {
    ((mph_tenths as u32) * 1609 / 100) as u16
}

/// km/h x 100 -> mph x 10. `kmh_hundredths / 16.0934`, computed as
/// `kmh_hundredths * 100 / 1609`. Lossy the same way; 161 -> 10.
pub fn kmh_hundredths_to_mph_tenths(kmh_hundredths: u16) -> u16 {
    ((kmh_hundredths as u32) * 100 / 1609) as u16
}

/// [`SpeedTenths`] -> km/h x 100 for the notify path.
///
/// Saturates instead of wrapping: a negative or absurd speed must not become a
/// SMALL POSITIVE km/h on the wire, which is the only failure here a client
/// could act on. Unreachable in practice (the controller clamps to 0..=120)
/// and total anyway, because unreachable is not the same as cannot-happen
/// under `panic = "abort"`.
pub fn speed_tenths_to_kmh_hundredths(speed: SpeedTenths) -> u16 {
    let t = speed.get();
    if t <= 0 {
        return 0;
    }
    let clamped = if t > u16::MAX as i32 {
        u16::MAX
    } else {
        t as u16
    };
    mph_tenths_to_kmh_hundredths(clamped)
}

/// [`InclineHalfPct`] -> percent x 10. `half_pct * 5` (0.5% = 5 tenths),
/// saturating into i16 for the same reason as above.
pub fn incline_half_pct_to_ftms_tenths(incline: InclineHalfPct) -> i16 {
    let tenths = incline.get().saturating_mul(5);
    if tenths > i16::MAX as i32 {
        i16::MAX
    } else if tenths < i16::MIN as i32 {
        i16::MIN
    } else {
        tenths as i16
    }
}

/// percent x 10 -> [`InclineHalfPct`], rounded to the nearest half-percent.
///
/// The daemon does this in floating point — `(pct * 2.0).round() / 2.0` after
/// a clamp — which is `round(tenths / 5)`. Done here in integers, exactly:
/// `tenths / 5` is never a half-integer (it would need `tenths = 5k + 2.5`),
/// so there is no tie to break and no rounding mode to pick.
///
/// NO CLAMP, DELIBERATELY. See [`CpEffect`].
pub fn ftms_tenths_to_incline_half_pct(tenths: i16) -> InclineHalfPct {
    let t = tenths as i32;
    // round-half-away-from-zero on t/5, in integers: (2t +/- 5) / 10.
    let half = if t >= 0 {
        (2 * t + 5) / 10
    } else {
        (2 * t - 5) / 10
    };
    InclineHalfPct::new(half)
}

/// km/h x 100 -> [`SpeedTenths`]. No clamp; see [`CpEffect`].
pub fn kmh_hundredths_to_speed_tenths(kmh_hundredths: u16) -> SpeedTenths {
    SpeedTenths::new(kmh_hundredths_to_mph_tenths(kmh_hundredths) as i32)
}

// --- The belt edge --------------------------------------------------------

/// What a Control Point write ASKS FOR, expressed in belt units.
///
/// This is the whole of this crate's contact with the belt, and it is a
/// DESCRIPTION, not an action: nothing in `ble_core` can command motion.
/// The BLE surface feeds a `CpEffect` to `esp32tap::control::command` — THE
/// ONE PATH TO THE BELT — which applies the lease, the controller's clamps and
/// the auto-emulate policy that the HTTP surface already goes through. There
/// is deliberately no second path and no second set of clamps.
///
/// ## Why this does not clamp, when the Pi daemon does
///
/// `ftms_service::handle_control_command` clamps to 0..=12.0 mph and
/// 0..=15.0% before sending, because on the Pi the FTMS daemon, the web server
/// and the AI tier each reach `treadmill_io` through their own socket write —
/// there is no shared choke point to put the clamp behind, so every caller
/// carries a copy. Here `control::command` IS that choke point. A clamp
/// repeated in front of it would be a second opinion about what is safe, and a
/// second opinion that agrees today is exactly what `control.rs`'s header
/// warns about.
///
/// The observable consequence is BETTER, not merely equivalent: a peer that
/// writes 40 mph gets `RESULT_INVALID_PARAM` and the belt does not move,
/// where the Pi silently substituted 12 mph and moved the belt at a speed
/// nobody asked for. [`result_for_reject`] does that mapping.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CpEffect {
    /// Acknowledge and do nothing. FTMS requires a client to take control
    /// before setting targets; the device has no per-peer permission model
    /// (the LEASE in `control.rs` is the real arbitration), so this always
    /// succeeds and never touches the belt.
    AckOnly,
    /// Command this speed, leaving incline as it is.
    SetSpeed(SpeedTenths),
    /// Command this incline, leaving speed as it is.
    SetIncline(InclineHalfPct),
    /// Resume the retained target. The caller supplies what that is; entering
    /// emulate is NOT requested explicitly, because `control::command` does it
    /// itself on any accepted motion (the auto-emulate policy) and asking for
    /// it separately would be the second path this type exists to prevent.
    Start,
    /// Zero the belt. Speed AND incline, matching `treadmill::send_stop`.
    /// `param` is 1 = stop, 2 = pause; the belt treats both identically —
    /// there is no paused state below the application tier, and a "pause" that
    /// left the belt running would be the dangerous reading of the word.
    Stop { param: u8 },
}

/// Translate a parsed Control Point write into its belt-unit effect.
///
/// Total: every `ControlCommand` has one, so a peer cannot produce a write
/// that parses and then falls through unhandled.
pub fn effect_of(cmd: ControlCommand) -> CpEffect {
    match cmd {
        ControlCommand::RequestControl => CpEffect::AckOnly,
        ControlCommand::SetTargetSpeed(kmh) => {
            CpEffect::SetSpeed(kmh_hundredths_to_speed_tenths(kmh))
        }
        ControlCommand::SetTargetInclination(tenths) => {
            CpEffect::SetIncline(ftms_tenths_to_incline_half_pct(tenths))
        }
        ControlCommand::StartOrResume => CpEffect::Start,
        ControlCommand::StopOrPause(param) => CpEffect::Stop { param },
    }
}

/// What the belt is doing now, and what Start/Resume should resume TO.
///
/// Named fields rather than a `(SpeedTenths, SpeedTenths)` pair on purpose:
/// `speed` and `resume_speed` are the same type and differ only in meaning, so
/// a positional API would let them be swapped silently — and the swap is
/// invisible until the one case where they differ (a peer that stops and then
/// resumes), which is the worst time to find it.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct BeltNow {
    /// Currently commanded speed.
    pub speed: SpeedTenths,
    /// Currently commanded incline.
    pub incline: InclineHalfPct,
    /// The target a Start/Resume returns to. The caller owns this (it is the
    /// last non-zero target it accepted); `ble_core` holds no state.
    pub resume_speed: SpeedTenths,
}

/// The (speed, incline) pair to hand `control::command` for an effect.
///
/// `control::command` takes BOTH axes at once — one lease, one motion — so a
/// speed-only write has to carry the current incline through unchanged, and
/// vice versa. Getting that backwards would zero the other axis on every
/// write, which is precisely the kind of silent cross-talk a host test is for.
///
/// Returns `None` for [`CpEffect::AckOnly`]: no motion is to be commanded at
/// all, which is NOT the same as commanding the current motion again — that
/// would take the lease away from a running program.
pub fn motion_for(effect: CpEffect, now: BeltNow) -> Option<(SpeedTenths, InclineHalfPct)> {
    match effect {
        CpEffect::AckOnly => None,
        CpEffect::SetSpeed(s) => Some((s, now.incline)),
        CpEffect::SetIncline(i) => Some((now.speed, i)),
        CpEffect::Start => Some((now.resume_speed, now.incline)),
        CpEffect::Stop { .. } => Some((SpeedTenths::ZERO, InclineHalfPct::ZERO)),
    }
}

/// Why a Control Point write did not reach the belt, mapped to the FTMS result
/// code the indication must carry.
///
/// Mirrors `esp32tap::control::Reject` without depending on it (that type
/// lives in the firmware crate, which this one must not pull in):
///  * `NotOwner` -> `RESULT_FAILED` — a program or the console holds the belt;
///    the request was well-formed and the machine simply would not do it.
///  * `Refused` -> `RESULT_INVALID_PARAM` — the controller rejected the
///    motion, which for a well-formed write means it was out of clamp.
///  * anything else -> `RESULT_FAILED`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CpReject {
    NotOwner,
    Refused,
    Other,
}

pub const fn result_for_reject(reject: CpReject) -> u8 {
    match reject {
        CpReject::NotOwner => RESULT_FAILED,
        CpReject::Refused => RESULT_INVALID_PARAM,
        CpReject::Other => RESULT_FAILED,
    }
}
