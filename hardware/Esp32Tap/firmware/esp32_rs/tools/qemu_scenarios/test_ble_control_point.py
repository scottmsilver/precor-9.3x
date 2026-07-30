"""The BLE tier's BELT EDGE, driven end to end — with no radio anywhere.

WHAT THIS FILE IS, AND WHAT IT IS NOT
=====================================

It is NOT proof that Bluetooth works. QEMU has no BLE radio and no board
exists. Nothing here advertises, connects, pairs, notifies or indicates, and
nothing here touches an mbuf. Bead `precor-9_3x-l0h` still owns all of that.

It IS proof of everything BELOW the radio. A Control Point write's journey is:

    ATT PDU -> access_cb (mbuf copy) -> plan -> effect_of -> apply
            -> control::command -> lease -> clamps -> auto-emulate -> BELT

Only the first two steps involve FFI or a radio. From `plan` down it is
ordinary Rust operating on the real safety controller and the real relay, so
the QEMU-test-only verb `QT ble_cp <hex>` can drive the whole of it on a
machine with no Bluetooth adapter at all. That verb is the same two calls
`on_control_point` makes, in the same order (see `qemu_test/shim_task.rs`).

WHY IT EXISTS
=============

Two REAL defects in this tier were, until this file, establishable only by
READING the source — which is the weakest form of knowing anything:

  1. FTMS Stop was DENIED by the lease exactly when it mattered most. Every
     effect, Stop included, went to `control::command(Surface::Http, ..)`.
     While the interval executor owned the belt that returns
     `Reject::NotOwner`, so a user running a 30-minute program at 6 mph who
     pressed stop in Zwift was answered `80 08 04` (FAILED) and the belt kept
     running. On the Pi, `treadmill::send_stop` wrote straight to
     `treadmill_io` and the zero always landed.

  2. A SetTargetSpeed carried a STALE incline. The surface read the belt under
     one lock hold and commanded under a second, and `motion_for` carries the
     other axis through unchanged — so an incline committed by the httpd task
     in between was silently reverted.

HOW MUCH OF THAT THIS FILE ACTUALLY PROVES, stated precisely because the whole
point of the file is to stop taking claims on trust:

  * (1) IS PROVEN. `test_stop_zeroes_the_belt_while_a_program_owns_the_lease`
    and `test_stop_hands_the_belt_back_so_a_manual_command_works_again` were
    both RUN AGAINST THE PRE-FIX IMAGE and both FAILED with `RESULT_FAILED`
    and a belt still running; they pass on the fix.
  * (2) IS NOT REPRODUCED HERE, and the test that covers its ground says so in
    its own docstring. The defect was a window between two lock acquisitions;
    closing it removed the window rather than making a race come out the other
    way, and a harness test for a window that narrow would be an intermittent.
    What is asserted is the carry-through invariant itself.

Everything else below (the negative-inclination clamp, the km/h conversion, the
zero-length write, the malformed-write bounds, the relay not cycling) is
asserted against the real safety controller and the real relay.
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

# FTMS result codes (spec Table 4.24), as `ble_core::ftms` defines them.
RESULT_SUCCESS = 1
RESULT_NOT_SUPPORTED = 2
RESULT_INVALID_PARAM = 3
RESULT_FAILED = 4

# Control Point opcodes.
OP_REQUEST_CONTROL = 0x00
OP_SET_TARGET_SPEED = 0x02
OP_SET_TARGET_INCLINATION = 0x03
OP_START_OR_RESUME = 0x07
OP_STOP_OR_PAUSE = 0x08

# Set-Target-Speed parameters (km/h x 100, LE) that decode to an exact mph.
#
# THE ROUND TRIP IS LOSSY AND THE LOSS IS THE DAEMON'S, pinned deliberately in
# `ble_core` so a phone paired with the Pi sees the same numbers. Encoding is
# `mph_tenths * 1609 / 100` and decoding is `kmh_hundredths * 100 / 1609`, BOTH
# truncating, so encoding 4.0 mph gives 643 and decoding 643 gives 3.9 — a
# round trip loses a tenth. These are the SMALLEST parameters that decode to
# each target, which is what a real client sending "4.0 mph" as its own rounded
# km/h would land on.
KMH = {
    2.0: (0x42, 0x01),  # 322
    3.0: (0xE3, 0x01),  # 483
    4.0: (0x84, 0x02),  # 644
    5.0: (0x25, 0x03),  # 805
    6.0: (0xC6, 0x03),  # 966
}


def http(s, method, path, body=None, timeout=20):
    """A 4xx/5xx is a RESULT here, not an exception — the device REFUSING
    things is half of what this file checks."""
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def armed(qemu):
    """A booted guest with the HARDWARE preconditions for emulate entry met."""
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def ble_cp(s, *bytes_: int) -> tuple[int, int]:
    """Drive one Control Point write. Returns (echoed opcode, result code).

    The reply line is `QTOK ble_cp op=<n> result=<n>`; `cmd_ok` waits for the
    `QTOK ble_cp ` prefix, so this cannot pick up a stale line from an earlier
    write in the same session.
    """
    args = " ".join(f"{b:02X}" for b in bytes_)
    line = s.cmd_ok(f"QT ble_cp {args}".rstrip(), timeout=30)
    fields = dict(tok.split("=", 1) for tok in line.split() if "=" in tok)
    return int(fields["op"]), int(fields["result"])


def status(s) -> dict:
    st, body = http(s, "GET", "/api/status")
    assert st == 200, body
    return body


def wait_for(s, predicate, what: str, timeout: float = 60.0):
    """Poll GET /api/status until `predicate(state)`. Bounded by the guest's
    own reported state, never by a bare sleep."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = status(s)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"never observed {what}; last status was {last!r}")


# ---------------------------------------------------------------------------
# THE DEFECT THIS FILE WAS WRITTEN FOR
# ---------------------------------------------------------------------------


def test_stop_zeroes_the_belt_while_a_program_owns_the_lease(qemu):
    """A BLE Stop must land even when the interval executor holds the belt.

    This is the one Control Point opcode whose failure mode is safety-shaped
    and the only stop control a BLE-only peer has. `POST /api/program/stop` is
    not something a phone on Bluetooth can call.
    """
    s = armed(qemu)

    program = {
        "name": "QEMU Long Run",
        "intervals": [{"name": "Steady", "duration": 1800, "speed": 6.0, "incline": 0}],
    }
    st, body = http(s, "POST", "/api/program/start", program)
    assert st == 200 and body["running"] is True, body

    # The belt is genuinely moving under the EXECUTOR's lease before the stop.
    wait_for(s, lambda x: x["speed"] == 6.0, "the program's belt speed")
    s.wait_tx_contains(b"[hmph:258]", timeout=60)  # 6.00 mph = 600 = 0x258

    # A manual HTTP command is refused right now — which is the CORRECT
    # arbitration and is exactly what used to swallow the BLE stop too.
    st, _ = http(s, "POST", "/api/speed", {"value": 1.0})
    assert st == 409, f"a program must still refuse a manual command, got {st}"

    # THE ASSERTION. `08 01` = StopOrPause(stop).
    op, result = ble_cp(s, OP_STOP_OR_PAUSE, 0x01)
    assert op == OP_STOP_OR_PAUSE
    assert result == RESULT_SUCCESS, (
        f"FTMS Stop answered {result} (RESULT_FAILED=4 means the lease denied it). "
        "The belt is still running at the program's speed and a BLE-only peer "
        "has no other way to stop it."
    )

    # The belt is ACTUALLY stopped, not merely answered SUCCESS...
    wait_for(s, lambda x: x["speed"] == 0.0, "the belt at zero after a BLE stop")
    # ...and the program is over, the way POST /api/program/stop leaves it.
    st, prog = http(s, "GET", "/api/program")
    assert st == 200 and prog["running"] is False, prog
    s.stop_pacer()


def test_stop_hands_the_belt_back_so_a_manual_command_works_again(qemu):
    """The stop RELEASES, so the surface a user reaches for next is not stuck.

    On the Pi, `send_stop`'s third command is `emulate:false` — it returns the
    bridge to copper so the physical console buttons work. Here the equivalent
    is the lease release inside `stop_the_belt`; what is observable from the
    outside is that the belt can be commanded again afterwards.
    """
    s = armed(qemu)
    program = {
        "name": "QEMU Long Run",
        "intervals": [{"name": "Steady", "duration": 1800, "speed": 4.0, "incline": 0}],
    }
    st, body = http(s, "POST", "/api/program/start", program)
    assert st == 200, body
    wait_for(s, lambda x: x["speed"] == 4.0, "the program's belt speed")

    op, result = ble_cp(s, OP_STOP_OR_PAUSE, 0x01)
    assert (op, result) == (OP_STOP_OR_PAUSE, RESULT_SUCCESS)
    wait_for(s, lambda x: x["speed"] == 0.0, "the belt at zero")

    # The release is a GAP-SAFE EXIT, not an instant handover: the controller
    # holds the lease until the exit completes and the relay is back on copper.
    # Waiting for the guest's own reported mode is the fact that says it did.
    wait_for(s, lambda x: x["mode"] == "proxy", "the controller back in proxy")

    # The lease came back: a manual command is accepted where it was 409 above.
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, (st, body)
    wait_for(s, lambda x: x["speed"] == 2.0, "the manual speed after a BLE stop")
    s.stop_pacer()


def test_each_axis_survives_a_write_to_the_other(qemu):
    """`motion_for` carries the OTHER axis through unchanged, so a speed write
    must preserve the incline and vice versa.

    WHAT THIS PROVES AND WHAT IT DOES NOT. It proves the CARRY-THROUGH is
    correctly wired end to end — that a `SetTargetSpeed` reaching the belt does
    not zero or stale the incline, which is the "silent cross-talk" `ble_core`'s
    own doc on `motion_for` says a host test is for, here asserted against the
    real controller with the two surfaces genuinely alternating.

    It does NOT reproduce the interleaving RACE that motivated the fix. That
    defect was a window between two lock acquisitions: the surface read the
    belt under one hold and commanded under a second, and the httpd task shares
    `Surface::Http` by design, so only the mutex serialised them. Hitting a
    window that narrow from the harness would need a test that is
    timing-dependent by construction, and a test that only sometimes catches a
    bug is worse than one that never does — it costs an investigation every
    time it fires. The window was closed the way `net::api`'s motion handler
    already had it closed, by building `BeltNow` inside the same `lock()` hold
    that commands: there is no window left to race. Verified by reading
    `ble::ftms::apply` against `net::api`'s handler, and stated here rather
    than implied by a green test that would pass either way.
    """
    s = armed(qemu)

    # Bring the belt up manually so both axes are non-zero and owned by
    # Surface::Http — the case where no lease refusal can mask the bug.
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200 and body["ok"] is True, body
    wait_for(s, lambda x: x["speed"] == 3.0, "the manual belt speed")
    st, body = http(s, "POST", "/api/incline", {"value": 2.0})
    assert st == 200, body
    wait_for(s, lambda x: x["incline"] == 2.0, "the manual incline")

    for _ in range(6):
        # HTTP raises the incline...
        st, _ = http(s, "POST", "/api/incline", {"value": 8.0})
        assert st == 200
        # ...and a BLE speed write must NOT drag it back down. See KMH below
        # for why 4.0 mph is written as 644 and not 643.
        op, result = ble_cp(s, OP_SET_TARGET_SPEED, *KMH[4.0])
        assert op == OP_SET_TARGET_SPEED
        assert result == RESULT_SUCCESS, result
        st = status(s)
        assert st["incline"] == 8.0, (
            f"a BLE SetTargetSpeed reverted the incline to {st['incline']} — it "
            "carried an incline read under a DIFFERENT lock hold than the one "
            "that commanded it"
        )

        # And the mirror image: a BLE incline write must not drop the speed
        # the HTTP surface just set.
        st, _ = http(s, "POST", "/api/speed", {"value": 5.0})
        assert st == 200
        op, result = ble_cp(s, OP_SET_TARGET_INCLINATION, 0x1E, 0x00)  # 30 = 3.0%
        assert (op, result) == (OP_SET_TARGET_INCLINATION, RESULT_SUCCESS)
        st = status(s)
        assert st["speed"] == 5.0, f"a BLE SetTargetInclination reverted the speed to {st['speed']}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# Conversion and clamping, at the belt rather than in a host test
# ---------------------------------------------------------------------------


def test_a_descent_flattens_the_belt_instead_of_being_refused(qemu):
    """A route-simulating app on a downhill must not leave the belt uphill.

    `03 9C FF` is SetTargetInclination(-10.0%). The daemon clamped it to 0.0%
    and answered SUCCESS; this device used to pass the negative through to the
    controller, which refuses `incline < 0`, so the belt STAYED on the previous
    uphill grade for the whole descent while the app collected error
    indications.
    """
    s = armed(qemu)

    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    wait_for(s, lambda x: x["speed"] == 3.0, "the belt running")
    st, _ = http(s, "POST", "/api/incline", {"value": 8.0})
    assert st == 200
    wait_for(s, lambda x: x["incline"] == 8.0, "the uphill grade")

    op, result = ble_cp(s, OP_SET_TARGET_INCLINATION, 0x9C, 0xFF)  # -100 tenths
    assert op == OP_SET_TARGET_INCLINATION
    assert result == RESULT_SUCCESS, f"a descent was answered {result}; the belt is stuck on the last hill"
    wait_for(s, lambda x: x["incline"] == 0.0, "the belt flattened for the descent")
    # The speed is untouched — this was an inclination write.
    assert status(s)["speed"] == 3.0
    s.stop_pacer()


def test_above_the_range_is_refused_rather_than_silently_substituted(qemu):
    """The other half of the asymmetry, at the belt.

    40.0% is out of range. The Pi silently substituted 15% and moved the belt
    at a grade nobody asked for; this device refuses and says so.
    """
    s = armed(qemu)
    st, _ = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200
    wait_for(s, lambda x: x["speed"] == 3.0, "the belt running")
    before = status(s)["incline"]

    op, result = ble_cp(s, OP_SET_TARGET_INCLINATION, 0x90, 0x01)  # 400 tenths
    assert op == OP_SET_TARGET_INCLINATION
    assert result == RESULT_INVALID_PARAM, result
    assert status(s)["incline"] == before, "a refused write must change nothing"
    s.stop_pacer()


def test_kmh_to_mph_conversion_happens_at_the_edge(qemu):
    """FTMS speaks km/h x 100 and the belt speaks tenths of mph.

    `ble_core` pins the arithmetic over the whole u16 domain in a host test;
    this asserts the conversion is actually WIRED — that the number the peer
    wrote comes back out of `/api/status` as the mph it means, rather than as
    a km/h value the belt would run 60% too fast at.
    """
    s = armed(qemu)
    op, result = ble_cp(s, OP_SET_TARGET_SPEED, *KMH[4.0])
    assert (op, result) == (OP_SET_TARGET_SPEED, RESULT_SUCCESS)
    wait_for(s, lambda x: x["speed"] == 4.0, "4.0 mph from a 6.44 km/h write")
    s.wait_tx_contains(b"[hmph:190]", timeout=60)  # 4.00 mph = 400 = 0x190
    s.stop_pacer()


# ---------------------------------------------------------------------------
# Untrusted input
# ---------------------------------------------------------------------------


def test_a_zero_length_write_is_not_answered_as_opcode_zero(qemu):
    """An empty ATT write carries NO opcode, and 0x00 is RequestControl.

    The old recovery was `bytes.first().copied().unwrap_or(0)`, so a zero-length
    write was answered `[0x80, 0x00, 0x02]` — "RequestControl not supported" —
    naming an opcode the peer never sent, for the ONE opcode this device always
    accepts. A client debugging its handshake was told the exact opposite of
    the truth.
    """
    s = armed(qemu)
    op, result = ble_cp(s)
    assert result == RESULT_INVALID_PARAM, (
        f"a zero-length write was answered {result}; NOT_SUPPORTED(2) alongside "
        "op=0 is the old bug, which claims RequestControl is unsupported"
    )

    # ...and RequestControl itself is still accepted, which is what makes the
    # old answer a lie rather than merely imprecise.
    op, result = ble_cp(s, OP_REQUEST_CONTROL)
    assert (op, result) == (OP_REQUEST_CONTROL, RESULT_SUCCESS)
    s.stop_pacer()


def test_garbage_and_truncated_writes_never_reach_the_belt(qemu):
    """Every malformed shape a peer can send, against a moving belt.

    UNTRUSTED INPUT: anything in radio range can write this characteristic
    without pairing. The parse is total (proven exhaustively over the 1-, 2-
    and 3-byte domains in `ble_core`); what is asserted HERE is that a refused
    write leaves the belt exactly where it was and does not fault the device.
    """
    s = armed(qemu)
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200 and body["ok"] is True, body
    wait_for(s, lambda x: x["speed"] == 3.0, "the belt running")
    n0 = len(s.audit_events())

    malformed = [
        (0xFF,),  # unknown opcode
        (0x99, 0x01, 0x02),  # unknown opcode with a payload
        (OP_SET_TARGET_SPEED,),  # opcode, no parameter
        (OP_SET_TARGET_SPEED, 0x01),  # parameter one byte short
        (OP_SET_TARGET_INCLINATION, 0x01),  # parameter one byte short
        (OP_STOP_OR_PAUSE,),  # opcode, no parameter
    ]
    for write in malformed:
        op, result = ble_cp(s, *write)
        assert result == RESULT_NOT_SUPPORTED, (write, result)
        assert op == write[0], (write, op)

    # The belt never moved and nothing latched.
    assert status(s)["speed"] == 3.0
    s.assert_no_audit(
        lambda t: t.startswith("emergency:") or t.startswith("motion_rejected:"),
        since=n0,
        label="across malformed Control Point writes",
    )
    s.stop_pacer()


def test_repeated_writes_do_not_cycle_the_relay(qemu):
    """A peer can write at ATT rate. That must not be a relay cycle each time.

    Same property `test_program.py` pins for the HTTP surface: the lease is
    REUSED while it is still ours, so a stream of Control Point writes commands
    motion without re-minting a generation and superseding itself — which would
    emergency-stop the treadmill mid-stride, once per write.
    """
    s = armed(qemu)
    op, result = ble_cp(s, OP_SET_TARGET_SPEED, *KMH[4.0])
    assert (op, result) == (OP_SET_TARGET_SPEED, RESULT_SUCCESS)
    wait_for(s, lambda x: x["speed"] == 4.0, "the belt at 4.0 mph")
    n0 = len(s.audit_events())

    for mph in (2.0, 3.0, 5.0):
        op, result = ble_cp(s, OP_SET_TARGET_SPEED, *KMH[mph])
        assert (op, result) == (OP_SET_TARGET_SPEED, RESULT_SUCCESS), (mph, result)
        wait_for(s, lambda x, m=mph: x["speed"] == m, f"the belt at {mph} mph")

    s.assert_no_audit(
        lambda t: t.startswith("emergency:"),
        since=n0,
        label="across repeated Control Point writes",
    )
    s.assert_no_audit(
        lambda t: t in ("relay_cmd_on", "relay_cmd_off"),
        since=n0,
        label="across repeated Control Point writes",
    )
    s.stop_pacer()
