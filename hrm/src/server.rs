//! Unix socket server for the HRM daemon.
//!
//! Accepts multiple clients on a Unix domain socket. Broadcasts heart rate
//! data at 1 Hz as newline-delimited JSON. Accepts commands for device
//! management (connect, disconnect, forget, scan).

use std::sync::Arc;

use log::{debug, info, warn};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::sync::Mutex;
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

use crate::scanner::{HrmCommand, HrmState};

/// Run the Unix socket server. Listens for clients and broadcasts HR data.
pub async fn run(
    state: Arc<Mutex<HrmState>>,
    socket_path: &str,
    cmd_tx: mpsc::Sender<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Remove stale socket file
    let _ = std::fs::remove_file(socket_path);

    let listener = UnixListener::bind(socket_path)?;

    // Make socket world-accessible (server.py runs as non-root user)
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(socket_path, std::fs::Permissions::from_mode(0o777))?;

    info!("HRM server listening on {}", socket_path);

    loop {
        let (stream, _addr) = listener.accept().await?;
        info!("Client connected");

        let state = state.clone();
        let cmd_tx = cmd_tx.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_client(stream, state, cmd_tx).await {
                debug!("Client disconnected: {}", e);
            }
        });
    }
}

async fn handle_client(
    stream: tokio::net::UnixStream,
    state: Arc<Mutex<HrmState>>,
    cmd_tx: mpsc::Sender<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    let mut broadcast_interval = interval(Duration::from_secs(1));
    // Skip the first immediate tick
    broadcast_interval.tick().await;

    loop {
        tokio::select! {
            line_result = lines.next_line() => {
                match line_result {
                    Ok(Some(line)) => {
                        let line = line.trim().to_string();
                        if line.is_empty() {
                            continue;
                        }
                        if let Err(e) = handle_command(&line, &state, &cmd_tx, &mut writer).await {
                            warn!("Error handling command: {}", e);
                        }
                    }
                    Ok(None) => return Ok(()), // EOF
                    Err(e) => return Err(e.into()),
                }
            }
            _ = broadcast_interval.tick() => {
                let msg = {
                    let s = state.lock().await;
                    serde_json::json!({
                        "type": "hr",
                        "bpm": s.heart_rate,
                        "connected": s.connected,
                        "device": s.device_name,
                        "address": s.device_address,
                    })
                };
                let mut line = serde_json::to_string(&msg)?;
                line.push('\n');
                if writer.write_all(line.as_bytes()).await.is_err() {
                    return Ok(()); // Client gone
                }
            }
        }
    }
}

async fn handle_command(
    line: &str,
    state: &Arc<Mutex<HrmState>>,
    cmd_tx: &mpsc::Sender<HrmCommand>,
    writer: &mut tokio::net::unix::OwnedWriteHalf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let parsed: serde_json::Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            let err_msg = serde_json::json!({
                "type": "error",
                "message": format!("invalid JSON: {}", e),
            });
            let mut out = serde_json::to_string(&err_msg)?;
            out.push('\n');
            writer.write_all(out.as_bytes()).await?;
            return Ok(());
        }
    };

    let cmd = parsed.get("cmd").and_then(|v| v.as_str()).unwrap_or("");

    match cmd {
        "connect" => {
            let address = parsed.get("address").and_then(|v| v.as_str()).unwrap_or("");
            if address.is_empty() {
                send_error(writer, "missing 'address' field").await?;
                return Ok(());
            }
            info!("Connect command for {}", address);
            let _ = cmd_tx.send(HrmCommand::Connect(address.to_string())).await;
            send_status(state, writer).await?;
        }
        "disconnect" => {
            info!("Disconnect command");
            let _ = cmd_tx.send(HrmCommand::Disconnect).await;
            send_status(state, writer).await?;
        }
        "forget" => {
            info!("Forget command");
            let _ = cmd_tx.send(HrmCommand::Forget).await;
            send_status(state, writer).await?;
        }
        "scan" => {
            info!("Scan command");
            let _ = cmd_tx.send(HrmCommand::Scan).await;
            send_status(state, writer).await?;
        }
        "status" => {
            send_status(state, writer).await?;
        }
        _ => {
            send_error(writer, &format!("unknown command: '{}'", cmd)).await?;
        }
    }

    Ok(())
}

async fn send_status(
    state: &Arc<Mutex<HrmState>>,
    writer: &mut tokio::net::unix::OwnedWriteHalf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let s = state.lock().await;
    let msg = serde_json::json!({
        "type": "status",
        "scanning": s.scanning,
        "connected": s.connected,
        "bpm": s.heart_rate,
        "device": s.device_name,
        "address": s.device_address,
        "available_devices": s.available_devices,
    });
    drop(s);

    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    writer.write_all(line.as_bytes()).await?;
    Ok(())
}

async fn send_error(
    writer: &mut tokio::net::unix::OwnedWriteHalf,
    message: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let msg = serde_json::json!({
        "type": "error",
        "message": message,
    });
    let mut line = serde_json::to_string(&msg)?;
    line.push('\n');
    writer.write_all(line.as_bytes()).await?;
    Ok(())
}
