# Esp32Tap Raspberry Pi Control-Parity Design

Date: 2026-07-30
Status: Owner-approved behavioral contract; implementation not started
Tracking: `precor-9_3x-1gd`, `precor-9_3x-3yk`, `precor-9_3x-d03`

## Decision

The first Esp32Tap safety correction will restore the Raspberry Pi system's
operator-visible control behavior. It will not introduce a new client
deadman, ownership policy, or safety goal.

The ESP32 may keep protections required by its different hardware: fail-safe
relay drive, relay-contact feedback, `TREAD_OK`, UART transfer sequencing,
bounded emulation time, and the hardware/task watchdog. Those protections
must not turn an ordinary, accepted treadmill command into a four-second
emergency stop.

This document is a versioned baseline, not an irreversible policy. A later
change may deliberately add a different deadman or recovery policy, but it
must be proposed as a new design, identify the behavior being changed, and
replace the relevant acceptance tests. This document should remain in Git as
the record of what was approved at this stage.

## Goal

Match these Raspberry Pi behaviors:

1. A manual speed remains commanded until another operator action or a real
   safety condition changes it.
2. A physical-console takeover returns control to the console and pauses an
   active program until an explicit program resume or start.
3. A fresh Start or positive-speed command is an explicit recovery request.
   It may clear a fault only after the device observes a healthy bypass state.
4. Stop and Pause command zero immediately. Pause keeps the paused program's
   ownership; Stop completes a guarded exit and releases ownership.
5. An API or BLE success response means the requested control operation was
   accepted. A rejected or blocked operation is reported as such.

## Reference Behavior

The Raspberry Pi behavior is established from executable code rather than
documentation:

- `python/server.py::_apply_speed` stores and sends the requested speed without
  a four-second command lifetime.
- `python/server.py::_apply_stop` unconditionally commands zero speed and
  incline.
- `python/server.py::_apply_pause_toggle` commands zero when pausing.
- `python/server.py::_handle_auto_proxy` marks the running program and session
  paused when the physical console takes over.
- `python/server.py` starts `TreadmillClient`'s heartbeat for the life of the
  server process.
- `python/treadmill_client.py` sends that heartbeat to the local
  `treadmill_io` daemon.
- `cpp/treadmill_io.h` uses the heartbeat to detect loss of the controlling
  process, not to expire each operator speed command.

The old heartbeat therefore means "the local controller process is alive."
It does not mean "the phone must resend its speed every four seconds."

The standalone ESP32 has no separate Python process and `treadmill_io` daemon.
Its equivalent process-liveness protection is the ESP hardware/task watchdog:
if the firmware or critical tasks stop running, hardware releases the relay.
HTTP requests and Android WebSocket connections are not substitutes for that
internal liveness signal.

## Behavioral Contract

### Manual motion

- A speed or incline accepted from the HTTP, coach, or BLE control surface
  remains the commanded value.
- Elapsed wall time alone does not revoke the manual owner or zero motion.
- A later speed/incline command updates the same ownership session without
  cycling the relay.
- Pause commands zero while retaining the paused program's executor ownership
  and emulation session. Resume therefore does not needlessly relay-cycle.
- Stop (including a zero-speed action that means user Stop) commands zero,
  completes the guarded normal-exit sequence, and releases ownership.
- Physical-console takeover, `TREAD_OK` loss, watchdog failure, invalid relay
  feedback, bounded-emulation timeout, and a real owner disconnect where the
  transport supplies one still end control.
- Loss of a transient REST connection after its response is not an owner
  disconnect. REST is stateless.

### Heartbeat

- The existing four-second `LeaseExpiry::Manual` behavior is removed from
  ordinary application commands.
- The five-second QEMU/UART heartbeat remains diagnostic only.
- The ESP task watchdog remains the safety mechanism for failure of the
  standalone controller itself.
- No Android or WebSocket heartbeat is required to keep a normal manual speed
  alive.
- A future network-client deadman is explicitly out of scope and requires a
  separate owner-approved design.

### Physical-console takeover

When a valid physical-console change is detected while Esp32Tap is emulating:

1. The safety controller immediately disables motor TX, releases the relay,
   zeros its command, and drops the current lease.
2. It sets an executor takeover inhibit before releasing the safety lock.
3. The program/session layer consumes the takeover event and marks an active
   program paused without trying to command the belt.
4. Program ticks, interval boundaries, and delayed executor work cannot
   reacquire control while the inhibit is set.
5. WebSocket/program state reports the paused state, and the protected audit
   log records the takeover reason.
6. An explicit program Resume or Start may clear the executor inhibit and
   request control again through the normal safety-entry sequence.

An HTTP/coach/BLE manual command is a separate explicit operator action. It may
request manual control while the program remains paused; it must not silently
resume the scheduled program.

The required Android-facing wire result is the existing program payload with
`paused: true`; no new program JSON field is introduced in this slice. The
existing protected audit event `emergency:console_takeover` carries the
machine-readable reason. Adding the Pi's transient encouragement string
(`Console took over — paused`) requires the separately omitted encouragement
surface and is not required to close the reacquisition defect.

### Fault recovery

A latched fault continues to inhibit automatic re-entry. It is not cleared by
time, a background tick, a retry loop, or an interval boundary.

A fresh program Start, program Resume, or positive-speed command is a recovery
request. The request may clear the latch only when all of these are true:

- commanded motor TX is disabled;
- the relay command is released;
- relay feedback has continuously qualified as Bypass for the normal feedback
  qualification interval;
- `TREAD_OK` is healthy;
- no transfer is in flight; and
- the normal console-freshness and UART-idle entry checks can still be applied.

If the hardware remains unhealthy, the latch remains set, the relay remains
released, and the request returns a rejection. If it is healthy, the latch is
cleared and that same explicit request proceeds through the normal
zero-frame/gap/feedback entry sequence. Recovery does not bypass entry checks
or energize the relay directly.

This makes a fault a user-acknowledged, health-gated stop rather than a
power-cycle-only brick. A persistent `BOTH_CLOSED`, `BOTH_OPEN`, stale-console,
`TREAD_OK` failure, or transfer failure remains fail-safe.

`/api/reset` parity is not part of this control slice. When implemented, Reset
must finish stopped in Proxy; it must not feed itself into
`request_emulate`. Its fault-clear policy can reuse the same health-gated
acknowledgement, but Reset never energizes the relay.

### Transactional program Start and Resume

Program state must not claim success before control succeeds:

1. Start/Resume prepares the first/current motion plan without marking the
   program running or unpaused.
2. The guarded control path attempts takeover-inhibit release, fault recovery,
   lease acquisition, and normal entry as one logical operation.
3. Only an accepted operation commits the program to running/unpaused with the
   current timestamp.
4. If any immediate step fails, Start leaves the program loaded but stopped;
   Resume leaves it paused; the takeover inhibit and fault latch retain their
   prior state; and no background executor retry is scheduled.
5. If the asynchronous entry sequence later fails, loss of the executor lease
   sets the inhibit and pauses the program before it can advance or reacquire.

This requires prepare/commit behavior in `ProgramState` (or an equivalent
bounded transaction), rather than mutating state with `start`/`toggle_pause`
and hoping to undo it after a controller rejection.

### Command outcomes

- Success means the command was accepted and either emulation is already
  active or the guarded entry sequence was successfully initiated.
- A latched/unhealthy state, ownership conflict, invalid range, or failed
  entry request returns a non-success result.
- A rejected command must not leave a nonzero advertised command in Proxy.
- Status must continue to expose mode, commanded speed/incline, relay state,
  and fault state so clients can reconcile with the device.

## State and Concurrency Design

The immediate relay release stays inside the safety-controller path. Pausing a
program must not be implemented by taking the program mutex from
`serial_engine` while it holds the guarded safety mutex: program endpoints
currently take those locks in the opposite order, so that construction could
deadlock.

Instead, add a bounded, allocation-free takeover signal plus an executor
inhibit:

- The inhibit lives with guarded control state and is set atomically with the
  console-takeover emergency stop.
- The signal is a monotonic generation or sticky flag, not a lossy queue.
- The interval executor checks and consumes it before advancing or applying a
  program plan, then pauses `ProgramState` under the program lock.
- Every executor lease acquisition checks the inhibit. This closes the window
  between immediate relay release and program-state synchronization.
- Program Resume/Start clears the inhibit only if its transactional control
  attempt is accepted. A failed attempt leaves the inhibit set. Pause
  synchronization is idempotent, so repeated observations cannot resume or
  otherwise advance a program.

Manual control and executor control retain distinct ownership identities.
This design does not make an HTTP request impersonate `Transport::Executor`
merely to obtain a no-deadline lease.

## Required Implementation Changes

### 1. Safety lease policy

In `safety_core/src/safety/controller.rs` and the normative/reference models:

- decouple lease lifetime from `Transport`;
- make normal HTTP/coach/BLE manual ownership persistent;
- remove `MANUAL_LEASE_US` expiry from ordinary command processing;
- preserve explicit disconnect, Stop, emergency, and maximum-emulation exit
  paths;
- retain heartbeat parsing only where it represents a real liveness source,
  rather than pretending the firmware has a caller that does not exist.

Update the Python safety model, C++ reference, Rust controller, vectors, and
differential tests together so the comparison suite verifies the approved
contract instead of preserving the obsolete four-second rule.

### 2. Health-gated recovery operation

Add one controller-owned recovery operation. It must:

- verify qualified Bypass and all safe-output preconditions;
- clear the latch only for the current explicit recovery request;
- record recovery accepted/rejected and the reason in the audit log;
- leave the device in Proxy with zero motion on failure; and
- feed the successful request into ordinary `request_emulate`, never directly
  manipulate relay/TX outputs.

Call it from positive manual speed and program Start/Resume. BLE and coach
commands must use the same control function as HTTP.

### 3. Console-takeover propagation

In `esp32tap/src/tasks/serial_engine.rs`, guarded context, the interval
executor, and program/session publication:

- set the executor inhibit during `console_takeover`;
- pause the active `ProgramState` before its next tick or interval plan;
- suppress every executor acquisition while inhibited;
- preserve the current program position and session elapsed state;
- publish the existing program payload with `paused: true` to WebSocket
  clients; and
- clear the inhibit only when an explicit program Resume/Start transaction is
  accepted.

### 4. Truthful command boundary

In `esp32tap/src/control.rs`, HTTP API handlers, coach, and BLE mappings:

- return success only when motion was accepted and entry was active or
  initiated;
- roll back or zero a command whose entry request is rejected;
- map ownership conflicts and safety rejection to the existing error-capable
  API/FTMS results; and
- keep all surfaces on the same controller path.

### 5. Stop and Pause choreography

Keep the two actions distinct:

- Pause atomically marks the program paused and commands zero through the
  existing executor lease. It keeps the relay in Emulate and retains the
  executor lease.
- Resume uses the transactional Start/Resume operation above.
- Stop atomically marks the program stopped, commands a complete zero frame,
  performs the gap/feedback-qualified normal exit, and releases the lease.
- Manual zero-speed Stop follows the same zero/normal-exit/release behavior.
- A failed Pause must not report `paused: true` while nonzero motion remains.

### 6. Documentation and release gates

Update `PLAN.md`, the Rust README, route inventory, and audit follow-up so they
no longer describe the four-second manual lease as required behavior.
Promote `test_reviewer_attacks.py` into the normal release gate once green.

## Verification

### Host contract tests

Add tests proving:

- one manual speed command remains active beyond four and ten seconds;
- repeated commands reuse ownership and do not cycle the relay;
- Pause commands zero while retaining executor ownership, and Stop commands
  zero then completes normal exit and releases ownership;
- a console takeover sets the executor inhibit and no executor acquisition can
  clear it;
- accepted Start/Resume clears that inhibit through the guarded path;
- rejected Start/Resume leaves the program stopped/paused and the inhibit set;
- asynchronous entry failure pauses the program and restores the inhibit;
- a fresh Start/speed clears a latch only after qualified healthy Bypass;
- the same request is rejected while feedback or `TREAD_OK` remains unhealthy;
- a rejected request leaves zero advertised motion in Proxy;
- Pause retains executor ownership at zero, while Stop performs normal exit
  and releases it;
- task-watchdog and maximum-emulation exits still release the relay; and
- Rust, Python-model, and C++-reference sequences remain equivalent after the
  contract update.

### QEMU adversarial tests

Make the existing red tests pass without weakening their assertions:

- `test_a_manual_speed_command_survives_ten_seconds`;
- `test_d_console_takeover_is_not_undone_by_the_running_program`; and
- `test_e_a_latched_fault_is_recoverable`.

Extend them to cover:

- multiple interval boundaries after takeover;
- concurrent/delayed executor work;
- unhealthy feedback refusing Start/speed recovery;
- healthy feedback allowing the same explicit request;
- truthful HTTP status/body and `/api/status`; and
- BLE and coach paths using the same semantics.

Run the complete host, differential, QEMU, memory, and log-contract gates, not
only the three regression tests.

### Cycle-time and device-load gate

Safety parity is not sufficient if the port only works by exhausting the
ESP32-S3. Add a flashable, validation-only `perf-audit` build that measures
the real device under representative simultaneous load: console and motor
traffic, HTTPS/WebSocket traffic, a running interval program, session
recording, and BLE when the radio is available.

Instrumentation must be bounded and cheap:

- compile every probe out unless `perf-audit` is enabled;
- use fixed-size counters/atomics and no allocation in a control-loop sample;
- never log per iteration;
- aggregate steady-state loop work, loop-start service gaps, intentional relay
  transfer-window duration, stack high-water marks, heap
  free/minimum/largest-block values, task-wide CPU share, and per-core idle
  share;
- emit one snapshot at an explicitly requested point or a slow validation-only
  interval; and
- measure and report the instrumentation build's own overhead.

QEMU may verify probe plumbing and counter behavior, but its wall-clock and CPU
figures are not hardware evidence. The serial task's 5 ms constant is its
post-work sleep, not a whole-iteration deadline: a valid relay transfer can
intentionally occupy the controller's 10 ms qualification window. Report
steady-state work, service gap, and transfer duration separately. On the board,
run the representative load for ten minutes after a two-minute warm-up. The
physical relay feedback must qualify within 10 ms on every transfer. The WDT
and relay-release limits remain unchanged.

For the first board, record a baseline before tuning. Optimize and remeasure
when any of these triggers is observed:

- a periodic task's non-transfer work spends more than half of its configured
  post-work delay in its measured p99 iteration;
- per-core idle headroom is below 20% (below 10% is a release failure);
- a task has less than 1 KiB or 20% of its configured stack remaining;
- the median free heap of the final three same-phase samples is more than 2 KiB
  below the first three post-warm-up samples, or the largest free block is less
  than 64 KiB (the documented 40–50 KiB TLS-session estimate plus margin); or
- enabling the probe raises the externally measured median or p99 serial-frame
  interval by more than 5% over at least 1,000 frames.

Optimization may remove work, move bounded non-safety work out of a critical
loop, reduce copies, or right-size a proven-overallocated stack. It must not
lower task-WDT coverage, relax transfer timing, weaken tests, or change the
approved control behavior.

Development cycle time is a separate concern. Each implementation task runs
its narrow host/model/QEMU red-green gate and records elapsed time. Reuse an
unchanged QEMU image across test-only runs. Run the normal full sweep once
after integration and the deep sweep once at the final release gate; do not
pay those costs after every local edit unless a focused failure requires it.

### Hardware gate

Before treadmill contact, verify on the bench:

1. Set a manual speed once and observe stable motor frames for materially more
   than ten seconds without any phone heartbeat.
2. Press a physical-console control during the middle of a long interval and
   confirm relay release, motor-TX silence, paused program state, and no
   reacquisition across interval boundaries.
3. Resume explicitly and confirm the complete normal entry sequence.
4. Induce a recoverable fault, restore healthy Bypass, then confirm one fresh
   Start/speed request recovers through the normal entry sequence.
5. Hold invalid feedback or `TREAD_OK` unhealthy and confirm the same request
   is rejected with the relay released.
6. Verify Pause holds the program owner in Emulate at zero; Stop performs the
   guarded exit and releases control; controller-task watchdog and
   maximum-emulation timeout release control.
7. Capture relay command, NC/NO feedback, `TREAD_OK`, console UART, and motor
   UART timing so the result is based on physical signals rather than API
   state alone.
8. Run the validation-only load test above and retain its timing, CPU, stack,
   and heap snapshot together with the signal capture.

The repeatable load is a 60-second two-interval fixture looping between
2.0 mph/0% and 4.0 mph/5%, one WebSocket subscriber, two TLS clients polling
`/api/status` at 2 Hz total, the normal one-second session-task tick with
30-second flash checkpoints, one connected FTMS control peer, and one connected
HRM peer. Take same-phase snapshots once per minute after warm-up. If production
networking or either required BLE peer is unavailable, the representative-load
gate is blocked, not silently downgraded.

## Non-Goals

- Designing a new phone/network deadman.
- Requiring Android to maintain motion.
- Removing ESP-specific physical safety checks.
- Claiming full Pi API parity beyond this control slice.
- Adding or completing `/api/reset`; when separately implemented it must remain
  stopped in Proxy.
- Resolving the clean-build, production WiFi, BLE-radio, authentication, or
  broader capacity issues identified by the independent audit beyond measuring
  and correcting regressions introduced by this control slice.
- Allowing background program execution to recover from takeover or a fault
  without explicit operator action.

## Completion Criteria

This slice is complete only when:

- the normative behavior and all reference implementations agree;
- the full regression and adversarial gates pass;
- API/FTMS responses accurately report rejection;
- the hardware load gate meets its hard deadlines and headroom thresholds;
- the hardware bench checks pass with captured evidence; and
- the external production-build/WiFi prerequisite (`precor-9_3x-p0q`) is
  resolved sufficiently to run those checks on the actual ESP32-S3 artifact.
