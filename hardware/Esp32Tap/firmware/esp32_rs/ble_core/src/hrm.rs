//! Heart Rate Service (0x180D) client-side parsing — a `no_std`,
//! allocation-free port of `parse_hr_measurement` from
//! `rust/hrm/src/scanner.rs`, plus the sentinel the whole stack above it
//! already agrees on.
//!
//! This is the CLIENT half of BLE: the device connects OUT to a chest strap
//! and subscribes to its notifications. Nothing here scans, connects or
//! bonds — that is radio work and it lives in the firmware crate.

/// Heart Rate Service.
pub const SERVICE_HEART_RATE: u16 = 0x180D;
/// Heart Rate Measurement (notify).
pub const CHAR_HR_MEASUREMENT: u16 = 0x2A37;

/// Parse a Heart Rate Measurement characteristic value.
///
/// Byte 0 is flags; **only bit 0 is inspected**, selecting the HR value's
/// width:
///   * 0 -> uint8 in byte 1
///   * 1 -> uint16 LE in bytes 1..3
///
/// Returns `None` if the buffer is too short for the format its own flags
/// declare. UNTRUSTED INPUT: a strap is a third-party device and a notification
/// is whatever it sends, so this must be total over every byte string —
/// `parse_never_panics_over_the_short_domain` walks every input of length 0..=3.
///
/// ## What is deliberately NOT parsed
///
/// Sensor Contact Status (bits 1-2), Energy Expended Present (bit 3) and
/// RR-Interval Present (bit 4), along with the optional fields those bits
/// introduce. The Pi daemon ignores them too, and the whole consuming stack —
/// `hrm_client.py`, `/api/hrm`, the `hr` WebSocket frame, and all three Kotlin
/// call sites — carries exactly `heart_rate`, `connected` and `device`. Adding
/// fields nothing displays would be state to keep correct for no observable
/// gain.
///
/// Sensor contact is the one with a real argument for it, and the argument
/// loses: a strap reporting "contact not detected" still reports a BPM, and
/// treating that as no-reading would blank the user's HR every time the strap
/// dried out mid-run. The reading is already advisory — nothing in the safety
/// path or the belt path consumes heart rate.
pub fn parse_hr_measurement(data: &[u8]) -> Option<u16> {
    let flags = *data.first()?;
    let hr_format_16bit = (flags & 0x01) != 0;
    if hr_format_16bit {
        if data.len() < 3 {
            return None;
        }
        Some(u16::from_le_bytes([data[1], data[2]]))
    } else {
        if data.len() < 2 {
            return None;
        }
        Some(data[1] as u16)
    }
}

/// The BPM value that means "no reading".
///
/// Zero, and that is not a choice made here — it is what the stack already
/// does. `scanner.rs::mark_disconnected` zeroes `heart_rate` on disconnect,
/// and every consumer gates on `> 0`: `MetricsRow.kt` and `RidgelineHud.kt`
/// hide the readout, `SettingsSheet.kt` shows `---`. Stating it as a named
/// constant is what stops a future "unknown" from being invented as -1 or
/// 255, either of which those call sites would render as a heart rate.
pub const BPM_NONE: u16 = 0;

/// One heart-rate reading and the connection state that produced it.
///
/// Fixed-size and `Copy` — no device name, no address, no scan list. Those
/// belong to the firmware's connection manager, where the strings can be
/// bounded `FixedStr`s next to the radio state that owns them; duplicating
/// them here would be two copies of one truth. This is the payload the
/// application tier publishes.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct HrReading {
    pub bpm: u16,
    pub connected: bool,
}

impl HrReading {
    /// Not connected, no reading. The state at boot and after a disconnect.
    pub const NONE: HrReading = HrReading {
        bpm: BPM_NONE,
        connected: false,
    };

    /// Fold a notification payload into a reading. A payload that does not
    /// parse leaves the PREVIOUS bpm in place rather than zeroing it: the link
    /// is still up, one frame was malformed, and blanking the display on a
    /// single bad notification would flicker the number the user is watching.
    pub fn updated(self, payload: &[u8]) -> HrReading {
        match parse_hr_measurement(payload) {
            Some(bpm) => HrReading {
                bpm,
                connected: true,
            },
            None => HrReading {
                bpm: self.bpm,
                connected: self.connected,
            },
        }
    }

    /// The link dropped. Zeroes the bpm, matching
    /// `scanner.rs::mark_disconnected`, so a stale number cannot sit on screen
    /// looking live after the strap walks away.
    pub const fn disconnected(self) -> HrReading {
        HrReading::NONE
    }

    /// Whether a consumer should display this. The `> 0` gate the Kotlin call
    /// sites apply, written once.
    pub const fn is_displayable(self) -> bool {
        self.connected && self.bpm > BPM_NONE
    }
}
