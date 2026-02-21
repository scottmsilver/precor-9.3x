//! BLE scanner and heart rate monitor client.
//!
//! Scans for BLE devices advertising the Heart Rate Service (0x180D),
//! connects via GATT, subscribes to HR Measurement notifications (0x2A37),
//! and updates shared state with heart rate readings.
//!
//! Commands are received via a `tokio::sync::mpsc` channel, allowing
//! immediate responsiveness even during blocking operations like BLE
//! notification streaming and scan timeouts.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use bluer::gatt::remote::Characteristic;
use bluer::{Adapter, AdapterEvent, Address, Device};
use futures::StreamExt;
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use tokio::sync::mpsc;
use uuid::Uuid;

use crate::config;

// Bluetooth SIG base UUID: 0000XXXX-0000-1000-8000-00805f9b34fb
const fn ble_uuid(short: u16) -> Uuid {
    Uuid::from_u128(
        ((short as u128) << 96) | 0x0000_0000_0000_1000_8000_00805f9b34fb_u128,
    )
}

/// Heart Rate Service UUID.
const HR_SERVICE_UUID: Uuid = ble_uuid(0x180D);

/// Heart Rate Measurement Characteristic UUID.
const HR_MEASUREMENT_UUID: Uuid = ble_uuid(0x2A37);

/// Shared HRM state, updated by the scanner and read by server/debug_server.
#[derive(Debug, Clone, Default)]
pub struct HrmState {
    /// Current heart rate in BPM. 0 when not connected.
    pub heart_rate: u16,
    /// Whether we are connected to a device.
    pub connected: bool,
    /// Name of the connected device (empty when not connected).
    pub device_name: String,
    /// BLE address of the connected device.
    pub device_address: String,
    /// Whether we are actively scanning.
    pub scanning: bool,
    /// Devices found during the most recent scan.
    pub available_devices: Vec<BleDevice>,
}

/// A BLE device found during scanning.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BleDevice {
    pub address: String,
    pub name: String,
    pub rssi: i16,
}

/// Commands that can be sent to the scanner from the server.
#[derive(Debug, Clone)]
pub enum HrmCommand {
    Connect(String),  // address
    Disconnect,
    Forget,
    Scan,
}

/// Parse a BLE Heart Rate Measurement characteristic value.
///
/// Per the Bluetooth spec, byte 0 is flags:
///   bit 0: 0 = HR is uint8 in byte 1, 1 = HR is uint16 LE in bytes 1-2
///
/// Returns the heart rate in BPM, or None if the data is too short.
pub fn parse_hr_measurement(data: &[u8]) -> Option<u16> {
    if data.is_empty() {
        return None;
    }

    let flags = data[0];
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

/// Run the BLE scanner loop. Connects to a saved device or scans for new ones.
/// Reconnects on disconnection with exponential backoff.
///
/// Commands arrive via `cmd_rx` and are handled immediately, even during
/// active BLE connections or scan timeouts.
pub async fn run(
    state: Arc<Mutex<HrmState>>,
    config_path: String,
    mut cmd_rx: mpsc::Receiver<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let session = bluer::Session::new().await?;
    let adapter = session.default_adapter().await?;
    info!("Using BLE adapter: {}", adapter.name());

    adapter.set_powered(true).await?;

    let mut backoff = Duration::from_secs(1);
    // Holds a command that was received during a wait and needs processing
    // on the next iteration.
    let mut pending: Option<HrmCommand> = None;

    loop {
        // Use a command carried over from an interruptible wait, or drain
        // any new commands from the channel (last one wins).
        let cmd = pending.take().or_else(|| drain_last(&mut cmd_rx));

        match cmd {
            Some(HrmCommand::Disconnect) => {
                info!("Disconnect command received");
                // Will naturally fall through to scan
            }
            Some(HrmCommand::Forget) => {
                info!("Forget command received");
                config::forget(&config_path);
            }
            Some(HrmCommand::Connect(addr)) => {
                info!("Connect command for {}", addr);
                match addr.parse::<Address>() {
                    Ok(address) => {
                        match connect_and_stream(&adapter, address, &state, &config_path, &mut cmd_rx).await {
                            Ok(()) => {
                                info!("Device disconnected cleanly");
                            }
                            Err(e) => {
                                warn!("Connection error: {}", e);
                            }
                        }
                        mark_disconnected(&state).await;
                        backoff = Duration::from_secs(1);
                        continue;
                    }
                    Err(e) => {
                        warn!("Invalid address '{}': {}", addr, e);
                    }
                }
            }
            Some(HrmCommand::Scan) => {
                info!("Scan command received, skipping saved device");
                // Fall through to scan, bypassing saved-device reconnect
            }
            None => {
                // No command -- try saved device first
                if let Some(cfg) = config::load(&config_path) {
                    if let Ok(address) = cfg.address.parse::<Address>() {
                        info!("Attempting to connect to saved device: {} ({})", cfg.name, cfg.address);
                        match connect_and_stream(&adapter, address, &state, &config_path, &mut cmd_rx).await {
                            Ok(()) => {
                                info!("Saved device disconnected");
                            }
                            Err(e) => {
                                warn!("Failed to connect to saved device: {}", e);
                            }
                        }
                        mark_disconnected(&state).await;
                        backoff = Duration::from_secs(1);
                        continue;
                    }
                }
            }
        }

        // Scan for HR devices
        info!("Scanning for HR devices...");
        {
            let mut s = state.lock().await;
            s.scanning = true;
            s.available_devices.clear();
        }

        let (devices, interrupted_cmd) = scan_for_hr_devices(&adapter, Duration::from_secs(10), &mut cmd_rx).await;

        {
            let mut s = state.lock().await;
            s.scanning = false;
            s.available_devices = devices.clone();
        }

        // If a command interrupted the scan, process it next iteration
        if let Some(cmd) = interrupted_cmd {
            pending = Some(cmd);
            continue;
        }

        match devices.len() {
            0 => {
                info!("No HR devices found, retrying in {:?}", backoff);
                // Interruptible sleep: respond to commands during backoff
                tokio::select! {
                    _ = tokio::time::sleep(backoff) => {}
                    cmd = cmd_rx.recv() => {
                        if let Some(cmd) = cmd {
                            pending = Some(cmd);
                        }
                    }
                }
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
            1 => {
                // Auto-connect to sole device
                let dev = &devices[0];
                info!("Found single HR device: {} ({}), auto-connecting", dev.name, dev.address);
                if let Ok(address) = dev.address.parse::<Address>() {
                    match connect_and_stream(&adapter, address, &state, &config_path, &mut cmd_rx).await {
                        Ok(()) => {
                            info!("Device disconnected");
                        }
                        Err(e) => {
                            warn!("Connection error: {}", e);
                        }
                    }
                    mark_disconnected(&state).await;
                }
                backoff = Duration::from_secs(1);
            }
            n => {
                // Multiple devices found -- wait for user to choose via connect command
                info!("Found {} HR devices, waiting for connect command", n);
                for d in &devices {
                    info!("  {} - {} (RSSI: {})", d.address, d.name, d.rssi);
                }
                // Interruptible wait for user input before rescanning
                tokio::select! {
                    _ = tokio::time::sleep(Duration::from_secs(5)) => {}
                    cmd = cmd_rx.recv() => {
                        if let Some(cmd) = cmd {
                            pending = Some(cmd);
                        }
                    }
                }
                backoff = Duration::from_secs(1);
            }
        }
    }
}

/// Drain all pending messages from the channel, returning the last one.
fn drain_last(rx: &mut mpsc::Receiver<HrmCommand>) -> Option<HrmCommand> {
    let mut last = None;
    while let Ok(cmd) = rx.try_recv() {
        last = Some(cmd);
    }
    last
}

/// Scan for BLE devices advertising the Heart Rate Service.
/// Aborts early if a command arrives on cmd_rx, returning the interrupting
/// command so the caller can process it.
async fn scan_for_hr_devices(
    adapter: &Adapter,
    timeout: Duration,
    cmd_rx: &mut mpsc::Receiver<HrmCommand>,
) -> (Vec<BleDevice>, Option<HrmCommand>) {
    let mut found: HashMap<Address, BleDevice> = HashMap::new();
    let mut interrupted_cmd = None;

    let discover = match adapter.discover_devices().await {
        Ok(stream) => stream,
        Err(e) => {
            error!("Failed to start discovery: {}", e);
            return (Vec::new(), None);
        }
    };

    let mut discover = Box::pin(discover);

    let deadline = tokio::time::sleep(timeout);
    tokio::pin!(deadline);

    loop {
        tokio::select! {
            _ = &mut deadline => {
                debug!("Scan timeout reached");
                break;
            }
            cmd = cmd_rx.recv() => {
                if let Some(cmd) = cmd {
                    info!("Command received during scan, aborting scan early");
                    interrupted_cmd = Some(cmd);
                    break;
                } else {
                    break; // channel closed
                }
            }
            event = discover.next() => {
                match event {
                    Some(AdapterEvent::DeviceAdded(addr)) => {
                        if let Ok(device) = adapter.device(addr) {
                            if is_hr_device(&device).await {
                                let name = device.name().await.ok().flatten()
                                    .unwrap_or_else(|| "Unknown".to_string());
                                let rssi = device.rssi().await.ok().flatten().unwrap_or(0);
                                info!("Found HR device: {} ({}) RSSI={}", name, addr, rssi);
                                found.insert(addr, BleDevice {
                                    address: addr.to_string(),
                                    name,
                                    rssi,
                                });
                            }
                        }
                    }
                    Some(_) => {}
                    None => break,
                }
            }
        }
    }

    // Discovery stream drop handles cleanup (no need for set_discovery_filter)

    let mut devices: Vec<BleDevice> = found.into_values().collect();
    devices.sort_by(|a, b| b.rssi.cmp(&a.rssi)); // strongest signal first
    (devices, interrupted_cmd)
}

/// Check if a device advertises the Heart Rate Service.
async fn is_hr_device(device: &Device) -> bool {
    if let Ok(Some(uuids)) = device.uuids().await {
        return uuids.contains(&HR_SERVICE_UUID);
    }
    false
}

/// Connect to a device, find the HR characteristic, and stream notifications.
/// Uses `tokio::select!` to respond to commands immediately, even while
/// waiting for BLE notifications.
async fn connect_and_stream(
    adapter: &Adapter,
    address: Address,
    state: &Arc<Mutex<HrmState>>,
    config_path: &str,
    cmd_rx: &mut mpsc::Receiver<HrmCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let device = adapter.device(address)?;

    if !device.is_connected().await? {
        info!("Connecting to {}...", address);
        device.connect().await?;
    }

    let name = device.name().await.ok().flatten()
        .unwrap_or_else(|| "Unknown".to_string());
    info!("Connected to {} ({})", name, address);

    // Save to config
    config::save(config_path, &config::HrmConfig {
        address: address.to_string(),
        name: name.clone(),
    });

    // Update state
    {
        let mut s = state.lock().await;
        s.connected = true;
        s.device_name = name.clone();
        s.device_address = address.to_string();
        s.scanning = false;
    }

    // Find HR Measurement characteristic
    let hr_char = find_hr_characteristic(&device).await?;
    info!("Found HR Measurement characteristic, subscribing to notifications");

    let notify_stream = hr_char.notify().await?;

    let mut notify_stream = Box::pin(notify_stream);

    loop {
        tokio::select! {
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(HrmCommand::Disconnect) | Some(HrmCommand::Forget) => {
                        info!("Disconnecting from {} per command", address);
                        let _ = device.disconnect().await;
                        if matches!(cmd, Some(HrmCommand::Forget)) {
                            config::forget(config_path);
                        }
                        return Ok(());
                    }
                    Some(HrmCommand::Connect(addr)) => {
                        info!("Connect to different device requested ({}), disconnecting from {}", addr, address);
                        let _ = device.disconnect().await;
                        return Ok(());
                    }
                    Some(HrmCommand::Scan) => {
                        info!("Scan requested, disconnecting from {}", address);
                        let _ = device.disconnect().await;
                        return Ok(());
                    }
                    None => {
                        // Channel closed
                        let _ = device.disconnect().await;
                        return Ok(());
                    }
                }
            }
            notification = notify_stream.next() => {
                match notification {
                    Some(data) => {
                        if let Some(hr) = parse_hr_measurement(&data) {
                            debug!("HR: {} bpm", hr);
                            let mut s = state.lock().await;
                            s.heart_rate = hr;
                        } else {
                            warn!("Failed to parse HR measurement: {:?}", data);
                        }
                    }
                    None => {
                        info!("Notification stream ended");
                        break;
                    }
                }
            }
        }
    }

    let _ = device.disconnect().await;
    Ok(())
}

/// Walk the GATT service tree to find the HR Measurement characteristic.
async fn find_hr_characteristic(
    device: &Device,
) -> Result<Characteristic, Box<dyn std::error::Error + Send + Sync>> {
    // Wait briefly for services to be resolved
    for _ in 0..20 {
        if device.is_services_resolved().await? {
            break;
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }

    for service in device.services().await? {
        let uuid = service.uuid().await?;
        if uuid == HR_SERVICE_UUID {
            for chr in service.characteristics().await? {
                let chr_uuid = chr.uuid().await?;
                if chr_uuid == HR_MEASUREMENT_UUID {
                    return Ok(chr);
                }
            }
        }
    }

    Err("HR Measurement characteristic not found".into())
}

/// Mark state as disconnected and clear HR.
async fn mark_disconnected(state: &Arc<Mutex<HrmState>>) {
    let mut s = state.lock().await;
    s.connected = false;
    s.heart_rate = 0;
    s.device_name.clear();
    s.device_address.clear();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_hr_uint8() {
        // flags=0x00 (uint8 format), HR=72
        let data = [0x00, 72];
        assert_eq!(parse_hr_measurement(&data), Some(72));
    }

    #[test]
    fn test_parse_hr_uint16() {
        // flags=0x01 (uint16 format), HR=300 (0x012C LE = [0x2C, 0x01])
        let data = [0x01, 0x2C, 0x01];
        assert_eq!(parse_hr_measurement(&data), Some(300));
    }

    #[test]
    fn test_parse_hr_uint8_with_extra_flags() {
        // flags=0x06 (bit0=0 so uint8, other bits set for energy/rr), HR=155
        let data = [0x06, 155, 0x00, 0x00];
        assert_eq!(parse_hr_measurement(&data), Some(155));
    }

    #[test]
    fn test_parse_hr_uint16_with_extra_flags() {
        // flags=0x11 (bit0=1 so uint16, bit4=rr), HR=256 (0x0100 LE = [0x00, 0x01])
        let data = [0x11, 0x00, 0x01, 0x00, 0x00];
        assert_eq!(parse_hr_measurement(&data), Some(256));
    }

    #[test]
    fn test_parse_hr_empty() {
        assert_eq!(parse_hr_measurement(&[]), None);
    }

    #[test]
    fn test_parse_hr_uint8_too_short() {
        // Only flags byte, no HR value
        assert_eq!(parse_hr_measurement(&[0x00]), None);
    }

    #[test]
    fn test_parse_hr_uint16_too_short() {
        // flags=0x01 (uint16) but only one data byte
        assert_eq!(parse_hr_measurement(&[0x01, 0x48]), None);
    }

    #[test]
    fn test_parse_hr_zero() {
        let data = [0x00, 0];
        assert_eq!(parse_hr_measurement(&data), Some(0));
    }

    #[test]
    fn test_parse_hr_max_uint8() {
        let data = [0x00, 255];
        assert_eq!(parse_hr_measurement(&data), Some(255));
    }

    #[test]
    fn test_parse_hr_max_uint16() {
        let data = [0x01, 0xFF, 0xFF];
        assert_eq!(parse_hr_measurement(&data), Some(65535));
    }

    #[test]
    fn test_parse_hr_typical_workout() {
        // Simulating typical HR values during a run
        for bpm in [60u8, 90, 120, 150, 180, 200] {
            let data = [0x00, bpm];
            assert_eq!(parse_hr_measurement(&data), Some(bpm as u16));
        }
    }

    #[test]
    fn test_drain_last_empty() {
        let (_tx, mut rx) = mpsc::channel::<HrmCommand>(8);
        assert!(drain_last(&mut rx).is_none());
    }

    #[test]
    fn test_drain_last_returns_last() {
        let (tx, mut rx) = mpsc::channel::<HrmCommand>(8);
        tx.try_send(HrmCommand::Disconnect).unwrap();
        tx.try_send(HrmCommand::Scan).unwrap();
        let last = drain_last(&mut rx);
        assert!(matches!(last, Some(HrmCommand::Scan)));
        // Channel should be empty now
        assert!(drain_last(&mut rx).is_none());
    }
}
