"""SECOND-PASS adversarial review — attacks the existing suite does not make.

Every test here states the SAFE behaviour as its assertion. A failure is a
claim about the device, reproducible with the command in the docstring.
Nothing here retries, sleeps for a guest fact, or loosens a bound.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10

WIRE_3 = b"[hmph:12C]"  # 3.00 mph
WIRE_2 = b"[hmph:C8]"  # 2.00 mph
WIRE_0 = b"[hmph:0]"

ENTRY_SEQUENCE = [
    "command_zero",
    "configure_inverted_uart",
    "verify_physical_idle_low",
    "tx_enable_on",
    "wait_entry_gap",
    "relay_cmd_on",
    "feedback_candidate",
    "feedback_emulate_stable",
]


def http(s, method, path, body=None, timeout=20):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def armed(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=240)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def status(s):
    st, body = http(s, "GET", "/api/status")
    assert st == 200, body
    return body


def program(s):
    st, body = http(s, "GET", "/api/program")
    assert st == 200, body
    return body


def wait_program(s, predicate, what: str, timeout: float = 90.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = program(s)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"never observed {what}; last state was {last!r}")


def assert_proxy_fault_stop(s):
    belt = status(s)
    assert belt["mode"] == "proxy", belt
    assert belt["relay"] is False, belt
    assert belt["speed"] == 0.0, belt
    assert belt["incline"] == 0.0, belt
    assert belt["fault"] is True, belt

    hardware = s.state()
    assert hardware["mode"] == "PROXY", hardware
    assert hardware["relay"] == 0 and hardware["io_relay"] == 0, hardware
    assert hardware["tx"] == 0 and hardware["io_tx"] == 0, hardware
    assert hardware["speed"] == 0 and hardware["incline"] == 0, hardware
    assert hardware["fault"] == 1, hardware


def inject_relay_feedback_fault(s):
    """Pin K1 to Bypass while Emulating and observe the fail-closed result."""
    assert http(s, "POST", "/api/speed", {"value": 3.0})[0] == 200
    s.wait_tx_contains(WIRE_3, timeout=60)
    idx0 = s.audit_events()[-1][0] + 1
    s.cmd_ok("QT k1 bypass")
    fault_idx = s.wait_audit("emergency:relay_feedback_invalid", since=idx0, timeout=30)
    assert_proxy_fault_stop(s)
    return fault_idx


def wait_for_qualified_bypass(s):
    """Restore the relay model, then give Bypass a measured guest-time hold."""
    s.cmd_ok("QT k1 auto")
    frame_from = s.audit_events()[-1][0] + 1
    s.wait_guest_uptime_delta(1, timeout=60)
    s.wait_audit("complete_console_frame", since=frame_from, timeout=15)


# ---------------------------------------------------------------------------
# ATTACK A — A MANUAL COMMAND PERSISTS WITHOUT CLIENT TRAFFIC.
#
# The Pi keeps a manual speed until the user changes or stops it. The device
# must do the same: silence from an HTTP client is not a treadmill fault and
# must not manufacture an emergency stop.
#
# repro: env -C tools/qemu_scenarios python3 -m pytest \
#          test_reviewer_attacks.py::test_a_manual_speed_command_survives_ten_seconds -q -s
# ---------------------------------------------------------------------------


def test_a_manual_speed_command_survives_ten_seconds(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_3, timeout=60)
    assert status(s)["mode"] == "emulate"
    n0 = len(s.audit_events())

    # Do NOTHING for ten seconds of the GUEST clock. A user who set a speed and
    # started walking sends no further request. The wait observes the same
    # monotonic clock that drives the controller; it sends nothing to the guest.
    t0 = time.monotonic()
    s.wait_guest_uptime_delta(10, timeout=90)

    after = status(s)
    events = [t for _, t in s.audit_events()[n0:] if t != "complete_console_frame"]
    expired = [e for e in events if "lease_expired" in e]
    print(f"\nATTACK A: 10 guest seconds ({time.monotonic()-t0:.1f}s wall) idle -> status={after}")
    print(f"          audit(non-frame): {events[:40]}")
    assert not expired, (
        "the belt was emergency-stopped while the client was legitimately "
        f"idle: {expired}; status={after}"
    )
    assert after["mode"] == "emulate", after
    assert after["speed"] == 3.0, after
    assert after["relay"] is True, after
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK B — STOP MUST STOP.
#
# `python/server.py::_apply_stop` unconditionally does `_hw_set_speed(0)` /
# `_hw_set_incline(0)` whether or not a program was running. Here
# `POST /api/program/stop` drives `ProgramState::stop()`'s plan (empty when no
# program is loaded) and then `control::release(Surface::Executor)`, which is a
# no-op when the EXECUTOR does not hold the lease. A belt commanded manually is
# owned by `Surface::Http`, so nothing touches it.
#
# The Android `emergencyStop()` fires setSpeed(0) alongside stopProgram(), but
# the running screen's plain Stop calls `stopProgram()` ALONE
# (TreadmillViewModel.kt:518).
#
# repro: ...::test_b_program_stop_zeroes_a_manually_commanded_belt -q -s
# ---------------------------------------------------------------------------


def test_b_program_stop_zeroes_a_manually_commanded_belt(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200, body
    after = status(s)
    print(f"\nATTACK B: after /api/program/stop -> {after}")
    assert after["speed"] == 0.0, "STOP left the belt commanded at a nonzero speed: " f"{after}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK C — ADMISSION IS NOT UNIVERSAL.
#
# `net/api.rs` claims "Every body-bearing endpoint calls `reqbudget::admit()`
# first". Six POST routes never read a body and never admit one:
# /api/program/{stop,pause,skip,prev} and /api/profile/select. IDF must then
# PURGE the undeclared-but-declared body itself, on the single httpd worker,
# before the connection can be reused or closed.
#
# So: declare a 1 MB body on /api/program/stop, send one byte, and never send
# another. Measure how long a DIFFERENT client waits for GET /api/status.
#
# repro: ...::test_c_an_unread_declared_body_cannot_park_the_worker -q -s
# ---------------------------------------------------------------------------


def test_c_an_unread_declared_body_cannot_park_the_worker(qemu):
    s = armed(qemu)

    def probe():
        t0 = time.monotonic()
        try:
            http(s, "GET", "/api/status", timeout=30)
            return time.monotonic() - t0, "ok"
        except Exception as e:  # noqa: BLE001
            return time.monotonic() - t0, repr(e)

    base, r0 = probe()
    assert r0 == "ok", r0

    raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
    tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
    try:
        tls.sendall(b"POST /api/program/stop HTTP/1.1\r\nHost: x\r\n" b"Content-Length: 1000000\r\n\r\nX")
        held, r1 = probe()
        print(f"\nATTACK C: baseline={base:.2f}s  with 1MB-declared dribbler={held:.2f}s ({r1})")
    finally:
        try:
            tls.close()
        except (OSError, ssl.SSLError):
            pass

    assert r1 == "ok", f"an unread declared body made the server unreachable: {r1}"
    assert held < base + 3.0, (
        f"one client declaring a body nobody reads parked the single httpd "
        f"worker for {held:.1f}s (baseline {base:.1f}s) — the Stop button is on "
        "this worker"
    )
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK D — CONSOLE TAKEOVER MUST STICK.
#
# A physical console button press emergency-stops and drops to PROXY
# (`emergency:console_takeover`). The safety loss must also become a sticky
# program pause. Otherwise, at the next interval boundary the executor can
# mint a fresh generation, re-acquire the free lease and re-enter emulate —
# taking the belt back from the human who just grabbed it.
#
# Two minimum-duration 10-second intervals put two boundaries inside the
# 25-second guest-time observation window.
#
# repro: ...::test_d_console_takeover_is_not_undone_by_the_running_program -q -s
# ---------------------------------------------------------------------------


def test_d_console_takeover_is_not_undone_by_the_running_program(qemu):
    s = armed(qemu)
    workout = {
        "name": "Takeover",
        "intervals": [
            {"name": "A", "duration": 10, "speed": 3.0, "incline": 0},
            {"name": "B", "duration": 10, "speed": 4.0, "incline": 0},
            {"name": "C", "duration": 600, "speed": 5.0, "incline": 0},
        ],
    }
    st, body = http(s, "POST", "/api/program/start", workout)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    ev = s.audit_events()
    idx0 = ev[-1][0] if ev else 0
    # Console button press: hmph 0 -> 2.0 mph at unchanged cadence.
    s.set_pacer_payload(synth.console_cycle_bytes(20, 0))
    takeover_idx = s.wait_audit("emergency:console_takeover", since=idx0, timeout=15)
    # Baseline at the takeover event itself, before waiting for the executor's
    # paused observation; otherwise a one-tick reacquisition can hide inside
    # the observation wait.
    tx0 = len(s.tx_bytes())
    took = status(s)
    assert took["mode"] == "proxy" and took["relay"] is False, took

    # The executor observes the loss on its next 1 s iteration. It must keep
    # the workout loaded/running for explicit Resume, but pause its clock at
    # the exact interval position where control was lost.
    paused = wait_program(
        s,
        lambda p: p["running"] is True and p["paused"] is True,
        "safety pause",
        timeout=15,
    )
    held = (
        paused["current_interval"],
        paused["interval_elapsed"],
        paused["total_elapsed"],
    )
    # Observe 25 s of GUEST time — more than two complete 10 s intervals,
    # even though QEMU wall time is elastic. Poll throughout so a forbidden
    # audit event cannot roll out of the fixed-size ring under console traffic.
    target_uptime = s.guest_uptime() + 25
    deadline = time.monotonic() + 180
    next_audit = takeover_idx + 1
    observed_events = [(takeover_idx, "emergency:console_takeover")]
    samples = []
    sampled_second = -1
    forbidden = []
    reclaimed = None
    while s.guest_uptime() < target_uptime:
        st = status(s)
        if st["mode"] != "proxy" or st["relay"] is not False:
            reclaimed = st
            break
        hw = s.state()
        guest_second = hw["t_us"] // 1_000_000
        if guest_second > sampled_second:
            sampled_second = guest_second
            p = program(s)
            samples.append(
                (
                    guest_second,
                    st["mode"],
                    st["relay"],
                    p["paused"],
                    p["current_interval"],
                    p["interval_elapsed"],
                    p["total_elapsed"],
                )
            )
        for idx, text in s.audit_events():
            if idx >= next_audit:
                next_audit = idx + 1
                if text != "complete_console_frame":
                    observed_events.append((idx, text))
                if text.startswith("lease_acquired:EXECUTOR") or text == "relay_cmd_on":
                    forbidden.append((idx, text))
        if time.monotonic() > deadline:
            raise AssertionError("guest did not advance 25 s while observing console takeover")
        time.sleep(0.2)

    after = program(s)
    post_takeover_wire = s.tx_bytes()[tx0:]
    nonzero_motor = [
        token
        for token in re.findall(rb"\[hmph:([0-9A-F]+)\]", post_takeover_wire)
        if int(token, 16) != 0
    ]
    print(f"\nATTACK D: reclaimed={reclaimed}")
    print(f"          program={after}")
    print(f"          forbidden audit={forbidden}")
    print(f"          nonzero motor frames={nonzero_motor}")
    print(f"          indexed audit={observed_events}")
    print(f"          guest samples={samples}")
    assert reclaimed is None, f"the running program took the belt back: {reclaimed}"
    assert after["running"] is True and after["paused"] is True, after
    assert (
        after["current_interval"],
        after["interval_elapsed"],
        after["total_elapsed"],
    ) == held, (
        f"safety-paused program advanced: {held} -> "
        f"{(after['current_interval'], after['interval_elapsed'], after['total_elapsed'])}"
    )
    assert not forbidden, f"executor reacquired control after takeover: {forbidden}"
    assert not nonzero_motor, f"nonzero motor frames after takeover: {nonzero_motor}"

    # Restoring healthy console intent is still insufficient by itself. The
    # one user action below is the explicit Resume (pause is a toggle), and it
    # must continue from the exact safety-paused position.
    s.set_pacer_payload(synth.console_cycle_bytes(0, 0))
    frame_from = s.audit_events()[-1][0] + 1
    s.wait_audit("complete_console_frame", since=frame_from, timeout=15)
    entry_from = s.audit_events()[-1][0] + 1
    tx1 = len(s.tx_bytes())
    st, resumed = http(s, "POST", "/api/program/pause")
    assert st == 200 and resumed["paused"] is False, resumed
    assert (
        resumed["current_interval"],
        resumed["interval_elapsed"],
        resumed["total_elapsed"],
    ) == held, (
        f"explicit Resume changed the held position: {held} -> "
        f"{(resumed['current_interval'], resumed['interval_elapsed'], resumed['total_elapsed'])}"
    )
    s.wait_audit_sequence(ENTRY_SEQUENCE, since=entry_from, timeout=45)
    requested_speed = workout["intervals"][held[0]]["speed"]
    requested_wire = f"[hmph:{int(requested_speed * 100):X}]".encode()
    assert requested_wire != WIRE_0
    s.wait_tx_contains(requested_wire, timeout=45, offset=tx1)
    resumed_belt = status(s)
    assert resumed_belt["mode"] == "emulate" and resumed_belt["relay"] is True, resumed_belt
    assert resumed_belt["speed"] == requested_speed, resumed_belt
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK E — EXPLICIT, HEALTH-GATED FAULT RECOVERY.
#
# Force a real relay-feedback fault while the bridge is live. Restoring the
# physical prerequisites must not move the belt by itself; one fresh positive
# speed request is the acknowledgement allowed to clear the latch and perform
# the complete normal entry choreography.
#
# repro: ...::test_e_a_latched_fault_is_recoverable -q -s
# ---------------------------------------------------------------------------


def test_e_a_latched_fault_is_recoverable(qemu):
    s = armed(qemu)
    fault_idx = inject_relay_feedback_fault(s)
    tx0 = len(s.tx_bytes())
    wait_for_qualified_bypass(s)
    assert_proxy_fault_stop(s)
    s.assert_no_audit(
        lambda text: text == "fault_recovery_accepted" or text == "relay_cmd_on",
        since=fault_idx + 1,
        label="before an explicit recovery request",
    )

    entry_from = s.audit_events()[-1][0] + 1
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, (st, body)
    recovery_sequence = ["fault_recovery_accepted", *ENTRY_SEQUENCE]
    s.wait_audit_sequence(recovery_sequence, since=entry_from, timeout=45)
    s.wait_tx_contains(WIRE_2, timeout=45, offset=tx0)
    after = status(s)
    print(f"\nATTACK E: reply={st} {body}")
    print(f"          status={after}")
    assert after["mode"] == "emulate" and after["relay"] is True, after
    assert after["speed"] == 2.0 and after["fault"] is False, after
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK F — UNHEALTHY EXPLICIT RECOVERY NEVER CLEARS THE LATCH.
#
# A recovery request is an attempt, not a reset button. With TREAD_OK held low,
# every new positive request must fail truthfully, keep the bridge in Proxy at
# zero with relay/TX off, and leave the original relay-feedback fault latched.
# ---------------------------------------------------------------------------


def test_f_unhealthy_recovery_requests_keep_the_fault_latched(qemu):
    s = armed(qemu)
    fault_idx = inject_relay_feedback_fault(s)
    tx0 = len(s.tx_bytes())
    s.cmd_ok("QT k1 auto")
    s.cmd_ok("QT tread 0")

    rejected = []
    for attempt in range(3):
        request_from = s.audit_events()[-1][0] + 1
        st, body = http(s, "POST", "/api/speed", {"value": 2.0})
        rejected.append((st, body))
        assert st == 409 and body["ok"] is False, (attempt, st, body)
        s.wait_audit("recovery_rejected:tread_not_ok", since=request_from, timeout=15)
        assert_proxy_fault_stop(s)

    s.assert_no_audit(
        lambda text: text == "fault_recovery_accepted" or text == "relay_cmd_on",
        since=fault_idx + 1,
        label="across unhealthy explicit recovery requests",
    )
    assert WIRE_2 not in s.tx_bytes()[tx0:]
    print(f"\nATTACK F: rejected explicit recoveries={rejected}")
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK G — INTEGER OVERFLOW IN THE INCLINE CONVERSION.
#
# `net/api.rs::motion_handler` does `InclineHalfPct::new(hundredths * 2 / 100)`.
# `parse_value_hundredths` bounds `hundredths` to +-i32::MAX, so `* 2` overflows
# i32. `[profile.release]` in esp32tap/Cargo.toml sets no `overflow-checks`, so
# release wraps (and a build that ever turned them ON would PANIC -> abort ->
# reboot -> relay drop mid-run).
#
# -21474835.98 -> hundredths = -2147483598 -> *2 wraps to +100 -> /100 = 1 ->
# 0.5% incline. A request asking for a wildly out-of-range NEGATIVE incline is
# answered 200 and MOVES THE INCLINE, instead of being rejected 409 by the
# controller's 0..=30 half-percent clamp.
#
# repro: ...::test_g_incline_conversion_does_not_wrap -q -s
# ---------------------------------------------------------------------------


def test_g_incline_conversion_does_not_wrap(qemu):
    s = armed(qemu)
    assert http(s, "POST", "/api/speed", {"value": 3.0})[0] == 200
    s.wait_tx_contains(WIRE_3, timeout=60)
    assert http(s, "POST", "/api/incline", {"value": 5.0})[0] == 200
    before = status(s)

    st, body = http(s, "POST", "/api/incline", {"value": -21474835.98})
    after = status(s)
    print(f"\nATTACK G: reply={st} body={body}")
    print(f"          before={before['incline']}  after={after['incline']}")
    assert st == 409, (
        "an out-of-range incline was ACCEPTED after the i32 conversion wrapped: "
        f"{st} {body}; incline {before['incline']} -> {after['incline']}"
    )
    s.stop_pacer()
