"""Slice 2 proof: an HTTP POST causes a REAL relay transfer.

The hardware preconditions for emulate entry (TREAD_OK asserted, K1 reporting
BYPASS, a fresh console) are HARDWARE state, so the QEMU shim scripts them
exactly as it does for the QT-driven scenarios. The COMMAND, though, arrives
over HTTP — that is the thing under test.
"""
import json, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10


def http(sess, method, path, body=None):
    url = f"http://127.0.0.1:{sess.http_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def test_http_speed_causes_a_real_relay_transfer(qemu):
    s = qemu(net=True)
    s.wait_log(r"http server up on :8000", timeout=120)

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

    n0 = len(s.audit_events())
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200 and body["ok"] is True, body

    # THE CLAIM: the HTTP command drove a real transfer.
    s.wait_audit("relay_cmd_on", since=n0, timeout=30)
    s.wait_audit("tx_enable_on", since=n0, timeout=5)

    # ...and the first frame after entry carries ZERO motion (PLAN entry step 6).
    s.wait_tx_contains(b"[hmph:0]", timeout=20)
    s.wait_tx_contains(b"[inc:0]", timeout=20)
    s.stop_pacer()
