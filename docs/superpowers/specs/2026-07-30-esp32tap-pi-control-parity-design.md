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
4. Stop and Pause command zero immediately.
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
- Stop, Pause, physical-console takeover, `TREAD_OK` loss, watchdog failure,
  invalid relay feedback, bounded-emulation timeout, and a real owner
  disconnect where the transport supplies one may still end control.
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
5. WebSocket/program state reports the paused state and the takeover reason.
6. An explicit program Resume or Start may clear the executor inhibit and
   request control again through the normal safety-entry sequence.

An HTTP/coach/BLE manual command is a separate explicit operator action. It may
request manual control while the program remains paused; it must not silently
resume the scheduled program.

### Fault recovery

A latched fault continues to inhibit automatic re-entry. It is not cleared by
time, a background tick, a retry loop, or an interval boundary.

A fresh program Start, program Resume, positive-speed command, or explicit
Reset is a recovery request. The request may clear the latch only when all of
these are true:

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
- Program Resume/Start clears the inhibit only as part of its explicit
  control attempt. Pause synchronization is idempotent, so repeated
  observations cannot resume or otherwise advance a program.

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

Call it from positive manual speed, program Start/Resume, and `/api/reset`.
BLE and coach commands must use the same control function as HTTP.

### 3. Console-takeover propagation

In `esp32tap/src/tasks/serial_engine.rs`, guarded context, the interval
executor, and program/session publication:

- set the executor inhibit during `console_takeover`;
- pause the active `ProgramState` before its next tick or interval plan;
- suppress every executor acquisition while inhibited;
- preserve the current program position and session elapsed state;
- publish a paused/takeover state to WebSocket clients; and
- clear the inhibit only on explicit program Resume/Start.

### 4. Truthful command boundary

In `esp32tap/src/control.rs`, HTTP API handlers, coach, and BLE mappings:

- return success only when motion was accepted and entry was active or
  initiated;
- roll back or zero a command whose entry request is rejected;
- map ownership conflicts and safety rejection to the existing error-capable
  API/FTMS results; and
- keep all surfaces on the same controller path.

### 5. Reset parity

Add or complete `/api/reset` parity with the Pi:

- stop and release the belt;
- clear program/session state as currently defined by the Pi endpoint;
- treat reset as an explicit health-gated recovery acknowledgement; and
- return failure if physical feedback is still unsafe.

Reset is not a back door around relay or `TREAD_OK` checks.

### 6. Documentation and release gates

Update `PLAN.md`, the Rust README, route inventory, and audit follow-up so they
no longer describe the four-second manual lease as required behavior.
Promote `test_reviewer_attacks.py` into the normal release gate once green.

## Verification

### Host contract tests

Add tests proving:

- one manual speed command remains active beyond four and ten seconds;
- repeated commands reuse ownership and do not cycle the relay;
- Stop and Pause command zero and release as specified;
- a console takeover sets the executor inhibit and no executor acquisition can
  clear it;
- Start/Resume explicitly clears that inhibit through the guarded path;
- a fresh Start/speed clears a latch only after qualified healthy Bypass;
- the same request is rejected while feedback or `TREAD_OK` remains unhealthy;
- a rejected request leaves zero advertised motion in Proxy;
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
6. Verify Stop, Pause, controller-task watchdog, and maximum-emulation timeout
   all release control.
7. Capture relay command, NC/NO feedback, `TREAD_OK`, console UART, and motor
   UART timing so the result is based on physical signals rather than API
   state alone.

## Non-Goals

- Designing a new phone/network deadman.
- Requiring Android to maintain motion.
- Removing ESP-specific physical safety checks.
- Claiming full Pi API parity beyond this control slice.
- Resolving the clean-build, production WiFi, BLE-radio, authentication, or
  memory-budget issues identified by the independent audit.
- Allowing background program execution to recover from takeover or a fault
  without explicit operator action.

## Completion Criteria

This slice is complete only when:

- the normative behavior and all reference implementations agree;
- the full regression and adversarial gates pass;
- API/FTMS responses accurately report rejection;
- the hardware bench checks pass with captured evidence; and
- the separate production-build/WiFi blocker is resolved sufficiently to run
  those checks on the actual ESP32-S3 artifact.
