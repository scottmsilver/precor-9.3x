# Esp32Tap Raspberry Pi Control-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Raspberry Pi operator-visible control behavior in the Rust Esp32Tap firmware while retaining the ESP32 relay-feedback, `TREAD_OK`, transfer-ordering, maximum-emulation, and task-watchdog protections.

**Architecture:** Remove the unused network-command expiry from the normative safety contract, then add one controller-owned, health-gated recovery entry operation. Keep immediate console takeover inside the guarded safety path, propagate it to the program through a sticky executor inhibit without reversing lock order, and make Start/Resume commit program state only after control entry is accepted. All HTTP, coach, BLE, and interval-executor actions continue through `esp32tap/src/control.rs`.

**Tech Stack:** Rust `no_std` safety/program cores, Rust ESP-IDF firmware, Python normative safety model and pytest, C++ differential reference and Catch2, ESP-IDF QEMU, pytest QEMU harness, ESP32-S3 bench hardware.

---

## Scope and prerequisites

Implement the approved design:

- `docs/superpowers/specs/2026-07-30-esp32tap-pi-control-parity-design.md`

Track implementation against:

- `precor-9_3x-3yk` — persistent manual command and recoverable fault
- `precor-9_3x-d03` — console takeover must pause and inhibit the executor
- `precor-9_3x-p0q` — external prerequisite for a flashable network-enabled
  production artifact; do not absorb it into this control slice

Do not add `/api/reset`, a phone heartbeat, a network deadman, authentication,
WiFi provisioning, or BLE radio work here.

The starting baseline deliberately has three red reviewer attacks:

```text
test_a_manual_speed_command_survives_ten_seconds
test_d_console_takeover_is_not_undone_by_the_running_program
test_e_a_latched_fault_is_recoverable
```

## File responsibility map

- `firmware/safety_model.py`: normative executable safety contract.
- `firmware/esp32/components/portable_core/safety/*`: C++ reference used by
  differential tests; not production firmware.
- `firmware/esp32_rs/safety_core/*`: allocation-free Rust safety controller.
- `firmware/esp32_rs/program_core/src/state.rs`: bounded program lifecycle and
  prepare/commit state transitions.
- `firmware/esp32_rs/esp32tap/src/control.rs`: the sole application-to-belt
  ownership, command, recovery, and result boundary.
- `firmware/esp32_rs/esp32tap/src/context.rs`: guarded executor inhibit.
- `firmware/esp32_rs/esp32tap/src/tasks/serial_engine.rs`: immediate physical
  console takeover detection.
- `firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs`: synchronize
  safety ownership loss into paused program state before ticking.
- `firmware/esp32_rs/esp32tap/src/net/program.rs`: transactional HTTP
  Start/Resume/Pause/Stop.
- `firmware/esp32_rs/esp32tap/src/net/api.rs`,
  `src/net/coach.rs`, and `src/ble/ftms.rs`: surface-specific result mapping
  only; no independent safety policy.
- `firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py`: end-to-end
  regression and adversarial proof.
- `firmware/esp32_rs/tools/sweep.sh`: release-gate wiring.

### Task 1: Record the red baseline and lock the watchdog invariant

**Files:**

- Read: `hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md`
- Read: `hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.defaults`
- Read: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/hal/wdt.rs`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py`

- [ ] **Step 1: Claim the two implementation issues**

Run:

```bash
bd update precor-9_3x-3yk --claim
bd update precor-9_3x-d03 --claim
```

Expected: both issues become `in_progress`.

- [ ] **Step 2: Verify the production watchdog configuration is still present**

Run:

```bash
rg -n '^CONFIG_ESP_TASK_WDT_(EN|INIT|TIMEOUT_S|PANIC)=|^CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=' \
  hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.defaults
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py
```

Expected: timeout `2`, panic enabled, silent reboot enabled, and the watchdog
chain check passes. Stop if any is absent; the parity change must not proceed
on an artifact that can freeze with K1 energized.

- [ ] **Step 3: Run the three known-red attacks**

Run:

```bash
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_reviewer_attacks.py \
  -k 'manual_speed_command or console_takeover or latched_fault' -q -s
```

Expected on the starting image: three failures matching the audit. Preserve
the complete output in the issue notes; an unexpected pass or different
failure requires investigation before implementation.

### Task 2: Make ordinary manual ownership persistent in all contract models

**Files:**

- Modify: `hardware/Esp32Tap/firmware/safety_model.py`
- Modify: `hardware/Esp32Tap/tests/test_firmware_safety_model.py`
- Modify: `hardware/Esp32Tap/firmware/esp32/components/portable_core/safety/safety_constants.h`
- Modify: `hardware/Esp32Tap/firmware/esp32/components/portable_core/safety/safety_controller.h`
- Modify: `hardware/Esp32Tap/firmware/esp32/components/portable_core/safety/safety_controller.cpp`
- Modify: `hardware/Esp32Tap/firmware/esp32/host/tests/test_safety_controller.cpp`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/safety_core/src/safety/constants.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/safety_core/src/safety/controller.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/safety_core/tests/safety_controller.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/difftest/tests/d3_controller_sequences.rs`
- Modify: `hardware/Esp32Tap/firmware/build_safety_manifest.py`
- Modify: `hardware/Esp32Tap/firmware/safety_manifest.schema.json`

- [ ] **Step 1: Replace expiry tests with persistent-owner tests**

In the Python, C++, and Rust controller suites, add equivalent cases:

```rust
#[test]
fn manual_owner_does_not_expire_from_elapsed_time() {
    let (mut c, owner) = connected_manual_owner();
    assert!(c.acquire(&owner, Micros::ZERO));
    assert!(c.command_motion(&owner, SpeedTenths::new(30), InclineHalfPct::ZERO, Micros::ZERO));

    assert!(c.heartbeat(&owner, Micros::new(10_000_000)));

    assert_eq!(c.owner(), Some(owner));
    assert_eq!(c.speed_tenths(), SpeedTenths::new(30));
    assert!(!has_event(&c, "emergency:lease_expired", 0));
    assert_eq!(c.lease_expires_at(), None);
}
```

Also retain tests proving explicit disconnect, emergency stop, and watchdog
reset remove ownership and zero motion. Preserve the Pi's distinct three-hour
no-change behavior: it zeros authoritative motion but retains Emulate mode and
ownership until an explicit exit.

- [ ] **Step 2: Run the contract tests and confirm the new tests fail**

Run:

```bash
pytest -q hardware/Esp32Tap/tests/test_firmware_safety_model.py
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml \
  manual_owner_does_not_expire_from_elapsed_time -- --exact
```

Expected: failures caused by the current four-second deadline.

- [ ] **Step 3: Remove command expiry without falsifying transport identity**

Implement these equivalent semantics in Python, C++, and Rust:

```rust
struct Lease {
    owner: ConnectionIdentity,
}

pub fn lease_expires_at(&self) -> Option<Micros> {
    None
}

pub fn acquire(&mut self, connection: &ConnectionIdentity, now: Micros) -> bool {
    self.enforce_due_safety(now);
    // existing ownership/active-connection checks
    self.lease = Some(Lease { owner: *connection });
    self.push_connection_event("lease_acquired", connection);
    true
}
```

Delete `LeaseExpiry`, `MANUAL_LEASE_US`,
`Controller.MANUAL_LEASE_SECONDS`, expiry renewal, and
`expire_manual_lease`. Keep `heartbeat()` as an authenticated audit/liveness
operation if reference callers still use it, but it must not control command
lifetime.

Do not label HTTP or BLE as `Transport::Executor`; transport identity and
lifetime policy are separate facts.

- [ ] **Step 4: Make the manifest describe “no deadline”**

Keep the existing manifest key for compatibility but encode no deadline:

```json
"manual_lease_seconds": null
```

Update `safety_manifest.schema.json` to permit only `null` for this field in
the current contract and update the manifest tests accordingly. Do not add a
new client heartbeat field.

- [ ] **Step 5: Remove expiry-only generators and keep differential coverage**

Update `d3_controller_sequences.rs` so time still crosses 4 seconds and three
hours, but four seconds has no special branch. Remove only assertions tied to
`MANUAL_LEASE_US`; keep ownership, disconnect, emergency, feedback, and reset
coverage.

- [ ] **Step 6: Run all model and differential gates**

Run:

```bash
pytest -q hardware/Esp32Tap/tests/test_firmware_safety_model.py
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml -q
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/difftest/Cargo.toml -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_case_parity.py
```

Expected: all pass with no `lease_expired` expectation remaining.

- [ ] **Step 7: Commit the persistent-ownership contract**

```bash
git add hardware/Esp32Tap/firmware/safety_model.py \
  hardware/Esp32Tap/tests/test_firmware_safety_model.py \
  hardware/Esp32Tap/firmware/esp32/components/portable_core/safety \
  hardware/Esp32Tap/firmware/esp32/host/tests/test_safety_controller.cpp \
  hardware/Esp32Tap/firmware/esp32_rs/safety_core \
  hardware/Esp32Tap/firmware/esp32_rs/difftest/tests/d3_controller_sequences.rs \
  hardware/Esp32Tap/firmware/build_safety_manifest.py \
  hardware/Esp32Tap/firmware/safety_manifest.schema.json
git commit -m "fix(Esp32Tap): keep manual control until an explicit stop"
```

### Task 3: Add atomic, health-gated fault recovery to the safety contract

**Files:**

- Modify: `hardware/Esp32Tap/firmware/safety_model.py`
- Modify: `hardware/Esp32Tap/tests/test_firmware_safety_model.py`
- Modify: `hardware/Esp32Tap/firmware/esp32/components/portable_core/safety/safety_controller.h`
- Modify: `hardware/Esp32Tap/firmware/esp32/components/portable_core/safety/safety_controller.cpp`
- Modify: `hardware/Esp32Tap/firmware/esp32/host/tests/test_safety_controller.cpp`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/safety_core/src/safety/controller.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/safety_core/tests/safety_controller.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/difftest/src/cpp.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/difftest/tests/r1_reviewer_independent.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/difftest/tests/r2_guided_transfer.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/difftest/tests/d3_controller_sequences.rs`

- [ ] **Step 1: Write identical recovery acceptance/rejection vectors**

Add tests for:

1. fault + continuously qualified Bypass + healthy `TREAD_OK` + fresh console
   + idle UART accepts explicit recovery;
2. ordinary `request_emulate` never clears a fault;
3. recovery rejects `BOTH_OPEN`, `BOTH_CLOSED`, Emulate feedback, unhealthy
   `TREAD_OK`, stale console, busy UART, non-Proxy mode, relay-on, and TX-on;
4. a rejected request leaves the latch set, motion zero, relay off, TX off;
5. a successful request logs `fault_recovery_accepted` before
   `wait_entry_gap`;
6. Bypass observed for less than `RELAY_FEEDBACK_STABLE_US` is rejected.

Use exact event tokens:

```text
fault_recovery_accepted
recovery_rejected:not_proxy
recovery_rejected:tread_not_ok
recovery_rejected:feedback_not_qualified_bypass
recovery_rejected:console_not_fresh
recovery_rejected:uart_not_idle_low
```

- [ ] **Step 2: Run the new vectors and confirm they fail**

Run the Python, Rust safety-core, and C++ host tests. Expected: missing
`request_emulate_recovering`/equivalent operation.

- [ ] **Step 3: Track continuous Bypass qualification**

Add `bypass_since` to each controller. In `observe_relay_feedback`, set it on
the first Bypass sample, retain it only while every subsequent sample is
Bypass, and clear it on every other feedback state and reset-class stop.

Expose no general `clear_fault()` method. The only clearing operation is the
explicit recovery entry below.

- [ ] **Step 4: Implement one atomic recovery-entry operation**

Refactor common entry validation behind:

```rust
pub fn request_emulate_recovering(
    &mut self,
    connection: &ConnectionIdentity,
    now: Micros,
    uart_idle_low: bool,
) -> bool
```

The method must validate every ordinary entry precondition plus qualified
Bypass before changing `fault_latched`. Clear the latch only after all
validation succeeds and immediately begin the existing entry sequence in the
same call. A failure must not require rollback because no state changed.

Keep `request_emulate` unchanged for background/non-explicit attempts; it
continues to reject `fault_latched`.

- [ ] **Step 5: Extend the C++ bridge and differential operations**

Add the method to `difftest/src/cpp.rs` and exercise it in directed and
generated sequences. Compare return value, complete observable state, and
events after every call.

- [ ] **Step 6: Run all safety and differential gates**

Run:

```bash
pytest -q hardware/Esp32Tap/tests/test_firmware_safety_model.py
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml -q
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/difftest/Cargo.toml -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_case_parity.py
```

Expected: all pass and ordinary entry still cannot clear faults.

- [ ] **Step 7: Commit the recovery primitive**

```bash
git add hardware/Esp32Tap/firmware/safety_model.py \
  hardware/Esp32Tap/tests/test_firmware_safety_model.py \
  hardware/Esp32Tap/firmware/esp32/components/portable_core/safety \
  hardware/Esp32Tap/firmware/esp32/host/tests/test_safety_controller.cpp \
  hardware/Esp32Tap/firmware/esp32_rs/safety_core \
  hardware/Esp32Tap/firmware/esp32_rs/difftest
git commit -m "feat(Esp32Tap): add health-gated fault recovery entry"
```

### Task 4: Make application commands truthful and opt-in to recovery

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/control.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/api.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/coach.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/ble/ftms.rs`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_http_entry.py`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_ble_control_point.py`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_coach_review.py`

- [ ] **Step 1: Add failing QEMU cases for truthful rejection**

Add cases that force latched invalid feedback, then assert:

```python
st, body = http(s, "POST", "/api/speed", {"value": 2.0})
assert st == 409
after = status(s)
assert after["mode"] == "proxy"
assert after["relay"] is False
assert after["speed"] == 0.0
```

Add equivalent coach and BLE result assertions. Run them against the current
image and confirm the HTTP/status mismatch fails.

- [ ] **Step 2: Separate ordinary and explicit-recovery commands**

In `control.rs`, use an explicit type rather than a boolean:

```rust
pub enum EntryIntent {
    Ordinary,
    ExplicitRecovery,
}
```

Keep one internal `command_as` implementation. Positive manual speed, program
Start/Resume, positive BLE target speed, and coach positive speed use
`ExplicitRecovery`. Incline-only changes, zero/Stop, executor ticks, and
background `reassert` use `Ordinary`.

- [ ] **Step 3: Return failure when Proxy entry is rejected**

If motion was accepted but the controller is in Proxy and the selected entry
operation rejects:

1. command zero through the same current owner;
2. apply outputs;
3. return `Err(Reject::Refused)`.

Never return `Ok(())` merely because `command_motion` accepted a value that
cannot reach the motor.

- [ ] **Step 4: Map the shared result at each surface**

- HTTP: `409 Conflict` with `ok:false`.
- Coach: override the optimistic transcript with the existing refusal text.
- BLE: return FTMS `RESULT_OPERATION_FAILED`/existing refused mapping.
- Status: zero commanded speed in Proxy after rejection.

Do not duplicate recovery preconditions in any surface.

- [ ] **Step 5: Build and run the focused QEMU suites**

Run:

```bash
ONLY=qemu bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_http_entry.py test_ble_control_point.py \
  test_coach_review.py -q -n 3
```

Expected: all pass.

- [ ] **Step 6: Commit the truthful boundary**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/control.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/api.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/coach.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/ble/ftms.rs \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_http_entry.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_ble_control_point.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_coach_review.py
git commit -m "fix(Esp32Tap): report rejected control entry truthfully"
```

### Task 5: Propagate safety ownership loss into a sticky program pause

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/context.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/control.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/serial_engine.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_program.py`

- [ ] **Step 1: Add an idempotent external-pause unit test**

Add `ProgramState::pause_due_to_safety(now)` tests proving it:

- pauses only a running, unpaused program;
- preserves interval and elapsed position;
- does not resume on repeated calls;
- returns no belt plan, because the safety path already released control.

Run `program_core` tests and confirm the new method is missing.

- [ ] **Step 2: Add guarded executor-inhibit state**

In `Guarded`, add:

```rust
pub executor_inhibited: bool,
```

Initialize it `false`. In `control::hold_lease`, reject
`Surface::Executor` while it is true, using a distinct
`Reject::ExecutorInhibited` so callers and tests cannot confuse it with
another owner.

- [ ] **Step 3: Set the inhibit atomically with console takeover**

In `serial_engine.rs`, in the same guarded hold as:

```rust
controller.emergency_stop("console_takeover", now);
```

set `executor_inhibited = true` before releasing the lock. Do not acquire the
program mutex from this task.

- [ ] **Step 4: Synchronize program state before every executor tick**

In `interval_executor.rs`, preserve the mandatory lock order:

1. lock `program`;
2. lock `guarded`;
3. if a running program is inhibited, or its remembered executor identity no
   longer owns the controller lease, set the inhibit and call
   `pause_due_to_safety(now)`;
4. release `guarded`;
5. do not tick, advance, apply a plan, or reassert on that iteration.

This same ownership-loss check handles asynchronous entry failure, watchdog
mode loss, and console takeover. Request a WebSocket push after releasing both
locks so the app receives `paused:true`. Gate the push with
`#[cfg(feature = "net")]`; the production non-network build must continue to
compile without naming `crate::net`.

- [ ] **Step 5: Strengthen the console-takeover attack**

Extend attack D to assert:

```python
program = http(s, "GET", "/api/program")[1]
assert program["running"] is True
assert program["paused"] is True
```

Continue observing across at least two interval boundaries and assert no new
`lease_acquired:EXECUTOR`, `relay_cmd_on`, or nonzero motor frame.

- [ ] **Step 6: Run focused host and QEMU tests**

Run:

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q
ONLY=qemu bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest \
  test_reviewer_attacks.py::test_d_console_takeover_is_not_undone_by_the_running_program \
  test_program.py -q -s
```

Expected: takeover remains Proxy and program state becomes paused.

- [ ] **Step 7: Commit takeover propagation**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/context.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/control.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/serial_engine.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_program.py
git commit -m "fix(Esp32Tap): pause programs after console takeover"
```

### Task 6: Make program Start and Resume transactional

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/program.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/coach.rs`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_program.py`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_coach.py`
- Test: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py`

- [ ] **Step 1: Write program-core prepare/commit tests**

Add a small, copyable transition token that excludes the `Program` itself:

```rust
pub struct PreparedStart {
    plan: Plan,
    resume_interval: usize,
    resume_elapsed: i64,
}
```

Tests must prove `prepare_start` and `prepare_resume` do not mutate
`running`, `paused`, elapsed counters, or pause timestamps; `commit_*` performs
the existing mutation exactly once.

Do not clone the ~1.5 KB `ProgramState` onto the HTTP task stack.

- [ ] **Step 2: Run program-core tests and confirm failure**

Run:

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q
```

Expected: missing prepare/commit API.

- [ ] **Step 3: Implement prepare/commit without duplicating lifecycle math**

Refactor the current `start`/resume calculations behind private helpers used by
both the legacy method tests and the new transaction API. Keep `Plan` bounded
to two commands and keep `ProgramState: Copy`.

- [ ] **Step 4: Return a result from plan application**

Add a transactional plan application function that returns the first
`control::Reject` instead of only an accepted count. It must use
`EntryIntent::ExplicitRecovery` for Start/Resume and ordinary intent for
Pause/Stop/ticks.

If any command in a start plan fails, command zero and leave program state
uncommitted.

- [ ] **Step 5: Centralize Start/Resume under program-then-guarded lock order**

In `net/program.rs`, add shared helpers used by HTTP and coach:

```rust
pub(crate) fn start_transaction(p: &mut ProgramState, now: Micros) -> Result<(), control::Reject>;
pub(crate) fn resume_transaction(p: &mut ProgramState, now: Micros) -> Result<(), control::Reject>;
```

Each helper:

1. prepares without mutation;
2. locks `guarded`;
3. attempts explicit recovery/entry through a start-only acquisition path
   that may test an inhibited executor without clearing the inhibit first;
4. clears `executor_inhibited` only after the complete control command is
   accepted;
5. commits program state only after acceptance;
6. leaves Start loaded-but-stopped or Resume paused on failure.

Do not temporarily set the inhibit false to call ordinary `hold_lease`: a
concurrent or partially failed attempt would open an automatic reacquisition
window. The start-only acquisition path is the sole bypass and couples
successful acquisition/entry with inhibit clearing under the same guarded
lock.

Start, Quick Start, HTTP Pause-as-Resume, `Action::StartWorkout`, and
`Action::ResumeProgram` must use these helpers.

- [ ] **Step 6: Add end-to-end rollback tests**

In QEMU, hold `TREAD_OK` false and separately force unqualified feedback, then
assert:

- Start returns a rejection and program remains stopped;
- Resume returns a rejection and program remains paused;
- inhibit stays set;
- the controller has no stranded executor owner/lease after either rejection;
- no delayed executor tick acquires a lease;
- after restoring healthy Bypass, a manual positive-speed request can acquire
  ownership, proving no partial program transaction blocked it;
- restoring healthy Bypass plus one new explicit Start/Resume succeeds in a
  separate fresh case.

Use HTTP `409` plus `ok:false` for an immediate safety/ownership rejection;
reserve the Pi-compatible HTTP `200` plus `ok:false` behavior for semantic
no-op errors such as “No program loaded.”

- [ ] **Step 7: Verify Pause and Stop remain distinct**

Add/retain tests proving:

- Pause commands speed zero, retains the executor lease and Emulate mode, and
  marks `paused:true`;
- Stop commands a complete zero frame, begins normal exit, reaches Proxy, and
  releases ownership;
- `/api/program/stop` also stops a manually owned belt;
- failed Pause does not report `paused:true` with nonzero commanded motion.

- [ ] **Step 8: Run focused suites**

Run:

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q
ONLY=qemu bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_program.py test_coach.py \
  test_reviewer_attacks.py -q -n 3
```

Expected: all pass.

- [ ] **Step 9: Commit transactional lifecycle**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/program.rs \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/coach.rs \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_program.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_coach.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py
git commit -m "fix(Esp32Tap): commit program starts only after safe entry"
```

### Task 7: Turn the reviewer attacks into a permanent release gate

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/README.md`

- [ ] **Step 1: Rewrite attacks E and F around intentional fault injection**

The removed lease expiry can no longer create the test fault. Use the existing
QEMU relay model:

1. enter Emulate;
2. `QT k1 bypass` to cause `emergency:relay_feedback_invalid`;
3. assert Proxy, relay/TX off, zero motion, fault latched;
4. `QT k1 auto` and wait for continuously qualified Bypass;
5. issue one positive speed or Start;
6. assert `fault_recovery_accepted`, ordinary entry sequencing, and requested
   motor bytes.

Replace attack F with the negative case: keep `QT k1 closed` or `QT tread 0`
and prove repeated explicit requests deterministically return rejection,
remain zero/Proxy, and never clear the latch.

- [ ] **Step 2: Add explicit resume after console takeover**

After attack D proves no automatic reacquisition across interval boundaries,
POST `/api/program/pause` once (the existing toggle endpoint acts as Resume
while paused) and assert normal entry, the same interval position, and nonzero
wire output. This proves the inhibit is sticky rather than permanent.

- [ ] **Step 3: Run the entire reviewer suite**

Run:

```bash
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_reviewer_attacks.py -q -s
```

Expected: all tests pass, including A through G.

- [ ] **Step 4: Wire it into `sweep.sh`**

Replace the “RED BY DESIGN” exclusion with:

```bash
run reviewer env -C tools/qemu_scenarios \
  python3 -m pytest test_reviewer_attacks.py -q -n 3
```

Keep it outside `DEEP`; these are release-blocking control behaviors, not a
soak.

- [ ] **Step 5: Update README gate documentation**

State that reviewer attacks are mandatory and remove every statement that the
four-second manual lease is normative or that the file is intentionally red.

- [ ] **Step 6: Commit the release gate**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_reviewer_attacks.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh \
  hardware/Esp32Tap/firmware/esp32_rs/README.md
git commit -m "test(Esp32Tap): gate Pi-parity control attacks"
```

### Task 8: Add validation-only cycle-time and device-load instrumentation

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/Cargo.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/perf_core/Cargo.toml`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/perf_core/src/lib.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/perf.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/main.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/mod.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/serial_engine.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/emulate_cycle.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs`
- Modify when enabled: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/session.rs`
- Modify when enabled: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/ble/mod.rs`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/qemu_test/shim_task.rs`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.perf.defaults`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/check_perf_audit.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_perf_audit.py`

- [ ] **Step 1: Write the failing structural gate**

Add `check_perf_audit.py` assertions that initially fail because:

- `perf-audit` is not a declared firmware feature;
- production source does not yet prove that all probes are cfg-gated;
- the serial, emulate, executor, and enabled session/BLE tasks do not publish
  fixed-slot timing and stack-water data;
- heap free/minimum/largest-block, task-wide CPU share, and per-core idle share
  are absent; and
- the implementation has no fixed-size snapshot format or production-artifact
  string-absence check.

The gate must reject heap allocation, formatting, or logging inside a hot-loop
sample. Add failing `perf_core` host tests for saturating maxima/counters,
runtime-counter wraparound, delta CPU percentages, dual-core normalization,
idle-handle classification, and fixed task-capacity overflow. Run both failures
and preserve their output.

- [ ] **Step 2: Add bounded instrumentation behind one feature**

Declare `perf-audit = ["dep:perf_core"]`. Implement fixed-size atomic slots for
iteration count, maximum/p99-bucket steady-state work, loop-start service gap,
relay transfer duration/outcome, and minimum observed stack headroom. Use the
existing monotonic `hal::clock::Clock`; do not add a second timer abstraction.
The serial task's 5 ms constant is a post-work sleep and must not be asserted
as a whole-loop execution deadline. Measure the controller's 10 ms feedback
deadline directly at relay transfer completion.

Each periodic task records only integer samples. Stack high-water is sampled
at most once per second by the task that owns that stack. Add a priority-2,
6144-byte `perf_audit` task that takes a snapshot no more than once per 60
seconds and reads:

- current/minimum free heap and largest free block;
- fixed-slot task timing and stack values; and
- `uxTaskGetSystemState` runtime counters from a fixed-capacity buffer;
- the two `xTaskGetIdleTaskHandleForCore` handles to calculate per-core idle
  share; and
- task-wide CPU share, with core attribution only for pinned tasks.

Use a fixed 48-entry task-status buffer and report overflow as an invalid
snapshot, never a truncated success. From successive snapshots, compute a
pinned task's whole-device share as
`task_delta / (elapsed_runtime_delta * 2)` and core N's idle share as
`idle_N_delta / elapsed_runtime_delta`; handle counter wrap explicitly.

Enable the required FreeRTOS trace/runtime-stat sdkconfig keys only in
`sdkconfig.perf.defaults`, including
`CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID=y`. Add the task to the checked task
matrix as validation-only and WDT-exempt: it owns no safety state, and a
60-second sleep cannot subscribe to the two-second task WDT. Build the
validation artifact with `ESP_IDF_SDKCONFIG_DEFAULTS` naming both the normal
defaults and that overlay; the production sdkconfig and artifact must not
enable runtime statistics.
Call the snapshot APIs only under `#[cfg(feature = "perf-audit")]`. Document
every unsafe FFI boundary. Update the closed unsafe-file/module lists and line
budget for exactly the validation-only FFI sites. Do not log per iteration and
do not allocate while sampling or formatting the snapshot.

- [ ] **Step 3: Make the snapshot machine-readable and measure probe cost**

Emit a single bounded `PERF` record at the 60-second validation interval. Add
`QT perf` to the never-flashed QEMU shim for an immediate snapshot, so focused
tests do not wait a minute. Include firmware commit/binary identity fields in
the hardware capture metadata, not in every hot-loop sample.

Use the external UART/logic capture to record at least 1,000 serial frames from
otherwise identical production and `perf-audit` hardware builds under the
same fixture. More than 5% increase in either median or p99 frame interval
fails the probe design. Any new 10 ms relay-feedback violation also fails.

- [ ] **Step 4: Prove plumbing in QEMU without treating it as timing evidence**

Add a QEMU scenario that verifies counters advance, all required task slots
appear, heap fields are sane, the snapshot is bounded, and no per-iteration
log flood occurs. State in the test that guest wall time, CPU share, stack
water, and maxima are structural observations only.

Run:

```bash
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/perf_core/Cargo.toml -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_perf_audit.py
ONLY=qemu-perf bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
ESP32TAP_TEST_BUILD=build_qemu_perf \
  env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_perf_audit.py -q
```

- [ ] **Step 5: Isolate production and performance artifacts**

Extend `tools/build.sh` with explicit `perf` and `qemu-perf` targets.

- `perf` uses `target_perf/`, `build_perf/`, feature `perf-audit` only, and the
  normal defaults plus `sdkconfig.perf.defaults`. This baseline artifact is
  buildable and flashable before `precor-9_3x-p0q`, but cannot run the
  representative network/BLE load.
- `qemu-perf` uses `target_qemu_perf/`, `build_qemu_perf/`, features
  `qemu-test,net,ble,perf-audit`, the normal defaults, QEMU defaults, and the
  perf overlay.
- existing `prod`/`qemu` target and output directories remain unchanged.

After each build, assert the generated perf sdkconfig enables runtime stats and
core IDs; assert the generated production sdkconfig disables them. Never reuse
a Cargo target directory between these variants. Make `conftest.py` accept only
the explicit `ESP32TAP_TEST_BUILD=build_qemu_perf` override for this scenario;
retain `build_qemu_test` as the default for every existing test.

The exact build invocations are:

```bash
ONLY=prod bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
ONLY=perf bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
ONLY=qemu-perf bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
```

Verify the production ELF has no `perf_audit`, `perf_core`, or `PERF` symbols
or strings. After `precor-9_3x-p0q` supplies the hardware network path, add
`net,ble` to a separately named full-load perf variant using its own target and
output directories; do not pretend the baseline `perf` artifact exercises that
load.

- [ ] **Step 6: Add the focused gate without slowing every edit**

Add the structural check to the normal sweep. Put the QEMU perf scenario in
the release reviewer group so an unchanged built image can be reused with the
other focused QEMU tests. Record elapsed build and test times in the bead
notes. Do not add the ten-minute hardware run to an automated host sweep.

- [ ] **Step 7: Commit instrumentation**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/esp32tap/Cargo.toml \
  hardware/Esp32Tap/firmware/esp32_rs/perf_core \
  hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src \
  hardware/Esp32Tap/firmware/esp32_rs/sdkconfig.perf.defaults \
  hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_perf_audit.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_perf_audit.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
git commit -m "feat(Esp32Tap): instrument validation load headroom"
```

### Task 9: Reconcile documentation and add the hardware evidence runbook

**Files:**

- Read/verify: `CLAUDE.md` (the repository API route inventory)
- Modify: `hardware/Esp32Tap/firmware/PLAN.md`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/HARDWARE_CONTROL_PARITY.md`

- [ ] **Step 1: Remove obsolete policy statements**

Update `PLAN.md` tables and prose so:

- manual HTTP/coach/BLE commands have no elapsed-time deadline;
- watchdog/reset, explicit Stop, real transport disconnect where applicable,
  `TREAD_OK`, relay-feedback faults, and maximum-emulation timeout remain;
- no phone/WebSocket heartbeat is required for motion;
- console takeover pauses the program and sets the executor inhibit.

- [ ] **Step 2: Verify the route inventory does not change**

The route inventory named by the design is the API contract table in
`CLAUDE.md`. This slice changes control semantics but adds no endpoint
(`/api/reset` is explicitly out of scope), so verify the existing speed,
program Start/Pause/Stop, coach, BLE, status, and WebSocket entries remain
accurate. Modify the table only if it currently misstates one of those routes;
do not add a new route for this work.

- [ ] **Step 3: Mark audit findings resolved only by evidence**

In the audit report, retain the original findings and add a dated resolution
section listing the fixing commits and fresh test counts. Do not rewrite the
original audit verdict.

- [ ] **Step 4: Write the hardware runbook**

`HARDWARE_CONTROL_PARITY.md` must include:

- exact firmware commit and binary hash fields;
- board revision and serial number;
- required probes: `RELAY_CMD`, K1 NC/NO contacts, `TREAD_OK`, console UART,
  motor UART;
- current-limited bench-power prerequisites;
- a manual speed held for at least 60 seconds with no phone traffic;
- physical console takeover during a long interval and observation across two
  interval boundaries;
- explicit Resume;
- healthy and unhealthy fault-recovery attempts;
- Pause-at-zero versus Stop-to-Proxy;
- injected stall of every WDT-supervised task, with stable NC bypass required
  within 2.25 seconds;
- a results table with measured timing, pass/fail, capture filename, and
  operator.
- production and `perf-audit` binary hashes plus the measured probe overhead;
- a representative-load recipe covering console/motor traffic, interval
  execution, HTTPS/WebSocket traffic, session recording, and BLE when
  available;
- a two-minute warm-up followed by ten minutes looping a 60-second two-interval
  2.0 mph/0% and 4.0 mph/5% program, one WebSocket subscriber, two TLS clients
  polling `/api/status` at 2 Hz total, a one-second session-task tick with
  normal 30-second flash checkpoints, one FTMS control peer, and one HRM peer;
- same-phase `PERF` samples once per minute with steady-state work/service gap,
  transfer duration/outcome, stack headroom, task-wide CPU, per-core idle, and
  free/minimum/largest heap values; and
- explicit optimization triggers: p99 non-transfer work above half the
  configured post-work delay, below 20% per-core idle headroom (below 10%
  fails release), below 1 KiB or 20% stack headroom, final-three median free
  heap more than 2 KiB below the first-three post-warm-up median, or largest
  free block below 64 KiB.

State plainly that QEMU cannot validate the watchdog panic/reset path, relay
mechanics, contact timing, or hardware performance/load thresholds.
State that the representative-load result is blocked—not passed with a reduced
fixture—until `precor-9_3x-p0q` provides a flashable production network/BLE
artifact and both BLE peers are available.

- [ ] **Step 5: Run documentation consistency checks**

Run:

```bash
rg -n '4 s|four.second|MANUAL_LEASE|red by design|RED BY DESIGN' \
  hardware/Esp32Tap/firmware/PLAN.md \
  hardware/Esp32Tap/firmware/esp32_rs/README.md \
  hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
git diff --check
```

Expected: no obsolete policy claims; historical references must be explicitly
labelled historical.

- [ ] **Step 6: Commit documentation and runbook**

```bash
git add hardware/Esp32Tap/firmware/PLAN.md \
  hardware/Esp32Tap/firmware/esp32_rs/README.md \
  hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md \
  hardware/Esp32Tap/firmware/esp32_rs/HARDWARE_CONTROL_PARITY.md
git commit -m "docs(Esp32Tap): document Pi-parity hardware acceptance"
```

### Task 10: Run full release verification and hand off hardware work

**Files:**

- Verify only; do not weaken tests or exclude failures.

- [ ] **Step 1: Run fast source and host gates**

```bash
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_case_parity.py
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py
pytest -q hardware/Esp32Tap/tests/test_firmware_safety_model.py
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml -q
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q
cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/difftest/Cargo.toml -q
```

Expected: all pass.

- [ ] **Step 2: Run the complete normal sweep**

Run:

```bash
bash hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
```

Expected: `SWEEP: ... ALL GREEN`, including the reviewer gate.

- [ ] **Step 3: Run the deep sweep**

Run:

```bash
DEEP=1 bash hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
```

Expected: `SWEEP: ... ALL GREEN`.

- [ ] **Step 4: Verify the exact watchdog config in the built production sdkconfig**

Run:

```bash
grep -E '^CONFIG_ESP_TASK_WDT_(EN|INIT|TIMEOUT_S|PANIC)=|^CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=' \
  hardware/Esp32Tap/firmware/esp32_rs/build/sdkconfig
```

Expected: enabled, initialized, two-second timeout, panic enabled, silent
reboot enabled.

- [ ] **Step 5: Record code-complete status without claiming hardware proof**

Update `precor-9_3x-3yk` and `precor-9_3x-d03` with:

- commit hashes;
- host/QEMU commands and counts;
- production binary hash;
- remaining hardware runbook path;
- explicit statement that WDT-to-contact timing is unverified until bench
  captures exist.

Do not close a hardware-dependent acceptance item before the corresponding
capture passes.

- [ ] **Step 6: Commit any verification-only metadata**

```bash
git status --short
git diff --check
```

Commit only intentional tracked metadata. Preserve unrelated user changes.

- [ ] **Step 7: Push code and beads**

```bash
git pull --rebase
git push
bd dolt push
git status
```

Expected: branch is up to date with its remote. If no beads remote is
configured, record that exact result; do not claim beads were remotely synced.

## Hardware execution gate

After Task 10 and after a flashable hardware build exists, execute
`HARDWARE_CONTROL_PARITY.md` on the bench before treadmill contact. A passing
host/QEMU sweep is code-complete evidence only. It is not evidence that the
physical relay releases, that NC closes within 2.25 seconds after a task stall,
or that the real treadmill accepts the resulting UART behavior.
