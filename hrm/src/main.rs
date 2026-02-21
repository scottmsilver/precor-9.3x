mod config;
mod debug_server;
mod scanner;
mod server;

use std::sync::Arc;
use tokio::sync::Mutex;

pub use scanner::{BleDevice, HrmState};

const DEFAULT_SOCKET: &str = "/tmp/hrm.sock";
const DEFAULT_CONFIG: &str = "hrm_config.json";
const DEFAULT_DEBUG_PORT: u16 = 8827;

#[tokio::main]
async fn main() {
    env_logger::init();

    let (socket_path, config_path, debug_port) = parse_args();
    log::info!(
        "HRM daemon starting, socket: {}, config: {}, debug port: {}",
        socket_path,
        config_path,
        debug_port
    );

    let state = Arc::new(Mutex::new(HrmState::default()));

    // Command channel: server and debug_server send commands, scanner receives them.
    let (cmd_tx, cmd_rx) = tokio::sync::mpsc::channel(16);

    tokio::select! {
        _ = tokio::signal::ctrl_c() => {
            log::info!("Received shutdown signal");
        }
        result = scanner::run(state.clone(), config_path.clone(), cmd_rx) => {
            if let Err(e) = result {
                log::error!("Scanner task exited with error: {}", e);
            }
        }
        result = server::run(state.clone(), &socket_path, cmd_tx.clone()) => {
            if let Err(e) = result {
                log::error!("Server task exited with error: {}", e);
            }
        }
        result = debug_server::run(state.clone(), config_path, debug_port, cmd_tx) => {
            if let Err(e) = result {
                log::error!("Debug server exited with error: {}", e);
            }
        }
    }

    log::info!("HRM daemon shutting down");
}

fn parse_args() -> (String, String, u16) {
    let args: Vec<String> = std::env::args().collect();
    let mut socket_path = DEFAULT_SOCKET.to_string();
    let mut config_path = DEFAULT_CONFIG.to_string();
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
            "--config" => {
                if let Some(path) = args.get(i + 1) {
                    config_path = path.clone();
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
    (socket_path, config_path, debug_port)
}
