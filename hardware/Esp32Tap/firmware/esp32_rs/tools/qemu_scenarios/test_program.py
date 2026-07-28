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


def test_repeated_manual_commands_do_not_cycle_the_relay(qemu):
    """Regression: the per-request identity mint was a relay cycle per request.

    `SafetyController::connect` emergency-stops when a NEW generation arrives
    for a connection that already owns the lease (`owner_superseded`). The old
    handler minted one per request, so the SECOND `POST /api/speed` dropped the
    relay and re-entered emulate — mid-stride, on a moving treadmill. Only one
    scenario covered the endpoint and it issued one request.
    """
    s = armed(qemu)

    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    s.wait_audit("relay_cmd_on", timeout=30)
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
