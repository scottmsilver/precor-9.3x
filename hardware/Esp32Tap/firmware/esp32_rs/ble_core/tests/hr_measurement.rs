//! Heart Rate Measurement vectors, ported BYTE FOR BYTE from
//! `rust/hrm/src/scanner.rs` — the daemon that has been reading real chest
//! straps on the Pi.
//!
//! Same rule as the FTMS file: if a vector here and the daemon disagree, the
//! daemon is right. Nothing here proves a radio works — see precor-9_3x-l0h.

use ble_core::hrm::*;

#[test]
fn uuids_match_the_documented_gatt_surface() {
    // CLAUDE.md, "HRM Bluetooth — hrm-daemon".
    assert_eq!(SERVICE_HEART_RATE, 0x180D);
    assert_eq!(CHAR_HR_MEASUREMENT, 0x2A37);
}

#[test]
fn parse_uint8_format() {
    // flags=0x00 (bit 0 clear -> uint8), HR=72
    assert_eq!(parse_hr_measurement(&[0x00, 72]), Some(72));
}

#[test]
fn parse_uint16_format() {
    // flags=0x01 (bit 0 set -> uint16 LE), HR=300 = 0x012C
    assert_eq!(parse_hr_measurement(&[0x01, 0x2C, 0x01]), Some(300));
}

#[test]
fn parse_uint8_ignoring_other_flag_bits_and_trailing_fields() {
    // flags=0x06: bit 0 clear (uint8) with sensor-contact bits set. The
    // trailing bytes are whatever optional fields the strap appended; they are
    // deliberately not parsed.
    assert_eq!(parse_hr_measurement(&[0x06, 155, 0x00, 0x00]), Some(155));
}

#[test]
fn parse_uint16_ignoring_the_rr_interval_flag() {
    // flags=0x11: bit 0 set (uint16) + bit 4 (RR-Interval present).
    // HR=256 = 0x0100 LE, then RR data we ignore.
    assert_eq!(
        parse_hr_measurement(&[0x11, 0x00, 0x01, 0x00, 0x00]),
        Some(256)
    );
}

#[test]
fn parse_rejects_empty_and_truncated() {
    assert_eq!(parse_hr_measurement(&[]), None);
    assert_eq!(parse_hr_measurement(&[0x00]), None, "uint8 needs 2 bytes");
    assert_eq!(
        parse_hr_measurement(&[0x01, 0x48]),
        None,
        "uint16 needs 3 bytes"
    );
}

#[test]
fn parse_boundary_values() {
    assert_eq!(parse_hr_measurement(&[0x00, 0]), Some(0));
    assert_eq!(parse_hr_measurement(&[0x00, 255]), Some(255));
    assert_eq!(parse_hr_measurement(&[0x01, 0xFF, 0xFF]), Some(65535));
}

#[test]
fn parse_typical_workout_values() {
    for bpm in [60u8, 90, 120, 150, 180, 200] {
        assert_eq!(parse_hr_measurement(&[0x00, bpm]), Some(bpm as u16));
    }
}

#[test]
fn parse_never_panics_over_the_short_domain() {
    // A strap is a third-party device and a notification is whatever it sends.
    // Every input of length 0..=3 — the lengths at which the two length guards
    // decide — must return, not panic.
    let _ = parse_hr_measurement(&[]);
    for b0 in 0u8..=255 {
        let _ = parse_hr_measurement(&[b0]);
        for b1 in [0u8, 1, 0x7F, 0x80, 0xFF] {
            let _ = parse_hr_measurement(&[b0, b1]);
            for b2 in [0u8, 0x7F, 0xFF] {
                let _ = parse_hr_measurement(&[b0, b1, b2]);
            }
        }
    }
}

#[test]
fn only_bit_zero_selects_the_format() {
    // Every one of the 128 flag bytes with bit 0 clear must read a uint8, and
    // every one with it set must read a uint16. This is the whole of the flag
    // handling, stated as a property rather than four examples.
    for flags in 0u8..=255 {
        let payload = [flags, 0x2C, 0x01];
        let got = parse_hr_measurement(&payload).expect("3 bytes is enough for either format");
        if flags & 0x01 == 0 {
            assert_eq!(got, 0x2C, "flags 0x{flags:02X} should read a uint8");
        } else {
            assert_eq!(got, 0x012C, "flags 0x{flags:02X} should read a uint16 LE");
        }
    }
}

// --- The sentinel the whole stack already agrees on -----------------------

#[test]
fn no_reading_is_zero() {
    // `scanner.rs::mark_disconnected` zeroes the bpm and every consumer gates
    // on `> 0` (MetricsRow.kt, RidgelineHud.kt, SettingsSheet.kt). A different
    // "unknown" value would render as a heart rate at all three.
    assert_eq!(BPM_NONE, 0);
    assert_eq!(
        HrReading::NONE,
        HrReading {
            bpm: 0,
            connected: false
        }
    );
    assert!(!HrReading::NONE.is_displayable());
}

#[test]
fn a_good_notification_connects_and_updates() {
    let r = HrReading::NONE.updated(&[0x00, 142]);
    assert_eq!(r.bpm, 142);
    assert!(r.connected);
    assert!(r.is_displayable());
}

#[test]
fn a_malformed_notification_holds_the_previous_reading() {
    // The link is still up; one frame was short. Blanking the number the user
    // is watching because of a single bad notification would flicker it.
    let good = HrReading::NONE.updated(&[0x00, 142]);
    let after = good.updated(&[0x01, 0x48]);
    assert_eq!(after.bpm, 142);
    assert!(after.connected);
}

#[test]
fn a_disconnect_clears_the_reading() {
    // A stale number must not sit on screen looking live after the strap walks
    // away — `mark_disconnected`'s behaviour, kept.
    let good = HrReading::NONE.updated(&[0x00, 142]);
    assert_eq!(good.disconnected(), HrReading::NONE);
    assert!(!good.disconnected().is_displayable());
}

#[test]
fn a_zero_bpm_reading_is_not_displayable_even_while_connected() {
    // Some straps send 0 while searching for contact. `> 0` is the gate the
    // Kotlin call sites apply; it is applied here once instead of three times.
    let r = HrReading::NONE.updated(&[0x00, 0]);
    assert!(r.connected);
    assert_eq!(r.bpm, 0);
    assert!(!r.is_displayable());
}
