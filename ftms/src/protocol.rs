/// FTMS (Fitness Machine Service) binary protocol encoding/decoding.
///
/// All multi-byte values are little-endian per the Bluetooth GATT specification.
/// FTMS uses metric units internally: speed in km/h * 100, inclination in % * 10.

use uuid::Uuid;

// Bluetooth SIG base UUID: 0000XXXX-0000-1000-8000-00805f9b34fb
pub const fn ble_uuid(short: u16) -> Uuid {
    Uuid::from_u128(
        ((short as u128) << 96) | 0x0000_0000_0000_1000_8000_00805f9b34fb_u128,
    )
}

// FTMS service and characteristic UUIDs
pub const FTMS_SERVICE_UUID: Uuid = ble_uuid(0x1826);
pub const FEATURE_UUID: Uuid = ble_uuid(0x2ACC);
pub const TREADMILL_DATA_UUID: Uuid = ble_uuid(0x2ACD);
pub const SPEED_RANGE_UUID: Uuid = ble_uuid(0x2AD4);
pub const INCLINE_RANGE_UUID: Uuid = ble_uuid(0x2AD5);
pub const TRAINING_STATUS_UUID: Uuid = ble_uuid(0x2AD3);
pub const CONTROL_POINT_UUID: Uuid = ble_uuid(0x2AD9);
pub const MACHINE_STATUS_UUID: Uuid = ble_uuid(0x2ADA);

#[derive(Debug, PartialEq)]
pub enum ControlCommand {
    RequestControl,
    SetTargetSpeed(u16),       // km/h * 100
    SetTargetInclination(i16), // percent * 10
    StartOrResume,
    StopOrPause(u8),           // 1=stop, 2=pause
}

// Control Point result codes (FTMS spec Table 4.24)
pub const RESULT_SUCCESS: u8 = 0x01;
pub const RESULT_NOT_SUPPORTED: u8 = 0x02;
pub const RESULT_INVALID_PARAM: u8 = 0x03;
pub const RESULT_FAILED: u8 = 0x04;
pub const RESPONSE_CODE: u8 = 0x80;

/// Encode FTMS Treadmill Data characteristic (0x2ACD).
///
/// Flags 0x040C = bits 2,3,10 set:
///   - Bit 0 = 0: Instantaneous Speed present
///   - Bit 2 = 1: Total Distance present
///   - Bit 3 = 1: Inclination and Ramp Angle present
///   - Bit 10 = 1: Elapsed Time present
///
/// Layout: flags(2) + speed(2) + distance(3) + inclination(2) + ramp_angle(2) + elapsed(2) = 13 bytes
pub fn encode_treadmill_data(
    speed_kmh_hundredths: u16,
    incline_tenths: i16,
    distance_meters: u32,
    elapsed_secs: u16,
) -> Vec<u8> {
    let flags: u16 = 0x040C;
    let mut buf = Vec::with_capacity(13);

    // Flags (uint16 LE)
    buf.extend_from_slice(&flags.to_le_bytes());

    // Instantaneous Speed (uint16 LE, km/h with 0.01 resolution)
    buf.extend_from_slice(&speed_kmh_hundredths.to_le_bytes());

    // Total Distance (uint24 LE, meters)
    let dist_bytes = distance_meters.to_le_bytes();
    buf.push(dist_bytes[0]);
    buf.push(dist_bytes[1]);
    buf.push(dist_bytes[2]);

    // Inclination (sint16 LE, percent with 0.1 resolution)
    buf.extend_from_slice(&incline_tenths.to_le_bytes());

    // Ramp Angle Setting (sint16 LE, degree with 0.1 resolution) — always 0
    buf.extend_from_slice(&0i16.to_le_bytes());

    // Elapsed Time (uint16 LE, seconds)
    buf.extend_from_slice(&elapsed_secs.to_le_bytes());

    buf
}

/// Encode FTMS Feature characteristic (0x2ACC).
///
/// Fitness Machine Features (uint32 LE):
///   - Bit 2: Total Distance Supported
///   - Bit 3: Inclination Supported
///   - Bit 12: Elapsed Time Supported
///   = 0x0000_100C
///
/// Target Setting Features (uint32 LE):
///   - Bit 0: Speed Target Supported
///   - Bit 1: Inclination Target Supported
///   = 0x0000_0003
pub fn encode_feature() -> [u8; 8] {
    let machine_features: u32 = 0x0000_100C;
    let target_features: u32 = 0x0000_0003;
    let mut buf = [0u8; 8];
    buf[0..4].copy_from_slice(&machine_features.to_le_bytes());
    buf[4..8].copy_from_slice(&target_features.to_le_bytes());
    buf
}

/// Encode Supported Speed Range characteristic (0x2AD4).
///
/// 3x uint16 LE: minimum, maximum, step (all in km/h * 100).
///   - Min: 80  (0.80 km/h ~ 0.5 mph)
///   - Max: 1931 (19.31 km/h ~ 12.0 mph)
///   - Step: 16 (0.16 km/h ~ 0.1 mph)
pub fn encode_speed_range() -> [u8; 6] {
    let min: u16 = 80;
    let max: u16 = 1931;
    let step: u16 = 16;
    let mut buf = [0u8; 6];
    buf[0..2].copy_from_slice(&min.to_le_bytes());
    buf[2..4].copy_from_slice(&max.to_le_bytes());
    buf[4..6].copy_from_slice(&step.to_le_bytes());
    buf
}

/// Encode Supported Inclination Range characteristic (0x2AD5).
///
/// 3x sint16 LE: minimum, maximum, step (all in percent * 10).
///   - Min: 0   (0.0%)
///   - Max: 150 (15.0%)
///   - Step: 10 (1.0%)
pub fn encode_incline_range() -> [u8; 6] {
    let min: i16 = 0;
    let max: i16 = 150;
    let step: i16 = 10;
    let mut buf = [0u8; 6];
    buf[0..2].copy_from_slice(&min.to_le_bytes());
    buf[2..4].copy_from_slice(&max.to_le_bytes());
    buf[4..6].copy_from_slice(&step.to_le_bytes());
    buf
}

/// Parse FTMS Control Point writes (0x2AD9).
///
/// Returns `None` for unsupported/unknown opcodes or malformed data.
pub fn parse_control_point(bytes: &[u8]) -> Option<ControlCommand> {
    let opcode = *bytes.first()?;
    match opcode {
        0x00 => Some(ControlCommand::RequestControl),
        0x02 => {
            // Set Target Speed: opcode(1) + uint16 LE
            if bytes.len() < 3 {
                return None;
            }
            let speed = u16::from_le_bytes([bytes[1], bytes[2]]);
            Some(ControlCommand::SetTargetSpeed(speed))
        }
        0x03 => {
            // Set Target Inclination: opcode(1) + sint16 LE
            if bytes.len() < 3 {
                return None;
            }
            let incline = i16::from_le_bytes([bytes[1], bytes[2]]);
            Some(ControlCommand::SetTargetInclination(incline))
        }
        0x07 => Some(ControlCommand::StartOrResume),
        0x08 => {
            // Stop or Pause: opcode(1) + uint8
            if bytes.len() < 2 {
                return None;
            }
            Some(ControlCommand::StopOrPause(bytes[1]))
        }
        _ => None,
    }
}

/// Encode a Control Point response indication.
///
/// Format: `[0x80, request_opcode, result_code]`
pub fn encode_control_response(request_opcode: u8, result: u8) -> Vec<u8> {
    vec![RESPONSE_CODE, request_opcode, result]
}

/// Convert treadmill-native speed (mph * 10) to FTMS speed (km/h * 100).
///
/// 1 mph = 1.60934 km/h
/// mph_tenths * 0.1 mph * 1.60934 * 100 = mph_tenths * 16.0934
pub fn mph_tenths_to_kmh_hundredths(mph_tenths: u16) -> u16 {
    ((mph_tenths as u32) * 1609 / 100) as u16
}

/// Convert FTMS speed (km/h * 100) to treadmill-native speed (mph * 10).
///
/// kmh_hundredths * 0.01 km/h / 1.60934 * 10 = kmh_hundredths / 16.0934
pub fn kmh_hundredths_to_mph_tenths(kmh_hundredths: u16) -> u16 {
    ((kmh_hundredths as u32) * 100 / 1609) as u16
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encode_treadmill_data_zeros() {
        let data = encode_treadmill_data(0, 0, 0, 0);
        assert_eq!(data.len(), 13);
        // Flags: 0x040C LE
        assert_eq!(data[0], 0x0C);
        assert_eq!(data[1], 0x04);
        // Speed: 0
        assert_eq!(data[2], 0x00);
        assert_eq!(data[3], 0x00);
        // Distance (3 bytes): 0
        assert_eq!(data[4], 0x00);
        assert_eq!(data[5], 0x00);
        assert_eq!(data[6], 0x00);
        // Inclination: 0
        assert_eq!(data[7], 0x00);
        assert_eq!(data[8], 0x00);
        // Ramp angle: 0
        assert_eq!(data[9], 0x00);
        assert_eq!(data[10], 0x00);
        // Elapsed time: 0
        assert_eq!(data[11], 0x00);
        assert_eq!(data[12], 0x00);
    }

    #[test]
    fn test_encode_treadmill_data_running() {
        // speed=500 (5.00 km/h), incline=30 (3.0%), distance=1234m, elapsed=300s
        let data = encode_treadmill_data(500, 30, 1234, 300);
        assert_eq!(data.len(), 13);

        // Flags
        assert_eq!(u16::from_le_bytes([data[0], data[1]]), 0x040C);

        // Speed: 500 = 0x01F4 LE
        assert_eq!(u16::from_le_bytes([data[2], data[3]]), 500);

        // Distance: 1234 = 0x0004D2, 3 bytes LE
        assert_eq!(data[4], 0xD2);
        assert_eq!(data[5], 0x04);
        assert_eq!(data[6], 0x00);

        // Inclination: 30
        assert_eq!(i16::from_le_bytes([data[7], data[8]]), 30);

        // Ramp angle: 0
        assert_eq!(i16::from_le_bytes([data[9], data[10]]), 0);

        // Elapsed time: 300
        assert_eq!(u16::from_le_bytes([data[11], data[12]]), 300);
    }

    #[test]
    fn test_encode_feature() {
        let feat = encode_feature();
        assert_eq!(feat.len(), 8);
        let machine = u32::from_le_bytes([feat[0], feat[1], feat[2], feat[3]]);
        let target = u32::from_le_bytes([feat[4], feat[5], feat[6], feat[7]]);
        assert_eq!(machine, 0x0000_100C);
        assert_eq!(target, 0x0000_0003);
    }

    #[test]
    fn test_encode_speed_range() {
        let range = encode_speed_range();
        let min = u16::from_le_bytes([range[0], range[1]]);
        let max = u16::from_le_bytes([range[2], range[3]]);
        let step = u16::from_le_bytes([range[4], range[5]]);
        assert_eq!(min, 80);
        assert_eq!(max, 1931);
        assert_eq!(step, 16);
    }

    #[test]
    fn test_encode_incline_range() {
        let range = encode_incline_range();
        let min = i16::from_le_bytes([range[0], range[1]]);
        let max = i16::from_le_bytes([range[2], range[3]]);
        let step = i16::from_le_bytes([range[4], range[5]]);
        assert_eq!(min, 0);
        assert_eq!(max, 150);
        assert_eq!(step, 10);
    }

    #[test]
    fn test_parse_control_request_control() {
        let cmd = parse_control_point(&[0x00]);
        assert_eq!(cmd, Some(ControlCommand::RequestControl));
    }

    #[test]
    fn test_parse_control_set_speed() {
        // Opcode 0x02, speed = 500 (0x01F4 LE = [0xF4, 0x01])
        let cmd = parse_control_point(&[0x02, 0xF4, 0x01]);
        assert_eq!(cmd, Some(ControlCommand::SetTargetSpeed(500)));
    }

    #[test]
    fn test_parse_control_set_incline() {
        // Opcode 0x03, incline = 30 (0x001E LE = [0x1E, 0x00])
        let cmd = parse_control_point(&[0x03, 0x1E, 0x00]);
        assert_eq!(cmd, Some(ControlCommand::SetTargetInclination(30)));

        // Negative inclination (not used by our treadmill, but protocol supports it)
        // -10 as i16 = 0xFFF6 LE = [0xF6, 0xFF]
        let cmd_neg = parse_control_point(&[0x03, 0xF6, 0xFF]);
        assert_eq!(cmd_neg, Some(ControlCommand::SetTargetInclination(-10)));
    }

    #[test]
    fn test_parse_control_start() {
        let cmd = parse_control_point(&[0x07]);
        assert_eq!(cmd, Some(ControlCommand::StartOrResume));
    }

    #[test]
    fn test_parse_control_stop() {
        // Stop (param=1)
        let cmd = parse_control_point(&[0x08, 0x01]);
        assert_eq!(cmd, Some(ControlCommand::StopOrPause(1)));

        // Pause (param=2)
        let cmd = parse_control_point(&[0x08, 0x02]);
        assert_eq!(cmd, Some(ControlCommand::StopOrPause(2)));
    }

    #[test]
    fn test_parse_control_unknown() {
        let cmd = parse_control_point(&[0xFF]);
        assert_eq!(cmd, None);
    }

    #[test]
    fn test_parse_control_empty() {
        let cmd = parse_control_point(&[]);
        assert_eq!(cmd, None);
    }

    #[test]
    fn test_parse_control_truncated_speed() {
        // Opcode 0x02 but missing the uint16 param
        assert_eq!(parse_control_point(&[0x02]), None);
        assert_eq!(parse_control_point(&[0x02, 0xF4]), None);
    }

    #[test]
    fn test_parse_control_truncated_stop() {
        // Opcode 0x08 but missing the uint8 param
        assert_eq!(parse_control_point(&[0x08]), None);
    }

    #[test]
    fn test_encode_control_response() {
        let resp = encode_control_response(0x02, RESULT_SUCCESS);
        assert_eq!(resp, vec![0x80, 0x02, 0x01]);

        let resp = encode_control_response(0x00, RESULT_NOT_SUPPORTED);
        assert_eq!(resp, vec![0x80, 0x00, 0x02]);
    }

    #[test]
    fn test_mph_to_kmh_conversion() {
        // 1.0 mph = 10 tenths → ~161 hundredths km/h (1.609 km/h)
        let kmh = mph_tenths_to_kmh_hundredths(10);
        assert_eq!(kmh, 160); // 1609 * 10 / 100 = 160 (truncated from 160.9)

        // 12.0 mph = 120 tenths → ~1931 hundredths km/h (19.31 km/h)
        let kmh = mph_tenths_to_kmh_hundredths(120);
        assert_eq!(kmh, 1930); // 1609 * 120 / 100 = 1930 (truncated from 1930.8)

        // 0 mph → 0
        assert_eq!(mph_tenths_to_kmh_hundredths(0), 0);
    }

    #[test]
    fn test_kmh_to_mph_conversion() {
        // ~161 hundredths km/h → 10 tenths mph (1.0 mph)
        let mph = kmh_hundredths_to_mph_tenths(161);
        assert_eq!(mph, 10); // 161 * 100 / 1609 = 10.006 → 10

        // 0 → 0
        assert_eq!(kmh_hundredths_to_mph_tenths(0), 0);
    }

    #[test]
    fn test_conversion_roundtrip() {
        // Convert mph → kmh → mph, should be within ±1 of original
        for mph_tenths in [0u16, 5, 10, 25, 50, 75, 100, 120] {
            let kmh = mph_tenths_to_kmh_hundredths(mph_tenths);
            let back = kmh_hundredths_to_mph_tenths(kmh);
            let diff = (back as i32 - mph_tenths as i32).unsigned_abs();
            assert!(
                diff <= 1,
                "roundtrip failed for {mph_tenths} tenths mph: got {back} (diff {diff})"
            );
        }
    }

    // ---- Fuzz / adversarial tests ----

    #[test]
    fn test_parse_every_single_byte_opcode() {
        // Every possible single-byte input must return Some or None, never panic
        for byte in 0u8..=255 {
            let _ = parse_control_point(&[byte]);
        }
    }

    #[test]
    fn test_parse_all_opcodes_with_garbage_trailing() {
        // Valid opcodes followed by excessive trailing bytes — should still parse
        let garbage: Vec<u8> = (0..255).collect();

        // Request Control (0x00) ignores trailing data
        let mut buf = vec![0x00];
        buf.extend_from_slice(&garbage);
        assert_eq!(parse_control_point(&buf), Some(ControlCommand::RequestControl));

        // Set Speed (0x02) reads 2 bytes, ignores rest
        let mut buf = vec![0x02, 0x00, 0x00];
        buf.extend_from_slice(&garbage);
        assert_eq!(parse_control_point(&buf), Some(ControlCommand::SetTargetSpeed(0)));

        // Start (0x07) ignores trailing data
        let mut buf = vec![0x07];
        buf.extend_from_slice(&garbage);
        assert_eq!(parse_control_point(&buf), Some(ControlCommand::StartOrResume));
    }

    #[test]
    fn test_parse_control_every_two_byte_combo() {
        // All 65536 two-byte inputs — must not panic
        for b0 in 0u8..=255 {
            for b1 in 0u8..=255 {
                let _ = parse_control_point(&[b0, b1]);
            }
        }
    }

    #[test]
    fn test_parse_control_max_values() {
        // Speed = u16::MAX
        let cmd = parse_control_point(&[0x02, 0xFF, 0xFF]);
        assert_eq!(cmd, Some(ControlCommand::SetTargetSpeed(u16::MAX)));

        // Incline = i16::MAX (32767 = 3276.7%)
        let cmd = parse_control_point(&[0x03, 0xFF, 0x7F]);
        assert_eq!(cmd, Some(ControlCommand::SetTargetInclination(i16::MAX)));

        // Incline = i16::MIN (-32768)
        let cmd = parse_control_point(&[0x03, 0x00, 0x80]);
        assert_eq!(cmd, Some(ControlCommand::SetTargetInclination(i16::MIN)));

        // Stop with param = 255
        let cmd = parse_control_point(&[0x08, 0xFF]);
        assert_eq!(cmd, Some(ControlCommand::StopOrPause(255)));
    }

    #[test]
    fn test_parse_control_unsupported_opcodes() {
        // All opcodes we don't handle should return None
        for opcode in [0x01, 0x04, 0x05, 0x06, 0x09, 0x0A, 0x10, 0x20, 0x7F, 0x80, 0xFE] {
            assert_eq!(
                parse_control_point(&[opcode]),
                None,
                "opcode 0x{:02x} should return None",
                opcode
            );
        }
    }

    #[test]
    fn test_encode_treadmill_data_max_values() {
        let data = encode_treadmill_data(u16::MAX, i16::MAX, u32::MAX, u16::MAX);
        assert_eq!(data.len(), 13, "always 13 bytes regardless of values");

        let speed = u16::from_le_bytes([data[2], data[3]]);
        assert_eq!(speed, u16::MAX);

        let incline = i16::from_le_bytes([data[7], data[8]]);
        assert_eq!(incline, i16::MAX);

        let elapsed = u16::from_le_bytes([data[11], data[12]]);
        assert_eq!(elapsed, u16::MAX);

        // Distance is uint24 — only bottom 3 bytes of u32
        let dist = u32::from_le_bytes([data[4], data[5], data[6], 0]);
        assert_eq!(dist, 0x00FFFFFF, "uint24 should truncate to 3 bytes");
    }

    #[test]
    fn test_encode_treadmill_data_negative_incline() {
        let data = encode_treadmill_data(0, -150, 0, 0); // -15.0%
        let incline = i16::from_le_bytes([data[7], data[8]]);
        assert_eq!(incline, -150);
    }

    #[test]
    fn test_conversion_extreme_values() {
        // u16::MAX speed — should not overflow (uses u32 intermediate)
        let kmh = mph_tenths_to_kmh_hundredths(u16::MAX);
        assert!(kmh > 0, "should produce a positive value");

        let mph = kmh_hundredths_to_mph_tenths(u16::MAX);
        assert!(mph > 0, "should produce a positive value");

        // Verify no overflow: 65535 * 1609 = 105_446_415 fits in u32
        assert_eq!(kmh, ((65535u32 * 1609) / 100) as u16);
        assert_eq!(mph, ((65535u32 * 100) / 1609) as u16);
    }

    #[test]
    fn test_encode_control_response_all_combos() {
        // Every opcode + result combo should produce exactly 3 bytes
        for opcode in [0x00, 0x02, 0x03, 0x07, 0x08, 0xFF] {
            for result in [RESULT_SUCCESS, RESULT_NOT_SUPPORTED, RESULT_INVALID_PARAM, RESULT_FAILED] {
                let resp = encode_control_response(opcode, result);
                assert_eq!(resp.len(), 3);
                assert_eq!(resp[0], RESPONSE_CODE);
                assert_eq!(resp[1], opcode);
                assert_eq!(resp[2], result);
            }
        }
    }
}
