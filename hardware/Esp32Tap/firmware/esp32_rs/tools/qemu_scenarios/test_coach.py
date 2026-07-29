"""The AI coach tier, against a LOCAL STUB the test controls.

WHY THERE IS NO LIVE-ENDPOINT GATE HERE, and why that is not a gap
==================================================================
A real `generativelanguage.googleapis.com` call needs a live per-device key,
returns different words every time, costs money, and depends on somebody else's
uptime. Wiring one into `tools/sweep.sh` would produce exactly the thing this
project spent a night removing: an intermittent that costs an investigation
every time and lands on the wrong theory. `test_coach_live.py` (opt-in, never a
gate) is where a real call goes; bead `precor-9_3x-zt8` tracks the
once-off manual confirmation.

What a live call would prove that a stub cannot is narrow and worth naming: that
the request body is one Gemini accepts, and that the CA bundle validates the
real certificate chain. Everything ELSE about this tier — the shape of the call,
the bound on the httpd worker, the clamps, the memory behaviour, the failure
handling — is about what the DEVICE does when an endpoint behaves badly, and a
stub is strictly better at that because it can behave badly on demand.

So the stub speaks the shapes that break things: a slow answer, a truncated one,
an oversized one, a malformed one, a hostile tool call, and an HTTP error.

HOW THE GUEST REACHES THE STUB
QEMU user-mode networking (`-nic user,model=open_eth,...`) puts the guest on
10.0.2.0/24 with the SLIRP gateway at 10.0.2.2, which proxies to the host's
loopback. The stub therefore binds 127.0.0.1 and the device is pointed at
`http://10.0.2.2:<port>/...` through `POST /api/coach/url`. That endpoint exists
because hard-coding a server URL is forbidden in this project — and because a
configurable endpoint on an unauthenticated LAN surface is a key-exfiltration
path, which is why `key_allowed` only ever sends the key to the pinned host.
`test_the_key_is_never_sent_to_an_unpinned_endpoint` is that rule's assertion.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
import wsc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10

# 3.0 mph on the wire: `hmph` is mph x 100 in uppercase hex, so 300 -> 12C.
WIRE_3 = b"[hmph:12C]"

PROGRAM = {
    "name": "Coach Baseline",
    "intervals": [
        {"name": "A", "duration": 600, "speed": 3.0, "incline": 0},
        {"name": "B", "duration": 600, "speed": 4.0, "incline": 1.0},
    ],
}

# The SLIRP gateway, as seen from inside the guest.
GUEST_VIEW_OF_HOST = "10.0.2.2"


def http(s, method, path, body=None, timeout=30):
    """A 4xx/5xx is DATA here — half of what this file proves is refusals."""
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


# ---------------------------------------------------------------------------
# The stub endpoint.
# ---------------------------------------------------------------------------


def candidate(parts, finish="STOP"):
    return json.dumps(
        {
            "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 12},
            "modelVersion": "stub",
        }
    ).encode()


def fn_call(name, args):
    return {"functionCall": {"name": name, "args": args}}


PROGRAM_JSON = json.dumps(
    {
        "name": "Stub Hills",
        "intervals": [
            {"name": "Warm", "duration": 300, "speed": 2.5, "incline": 0},
            {"name": "Climb", "duration": 240, "speed": 4.0, "incline": 6},
            {"name": "Cool", "duration": 300, "speed": 2.0, "incline": 0},
        ],
    }
)


class Stub:
    """A tiny HTTP server whose behaviour is chosen per PATH.

    Records every request (path, headers, body) so the tests can assert what the
    device SENT as well as what it did with the answer — the key-pinning rule is
    only checkable from the request side.
    """

    def __init__(self):
        self.seen: list[dict] = []
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # silence
                pass

            def do_POST(self):  # noqa: N802 (stdlib naming)
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n) if n else b""
                with stub._lock:
                    stub.seen.append(
                        {
                            "path": self.path,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body,
                        }
                    )
                    turn = stub._counters.get(self.path, 0)
                    stub._counters[self.path] = turn + 1
                stub.answer(self, self.path, turn)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    # -- behaviours ---------------------------------------------------------

    def answer(self, h, path, turn):
        if path == "/plain":
            return self.send(h, 200, candidate([{"text": "Nice work, keep going."}]))
        if path == "/hostile":
            return self.send(
                h,
                200,
                candidate(
                    [
                        {"text": "Taking it up."},
                        fn_call("set_speed", {"mph": 999}),
                    ]
                ),
            )
        if path == "/incline":
            return self.send(h, 200, candidate([fn_call("set_incline", {"incline": 99})]))
        if path == "/unknown-tool":
            return self.send(h, 200, candidate([fn_call("query_workout_data", {"sql": "SELECT 1"})]))
        if path == "/generate":
            # TURN 0 is the chat call and asks for a workout; TURN 1 is the
            # generation call the device makes in response. One stub, two
            # answers, because that is exactly the two-call shape the firmware
            # uses.
            if turn == 0:
                return self.send(
                    h,
                    200,
                    candidate(
                        [
                            {"text": "Building you a hill workout."},
                            fn_call("generate_workout", {"description": "hills"}),
                        ]
                    ),
                )
            return self.send(h, 200, candidate([{"text": PROGRAM_JSON}]))
        if path == "/generate-truncated":
            if turn == 0:
                return self.send(
                    h,
                    200,
                    candidate([fn_call("generate_workout", {"description": "hills"})]),
                )
            cut = PROGRAM_JSON[: PROGRAM_JSON.index("Climb") + 20]
            return self.send(h, 200, candidate([{"text": cut}], finish="MAX_TOKENS"))
        if path == "/slow":
            # HOLDS THE DEVICE'S REQUEST OPEN. This is the whole point of the
            # tier's headline test: while this is in flight, the httpd worker
            # must still be free.
            time.sleep(SLOW_SECONDS)
            return self.send(h, 200, candidate([{"text": "Sorry, I was thinking."}]))
        if path == "/truncated":
            # A body cut off inside the argument object, with a Content-Length
            # that matches — so it is a well-formed HTTP response carrying a
            # malformed JSON payload, which is what a token-limited or
            # proxy-interrupted answer looks like.
            full = candidate([{"text": "On it."}, fn_call("set_speed", {"mph": 11})])
            return self.send(h, 200, full[: full.index(b'"mph":11') + 7])
        if path == "/huge":
            pad = b"x" * 200_000
            return self.send(h, 200, candidate([{"text": pad.decode()}]))
        if path == "/malformed":
            return self.send(
                h,
                502,
                b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>",
                ctype="text/html",
            )
        if path == "/429":
            return self.send(
                h,
                429,
                json.dumps(
                    {
                        "error": {
                            "code": 429,
                            "message": "Resource has been exhausted",
                            "status": "RESOURCE_EXHAUSTED",
                        }
                    }
                ).encode(),
            )
        return self.send(h, 404, b"{}")

    def send(self, h, status, body, ctype="application/json"):
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def url(self, path):
        return f"http://{GUEST_VIEW_OF_HOST}:{self.port}{path}"

    def requests_to(self, path):
        with self._lock:
            return [r for r in self.seen if r["path"] == path]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


SLOW_SECONDS = 8.0


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Session helpers.
# ---------------------------------------------------------------------------


def booted(qemu):
    s = qemu(net=True)
    s.wait_log("https server up on", timeout=90)
    return s


def armed(qemu):
    """Booted, with a console pacer so the safety controller will enter emulate
    and the belt can actually move. Every test that asserts something about the
    BELT needs this; the ones that only assert about JSON do not."""
    s = booted(qemu)
    s.start_pacer(synth.console_cycle_bytes(0, 0), interval=PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def point_at(s, stub, path):
    st, body = http(s, "POST", "/api/coach/url", {"url": stub.url(path)})
    assert st == 200 and body["ok"] is True, body
    # THE CONSEQUENCE THE OPERATOR NEEDS TO SEE: an overridden endpoint is not
    # the pinned one, so no key will be sent to it.
    assert body["endpoint_pinned"] is False, body


def ask(s, message="how am I doing?"):
    st, body = http(s, "POST", "/api/chat", {"message": message})
    return st, body


def await_reply(s, turn, timeout=60):
    """Poll `GET /api/chat` until the coach task publishes `turn`.

    Polls a GUEST-OBSERVED fact rather than sleeping a fixed time: guest time
    under QEMU lags wall time, badly and variably under xdist, and a
    `time.sleep(n)` here would be exactly the kind of intermittent this suite
    exists not to have.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st, body = http(s, "GET", "/api/chat")
        assert st == 200, body
        if body.get("turn") == turn and body.get("pending") is False:
            return body
        time.sleep(0.25)
    raise AssertionError(f"coach never published turn {turn}")


# ---------------------------------------------------------------------------
# THE HEADLINE PROPERTY. Everything else in this file is secondary to it.
# ---------------------------------------------------------------------------


def test_the_chat_endpoint_returns_without_waiting_for_the_model(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/slow")

    t0 = time.monotonic()
    st, body = ask(s)
    elapsed = time.monotonic() - t0

    assert st == 202, (st, body)
    assert body["pending"] is True and body["turn"] >= 1, body
    # The stub holds ITS request for SLOW_SECONDS. If the handler waited for the
    # round trip, this would be >= that.
    assert elapsed < 2.0, (
        f"POST /api/chat held the caller for {elapsed:.2f}s while the model was "
        f"thinking — the single httpd worker was occupied for the round trip"
    )
    # And the answer does arrive, on the task, afterwards.
    got = await_reply(s, body["turn"], timeout=SLOW_SECONDS + 60)
    assert "thinking" in got["text"], got


def test_stop_stays_responsive_while_a_coach_call_is_in_flight(qemu, stub):
    """THE DEFECT CLASS THIS TIER WAS BUILT NOT TO REPEAT.

    IDF runs ONE httpd worker. A slow client held it for 60 s once and the
    network Stop button went dead WITH THE BELT MOVING. A Gemini call takes
    seconds; making it on that worker would be the same outage, deliberately, on
    every coach message.

    So: a program running and the belt moving, a coach call in flight against an
    endpoint that will not answer for SLOW_SECONDS, and `POST /api/program/stop`
    must complete PROMPTLY and actually stop the belt.

    IT IS THE PROGRAM'S BELT, not a manually commanded one, and that is
    deliberate rather than incidental. `POST /api/program/stop` leaving a
    MANUALLY commanded belt running is a real, separate, still-open defect
    (`test_reviewer_attacks.py::test_b_program_stop_zeroes_a_manually_commanded_belt`,
    red by design). Asserting it here would make this file red for somebody
    else's bug and would say nothing about the coach. The scenario that matters
    for this tier — the Stop button during a workout — is the program one.
    """
    s = armed(qemu)
    point_at(s, stub, "/slow")

    def start_and_move():
        st, body = http(s, "POST", "/api/program/start", PROGRAM)
        assert st == 200 and body["running"] is True, body
        s.wait_tx_contains(WIRE_3, timeout=60)

    # Baseline: what Stop costs with nothing in flight.
    start_and_move()
    t0 = time.monotonic()
    st, body = http(s, "POST", "/api/program/stop")
    baseline = time.monotonic() - t0
    assert st == 200 and body["running"] is False, body

    # Belt moving again, and now a coach call in flight.
    start_and_move()
    n0 = len(s.audit_events())
    st, chat = ask(s, "talk to me while I run")
    assert st == 202, chat

    t0 = time.monotonic()
    st, body = http(s, "POST", "/api/program/stop")
    blocked = time.monotonic() - t0
    assert st == 200 and body["running"] is False, body

    # THE BELT REALLY STOPPED, waited for on a GUEST-OBSERVED fact rather than a
    # wall-clock sleep: PLAN's polite exit sends a complete zero frame and then
    # releases the lease, and `emu_speed` reads the commanded value until that
    # sequence has run. Asserting the status the instant the 200 came back read
    # the pre-exit value and looked like a Stop that did nothing.
    s.wait_audit("send_and_finish_complete_zero_frame", since=n0, timeout=30)
    s.wait_audit("lease_released", since=n0, timeout=45)
    st, status = http(s, "GET", "/api/status")
    assert status["speed"] == 0.0 and status["relay"] is False, status

    print(f"\nSTOP LATENCY  baseline={baseline:.2f}s  during-coach-call={blocked:.2f}s")
    assert blocked < baseline + 2.0, (
        f"Stop took {blocked:.2f}s while a coach call was in flight "
        f"(baseline {baseline:.2f}s) — the model round trip is on the httpd worker"
    )
    # The stub really was still holding its request when Stop ran, so the
    # measurement is of the thing it claims to measure.
    assert blocked < SLOW_SECONDS, "the coach call had already finished; the test is vacuous"
    st, coach = http(s, "GET", "/api/coach")
    assert coach["busy"] is True, coach

    await_reply(s, chat["turn"], timeout=SLOW_SECONDS + 60)
    s.stop_pacer()


# ---------------------------------------------------------------------------
# A model reply is untrusted input.
# ---------------------------------------------------------------------------


def test_a_hostile_speed_is_clamped_before_it_reaches_the_belt(qemu, stub):
    s = armed(qemu)
    point_at(s, stub, "/hostile")
    st, chat = ask(s, "go as fast as you can")
    assert st == 202, chat
    got = await_reply(s, chat["turn"])

    assert got["actions"], got
    a = got["actions"][0]
    assert a["name"] == "set_speed", a
    assert a["result"] == "speed set to 12.0 mph", a
    # The model asked for 999. The DEVICE decided.
    st, status = http(s, "GET", "/api/status")
    assert status["emu_speed"] == 120, status
    # And the args are echoed unchanged, so the transcript is honest about what
    # was asked for as well as what happened.
    assert a["args"]["mph"] == 999, a


def test_a_hostile_incline_is_clamped_too(qemu, stub):
    s = armed(qemu)
    point_at(s, stub, "/incline")
    st, chat = ask(s)
    got = await_reply(s, chat["turn"])
    assert got["actions"][0]["result"] == "incline set to 15.0%", got
    st, status = http(s, "GET", "/api/status")
    assert status["emu_incline"] == 15.0, status


def test_a_tool_this_device_does_not_have_is_refused_by_name(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/unknown-tool")
    st, chat = ask(s, "how did last week go?")
    got = await_reply(s, chat["turn"])
    a = got["actions"][0]
    assert a["name"] == "query_workout_data", a
    assert "does not exist on this device" in a["result"], a


def test_a_truncated_reply_produces_no_action(qemu, stub):
    s = armed(qemu)
    point_at(s, stub, "/truncated")
    http(s, "POST", "/api/speed", {"value": 2.0})
    st, chat = ask(s)
    got = await_reply(s, chat["turn"])

    # `{"mph":11` would parse to 11.0 mph if anything guessed. Nothing does.
    st, status = http(s, "GET", "/api/status")
    assert status["emu_speed"] == 20, status
    for a in got["actions"]:
        assert "did not arrive intact" in a["result"], got


def test_an_http_error_is_reported_and_the_device_carries_on(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/429")
    st, chat = ask(s)
    got = await_reply(s, chat["turn"])
    assert got["actions"] == [], got
    assert got["text"], "the user must be told something"
    # The upstream error's own prose must NOT be shown as the coach speaking.
    assert "RESOURCE_EXHAUSTED" not in got["text"], got
    assert "exhausted" not in got["text"].lower(), got
    # The device is still serving.
    st, _ = http(s, "GET", "/api/status")
    assert st == 200


def test_a_non_json_reply_is_survivable(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/malformed")
    st, chat = ask(s)
    got = await_reply(s, chat["turn"])
    assert got["actions"] == [], got
    st, _ = http(s, "GET", "/api/status")
    assert st == 200


def test_an_oversized_reply_does_not_reboot_or_grow_the_device(qemu, stub):
    """200 KB of answer through a device whose text sink is 2 KB.

    The property is not "it truncates" — it is that the DEVICE does not care how
    large the answer was. The reply is streamed through a 512-byte chunk that is
    reused, so a body a hundred times the buffer costs the buffer.
    """
    s = booted(qemu)
    boot_lines = len(s.lines())
    point_at(s, stub, "/huge")
    for _ in range(3):
        st, chat = ask(s)
        assert st == 202, chat
        await_reply(s, chat["turn"], timeout=90)
    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED on an oversized reply: {reboots}"
    st, _ = http(s, "GET", "/api/status")
    assert st == 200


# ---------------------------------------------------------------------------
# A generated workout has to become real intervals.
# ---------------------------------------------------------------------------


def test_a_generated_workout_lands_through_the_existing_program_path(qemu, stub):
    s = armed(qemu)
    point_at(s, stub, "/generate")
    st, chat = ask(s, "give me a hill workout")
    got = await_reply(s, chat["turn"], timeout=90)

    assert any("workout ready" in a["result"] for a in got["actions"]), got

    st, prog = http(s, "GET", "/api/program")
    assert st == 200
    assert prog["program"]["name"] == "Stub Hills", prog
    assert len(prog["program"]["intervals"]) == 3, prog
    # LOADED, NOT STARTED — the Pi's `generate_workout` does not start either,
    # and a belt that starts because a sentence was ambiguous is not a thing
    # this device will do.
    assert prog["running"] is False, prog

    # It is a REAL program: start it and the belt follows.
    st, body = http(s, "POST", "/api/program/start")
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(b"[hmph:", timeout=60)

    # And it went into history through the SAME writer every other load uses —
    # with the description the model asked for as its prompt, so the lobby entry
    # says where the workout came from.
    st, hist = http(s, "GET", "/api/programs/history")
    assert st == 200, hist
    entry = next((h for h in hist if h["program"]["name"] == "Stub Hills"), None)
    assert entry is not None, hist
    assert entry["prompt"] == "hills", entry


def test_a_workout_truncated_by_the_token_limit_is_salvaged_or_refused(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/generate-truncated")
    st, chat = ask(s, "give me a hill workout")
    got = await_reply(s, chat["turn"], timeout=90)

    st, prog = http(s, "GET", "/api/program")
    assert st == 200
    loaded = prog.get("program")
    if loaded is None:
        # Refused: acceptable, and it must SAY so rather than silently doing
        # nothing.
        assert any("could use" in a["result"] for a in got["actions"]), got
        return
    # Salvaged: every interval that survived must be a COMPLETE one, and the
    # partial one must be gone rather than guessed at.
    assert loaded["intervals"], loaded
    for iv in loaded["intervals"]:
        assert 0 <= iv["speed"] <= 12.0, iv
        assert 0 <= iv["incline"] <= 15.0, iv
        assert iv["duration"] >= 10, iv


# ---------------------------------------------------------------------------
# The key is a per-device secret.
# ---------------------------------------------------------------------------

FAKE_KEY = "NOT-A-REAL-KEY-0123456789abcdef"


def test_the_key_is_never_echoed_logged_or_returned(qemu, stub):
    s = booted(qemu)
    boot_lines = len(s.lines())

    st, body = http(s, "POST", "/api/coach/key", {"key": FAKE_KEY})
    assert st == 200 and body["ok"] is True, body
    assert FAKE_KEY not in json.dumps(body), body

    st, body = http(s, "GET", "/api/coach")
    assert st == 200 and body["configured"] is True, body
    # WHETHER, NEVER WHAT.
    assert FAKE_KEY not in json.dumps(body), body

    st, body = http(s, "GET", "/api/chat")
    assert FAKE_KEY not in json.dumps(body), body

    # Not on the console either — not whole, and not as a prefix.
    console = "\n".join(s.lines()[boot_lines:])
    assert FAKE_KEY not in console, console
    assert FAKE_KEY[:12] not in console, console


def test_the_key_is_never_sent_to_an_unpinned_endpoint(qemu, stub):
    """The exfiltration path that a configurable endpoint would otherwise be.

    `POST /api/coach/url` is unauthenticated, like every endpoint on this
    device. Without the pinning rule, anyone on the LAN could point the device
    at their own server and collect the key on the next message.
    """
    s = booted(qemu)
    http(s, "POST", "/api/coach/key", {"key": FAKE_KEY})
    point_at(s, stub, "/plain")

    st, chat = ask(s)
    await_reply(s, chat["turn"])

    reqs = stub.requests_to("/plain")
    assert reqs, "the device never called the stub"
    for r in reqs:
        assert "x-goog-api-key" not in r["headers"], r["headers"]
        assert FAKE_KEY.encode() not in r["body"], "the key was in the request BODY"
        # And nowhere in the raw header block either, under any name.
        joined = " ".join(f"{k}: {v}" for k, v in r["headers"].items())
        assert FAKE_KEY not in joined, joined


def test_the_request_carries_the_prompt_the_history_and_no_timestamp(qemu, stub):
    """The device has NO RTC and NO SNTP. It must not invent a clock."""
    s = booted(qemu)
    point_at(s, stub, "/plain")

    st, chat = ask(s, "first message")
    await_reply(s, chat["turn"])
    st, chat = ask(s, "second message")
    await_reply(s, chat["turn"])

    reqs = stub.requests_to("/plain")
    assert len(reqs) == 2, reqs
    second = json.loads(reqs[1]["body"])

    # The conversation is carried, oldest first, with the pending message last.
    texts = [p["text"] for c in second["contents"] for p in c["parts"]]
    assert texts[-1] == "second message", texts
    assert "first message" in texts, texts

    # The system instruction says what the device cannot do.
    sysi = second["systemInstruction"]["parts"][0]["text"]
    assert "NO CLOCK" in sysi.upper(), sysi
    assert "Right now: belt" in sysi, sysi

    # NINE tools, and none of them is the Pi's SQL surface.
    names = [d["name"] for t in second["tools"] for d in t["functionDeclarations"]]
    assert "query_workout_data" not in names, names
    assert "set_speed" in names and "generate_workout" in names, names


def test_a_second_message_while_busy_is_refused_rather_than_queued(qemu, stub):
    """ONE turn in flight, by construction.

    A queue would make memory a function of how fast somebody types, and a queue
    of stale questions answered minutes later is worse than a refusal.
    """
    s = booted(qemu)
    point_at(s, stub, "/slow")
    st, first = ask(s, "one")
    assert st == 202, first
    st, second = ask(s, "two")
    assert st == 429, (st, second)
    assert second["actions"] == [], second
    await_reply(s, first["turn"], timeout=SLOW_SECONDS + 60)
    # And afterwards the coach is available again.
    st, third = ask(s, "three")
    assert st == 202, third


def test_the_history_window_is_capped(qemu, stub):
    """`coach_core::hist::TURNS` = 6 entries — three exchanges. Resident memory
    must not be a function of how long somebody has been talking."""
    s = booted(qemu)
    point_at(s, stub, "/plain")
    for i in range(8):
        st, chat = ask(s, f"message number {i}")
        assert st == 202, chat
        await_reply(s, chat["turn"], timeout=90)

    last = json.loads(stub.requests_to("/plain")[-1]["body"])
    # 6 remembered turns + the pending user message.
    assert len(last["contents"]) <= 7, len(last["contents"])
    texts = [p["text"] for c in last["contents"] for p in c["parts"]]
    assert "message number 0" not in texts, texts
    assert "message number 7" in texts, texts


def test_the_coach_endpoints_answer_before_a_key_is_configured(qemu):
    """A 404 reads to a client as much older firmware; an honest sentence reads
    as the truth. Same rule the HRM routes follow on a build with no radio."""
    s = booted(qemu)
    st, body = http(s, "GET", "/api/coach")
    assert st == 200 and body["configured"] is False, body
    assert body["endpoint_pinned"] is True, body

    st, body = http(s, "POST", "/api/chat", {"message": "hello"})
    assert st == 200, (st, body)
    assert body["actions"] == [], body
    assert "not set up" in body["text"], body


# ---------------------------------------------------------------------------
# The live push. The app feeds its whole running screen from `/ws`, so an
# answer that only exists behind a poll is an answer most clients never see.
# ---------------------------------------------------------------------------


def test_the_answer_is_pushed_down_ws_exactly_once(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/plain")
    with wsc.WsClient(s) as ws:
        hello = ws.frame(timeout=20)
        assert hello == {"type": "connection", "connected": True}, hello

        st, chat = ask(s, "how am I doing?")
        assert st == 202, chat
        frames = ws.collect(12)

    coach = [f for f in frames if f.get("type") == "coach"]
    assert coach, f"no coach frame in {sorted({f.get('type') for f in frames})}"
    assert coach[0]["text"] == "Nice work, keep going.", coach[0]
    assert coach[0]["turn"] == chat["turn"], coach[0]
    # EXACTLY ONCE. status/program/session are STATE and are re-sent every tick
    # so a client that connected late catches up; a coach reply is an EVENT, and
    # repeating it would grow the app's transcript by an identical line a second
    # forever and spend the single worker's send budget on nothing.
    assert len(coach) == 1, f"the coach frame was pushed {len(coach)} times"
