"""Network-level QEMU scenarios (N1..N13) for the native server tier.

Boots the ESP32TAP_QEMU_TEST image with the openeth NIC attached
(-nic user + hostfwd -> guest :8000) and exercises the REAL stack:
in-guest EC P-256 cert generation, esp_https_server TLS, the router/
executor RPC path into the SafetyController, /ws streaming, mDNS
advertisement (pcap), and littlefs run-record persistence across a
hard kill.

Scenario map (design doc §5 L2):
  N1 banner + /api/status over HTTPS   } one boot
  N3 incline snap 5.3 -> 5.5           } (test_n1_n3_https_status_incline)
  N5 mDNS pcap assertion               } (test_n5_mdns_pcap)
  N2 POST /api/speed drives the controller Emulate entry sequence
  N4 WS triple-send order + 1 Hz session frames
  N6 30 s checkpoint -> hard kill -> reboot -> run "disconnect"
  N7 console takeover while a program runs -> paused + bounce message
  N8 slowloris body cannot block POST /api/program/stop (duration bound)
  N9 WS client with NO hello still receives the broadcast stream
  N10 WS "kv" frames (Debug screen + status.motor merge) are emitted
  N11 slowloris HEADER block cannot block POST /api/program/stop
  N12 a real (tens of KB) multipart GPX upload reaches its 501
  N13 the app's 3-connection emergencyStop burst does not purge /ws
"""

from __future__ import annotations

import time

import pytest
import synth
from net_session import RawHttpsConn
from qemu_session import HarnessError, QemuSession

pytestmark = [pytest.mark.qemu, pytest.mark.net]

STATUS_MANDATORY = ["proxy", "emulate", "emu_speed", "emu_speed_mph", "emu_incline", "treadmill_connected"]
PACER_INTERVAL = 0.15


def _wait_emulating(s: QemuSession, timeout: float = 60.0, **want) -> dict:
    """Poll QTSTATE until mode EMULATING (+ extra field expectations)."""
    deadline = time.monotonic() + timeout
    st = {}
    while time.monotonic() < deadline:
        st = s.state()
        if st["mode"] == "EMULATING" and all(st[k] == v for k, v in want.items()):
            return st
        time.sleep(0.5)
    raise AssertionError(f"never reached EMULATING {want}; last {st}")


def test_n1_n3_https_status_incline(net_qemu):
    s = net_qemu()
    s.wait_server_up()

    # N1: banner over TLS (cert generated in-guest this boot).
    status, banner = s.get("/")
    assert status == 200
    assert banner == {"service": "precor-treadmill", "api": "/api", "ws": "/ws"}

    # N1: /api/status carries every Kotlin-mandatory key.
    status, d = s.get("/api/status")
    assert status == 200
    for key in STATUS_MANDATORY:
        assert key in d, f"missing status key {key}"
    assert d["type"] == "status"
    assert d["treadmill_connected"] is True
    assert d["speed"] is None  # no motor traffic in this boot

    # N3: incline snap happens server-side before the controller.
    status, d = s.post("/api/incline", {"value": 5.3})
    assert status == 200
    assert d["emu_incline"] == 5.5
    st = s.state()
    assert st["incline"] == 11  # controller half-pct units

    # Out-of-scope + unknown endpoints behave per contract.
    status, d = s.post("/api/gpx/upload")
    assert status == 501
    status, d = s.post("/api/chat", {"message": "hi"})
    assert status == 503
    status, d = s.get("/api/nonsense")
    assert status == 404

    # Hostile wire input: malformed JSON and an oversized body are both
    # rejected with 400 without disturbing the server.
    status, d = s.request("POST", "/api/speed", raw_body="{oops")
    assert status == 400
    status, d = s.request("POST", "/api/speed", raw_body="x" * 9000)
    assert status == 400

    # Profiles are native: a fresh device falls back to the synthesized
    # guest profile, so the unchanged app routes straight to the Lobby.
    status, d = s.get("/api/profile/active")
    assert status == 200
    assert d["guest_mode"] is False
    assert d["profile"]["name"] == "Guest"
    status, d = s.get("/api/profiles")
    assert status == 200
    assert d == []


def test_n2_speed_drives_emulate_entry(net_qemu):
    s = net_qemu()
    s.wait_server_up()
    # Healthy console + auto K1 (QemuTestSafetyIo default) so the
    # controller can grant Emulate entry.
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    status, d = s.post("/api/speed", {"value": 9.9})
    assert status == 200
    assert d["emu_speed"] == 99
    assert d["emulate"] is True
    assert d["proxy"] is False

    # The REST write drove the REAL controller entry sequence (gap ->
    # feedback), not a flag: the guest settles in EMULATING with the
    # commanded speed, and the audit ring shows the entry ordering.
    st = _wait_emulating(s, speed=99)
    assert st["relay"] == 1 and st["tx"] == 1
    s.wait_audit("wait_entry_gap", timeout=10)
    s.wait_audit("relay_cmd_on", timeout=10)
    s.wait_audit("feedback_emulate_stable", timeout=10)

    # Clamp parity: 99 mph -> 120 tenths.
    status, d = s.post("/api/speed", {"value": 99})
    assert d["emu_speed"] == 120

    # A manual program + session auto-started (server.py ensure_manual).
    status, p = s.get("/api/program")
    assert p["running"] is True
    assert p["program"]["manual"] is True
    assert p["program"]["name"] == "60-Min Manual"


def test_n4_ws_triple_send_and_session_stream(net_qemu):
    s = net_qemu()
    s.wait_server_up()

    # Fresh boot: on-connect sends status only (no session, no program).
    ws = s.ws_connect()
    first = ws.recv_text()
    assert first["type"] == "status"
    ws.close()

    # Start a session (auto-manual program) and reconnect: ordered
    # triple-send status -> session -> program.
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    status, _ = s.post("/api/speed", {"value": 3.0})
    assert status == 200

    ws = s.ws_connect()
    f1 = ws.recv_text()
    assert f1["type"] == "status"
    f2 = ws.recv_text()
    assert f2["type"] == "session"
    assert f2["active"] is True
    f3 = ws.recv_text()
    assert f3["type"] == "program"
    assert f3["running"] is True

    # 1 Hz session tick frames keep arriving.
    seen_sessions = 0
    deadline = time.monotonic() + 60
    while seen_sessions < 3 and time.monotonic() < deadline:
        frame = ws.recv_text(timeout=30)
        if frame["type"] == "session":
            seen_sessions += 1
            for key in ("active", "elapsed", "distance", "vert_feet", "wall_started_at"):
                assert key in frame, f"missing session key {key}"
    assert seen_sessions >= 3
    ws.close()


def test_n5_mdns_pcap(net_qemu):
    s = net_qemu()
    s.wait_server_up()
    # mDNS probes + announcements land in the filter-dump pcap shortly
    # after service registration (slirp never forwards multicast back,
    # so the pcap IS the assertion surface — proven in the net
    # experiment).
    deadline = time.monotonic() + 60
    pcap = b""
    while time.monotonic() < deadline:
        pcap = s.pcap_bytes()
        if b"_treadmill" in pcap and b"scheme=https" in pcap:
            break
        time.sleep(2.0)
    assert b"_treadmill" in pcap, "mDNS service type never announced"
    assert b"scheme=https" in pcap
    assert b"path=/" in pcap
    assert b"treadmill" in pcap  # instance/hostname


def test_n6_checkpoint_survives_hard_kill(net_qemu):
    s = net_qemu()
    s.wait_server_up()
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    # A workout whose fingerprint links history <-> run records.
    program = {
        "name": "N6 Run",
        "intervals": [{"name": "All", "duration": 600, "speed": 3.0, "incline": 0.0}],
    }
    status, d = s.post("/api/workouts", {"program": program, "source": "generated"})
    assert d["ok"] is True
    wid = d["workout"]["id"]
    status, d = s.post(f"/api/workouts/{wid}/load")
    assert d["ok"] is True
    status, d = s.post("/api/program/start")
    assert d["running"] is True

    # >= 35 s of guest run time so the 30 s checkpoint fires (record
    # inserted as in_progress, persisted to littlefs).
    s.wait_guest_uptime_delta(40, timeout=240.0)
    s.stop_pacer()

    # Hard kill (docker kill — no graceful shutdown path runs).
    s.close()

    # Reboot the SAME flash image (merge=False keeps littlefs intact).
    s2 = net_qemu(merge=False)
    s2.wait_server_up()
    status, hist = s2.get("/api/programs/history")
    assert status == 200
    entry = next(e for e in hist if e["name"] == "N6 Run")
    run = entry["last_run"]
    assert run is not None, "checkpointed run record lost across reboot"
    assert run["end_reason"] == "disconnect"
    assert run["elapsed"] >= 25


def test_n7_console_takeover_pauses_program(net_qemu):
    s = net_qemu()
    s.wait_server_up()
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    status, d = s.post("/api/speed", {"value": 3.0})
    assert status == 200
    _wait_emulating(s, speed=30)
    # Let the executor's ~250 ms edge sampler observe the EMULATING
    # phase before we yank the belt away (a human takeover is seconds
    # after entry in reality).
    time.sleep(1.5)

    ws = s.ws_connect()
    # Drain the triple-send.
    for _ in range(3):
        ws.recv_text()

    # Console button press while emulating: hmph change on the console
    # tap -> emergency_stop(console_takeover) -> PROXY. The executor
    # surfaces the arbitration as a paused program + the exact python
    # bounce message.
    s.set_pacer_payload(synth.console_cycle_bytes(55, 0))
    s.wait_audit("emergency:console_takeover", timeout=30)

    deadline = time.monotonic() + 30
    bounced = None
    while time.monotonic() < deadline:
        frame = ws.recv_text(timeout=20)
        if frame["type"] == "program" and frame.get("encouragement"):
            bounced = frame
            break
    assert bounced is not None, "no program bounce frame seen"
    assert bounced["paused"] is True
    assert bounced["encouragement"] == "Console took over — paused"
    ws.close()


def test_n8_slowloris_cannot_block_stop(net_qemu):
    """A dribbling client must not hold the single httpd worker.

    esp_https_server serves every socket from ONE worker task, and the
    per-recv timeout is not a bound on the request as a whole: a client
    that sends one body byte just inside each recv window held the
    worker for hours, delaying every other request — including the one
    that stops the belt. The body phase is now bounded in total wall
    clock, so the worker is released and Stop still lands.
    """
    s = net_qemu()
    s.wait_server_up()
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    # A program is running and driving the belt — this is exactly when
    # Stop must not be blockable.
    status, _ = s.post("/api/speed", {"value": 3.0})
    assert status == 200
    _wait_emulating(s, speed=30)

    # Announce a body we will dribble one byte at a time, well inside
    # the per-recv timeout so no single recv ever times out.
    slow = RawHttpsConn("127.0.0.1", s.http_port)
    slow.send_headers("POST", "/api/speed", 4096)
    slow.send_raw(b"{")

    def dribble(n: int) -> None:
        for _ in range(n):
            try:
                slow.send_raw(b" ")
            except OSError:
                return
            time.sleep(1.5)

    import threading

    t = threading.Thread(target=dribble, args=(20,), daemon=True)
    t.start()
    try:
        # Stop must complete promptly on a SECOND connection while the
        # slowloris is still dribbling. Without the total-duration bound
        # this request waits behind the hostile one indefinitely.
        began = time.monotonic()
        status, d = s.post("/api/program/stop", timeout=45.0)
        elapsed = time.monotonic() - began
        assert status == 200, d
        assert elapsed < 30.0, f"stop took {elapsed:.1f}s behind a slowloris"
    finally:
        slow.close()
        t.join(timeout=5)

    # The belt is actually stopped (not just a 200).
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if s.state()["speed"] == 0:
            break
        time.sleep(0.5)
    assert s.state()["speed"] == 0

    # The server is healthy afterwards: the hostile socket was dropped,
    # not the whole worker.
    status, _ = s.get("/api/status")
    assert status == 200


def test_n9_ws_client_without_hello_still_gets_the_stream(net_qemu):
    """Registration must not depend on hello delivery.

    The 101 handshake has already completed by the time the hello
    frames are built, so a client that loses them must still be
    registered: otherwise the app reports "connected", receives nothing
    for the life of the socket, never reconnects (it only reconnects on
    onClosed/onFailure), and the executor's dead-man later pauses a
    running program. QT wsdrophello forces exactly that path.
    """
    s = net_qemu()
    s.wait_server_up()
    s.cmd_ok("QT wsdrophello 1")

    ws = s.ws_connect()
    # The injection must really drop the hello, or this scenario would
    # pass vacuously: nothing else broadcasts on an idle boot (no
    # session, no motor traffic), so the client sees NOTHING here.
    try:
        stray = ws.recv_text(timeout=20)
        raise AssertionError(f"hello was not dropped: {stray}")
    except HarnessError:
        pass

    # The client is nonetheless REGISTERED: drive a change so a status
    # frame is broadcast, and require it lands on this very socket.
    # (Before the fix, registration lived inside the hello-delivery
    # callback, so this client would stay invisible forever.)
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    status, _ = s.post("/api/incline", {"value": 3.0})
    assert status == 200

    seen = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        frame = ws.recv_text(timeout=30)
        if frame["type"] == "status":
            seen = frame
            break
    assert seen is not None, "registered WS client never received a frame"
    assert "emu_incline" in seen
    ws.close()
    time.sleep(2.0)  # let httpd reap the socket before reusing the slot

    # With the injection off, the ordered hello triple-send still works.
    s.cmd_ok("QT wsdrophello 0")
    ws2 = s.ws_connect()
    assert ws2.recv_text()["type"] == "status"
    ws2.close()


def test_n10_kv_frames_reach_the_app(net_qemu):
    """server.py's continuous WS traffic is {"type":"kv",...} frames —
    the Debug screen's only input and the incremental status.motor
    merge. The native tier emitted none, so both went stale."""
    s = net_qemu()
    s.wait_server_up()
    ws = s.ws_connect()

    # Motor tap traffic -> decoded KV -> kv frames on the socket.
    # (repeat: the tap is unpaced under QEMU and the executor samples
    # the cache at 1 Hz)
    s.send_motor(synth.motor_reply("hmph", "78") + synth.motor_reply("inc", "A"))

    seen = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        frame = ws.recv_text(timeout=30)
        if frame["type"] == "kv":
            seen = frame
            break
    assert seen is not None, "no kv frames emitted on /ws"
    # Exact KVMessage shape the Kotlin model deserializes.
    for key in ("source", "key", "value"):
        assert key in seen, f"missing kv key {key}"
    assert seen["source"] == "motor"
    assert isinstance(seen["value"], str)
    ws.close()


def test_n11_header_slowloris_cannot_block_stop(net_qemu):
    """The BODY bound is not enough: the header phase must be bounded too.

    IDF's httpd_parse_req() loops read_block() until the header block is
    complete with only the per-recv SO_RCVTIMEO — no total bound. A
    client dribbling the request line one byte at a time never times out
    a single recv and never completes the header block, so the single
    shared worker never returns to select()/accept(): even a NEW
    connection could not finish its TLS handshake. N8 sends a COMPLETE
    header block and dribbles only the body, so it proves nothing here.
    """
    s = net_qemu()
    s.wait_server_up()
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    status, _ = s.post("/api/speed", {"value": 3.0})
    assert status == 200
    _wait_emulating(s, speed=30)

    # Dribble the HEADER block, one byte at a time, never terminating it.
    slow = RawHttpsConn("127.0.0.1", s.http_port)
    header = b"POST /api/speed HTTP/1.1\r\nHost: h\r\nX-Pad: "

    def dribble() -> None:
        for byte in header:
            try:
                slow.send_raw(bytes([byte]))
            except OSError:
                return
            time.sleep(1.5)
        for _ in range(40):
            try:
                slow.send_raw(b"a")
            except OSError:
                return
            time.sleep(1.5)

    import threading

    t = threading.Thread(target=dribble, daemon=True)
    t.start()
    time.sleep(4.0)  # let the guest be well inside the header phase
    try:
        began = time.monotonic()
        status, d = s.post("/api/program/stop", timeout=60.0)
        elapsed = time.monotonic() - began
        assert status == 200, d
        assert elapsed < 30.0, f"stop took {elapsed:.1f}s behind a header slowloris"
    finally:
        slow.close()
        t.join(timeout=5)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if s.state()["speed"] == 0:
            break
        time.sleep(0.5)
    assert s.state()["speed"] == 0

    status, _ = s.get("/api/status")
    assert status == 200


def test_n12_real_gpx_upload_gets_the_501_not_body_too_large(net_qemu):
    """A real GPX route is tens of KB — far over the 8 KB JSON body cap.

    The transport used to short-circuit it to 400 {"error":"body too
    large"} before the router ever saw the path, so the app's toast read
    "GPX upload failed: HTTP 400" instead of the intended message. The
    host router test could not catch it: it calls handle_request()
    directly, bypassing the transport cap.
    """
    s = net_qemu()
    s.wait_server_up()

    boundary = "----esp32tapBoundary"
    track = "<trkpt lat='37.1' lon='-122.1'><ele>10</ele></trkpt>" * 900
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="route.gpx"\r\n'
        "Content-Type: application/gpx+xml\r\n\r\n"
        f"<?xml version='1.0'?><gpx><trk><trkseg>{track}</trkseg></trk></gpx>\r\n"
        f"--{boundary}--\r\n"
    )
    assert len(body) > 32 * 1024, "the point is a body far over the 8 KB cap"

    status, d = s.post("/api/gpx/upload", raw_body=body, timeout=60.0)
    assert status == 501, f"{status} {d}"
    assert d["ok"] is False
    assert "not supported" in d["error"]

    # The connection/worker survived the drain.
    status, _ = s.get("/api/status")
    assert status == 200


def test_n13_ws_survives_the_apps_concurrent_request_burst(net_qemu):
    """The app's emergencyStop fires three concurrent REST requests.

    OkHttp puts them on three separate connections, on top of the
    WebSocket. With only 3 httpd sockets that burst forced an LRU purge,
    and the WS session — whose lru_counter is bumped only by INBOUND
    requests, and the app never sends any on /ws — was always the
    victim. The app then went blind for a full reconnect delay at the
    exact moment the user hit Stop.
    """
    import threading

    s = net_qemu()
    s.wait_server_up()
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)

    ws = s.ws_connect()
    assert ws.recv_text()["type"] == "status"  # hello

    results: list[int] = []
    lock = threading.Lock()

    def fire(path: str, body: dict | None) -> None:
        st, _ = s.post(path, body, timeout=60.0)
        with lock:
            results.append(st)

    threads = [
        threading.Thread(target=fire, args=("/api/speed", {"value": 0.0})),
        threading.Thread(target=fire, args=("/api/incline", {"value": 0.0})),
        threading.Thread(target=fire, args=("/api/program/stop", None)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert results == [200, 200, 200] or sorted(results) == [200, 200, 200], results

    # The SAME socket must still be alive and receiving.
    status, _ = s.post("/api/incline", {"value": 2.5})
    assert status == 200
    seen = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        frame = ws.recv_text(timeout=30)
        if frame["type"] == "status":
            seen = frame
            break
    assert seen is not None, "WS was purged by the app's own request burst"
    ws.close()
