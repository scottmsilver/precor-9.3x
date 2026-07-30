"""ADVERSARIAL REVIEW REPROS for the coach tier.

Every test here was written against the CURRENT image to prove or disprove a
claim made in `net/coach.rs`'s header. They are repros first and gates second:
where one is RED, the finding it names is real.

WHAT IS UNDER TEST, in the reviewer's words:
  (a) a coach call in flight cannot delay Stop or any belt command;
  (b) no WDT-supervised task blocks on the network;
  (c) a tool call reaches the belt ONLY through control.rs with clamps intact;
  (d) free heap CONVERGES across many chat turns;
  (e) a slow/truncated/oversized/malformed reply cannot wedge or reboot.

`test_coach.py` already covers (a) once, (c) for two hostile numbers, and (e)
for four shapes. What is NEW here is the JSON the device EMITS about an untrusted
reply — the one direction nothing asserted — plus a heap curve across many turns
and a belt-command check while a call is in flight.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10
WIRE_3 = b"[hmph:12C]"
GUEST_VIEW_OF_HOST = "10.0.2.2"

PROGRAM = {
    "name": "Review Baseline",
    "intervals": [
        {"name": "A", "duration": 600, "speed": 3.0, "incline": 0},
        {"name": "B", "duration": 600, "speed": 4.0, "incline": 1.0},
    ],
}


def http(s, method, path, body=None, timeout=30):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def chat_raw(s, timeout=30) -> str:
    """`GET /api/chat` as RAW TEXT, undecoded.

    `httpc.request` json.loads() for you, which turns "the device emitted
    invalid JSON" into an opaque ValueError inside a helper. The whole point of
    this file is to look at those bytes, so it asks for them.
    """
    url = f"https://127.0.0.1:{s.http_port}/api/chat"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=httpc.tls_context()) as r:
        return r.read().decode(errors="replace")


# ---------------------------------------------------------------------------
# The stub. Behaviour per path, like test_coach.py's.
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


SLOW_SECONDS = 8.0

# `scan::ARGS_BYTES` is 192. An args object larger than that is captured
# TRUNCATED and the call is marked `args_overflow`, which `tool::validate`
# refuses. A description this long is not exotic — it is one sentence.
LONG_DESC = (
    "a forty five minute progressive hill workout with four climbs, recovery "
    "valleys between them, a long easy cool down at the end, and please keep "
    "the steepest climb under ten percent because my knees are not what they "
    "used to be"
)
assert len(LONG_DESC) + len('{"description": ""}') > 192, "the fixture must actually overflow"


class Stub:
    def __init__(self):
        self.seen: list[dict] = []
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n) if n else b""
                with stub._lock:
                    stub.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
                    turn = stub._counters.get(self.path, 0)
                    stub._counters[self.path] = turn + 1
                stub.answer(self, self.path, turn)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def answer(self, h, path, turn):
        if path == "/plain":
            return self.send(h, 200, candidate([{"text": "Nice work, keep going."}]))
        if path == "/slow":
            time.sleep(SLOW_SECONDS)
            return self.send(h, 200, candidate([{"text": "Sorry, I was thinking."}]))
        if path == "/big-args":
            # ONE tool call whose argument object exceeds ARGS_BYTES.
            return self.send(h, 200, candidate([fn_call("generate_workout", {"description": LONG_DESC})]))
        if path == "/quoted-name":
            # A tool NAME carrying a quote. `push_action` writes the name
            # between two quotes with no escaping of its own.
            return self.send(h, 200, candidate([fn_call('set_speed", "x":"', {"mph": 3})]))
        if path == "/newline-name":
            return self.send(h, 200, candidate([fn_call("set\nspeed", {"mph": 3})]))
        if path == "/stop":
            return self.send(h, 200, candidate([{"text": "Stopping."}, fn_call("stop_treadmill", {})]))
        if path == "/set-speed":
            return self.send(
                h,
                200,
                candidate([{"text": "Speeding up."}, fn_call("set_speed", {"mph": 2})]),
            )
        if path == "/many-actions":
            # Four calls, each with a fat-but-legal args object, so the rendered
            # `actions` array runs past ACTIONS_BYTES and saturates mid-entry.
            pad = "p" * 100
            return self.send(
                h,
                200,
                candidate(
                    [
                        fn_call("set_speed", {"mph": 3, "note": pad}),
                        fn_call("set_incline", {"incline": 2, "note": pad}),
                        fn_call("pause_program", {"note": pad}),
                        fn_call("skip_interval", {"note": pad}),
                    ]
                ),
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

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def booted(qemu):
    s = qemu(net=True)
    s.wait_log("https server up on", timeout=90)
    return s


def armed(qemu):
    s = booted(qemu)
    s.start_pacer(synth.console_cycle_bytes(0, 0), interval=PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def point_at(s, stub, path):
    st, body = http(s, "POST", "/api/coach/url", {"url": stub.url(path)})
    assert st == 200 and body["ok"] is True, body


def ask(s, message="how am I doing?"):
    return http(s, "POST", "/api/chat", {"message": message})


def await_turn(s, turn, timeout=90):
    """Wait for the coach to publish `turn`, WITHOUT decoding the body.

    `GET /api/chat` is the surface under test here, so waiting for it must not
    require it to be valid JSON — otherwise a device that emits garbage looks
    like a device that never answered.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        raw = chat_raw(s)
        last = raw
        if f'"turn":{turn}' in raw and '"pending":false' in raw:
            return raw
        time.sleep(0.25)
    raise AssertionError(f"coach never published turn {turn}; last body: {last!r}")


def heap(s):
    for _ in range(4):
        try:
            line = s.cmd_ok("QT heap", timeout=20)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise AssertionError("heap probe never answered")
    return int(line.split("free=")[1].split()[0])


# ---------------------------------------------------------------------------
# FINDING 1 — an args object larger than ARGS_BYTES is echoed TRUNCATED and
# verbatim, so the device emits invalid JSON.
# ---------------------------------------------------------------------------


def test_an_oversized_args_object_does_not_break_the_json_the_device_emits(qemu, stub):
    """`net/coach.rs::push_action` writes `args` verbatim on the claim that it is
    "balanced JSON the model itself wrote", justified by "args is only non-empty
    for a call the scanner saw open AND close inside its budget".

    That claim is FALSE for `args_overflow`: `scan::capture_arg` stops pushing at
    ARGS_BYTES but leaves everything captured so far in the buffer, so `args` is
    non-empty AND unbalanced. `push_action` tests only `args.is_empty()`.
    """
    s = booted(qemu)
    point_at(s, stub, "/big-args")
    st, chat = ask(s, "give me a long hill workout")
    assert st == 202, chat
    raw = await_turn(s, chat["turn"])
    print(f"\nGET /api/chat -> {raw!r}")
    json.loads(raw)  # <- the assertion


# ---------------------------------------------------------------------------
# FINDING 2 — a tool NAME is written between quotes with no escaping.
# ---------------------------------------------------------------------------


def test_a_tool_name_carrying_a_quote_does_not_break_the_json(qemu, stub):
    """`publish` sanitises `text` and `push_action` sanitises `result`; `name` is
    pushed verbatim. The scanner DECODES `\\"` into a bare `"` on the way into
    `ToolCall::name`, so a name is not quote-free by construction."""
    s = booted(qemu)
    point_at(s, stub, "/quoted-name")
    st, chat = ask(s)
    assert st == 202, chat
    raw = await_turn(s, chat["turn"])
    print(f"\nGET /api/chat -> {raw!r}")
    body = json.loads(raw)
    # Parsing is not enough. A quote inside the name CLOSES the name string and
    # what follows is read as more MEMBERS of the action object — valid JSON
    # that says something the device never meant. The action the client sees
    # must have exactly the three fields the device renders.
    act = body["actions"][0]
    assert set(act) == {"name", "args", "result"}, act


def test_a_tool_name_carrying_a_newline_does_not_break_the_json(qemu, stub):
    s = booted(qemu)
    point_at(s, stub, "/newline-name")
    st, chat = ask(s)
    raw = await_turn(s, chat["turn"])
    json.loads(raw)


# ---------------------------------------------------------------------------
# FINDING 3 — the rendered `actions` array saturates mid-entry.
# ---------------------------------------------------------------------------


def test_four_actions_do_not_saturate_the_array_into_invalid_json(qemu, stub):
    """MAX_CALLS is 4, so four calls is the DECLARED maximum, not an abuse.
    ACTIONS_BYTES is 384 and one entry is `{"name":"...","args":{...},
    "result":"..."}` — four of them do not fit, and `FixedStr::push_str`
    saturates silently."""
    s = armed(qemu)
    point_at(s, stub, "/many-actions")
    st, chat = ask(s)
    assert st == 202, chat
    raw = await_turn(s, chat["turn"])
    json.loads(raw)


# ---------------------------------------------------------------------------
# (a) THE BELT, restated for a MANUAL command rather than the program Stop.
# ---------------------------------------------------------------------------


def test_a_manual_belt_command_is_not_delayed_by_a_coach_call(qemu, stub):
    """`test_coach.py` proves `POST /api/program/stop` stays prompt. This is the
    same property for the OTHER belt surface — `POST /api/speed` — because both
    go through the same single httpd worker and the app's manual controls are
    what a user reaches for when a coach answer is slow."""
    s = armed(qemu)
    point_at(s, stub, "/slow")

    t0 = time.monotonic()
    st, _ = http(s, "POST", "/api/speed", {"value": 2.0})
    baseline = time.monotonic() - t0
    assert st == 200

    st, chat = ask(s, "talk while I run")
    assert st == 202, chat

    t0 = time.monotonic()
    st, _ = http(s, "POST", "/api/speed", {"value": 3.0})
    during = time.monotonic() - t0
    assert st == 200
    st, coach = http(s, "GET", "/api/coach")
    assert coach["busy"] is True, "the call had finished; the measurement is vacuous"

    print(f"\nSPEED LATENCY baseline={baseline:.2f}s during-coach-call={during:.2f}s")
    assert during < baseline + 2.0, (during, baseline)
    st, status = http(s, "GET", "/api/status")
    assert status["emu_speed"] == 30, status
    s.stop_pacer()


# ---------------------------------------------------------------------------
# (d) HEAP CONVERGENCE ACROSS MANY TURNS. Driven hard, and measured.
# ---------------------------------------------------------------------------


def test_free_heap_converges_across_many_chat_turns(qemu, stub):
    """Resident memory must be independent of conversation length.

    Forty full turns through the coach task — request built, client opened,
    reply streamed through the 512-byte chunk, history ring pushed twice per
    turn (so the 6-slot ring wraps thirteen times over).

    THE ASSERTION IS CONVERGENCE, NOT A TOLERANCE. A loose "did not lose more
    than 4 KB" would pass a slow leak as happily as a flat curve. The measured
    shape over 50 turns is a ~3.4 KB allocator/lwIP warm-up across the first two
    rounds and then FLAT to within 12 bytes for the remaining eight, so this
    requires the tail rounds to agree with each other rather than merely to be
    near the start:

        boot=145200 r0=143652 r1=142856 r2=141796 r3=141796 r4=141796
        r5=141784 r6=141784 r7=141784 r8=141796 r9=141796
    """
    s = booted(qemu)
    point_at(s, stub, "/plain")

    curve = [("boot", heap(s))]
    for rnd in range(8):
        for i in range(5):
            st, chat = ask(s, f"round {rnd} message {i} - how am I doing out here today?")
            assert st == 202, chat
            await_turn(s, chat["turn"])
        curve.append((f"r{rnd}", heap(s)))

    print("\nHEAP CURVE  " + "  ".join(f"{k}={v}" for k, v in curve))
    frees = [v for _, v in curve]
    tail = frees[-4:]  # the last twenty turns, after any warm-up
    assert max(tail) - min(tail) <= 512, f"free heap has not CONVERGED: {curve}"
    # And the warm-up itself has to be a warm-up, not a slow slide.
    assert frees[-1] >= frees[3] - 512, f"free heap still declining after 20 turns: {curve}"


# ---------------------------------------------------------------------------
# FINDING 4 — `stop_treadmill` reports "treadmill stopped" for a belt it did
# not stop.
# ---------------------------------------------------------------------------


def test_the_coach_stop_actually_stops_a_manually_commanded_belt(qemu, stub):
    """`net/coach.rs::apply` routes `stop_treadmill` to `ProgramState::stop()`
    and nothing else, so with NO program running it drives an EMPTY plan and the
    HTTP surface keeps its lease and its commanded speed.

    That half is a known open defect the HTTP endpoint shares
    (`test_reviewer_attacks.py::test_b_...`, red by design). What is NEW and
    belongs to this tier is the SENTENCE: `describe()` runs BEFORE `apply()`, and
    `apply` returns an override only on failure — `StopTreadmill` has no failure
    branch — so the transcript says "treadmill stopped" while the belt runs. The
    module header claims "what the user is told and what the belt was asked for
    cannot diverge". Here they do, in the one direction that matters.
    """
    s = armed(qemu)
    point_at(s, stub, "/stop")
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    st, chat = ask(s, "stop the treadmill")
    assert st == 202, chat
    raw = await_turn(s, chat["turn"])
    got = json.loads(raw)
    st, status = http(s, "GET", "/api/status")
    print(f"\nCOACH SAID {got['actions']}  DEVICE STATUS {status}")

    said_stopped = any(a["result"] == "treadmill stopped" for a in got["actions"])
    if said_stopped:
        assert status["speed"] == 0.0, (
            "the coach told the user 'treadmill stopped' and the belt is still "
            f"commanded at {status['speed']} mph: {status}"
        )
    s.stop_pacer()


def test_coach_reports_failed_positive_speed_recovery_truthfully(qemu, stub):
    """The transcript must report what reached the belt, not model intent."""
    s = armed(qemu)
    point_at(s, stub, "/set-speed")
    s.cmd_ok("QT k1 closed")
    s.wait_audit("emergency:relay_feedback_both_closed", timeout=30)

    tx0 = len(s.tx_bytes())
    st, chat = ask(s, "set the speed to two")
    assert st == 202, chat
    got = json.loads(await_turn(s, chat["turn"]))
    matching = [a for a in got["actions"] if a["name"] == "set_speed"]
    assert len(matching) == 1, got
    assert matching[0]["result"] == "the treadmill refused that change", matching[0]

    st, after = http(s, "GET", "/api/status")
    assert st == 200, after
    assert after["mode"] == "proxy", after
    assert after["relay"] is False, after
    assert after["speed"] == 0.0, after
    audit0 = s.audit_events()[-1][0] + 1
    s.wait_audit("complete_console_frame", since=audit0, timeout=5)
    assert b"[hmph:C8]" not in s.tx_bytes()[tx0:]
    s.stop_pacer()
