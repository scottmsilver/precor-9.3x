/*
 * safety_constants.h — normative safety deadlines and clamps, integer units.
 *
 * Each constant names its twin in firmware/safety_model.py (Controller)
 * and firmware/safety_manifest.schema.json (safety_contract consts).
 * Time is int64_t monotonic MICROSECONDS (PLAN D4): the float-epsilon
 * comparisons of the host model become exact integer comparisons.
 */

#pragma once

#include <cstdint>

namespace esp32tap::safety {

// safety_manifest.schema.json: console_fresh_seconds = 1.5
// safety_model.py: Controller.CONSOLE_FRESH_SECONDS
inline constexpr int64_t CONSOLE_FRESH_US = 1'500'000;

// safety_manifest.schema.json: transfer_gap_seconds = 1.0
// safety_model.py: Controller.TRANSFER_GAP_DEADLINE_SECONDS
inline constexpr int64_t TRANSFER_GAP_DEADLINE_US = 1'000'000;

// safety_manifest.schema.json: relay_feedback_seconds = 0.01
// safety_model.py: Controller.RELAY_FEEDBACK_DEADLINE_SECONDS
inline constexpr int64_t RELAY_FEEDBACK_DEADLINE_US = 10'000;

// safety_manifest.schema.json: relay_feedback_stable_seconds = 0.001
// safety_model.py: Controller.RELAY_FEEDBACK_STABLE_SECONDS
inline constexpr int64_t RELAY_FEEDBACK_STABLE_US = 1'000;

// safety_manifest.schema.json: watchdog_seconds = 2.0
// safety_model.py: Controller.WDT_SECONDS
// sdkconfig: CONFIG_ESP_TASK_WDT_TIMEOUT_S=2
inline constexpr int64_t WDT_US = 2'000'000;

// safety_manifest.schema.json: tread_ok_to_nc_max_seconds = 0.01 (bench gate)
inline constexpr int64_t TREAD_OK_TO_NC_MAX_US = 10'000;

// safety_manifest.schema.json: software_to_nc_max_seconds = 0.25 (bench gate)
inline constexpr int64_t SOFTWARE_TO_NC_MAX_US = 250'000;

// safety_manifest.schema.json: watchdog_to_nc_max_seconds = 2.25 (bench gate)
inline constexpr int64_t WDT_TO_NC_MAX_US = 2'250'000;

// safety_manifest.schema.json: normal_transition_acceptance_cycles = 1000
inline constexpr int NORMAL_TRANSITION_ACCEPTANCE_CYCLES = 1'000;

// Motion clamps (PLAN "Clamps on-MCU"):
// speed 0-120 tenths of mph (12.0 mph) — matches cpp MAX_SPEED_TENTHS
inline constexpr int SPEED_MAX_TENTHS = 120;
// incline application limit 0-30 half-pct (15%) — the remote box is no
// longer a trust boundary, so the app clamp is enforced on-MCU
inline constexpr int INCLINE_APP_MAX_HALF = 30;
// incline absolute hardware guard 0-198 half-pct (99%) — matches cpp
// MAX_INCLINE; guards the emulate cycle encoder, never user commands
inline constexpr int INCLINE_ABS_MAX_HALF = 198;

// Emulate 3-hour no-change timeout (PLAN / cpp EMU_TIMEOUT_SEC)
inline constexpr int64_t EMULATE_TIMEOUT_US = static_cast<int64_t>(3 * 3600) * 1'000'000;

// Dedicated relay-feedback sampling cadence while the controller is in
// ENTRY_WAIT_FEEDBACK / EXIT_WAIT_FEEDBACK (safety/feedback_window.h).
// The 10 ms feedback deadline with the 1 ms continuous-stable requirement
// is UNSATISFIABLE at the serial task's 5 ms loop cadence (first sample
// lands at ~+5 ms, the next at ~+10 ms — exactly the fail-closed
// deadline), so those two states get a sub-ms poll window: 200 us gives
// ~5 samples per stable-millisecond and qualification by ~+1.2 ms, well
// inside the 10 ms deadline.
inline constexpr int64_t FEEDBACK_POLL_US = 200;

// TODO(M2): console inter-frame gap qualification threshold. The gap
// detector in the serial engine calls observe_interframe_gap() only after
// this much console-RX silence. 20 ms is a placeholder consistent with the
// 100 ms burst gap; the real number is a bench capture measurement.
inline constexpr int64_t GAP_QUALIFY_US = 20'000;

}  // namespace esp32tap::safety
