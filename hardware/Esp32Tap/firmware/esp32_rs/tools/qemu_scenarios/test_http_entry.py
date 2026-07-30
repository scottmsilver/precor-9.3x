"""Slice 2 proof: an HTTP POST causes a REAL relay transfer.

The hardware preconditions for emulate entry (TREAD_OK asserted, K1 reporting
BYPASS, a fresh console) are HARDWARE state, so the QEMU shim scripts them
exactly as it does for the QT-driven scenarios. The COMMAND, though, arrives
over HTTP — that is the thing under test.
"""

import json
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10

# Slice 3 made the server HTTPS-only — there is no plaintext listener left to
# fall back to — so this scenario now drives the same endpoints over TLS. What
# it proves is unchanged: a request from the network causes a real transfer.
http = httpc.request


def http_result(s, method, path, body=None, timeout=20):
    """Treat an application-level HTTP refusal as a result."""
    try:
        return http(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# The Android app's `StatusMessage` (kotlin/.../data/remote/models/
# ServerMessages.kt) declares these SIX with no kotlinx default, which makes
# every one of them REQUIRED: `coerceInputValues` can only rewrite an explicit
# null into a default that already exists, it cannot invent one. A body missing
# any of them raises MissingFieldException inside the app's `runCatching`, so
# the belt still moves and the UI keeps only its optimistic local echo — a
# treadmill screen showing a speed the belt is not running at. Typed, not just
# present, because `LenientBoolSerializer` would silently coerce the string
# "proxy" to `false` and read as valid.
APP_REQUIRED_STATUS_FIELDS = {
    "proxy": bool,
    "emulate": bool,
    "emu_speed": int,  # TENTHS of mph — the app divides by 10 in three places
    "emu_speed_mph": float,
    "emu_incline": float,
    "treadmill_connected": bool,
}


def assert_app_status_shape(body):
    for key, typ in APP_REQUIRED_STATUS_FIELDS.items():
        assert key in body, f"/api/status is missing {key!r}, which the app requires: {body}"
        got = body[key]
        # bool is a subclass of int in Python; check it first and exactly.
        if typ is bool:
            assert isinstance(got, bool), f"{key} must be a JSON boolean, got {got!r}"
        elif typ is int:
            assert isinstance(got, int) and not isinstance(got, bool), f"{key} must be a JSON integer, got {got!r}"
        else:
            assert isinstance(got, (int, float)) and not isinstance(
                got, bool
            ), f"{key} must be a JSON number, got {got!r}"
    # emu_speed is tenths, emu_speed_mph is mph: they must agree, or the app
    # renders one of them 10x wrong.
    assert abs(body["emu_speed"] / 10.0 - body["emu_speed_mph"]) < 1e-9, body
    assert body["proxy"] is not body["emulate"], body


def test_http_speed_causes_a_real_relay_transfer(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

    # Hardware preconditions, scripted by the shim (not by HTTP).
    s.cmd_ok("QT tread 1")
    # AUTO: the shim's relay model tracks RELAY_CMD with a 2 ms
    # break-before-make, so feedback can actually qualify the transfer.
    # Pinning it to bypass would stall entry in ENTRY_WAIT_FEEDBACK forever.
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    st, body = http(s, "GET", "/api/status")
    assert st == 200 and body["mode"] == "proxy", body
    assert body["relay"] is False
    assert_app_status_shape(body)
    # ...and `treadmill_connected` is a real observation, not a constant: the
    # pacer has been delivering console frames for the last few hundred ms.
    assert body["treadmill_connected"] is True, body

    n0 = len(s.audit_events())
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200 and body["ok"] is True, body
    # The app types setSpeed/setIncline as returning a full StatusMessage, so
    # the POST reply must be a status body too, not a bare ok.
    assert_app_status_shape(body)

    # THE CLAIM: the HTTP command drove a real transfer.
    s.wait_audit("relay_cmd_on", since=n0, timeout=30)
    s.wait_audit("tx_enable_on", since=n0, timeout=5)

    # ...and the first frame after entry carries ZERO motion (PLAN entry step 6).
    s.wait_tx_contains(b"[hmph:0]", timeout=20)
    s.wait_tx_contains(b"[inc:0]", timeout=20)
    s.stop_pacer()


def test_positive_speed_reports_rejected_recovery_truthfully(qemu):
    """A fresh positive command may recover a latch only while health is good.

    BOTH_CLOSED is deliberately held active here, so recovery must fail.  The
    accepted motion value cannot be advertised as reachable while the relay
    remains in Proxy.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    s.cmd_ok("QT tread 1")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    s.cmd_ok("QT k1 closed")
    s.wait_audit("emergency:relay_feedback_both_closed", timeout=30)

    st, body = http_result(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 409 and body["ok"] is False, (st, body)

    st, after = http(s, "GET", "/api/status")
    assert st == 200, after
    assert after["mode"] == "proxy", after
    assert after["relay"] is False, after
    assert after["speed"] == 0.0, after
    s.stop_pacer()
