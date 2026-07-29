"""The live push down `/ws` — the ONLY thing that moves the app's Running screen.

WHY THIS IS A GATE AND NOT A NICETY. `TreadmillViewModel` discards every
program-endpoint response body: `startProgram`, `stopProgram`, `pauseProgram`,
`skipInterval`, `prevInterval`, `extendInterval` and `quickStart` are all bare
`runCatching { api.X() }` with no `onSuccess`, and `_status`/`_session`/
`_program` are mutated ONLY in `handleMessage()`, which is fed by
`webSocket.messages`. Before these frames existed the device recorded a running
workout perfectly and the screen stayed at 0:00 for the whole thing.

TYPES, NOT PRESENCE — the same rule as the record shapes, and here it is
sharper: `ServerMessageSerializer` DISPATCHES on `type`, so a frame without one
decodes to `UnknownMessage` and is silently dropped; and `StatusMessage`,
`ProgramMessage` and `SessionMessage` each declare fields with NO kotlinx
default, which `coerceInputValues` cannot invent — an omission throws
MissingFieldException and kills the frame rather than degrading it.
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
import wsc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10

PROGRAM = {
    "name": "WS Test",
    "intervals": [
        {"name": "A", "duration": 600, "speed": 3.0, "incline": 1.0},
        {"name": "B", "duration": 600, "speed": 4.0, "incline": 2.0},
    ],
}


def http(s, method, path, body=None, timeout=20):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def armed(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


# Fields the Kotlin models declare with NO default: an omission is a thrown
# MissingFieldException in the app, not a degraded screen.
REQUIRED = {
    "status": {
        "proxy": bool,
        "emulate": bool,
        "emu_speed": int,
        "emu_speed_mph": (int, float),
        "emu_incline": (int, float),
        "treadmill_connected": bool,
    },
    "program": {
        "running": bool,
        "paused": bool,
        "completed": bool,
        "current_interval": int,
        "interval_elapsed": (int, float),
        "total_elapsed": (int, float),
        "total_duration": (int, float),
    },
    "session": {
        "active": bool,
        "elapsed": (int, float),
        "distance": (int, float),
        "vert_feet": (int, float),
        "wall_started_at": str,
    },
}


def check_shape(frame):
    kind = frame["type"]
    for field, want in REQUIRED.get(kind, {}).items():
        assert field in frame, f"{kind} frame is missing {field!r}: {frame}"
        v = frame[field]
        if want is bool:
            ok = isinstance(v, bool)
        else:
            # `True` is an `int` in Python; a bool where the app declares a
            # number is a WRONG TYPE, which is the failure this checks for.
            ok = isinstance(v, want) and not isinstance(v, bool)
        assert ok, f"{kind}.{field} is {type(v).__name__}, the app declares {want}: {frame}"


def test_the_socket_pushes_status_program_and_session_while_a_program_runs(qemu):
    s = armed(qemu)
    with wsc.WsClient(s) as ws:
        hello = ws.frame(timeout=20)
        assert hello == {"type": "connection", "connected": True}, hello

        st, body = http(s, "POST", "/api/program/start", PROGRAM)
        assert st == 200 and body["running"] is True, body

        frames = ws.collect(20)

    kinds = {f["type"] for f in frames}
    assert {"status", "program", "session"} <= kinds, (
        "the app feeds its whole live UI from this socket; it received only "
        f"{sorted(kinds)} in 20 s of a running program"
    )
    for f in frames:
        assert "type" in f, f
        check_shape(f)

    # The frames must MOVE. A constant frame is the same frozen screen with
    # more network traffic.
    prog = [f for f in frames if f["type"] == "program"]
    sess = [f for f in frames if f["type"] == "session"]
    assert len(prog) >= 5, f"only {len(prog)} program frames in 20 s"
    assert prog[-1]["total_elapsed"] > prog[0]["total_elapsed"], [p["total_elapsed"] for p in prog]
    assert prog[-1]["running"] is True, prog[-1]
    assert prog[-1]["program"]["name"] == "WS Test", prog[-1]
    assert sess[-1]["active"] is True, sess[-1]
    assert sess[-1]["elapsed"] > 0, [x["elapsed"] for x in sess]
    assert sess[-1]["distance"] > 0, [x["distance"] for x in sess]
    assert sess[-1]["end_reason"] is None, sess[-1]

    http(s, "POST", "/api/program/stop")
    s.stop_pacer()


def test_the_last_session_frame_carries_the_end_reason(qemu):
    """`active:false` + a reason is how the app runs its completion transition."""
    s = armed(qemu)
    with wsc.WsClient(s) as ws:
        ws.frame(timeout=20)  # hello
        st, body = http(s, "POST", "/api/program/start", PROGRAM)
        assert st == 200 and body["running"] is True, body
        ws.collect(8)
        http(s, "POST", "/api/program/stop")
        frames = ws.collect(10)

    sess = [f for f in frames if f["type"] == "session"]
    assert sess, [f["type"] for f in frames]
    ended = [f for f in sess if f["end_reason"] is not None]
    assert ended, f"no session frame reported why the session ended: {sess}"
    assert ended[0]["end_reason"] == "user_stop", ended[0]
    assert ended[0]["active"] is False, ended[0]
    s.stop_pacer()


def test_a_client_that_goes_away_costs_a_frame_and_not_the_device(qemu):
    """The pusher runs on a WDT-supervised task; a dead tablet must not reboot it.

    `httpd_ws_send_frame_async` writes through mbedtls on a socket with
    `SO_SNDTIMEO = 1 s`, so one send is bounded — and `net::ws::broadcast` feeds
    the watchdog before each socket. What this asserts is the observable
    consequence: the device stays up, and the HTTP surface stays answerable,
    after a WebSocket is opened and abandoned repeatedly.
    """
    s = armed(qemu)
    boot_lines = len(s.lines())
    http(s, "POST", "/api/program/start", PROGRAM)

    for _ in range(6):
        ws = wsc.WsClient(s)
        ws.__enter__()
        time.sleep(1.5)
        # Close without reading — the far end vanishes mid-push.
        ws.tls.close()

    time.sleep(3)
    st, prog = http(s, "GET", "/api/program", timeout=30)
    assert st == 200 and prog["running"] is True, prog
    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED while pushing to a vanished client: {reboots}"

    http(s, "POST", "/api/program/stop")
    s.stop_pacer()
