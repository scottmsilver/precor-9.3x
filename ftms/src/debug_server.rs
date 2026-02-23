//! TCP debug server for testing the FTMS daemon without BLE hardware.
//!
//! Listens on a TCP port (default 8826) and accepts line-based text commands
//! with hex-encoded binary payloads — mirroring exactly what a BLE FTMS client
//! would send/receive via GATT characteristics.
//!
//! Usage from dev machine:
//!   nc rpi 8826
//!
//! Commands:
//!   state           → human-readable treadmill state
//!   td              → treadmill data (0x2ACD) as hex
//!   feat            → feature (0x2ACC) as hex
//!   sr              → speed range (0x2AD4) as hex
//!   ir              → incline range (0x2AD5) as hex
//!   cp <hex>        → write to control point (0x2AD9), returns response hex
//!   sub             → subscribe to 1 Hz treadmill data stream (hex lines)
//!   help            → list commands

use std::sync::Arc;

use log::info;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

use crate::protocol;
use crate::treadmill::TreadmillState;

/// Run the TCP debug server.
pub async fn run(
    state: Arc<Mutex<TreadmillState>>,
    socket_path: String,
    port: u16,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let listener = TcpListener::bind(("0.0.0.0", port)).await?;
    info!("Debug server listening on port {}", port);

    loop {
        let (stream, addr) = listener.accept().await?;
        info!("Debug client connected from {}", addr);

        let state = state.clone();
        let socket_path = socket_path.clone();

        tokio::spawn(async move {
            if let Err(e) = handle_client(stream, state, socket_path).await {
                info!("Debug client {} disconnected: {}", addr, e);
            }
        });
    }
}

async fn handle_client(
    stream: tokio::net::TcpStream,
    state: Arc<Mutex<TreadmillState>>,
    socket_path: String,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    writer
        .write_all(b"ftms-debug> connected. type 'help' for commands.\n")
        .await?;

    loop {
        writer.write_all(b"ftms-debug> ").await?;

        match lines.next_line().await? {
            Some(line) => {
                let line = line.trim().to_lowercase();
                if line.is_empty() {
                    continue;
                }

                let response = match line.split_once(' ') {
                    Some(("cp", hex)) => handle_cp(hex.trim(), &socket_path).await,
                    _ => match line.as_str() {
                        "help" => Ok(HELP_TEXT.to_string()),
                        "state" => handle_state(&state).await,
                        "td" => handle_td(&state).await,
                        "feat" => Ok(format!("feat {}", hex_encode(&protocol::encode_feature()))),
                        "sr" => Ok(format!("range {}", hex_encode(&protocol::encode_speed_range()))),
                        "ir" => Ok(format!("range {}", hex_encode(&protocol::encode_incline_range()))),
                        "sub" => {
                            handle_subscribe(&state, &mut writer).await?;
                            continue; // subscribe handles its own output
                        }
                        "quit" | "exit" => return Ok(()),
                        _ => Ok(format!("unknown command: '{}'. type 'help'.", line)),
                    },
                };

                match response {
                    Ok(msg) => {
                        writer.write_all(msg.as_bytes()).await?;
                        writer.write_all(b"\n").await?;
                    }
                    Err(e) => {
                        writer
                            .write_all(format!("error: {}\n", e).as_bytes())
                            .await?;
                    }
                }
            }
            None => return Ok(()), // EOF
        }
    }
}

async fn handle_state(
    state: &Arc<Mutex<TreadmillState>>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let s = state.lock().await;
    let speed_mph = s.speed_tenths_mph as f64 / 10.0;
    let speed_kmh = protocol::mph_tenths_to_kmh_hundredths(s.speed_tenths_mph) as f64 / 100.0;
    Ok(format!(
        "speed:    {:.1} mph ({:.2} km/h)  [raw: {} tenths]\n\
         incline:  {:.1}%  [raw: {} half-pct]\n\
         elapsed:  {}s ({}:{:02})\n\
         distance: {}m ({:.2} mi)\n\
         connected: {}",
        speed_mph,
        speed_kmh,
        s.speed_tenths_mph,
        s.incline_half_pct as f64 / 2.0,
        s.incline_half_pct,
        s.elapsed_secs,
        s.elapsed_secs / 60,
        s.elapsed_secs % 60,
        s.distance_meters,
        s.distance_meters as f64 / 1609.34,
        s.connected,
    ))
}

async fn handle_td(
    state: &Arc<Mutex<TreadmillState>>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let s = state.lock().await;
    let data = s.encode_ftms_data();
    let speed_kmh = protocol::mph_tenths_to_kmh_hundredths(s.speed_tenths_mph);
    let incline_tenths = (s.incline_half_pct as i16) * 5;

    Ok(format!(
        "data {} (speed={} incline={} dist={}m elapsed={}s)",
        hex_encode(&data),
        speed_kmh,
        incline_tenths,
        s.distance_meters,
        s.elapsed_secs,
    ))
}

async fn handle_cp(
    hex: &str,
    socket_path: &str,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let bytes = hex_decode(hex)?;
    if bytes.is_empty() {
        return Ok("error: empty control point data".to_string());
    }

    let opcode = bytes[0];
    match protocol::parse_control_point(&bytes) {
        Some(cmd) => {
            let description = match &cmd {
                protocol::ControlCommand::RequestControl => "Request Control".to_string(),
                protocol::ControlCommand::SetTargetSpeed(v) => {
                    let mph = protocol::kmh_hundredths_to_mph_tenths(*v) as f64 / 10.0;
                    format!("Set Target Speed: {} km/h*100 ({:.1} mph)", v, mph)
                }
                protocol::ControlCommand::SetTargetInclination(v) => {
                    format!("Set Target Incline: {} ({:.1}%)", v, *v as f64 / 10.0)
                }
                protocol::ControlCommand::StartOrResume => "Start/Resume".to_string(),
                protocol::ControlCommand::StopOrPause(p) => {
                    format!("Stop/Pause (param={})", p)
                }
            };

            // Execute via the same handler the BLE GATT server uses
            let (resp_opcode, result_code) =
                crate::ftms_service::handle_control_command(&cmd, socket_path).await;
            let response = protocol::encode_control_response(resp_opcode, result_code);

            let mut output = format!("parsed: {}\nresp {}", description, hex_encode(&response));
            if result_code != protocol::RESULT_SUCCESS {
                output.push_str("\nwarning: command failed (see daemon log)");
            }

            Ok(output)
        }
        None => {
            let response = protocol::encode_control_response(opcode, protocol::RESULT_NOT_SUPPORTED);
            Ok(format!(
                "parsed: unknown opcode 0x{:02x}\nresp {}",
                opcode,
                hex_encode(&response)
            ))
        }
    }
}

async fn handle_subscribe(
    state: &Arc<Mutex<TreadmillState>>,
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    writer
        .write_all(b"subscribed to treadmill data at 1 Hz. ctrl-c to stop.\n")
        .await?;

    let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
    loop {
        interval.tick().await;

        let s = state.lock().await;
        let data = s.encode_ftms_data();
        let speed_mph = s.speed_tenths_mph as f64 / 10.0;
        let incline_half_pct = s.incline_half_pct;
        drop(s);

        let line = format!(
            "data {} | {:.1}mph {:.1}%\n",
            hex_encode(&data),
            speed_mph,
            incline_half_pct as f64 / 2.0,
        );

        if writer.write_all(line.as_bytes()).await.is_err() {
            break;
        }
    }

    Ok(())
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join("")
}

fn hex_decode(hex: &str) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    let hex = hex.replace(' ', "");
    if hex.len() % 2 != 0 {
        return Err("hex string must have even length".into());
    }
    (0..hex.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&hex[i..i + 2], 16)
                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { Box::new(e) })
        })
        .collect()
}

const HELP_TEXT: &str = "\
commands:
  state           show current treadmill state (human-readable)
  td              read treadmill data characteristic (0x2ACD) as hex
  feat            read feature characteristic (0x2ACC) as hex
  sr              read supported speed range (0x2AD4) as hex
  ir              read supported incline range (0x2AD5) as hex
  cp <hex>        write to control point (0x2AD9), execute + show response
  sub             subscribe to 1 Hz treadmill data stream
  help            this message
  quit            disconnect

control point examples:
  cp 00           Request Control
  cp 02 f401      Set Target Speed 5.00 km/h (500 = 0x01f4 LE)
  cp 02 8b07      Set Target Speed 19.31 km/h (1931 = 0x078b LE)
  cp 03 1e00      Set Target Incline 3.0% (30 = 0x001e LE)
  cp 03 9600      Set Target Incline 15.0% (150 = 0x0096 LE)
  cp 07           Start or Resume
  cp 08 01        Stop
  cp 08 02        Pause

all values are little-endian hex, matching raw BLE GATT writes.";
