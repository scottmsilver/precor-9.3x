#![allow(unused)]
//! FTMS BLE Integration Tests
//!
//! Requires:
//! - Two BLE adapters (hci0 for server, hci1 for client)
//! - ftms-daemon running on hci0
//! - treadmill_io running
//!
//! Run: cargo test --test integration -- --ignored --test-threads=1

use bluer::{Adapter, AdapterEvent, Device};
use futures::StreamExt;
use std::time::Duration;
use tokio::time::timeout;

const FTMS_SERVICE_UUID: uuid::Uuid =
    uuid::Uuid::from_u128(0x00001826_0000_1000_8000_00805f9b34fb_u128);
const FEATURE_UUID: uuid::Uuid =
    uuid::Uuid::from_u128(0x00002ACC_0000_1000_8000_00805f9b34fb_u128);
const SPEED_RANGE_UUID: uuid::Uuid =
    uuid::Uuid::from_u128(0x00002AD4_0000_1000_8000_00805f9b34fb_u128);
const INCLINE_RANGE_UUID: uuid::Uuid =
    uuid::Uuid::from_u128(0x00002AD5_0000_1000_8000_00805f9b34fb_u128);

const SCAN_TIMEOUT: Duration = Duration::from_secs(10);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

/// Helper: get hci1 adapter for client-side scanning
async fn get_test_adapter() -> bluer::Result<Adapter> {
    let session = bluer::Session::new().await?;
    session.adapter("hci1")
}

/// Helper: scan for "Precor 9.31" device and connect
async fn find_and_connect(adapter: &Adapter) -> bluer::Result<Device> {
    adapter.set_powered(true).await?;

    let filter = bluer::DiscoveryFilter {
        uuids: std::collections::HashSet::from([FTMS_SERVICE_UUID]),
        ..Default::default()
    };
    adapter.set_discovery_filter(filter).await?;

    let mut events = adapter.discover_devices().await?;

    let device = timeout(SCAN_TIMEOUT, async {
        while let Some(event) = events.next().await {
            if let AdapterEvent::DeviceAdded(addr) = event {
                let device = adapter.device(addr)?;
                if let Ok(Some(name)) = device.name().await {
                    if name == "Precor 9.31" {
                        return Ok::<_, bluer::Error>(device);
                    }
                }
            }
        }
        Err(bluer::Error::from(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "Precor 9.31 not found during scan",
        )))
    })
    .await
    .map_err(|_| {
        bluer::Error::from(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "BLE scan timed out",
        ))
    })??;

    timeout(CONNECT_TIMEOUT, device.connect())
        .await
        .map_err(|_| {
            bluer::Error::from(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "BLE connect timed out",
            ))
        })??;

    Ok(device)
}

/// Helper: find a characteristic by UUID on a connected device
async fn find_char(
    device: &Device,
    service_uuid: uuid::Uuid,
    char_uuid: uuid::Uuid,
) -> bluer::Result<bluer::gatt::remote::Characteristic> {
    let services = device.services().await?;
    for svc in &services {
        if svc.uuid().await? == service_uuid {
            let chars = svc.characteristics().await?;
            for ch in &chars {
                if ch.uuid().await? == char_uuid {
                    return Ok(ch.clone());
                }
            }
        }
    }
    Err(bluer::Error::from(std::io::Error::new(
        std::io::ErrorKind::NotFound,
        format!("Characteristic {:?} not found", char_uuid),
    )))
}

#[tokio::test]
#[ignore]
async fn test_discovery() {
    let adapter = get_test_adapter().await.expect("Need hci1 adapter");

    adapter.set_powered(true).await.expect("Power on hci1");
    let filter = bluer::DiscoveryFilter {
        uuids: std::collections::HashSet::from([FTMS_SERVICE_UUID]),
        ..Default::default()
    };
    adapter
        .set_discovery_filter(filter)
        .await
        .expect("Set filter");

    let mut events = adapter.discover_devices().await.expect("Start discovery");

    let found = timeout(SCAN_TIMEOUT, async {
        while let Some(event) = events.next().await {
            if let AdapterEvent::DeviceAdded(addr) = event {
                let device = adapter.device(addr).expect("Get device");
                if let Ok(Some(name)) = device.name().await {
                    if name == "Precor 9.31" {
                        return true;
                    }
                }
            }
        }
        false
    })
    .await
    .unwrap_or(false);

    assert!(found, "Should find 'Precor 9.31' advertising FTMS");
}

#[tokio::test]
#[ignore]
async fn test_read_feature() {
    let adapter = get_test_adapter().await.expect("Need hci1 adapter");
    let device = find_and_connect(&adapter)
        .await
        .expect("Should find and connect to Precor 9.31");

    let ch = find_char(&device, FTMS_SERVICE_UUID, FEATURE_UUID)
        .await
        .expect("Should have Feature characteristic");

    let data = ch.read().await.expect("Should read Feature");
    assert_eq!(data.len(), 8, "Feature should be 8 bytes");

    device.disconnect().await.ok();
}

#[tokio::test]
#[ignore]
async fn test_read_speed_range() {
    let adapter = get_test_adapter().await.expect("Need hci1 adapter");
    let device = find_and_connect(&adapter)
        .await
        .expect("Should find and connect to Precor 9.31");

    let ch = find_char(&device, FTMS_SERVICE_UUID, SPEED_RANGE_UUID)
        .await
        .expect("Should have Speed Range characteristic");

    let data = ch.read().await.expect("Should read Speed Range");
    assert_eq!(data.len(), 6, "Speed Range should be 6 bytes");

    let min = u16::from_le_bytes([data[0], data[1]]);
    let max = u16::from_le_bytes([data[2], data[3]]);
    let step = u16::from_le_bytes([data[4], data[5]]);
    assert_eq!(min, 80);
    assert_eq!(max, 1931);
    assert_eq!(step, 16);

    device.disconnect().await.ok();
}

#[tokio::test]
#[ignore]
async fn test_read_incline_range() {
    let adapter = get_test_adapter().await.expect("Need hci1 adapter");
    let device = find_and_connect(&adapter)
        .await
        .expect("Should find and connect to Precor 9.31");

    let ch = find_char(&device, FTMS_SERVICE_UUID, INCLINE_RANGE_UUID)
        .await
        .expect("Should have Incline Range characteristic");

    let data = ch.read().await.expect("Should read Incline Range");
    assert_eq!(data.len(), 6, "Incline Range should be 6 bytes");

    let min = i16::from_le_bytes([data[0], data[1]]);
    let max = i16::from_le_bytes([data[2], data[3]]);
    let step = u16::from_le_bytes([data[4], data[5]]);
    assert_eq!(min, 0);
    assert_eq!(max, 150);
    assert_eq!(step, 10);

    device.disconnect().await.ok();
}

#[tokio::test]
#[ignore]
async fn test_treadmill_data_notifications() {
    // Subscribe to 0x2ACD, receive >=3 notifications, verify format
    todo!("Subscribe to notifications and verify binary format");
}

#[tokio::test]
#[ignore]
async fn test_control_point_request_control() {
    // Write 0x00 to Control Point, verify success indication
    todo!("Write request control and verify response");
}

#[tokio::test]
#[ignore]
async fn test_control_point_set_speed() {
    // Write Set Target Speed, verify indication
    todo!("Write speed command and verify response");
}
