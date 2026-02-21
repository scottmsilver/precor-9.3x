//! TCP debug server for testing the HRM daemon without BLE hardware.
//!
//! Listens on a TCP port (default 8827) and accepts line-based text commands
//! for inspecting state and controlling the scanner.
//!
//! Usage from dev machine:
//!   nc rpi 8827
//!
//! Commands:
//!   state           show HR + device info
//!   sub             subscribe to 1 Hz HR stream
//!   scan            trigger BLE scan
//!   connect <addr>  connect to a device by address
//!   disconnect      disconnect from current device
//!   forget          forget saved device + disconnect
//!   mock <bpm>      fake a connected HRM at given BPM (for testing without hardware)
//!   mock off        stop mocking, revert to disconnected
//!   help            list commands
//!   quit            disconnect

use std::sync::Arc;

use log::info;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::Mutex;
use tokio::sync::mpsc;

use crate::config;
use crate::scanner::{HrmCommand, HrmState};

/// Run the TCP debug server.
pub async fn run(
    state: Arc<Mutex<HrmState>>,
    config_path: String,
    port: u16,
    cmd_tx: mpsc::Sender<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let listener = TcpListener::bind(("0.0.0.0", port)).await?;
    info!("Debug server listening on port {}", port);

    loop {
        let (stream, addr) = listener.accept().await?;
        info!("Debug client connected from {}", addr);

        let state = state.clone();
        let config_path = config_path.clone();
        let cmd_tx = cmd_tx.clone();

        tokio::spawn(async move {
            if let Err(e) = handle_client(stream, state, config_path, cmd_tx).await {
                info!("Debug client {} disconnected: {}", addr, e);
            }
        });
    }
}

async fn handle_client(
    stream: tokio::net::TcpStream,
    state: Arc<Mutex<HrmState>>,
    config_path: String,
    cmd_tx: mpsc::Sender<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    writer
        .write_all(b"hrm-debug> connected. type 'help' for commands.\n")
        .await?;

    loop {
        writer.write_all(b"hrm-debug> ").await?;

        match lines.next_line().await? {
            Some(line) => {
                let line = line.trim().to_lowercase();
                if line.is_empty() {
                    continue;
                }

                let response = match line.split_once(' ') {
                    Some(("connect", addr)) => handle_connect(addr.trim(), &cmd_tx).await,
                    Some(("mock", arg)) => handle_mock(arg.trim(), &state).await,
                    _ => match line.as_str() {
                        "help" => Ok(HELP_TEXT.to_string()),
                        "state" => handle_state(&state, &config_path).await,
                        "scan" => handle_scan(&cmd_tx).await,
                        "disconnect" => handle_disconnect(&cmd_tx).await,
                        "forget" => handle_forget(&cmd_tx).await,
                        "mock" => Ok("usage: mock <bpm> or mock off".to_string()),
                        "sub" => {
                            handle_subscribe(&state, &mut writer).await?;
                            continue;
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
            None => return Ok(()),
        }
    }
}

async fn handle_state(
    state: &Arc<Mutex<HrmState>>,
    config_path: &str,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let s = state.lock().await;
    let saved = config::load(config_path);
    let saved_info = match saved {
        Some(cfg) => format!("{} ({})", cfg.name, cfg.address),
        None => "none".to_string(),
    };

    let mut out = format!(
        "heart_rate: {} bpm\n\
         connected:  {}\n\
         device:     {}\n\
         address:    {}\n\
         scanning:   {}\n\
         saved:      {}",
        s.heart_rate,
        s.connected,
        if s.device_name.is_empty() { "-" } else { &s.device_name },
        if s.device_address.is_empty() { "-" } else { &s.device_address },
        s.scanning,
        saved_info,
    );

    if !s.available_devices.is_empty() {
        out.push_str("\navailable devices:");
        for d in &s.available_devices {
            out.push_str(&format!("\n  {} - {} (RSSI: {})", d.address, d.name, d.rssi));
        }
    }

    Ok(out)
}

async fn handle_scan(
    cmd_tx: &mpsc::Sender<HrmCommand>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let _ = cmd_tx.send(HrmCommand::Scan).await;
    Ok("scan triggered".to_string())
}

async fn handle_connect(
    addr: &str,
    cmd_tx: &mpsc::Sender<HrmCommand>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    if addr.is_empty() {
        return Ok("usage: connect <address>".to_string());
    }
    let _ = cmd_tx.send(HrmCommand::Connect(addr.to_string())).await;
    Ok(format!("connecting to {}...", addr))
}

async fn handle_disconnect(
    cmd_tx: &mpsc::Sender<HrmCommand>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let _ = cmd_tx.send(HrmCommand::Disconnect).await;
    Ok("disconnect requested".to_string())
}

async fn handle_mock(
    arg: &str,
    state: &Arc<Mutex<HrmState>>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    if arg == "off" {
        let mut s = state.lock().await;
        s.connected = false;
        s.heart_rate = 0;
        s.device_name.clear();
        s.device_address.clear();
        return Ok("mock off — state reset to disconnected".to_string());
    }

    match arg.parse::<u16>() {
        Ok(bpm) => {
            let mut s = state.lock().await;
            s.connected = true;
            s.heart_rate = bpm;
            if s.device_name.is_empty() {
                s.device_name = "Mock HRM".to_string();
                s.device_address = "00:00:00:00:00:00".to_string();
            }
            s.scanning = false;
            Ok(format!("mock: HR set to {} bpm (device: {})", bpm, s.device_name))
        }
        Err(_) => Ok("usage: mock <bpm> or mock off".to_string()),
    }
}

async fn handle_forget(
    cmd_tx: &mpsc::Sender<HrmCommand>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let _ = cmd_tx.send(HrmCommand::Forget).await;
    Ok("forget + disconnect requested".to_string())
}

async fn handle_subscribe(
    state: &Arc<Mutex<HrmState>>,
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    writer
        .write_all(b"subscribed to HR data at 1 Hz. ctrl-c to stop.\n")
        .await?;

    let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
    loop {
        interval.tick().await;

        let s = state.lock().await;
        let line = if s.connected {
            format!(
                "hr {} bpm | {} ({})\n",
                s.heart_rate, s.device_name, s.device_address
            )
        } else {
            format!(
                "hr -- bpm | disconnected (scanning: {})\n",
                s.scanning
            )
        };
        drop(s);

        if writer.write_all(line.as_bytes()).await.is_err() {
            break;
        }
    }

    Ok(())
}

const HELP_TEXT: &str = "\
commands:
  state           show current HR + device state
  sub             subscribe to 1 Hz HR stream
  scan            trigger BLE scan for HR devices
  connect <addr>  connect to device by BLE address
  disconnect      disconnect from current device
  forget          forget saved device + disconnect
  mock <bpm>      fake a connected HRM at given BPM (no hardware needed)
  mock off        stop mocking, revert to disconnected
  help            this message
  quit            disconnect

examples:
  mock 142         simulate 142 bpm heart rate
  mock off         stop simulating
  connect AA:BB:CC:DD:EE:FF
  scan
  state";
