/*
 * emulate_task_policy.h — per-iteration decision logic of the emulate
 * cycle task, extracted so the host suite can test it (fake HAL) and the
 * FreeRTOS task can execute it unchanged.
 *
 * PLAN entry step 6 (normative): after gap-safe emulate entry, "only
 * then transmit the first complete zero frame". The SafetyController
 * accepts owner command_motion during ENTRY_WAIT_* (faithful to
 * safety_model.py), so by the time the controller reports EMULATING it
 * may already hold nonzero owner motion. If the task mirrored that into
 * the cycle parameter engine in the same iteration that arms the cycle,
 * the FIRST transmitted burst (burst 0 = inc,hmph) would carry nonzero
 * motion — violating step 6. This policy defers the controller->mode
 * motion mirror until after the first post-entry burst has actually been
 * transmitted; enter_emulate_locked() zeroes the cycle engine, so that
 * first burst encodes hmph=0/inc=0 by construction. The model itself is
 * not weakened: motion acceptance during entry stays exactly as
 * safety_model.py defines it.
 */

#pragma once

namespace esp32tap {

class EmulateTaskPolicy {
public:
    struct Decision {
        // Controller finished gap-safe entry: arm the cycle engine at
        // zero (mode.request_emulate(true) + cycle.reset()).
        bool arm = false;
        // Controller left EMULATING but the cycle engine still thinks it
        // is emulating: force it back to proxy.
        bool force_proxy = false;
        // Safe to mirror owner-commanded motion into the cycle engine
        // this iteration (false until the first zero burst went out).
        bool mirror = false;
        // Tick the cycle engine (transmit a burst) this iteration.
        bool send_burst = false;
    };

    // One iteration's decisions. `controller_emulating` is
    // SafetyController::mode() == EMULATING; `mode_emulating` is the
    // cycle parameter engine's pre-arm state (ModeStateMachine).
    Decision step(bool controller_emulating, bool mode_emulating) {
        Decision d;
        if (controller_emulating && !was_emulating_) {
            d.arm = true;
            entry_zero_pending_ = true;
        } else if (!controller_emulating && was_emulating_ && mode_emulating) {
            d.force_proxy = true;
        }
        if (!controller_emulating) {
            entry_zero_pending_ = false;
        } else {
            d.send_burst = true;
            d.mirror = !entry_zero_pending_;
        }
        was_emulating_ = controller_emulating;
        return d;
    }

    // Call ONLY after the cycle engine reports a burst was actually
    // transmitted (EmulationCycle::tick() returned true). Until then the
    // entry-zero gate stays closed and the mirror stays deferred.
    void on_burst_sent() { entry_zero_pending_ = false; }

    bool entry_zero_pending() const { return entry_zero_pending_; }

private:
    bool was_emulating_ = false;
    bool entry_zero_pending_ = false;
};

}  // namespace esp32tap
