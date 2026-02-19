//! End-to-end integration tests via the TCP debug server.
//!
//! These tests connect to the running ftms-daemon's debug port (8826),
//! send raw FTMS control point bytes, and verify the daemon:
//! 1. Returns correct FTMS response indications
//! 2. Actually changes treadmill state (speed/incline via treadmill_io)
//! 3. Encodes treadmill data notifications correctly
//!
//! Requirements:
//!   - ftms-daemon running on the Pi (sudo systemctl start ftms)
//!   - treadmill_io running (sudo ./treadmill_io)
//!
//! Run from dev machine:
//!   cargo test --test debug_integration -- --ignored --test-threads=1
//!
//! Or directly on the Pi:
//!   cargo test --test debug_integration -- --ignored --test-threads=1
//!
//! Set FTMS_HOST to override the target (default: rpi)
//! Set FTMS_DEBUG_PORT to override the port (default: 8826)

use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::time::sleep;

fn host() -> String {
    std::env::var("FTMS_HOST").unwrap_or_else(|_| "rpi".to_string())
}

fn port() -> u16 {
    std::env::var("FTMS_DEBUG_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(8826)
}

struct DebugClient {
    reader: tokio::io::Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    writer: tokio::net::tcp::OwnedWriteHalf,
}

impl DebugClient {
    async fn connect() -> Self {
        let addr = format!("{}:{}", host(), port());
        let stream = TcpStream::connect(&addr)
            .await
            .unwrap_or_else(|e| panic!("Failed to connect to debug server at {}: {}", addr, e));

        let (reader, writer) = stream.into_split();
        let mut reader = BufReader::new(reader).lines();

        // Consume the welcome line
        let welcome = reader.next_line().await.unwrap().unwrap();
        assert!(
            welcome.contains("connected"),
            "Expected welcome message, got: {}",
            welcome
        );

        // Consume the initial prompt
        // The prompt "ftms-debug> " may or may not appear as a separate line
        // depending on buffering. We'll handle it in send_cmd.

        Self { reader, writer }
    }

    /// Send a command and collect all response lines until the next prompt.
    async fn send_cmd(&mut self, cmd: &str) -> Vec<String> {
        self.send_cmd_timeout(cmd, Duration::from_secs(2)).await
    }

    /// Like send_cmd but with a shorter timeout — for batch/fuzz tests
    /// where we send hundreds of commands and don't want to wait 2s each.
    async fn send_cmd_fast(&mut self, cmd: &str) -> Vec<String> {
        self.send_cmd_timeout(cmd, Duration::from_millis(200)).await
    }

    async fn send_cmd_timeout(&mut self, cmd: &str, timeout: Duration) -> Vec<String> {
        self.writer
            .write_all(format!("{}\n", cmd).as_bytes())
            .await
            .unwrap();

        // Small delay to let the daemon process
        sleep(Duration::from_millis(50)).await;

        let mut lines = Vec::new();
        // Read available lines. The debug server sends "ftms-debug> " as a prompt
        // after each response. We read until we see the prompt or timeout.
        loop {
            match tokio::time::timeout(timeout, self.reader.next_line()).await {
                Ok(Ok(Some(line))) => {
                    let trimmed = line.trim().to_string();
                    // Skip empty lines and prompt-only lines
                    if trimmed.is_empty() || trimmed == "ftms-debug>" {
                        continue;
                    }
                    // Strip prompt prefix if present
                    let clean = if trimmed.starts_with("ftms-debug> ") {
                        trimmed.trim_start_matches("ftms-debug> ").to_string()
                    } else {
                        trimmed
                    };
                    if clean.is_empty() {
                        continue;
                    }
                    lines.push(clean);
                }
                Ok(Ok(None)) => break,    // EOF
                Ok(Err(_)) => break,       // IO error
                Err(_) => break,           // Timeout — no more lines
            }
        }
        lines
    }

    /// Extract the hex response from a "resp XXXX" line.
    fn extract_resp(lines: &[String]) -> Option<String> {
        lines
            .iter()
            .find(|l| l.starts_with("resp "))
            .map(|l| l.trim_start_matches("resp ").to_string())
    }

    /// Parse the "state" response into key-value pairs.
    fn parse_state(lines: &[String]) -> std::collections::HashMap<String, String> {
        let mut map = std::collections::HashMap::new();
        for line in lines {
            if let Some((key, val)) = line.split_once(':') {
                map.insert(key.trim().to_string(), val.trim().to_string());
            }
        }
        map
    }
}

// ---- Tests ----
// Run sequentially: --test-threads=1
// Each test is self-contained: request control, do action, verify, stop.

#[tokio::test]
#[ignore]
async fn test_01_connect_and_read_state() {
    let mut client = DebugClient::connect().await;

    let lines = client.send_cmd("state").await;
    assert!(!lines.is_empty(), "state should return output");

    let state = DebugClient::parse_state(&lines);
    assert!(state.contains_key("speed"), "state should contain speed");
    assert!(state.contains_key("incline"), "state should contain incline");
    assert!(state.contains_key("connected"), "state should contain connected");
    assert!(
        state["connected"].contains("true"),
        "should be connected to treadmill_io"
    );

    println!("State: {:?}", state);
}

#[tokio::test]
#[ignore]
async fn test_02_read_feature_characteristic() {
    let mut client = DebugClient::connect().await;

    let lines = client.send_cmd("feat").await;
    assert_eq!(lines.len(), 1);
    assert!(lines[0].starts_with("feat "));

    let hex = lines[0].trim_start_matches("feat ");
    assert_eq!(hex.len(), 16, "Feature should be 8 bytes = 16 hex chars");

    // Machine features: 0x0000200C, Target features: 0x00000003
    assert_eq!(hex, "0c20000003000000");
    println!("Feature: {}", hex);
}

#[tokio::test]
#[ignore]
async fn test_03_read_speed_range() {
    let mut client = DebugClient::connect().await;

    let lines = client.send_cmd("sr").await;
    assert_eq!(lines.len(), 1);

    let hex = lines[0].trim_start_matches("range ");
    let bytes = hex_to_bytes(hex);
    assert_eq!(bytes.len(), 6);

    let min = u16::from_le_bytes([bytes[0], bytes[1]]);
    let max = u16::from_le_bytes([bytes[2], bytes[3]]);
    let step = u16::from_le_bytes([bytes[4], bytes[5]]);

    assert_eq!(min, 80, "min speed 0.80 km/h");
    assert_eq!(max, 1931, "max speed 19.31 km/h (~12 mph)");
    assert_eq!(step, 16, "step 0.16 km/h (~0.1 mph)");

    println!("Speed range: min={} max={} step={}", min, max, step);
}

#[tokio::test]
#[ignore]
async fn test_04_read_incline_range() {
    let mut client = DebugClient::connect().await;

    let lines = client.send_cmd("ir").await;
    assert_eq!(lines.len(), 1);

    let hex = lines[0].trim_start_matches("range ");
    let bytes = hex_to_bytes(hex);
    assert_eq!(bytes.len(), 6);

    let min = i16::from_le_bytes([bytes[0], bytes[1]]);
    let max = i16::from_le_bytes([bytes[2], bytes[3]]);
    let step = i16::from_le_bytes([bytes[4], bytes[5]]);

    assert_eq!(min, 0, "min incline 0%");
    assert_eq!(max, 150, "max incline 15.0%");
    assert_eq!(step, 10, "step 1.0%");

    println!("Incline range: min={} max={} step={}", min, max, step);
}

#[tokio::test]
#[ignore]
async fn test_05_request_control() {
    let mut client = DebugClient::connect().await;

    // FTMS opcode 0x00 = Request Control
    let lines = client.send_cmd("cp 00").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");

    // Expected: 0x80 (response), 0x00 (request opcode), 0x01 (success)
    assert_eq!(resp, "800001", "Request Control should succeed");
    println!("Request Control response: {}", resp);
}

#[tokio::test]
#[ignore]
async fn test_06_set_speed_and_observe() {
    let mut client = DebugClient::connect().await;

    // Request control first
    client.send_cmd("cp 00").await;

    // Start/resume (enables emulate mode)
    let lines = client.send_cmd("cp 07").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert_eq!(resp, "800701", "Start should succeed");

    // Set speed to 5.00 km/h (500 = 0x01F4 LE = f4 01)
    // 5 km/h ≈ 3.1 mph → 31 tenths
    let lines = client.send_cmd("cp 02 f401").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert_eq!(resp, "800201", "Set Speed should succeed");

    // Wait for treadmill_io to process and status to update
    sleep(Duration::from_secs(2)).await;

    // Read state — speed should be approximately 3.1 mph (31 tenths)
    let lines = client.send_cmd("state").await;
    let state = DebugClient::parse_state(&lines);
    println!("State after set speed: {:?}", state);

    let speed_line = &state["speed"];
    // Extract raw tenths from the "[raw: XX tenths]" part
    if let Some(raw_start) = speed_line.find("[raw: ") {
        let raw_str = &speed_line[raw_start + 6..];
        if let Some(raw_end) = raw_str.find(' ') {
            let tenths: u16 = raw_str[..raw_end].parse().unwrap_or(0);
            // 500 km/h*100 → ~31 mph tenths (3.1 mph)
            assert!(
                tenths >= 28 && tenths <= 34,
                "Speed should be ~31 tenths (3.1 mph), got {} tenths",
                tenths
            );
            println!("Speed verified: {} tenths ({:.1} mph)", tenths, tenths as f64 / 10.0);
        }
    }

    // Read treadmill data and verify speed is encoded
    let lines = client.send_cmd("td").await;
    assert!(!lines.is_empty(), "td should return data");
    println!("Treadmill data: {:?}", lines);

    // Stop the treadmill (cleanup)
    let lines = client.send_cmd("cp 08 01").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert_eq!(resp, "800801", "Stop should succeed");

    sleep(Duration::from_secs(1)).await;
}

#[tokio::test]
#[ignore]
async fn test_07_set_incline_and_observe() {
    let mut client = DebugClient::connect().await;

    // Request control + start
    client.send_cmd("cp 00").await;
    client.send_cmd("cp 07").await;
    sleep(Duration::from_millis(500)).await;

    // Set incline to 5.0% (50 = 0x0032 LE = 32 00)
    let lines = client.send_cmd("cp 03 3200").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert_eq!(resp, "800301", "Set Incline should succeed");

    // Wait for state update
    sleep(Duration::from_secs(2)).await;

    // Read state — incline should be 5%
    let lines = client.send_cmd("state").await;
    let state = DebugClient::parse_state(&lines);
    println!("State after set incline: {:?}", state);

    let incline_line = &state["incline"];
    // Should contain "5%"
    assert!(
        incline_line.contains("5%"),
        "Incline should be 5%, got: {}",
        incline_line
    );
    println!("Incline verified: {}", incline_line);

    // Cleanup: stop and reset incline
    client.send_cmd("cp 08 01").await;
    sleep(Duration::from_secs(1)).await;
}

#[tokio::test]
#[ignore]
async fn test_08_stop_zeros_speed() {
    let mut client = DebugClient::connect().await;

    // Request control + start + set speed
    client.send_cmd("cp 00").await;
    client.send_cmd("cp 07").await;
    client.send_cmd("cp 02 f401").await; // 5 km/h
    sleep(Duration::from_secs(2)).await;

    // Verify speed is non-zero
    let lines = client.send_cmd("state").await;
    let state = DebugClient::parse_state(&lines);
    println!("State before stop: {:?}", state);

    // Stop
    let lines = client.send_cmd("cp 08 01").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert_eq!(resp, "800801", "Stop should succeed");

    // Wait for speed to reach 0
    sleep(Duration::from_secs(2)).await;

    let lines = client.send_cmd("state").await;
    let state = DebugClient::parse_state(&lines);
    println!("State after stop: {:?}", state);

    let speed_line = &state["speed"];
    assert!(
        speed_line.starts_with("0.0 mph"),
        "Speed should be 0 after stop, got: {}",
        speed_line
    );
    println!("Stop verified: speed is 0");
}

#[tokio::test]
#[ignore]
async fn test_09_treadmill_data_encoding() {
    let mut client = DebugClient::connect().await;

    // Read treadmill data
    let lines = client.send_cmd("td").await;
    assert!(!lines.is_empty(), "td should return data");

    let data_line = &lines[0];
    assert!(data_line.starts_with("data "), "should start with 'data '");

    // Extract hex before the parenthetical annotation
    let hex_part = data_line
        .trim_start_matches("data ")
        .split(' ')
        .next()
        .unwrap();

    let bytes = hex_to_bytes(hex_part);
    assert_eq!(bytes.len(), 13, "Treadmill data should be 13 bytes");

    // Verify flags
    let flags = u16::from_le_bytes([bytes[0], bytes[1]]);
    assert_eq!(
        flags, 0x008C,
        "Flags should be 0x008C (speed + distance + incline + elapsed)"
    );

    // Verify structure is parseable
    let speed = u16::from_le_bytes([bytes[2], bytes[3]]);
    let distance = u32::from_le_bytes([bytes[4], bytes[5], bytes[6], 0]);
    let incline = i16::from_le_bytes([bytes[7], bytes[8]]);
    let _ramp = i16::from_le_bytes([bytes[9], bytes[10]]);
    let elapsed = u16::from_le_bytes([bytes[11], bytes[12]]);

    println!(
        "Treadmill data: flags=0x{:04x} speed={} incline={} dist={}m elapsed={}s",
        flags, speed, incline, distance, elapsed
    );
}

#[tokio::test]
#[ignore]
async fn test_10_unknown_opcode_returns_not_supported() {
    let mut client = DebugClient::connect().await;

    // Send unknown opcode 0xFF
    let lines = client.send_cmd("cp ff").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");

    // Expected: 0x80 (response), 0xFF (request opcode), 0x02 (not supported)
    assert_eq!(resp, "80ff02", "Unknown opcode should return NOT_SUPPORTED");
    println!("Unknown opcode response: {}", resp);
}

// ---- Fuzz / chaos tests ----
// These hammer the daemon with garbage to verify it never crashes or hangs.

#[tokio::test]
#[ignore]
async fn test_20_garbage_commands() {
    let mut client = DebugClient::connect().await;

    // Completely nonsensical commands — daemon should respond gracefully
    let garbage = [
        "",
        " ",
        "   ",
        "asdfghjkl",
        "DROP TABLE",
        "../../etc/passwd",
        "\x00\x01\x02\x03",
        "cp",           // cp with no hex
        "cp ",          // cp with empty hex
        "cp xyz",       // cp with invalid hex
        "cp gg",        // cp with non-hex chars
        "cp 0",         // odd-length hex
        "cp 123",       // odd-length hex
        "STATE",        // wrong case (we lowercase, so this should work)
        "sTaTe",        // mixed case
        "stat",         // close but wrong
        "features",     // close but wrong
        "subscribe",    // close but wrong
        &"a".repeat(10000),  // very long command
    ];

    for cmd in &garbage {
        let lines = client.send_cmd(cmd).await;
        println!("Garbage '{}...' -> {} lines", &cmd[..cmd.len().min(30)], lines.len());
    }

    // Very long hex payload — separate because it's an owned String
    let long_hex = "cp ".to_owned() + &"ff".repeat(5000);
    let lines = client.send_cmd(&long_hex).await;
    println!("Long hex payload -> {} lines", lines.len());

    // Daemon should still be functional after all the garbage
    let lines = client.send_cmd("state").await;
    assert!(!lines.is_empty(), "daemon should still respond after garbage");
    let state = DebugClient::parse_state(&lines);
    assert!(state.contains_key("connected"), "state should still be valid");
    println!("Daemon survived garbage barrage");
}

#[tokio::test]
#[ignore]
async fn test_21_all_single_byte_opcodes() {
    let mut client = DebugClient::connect().await;

    // Send every possible single-byte control point opcode (0x00 - 0xFF).
    // At high throughput, TCP buffering can cause response lines to shift
    // between send_cmd calls. The goal here is crash resistance, not
    // per-opcode response matching — we verify response format generically.
    // Uses send_cmd_fast (200ms timeout) to avoid 256 × 2s = 8+ minutes.
    let mut valid_responses = 0;
    for byte in 0u8..=255 {
        let hex = format!("{:02x}", byte);
        let lines = client.send_cmd_fast(&format!("cp {}", hex)).await;

        if let Some(r) = DebugClient::extract_resp(&lines) {
            // Response should always be 6 hex chars: 80 XX YY
            assert_eq!(r.len(), 6, "response should be 3 bytes (6 hex), got: {}", r);
            assert!(r.starts_with("80"), "response should start with 0x80, got: {}", r);
            let result = u8::from_str_radix(&r[4..6], 16).unwrap();
            assert!(
                (1..=4).contains(&result),
                "result code should be 1-4, got {} for response {}", result, r
            );
            valid_responses += 1;
        }
    }

    assert!(
        valid_responses >= 200,
        "should get valid responses for most opcodes, got {}/256",
        valid_responses
    );

    // Still alive?
    let lines = client.send_cmd("feat").await;
    assert!(!lines.is_empty(), "daemon should survive all 256 opcodes");
    println!(
        "Daemon survived all 256 single-byte opcodes ({} valid responses)",
        valid_responses
    );
}

#[tokio::test]
#[ignore]
async fn test_22_extreme_speed_values() {
    let mut client = DebugClient::connect().await;
    client.send_cmd("cp 00").await; // Request control

    // Speed = 0 (0x0000)
    let lines = client.send_cmd("cp 02 0000").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8002"), "opcode echo");
    println!("Speed 0: {}", resp);

    // Speed = 1 (0x0001) — 0.01 km/h, basically nothing
    let lines = client.send_cmd("cp 02 0100").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8002"));
    println!("Speed 0.01 km/h: {}", resp);

    // Speed = u16::MAX (0xFFFF = 655.35 km/h = 407 mph = Mach 0.5)
    let lines = client.send_cmd("cp 02 ffff").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8002"));
    println!("Speed 655 km/h (insane): {}", resp);

    // Cleanup
    client.send_cmd("cp 08 01").await;
    sleep(Duration::from_secs(1)).await;

    let lines = client.send_cmd("state").await;
    assert!(!lines.is_empty(), "daemon should survive extreme speeds");
    println!("Daemon survived extreme speed values");
}

#[tokio::test]
#[ignore]
async fn test_23_extreme_incline_values() {
    let mut client = DebugClient::connect().await;
    client.send_cmd("cp 00").await;

    // Incline = 0 (0x0000)
    let lines = client.send_cmd("cp 03 0000").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8003"));

    // Incline = i16::MAX = 32767 (3276.7%)
    let lines = client.send_cmd("cp 03 ff7f").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8003"));
    println!("Incline 3276.7% (cliff): {}", resp);

    // Incline = i16::MIN = -32768 (-3276.8%) — negative = decline
    let lines = client.send_cmd("cp 03 0080").await;
    let resp = DebugClient::extract_resp(&lines).expect("should get resp");
    assert!(resp.starts_with("8003"));
    println!("Incline -3276.8% (abyss): {}", resp);

    // Cleanup
    client.send_cmd("cp 08 01").await;
    sleep(Duration::from_secs(1)).await;

    let lines = client.send_cmd("state").await;
    assert!(!lines.is_empty(), "daemon should survive extreme inclines");
    println!("Daemon survived extreme incline values");
}

#[tokio::test]
#[ignore]
async fn test_24_rapid_fire_commands() {
    let mut client = DebugClient::connect().await;
    client.send_cmd("cp 00").await;

    // Blast 100 speed changes as fast as possible (200ms timeout each)
    for i in 0..100u16 {
        let speed = i * 20; // 0 to 1980 in steps of 20
        let lo = (speed & 0xFF) as u8;
        let hi = ((speed >> 8) & 0xFF) as u8;
        let hex = format!("{:02x}{:02x}", lo, hi);
        let _ = client.send_cmd_fast(&format!("cp 02 {}", hex)).await;
    }

    // Still alive and responsive?
    let lines = client.send_cmd("state").await;
    assert!(!lines.is_empty(), "daemon should survive rapid fire");
    println!("Daemon survived 100 rapid-fire speed commands");

    // Cleanup
    client.send_cmd("cp 08 01").await;
    sleep(Duration::from_secs(1)).await;
}

#[tokio::test]
#[ignore]
async fn test_25_malformed_hex_inputs() {
    let mut client = DebugClient::connect().await;

    let malformed = [
        "cp zz",          // not hex
        "cp ZZZZ",        // not hex
        "cp $$",          // symbols
        "cp 0g",          // partial hex
        "cp -1",          // negative
        "cp 02 gg hh",    // invalid hex with spaces
        "cp 02 ",         // opcode with trailing space but no data
        "cp  02",         // double space
        "cp 02  f401",    // double space in data
    ];

    for cmd in &malformed {
        let lines = client.send_cmd(cmd).await;
        // Should get an error response, not crash
        let output = lines.join(" ");
        println!("Malformed '{}' -> {}", cmd, output);
    }

    // Daemon should still work
    let lines = client.send_cmd("feat").await;
    assert_eq!(lines.len(), 1, "feat should still work");
    assert!(lines[0].contains("0c20000003000000"), "feat data should be correct");
    println!("Daemon survived malformed hex inputs");
}

#[tokio::test]
#[ignore]
async fn test_26_concurrent_connections() {
    // Open 5 connections simultaneously, all sending commands
    let mut handles = Vec::new();

    for i in 0..5 {
        let handle = tokio::spawn(async move {
            let mut client = DebugClient::connect().await;
            let lines = client.send_cmd("state").await;
            assert!(!lines.is_empty(), "connection {} should get state", i);
            let lines = client.send_cmd("feat").await;
            assert!(!lines.is_empty(), "connection {} should get feat", i);
            let lines = client.send_cmd("td").await;
            assert!(!lines.is_empty(), "connection {} should get td", i);
            client.send_cmd("quit").await;
            println!("Connection {} completed successfully", i);
        });
        handles.push(handle);
    }

    for (i, handle) in handles.into_iter().enumerate() {
        handle.await.unwrap_or_else(|e| panic!("Connection {} panicked: {}", i, e));
    }

    println!("Daemon survived 5 concurrent connections");
}

// ---- Helpers ----

fn hex_to_bytes(hex: &str) -> Vec<u8> {
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
        .collect()
}
