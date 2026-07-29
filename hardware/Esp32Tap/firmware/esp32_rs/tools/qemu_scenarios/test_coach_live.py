"""The ONE check that needs the real endpoint. OPT-IN, and never a gate.

    COACH_LIVE_KEY=<your key> python3 -m pytest test_coach_live.py -q -s

It is deliberately not in `tools/sweep.sh` and it is skipped without the
environment variable, because as a gate it would be everything this project
spent a night removing: it needs a live per-device secret, it is
nondeterministic (the model chooses different words and sometimes different
tool calls every run), it costs money per invocation, and it fails when
somebody else's service is having a bad afternoon. An intermittent is worse
than a hard failure — it costs an investigation every time and the
investigations land on wrong theories.

WHAT ONLY THIS CAN PROVE, stated narrowly so nobody mistakes the stub suite for
covering it:

  1. the request body `coach_core::req` builds is one the real API ACCEPTS —
     a stub answers whatever it likes regardless of what we sent;
  2. the embedded CA bundle validates the real certificate chain for
     `generativelanguage.googleapis.com` (the stub is plain HTTP, on purpose:
     a configurable endpoint never receives the key, so it cannot be HTTPS with
     a device-trusted cert without weakening the pinning rule);
  3. the `x-goog-api-key` header is the auth mechanism the endpoint honours.

Everything else about the tier — the shape of the call, the bound on the httpd
worker, the clamps, the memory behaviour, every failure mode — is proven
deterministically in `test_coach.py`, which is strictly better at it because a
stub can misbehave on demand and the real API cannot be asked to.

THE KEY COMES FROM THE ENVIRONMENT AND IS NEVER WRITTEN ANYWHERE. It is not
read from `./.gemini_key` (that is the Pi's per-device secret and it is
gitignored and rsync-excluded for a reason), it is not printed, and it is not
asserted on.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

LIVE_KEY = os.environ.get("COACH_LIVE_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY,
    reason="set COACH_LIVE_KEY to run the once-off live-endpoint confirmation",
)


def http(s, method, path, body=None, timeout=60):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def test_a_real_turn_against_the_real_endpoint(qemu):
    s = qemu(net=True)
    s.wait_log("https server up on", timeout=90)

    st, body = http(s, "POST", "/api/coach/key", {"key": LIVE_KEY})
    assert st == 200 and body["ok"] is True, body

    st, body = http(s, "GET", "/api/coach")
    assert body["configured"] is True and body["endpoint_pinned"] is True, body

    st, chat = http(
        s,
        "POST",
        "/api/chat",
        {"message": "In one short sentence, what is a good warm-up pace?"},
    )
    assert st == 202, chat

    deadline = time.monotonic() + 90
    got = None
    while time.monotonic() < deadline:
        st, r = http(s, "GET", "/api/chat")
        if r.get("turn") == chat["turn"] and r.get("pending") is False:
            got = r
            break
        time.sleep(0.5)
    assert got is not None, "the live turn never completed"

    # WHAT IS ASSERTED IS THAT IT WORKED, NOT WHAT IT SAID. The model's words
    # are nondeterministic; an assertion on them is how this file would become
    # the flaky gate it exists not to be.
    print(f"\\nLIVE COACH REPLY: {got['text']!r}")
    print(f"LIVE COACH ACTIONS: {got['actions']}")
    assert got["text"], "the real endpoint answered with no text at all"
    for bad in ("could not reach", "turned that request down", "could not read"):
        assert bad not in got["text"], f"the device reported a transport failure: {got}"

    # The key must not have leaked into anything the device says about itself.
    console = "\\n".join(s.lines())
    assert LIVE_KEY not in console
    st, body = http(s, "GET", "/api/coach")
    assert LIVE_KEY not in json.dumps(body)
