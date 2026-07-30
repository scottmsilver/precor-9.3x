//! FTMS wire vectors, ported BYTE FOR BYTE from `rust/ftms/src/protocol.rs`
//! and the encoder tests in `rust/ftms/src/ftms_service.rs` — the daemon that
//! is in production on the Pi against real phones.
//!
//! THE RULE FOR THIS FILE: if a vector here and the daemon disagree, the
//! DAEMON is right and this crate is wrong. A phone that has been paired with
//! the Pi must see the same bytes from the ESP32, including the two places
//! where the daemon is lossy (the truncating mph->km/h divide, the uint24
//! distance). Those are pinned deliberately, with the reasoning at each site.
//!
//! Everything below runs on the host in well under a second. NOTHING here
//! proves a radio works: QEMU has no BLE radio and no board exists yet. What
//! is still unproven is enumerated in bead precor-9_3x-l0h.

use ble_core::ftms::*;
use safety_core::units::{InclineHalfPct, SpeedTenths};

// --- Treadmill Data (0x2ACD) ---------------------------------------------

#[test]
fn treadmill_data_all_zeros() {
    let data = encode_treadmill_data(0, 0, 0, 0);
    assert_eq!(data.len(), 13);
    // Flags 0x040C LE
    assert_eq!(data[0], 0x0C);
    assert_eq!(data[1], 0x04);
    // speed, distance(3), inclination, ramp, elapsed — all zero
    assert_eq!(&data[2..13], &[0u8; 11]);
}

#[test]
fn treadmill_data_running() {
    // speed=500 (5.00 km/h), incline=30 (3.0%), distance=1234m, elapsed=300s
    let data = encode_treadmill_data(500, 30, 1234, 300);
    assert_eq!(data.len(), 13);
    assert_eq!(u16::from_le_bytes([data[0], data[1]]), 0x040C);
    assert_eq!(u16::from_le_bytes([data[2], data[3]]), 500);
    // Distance 1234 = 0x0004D2, low 3 bytes LE
    assert_eq!(data[4], 0xD2);
    assert_eq!(data[5], 0x04);
    assert_eq!(data[6], 0x00);
    assert_eq!(i16::from_le_bytes([data[7], data[8]]), 30);
    assert_eq!(i16::from_le_bytes([data[9], data[10]]), 0, "ramp angle");
    assert_eq!(u16::from_le_bytes([data[11], data[12]]), 300);
}

#[test]
fn treadmill_data_is_always_thirteen_bytes_at_the_extremes() {
    let data = encode_treadmill_data(u16::MAX, i16::MAX, u32::MAX, u16::MAX);
    assert_eq!(data.len(), 13);
    assert_eq!(u16::from_le_bytes([data[2], data[3]]), u16::MAX);
    assert_eq!(i16::from_le_bytes([data[7], data[8]]), i16::MAX);
    assert_eq!(u16::from_le_bytes([data[11], data[12]]), u16::MAX);
    // Distance is uint24: the top byte of the u32 is DROPPED.
    let dist = u32::from_le_bytes([data[4], data[5], data[6], 0]);
    assert_eq!(dist, 0x00FF_FFFF, "uint24 truncation, inherited on purpose");
}

#[test]
fn treadmill_data_carries_a_negative_incline() {
    // Not reachable on this treadmill (the controller clamps at 0), but the
    // field is signed and the daemon encodes it signed.
    let data = encode_treadmill_data(0, -150, 0, 0);
    assert_eq!(i16::from_le_bytes([data[7], data[8]]), -150);
}

// --- Static characteristics ----------------------------------------------

#[test]
fn feature_characteristic() {
    let feat = encode_feature();
    assert_eq!(feat.len(), 8);
    assert_eq!(
        u32::from_le_bytes([feat[0], feat[1], feat[2], feat[3]]),
        0x0000_100C
    );
    assert_eq!(
        u32::from_le_bytes([feat[4], feat[5], feat[6], feat[7]]),
        0x0000_0003
    );
}

#[test]
fn feature_bits_agree_with_treadmill_data_flags() {
    // A machine that advertises Total Distance / Inclination / Elapsed Time
    // must actually set those bits in every Treadmill Data notification, or a
    // client parses the payload against the wrong layout. Bit 2/3/12 of the
    // feature word correspond to bit 2/3/10 of the data flags.
    assert_ne!(FEATURE_MACHINE & (1 << 2), 0, "Total Distance supported");
    assert_ne!(FEATURE_MACHINE & (1 << 3), 0, "Inclination supported");
    assert_ne!(FEATURE_MACHINE & (1 << 12), 0, "Elapsed Time supported");
    assert_ne!(TREADMILL_DATA_FLAGS & (1 << 2), 0, "Total Distance present");
    assert_ne!(TREADMILL_DATA_FLAGS & (1 << 3), 0, "Inclination present");
    assert_ne!(TREADMILL_DATA_FLAGS & (1 << 10), 0, "Elapsed Time present");
}

#[test]
fn target_setting_bits_match_the_opcodes_actually_accepted() {
    // Bit 0 = Speed Target, bit 1 = Inclination Target. Advertising a target
    // the Control Point then rejects is how a client ends up retrying forever.
    assert_ne!(FEATURE_TARGET_SETTING & (1 << 0), 0);
    assert!(matches!(
        parse_control_point(&[OP_SET_TARGET_SPEED, 0, 0]),
        Some(ControlCommand::SetTargetSpeed(_))
    ));
    assert_ne!(FEATURE_TARGET_SETTING & (1 << 1), 0);
    assert!(matches!(
        parse_control_point(&[OP_SET_TARGET_INCLINATION, 0, 0]),
        Some(ControlCommand::SetTargetInclination(_))
    ));
}

#[test]
fn speed_range_characteristic() {
    let r = encode_speed_range();
    assert_eq!(u16::from_le_bytes([r[0], r[1]]), 80);
    assert_eq!(u16::from_le_bytes([r[2], r[3]]), 1931);
    assert_eq!(u16::from_le_bytes([r[4], r[5]]), 16);
}

#[test]
fn speed_range_max_is_one_above_the_encoded_max() {
    // 12.0 mph advertises as 1931 but ENCODES as 1930 — the daemon's
    // truncating divide. Pinned so nobody "fixes" one side alone: a client
    // that clamps writes to the advertised max would then be told a speed it
    // was never allowed to request.
    assert_eq!(mph_tenths_to_kmh_hundredths(120), 1930);
    assert_eq!(SPEED_RANGE_MAX_KMH_HUNDREDTHS, 1931);
}

#[test]
fn incline_range_characteristic() {
    let r = encode_incline_range();
    assert_eq!(i16::from_le_bytes([r[0], r[1]]), 0);
    assert_eq!(i16::from_le_bytes([r[2], r[3]]), 150);
    assert_eq!(i16::from_le_bytes([r[4], r[5]]), 5);
}

#[test]
fn incline_range_max_is_the_application_clamp() {
    // 15.0% is `InclineHalfPct::APP_MAX` (30 half-percent), not the hardware
    // guard. Advertising the hardware range would invite a client to ask for
    // an incline the controller refuses.
    assert_eq!(
        incline_half_pct_to_ftms_tenths(InclineHalfPct::APP_MAX),
        INCLINE_RANGE_MAX_TENTHS
    );
}

#[test]
fn sig_uuid128_matches_the_pi_daemon_expansion() {
    // The daemon's `ble_uuid(short)` is
    //   Uuid::from_u128((short << 96) | 0x0000_0000_0000_1000_8000_00805f9b34fb)
    // whose big-endian bytes are what BlueZ puts on the wire.
    for short in [
        SERVICE_FTMS,
        CHAR_FEATURE,
        CHAR_TREADMILL_DATA,
        CHAR_TRAINING_STATUS,
        CHAR_SPEED_RANGE,
        CHAR_INCLINE_RANGE,
        CHAR_CONTROL_POINT,
        CHAR_MACHINE_STATUS,
    ] {
        let expected = (((short as u128) << 96) | 0x0000_0000_0000_1000_8000_0080_5f9b_34fb_u128)
            .to_be_bytes();
        assert_eq!(sig_uuid128(short), expected, "short 0x{short:04X}");
    }
    // Spot value, written out, so a change to BOTH sides still fails.
    assert_eq!(
        sig_uuid128(0x1826),
        [
            0x00, 0x00, 0x18, 0x26, 0x00, 0x00, 0x10, 0x00, 0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B,
            0x34, 0xFB
        ]
    );
}

#[test]
fn uuids_match_the_documented_gatt_surface() {
    // CLAUDE.md, "FTMS Bluetooth — ftms-daemon".
    assert_eq!(SERVICE_FTMS, 0x1826);
    assert_eq!(CHAR_FEATURE, 0x2ACC);
    assert_eq!(CHAR_TREADMILL_DATA, 0x2ACD);
    assert_eq!(CHAR_SPEED_RANGE, 0x2AD4);
    assert_eq!(CHAR_INCLINE_RANGE, 0x2AD5);
    assert_eq!(CHAR_CONTROL_POINT, 0x2AD9);
    assert_eq!(CHAR_MACHINE_STATUS, 0x2ADA);
}

// --- Control Point parsing (0x2AD9) --------------------------------------

#[test]
fn parse_request_control() {
    assert_eq!(
        parse_control_point(&[0x00]),
        Some(ControlCommand::RequestControl)
    );
}

#[test]
fn parse_set_speed() {
    // opcode 0x02, 500 = 0x01F4 LE
    assert_eq!(
        parse_control_point(&[0x02, 0xF4, 0x01]),
        Some(ControlCommand::SetTargetSpeed(500))
    );
}

#[test]
fn parse_set_incline_both_signs() {
    assert_eq!(
        parse_control_point(&[0x03, 0x1E, 0x00]),
        Some(ControlCommand::SetTargetInclination(30))
    );
    // -10 as i16 = 0xFFF6 LE
    assert_eq!(
        parse_control_point(&[0x03, 0xF6, 0xFF]),
        Some(ControlCommand::SetTargetInclination(-10))
    );
}

#[test]
fn parse_start() {
    assert_eq!(
        parse_control_point(&[0x07]),
        Some(ControlCommand::StartOrResume)
    );
}

#[test]
fn parse_stop_and_pause() {
    assert_eq!(
        parse_control_point(&[0x08, 0x01]),
        Some(ControlCommand::StopOrPause(1))
    );
    assert_eq!(
        parse_control_point(&[0x08, 0x02]),
        Some(ControlCommand::StopOrPause(2))
    );
}

#[test]
fn parse_rejects_unknown_and_empty() {
    assert_eq!(parse_control_point(&[0xFF]), None);
    assert_eq!(parse_control_point(&[]), None);
}

#[test]
fn parse_rejects_truncated_payloads() {
    assert_eq!(parse_control_point(&[0x02]), None);
    assert_eq!(parse_control_point(&[0x02, 0xF4]), None);
    assert_eq!(parse_control_point(&[0x03]), None);
    assert_eq!(parse_control_point(&[0x03, 0x1E]), None);
    assert_eq!(parse_control_point(&[0x08]), None);
}

#[test]
fn parse_rejects_every_unsupported_opcode() {
    for opcode in [
        0x01u8, 0x04, 0x05, 0x06, 0x09, 0x0A, 0x10, 0x20, 0x7F, 0x80, 0xFE,
    ] {
        assert_eq!(
            parse_control_point(&[opcode]),
            None,
            "opcode 0x{opcode:02X} must not be accepted"
        );
    }
}

#[test]
fn parse_ignores_trailing_garbage() {
    let garbage: [u8; 255] = core::array::from_fn(|i| i as u8);

    let mut buf = [0u8; 256];
    buf[0] = 0x00;
    buf[1..].copy_from_slice(&garbage);
    assert_eq!(
        parse_control_point(&buf),
        Some(ControlCommand::RequestControl)
    );

    let mut buf = [0u8; 258];
    buf[0..3].copy_from_slice(&[0x02, 0x00, 0x00]);
    buf[3..].copy_from_slice(&garbage);
    assert_eq!(
        parse_control_point(&buf),
        Some(ControlCommand::SetTargetSpeed(0))
    );

    let mut buf = [0u8; 256];
    buf[0] = 0x07;
    buf[1..].copy_from_slice(&garbage);
    assert_eq!(
        parse_control_point(&buf),
        Some(ControlCommand::StartOrResume)
    );
}

#[test]
fn parse_accepts_the_extreme_well_formed_payloads() {
    assert_eq!(
        parse_control_point(&[0x02, 0xFF, 0xFF]),
        Some(ControlCommand::SetTargetSpeed(u16::MAX))
    );
    assert_eq!(
        parse_control_point(&[0x03, 0xFF, 0x7F]),
        Some(ControlCommand::SetTargetInclination(i16::MAX))
    );
    assert_eq!(
        parse_control_point(&[0x03, 0x00, 0x80]),
        Some(ControlCommand::SetTargetInclination(i16::MIN))
    );
    assert_eq!(
        parse_control_point(&[0x08, 0xFF]),
        Some(ControlCommand::StopOrPause(255))
    );
}

#[test]
fn parse_never_panics_on_any_one_byte_input() {
    for b in 0u8..=255 {
        let _ = parse_control_point(&[b]);
    }
}

#[test]
fn parse_never_panics_on_any_two_byte_input() {
    for b0 in 0u8..=255 {
        for b1 in 0u8..=255 {
            let _ = parse_control_point(&[b0, b1]);
        }
    }
}

#[test]
fn parse_never_panics_on_any_three_byte_input() {
    // Beyond the daemon's fuzz: 3 bytes is the first length at which BOTH
    // multi-byte opcodes read their parameter, so it is where an off-by-one in
    // the length guard would actually index out of bounds.
    for b0 in 0u8..=255 {
        for b1 in 0u8..=255 {
            for b2 in [0u8, 1, 0x7F, 0x80, 0xFF] {
                let _ = parse_control_point(&[b0, b1, b2]);
            }
        }
    }
}

#[test]
fn opcode_of_a_parsed_command_round_trips() {
    // The response indication echoes the request opcode. Deriving it from the
    // variant (rather than remembering the raw byte) must not change it.
    for raw in [
        &[0x00u8][..],
        &[0x02, 0xF4, 0x01][..],
        &[0x03, 0x1E, 0x00][..],
        &[0x07][..],
        &[0x08, 0x01][..],
    ] {
        let cmd = parse_control_point(raw).expect("vector must parse");
        assert_eq!(cmd.opcode(), raw[0]);
    }
}

// --- Control Point response ----------------------------------------------

#[test]
fn control_response_vectors() {
    assert_eq!(
        encode_control_response(0x02, RESULT_SUCCESS),
        [0x80, 0x02, 0x01]
    );
    assert_eq!(
        encode_control_response(0x00, RESULT_NOT_SUPPORTED),
        [0x80, 0x00, 0x02]
    );
}

#[test]
fn control_response_is_three_bytes_for_every_combination() {
    for opcode in [0x00u8, 0x02, 0x03, 0x07, 0x08, 0xFF] {
        for result in [
            RESULT_SUCCESS,
            RESULT_NOT_SUPPORTED,
            RESULT_INVALID_PARAM,
            RESULT_FAILED,
        ] {
            let resp = encode_control_response(opcode, result);
            assert_eq!(resp.len(), 3);
            assert_eq!(resp[0], RESPONSE_CODE);
            assert_eq!(resp[1], opcode);
            assert_eq!(resp[2], result);
        }
    }
}

// --- Unit conversion ------------------------------------------------------

#[test]
fn conversion_matches_daemon_vectors() {
    // 1.0 mph -> 160, NOT 161: 1609 * 10 / 100 truncates 160.9.
    assert_eq!(mph_tenths_to_kmh_hundredths(10), 160);
    // 12.0 mph -> 1930, NOT 1931: truncates 1930.8.
    assert_eq!(mph_tenths_to_kmh_hundredths(120), 1930);
    assert_eq!(mph_tenths_to_kmh_hundredths(0), 0);
    // 1.61 km/h -> 10 tenths mph (161 * 100 / 1609 = 10.006).
    assert_eq!(kmh_hundredths_to_mph_tenths(161), 10);
    assert_eq!(kmh_hundredths_to_mph_tenths(0), 0);
}

#[test]
fn roundtrip_is_within_one_tenth_mph() {
    for mph_tenths in [0u16, 5, 10, 25, 50, 75, 100, 120] {
        let kmh = mph_tenths_to_kmh_hundredths(mph_tenths);
        let back = kmh_hundredths_to_mph_tenths(kmh);
        let diff = (back as i32 - mph_tenths as i32).unsigned_abs();
        assert!(diff <= 1, "{mph_tenths} tenths -> {kmh} -> {back}");
    }
}

#[test]
fn roundtrip_is_within_one_tenth_over_the_whole_belt_range() {
    // Beyond the daemon's 8 samples: EVERY speed the belt can be commanded.
    // A one-off in the divisor would show up here and not in the sample set.
    for mph_tenths in 0u16..=120 {
        let back = kmh_hundredths_to_mph_tenths(mph_tenths_to_kmh_hundredths(mph_tenths));
        let diff = (back as i32 - mph_tenths as i32).unsigned_abs();
        assert!(diff <= 1, "{mph_tenths} tenths round-tripped to {back}");
    }
}

#[test]
fn conversion_extremes_match_the_daemon() {
    // The daemon's `as u16` cast TRUNCATES here (65535*1609/100 = 1_054_464,
    // which does not fit). Inherited verbatim so the two agree at every input,
    // and unreachable through the newtyped path below, which saturates.
    assert_eq!(
        mph_tenths_to_kmh_hundredths(u16::MAX),
        ((65535u32 * 1609) / 100) as u16
    );
    assert_eq!(
        kmh_hundredths_to_mph_tenths(u16::MAX),
        ((65535u32 * 100) / 1609) as u16
    );
}

#[test]
fn newtyped_speed_conversion_saturates_rather_than_wrapping() {
    assert_eq!(speed_tenths_to_kmh_hundredths(SpeedTenths::new(0)), 0);
    assert_eq!(speed_tenths_to_kmh_hundredths(SpeedTenths::new(120)), 1930);
    assert_eq!(speed_tenths_to_kmh_hundredths(SpeedTenths::new(-1)), 0);
    assert_eq!(
        speed_tenths_to_kmh_hundredths(SpeedTenths::new(i32::MIN)),
        0
    );

    // A huge speed must NEVER become a small km/h. The raw daemon function
    // TRUNCATES here — 6553.5 mph reports as 58.88 km/h — which is why the
    // newtyped path deliberately diverges above ~407 mph and clamps at the top
    // of the range instead. A first version of it saturated only its INPUT and
    // had exactly that bug, under a doc comment claiming otherwise.
    assert_eq!(
        mph_tenths_to_kmh_hundredths(u16::MAX),
        5882,
        "the raw daemon function truncates — this is why the newtyped one does \
         not simply call it"
    );
    assert_eq!(
        speed_tenths_to_kmh_hundredths(SpeedTenths::new(i32::MAX)),
        u16::MAX
    );
    assert_eq!(
        speed_tenths_to_kmh_hundredths(SpeedTenths::new(u16::MAX as i32)),
        u16::MAX
    );
}

#[test]
fn newtyped_speed_conversion_is_monotonic_over_the_whole_i32_domain() {
    // Monotonicity is the property that makes saturation SAFE: no larger belt
    // speed may encode as a smaller km/h. Checked densely across the reachable
    // range and at every power-of-two boundary above it, which is where the u32
    // intermediate and the u16 clamp change behaviour.
    let mut prev = 0u16;
    for t in 0i32..=5000 {
        let v = speed_tenths_to_kmh_hundredths(SpeedTenths::new(t));
        assert!(v >= prev, "not monotonic at {t} tenths: {prev} -> {v}");
        prev = v;
    }
    for shift in 13..31 {
        let t = 1i32 << shift;
        let v = speed_tenths_to_kmh_hundredths(SpeedTenths::new(t));
        assert!(v >= prev, "not monotonic at {t} tenths: {prev} -> {v}");
        prev = v;
    }
    assert_eq!(
        speed_tenths_to_kmh_hundredths(SpeedTenths::new(i32::MAX)),
        u16::MAX
    );
}

#[test]
fn incline_conversion_matches_the_half_percent_scale() {
    assert_eq!(incline_half_pct_to_ftms_tenths(InclineHalfPct::ZERO), 0);
    // 10 half-percent = 5.0% = 50 tenths (the daemon's Kinomap vector).
    assert_eq!(incline_half_pct_to_ftms_tenths(InclineHalfPct::new(10)), 50);
    assert_eq!(incline_half_pct_to_ftms_tenths(InclineHalfPct::new(1)), 5);
    assert_eq!(
        incline_half_pct_to_ftms_tenths(InclineHalfPct::new(30)),
        150
    );
    // Saturating, not wrapping — same argument as the speed side.
    assert_eq!(
        incline_half_pct_to_ftms_tenths(InclineHalfPct::new(i32::MAX)),
        i16::MAX
    );
    assert_eq!(
        incline_half_pct_to_ftms_tenths(InclineHalfPct::new(i32::MIN)),
        i16::MIN
    );
}

#[test]
fn incline_tenths_round_to_the_nearest_half_percent() {
    // The daemon does `(pct * 2.0).round() / 2.0` in floating point. These are
    // the same values in integers.
    for (tenths, expect_half) in [
        (0i16, 0i32),
        (5, 1),   // 0.5%
        (25, 5),  // 2.5%
        (27, 5),  // 2.7% -> 2.5%
        (28, 6),  // 2.8% -> 3.0%
        (50, 10), // 5.0%
        (52, 10),
        (53, 11),
        (150, 30), // 15.0% = APP_MAX
    ] {
        assert_eq!(
            ftms_tenths_to_incline_half_pct(tenths).get(),
            expect_half,
            "{tenths} tenths"
        );
    }
    // BELOW the range the daemon clamped to 0.0% and answered SUCCESS, and so
    // does this. It used to round away from zero symmetrically and hand the
    // negative to the controller, which refuses `incline < 0` — so a descent
    // in a route-simulating app left the belt on the previous UPHILL grade for
    // the whole downhill and error-indicated at the client the entire way.
    for tenths in [-1i16, -27, -28, -100, i16::MIN] {
        assert_eq!(
            ftms_tenths_to_incline_half_pct(tenths).get(),
            0,
            "{tenths} tenths must flatten to 0, not refuse"
        );
    }
}

#[test]
fn a_descent_flattens_the_belt_rather_than_leaving_it_on_the_last_hill() {
    // The exact write a route simulator sends on a downhill: opcode 0x03,
    // i16 LE -100 = -10.0%. End to end, parse -> effect -> motion, with the
    // belt currently commanded 8.0% uphill by the previous segment.
    let cmd = parse_control_point(&[0x03, 0x9C, 0xFF]).expect("well-formed");
    assert_eq!(cmd, ControlCommand::SetTargetInclination(-100));
    let effect = effect_of(cmd);
    assert_eq!(effect, CpEffect::SetIncline(InclineHalfPct::new(0)));

    let now = BeltNow {
        speed: SpeedTenths::new(60),
        incline: InclineHalfPct::new(16), // 8.0%
        resume_speed: SpeedTenths::new(60),
    };
    let (speed, incline) = motion_for(effect, now).expect("commands motion");
    // Speed carries through untouched; the grade flattens rather than sticking.
    assert_eq!(speed, SpeedTenths::new(60));
    assert_eq!(incline, InclineHalfPct::ZERO);
}

#[test]
fn above_the_range_is_still_refused_rather_than_silently_substituted() {
    // The other half of the asymmetry, pinned so a future "just clamp both
    // ends" cannot land quietly: 40.0% stays 40.0% and the controller refuses
    // it, where the daemon would have moved the belt at 15%.
    let cmd = parse_control_point(&[0x03, 0x90, 0x01]).expect("well-formed");
    assert_eq!(cmd, ControlCommand::SetTargetInclination(400));
    assert_eq!(
        effect_of(cmd),
        CpEffect::SetIncline(InclineHalfPct::new(80)) // 40.0%, unclamped
    );
}

#[test]
fn incline_rounding_agrees_with_the_daemons_float_maths_over_its_whole_domain() {
    // Every i16 the Control Point can carry, compared against the daemon's
    // expression evaluated in f64. This is the vector set the daemon never
    // had: a rounding rule stated in integers has to be PROVEN equal to the
    // float one, not asserted to be.
    //
    // The daemon's expression is `(tenths / 10.0).clamp(0.0, 15.0)` and then
    // `(pct * 2.0).round()`. Only the LOW end of that clamp is reproduced here
    // — deliberately, see `ftms_tenths_to_incline_half_pct` — so the float
    // reference carries `.max(0.0)` and not `.min(15.0)`.
    for tenths in i16::MIN..=i16::MAX {
        let float_half = (((tenths as f64) / 10.0).max(0.0) * 2.0).round() as i32;
        assert_eq!(
            ftms_tenths_to_incline_half_pct(tenths).get(),
            float_half,
            "{tenths} tenths"
        );
    }
}

#[test]
fn speed_tenths_conversion_agrees_over_the_whole_control_point_domain() {
    for kmh in 0u16..=u16::MAX {
        assert_eq!(
            kmh_hundredths_to_speed_tenths(kmh).get(),
            kmh_hundredths_to_mph_tenths(kmh) as i32
        );
    }
}

// --- Notifications --------------------------------------------------------

#[test]
fn training_status_vectors() {
    assert_eq!(
        encode_training_status(ControlCommand::StartOrResume),
        Some([0x00, 0x0D]) // Manual Mode (Quick Start)
    );
    assert_eq!(
        encode_training_status(ControlCommand::StopOrPause(1)),
        Some([0x00, 0x01]) // Idle
    );
    assert_eq!(
        encode_training_status(ControlCommand::StopOrPause(2)),
        Some([0x00, 0x01])
    );
    assert_eq!(encode_training_status(ControlCommand::RequestControl), None);
    assert_eq!(
        encode_training_status(ControlCommand::SetTargetSpeed(500)),
        None
    );
}

#[test]
fn machine_status_vectors() {
    let n = encode_status_notification(ControlCommand::SetTargetSpeed(500)).unwrap();
    assert_eq!(n.as_slice(), &[0x05, 0xF4, 0x01]);

    let n = encode_status_notification(ControlCommand::SetTargetInclination(-10)).unwrap();
    assert_eq!(n.as_slice(), &[0x06, 0xF6, 0xFF]);

    let n = encode_status_notification(ControlCommand::StartOrResume).unwrap();
    assert_eq!(n.as_slice(), &[0x04]);

    let n = encode_status_notification(ControlCommand::StopOrPause(2)).unwrap();
    assert_eq!(n.as_slice(), &[0x02, 0x02]);

    assert_eq!(
        encode_status_notification(ControlCommand::RequestControl),
        None
    );
}

#[test]
fn every_notification_fits_the_fixed_buffer() {
    // The whole reason `Notification` replaces the daemon's `Vec<u8>`: the
    // notify path must be allocation-free, which is only sound if 3 bytes is
    // genuinely the maximum. Checked over every parseable Control Point write.
    for b0 in 0u8..=255 {
        for b1 in [0u8, 1, 2, 0x7F, 0x80, 0xFF] {
            for b2 in [0u8, 0x7F, 0xFF] {
                if let Some(cmd) = parse_control_point(&[b0, b1, b2]) {
                    if let Some(n) = encode_status_notification(cmd) {
                        assert!(n.len() <= Notification::MAX);
                        assert_eq!(n.as_slice().len(), n.len());
                        assert!(!n.is_empty());
                    }
                }
            }
        }
    }
}

// --- Idle alive signal ----------------------------------------------------

fn fields(data: &[u8; TREADMILL_DATA_LEN]) -> (u16, u16, u32, i16, u16) {
    (
        u16::from_le_bytes([data[0], data[1]]),
        u16::from_le_bytes([data[2], data[3]]),
        u32::from_le_bytes([data[4], data[5], data[6], 0]),
        i16::from_le_bytes([data[7], data[8]]),
        u16::from_le_bytes([data[11], data[12]]),
    )
}

#[test]
fn alive_signal_substitutes_speed_and_elapsed_when_idle() {
    let data = encode_treadmill_data_with_alive(&TreadmillSnapshot::default(), 42);
    let (flags, speed, _d, incline, elapsed) = fields(&data);
    assert_eq!(flags, 0x040C);
    assert_eq!(speed, IDLE_MIN_SPEED_KMH_HUNDREDTHS);
    assert_eq!(incline, 0);
    assert_eq!(elapsed, 42, "session uptime substituted");
}

#[test]
fn alive_signal_passes_a_real_speed_through() {
    let snap = TreadmillSnapshot {
        speed: SpeedTenths::new(30), // 3.0 mph
        ..Default::default()
    };
    let (_f, speed, _d, _i, _e) = fields(&encode_treadmill_data_with_alive(&snap, 10));
    assert!(speed > IDLE_MIN_SPEED_KMH_HUNDREDTHS);
    assert_eq!(speed, mph_tenths_to_kmh_hundredths(30));
}

#[test]
fn alive_signal_incline_only_kinomap_flow() {
    // Kinomap on iOS sets incline and never speed; the belt is idle, so the
    // alive speed carries the notification and the incline is real.
    let snap = TreadmillSnapshot {
        incline: InclineHalfPct::new(10), // 5.0%
        ..Default::default()
    };
    let (_f, speed, _d, incline, elapsed) = fields(&encode_treadmill_data_with_alive(&snap, 5));
    assert_eq!(speed, IDLE_MIN_SPEED_KMH_HUNDREDTHS);
    assert_eq!(incline, 50);
    assert_eq!(elapsed, 5);
}

#[test]
fn alive_signal_does_not_invent_an_elapsed_time() {
    let (_f, speed, _d, _i, elapsed) = fields(&encode_treadmill_data_with_alive(
        &TreadmillSnapshot::default(),
        0,
    ));
    assert_eq!(speed, IDLE_MIN_SPEED_KMH_HUNDREDTHS);
    assert_eq!(elapsed, 0, "no substitution when the session is 0 too");
}

#[test]
fn alive_signal_prefers_a_real_workout_elapsed() {
    let snap = TreadmillSnapshot {
        speed: SpeedTenths::new(50),
        elapsed_secs: 120,
        ..Default::default()
    };
    let (_f, _s, _d, _i, elapsed) = fields(&encode_treadmill_data_with_alive(&snap, 300));
    assert_eq!(elapsed, 120);
}

#[test]
fn alive_speed_is_half_a_kilometre_per_hour() {
    assert_eq!(IDLE_MIN_SPEED_KMH_HUNDREDTHS, 50);
}

// --- The belt edge --------------------------------------------------------

#[test]
fn request_control_never_touches_the_belt() {
    let effect = effect_of(ControlCommand::RequestControl);
    assert_eq!(effect, CpEffect::AckOnly);
    assert_eq!(
        motion_for(effect, BeltNow::default()),
        None,
        "AckOnly must not command motion — doing so would take the lease"
    );
}

#[test]
fn set_speed_converts_to_belt_units_and_preserves_incline() {
    // 4.83 km/h -> 30 tenths (3.0 mph).
    let effect = effect_of(ControlCommand::SetTargetSpeed(483));
    assert_eq!(effect, CpEffect::SetSpeed(SpeedTenths::new(30)));

    let now = BeltNow {
        speed: SpeedTenths::new(10),
        incline: InclineHalfPct::new(6),
        resume_speed: SpeedTenths::new(10),
    };
    assert_eq!(
        motion_for(effect, now),
        Some((SpeedTenths::new(30), InclineHalfPct::new(6))),
        "a speed write must not disturb the incline"
    );
}

#[test]
fn set_incline_converts_to_belt_units_and_preserves_speed() {
    let effect = effect_of(ControlCommand::SetTargetInclination(50)); // 5.0%
    assert_eq!(effect, CpEffect::SetIncline(InclineHalfPct::new(10)));

    let now = BeltNow {
        speed: SpeedTenths::new(35),
        incline: InclineHalfPct::new(2),
        resume_speed: SpeedTenths::new(35),
    };
    assert_eq!(
        motion_for(effect, now),
        Some((SpeedTenths::new(35), InclineHalfPct::new(10))),
        "an incline write must not disturb the speed"
    );
}

#[test]
fn start_resumes_the_retained_target_not_the_current_speed() {
    let now = BeltNow {
        speed: SpeedTenths::ZERO,
        incline: InclineHalfPct::new(4),
        resume_speed: SpeedTenths::new(45),
    };
    assert_eq!(
        motion_for(effect_of(ControlCommand::StartOrResume), now),
        Some((SpeedTenths::new(45), InclineHalfPct::new(4)))
    );
}

#[test]
fn stop_zeroes_both_axes_whatever_the_parameter() {
    let now = BeltNow {
        speed: SpeedTenths::new(70),
        incline: InclineHalfPct::new(20),
        resume_speed: SpeedTenths::new(70),
    };
    // 1 = stop, 2 = pause. There is no paused state below the application
    // tier, and a "pause" that left the belt running is the dangerous reading.
    for param in [1u8, 2, 0, 255] {
        assert_eq!(
            motion_for(effect_of(ControlCommand::StopOrPause(param)), now),
            Some((SpeedTenths::ZERO, InclineHalfPct::ZERO)),
            "param {param}"
        );
    }
}

#[test]
fn an_out_of_range_write_converts_faithfully_and_is_left_for_the_controller() {
    // 40 mph as km/h*100. The daemon silently substituted 12.0 mph HERE and
    // moved the belt at a speed nobody asked for; this crate hands the honest
    // value to `control::command`, whose clamp refuses it, and the peer is
    // told INVALID_PARAM. No clamp in this crate — one opinion about safety.
    let effect = effect_of(ControlCommand::SetTargetSpeed(6437));
    let CpEffect::SetSpeed(s) = effect else {
        panic!("expected SetSpeed")
    };
    assert!(
        s.get() > SpeedTenths::MAX.get(),
        "must NOT be pre-clamped: got {}",
        s.get()
    );
    assert_eq!(result_for_reject(CpReject::Refused), RESULT_INVALID_PARAM);
}

#[test]
fn a_lease_conflict_is_reported_as_failed_not_invalid() {
    // A running program owns the belt. The write was well-formed; the machine
    // simply would not do it. Telling the client INVALID_PARAM would make it
    // give up on a value that is perfectly legal.
    assert_eq!(result_for_reject(CpReject::NotOwner), RESULT_FAILED);
    assert_eq!(result_for_reject(CpReject::Other), RESULT_FAILED);
}

#[test]
fn every_parseable_write_has_an_effect_and_bounded_motion() {
    // Totality over the untrusted domain: no Control Point write can parse and
    // then fall through unhandled, and none produces motion outside i32.
    let now = BeltNow {
        speed: SpeedTenths::new(20),
        incline: InclineHalfPct::new(4),
        resume_speed: SpeedTenths::new(20),
    };
    for b0 in 0u8..=255 {
        for b1 in [0u8, 1, 2, 0x7F, 0x80, 0xFF] {
            for b2 in [0u8, 0x7F, 0x80, 0xFF] {
                let Some(cmd) = parse_control_point(&[b0, b1, b2]) else {
                    continue;
                };
                let effect = effect_of(cmd);
                match motion_for(effect, now) {
                    None => assert_eq!(effect, CpEffect::AckOnly),
                    Some((s, i)) => {
                        // Bounded by construction: both conversions are total
                        // over the u16/i16 domain a write can carry.
                        assert!(s.get().abs() <= 4074, "speed {} tenths", s.get());
                        assert!(i.get().abs() <= 6554, "incline {} half-pct", i.get());
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Distance units — thousandths of a mile in, metres out
// ---------------------------------------------------------------------------
//
// The daemon has no counterpart: on the Pi, `TreadmillState.distance` is
// already metres by the time `ftms_service` sees it. This device measures in
// thousandths of a MILE (`program_core::record::distance_milli`, rendered as
// `x.xx mi` in the run history and carried that way in the app's session
// frame), so the conversion happens at this edge — and a missing conversion
// would report 1.6x the real distance to Zwift for a whole run, which looks
// entirely plausible while being wrong.

#[test]
fn a_mile_is_1609_metres() {
    assert_eq!(ble_core::ftms::miles_milli_to_meters(1000), 1609);
}

#[test]
fn distance_conversion_vectors() {
    for (milli, meters) in [
        (0u32, 0u32),
        (1, 1),          // 0.001 mi -> 1.609 m, truncated
        (500, 804),      // half a mile
        (1000, 1609),
        (2075, 3338),    // the 2.075 mi vector record.rs itself uses
        (5000, 8045),
        (26_200, 42_155), // a marathon
    ] {
        assert_eq!(
            ble_core::ftms::miles_milli_to_meters(milli),
            meters,
            "{milli} thousandths of a mile"
        );
    }
}

#[test]
fn distance_conversion_is_monotonic_and_never_wraps() {
    // A wrap would put a SMALL distance on the wire for a huge input, which
    // reads to a client as the run resetting mid-session.
    let mut prev = 0u32;
    let mut x = 0u32;
    loop {
        let m = ble_core::ftms::miles_milli_to_meters(x);
        assert!(m >= prev, "went backwards at {x}");
        prev = m;
        if x > u32::MAX - 1_000_003 {
            break;
        }
        x += 1_000_003;
    }
    assert_eq!(ble_core::ftms::miles_milli_to_meters(u32::MAX), u32::MAX);
}

#[test]
fn the_mile_constant_is_the_same_one_the_speed_conversion_uses() {
    // Two different miles inside one crate is how a speed and a distance stop
    // agreeing with each other. 1 mph for 1 hour must be 1609 m, using the
    // SPEED path to get there: 10 tenths of a mph -> km/h*100 -> metres/hour.
    let kmh_hundredths = ble_core::ftms::mph_tenths_to_kmh_hundredths(10) as u32;
    // km/h*100 -> metres per hour is *10.
    assert_eq!(kmh_hundredths * 10, 1600);
    // Both paths use 1609 as the metres-per-mile constant; the speed path
    // additionally truncates at km/h*100 resolution, which is the daemon's
    // documented loss and is pinned by `conversion_matches_daemon_vectors`.
    assert_eq!(ble_core::ftms::miles_milli_to_meters(1000), 1609);
}

#[test]
fn the_default_status_reads_are_the_daemons_and_are_never_zero_length() {
    // Both characteristics have MANDATORY fixed leading fields, so 2 octets is
    // the minimum legal value. The device answered READ_CHR on both with a
    // ZERO-length value and justified it as "what the daemon's snapshot read
    // does" — the daemon does no such thing; these are its literal vectors.
    assert_eq!(encode_training_status_idle(), [0x00, 0x01]);
    assert_eq!(encode_machine_status_stopped(), [0x02, 0x01]);

    // The on-subscribe initial value and the read value are the SAME bytes in
    // the daemon, which is why one constant serves both call sites.
    assert_eq!(
        encode_training_status(ControlCommand::StopOrPause(1)),
        Some(encode_training_status_idle()),
        "a Stop notification and the idle read must agree"
    );
}
