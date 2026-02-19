//! Async Unix socket client for the treadmill_io C binary.
//!
//! Connects to the Unix domain socket, sends JSON commands,
//! and receives JSON event lines. Maintains shared state with
//! current speed, incline, elapsed time, and distance.

use std::sync::Arc;
use std::time::Instant;

use log::{debug, error, info, warn};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};

/// Shared treadmill state, updated continuously by the socket reader.
#[derive(Debug, Clone, Default)]
pub struct TreadmillState {
    /// Belt speed in tenths of mph (e.g. 35 = 3.5 mph)
    pub speed_tenths_mph: u16,
    /// Incline in percent grade (0-15)
    pub incline_percent: u16,
    /// Seconds elapsed since belt first started moving
    pub elapsed_secs: u16,
    /// Cumulative distance in meters
    pub distance_meters: u32,
    /// Whether we have an active connection to treadmill_io
    pub connected: bool,
}

impl TreadmillState {
    /// Encode current state as FTMS Treadmill Data (0x2ACD) bytes.
    /// Handles mph→km/h and incline→tenths conversions in one place.
    pub fn encode_ftms_data(&self) -> Vec<u8> {
        let speed_kmh = crate::protocol::mph_tenths_to_kmh_hundredths(self.speed_tenths_mph);
        let incline_tenths = (self.incline_percent as i16) * 10;
        crate::protocol::encode_treadmill_data(speed_kmh, incline_tenths, self.distance_meters, self.elapsed_secs)
    }
}

/// Run the treadmill socket client. Connects, reads state, auto-reconnects.
/// Updates shared state continuously. Runs until cancelled.
pub async fn run(
    state: Arc<Mutex<TreadmillState>>,
    socket_path: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut backoff = Duration::from_secs(1);

    loop {
        match connect_and_run(&state, socket_path).await {
            Ok(()) => {
                // Clean exit (shouldn't happen normally)
                info!("Treadmill connection closed cleanly");
            }
            Err(e) => {
                warn!("Treadmill connection error: {}", e);
            }
        }

        // Mark disconnected
        {
            let mut s = state.lock().await;
            s.connected = false;
        }

        info!("Reconnecting to treadmill_io in {:?}...", backoff);
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(10));
    }
}

/// Connect to the socket and run the read/heartbeat loop until disconnection.
async fn connect_and_run(
    state: &Arc<Mutex<TreadmillState>>,
    socket_path: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let stream = UnixStream::connect(socket_path).await?;
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();

    // Request initial status dump
    writer
        .write_all(b"{\"cmd\":\"status\"}\n")
        .await?;

    info!("Connected to treadmill_io at {}", socket_path);

    // Reset backoff on successful connect (caller tracks backoff, but mark connected)
    {
        let mut s = state.lock().await;
        s.connected = true;
    }

    // Distance/elapsed tracking state (local to this connection session)
    let mut last_update = Instant::now();
    let mut workout_start: Option<Instant> = None;
    let mut accumulated_distance_m: f64 = 0.0;

    let mut heartbeat = interval(Duration::from_secs(1));
    // First tick fires immediately — skip it since we just sent status
    heartbeat.tick().await;

    loop {
        tokio::select! {
            line_result = lines.next_line() => {
                match line_result {
                    Ok(Some(line)) => {
                        let now = Instant::now();
                        let dt_hours = now.duration_since(last_update).as_secs_f64() / 3600.0;
                        last_update = now;

                        if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&line) {
                            let msg_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

                            match msg_type {
                                "status" => {
                                    let emu_speed = msg.get("emu_speed")
                                        .and_then(|v| v.as_u64())
                                        .unwrap_or(0) as u16;
                                    let emu_incline = msg.get("emu_incline")
                                        .and_then(|v| v.as_u64())
                                        .unwrap_or(0) as u16;

                                    // Accumulate distance based on previous speed
                                    let mut s = state.lock().await;
                                    let prev_speed_mph = s.speed_tenths_mph as f64 / 10.0;
                                    accumulated_distance_m += prev_speed_mph * dt_hours * 1609.34;

                                    // Track elapsed time
                                    if emu_speed > 0 {
                                        if workout_start.is_none() {
                                            workout_start = Some(now);
                                        }
                                    }

                                    s.speed_tenths_mph = emu_speed;
                                    s.incline_percent = emu_incline;
                                    s.distance_meters = accumulated_distance_m as u32;
                                    if let Some(start) = workout_start {
                                        s.elapsed_secs = now.duration_since(start).as_secs() as u16;
                                    }

                                    debug!(
                                        "Status: speed={:.1} mph, incline={}%",
                                        emu_speed as f64 / 10.0,
                                        emu_incline
                                    );
                                }
                                "kv" => {
                                    // KV messages from the serial bus — mostly informational.
                                    // We could parse hmph as fallback speed, but emu_speed
                                    // from status messages is authoritative.
                                    debug!("KV: {:?}", msg);
                                }
                                _ => {
                                    debug!("Unknown message type: {}", msg_type);
                                }
                            }
                        }
                    }
                    Ok(None) => {
                        // EOF — socket closed
                        info!("Socket EOF");
                        return Ok(());
                    }
                    Err(e) => {
                        return Err(e.into());
                    }
                }
            }
            _ = heartbeat.tick() => {
                if let Err(e) = writer.write_all(b"{\"cmd\":\"heartbeat\"}\n").await {
                    return Err(e.into());
                }
            }
        }
    }
}

/// Send a speed command to treadmill_io (mph float).
/// Opens a short-lived connection, sends the command, and closes.
pub async fn send_speed(
    socket_path: &str,
    mph: f64,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let cmd = format!("{{\"cmd\":\"speed\",\"value\":{:.1}}}\n", mph);
    send_oneshot(socket_path, &cmd).await
}

/// Send an incline command to treadmill_io (0-15 int).
/// Opens a short-lived connection, sends the command, and closes.
pub async fn send_incline(
    socket_path: &str,
    incline: i16,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let cmd = format!("{{\"cmd\":\"incline\",\"value\":{}}}\n", incline);
    send_oneshot(socket_path, &cmd).await
}

/// Send start (emulate mode) command.
pub async fn send_start(
    socket_path: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    send_oneshot(socket_path, "{\"cmd\":\"emulate\",\"enabled\":true}\n").await
}

/// Send stop command (speed 0, incline 0).
pub async fn send_stop(
    socket_path: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Set speed to 0 first, then incline
    send_oneshot(socket_path, "{\"cmd\":\"speed\",\"value\":0.0}\n").await?;
    send_oneshot(socket_path, "{\"cmd\":\"incline\",\"value\":0}\n").await
}

/// Open a short-lived connection, send one command line, then close.
async fn send_oneshot(
    socket_path: &str,
    cmd: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut stream = UnixStream::connect(socket_path).await.map_err(|e| {
        error!("Failed to connect to treadmill_io at {}: {}", socket_path, e);
        e
    })?;
    stream.write_all(cmd.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}
