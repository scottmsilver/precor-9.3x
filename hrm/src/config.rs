//! Persistent HRM device configuration.
//!
//! Reads and writes `hrm_config.json` to remember the preferred
//! heart rate monitor between daemon restarts.

use log::{info, warn};
use serde::{Deserialize, Serialize};

/// Saved device configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HrmConfig {
    pub address: String,
    #[serde(default)]
    pub name: String,
}

/// Load config from disk. Returns None if file missing or invalid.
pub fn load(path: &str) -> Option<HrmConfig> {
    let data = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<HrmConfig>(&data) {
        Ok(cfg) => {
            info!("Loaded config: address={}, name={}", cfg.address, cfg.name);
            Some(cfg)
        }
        Err(e) => {
            warn!("Failed to parse config {}: {}", path, e);
            None
        }
    }
}

/// Save config to disk. Logs on failure but does not return error.
pub fn save(path: &str, config: &HrmConfig) {
    match serde_json::to_string_pretty(config) {
        Ok(json) => {
            if let Err(e) = std::fs::write(path, json) {
                warn!("Failed to write config {}: {}", path, e);
            } else {
                info!("Saved config: address={}, name={}", config.address, config.name);
            }
        }
        Err(e) => {
            warn!("Failed to serialize config: {}", e);
        }
    }
}

/// Delete config file. Used when user sends "forget" command.
pub fn forget(path: &str) {
    if std::fs::remove_file(path).is_ok() {
        info!("Deleted config file {}", path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_roundtrip() {
        let dir = std::env::temp_dir().join("hrm_config_test");
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("test_config.json");
        let path_str = path.to_str().unwrap();

        let cfg = HrmConfig {
            address: "AA:BB:CC:DD:EE:FF".to_string(),
            name: "Polar H10".to_string(),
        };
        save(path_str, &cfg);

        let loaded = load(path_str).expect("should load saved config");
        assert_eq!(loaded.address, "AA:BB:CC:DD:EE:FF");
        assert_eq!(loaded.name, "Polar H10");

        forget(path_str);
        assert!(load(path_str).is_none());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_load_missing() {
        assert!(load("/tmp/hrm_nonexistent_config.json").is_none());
    }

    #[test]
    fn test_load_invalid() {
        let path = "/tmp/hrm_invalid_config.json";
        std::fs::write(path, "not json").unwrap();
        assert!(load(path).is_none());
        let _ = std::fs::remove_file(path);
    }
}
