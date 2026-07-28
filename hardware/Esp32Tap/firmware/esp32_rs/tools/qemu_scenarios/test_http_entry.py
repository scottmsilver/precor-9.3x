"""Slice 2 proof: an HTTP POST causes a REAL relay transfer.

The hardware preconditions for emulate entry (TREAD_OK asserted, K1 reporting
BYPASS, a fresh console) are HARDWARE state, so the QEMU shim scripts them
exactly as it does for the QT-driven scenarios. The COMMAND, though, arrives
over HTTP — that is the thing under test.
"""

import sys
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
