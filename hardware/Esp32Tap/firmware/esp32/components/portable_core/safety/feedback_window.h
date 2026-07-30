/*
 * feedback_window.h — dedicated sub-ms relay-feedback sampling window.
 *
 * PLAN gap-safe transition steps (entry 5 / exit 3) require the feedback
 * pole to report the expected contact state continuously for >= 1 ms,
 * proven by an actual GPIO sample, all strictly before the 10 ms
 * deadline. The serial engine's 5 ms loop cadence cannot satisfy that:
 * after observe_interframe_gap() arms the deadline mid-iteration, the
 * next loop samples land at ~+5 ms and ~+10 ms — the second one AT the
 * fail-closed deadline, so every gap-safe entry/exit would latch a
 * feedback-timeout fault.
 *
 * Fix (task layer, model semantics untouched): while the controller is
 * in ENTRY_WAIT_FEEDBACK or EXIT_WAIT_FEEDBACK the owning task runs this
 * bounded poll window at FEEDBACK_POLL_US instead of returning to the
 * 5 ms cadence. Every iteration first applies controller outputs to the
 * hardware (RELAY_CMD must be physically driven before its feedback can
 * move), then takes a real GPIO sample. The loop exits when the
 * controller leaves the wait state — either qualified (EMULATING/PROXY)
 * or failed closed by its own 10 ms deadline in enforce_due_safety().
 *
 * Bounded by construction on target: the controller's deadline fires at
 * now >= deadline, i.e. within RELAY_FEEDBACK_DEADLINE_US of window
 * entry on a monotonic clock. If the clock were ever broken/stuck the
 * loop would spin until the 2 s task WDT panics -> reset -> relay
 * released — fail-safe, and exactly what PLAN prescribes for a stalled
 * supervised task.
 *
 * Header-only and HAL-free so the host suite can drive it with a fake
 * clock/GPIO at the REAL task cadence (see
 * host/tests/test_safety_boot_envelope.cpp "task-cadence" cases).
 */

#pragma once

#include "safety/safety_constants.h"
#include "safety/safety_controller.h"

namespace esp32tap::safety {

inline bool in_feedback_wait(const SafetyController& controller) {
    return controller.mode() == SafeMode::ENTRY_WAIT_FEEDBACK ||
           controller.mode() == SafeMode::EXIT_WAIT_FEEDBACK;
}

// NowFn: () -> int64_t monotonic microseconds.
// NcFn/NoFn: () -> bool, real GPIO reads of the K1 feedback pole.
// ApplyFn: () -> void, applies controller relay/tx outputs to hardware.
// DelayFn: () -> void, waits ~FEEDBACK_POLL_US (and on the host advances
//          the fake clock so the controller's deadline can fire).
template <typename NowFn, typename NcFn, typename NoFn, typename ApplyFn,
          typename DelayFn>
inline void run_feedback_window(SafetyController& controller, NowFn&& now_fn,
                                NcFn&& nc_fn, NoFn&& no_fn, ApplyFn&& apply_fn,
                                DelayFn&& delay_fn) {
    while (in_feedback_wait(controller)) {
        // Drive RELAY_CMD/TX_ENABLE first: the relay must be commanded
        // before its dry-contact feedback can reach the expected state.
        apply_fn();
        controller.observe_relay_feedback(nc_fn(), no_fn(), now_fn());
        if (!in_feedback_wait(controller)) break;
        delay_fn();
    }
    // Transfer finished (qualified or failed closed) — apply the final
    // output state before returning to the coarse cadence.
    apply_fn();
}

}  // namespace esp32tap::safety
