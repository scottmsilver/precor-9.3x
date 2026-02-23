//! BLE GATT server for the FTMS (Fitness Machine Service) treadmill profile.
//!
//! Advertises as "Precor 9.31" and exposes the standard FTMS treadmill service
//! (UUID 0x1826) so fitness apps like Zwift, QZ Fitness, and Apple Watch can
//! read treadmill data and send control commands.

use std::sync::Arc;
use std::time::Duration;

use bluer::{
    adv::Advertisement,
    gatt::local::{
        characteristic_control, Application, Characteristic, CharacteristicControlEvent,
        CharacteristicNotify, CharacteristicNotifyMethod, CharacteristicRead,
        CharacteristicWrite, CharacteristicWriteMethod, Service,
    },
};
use futures::{pin_mut, FutureExt, StreamExt};
use log::{debug, error, info, warn};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::Mutex;

use crate::protocol::{
    self, CONTROL_POINT_UUID, FEATURE_UUID, FTMS_SERVICE_UUID, INCLINE_RANGE_UUID,
    MACHINE_STATUS_UUID, SPEED_RANGE_UUID, TRAINING_STATUS_UUID, TREADMILL_DATA_UUID,
};
use crate::treadmill::TreadmillState;

/// Run the FTMS BLE GATT server. Advertises and notifies at 1 Hz.
/// `socket_path` is passed through for control point commands that need to send
/// speed/incline changes back to treadmill_io.
pub async fn run(
    state: Arc<Mutex<TreadmillState>>,
    socket_path: String,
) -> bluer::Result<()> {
    let session = bluer::Session::new().await?;
    let adapter = session.default_adapter().await?;
    adapter.set_powered(true).await?;

    info!(
        "FTMS using adapter {} ({})",
        adapter.name(),
        adapter.address().await?
    );

    // --- Advertisement ---
    // FTMS spec Section 3.1: Service Data must include Flags (available) + Machine Type (treadmill)
    let ftms_service_data: Vec<u8> = vec![
        0x01, // Flags: bit 0 = Fitness Machine Available
        0x01, // Fitness Machine Type: bit 0 = Treadmill Supported
    ];
    let adv = Advertisement {
        advertisement_type: bluer::adv::Type::Peripheral,
        service_uuids: vec![FTMS_SERVICE_UUID].into_iter().collect(),
        service_data: [(FTMS_SERVICE_UUID, ftms_service_data)].into_iter().collect(),
        local_name: Some("Precor 9.31".to_string()),
        discoverable: Some(true),
        ..Default::default()
    };
    let _adv_handle = adapter.advertise(adv).await?;
    info!("Advertising as 'Precor 9.31' with FTMS service");

    // --- Treadmill Data notify (1 Hz) ---
    // Uses the Fun callback model: when a client subscribes, we spawn a task that
    // pushes data at 1 Hz until the session is stopped.
    let td_state = state.clone();
    let treadmill_data_notify_fn: Box<
        dyn Fn(bluer::gatt::local::CharacteristicNotifier) -> std::pin::Pin<Box<dyn futures::Future<Output = ()> + Send>>
            + Send
            + Sync,
    > = Box::new(move |notifier| {
        let state = td_state.clone();
        async move {
            tokio::spawn(async move {
                info!(
                    "Treadmill Data notification session started (confirming={})",
                    notifier.confirming()
                );
                let mut notifier = notifier;
                let mut interval = tokio::time::interval(Duration::from_secs(1));
                loop {
                    interval.tick().await;

                    if notifier.is_stopped() {
                        break;
                    }

                    let data = state.lock().await.encode_ftms_data();

                    debug!("Treadmill Data notify: {} bytes", data.len());
                    if let Err(err) = notifier.notify(data).await {
                        warn!("Treadmill Data notification error: {}", err);
                        break;
                    }
                }
                info!("Treadmill Data notification session ended");
            });
        }
        .boxed()
    });

    // --- Machine Status notify ---
    // We need to send status updates when control commands are processed.
    // The status notifier is shared with the control point write handler.
    let status_notifier: Arc<Mutex<Option<bluer::gatt::local::CharacteristicNotifier>>> =
        Arc::new(Mutex::new(None));

    let sn_clone = status_notifier.clone();
    let machine_status_notify_fn: Box<
        dyn Fn(bluer::gatt::local::CharacteristicNotifier) -> std::pin::Pin<Box<dyn futures::Future<Output = ()> + Send>>
            + Send
            + Sync,
    > = Box::new(move |notifier| {
        let sn = sn_clone.clone();
        async move {
            info!(
                "Machine Status notification session started (confirming={})",
                notifier.confirming()
            );
            // Send initial "Stopped by User" status on subscribe so client knows machine state
            let mut notifier = notifier;
            let _ = notifier.notify(vec![0x02, 0x01]).await;
            // Store the notifier so control_point handler can send status updates
            let mut sn_guard = sn.lock().await;
            *sn_guard = Some(notifier);
        }
        .boxed()
    });

    // --- Training Status notify ---
    // Mandatory when Control Point is exposed (FTMS spec).
    // Notifies Idle (0x01) or Manual Mode (0x0D) on start/stop.
    let training_notifier: Arc<Mutex<Option<bluer::gatt::local::CharacteristicNotifier>>> =
        Arc::new(Mutex::new(None));

    let tn_clone = training_notifier.clone();
    let training_status_notify_fn: Box<
        dyn Fn(bluer::gatt::local::CharacteristicNotifier) -> std::pin::Pin<Box<dyn futures::Future<Output = ()> + Send>>
            + Send
            + Sync,
    > = Box::new(move |notifier| {
        let tn = tn_clone.clone();
        async move {
            info!(
                "Training Status notification session started (confirming={})",
                notifier.confirming()
            );
            // Send initial "Idle" status on subscribe so client knows training state
            let mut notifier = notifier;
            let _ = notifier.notify(vec![0x00, 0x01]).await;
            let mut tn_guard = tn.lock().await;
            *tn_guard = Some(notifier);
        }
        .boxed()
    });

    // --- Control Point write handler ---
    // Uses the Fun callback model: each write parses an FTMS control command,
    // dispatches it to treadmill_io, and returns an indication response.
    let (cp_control, cp_handle) = characteristic_control();
    let cp_status_notifier = status_notifier.clone();
    let cp_training_notifier = training_notifier.clone();
    let cp_socket = socket_path.clone();

    // --- Build GATT Application ---
    let app = Application {
        services: vec![Service {
            uuid: FTMS_SERVICE_UUID,
            primary: true,
            characteristics: vec![
                // Fitness Machine Feature (0x2ACC) -- Read
                Characteristic {
                    uuid: FEATURE_UUID,
                    read: Some(CharacteristicRead {
                        read: true,
                        fun: Box::new(|_req| {
                            async move {
                                debug!("Feature characteristic read");
                                Ok(protocol::encode_feature().to_vec())
                            }
                            .boxed()
                        }),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                // Treadmill Data (0x2ACD) -- Notify at 1 Hz
                Characteristic {
                    uuid: TREADMILL_DATA_UUID,
                    notify: Some(CharacteristicNotify {
                        notify: true,
                        method: CharacteristicNotifyMethod::Fun(treadmill_data_notify_fn),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                // Supported Speed Range (0x2AD4) -- Read
                Characteristic {
                    uuid: SPEED_RANGE_UUID,
                    read: Some(CharacteristicRead {
                        read: true,
                        fun: Box::new(|_req| {
                            async move {
                                debug!("Speed range characteristic read");
                                Ok(protocol::encode_speed_range().to_vec())
                            }
                            .boxed()
                        }),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                // Supported Inclination Range (0x2AD5) -- Read
                Characteristic {
                    uuid: INCLINE_RANGE_UUID,
                    read: Some(CharacteristicRead {
                        read: true,
                        fun: Box::new(|_req| {
                            async move {
                                debug!("Incline range characteristic read");
                                Ok(protocol::encode_incline_range().to_vec())
                            }
                            .boxed()
                        }),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                // Training Status (0x2AD3) -- Read + Notify
                // Mandatory when Control Point is present (FTMS spec).
                Characteristic {
                    uuid: TRAINING_STATUS_UUID,
                    read: Some(CharacteristicRead {
                        read: true,
                        fun: Box::new(|_req| {
                            async move {
                                debug!("Training Status read");
                                // Flags=0x00 (no string), Status=0x01 (Idle)
                                Ok(vec![0x00, 0x01])
                            }
                            .boxed()
                        }),
                        ..Default::default()
                    }),
                    notify: Some(CharacteristicNotify {
                        notify: true,
                        method: CharacteristicNotifyMethod::Fun(training_status_notify_fn),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                // Fitness Machine Control Point (0x2AD9) -- Write + Indicate
                // Uses IO mode so we can process writes in our event loop and send
                // indication responses via the notify/indicate handle.
                Characteristic {
                    uuid: CONTROL_POINT_UUID,
                    write: Some(CharacteristicWrite {
                        write: true,
                        method: CharacteristicWriteMethod::Io,
                        ..Default::default()
                    }),
                    notify: Some(CharacteristicNotify {
                        indicate: true,
                        method: CharacteristicNotifyMethod::Io,
                        ..Default::default()
                    }),
                    control_handle: cp_handle,
                    ..Default::default()
                },
                // Fitness Machine Status (0x2ADA) -- Read + Notify
                Characteristic {
                    uuid: MACHINE_STATUS_UUID,
                    read: Some(CharacteristicRead {
                        read: true,
                        fun: Box::new(|_req| {
                            async move {
                                debug!("Machine Status read");
                                // Default: Stopped by User (0x02, param 0x01=stop)
                                Ok(vec![0x02, 0x01])
                            }
                            .boxed()
                        }),
                        ..Default::default()
                    }),
                    notify: Some(CharacteristicNotify {
                        notify: true,
                        method: CharacteristicNotifyMethod::Fun(machine_status_notify_fn),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
            ],
            ..Default::default()
        }],
        ..Default::default()
    };

    let _app_handle = adapter.serve_gatt_application(app).await?;
    info!("FTMS GATT service registered");

    // --- Control Point event loop ---
    // Process write requests (commands) and notify events (indication subscribers)
    // from the IO-mode control point characteristic.
    let mut cp_reader: Option<bluer::gatt::CharacteristicReader> = None;
    let mut cp_writer: Option<bluer::gatt::CharacteristicWriter> = None;
    let mut read_buf = Vec::new();

    pin_mut!(cp_control);

    info!("FTMS service running");

    loop {
        tokio::select! {
            // Handle control point IO events (new subscriber or writer)
            evt = cp_control.next() => {
                match evt {
                    Some(CharacteristicControlEvent::Write(req)) => {
                        info!(
                            "Control Point write session from {} (MTU {})",
                            req.device_address(), req.mtu()
                        );
                        read_buf = vec![0u8; req.mtu()];
                        match req.accept() {
                            Ok(reader) => cp_reader = Some(reader),
                            Err(e) => error!("Failed to accept CP write: {}", e),
                        }
                    }
                    Some(CharacteristicControlEvent::Notify(notifier)) => {
                        info!(
                            "Control Point indicate session from {} (MTU {})",
                            notifier.device_address(), notifier.mtu()
                        );
                        cp_writer = Some(notifier);
                    }
                    None => {
                        info!("Control Point control stream ended");
                        break;
                    }
                }
            }

            // Read incoming control point writes
            read_res = async {
                match &mut cp_reader {
                    Some(reader) => reader.read(&mut read_buf).await,
                    None => futures::future::pending().await,
                }
            } => {
                match read_res {
                    Ok(0) => {
                        info!("Control Point write stream ended");
                        cp_reader = None;
                    }
                    Ok(n) => {
                        let bytes = &read_buf[..n];
                        debug!("Control Point write: {} bytes {:02x?}", n, bytes);

                        // Parse and handle the FTMS control command
                        let (opcode, result) = match protocol::parse_control_point(bytes) {
                            Some(cmd) => {
                                // Send Machine Status notification for this command
                                if let Some(status_data) = encode_status_notification(&cmd) {
                                    let mut sn = cp_status_notifier.lock().await;
                                    if let Some(notifier) = sn.as_mut() {
                                        if notifier.is_stopped() {
                                            *sn = None;
                                        } else if let Err(e) = notifier.notify(status_data).await {
                                            warn!("Status notification error: {}", e);
                                            *sn = None;
                                        }
                                    }
                                }

                                // Send Training Status notification on start/stop
                                if let Some(ts_data) = encode_training_status(&cmd) {
                                    let mut tn = cp_training_notifier.lock().await;
                                    if let Some(notifier) = tn.as_mut() {
                                        if notifier.is_stopped() {
                                            *tn = None;
                                        } else if let Err(e) = notifier.notify(ts_data).await {
                                            warn!("Training Status notification error: {}", e);
                                            *tn = None;
                                        }
                                    }
                                }

                                handle_control_command(&cmd, &cp_socket).await
                            }
                            None => {
                                warn!("Unknown control point opcode: 0x{:02x}", bytes[0]);
                                (bytes[0], protocol::RESULT_NOT_SUPPORTED)
                            }
                        };

                        // Send indication response via the CharacteristicWriter.
                        // This is a datagram socket, so a single write sends the
                        // complete 3-byte response as one BLE indication.
                        let response = protocol::encode_control_response(opcode, result);
                        if let Some(writer) = cp_writer.as_mut() {
                            if let Err(e) = writer.write(&response).await {
                                warn!("Control Point indication error: {}", e);
                                cp_writer = None;
                            }
                        }
                    }
                    Err(e) => {
                        warn!("Control Point read error: {}", e);
                        cp_reader = None;
                    }
                }
            }
        }
    }

    Ok(())
}

/// Handle a parsed FTMS control point command.
/// Sends the appropriate command to treadmill_io and returns the
/// (request_opcode, result_code) for the response indication.
///
/// Shared by both the BLE GATT server and the TCP debug server —
/// same code path regardless of transport.
pub async fn handle_control_command(
    cmd: &protocol::ControlCommand,
    socket_path: &str,
) -> (u8, u8) {
    match cmd {
        protocol::ControlCommand::RequestControl => {
            info!("FTMS: client requested control");
            (0x00, protocol::RESULT_SUCCESS)
        }
        protocol::ControlCommand::SetTargetSpeed(kmh_hundredths) => {
            let mph_tenths = protocol::kmh_hundredths_to_mph_tenths(*kmh_hundredths);
            let mph = (mph_tenths as f64 / 10.0).clamp(0.0, 12.0); // Safety clamp: max 12.0 mph
            info!(
                "FTMS: set speed to {:.1} mph ({} km/h*100)",
                mph, kmh_hundredths
            );

            match crate::treadmill::send_speed(socket_path, mph).await {
                Ok(()) => (0x02, protocol::RESULT_SUCCESS),
                Err(e) => {
                    error!("FTMS: failed to send speed command: {}", e);
                    (0x02, protocol::RESULT_FAILED)
                }
            }
        }
        protocol::ControlCommand::SetTargetInclination(incline_tenths) => {
            let incline = (*incline_tenths / 10).clamp(0, 15); // Safety clamp: 0-15% (hardware allows 0-99)
            info!(
                "FTMS: set incline to {}% ({} tenths, clamped from {})",
                incline, incline_tenths, *incline_tenths / 10
            );

            match crate::treadmill::send_incline(socket_path, incline).await {
                Ok(()) => (0x03, protocol::RESULT_SUCCESS),
                Err(e) => {
                    error!("FTMS: failed to send incline command: {}", e);
                    (0x03, protocol::RESULT_FAILED)
                }
            }
        }
        protocol::ControlCommand::StartOrResume => {
            info!("FTMS: start/resume");
            match crate::treadmill::send_start(socket_path).await {
                Ok(()) => (0x07, protocol::RESULT_SUCCESS),
                Err(e) => {
                    error!("FTMS: failed to send start command: {}", e);
                    (0x07, protocol::RESULT_FAILED)
                }
            }
        }
        protocol::ControlCommand::StopOrPause(param) => {
            info!("FTMS: stop/pause (param={})", param);
            match crate::treadmill::send_stop(socket_path).await {
                Ok(()) => (0x08, protocol::RESULT_SUCCESS),
                Err(e) => {
                    error!("FTMS: failed to send stop command: {}", e);
                    (0x08, protocol::RESULT_FAILED)
                }
            }
        }
    }
}

/// Encode a Training Status notification for start/stop state changes.
///
/// Training Status format: [flags(1), status(1)]
///   Flags: 0x00 (no string present)
///   Status values (FTMS spec Table 4.25):
///     0x01 = Idle
///     0x0D = Manual Mode (Quick Start)
fn encode_training_status(cmd: &protocol::ControlCommand) -> Option<Vec<u8>> {
    match cmd {
        protocol::ControlCommand::StartOrResume => {
            Some(vec![0x00, 0x0D]) // Manual Mode (Quick Start)
        }
        protocol::ControlCommand::StopOrPause(_) => {
            Some(vec![0x00, 0x01]) // Idle
        }
        _ => None,
    }
}

/// Encode a Fitness Machine Status notification for a state/target change.
///
/// Status opcodes (FTMS spec Table 4.16):
///   0x02 = Fitness Machine Stopped/Paused by user (param: 0x01=stop, 0x02=pause)
///   0x04 = Fitness Machine Started or Resumed by the User
///   0x05 = Target Speed Changed (uint16 LE param: km/h * 100)
///   0x06 = Target Incline Changed (int16 LE param: % * 10)
fn encode_status_notification(cmd: &protocol::ControlCommand) -> Option<Vec<u8>> {
    match cmd {
        protocol::ControlCommand::SetTargetSpeed(kmh_hundredths) => {
            let mut buf = vec![0x05]; // Target Speed Changed
            buf.extend_from_slice(&kmh_hundredths.to_le_bytes());
            Some(buf)
        }
        protocol::ControlCommand::SetTargetInclination(incline_tenths) => {
            let mut buf = vec![0x06]; // Target Incline Changed
            buf.extend_from_slice(&incline_tenths.to_le_bytes());
            Some(buf)
        }
        protocol::ControlCommand::StartOrResume => {
            Some(vec![0x04]) // Started or Resumed by User
        }
        protocol::ControlCommand::StopOrPause(param) => {
            Some(vec![0x02, *param]) // Stopped or Paused
        }
        _ => None,
    }
}
