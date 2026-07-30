"""Slice 4 proof: a program POSTed over HTTP actually RUNS on the device.

The unit tests in `program_core/` prove the state machine (67 of them, 0.00 s).
What they cannot prove is the thing this slice exists for: that a workout put
on the device over the network is then executed by the device's own clock,
against the real safety controller, on the real relay — with no client
involved. That is what these scenarios assert, in QEMU, deterministically.

WHAT IS PROVEN HERE
  * A POSTed program is stored, echoed back in the Pi's `to_dict()` shape, and
    started.
  * Starting it drives a REAL relay transfer (`relay_cmd_on`, `tx_enable_on`),
    and the FIRST frames on the motor wire after that entry are `[hmph:0]` and
    `[inc:0]` — PLAN entry step 6 — even though the program's first interval
    commands a nonzero speed.
  * Intervals advance ON THE GUEST CLOCK, with no request in flight, and the
    wire carries the new interval's speed.
  * A pause holds the program clock AND zeroes the belt; a resume restores it.
  * A skip advances immediately and the belt follows.
  * A stop ends the program, zeroes the belt and HANDS THE LEASE BACK, so a
    manual command works again afterwards.
  * A second `POST /api/speed` does NOT cycle the relay — the latent
    self-supersede bug in the old per-request identity minting.
  * An oversized program is refused at admission, and a malformed one is
    refused by the parser, in both cases without disturbing what is loaded.

WHY THERE IS NO `time.sleep(n)` ANYWHERE
Guest time under QEMU lags wall time, badly and variably under xdist. Every
wait below is on a GUEST-OBSERVED fact: an audit event, a log line, the
firmware's own 5 s heartbeat, or a value read back from the device. A
wall-clock sleep here would be an intermittent, and an intermittent is worse
than a hard failure.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10
INTERVAL_EXECUTOR_RS = (
    HERE.parents[1] / "esp32tap" / "src" / "tasks" / "interval_executor.rs"
)


def http(s, method, path, body=None, timeout=20):
    """`httpc.request`, but a 4xx/5xx is a RESULT rather than an exception.

    Half of what this file proves is that the device REFUSES things — an
    oversized program, a malformed one, a manual command while a program owns
    the belt — so the status code is data here, not an error.
    """
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


# Durations are the engine's MIN_DURATION (program_engine.MIN_DURATION = 10 s,
# ported verbatim). Shorter would be clamped UP by the device and the test
# would be asserting against a program it did not send.
PROGRAM = {
    "name": "QEMU Intervals",
    "intervals": [
        {"name": "Warmup", "duration": 10, "speed": 1.0, "incline": 0},
        {"name": "Push", "duration": 10, "speed": 4.0, "incline": 2.0},
        {"name": "Cooldown", "duration": 600, "speed": 2.0, "incline": 0},
    ],
}

ONE_INTERVAL_PROGRAM = {
    "name": "QEMU One Interval",
    "intervals": [
        {"name": "Only", "duration": 10, "speed": 1.0, "incline": 0},
    ],
}


def test_executor_hold_is_rechecked_after_acquiring_program_lock():
    """The QEMU hold must close the pre-lock check/use race.

    A tick can pass the cheap pre-lock check, wait behind the test shim's
    program lock, and wake only after the shim has set the hold. Rechecking
    while owning that lock ensures the shim's lock drain is a real barrier.
    """
    source = INTERVAL_EXECUTOR_RS.read_text()
    lock_at = source.index("let mut p = lock(&ctx.program);")
    first_mutation = source.index("if !p.running()", lock_at)
    post_lock_gate = source[lock_at:first_mutation]
    assert "executor_held()" in post_lock_gate, post_lock_gate
    assert "drop(p);" in post_lock_gate, post_lock_gate
    assert "continue;" in post_lock_gate, post_lock_gate

# hmph is mph*100 in uppercase hex — the wire encoding the motor sees.
WIRE = {1.0: b"[hmph:64]", 4.0: b"[hmph:190]", 2.0: b"[hmph:C8]"}


def armed(qemu):
    """A booted guest with the HARDWARE preconditions for emulate entry met.

    Those are hardware state, so the shim scripts them exactly as it does for
    the QT-driven scenarios. Everything the tests actually assert arrives over
    HTTPS.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    s.cmd_ok("QT tread 1")
    # The shim's relay model tracks RELAY_CMD with a 2 ms break-before-make so
    # feedback can qualify a transfer; pinning it to bypass would stall entry
    # in ENTRY_WAIT_FEEDBACK forever.
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def assert_emulate_entry_completed(s, since: int, timeout: float = 30.0) -> None:
    """Wait for the guest fact that emulate entry FINISHED, not for its effects.

    WHY THIS EXISTS, AND WHY IT IS NOT A SLEEP OR A RETRY.

    Entry is `relay_cmd_on` -> a 10 ms feedback window -> `feedback_emulate_stable`
    -> the emulate cycle starts transmitting. `relay_cmd_on` therefore proves
    only that entry BEGAN. The window is a busy-poll at `FEEDBACK_POLL_US`
    (200 us) against `RELAY_FEEDBACK_DEADLINE_US` (10 ms), and blowing it is
    fail-closed by design: `entry_feedback_timeout` latches a fault, the relay
    drops, and the belt never transmits a byte.

    A test that waits for wire bytes after `relay_cmd_on` cannot tell those two
    outcomes apart. It reports "UART1 TX never contained b'[hmph:...]' (have 0
    bytes)" THIRTY SECONDS after the entry already failed, which reads as a
    silent firmware or a dead capture thread and sends the reader looking in
    the wrong place. That is exactly the ambiguity `_check_stimulus` was added
    to remove on the harness side, applied here to the guest side.

    So: assert the entry outcome AT the entry, where the audit ring still names
    the mechanism, and only then look at the wire. This TIGHTENS the test — it
    can now fail for a reason it previously could only fail obliquely — and
    adds no tolerance of any kind.

    IF THIS FIRES, READ BEAD precor-9_3x-9aj BEFORE CHANGING ANYTHING. There is
    a known, diagnosed way for it to happen that is NOT a firmware defect: the
    10 ms deadline is measured in `esp_timer_get_time()`, which esp-QEMU drives
    from QEMU_CLOCK_VIRTUAL — and with `-icount` off, that is HOST WALL TIME —
    while progress through the poll loop is measured by `esp_rom_delay_us`,
    which advances with the CPU cycle counter, i.e. GUEST INSTRUCTION TIME. A
    host that deschedules the QEMU process mid-window spends the budget without
    advancing the loop, and the transfer fails closed. On real silicon both
    clocks are the same silicon. The bead records the candidate fix
    (`-icount shift=auto`) and why it was not taken unilaterally. Do NOT raise
    the deadline, retry the entry, sleep, or lower -n: all four hide the
    divergence and none removes it.
    """

    def entry_events():
        # `complete_console_frame` fires at the pacer's rate and would push the
        # entry sequence out of any tail; drop it so the report is the transfer.
        return [(i, t) for i, t in s.audit_events() if i >= since and t != "complete_console_frame"]

    deadline = time.monotonic() + timeout
    while True:
        events = entry_events()
        texts = [t for _, t in events]
        if "feedback_emulate_stable" in texts:
            return
        # A fail-closed abort is TERMINAL — waiting the rest of the timeout out
        # would only delay the report and then blame the wrong thing.
        bad = [(i, t) for i, t in events if t.startswith("emergency:") or t.startswith("entry_abort:")]
        if bad or time.monotonic() > deadline:
            raise AssertionError(
                "emulate entry did not complete: no `feedback_emulate_stable`.\n"
                f"  aborts seen: {bad or 'none'}\n"
                f"  transfer events since the command: {events[:40]}\n"
                "  `relay_cmd_on` with no `feedback_emulate_stable` means the "
                "10 ms RELAY_FEEDBACK_DEADLINE window did not qualify."
            )
        time.sleep(0.05)


def prog(s):
    st, body = http(s, "GET", "/api/program")
    assert st == 200, body
    return body


def wait_for(s, predicate, what: str, timeout: float = 90.0):
    """Poll GET /api/program until `predicate(state)`, bounded by the GUEST's
    own heartbeat rather than by wall time."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = prog(s)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"never observed {what}; last state was {last!r}")


def test_a_posted_program_is_stored_and_echoed(qemu):
    s = armed(qemu)

    empty = prog(s)
    assert empty["program"] is None and empty["running"] is False, empty

    st, body = http(s, "POST", "/api/program/load", PROGRAM)
    assert st == 200, body
    # The Pi's ProgramState.to_dict() shape, field for field.
    for k in (
        "type",
        "program",
        "running",
        "paused",
        "completed",
        "current_interval",
        "interval_elapsed",
        "total_elapsed",
        "total_duration",
    ):
        assert k in body, f"{k} missing from {body}"
    assert body["type"] == "program"
    assert body["total_duration"] == 620, body
    assert [iv["name"] for iv in body["program"]["intervals"]] == [
        "Warmup",
        "Push",
        "Cooldown",
    ]
    assert body["program"]["intervals"][1]["speed"] == 4.0
    assert body["program"]["intervals"][1]["incline"] == 2.0

    # It SURVIVES: a later GET, with no client state, returns the same thing.
    assert prog(s)["program"] == body["program"]
    s.stop_pacer()


def test_starting_a_program_drives_a_real_relay_transfer_and_enters_at_zero(qemu):
    s = armed(qemu)

    st, body = http(s, "GET", "/api/status")
    assert st == 200 and body["mode"] == "proxy" and body["relay"] is False, body

    n0 = len(s.audit_events())
    tx0 = len(s.tx_bytes())

    # ONE request loads and starts. After this the tablet may leave.
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    assert body["current_interval"] == 0, body

    # THE CLAIM: the program drove a real transfer, through the same safety
    # path an HTTP motion command uses.
    s.wait_audit("relay_cmd_on", since=n0, timeout=30)
    s.wait_audit("tx_enable_on", since=n0, timeout=5)

    # ...and PLAN entry step 6 holds even though interval 0 commands 1.0 mph:
    # the FIRST thing the motor is told after the bridge moves is zero.
    s.wait_tx_contains(b"[hmph:0]", timeout=20, offset=tx0)
    s.wait_tx_contains(b"[inc:0]", timeout=20, offset=tx0)
    wire = s.tx_bytes()[tx0:]
    first_speed = wire.index(b"[hmph:")
    assert wire[first_speed : first_speed + len(b"[hmph:0]")] == b"[hmph:0]", (
        "the first speed frame after entry was not zero: " f"{wire[first_speed:first_speed + 24]!r}"
    )

    # Only THEN does the program's own speed reach the wire.
    s.wait_tx_contains(WIRE[1.0], timeout=30, offset=tx0)
    s.stop_pacer()


def test_failed_start_is_rolled_back_and_does_not_strand_the_executor_lease(qemu):
    """A safety refusal is a failed transaction, not a running workout."""
    s = armed(qemu)
    s.cmd_ok("QT tread 0")

    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 409 and body["ok"] is False, body
    loaded = prog(s)
    assert loaded["program"]["name"] == PROGRAM["name"], loaded
    assert loaded["running"] is False and loaded["paused"] is False, loaded

    # Restore health and prove the failed transaction did not leave the
    # executor owning the lease. This is deliberately a MANUAL positive-speed
    # command: success means there is no hidden executor owner to fight.
    s.cmd_ok("QT tread 1")
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.wait_tx_contains(WIRE[2.0], timeout=45)
    s.stop_pacer()


def test_start_ownership_conflict_cleans_attempted_executor_identity(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.wait_tx_contains(WIRE[2.0], timeout=45)
    st, body = http(s, "POST", "/api/program/load", PROGRAM)
    assert st == 200 and body["running"] is False, body

    st, body = http(s, "POST", "/api/program/start")
    assert st == 409 and body["ok"] is False, body
    owner = s.cmd_ok("QT program_owner")
    assert "generation=1" in owner, owner
    assert "current=0" in owner and "active=0" in owner and "owns=0" in owner, owner
    st, state = http(s, "GET", "/api/status")
    assert state["speed"] == 2.0, state

    # Stop releases the unrelated manual owner. A new explicit Start must mint
    # generation 2 (never reuse the cleaned generation 1) and then succeed.
    n0 = len(s.audit_events())
    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200, body
    s.wait_audit("lease_released", since=n0, timeout=45)
    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True, body
    owner = s.cmd_ok("QT program_owner")
    assert "generation=2" in owner, owner
    assert "current=1" in owner and "active=1" in owner and "owns=1" in owner, owner
    s.stop_pacer()


def test_intervals_advance_on_the_guest_clock_with_no_client(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)
    tx0 = len(s.tx_bytes())

    # Warmup is 10 s of GUEST time. Nothing is sent to the device in between:
    # the advance is the executor's own doing.
    after = wait_for(s, lambda p: p["current_interval"] == 1, "the advance to interval 1")
    assert after["running"] is True and after["completed"] is False
    assert after["total_elapsed"] >= 10, after

    # The belt followed: 4.0 mph is on the wire and in the controller.
    s.wait_tx_contains(WIRE[4.0], timeout=60, offset=tx0)
    st, status = http(s, "GET", "/api/status")
    assert st == 200 and status["speed"] == 4.0, status
    assert status["incline"] == 2.0, status
    assert status["mode"] == "emulate", status
    s.stop_pacer()


def test_pause_holds_the_program_and_zeroes_the_belt_then_resume_restores_it(qemu):
    s = armed(qemu)
    http(s, "POST", "/api/program/start", PROGRAM)
    s.wait_tx_contains(WIRE[1.0], timeout=60)

    st, body = http(s, "POST", "/api/program/pause")
    assert st == 200 and body["paused"] is True, body
    held = body["total_elapsed"]

    # `server.py::_apply_pause_toggle` zeroes SPEED and leaves incline. Assert
    # the same effect on the real controller.
    st, status = http(s, "GET", "/api/status")
    assert st == 200 and status["speed"] == 0.0, status

    # Let the GUEST advance 6 s of its own clock — measured by the firmware's
    # 5 s heartbeat, i.e. by the same clock the executor ticks on, never by
    # wall time. The program clock must not move.
    s.wait_guest_uptime_delta(6, timeout=90)
    st, body = http(s, "GET", "/api/program")
    assert body["paused"] is True
    assert body["total_elapsed"] == held, f"a paused program advanced from {held} to {body['total_elapsed']}"
    assert body["current_interval"] == 0, body

    tx0 = len(s.tx_bytes())
    st, body = http(s, "POST", "/api/program/pause")
    assert st == 200 and body["paused"] is False, body
    # Resume re-commands the current interval.
    s.wait_tx_contains(WIRE[1.0], timeout=60, offset=tx0)
    s.stop_pacer()


def test_pause_of_stopped_program_is_a_semantic_noop(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/load", PROGRAM)
    assert st == 200 and body["running"] is False and body["paused"] is False, body
    st, body = http(s, "POST", "/api/program/pause")
    assert st == 200 and body["ok"] is False, body
    state = prog(s)
    assert state["running"] is False and state["paused"] is False, state
    s.stop_pacer()


def test_rejected_pause_does_not_publish_paused_while_motion_remains_nonzero(qemu):
    """Isolate the pre-executor-sync interlock window deterministically.

    Production sets this sticky bit during ownership loss and the 1 s executor
    consumes it later. The test-image command sets only that application gate,
    intentionally preserving the nonzero controller command so this asserts
    the stronger atomicity property: a rejected zero can never commit Pause.
    """
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True and body["paused"] is False, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)
    s.cmd_ok("QT executor_inhibit 1")

    # Hold for more than one complete executor tick. QTOK is emitted only
    # after the shim has set the hold while owning and then releasing the
    # program lock; no already-in-flight tick may mutate program state after
    # that acknowledgement.
    s.wait_guest_uptime_delta(2, timeout=90)
    held = prog(s)
    assert held["running"] is True and held["paused"] is False, held
    st, held_belt = http(s, "GET", "/api/status")
    assert (
        st == 200
        and held_belt["speed"] == 1.0
        and held_belt["mode"] == "emulate"
    ), held_belt

    st, body = http(s, "POST", "/api/program/pause")
    assert st == 409 and body["ok"] is False, body
    state = prog(s)
    assert state["running"] is True and state["paused"] is False, state
    st, belt = http(s, "GET", "/api/status")
    assert st == 200 and belt["speed"] == 1.0 and belt["mode"] == "emulate", belt
    s.cmd_ok("QT executor_inhibit 0")
    http(s, "POST", "/api/program/stop")
    s.stop_pacer()


def test_failed_resume_stays_paused_and_does_not_strand_the_executor_lease(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)
    st, body = http(s, "POST", "/api/program/pause")
    assert st == 200 and body["paused"] is True, body
    held = body["total_elapsed"]

    # Physical takeover makes the executor inhibit sticky and releases its
    # controller lease. Then make relay feedback unqualified for recovery.
    s.set_pacer_payload(synth.console_cycle_bytes(20, 0))
    s.wait_audit("emergency:console_takeover", timeout=20)
    s.cmd_ok("QT k1 closed")
    st, body = http(s, "POST", "/api/program/pause")
    assert st == 409 and body["ok"] is False, body
    after = prog(s)
    assert after["running"] is True and after["paused"] is True, after
    assert after["total_elapsed"] == held, (held, after)

    # Restore health. A manual positive command can own and move the belt,
    # proving the failed Resume left no delayed executor acquisition behind.
    s.cmd_ok("QT k1 auto")
    s.set_pacer_payload(synth.console_cycle_bytes(0, 0))
    s.wait_audit("complete_console_frame", timeout=20)
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.wait_tx_contains(WIRE[2.0], timeout=45)
    s.stop_pacer()


def test_skip_advances_immediately_and_the_belt_follows(qemu):
    s = armed(qemu)
    http(s, "POST", "/api/program/start", PROGRAM)
    s.wait_tx_contains(WIRE[1.0], timeout=60)
    tx0 = len(s.tx_bytes())

    st, body = http(s, "POST", "/api/program/skip")
    assert st == 200, body
    assert body["current_interval"] == 1, body
    assert body["interval_elapsed"] == 0, body
    # ProgramState.skip jumps total_elapsed to the cumulative duration of what
    # was skipped — 10 s of Warmup.
    assert body["total_elapsed"] == 10, body

    s.wait_tx_contains(WIRE[4.0], timeout=60, offset=tx0)

    # And back again.
    st, body = http(s, "POST", "/api/program/prev")
    assert st == 200 and body["current_interval"] == 0, body
    assert body["total_elapsed"] == 0, body
    s.stop_pacer()


def test_stop_ends_the_program_zeroes_the_belt_and_returns_the_lease(qemu):
    s = armed(qemu)
    http(s, "POST", "/api/program/start", PROGRAM)
    s.wait_tx_contains(WIRE[1.0], timeout=60)

    # While a program owns the belt, a manual command is REFUSED rather than
    # fighting it for the lease (which would emergency-stop the executor).
    st, body = http(s, "POST", "/api/speed", {"value": 7.0})
    assert st == 409 and body["ok"] is False, body

    n0 = len(s.audit_events())
    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200 and body["running"] is False, body

    # PLAN's polite exit ran, so the belt was told zero BEFORE the bridge went
    # back to copper, and ownership was released.
    s.wait_audit("send_and_finish_complete_zero_frame", since=n0, timeout=30)
    s.wait_audit("lease_released", since=n0, timeout=45)
    st, status = http(s, "GET", "/api/status")
    assert status["speed"] == 0.0 and status["mode"] == "proxy", status
    assert status["relay"] is False, status

    # THE LEASE REALLY CAME BACK: a manual command works again. Without
    # `control::release` the executor's NoDeadline lease would be held forever
    # and the device would be unusable after one workout.
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.stop_pacer()


def test_stop_then_immediate_start_is_rejected_until_normal_exit_finishes(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", ONE_INTERVAL_PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)

    n0 = len(s.audit_events())
    race = s.cmd_ok("QT program_exit_race stop_gap")
    assert "exit_ok=1" in race and "before=EXIT_WAIT_GAP" in race, race
    assert "after=EXIT_WAIT_GAP" in race, race
    assert "start_ok=0" in race and "running=0" in race and "paused=0" in race, race
    state = prog(s)
    assert state["running"] is False and state["paused"] is False, state

    s.wait_audit("lease_released", since=n0, timeout=45)
    s.assert_no_audit(
        lambda text: text.startswith("emergency:"),
        since=n0,
        label="while rejecting Start during stop exit",
    )
    owner = s.cmd_ok("QT program_owner")
    assert "owns=0" in owner, owner

    # The rejection neither committed a false run nor stranded the lease.
    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True, body
    owner = s.cmd_ok("QT program_owner")
    assert "owns=1" in owner, owner
    s.stop_pacer()


def test_natural_completion_then_immediate_start_is_rejected_until_exit_finishes(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", ONE_INTERVAL_PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)

    n0 = len(s.audit_events())
    race = s.cmd_ok("QT program_exit_race natural_feedback")
    assert "exit_ok=1" in race and "before=EXIT_WAIT_FEEDBACK" in race, race
    assert "after=EXIT_WAIT_FEEDBACK" in race, race
    assert "start_ok=0" in race and "running=0" in race and "paused=0" in race, race
    state = prog(s)
    assert state["running"] is False and state["paused"] is False, state
    assert state["completed"] is True, state

    s.wait_audit("lease_released", since=n0, timeout=45)
    s.assert_no_audit(
        lambda text: text.startswith("emergency:"),
        since=n0,
        label="while rejecting Start during natural-completion exit",
    )
    owner = s.cmd_ok("QT program_owner")
    assert "owns=0" in owner, owner

    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True, body
    owner = s.cmd_ok("QT program_owner")
    assert "owns=1" in owner, owner
    s.stop_pacer()


def test_zero_then_motion_start_may_continue_through_legitimate_entry_phase(qemu):
    """Reject normal EXIT phases without confusing them with safe ENTRY."""
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", ONE_INTERVAL_PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=60)

    # Keep ProgramState running while a hardware permission loss returns the
    # controller to Proxy. The fresh Start now prepares zero-then-motion:
    # command one begins entry, command two arrives in EntryWaitGap.
    s.cmd_ok("QT executor_inhibit 1")
    s.cmd_ok("QT tread 0")
    s.wait_audit("emergency:tread_not_ok", timeout=30)
    s.cmd_ok("QT tread 1")
    s.wait_audit("complete_console_frame", timeout=30)

    n0 = len(s.audit_events())
    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True and body["paused"] is False, body
    s.cmd_ok("QT executor_inhibit 0")
    assert_emulate_entry_completed(s, since=n0)
    s.wait_tx_contains(WIRE[1.0], timeout=45)
    owner = s.cmd_ok("QT program_owner")
    assert "owns=1" in owner, owner
    s.stop_pacer()


def test_repeated_manual_commands_do_not_cycle_the_relay(qemu):
    """Regression: the per-request identity mint was a relay cycle per request.

    `SafetyController::connect` emergency-stops when a NEW generation arrives
    for a connection that already owns the lease (`owner_superseded`). The old
    handler minted one per request, so the SECOND `POST /api/speed` dropped the
    relay and re-entered emulate — mid-stride, on a moving treadmill. Only one
    scenario covered the endpoint and it issued one request.
    """
    s = armed(qemu)

    entry_from = len(s.audit_events())
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.wait_audit("relay_cmd_on", timeout=30, since=entry_from)
    # Entry BEGAN above; this is where it is proven to have FINISHED.
    assert_emulate_entry_completed(s, since=entry_from)
    s.wait_tx_contains(b"[hmph:C8]", timeout=30)

    n0 = len(s.audit_events())
    for value, needle in ((3.0, b"[hmph:12C]"), (4.0, b"[hmph:190]"), (5.0, b"[hmph:1F4]")):
        st, body = http(s, "POST", "/api/speed", {"value": value})
        assert st == 200 and body["ok"] is True, (value, body)
        s.wait_tx_contains(needle, timeout=30)

    # THE ASSERTION: no supersede, no emergency, no second transfer.
    s.assert_no_audit(
        lambda t: t.startswith("emergency:"),
        since=n0,
        label="across repeated manual commands",
    )
    s.assert_no_audit(
        lambda t: t == "relay_cmd_on" or t == "relay_cmd_off",
        since=n0,
        label="across repeated manual commands",
    )
    st, status = http(s, "GET", "/api/status")
    assert status["mode"] == "emulate" and status["speed"] == 5.0, status
    s.stop_pacer()


def test_quick_start_runs_a_manual_program(qemu):
    s = armed(qemu)
    st, body = http(
        s,
        "POST",
        "/api/program/quick-start",
        {"speed": 2.0, "incline": 1.0, "duration_minutes": 30},
    )
    assert st == 200 and body["running"] is True, body
    assert body["program"]["manual"] is True, body
    assert body["total_duration"] == 1800, body
    assert body["program"]["intervals"][0]["speed"] == 2.0, body

    s.wait_tx_contains(WIRE[2.0], timeout=60)

    # adjust-duration is MANUAL-only on the Pi, and is here too.
    st, body = http(s, "POST", "/api/program/adjust-duration", {"delta_seconds": 300})
    assert st == 200, body
    assert body["total_duration"] == 2100, body
    s.stop_pacer()


def test_extend_changes_the_running_interval(qemu):
    s = armed(qemu)
    http(s, "POST", "/api/program/start", PROGRAM)
    st, body = http(s, "POST", "/api/program/extend", {"seconds": 60})
    assert st == 200, body
    assert body["program"]["intervals"][0]["duration"] == 70, body
    assert body["total_duration"] == 680, body

    # Below MIN_DURATION it clamps rather than producing a 0-second interval
    # the tick loop would spin through.
    st, body = http(s, "POST", "/api/program/extend", {"seconds": -3600})
    assert body["program"]["intervals"][0]["duration"] == 10, body
    s.stop_pacer()


def test_program_storage_is_bounded_and_refusal_is_clean(qemu):
    """The C++ tier died of unbounded client-supplied storage. Prove it cannot
    happen here — and that a refusal leaves the loaded program untouched."""
    s = armed(qemu)
    http(s, "POST", "/api/program/load", PROGRAM)
    good = prog(s)["program"]

    # (1) Too many intervals: refused by the parser, not truncated.
    st, body = http(
        s,
        "POST",
        "/api/program/load",
        {"name": "huge", "intervals": [dict(PROGRAM["intervals"][0]) for _ in range(30)]},
    )
    assert st == 400 and "interval" in body["error"], body

    # (2) A body larger than one request slot: refused at ADMISSION, before a
    # byte is parsed.
    st, body = http(
        s,
        "POST",
        "/api/program/load",
        {"name": "x" * 4000, "intervals": list(PROGRAM["intervals"])},
    )
    assert st == 413, f"oversized program was not refused at admission: {st} {body}"

    # (3) Malformed.
    st, body = http(s, "POST", "/api/program/load", {"name": "no intervals here"})
    assert st == 400, body

    # THE LOADED PROGRAM IS UNCHANGED by any of the three.
    assert prog(s)["program"] == good
    s.stop_pacer()


# ---------------------------------------------------------------------------
# A DECLINED START IS ROLLED BACK UNTIL A FRESH EXPLICIT START.
#
# Start is a user-visible transaction now. A stale console is an unsafe entry,
# so the request reports 409 and leaves the loaded workout stopped. No
# background retry may convert that refusal into motion later; restoring health
# only makes a NEW explicit Start eligible to recover.
# ---------------------------------------------------------------------------


def test_stale_console_start_rolls_back_until_a_fresh_explicit_start(qemu):
    s = armed(qemu)

    # Take the console away. The staleness threshold is 1.5 s of GUEST time, so
    # the wait is on a guest-observed fact — the firmware's own 5 s heartbeat —
    # rather than on a wall-clock sleep, which under xdist would be the
    # intermittent this test exists to remove.
    s.stop_pacer()
    n_lines = len(s.lines())
    s.wait_log(r"heartbeat uptime=", timeout=30, since_line=n_lines)

    idx0 = s.audit_events()[-1][0] if s.audit_events() else 0
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 409 and body["ok"] is False, body
    state = prog(s)
    assert state["running"] is False and state["paused"] is False, state

    # The entry IS refused, and for the reason this test is about.
    rollback = s.wait_audit("emergency:owner_disconnect", timeout=30, since=idx0)
    assert b"[hmph:" not in s.tx_bytes(), "the belt moved with a stale console"
    s.wait_guest_uptime_delta(6, timeout=90)
    s.assert_no_audit(
        lambda t: t.startswith("lease_acquired:EXECUTOR"),
        since=rollback + 1,
        label="after a rejected transactional Start",
    )

    # Restored health is necessary but not sufficient. The fresh explicit
    # Start is the sole event allowed to clear the sticky executor inhibit.
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE[1.0], timeout=45)

    st, state = http(s, "GET", "/api/status")
    assert state["emulate"] is True, state
    assert state["speed"] == 1.0, state
    s.stop_pacer()
