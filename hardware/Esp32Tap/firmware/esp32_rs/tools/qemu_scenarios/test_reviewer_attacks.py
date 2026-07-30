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
import threading
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
WIRE_INC_5 = b"[inc:A]"  # 5.0% = 10 half-percent units
WIRE_INC_0 = b"[inc:0]"

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

EXIT_SEQUENCE = [
    "send_and_finish_complete_zero_frame",
    "wait_exit_gap",
    "relay_cmd_off",
    "feedback_candidate",
    "feedback_bypass_stable",
    "tx_enable_off",
    "lease_released",
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


def wait_guest_time_delta(s, delta_us: int, timeout: float):
    """Wait on the controller's direct monotonic timestamp from QTSTATE."""
    start = s.state()["t_us"]
    last = start
    deadline = time.monotonic() + timeout
    while last - start < delta_us:
        if time.monotonic() > deadline:
            raise AssertionError(
                f"guest monotonic advanced only {last - start}us; wanted {delta_us}us"
            )
        time.sleep(0.05)
        last = s.state()["t_us"]
    return start, last


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
    wait_guest_time_delta(s, 1_000_000, timeout=60)
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
    guest_start, guest_end = wait_guest_time_delta(s, 10_000_000, timeout=90)
    assert guest_end - guest_start >= 10_000_000

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
# Stop is one public contract regardless of who owns the belt: command BOTH
# axes to zero, put those zeros on the motor wire, complete the normal relay
# exit to Proxy, and release ownership. This exercises the easy-to-miss manual
# case where ProgramState itself has no running plan to stop.
#
# repro: ...::test_b_program_stop_zeroes_a_manually_commanded_belt -q -s
# ---------------------------------------------------------------------------


def test_b_program_stop_zeroes_a_manually_commanded_belt(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_3, timeout=60)
    st, body = http(s, "POST", "/api/incline", {"value": 5.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_INC_5, timeout=30)

    audit_from = s.audit_events()[-1][0] + 1
    tx_from = len(s.tx_bytes())
    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200, body
    s.wait_audit_sequence(EXIT_SEQUENCE, since=audit_from, timeout=45)
    s.wait_tx_contains(WIRE_0, timeout=20, offset=tx_from)
    s.wait_tx_contains(WIRE_INC_0, timeout=20, offset=tx_from)
    after = status(s)
    print(f"\nATTACK B: after /api/program/stop -> {after}")
    assert after["speed"] == 0.0 and after["incline"] == 0.0, after
    assert after["mode"] == "proxy" and after["relay"] is False, after
    hardware = s.state()
    assert hardware["mode"] == "PROXY", hardware
    assert hardware["relay"] == 0 and hardware["tx"] == 0, hardware
    assert hardware["io_relay"] == 0 and hardware["io_tx"] == 0, hardware
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK C — ADMISSION IS NOT UNIVERSAL.
#
# Program stop/pause/skip/prev, stored-program/workout load, and HRM actions
# have no request-body contract. Profile select DOES carry `{"id":...}`, but
# this single-profile implementation used to ignore rather than drain it. If a
# handler answers without consuming or explicitly rejecting its declared body,
# IDF purges the unread bytes on the sole HTTP worker.
#
# One byte followed by silence is not the attack: IDF's per-receive timeout
# ends that connection after about a second. This client sends one byte every
# 400 ms, before each timeout, while a DIFFERENT client presses Stop. Each
# bodyless route must bound that abuse without changing its empty-body API.
#
# repro: ...::test_c_an_unread_declared_body_cannot_park_the_worker -q -s
# ---------------------------------------------------------------------------


def test_c_an_unread_declared_body_cannot_park_the_worker(qemu):
    s = armed(qemu)

    def probe_stop(timeout=8):
        t0 = time.monotonic()
        try:
            http(s, "POST", "/api/program/stop", timeout=timeout)
            return time.monotonic() - t0, "ok"
        except Exception as e:  # noqa: BLE001
            return time.monotonic() - t0, repr(e)

    base, r0 = probe_stop()
    assert r0 == "ok", r0
    assert http(s, "POST", "/api/speed", {"value": 3.0})[0] == 200
    s.wait_tx_contains(WIRE_3, timeout=60)

    bodyless_or_ignored_body_posts = (
        "/api/profile/select",
        "/api/program/stop",
        "/api/program/pause",
        "/api/program/skip",
        "/api/program/prev",
        "/api/programs/history/missing/load",
        "/api/programs/history/missing/resume",
        "/api/workouts/missing/load",
        # Already uses respond_and_close; kept in the inventory so that working
        # reference cannot silently regress while the other routes converge.
        "/api/hrm/forget",
        "/api/hrm/scan",
    )
    outcomes = []
    for path in bodyless_or_ignored_body_posts:
        raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
        tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
        stopped = threading.Event()
        sender_errors = []

        def keep_each_receive_alive():
            next_send = time.monotonic() + 0.4
            while not stopped.wait(max(0.0, next_send - time.monotonic())):
                try:
                    tls.sendall(b"X")
                except (OSError, ssl.SSLError) as e:
                    sender_errors.append(repr(e))
                    return
                next_send += 0.4

        try:
            tls.sendall(
                f"POST {path} HTTP/1.1\r\nHost: x\r\n"
                "Content-Length: 1000000\r\n\r\nX".encode()
            )
            sender = threading.Thread(target=keep_each_receive_alive, daemon=True)
            sender.start()
            held, result = probe_stop()
            outcomes.append((path, held, result, list(sender_errors)))
        finally:
            stopped.set()
            try:
                tls.close()
            except (OSError, ssl.SSLError):
                pass
            if "sender" in locals():
                sender.join(timeout=2)
                del sender

        assert result == "ok", (
            f"{path} let an unread declared body make Stop unreachable: {result}; "
            f"sender={sender_errors}"
        )
        assert held < base + 3.0, (
            f"{path} let a 400ms-byte dribbler park the sole HTTP worker for "
            f"{held:.1f}s (Stop baseline {base:.1f}s)"
        )

    print(f"\nATTACK C: timeout-refreshing dribbler outcomes={outcomes}")
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
    takeover_t_us = s.state()["t_us"]
    observed_t_us = takeover_t_us
    deadline = time.monotonic() + 180
    next_audit = takeover_idx + 1
    observed_events = [(takeover_idx, "emergency:console_takeover")]
    samples = []
    sampled_second = -1
    forbidden = []
    reclaimed = None
    while observed_t_us - takeover_t_us < 25_000_000:
        st = status(s)
        if st["mode"] != "proxy" or st["relay"] is not False:
            reclaimed = st
            break
        hw = s.state()
        observed_t_us = hw["t_us"]
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
    assert observed_t_us - takeover_t_us >= 25_000_000
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
    print(f"          guest monotonic delta_us={observed_t_us - takeover_t_us}")
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
# ATTACK G — INCLINE CONVERSION IS TOTAL AND REJECTION IS ATOMIC.
#
# The edge converts parsed hundredths to half-percent units with total `/ 50`
# arithmetic, then leaves range enforcement to the safety controller. A
# rejected extreme value must be atomic: 409, unchanged status, and no wire
# frame carrying any newly derived incline.
#
# repro: ...::test_g_incline_conversion_does_not_wrap -q -s
# ---------------------------------------------------------------------------


def test_g_incline_conversion_does_not_wrap(qemu):
    s = armed(qemu)
    assert http(s, "POST", "/api/speed", {"value": 3.0})[0] == 200
    s.wait_tx_contains(WIRE_3, timeout=60)
    assert http(s, "POST", "/api/incline", {"value": 5.0})[0] == 200
    s.wait_tx_contains(WIRE_INC_5, timeout=30)
    before = status(s)

    tx_from = len(s.tx_bytes())
    st, body = http(s, "POST", "/api/incline", {"value": -21474835.98})
    after = status(s)
    wait_guest_time_delta(s, 1_000_000, timeout=60)
    post_rejection = s.tx_bytes()[tx_from:]
    incline_tokens = re.findall(rb"\[inc:([0-9A-F]+)\]", post_rejection)
    print(f"\nATTACK G: reply={st} body={body}")
    print(f"          before={before['incline']}  after={after['incline']}")
    print(f"          post-rejection incline wire={incline_tokens}")
    assert st == 409, (st, body)
    assert after["incline"] == before["incline"], (before, after)
    assert incline_tokens, "no post-rejection motor cycle was captured"
    assert set(incline_tokens) == {b"A"}, (
        f"rejected incline changed the motor wire: {incline_tokens}"
    )
    s.stop_pacer()
