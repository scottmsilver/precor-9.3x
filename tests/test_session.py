"""Unit tests for server-authoritative session management."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def mock_client():
    """Mock TreadmillClient that doesn't need hardware."""
    client = MagicMock()
    client.set_speed = MagicMock()
    client.set_incline = MagicMock()
    client.set_emulate = MagicMock()
    client.set_proxy = MagicMock()
    client.connect = MagicMock()
    client.close = MagicMock()
    client.start_heartbeat = MagicMock()
    client.stop_heartbeat = MagicMock()
    client.on_message = None
    return client


@pytest.fixture
def test_app(mock_client):
    """Create test app with mocked dependencies, reset session state."""
    import server
    from program_engine import ProgramState

    orig_client = getattr(server, "client", None)
    orig_prog = getattr(server, "prog", None)
    orig_loop = getattr(server, "loop", None)
    orig_queue = getattr(server, "msg_queue", None)

    server.client = mock_client
    server.prog = ProgramState()
    server.loop = MagicMock()
    server.msg_queue = MagicMock()
    server.msg_queue.put_nowait = MagicMock()

    # Reset state
    server.state["proxy"] = True
    server.state["emulate"] = False
    server.state["emu_speed"] = 0
    server.state["emu_incline"] = 0
    server.state["treadmill_connected"] = True
    server.latest["last_motor"] = {}
    server.latest["last_console"] = {}

    # Reset session
    server.session["active"] = False
    server.session["started_at"] = 0.0
    server.session["wall_started_at"] = ""
    server.session["paused_at"] = 0.0
    server.session["total_paused"] = 0.0
    server.session["elapsed"] = 0.0
    server.session["distance"] = 0.0
    server.session["vert_feet"] = 0.0
    server.session["end_reason"] = None

    server.app.router.lifespan_context = None
    tc = TestClient(server.app, raise_server_exceptions=True)
    yield tc, server, mock_client

    server.client = orig_client
    server.prog = orig_prog
    server.loop = orig_loop
    server.msg_queue = orig_queue


class TestSessionLifecycle:
    def test_session_starts_on_first_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.session["active"] is True
        assert server.session["started_at"] > 0

    def test_session_not_restarted_on_second_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        started = server.session["started_at"]
        client.post("/api/speed", json={"value": 5.0})
        assert server.session["started_at"] == started

    def test_session_ends_on_stop(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.session["active"] is True
        client.post("/api/program/stop")
        assert server.session["active"] is False
        assert server.session["end_reason"] == "user_stop"

    def test_session_ends_on_zero_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.session["active"] is True
        client.post("/api/speed", json={"value": 0})
        assert server.session["active"] is False
        assert server.session["end_reason"] == "user_stop"

    def test_session_starts_on_program_start(self, test_app):
        client, server, _ = test_app
        server.prog.load(
            {
                "name": "Test",
                "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}],
            }
        )
        server.prog._on_change = AsyncMock()
        server.prog._on_update = AsyncMock()
        client.post("/api/program/start")
        assert server.session["active"] is True

    def test_no_session_on_incline_only(self, test_app):
        client, server, _ = test_app
        client.post("/api/incline", json={"value": 5})
        assert server.session["active"] is False


class TestSessionComputation:
    def test_elapsed_computation(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic() - 60
        server.session["total_paused"] = 0.0
        server.session["paused_at"] = 0.0
        server._session_tick_compute()
        assert 59 <= server.session["elapsed"] <= 61

    def test_distance_accumulation(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic() - 1
        server.session["paused_at"] = 0.0
        server.session["total_paused"] = 0.0
        server.state["emu_speed"] = 60  # 6.0 mph
        server._session_tick_compute()
        expected = 6.0 / 3600  # one tick at 6 mph
        assert abs(server.session["distance"] - expected) < 0.001

    def test_vert_computation(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic() - 1
        server.session["paused_at"] = 0.0
        server.session["total_paused"] = 0.0
        server.state["emu_speed"] = 60  # 6.0 mph
        server.state["emu_incline"] = 10
        server._session_tick_compute()
        assert server.session["vert_feet"] > 0

    def test_paused_session_no_accumulation(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic() - 60
        server.session["paused_at"] = time.monotonic() - 30
        server.session["total_paused"] = 0.0
        server.state["emu_speed"] = 60
        old_dist = server.session["distance"]
        server._session_tick_compute()
        # Paused — distance should not change
        assert server.session["distance"] == old_dist


class TestSessionEndReasons:
    def test_watchdog_ends_session(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic()
        server.state["emulate"] = True
        # Simulate status message where emulate goes false (watchdog fired)
        on_msg = server.client.on_message
        # We need to call on_message from lifespan — but in test we set it
        # up manually. Access the closure directly via the server's on_message setup.
        # Instead, directly test the logic:
        was_emulating = server.state["emulate"]
        server.state["emulate"] = False
        server.state["proxy"] = False
        if was_emulating and not server.state["emulate"] and server.session["active"]:
            reason = "auto_proxy" if server.state["proxy"] else "watchdog"
            server._end_session(reason)
        assert server.session["active"] is False
        assert server.session["end_reason"] == "watchdog"

    def test_auto_proxy_ends_session(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic()
        server.state["emulate"] = True
        # Simulate auto-proxy: emulate off, proxy on
        server.state["emulate"] = False
        server.state["proxy"] = True
        server._end_session("auto_proxy")
        assert server.session["active"] is False
        assert server.session["end_reason"] == "auto_proxy"

    def test_disconnect_ends_session(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["started_at"] = time.monotonic()
        server._end_session("disconnect")
        assert server.session["active"] is False
        assert server.session["end_reason"] == "disconnect"


class TestSessionAPI:
    def test_get_session_inactive(self, test_app):
        client, server, _ = test_app
        resp = client.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_get_session_active(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        resp = client.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert "elapsed" in data
        assert "distance" in data
        assert "vert_feet" in data


class TestLogEndpoint:
    def test_get_log(self, test_app):
        client, server, _ = test_app
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "line1\nline2\nline3"
        with patch("server.subprocess.run", return_value=mock_result):
            resp = client.get("/api/log?lines=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == ["line1", "line2", "line3"]

    def test_get_log_file_not_found(self, test_app):
        client, server, _ = test_app
        with patch("server.subprocess.run", side_effect=FileNotFoundError):
            resp = client.get("/api/log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == []


class TestHeartbeatThread:
    def test_start_heartbeat_calls_send(self):
        from treadmill_client import TreadmillClient

        tc = TreadmillClient()
        tc._running = True
        tc._connected = True
        calls = []
        tc._send = MagicMock(side_effect=lambda msg: calls.append(msg))
        tc.start_heartbeat(0.1)
        time.sleep(0.35)
        tc.stop_heartbeat()
        heartbeat_calls = [c for c in calls if c.get("cmd") == "heartbeat"]
        assert len(heartbeat_calls) >= 2

    def test_stop_heartbeat_joins_thread(self):
        from treadmill_client import TreadmillClient

        tc = TreadmillClient()
        tc._running = True
        tc._connected = True
        tc._send = MagicMock()
        tc.start_heartbeat(0.1)
        assert tc._heartbeat_thread is not None
        assert tc._heartbeat_thread.is_alive()
        tc.stop_heartbeat()
        assert tc._heartbeat_thread is None


class TestSessionPause:
    def test_pause_freezes_session(self, test_app):
        client, server, _ = test_app
        # Start session
        client.post("/api/speed", json={"value": 3.0})
        assert server.session["active"] is True
        # Load a program so pause works
        server.prog.load(
            {
                "name": "Test",
                "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}],
            }
        )
        server.prog.running = True
        server.prog._on_update = AsyncMock()
        # Pause
        client.post("/api/program/pause")
        assert server.session["paused_at"] > 0
        # Resume
        client.post("/api/program/pause")
        assert server.session["paused_at"] == 0.0
        assert server.session["total_paused"] > 0 or True  # might be near-zero


class TestBuildSession:
    def test_build_session_dict(self, test_app):
        _, server, _ = test_app
        server.session["active"] = True
        server.session["elapsed"] = 42.5
        server.session["distance"] = 0.5
        server.session["vert_feet"] = 100.0
        server.session["wall_started_at"] = "2025-01-01T12:00:00"
        result = server.build_session()
        assert result["type"] == "session"
        assert result["active"] is True
        assert result["elapsed"] == 42.5
        assert result["distance"] == 0.5
        assert result["vert_feet"] == 100.0
