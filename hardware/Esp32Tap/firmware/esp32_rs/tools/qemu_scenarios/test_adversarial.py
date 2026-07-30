"""ADVERSARIAL review scenarios — belt-safety and memory attacks.

Written by the safety/memory reviewer. Every assertion here is a claim about
the DEVICE, observed through the guest's own audit ring, wire capture and HTTP
surface. Nothing sleeps on wall time for a guest fact.
"""

from __future__ import annotations

import json
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

PROGRAM = {
    "name": "Adversarial",
    "intervals": [
        {"name": "A", "duration": 600, "speed": 3.0, "incline": 0},
        {"name": "B", "duration": 600, "speed": 4.0, "incline": 1.0},
    ],
}

WIRE_3 = b"[hmph:12C]"  # 3.00 mph -> 300 -> 0x12C
WIRE_2 = b"[hmph:C8]"  # 2.00 mph
WIRE_0 = b"[hmph:0]"


def http(s, method, path, body=None, timeout=20):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def raw_post(s, path, body: bytes, headers=None, timeout=20):
    """POST arbitrary BYTES with an arbitrary Content-Length."""
    url = f"https://127.0.0.1:{s.http_port}{path}"
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=httpc.tls_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def armed(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def prog(s):
    st, body = http(s, "GET", "/api/program")
    assert st == 200, body
    return body


def status(s):
    st, body = http(s, "GET", "/api/status")
    assert st == 200, body
    return body


def wait_for(s, predicate, what, timeout=90.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = prog(s)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"never observed {what}; last={last!r}")


# ---------------------------------------------------------------------------
# ATTACK 1 — quick-start while a program is already running.
#
# `post_impl` V_QUICK stops the running program with `release_belt=True`, which
# runs PLAN's normal EXIT (request_normal_exit -> ExitWaitGap). It then loads
# and starts the new program IN THE SAME LOCK HOLD and drives that plan — while
# the controller is still in ExitWaitGap. `control::command` accepts the motion
# (it is still the lease owner), but the exit continues to PROXY and nothing
# ever re-commands, so the Quick Start belt never moves.
# ---------------------------------------------------------------------------


def test_quick_start_over_a_running_program_actually_moves_the_belt(qemu):
    s = armed(qemu)

    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)
    assert status(s)["mode"] == "emulate"

    tx0 = len(s.tx_bytes())
    n0 = len(s.audit_events())

    # The Lobby's Quick Start button, pressed while a workout runs.
    st, body = http(s, "POST", "/api/program/quick-start", {"speed": 2.0, "incline": 0.0, "duration_minutes": 60})
    assert st == 200, body
    assert body["running"] is True, body
    assert body.get("ok") is True, body
    assert body["program"]["name"] == "Quick Start", body

    # THE CLAIM UNDER TEST: a program the device reports as RUNNING must
    # actually be driving the belt.
    deadline = time.monotonic() + 60
    seen = False
    while time.monotonic() < deadline:
        if WIRE_2 in s.tx_bytes()[tx0:]:
            seen = True
            break
        time.sleep(0.25)
    st2 = status(s)
    assert seen, (
        "quick-start reported running=True but 2.0 mph never reached the motor "
        f"wire; status={st2}; audit tail={[t for _, t in s.audit_events()[n0:]][:40]}"
    )
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 2 — hammer the write surface: accepted, refused, oversized, malformed,
# with a program running the whole time. Nothing permanent may be consumed.
# ---------------------------------------------------------------------------


def test_hammering_accepted_and_rejected_writes_does_not_degrade_the_device(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True
    s.wait_tx_contains(WIRE_3, timeout=60)

    boot_lines = len(s.lines())
    codes = {}

    def bump(c):
        codes[c] = codes.get(c, 0) + 1

    big = b'{"name":"x","intervals":[' + b'{"name":"n","duration":600,"speed":3.0,"incline":0},' * 200
    big = big[:-1] + b"]}"
    assert len(big) > 2048

    for i in range(60):
        # a) manual motion refused because the executor owns the belt (409)
        st, _ = http(s, "POST", "/api/speed", {"value": 5.0})
        bump(("speed", st))
        # b) oversized program -> 413 at admission, before parsing
        st, _ = raw_post(s, "/api/program/load", big)
        bump(("big", st))
        # c) malformed program -> 400 from the parser
        st, _ = raw_post(s, "/api/program/load", b'{"intervals":[{"duration":')
        bump(("malformed", st))
        # d) body that lies about its length: declare 128, send 4
        st, _ = raw_post(s, "/api/speed", b'{"v"', headers={"Content-Length": "4"})
        bump(("short", st))
        # e) read-only endpoints
        http(s, "GET", "/api/status")
        http(s, "GET", "/api/profiles")

    # The device is ALIVE and the workout it was given is still running on its
    # own clock, unaffected by ~360 requests.
    p = prog(s)
    assert p["running"] is True, p
    assert p["program"]["name"] == "Adversarial", p
    st = status(s)
    assert st["mode"] == "emulate", st
    assert st["fault"] is False, st

    # No reboot: the boot banner must not appear a second time.
    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED under load: {reboots}"

    # Refusals must be refusals, not 500s or hangs.
    assert codes.get(("big", 413), 0) == 60, codes
    assert codes.get(("malformed", 400), 0) == 60, codes
    assert codes.get(("speed", 409), 0) == 60, codes
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 3 — repeated program loads. Resident memory must not grow with the
# number of stored programs, and a rejected load must not disturb the stored
# one.
# ---------------------------------------------------------------------------


def test_repeated_program_loads_do_not_accumulate(qemu):
    s = armed(qemu)
    boot_lines = len(s.lines())

    maximal = {
        "name": "M" * 48,
        "intervals": [{"name": f"seg{i:02d}", "duration": 86400, "speed": 12.0, "incline": 15.0} for i in range(24)],
    }
    for i in range(80):
        st, body = http(s, "POST", "/api/program/load", maximal)
        assert st == 200, (i, body)
        assert len(body["program"]["intervals"]) == 24, body

    # A rejected load must leave the stored program untouched.
    st, _ = raw_post(s, "/api/program/load", b"not json at all")
    assert st == 400
    after = prog(s)
    assert len(after["program"]["intervals"]) == 24, after

    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED under repeated loads: {reboots}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 4 — connection churn and abrupt disconnects.
# ---------------------------------------------------------------------------


def test_connection_churn_and_abrupt_disconnects(qemu):
    s = armed(qemu)
    boot_lines = len(s.lines())

    # 120 complete TLS handshakes, each abandoned mid-request.
    for _ in range(120):
        try:
            raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
            tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
            tls.sendall(b'POST /api/speed HTTP/1.1\r\nHost: x\r\nContent-Length: 40\r\n\r\n{"val')
            tls.close()  # abrupt: body never finished
        except (OSError, ssl.SSLError):
            pass

    # The device still answers.
    assert status(s)["mode"] in ("proxy", "emulate")
    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED under connection churn: {reboots}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 5 — TLS slowloris. `max_open_sockets = 4`, `lru_purge_enable = false`,
# and the handshake runs ON THE SINGLE httpd worker with a 10 s timeout. Four
# TCP connections that never speak should therefore make the belt's only
# network Stop button unreachable.
# ---------------------------------------------------------------------------


def _stop_latency(s, timeout=8.0):
    t0 = time.monotonic()
    try:
        st, _ = http(s, "POST", "/api/program/stop", timeout=timeout)
        return time.monotonic() - t0, st
    except Exception as e:
        return time.monotonic() - t0, repr(e)


def test_idle_sockets_graded_denial_of_the_stop_button(qemu):
    """Hold N idle TCP connections and measure how long STOP takes.

    Graded on purpose: a single 4-socket failure could be a QEMU/slirp
    artifact. A clean step at exactly `max_open_sockets` is a property of the
    DEVICE's configuration, not of the emulator.
    """
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True
    s.wait_tx_contains(WIRE_3, timeout=60)

    curve = []
    held = []
    try:
        for n in range(0, 6):
            while len(held) < n:
                c = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
                c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                held.append(c)          # TCP open, NOT ONE TLS BYTE SENT
            dt, res = _stop_latency(s)
            curve.append((n, round(dt, 2), res))
            # restart so the next probe is against a moving belt again
            if res == 200:
                http(s, "POST", "/api/program/start", PROGRAM)
    finally:
        for c in held:
            c.close()
    print("STOP-LATENCY CURVE (held_idle_sockets, seconds, result):")
    for row in curve:
        print("   ", row)

    # Does the server recover once the sockets go away?
    time.sleep(1)
    recov = _stop_latency(s, timeout=15)
    print("after release:", recov)

    bad = [row for row in curve if row[2] != 200]
    assert not bad, (
        "the STOP button became unreachable while idle sockets were held, with "
        f"the belt moving: {curve}"
    )
    s.stop_pacer()


def test_one_idle_socket_blocks_the_server_for_the_handshake_timeout(qemu):
    """Pin the MECHANISM: the block equals `tls_handshake_timeout_ms` (10 s).

    `esp_https_server` runs `esp_tls_create_server_session` on the SINGLE httpd
    worker, inside `httpd_accept_conn`. A peer that completes the TCP handshake
    and then sends nothing parks that worker until the TLS handshake times out.
    """
    s = armed(qemu)
    assert _stop_latency(s, timeout=25)[1] in (200,)   # baseline, server idle

    c = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
    try:
        dt, res = _stop_latency(s, timeout=25)
        print(f"ONE-IDLE-SOCKET BLOCK: {dt:.2f}s result={res}")
        assert dt < 2.0, (
            f"one idle TCP connection blocked every other client for {dt:.1f}s "
            "(tls_handshake_timeout_ms = 10000 in net/http.rs)"
        )
    finally:
        c.close()
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 6 — the heap curve. The C++ tier died here: ~15 unauthenticated
# requests exhausted the heap, and under panic=abort exhaustion REBOOTS the
# device, which drops the relay mid-run. So the claim is not "it survived" but
# "free heap CONVERGES".
# ---------------------------------------------------------------------------

import base64
import os
import re

_HEAP_RE = re.compile(r"QTOK heap free=(\d+) minfree=(\d+) largest=(\d+)")


def heap(s):
    # The command ring can legitimately drop a probe when the storm starves the
    # shim's consumer task — the firmware now SAYS SO ("QTERR queue_full") in-
    # stead of dropping silently. Retry only on that explicit signal: silence
    # still means the device is wedged, which is what this test exists to catch.
    for attempt in range(4):
        try:
            line = s.cmd_ok("QT heap", timeout=20)
            break
        except Exception:
            dropped = [ln for ln in s.lines()[-200:] if "QTERR queue_full" in ln]
            if not dropped:
                raise  # genuine silence — do NOT paper over it
            time.sleep(0.5)
    else:
        raise AssertionError("heap probe dropped 4x running; shim consumer starved")
    m = _HEAP_RE.search(line)
    assert m, f"unparseable heap line: {line!r}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def ws_poke(s, payload: bytes, abrupt: bool):
    """Open a real WebSocket, send one frame, then close (or yank the plug)."""
    key = base64.b64encode(os.urandom(16)).decode()
    try:
        raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=10)
        tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
        tls.sendall(
            f"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        tls.recv(1024)
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n]) + mask
        else:
            hdr = bytes([0x81, 0x80 | 126, (n >> 8) & 0xFF, n & 0xFF]) + mask
        tls.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        if abrupt:
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        tls.close()
    except (OSError, ssl.SSLError, ValueError):
        pass


def test_free_heap_converges_under_a_sustained_request_storm(qemu):
    s = armed(qemu)
    boot_lines = len(s.lines())

    big = b'{"name":"x","intervals":[' + b'{"name":"n","duration":600,"speed":3.0,"incline":0},' * 200
    big = big[:-1] + b"]}"
    maximal = {
        "name": "M" * 48,
        "intervals": [
            {"name": f"s{i:02d}", "duration": 86400, "speed": 12.0, "incline": 15.0}
            for i in range(24)
        ],
    }

    curve = [("boot", *heap(s))]
    for rnd in range(6):
        for _ in range(10):
            http(s, "POST", "/api/speed", {"value": 3.0})           # accepted
            http(s, "POST", "/api/incline", {"value": 2.0})         # accepted
            raw_post(s, "/api/program/load", big)                   # 413
            raw_post(s, "/api/program/load", b'{"intervals":[{"du') # 400
            raw_post(s, "/api/speed", b"", headers={"Content-Length": "0"})
            http(s, "GET", "/api/status")
            http(s, "POST", "/api/program/load", maximal)           # 200, 1.9 KB
        for _ in range(5):
            ws_poke(s, b'{"type":"ping"}', abrupt=False)
            ws_poke(s, b"A" * 600, abrupt=True)                     # over MAX_WS_FRAME
            try:
                c = socket.create_connection(("127.0.0.1", s.http_port), timeout=5)
                c.close()                                           # TCP open/close, no TLS
            except OSError:
                pass
        curve.append((f"round{rnd}", *heap(s)))

    print("\nFREE-HEAP CURVE  (label, free, min_free_ever, largest_internal_block)")
    for row in curve:
        print(f"    {row[0]:>8}  free={row[1]:>7}  minfree={row[2]:>7}  largest={row[3]:>7}")

    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED during the storm: {reboots}"

    frees = [r[1] for r in curve]
    # CONVERGENCE, not survival: once past the first round the heap must not
    # keep giving ground. A steady leak shows up here long before it reboots.
    steady = frees[1:]
    assert min(steady) >= steady[0] - 4096, (
        f"free heap DECLINED across the storm: {curve}"
    )
    assert frees[-1] >= frees[0] - 8192, f"net heap loss over the whole storm: {curve}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# ATTACK 7 — clamps and lease, straight at the endpoints.
# ---------------------------------------------------------------------------

def test_clamps_are_the_controllers_and_are_not_pre_validated_away(qemu):
    s = armed(qemu)

    # Get into emulate with a legal manual command first.
    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, body
    s.wait_tx_contains(WIRE_3, timeout=60)
    assert status(s)["mode"] == "emulate"

    # SPEED: 0..120 tenths. 12.0 is legal, 12.1 and anything above is not.
    assert http(s, "POST", "/api/speed", {"value": 12.0})[0] == 200
    for bad in (12.1, 13.0, 99.0, 1000.0, -1.0, -0.1):
        st, body = http(s, "POST", "/api/speed", {"value": bad})
        assert st == 409, f"speed {bad} was ACCEPTED: {st} {body}"
    assert status(s)["speed"] == 12.0, status(s)

    # INCLINE: 0..30 half-percent (0..15%) at the application clamp.
    assert http(s, "POST", "/api/incline", {"value": 15.0})[0] == 200
    for bad in (15.5, 16.0, 99.0, -0.5, -1.0):
        st, body = http(s, "POST", "/api/incline", {"value": bad})
        assert st == 409, f"incline {bad} was ACCEPTED: {st} {body}"
    assert status(s)["incline"] == 15.0, status(s)

    # A refused command must not have disturbed the accepted one on the wire.
    assert status(s)["fault"] is False
    s.stop_pacer()


def test_a_program_owns_the_belt_and_a_manual_command_cannot_steal_it(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True
    s.wait_tx_contains(WIRE_3, timeout=60)

    n0 = len(s.audit_events())
    for _ in range(20):
        st, body = http(s, "POST", "/api/speed", {"value": 9.0})
        assert st == 409, f"a manual command STOLE the belt from a program: {st} {body}"

    # Refusing must be cheap: no emergency stop, no relay cycle, no fault.
    events = [t for _, t in s.audit_events()[n0:]]
    assert not [e for e in events if e.startswith("emergency:")], events[:40]
    assert not [e for e in events if e == "relay_cmd_off"], events[:40]
    st = status(s)
    assert st["mode"] == "emulate" and st["relay"] is True and st["fault"] is False, st
    assert st["speed"] == 3.0, st

    # ...and stopping the program HANDS THE BELT BACK.
    assert http(s, "POST", "/api/program/stop")[0] == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if status(s)["mode"] == "proxy":
            break
        time.sleep(0.2)
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200, f"the belt was never handed back: {st} {body}"
    s.stop_pacer()


def _belt_reaches(s, needle, tx0, timeout=45):
    """Wait for `needle` on the motor wire, but give up IMMEDIATELY on a
    latched fault.

    A latched fault is TERMINAL: `enforce_due_safety` refuses every subsequent
    motion, so once `/api/status` reports `fault: true` no amount of further
    waiting can succeed and the remaining timeout only delays the report. Worse,
    it delays it past the point where the evidence still exists — the device's
    audit ring is `EVENT_CAPACITY` (256) entries and `complete_console_frame`
    fires at the pacer's rate, so the event that LATCHED the fault is evicted by
    console-frame traffic within seconds. That is exactly the eviction hazard
    `tasks/serial_engine.rs` already calls out for VBUS_PRESENT_N.

    So: check the fault flag on every poll and raise naming it while the ring
    may still hold the cause. Not a tolerance and not a shortened bound — it
    fails EARLIER and more loudly than before.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in s.tx_bytes()[tx0:]:
            return True
        st = status(s)
        if st.get("fault"):
            interesting = [
                (i, t) for i, t in s.audit_events() if t != "complete_console_frame"
            ]
            raise AssertionError(
                "the controller has a LATCHED FAULT, so the belt can never "
                f"reach {needle!r} — every further motion is refused.\n"
                f"  status: {st}\n"
                f"  non-console audit events: {interesting[-30:] or 'NONE LEFT — '
                'the ring was flushed by complete_console_frame before the dump'}\n"
                "  A latched fault under QEMU is most often the 10 ms "
                "RELAY_FEEDBACK_DEADLINE window failing closed "
                "(`entry_feedback_timeout`) because the host descheduled the "
                "guest mid-transfer — see bead precor-9_3x-9aj. Confirm from "
                "the audit events above before treating it as a firmware "
                "regression, and do NOT widen the deadline."
            )
        time.sleep(0.25)
    return False


def test_quick_start_from_idle_works(qemu):
    """Scope the quick-start defect: from IDLE it must be fine."""
    s = armed(qemu)
    tx0 = len(s.tx_bytes())
    st, body = http(s, "POST", "/api/program/quick-start",
                    {"speed": 2.0, "incline": 0.0, "duration_minutes": 60})
    assert st == 200 and body["running"] is True, body
    # The app types quick-start as GenericOkResponse, whose `ok` has no
    # kotlinx default: without it the decode throws and the user is told
    # "Failed to start workout" while standing on a belt that just started.
    assert body.get("ok") is True, body
    assert _belt_reaches(s, WIRE_2, tx0), f"idle quick-start never moved the belt; {status(s)}"
    s.stop_pacer()


def test_switching_workouts_with_start_body_works(qemu):
    """The NEIGHBOURING path — `POST /api/program/start` with a new body over a
    running program — must still drive the belt. It does not release the
    lease, which is exactly why it survives where quick-start does not."""
    s = armed(qemu)
    st, _ = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200
    s.wait_tx_contains(WIRE_3, timeout=60)
    tx0 = len(s.tx_bytes())

    other = {"name": "Other", "intervals": [{"name": "X", "duration": 600, "speed": 2.0, "incline": 0}]}
    st, body = http(s, "POST", "/api/program/start", other)
    assert st == 200 and body["running"] is True, body
    assert _belt_reaches(s, WIRE_2, tx0), f"switching workouts never moved the belt; {status(s)}"
    s.stop_pacer()
