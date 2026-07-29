"""Adversarial memory/belt review of the Slice 5 persistence tier.

A GATE, and it did not start as one. These are reproduction cases written by a
reviewer; each asserts the SAFE behaviour so a failure names the defect. Three
of them were RED when they were written:

  * `test_a_slow_writer_cannot_hold_the_only_httpd_worker` — one TLS connection
    sending a space every 0.5 s of a 900-byte body held the SINGLE httpd worker
    for the whole 60 s test window, with the belt moving; STOP could not even
    complete its TLS handshake. Fixed by `net::api::Deadline` +
    `abandon_body`. Now: baseline 0.26 s, blocked 0.51 s.
  * `test_quick_start_does_not_write_its_progress_into_the_last_loaded_history`
    and `test_a_completed_quick_start_does_not_mark_another_program_completed`
    — Quick Start checkpointed into the PREVIOUSLY loaded program's entry and
    marked it completed, refusing `/resume` for it forever. Fixed by making
    `set_current` an else rather than an omission in `net::program::post_impl`.

The other five were green and are kept as standing assertions, which is the
point: `test_a_history_load_hands_the_belt_back` passed only because the
interval executor released the lease on its next tick — an unstated rescue in
another task — and `net::records::install` now releases it itself.

WIRED INTO tools/sweep.sh, because the same repository has twice shipped a
committed, passing test that NOTHING RAN (verify_harness_copy.py,
check_log_contract.sh, test_store_persistence.py). The two heap-convergence
cases cost ~5 minutes between them and run under DEEP=1; the belt-availability
and history-correctness cases run every sweep.
"""

from __future__ import annotations

import json
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
WIRE_3 = b"[hmph:12C]"

PROGRAM = {
    "name": "Adversarial",
    "intervals": [
        {"name": "A", "duration": 600, "speed": 3.0, "incline": 0},
        {"name": "B", "duration": 600, "speed": 4.0, "incline": 1.0},
    ],
}


def http(s, method, path, body=None, timeout=20):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def raw_req(s, method, path, body: bytes, headers=None, timeout=20):
    url = f"https://127.0.0.1:{s.http_port}{path}"
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=httpc.tls_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def booted(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    return s


def armed(qemu):
    s = booted(qemu)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def maximal(name):
    return {
        "name": name,
        "intervals": [{"name": f"s{i:02d}", "duration": 86400, "speed": 12.0, "incline": 15.0} for i in range(24)],
    }


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
# R1 — a history/workout LOAD must hand the belt back, exactly as
# `POST /api/program/load` does.
#
# `net::records::install` drives the stop plan with `release_belt = false`,
# while `net::program::post_impl` V_LOAD drives the identical plan with
# `release_belt = true`. If the executor keeps the lease, a manual command is
# refused 409 forever afterwards — the belt is stuck under a program that is
# not running.
# ---------------------------------------------------------------------------
def test_a_history_load_hands_the_belt_back(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    st, hist = http(s, "GET", "/api/programs/history")
    assert st == 200 and hist, hist
    hid = hist[0]["id"]

    st, body = http(s, "POST", f"/api/programs/history/{hid}/load")
    assert st == 200 and body.get("ok") is True, body

    st, p = http(s, "GET", "/api/program")
    assert p["running"] is False, p

    # The belt is idle and no program is running: a manual command must work.
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        last = http(s, "POST", "/api/speed", {"value": 2.0})
        if last[0] == 200:
            break
        time.sleep(0.5)
    assert last[0] == 200, (
        "after POST /api/programs/history/{id}/load the executor still owns "
        f"the belt lease; manual control is dead: {last}"
    )
    s.stop_pacer()


def test_a_workout_load_hands_the_belt_back(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/workouts", {"program": PROGRAM, "source": "manual"})
    assert st == 200, body
    wid = body["workout"]["id"]

    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    st, body = http(s, "POST", f"/api/workouts/{wid}/load")
    assert st == 200 and body.get("ok") is True, body
    st, p = http(s, "GET", "/api/program")
    assert p["running"] is False, p

    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        last = http(s, "POST", "/api/speed", {"value": 2.0})
        if last[0] == 200:
            break
        time.sleep(0.5)
    assert last[0] == 200, f"after POST /api/workouts/{{id}}/load manual control is dead: {last}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# R2 — a SLOW READER of a chunked list must not hold the single httpd worker.
#
# IDF runs ONE worker. `net::records::list_impl` streams up to 20 records in
# 512-byte chunks, each `httpd_resp_send_chunk` blocking up to
# `send_wait_timeout` (1 s). A client that keeps the window barely open drags
# the response out for as long as it likes, and NOTHING else is served —
# including `POST /api/program/stop` with the belt moving. There is no
# request-duration bound, only a per-send one.
# ---------------------------------------------------------------------------
def _slow_reader(s, path, stop_evt, chunk=64, interval=0.5):
    raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=20)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
    tls.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    got = 0
    try:
        while not stop_evt.is_set():
            time.sleep(interval)
            try:
                tls.settimeout(0.5)
                b = tls.recv(chunk)
            except (socket.timeout, ssl.SSLError):
                continue
            if not b:
                break
            got += len(b)
    except OSError:
        pass
    finally:
        try:
            tls.close()
        except OSError:
            pass
    return got


def test_a_slow_reader_of_the_history_list_cannot_block_the_stop_button(qemu):
    s = armed(qemu)
    # Fill the ring with 20 maximal programs so the list body is ~40 KB.
    for i in range(20):
        st, _ = http(s, "POST", "/api/program/load", maximal(f"Prog{i:02d}"))
        assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 20, len(hist)
    body_bytes = len(json.dumps(hist))
    print(f"history list body ~= {body_bytes} bytes")

    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    # Baseline: how long does STOP take with nobody else on the server?
    t0 = time.monotonic()
    st, _ = http(s, "POST", "/api/program/stop", timeout=30)
    base = time.monotonic() - t0
    assert st == 200
    http(s, "POST", "/api/program/start", PROGRAM)
    s.wait_tx_contains(WIRE_3, timeout=60)

    stop_evt = threading.Event()
    th = threading.Thread(target=_slow_reader, args=(s, "/api/programs/history", stop_evt), daemon=True)
    th.start()
    time.sleep(2.0)  # let the reader get the response started

    t0 = time.monotonic()
    try:
        st, _ = http(s, "POST", "/api/program/stop", timeout=60)
        res = st
    except Exception as e:  # noqa: BLE001
        res = repr(e)
    dt = time.monotonic() - t0
    stop_evt.set()
    th.join(timeout=10)
    print(f"STOP latency: baseline={base:.2f}s  under-slow-reader={dt:.2f}s result={res}")

    assert res == 200, f"STOP failed while a slow reader held the worker: {res} after {dt:.1f}s"
    assert dt < base + 5.0, (
        f"a single slow-reading client held the ONE httpd worker for {dt:.1f}s "
        f"(baseline {base:.2f}s) — the belt's network Stop button was "
        "unreachable for that whole time"
    )
    s.stop_pacer()


# ---------------------------------------------------------------------------
# R3 — heap convergence across the NEW store endpoints specifically. The
# existing storm (test_adversarial.py) predates them and never touches
# /api/workouts, /api/runs, /api/programs/history or the id-bearing routes.
# ---------------------------------------------------------------------------
def test_free_heap_converges_under_a_storm_on_the_record_endpoints(qemu):
    s = booted(qemu)
    boot_lines = len(s.lines())

    big = b'{"program":{"name":"x","intervals":[' + b'{"name":"n","duration":600,"speed":3.0,"incline":0},' * 200
    big = big[:-1] + b']},"source":"manual"}'
    assert len(big) > 2048

    # Warm the TLS/request path.
    for i in range(5):
        http(s, "POST", "/api/program/load", maximal(f"warm{i}"))
    for i in range(6):
        http(s, "POST", "/api/workouts", {"program": maximal(f"W{i}"), "source": "manual"})

    curve = [("boot", heap(s))]
    for rnd in range(5):
        for _ in range(10):
            http(s, "GET", "/api/programs/history")
            http(s, "GET", "/api/workouts")
            http(s, "GET", "/api/runs")
            raw_req(s, "POST", "/api/workouts", big)  # 413
            raw_req(s, "POST", "/api/workouts", b"{not json")  # 200 ok:false
            raw_req(s, "POST", "/api/workouts/w-nope/load", b"")  # 200 ok:false
            raw_req(s, "DELETE", "/api/workouts/w-nope", b"")
            raw_req(s, "PUT", "/api/workouts/w-nope", b'{"name":"x"}')
            raw_req(s, "POST", "/api/programs/history/h-nope/load", b"")
            raw_req(s, "POST", "/api/programs/history/" + "A" * 200 + "/load", b"")
            http(s, "POST", "/api/program/load", maximal(f"S{rnd}"))
        curve.append((f"round{rnd}", heap(s)))

    print("\nRECORD-ENDPOINT HEAP CURVE (label, free)")
    for lbl, f in curve:
        print(f"    {lbl:>8}  free={f}")

    reboots = [ln for ln in s.lines()[boot_lines:] if "phase-1 safety core started" in ln]
    assert not reboots, f"the device REBOOTED during the record storm: {reboots}"

    frees = [f for _, f in curve]
    steady = frees[1:]
    assert min(steady) >= steady[0] - 4096, f"free heap DECLINED: {curve}"
    assert frees[-1] >= frees[0] - 8192, f"net heap loss over the storm: {curve}"


# ---------------------------------------------------------------------------
# R4 — a REJECTED request must cost nothing permanent. The C++ tier answered
# 200 {"ok":false} and permanently consumed ~14 KB per refusal.
# ---------------------------------------------------------------------------
def test_rejected_record_writes_cost_nothing_permanent(qemu):
    s = booted(qemu)
    for i in range(5):
        http(s, "POST", "/api/program/load", maximal(f"warm{i}"))
    h0 = heap(s)
    for _ in range(200):
        raw_req(s, "POST", "/api/workouts", b'{"history_id":"nope"}')
        raw_req(s, "PUT", "/api/workouts/nope", b'{"name":"x"}')
        raw_req(s, "DELETE", "/api/workouts/nope", b"")
    h1 = heap(s)
    print(f"600 refused record writes: heap {h0} -> {h1}")
    assert h1 >= h0 - 4096, f"refusals consumed {h0 - h1} bytes permanently"
    st, w = http(s, "GET", "/api/workouts")
    assert st == 200 and w == [], w


# ---------------------------------------------------------------------------
# R5 — a SLOW WRITER must not hold the single httpd worker indefinitely.
#
# `recv_wait_timeout` is a PER-RECV budget. A client that sends one byte every
# 0.5 s of a 2048-byte body never trips it, and `read_slot_body` /
# `read_program` loop until `got == declared`. There is no total request
# duration bound, so the one worker is held for as long as the client likes —
# and `POST /api/program/stop` is not served meanwhile, with the belt moving.
# ---------------------------------------------------------------------------
def test_a_slow_writer_cannot_hold_the_only_httpd_worker(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    t0 = time.monotonic()
    assert http(s, "POST", "/api/program/pause", timeout=30)[0] == 200
    base = time.monotonic() - t0
    http(s, "POST", "/api/program/pause")  # un-pause

    stop_evt = threading.Event()

    def dribble():
        raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=20)
        tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
        tls.sendall(
            b"POST /api/workouts HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: application/json\r\nContent-Length: 900\r\n\r\n"
        )
        sent = 0
        try:
            while not stop_evt.is_set() and sent < 900:
                tls.sendall(b" ")
                sent += 1
                time.sleep(0.5)
        except OSError:
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    th = threading.Thread(target=dribble, daemon=True)
    th.start()
    time.sleep(3.0)

    t0 = time.monotonic()
    try:
        res = http(s, "POST", "/api/program/stop", timeout=60)[0]
    except Exception as e:  # noqa: BLE001
        res = repr(e)
    dt = time.monotonic() - t0
    stop_evt.set()
    th.join(timeout=10)
    print(f"STOP under slow-writer: baseline={base:.2f}s  blocked={dt:.2f}s result={res}")
    assert res == 200, f"STOP failed while a dribbling client held the worker: {res} after {dt:.1f}s"
    assert dt < base + 5.0, (
        f"one client sending 1 byte per 0.5 s held the ONE httpd worker for "
        f"{dt:.1f}s (baseline {base:.2f}s); the network Stop button was dead "
        "for that whole time, with the belt moving"
    )
    s.stop_pacer()


# ---------------------------------------------------------------------------
# R6 — the session recorder must never write one program's progress into
# another program's history entry.
#
# `post_impl` calls `session::set_current` ONLY when `record_loaded` returned
# an id. Quick Start deliberately writes no history entry, so it leaves
# CURRENT_HISTORY pointing at the PREVIOUSLY loaded program — and the
# recorder then checkpoints the Quick Start's interval/elapsed/completed into
# that entry.
# ---------------------------------------------------------------------------
def test_quick_start_does_not_write_its_progress_into_the_last_loaded_history(qemu):
    s = armed(qemu)
    long_prog = {
        "name": "LongOne",
        "intervals": [{"name": "A", "duration": 3600, "speed": 3.0, "incline": 0}],
    }
    st, _ = http(s, "POST", "/api/program/load", long_prog)
    assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    assert st == 200 and hist[0]["program"]["name"] == "LongOne", hist
    hid = hist[0]["id"]
    assert hist[0]["last_elapsed"] == 0.0 and hist[0]["completed"] is False, hist[0]

    st, body = http(s, "POST", "/api/program/quick-start", {"speed": 2.0, "incline": 0.0, "duration_minutes": 1})
    assert st == 200 and body["running"] is True, body
    assert body["program"]["name"] == "Quick Start", body

    time.sleep(15)

    st, hist = http(s, "GET", "/api/programs/history")
    entry = [h for h in hist if h["id"] == hid]
    assert entry, (hid, hist)
    e = entry[0]
    print(
        f"LongOne history entry after 15 s of Quick Start: {e['last_elapsed']=} "
        f"{e['last_interval']=} {e['completed']=}"
    )
    assert e["last_elapsed"] == 0.0, (
        "the Quick Start's progress was checkpointed into the previously " f"LOADED program's history entry: {e}"
    )
    http(s, "POST", "/api/program/stop")
    s.stop_pacer()


def test_a_completed_quick_start_does_not_mark_another_program_completed(qemu):
    """The consequence of R6: `completed` gates /resume."""
    s = armed(qemu)
    long_prog = {
        "name": "LongOne",
        "intervals": [{"name": "A", "duration": 3600, "speed": 3.0, "incline": 0}],
    }
    assert http(s, "POST", "/api/program/load", long_prog)[0] == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]

    st, body = http(s, "POST", "/api/program/quick-start", {"speed": 2.0, "incline": 0.0, "duration_minutes": 1})
    assert st == 200 and body["running"] is True, body

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        st, p = http(s, "GET", "/api/program")
        if p["running"] is False:
            break
        time.sleep(1)
    time.sleep(3)

    st, hist = http(s, "GET", "/api/programs/history")
    e = [h for h in hist if h["id"] == hid][0]
    print(f"LongOne after a COMPLETED Quick Start: completed={e['completed']} " f"last_elapsed={e['last_elapsed']}")
    st, r = http(s, "POST", f"/api/programs/history/{hid}/resume")
    print("resume LongOne ->", st, r if not isinstance(r, dict) else {k: r[k] for k in ("ok", "error") if k in r})
    assert e["completed"] is False, f"a Quick Start marked an unrelated program COMPLETED: {e}"
    s.stop_pacer()


# ---------------------------------------------------------------------------
# R7 — a route that DECLINES its body must not be dribble-able either.
#
# The body readers are bounded by `net::api::Deadline`. A handler that answers
# WITHOUT reading — the multi-profile refusals, an avatar upload this device has
# nowhere to put — is not finished with the connection: `httpd_req_delete`
# purges whatever is left through the per-recv timeout with no deadline near it.
# So the four routes added for the app's picker reopened exactly the hole R5
# closed, on a body that could be a megabyte.
# ---------------------------------------------------------------------------
def test_a_route_that_declines_its_body_cannot_hold_the_worker(qemu):
    s = armed(qemu)
    st, body = http(s, "POST", "/api/program/start", PROGRAM)
    assert st == 200 and body["running"] is True, body
    s.wait_tx_contains(WIRE_3, timeout=60)

    t0 = time.monotonic()
    assert http(s, "POST", "/api/program/pause", timeout=30)[0] == 200
    base = time.monotonic() - t0
    http(s, "POST", "/api/program/pause")  # un-pause

    stop_evt = threading.Event()

    def dribble():
        raw = socket.create_connection(("127.0.0.1", s.http_port), timeout=20)
        tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
        # An "avatar upload": a body the device has nowhere to put, delivered a
        # byte at a time.
        tls.sendall(
            b"POST /api/profiles/local/avatar HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: image/jpeg\r\nContent-Length: 65536\r\n\r\n"
        )
        sent = 0
        try:
            while not stop_evt.is_set() and sent < 65536:
                tls.sendall(b"\x00")
                sent += 1
                time.sleep(0.5)
        except OSError:
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    th = threading.Thread(target=dribble, daemon=True)
    th.start()
    time.sleep(3.0)

    t0 = time.monotonic()
    try:
        res = http(s, "POST", "/api/program/stop", timeout=60)[0]
    except Exception as e:  # noqa: BLE001
        res = repr(e)
    dt = time.monotonic() - t0
    stop_evt.set()
    th.join(timeout=10)
    print(f"STOP under declined-body dribbler: baseline={base:.2f}s blocked={dt:.2f}s result={res}")
    assert res == 200, f"STOP failed while a dribbler held the worker: {res} after {dt:.1f}s"
    assert dt < base + 5.0, (
        f"a client dribbling a body into a route that DECLINES it held the ONE "
        f"httpd worker for {dt:.1f}s (baseline {base:.2f}s)"
    )
    s.stop_pacer()
