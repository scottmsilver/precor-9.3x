mod debug_server;
mod ftms_service;
mod protocol;
mod treadmill;

use std::sync::Arc;
use tokio::sync::Mutex;

use treadmill::TreadmillState;

const DEFAULT_SOCKET: &str = "/tmp/treadmill_io.sock";
const DEFAULT_DEBUG_PORT: u16 = 8826;

#[tokio::main]
async fn main() {
    env_logger::init();

    let (socket_path, debug_port) = parse_args();
    log::info!("FTMS daemon starting, socket: {}, debug port: {}", socket_path, debug_port);

    let state = Arc::new(Mutex::new(TreadmillState::default()));

    tokio::select! {
        _ = tokio::signal::ctrl_c() => {
            log::info!("Received shutdown signal");
        }
        result = treadmill::run(state.clone(), &socket_path) => {
            if let Err(e) = result {
                log::error!("Treadmill task exited with error: {}", e);
            }
        }
        result = ftms_service::run(state.clone(), socket_path.clone()) => {
            if let Err(e) = result {
                log::error!("FTMS service task exited with error: {}", e);
            }
        }
        result = debug_server::run(state.clone(), socket_path.clone(), debug_port) => {
            if let Err(e) = result {
                log::error!("Debug server exited with error: {}", e);
            }
        }
    }

    log::info!("FTMS daemon shutting down");
}

fn parse_args() -> (String, u16) {
    let args: Vec<String> = std::env::args().collect();
    let mut socket_path = DEFAULT_SOCKET.to_string();
    let mut debug_port = DEFAULT_DEBUG_PORT;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--socket" => {
                if let Some(path) = args.get(i + 1) {
                    socket_path = path.clone();
                    i += 1;
                }
            }
            "--debug-port" => {
                if let Some(port) = args.get(i + 1) {
                    debug_port = port.parse().unwrap_or(DEFAULT_DEBUG_PORT);
                    i += 1;
                }
            }
            _ => {}
        }
        i += 1;
    }
    (socket_path, debug_port)
}
