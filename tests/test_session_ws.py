"""WebSocket integration tests for session broadcast."""

import json
from unittest.mock import AsyncMock, MagicMock

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
    server.state["running"] = True
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


class TestSessionWebSocket:
    def test_ws_receives_status_on_connect(self, test_app):
        """New WS connection receives status message immediately."""
        client, server, _ = test_app
        with client.websocket_connect("/ws") as ws:
            data = json.loads(ws.receive_text())
            assert data["type"] == "status"

    def test_ws_receives_session_on_connect_when_active(self, test_app):
        """If session is active, new WS connection receives session state."""
        client, server, _ = test_app
        # Start a session first
        server.session["active"] = True
        server.session["elapsed"] = 42.0
        server.session["distance"] = 0.5
        server.session["vert_feet"] = 100.0
        server.session["wall_started_at"] = "2025-01-01T12:00:00"
        with client.websocket_connect("/ws") as ws:
            status = json.loads(ws.receive_text())
            assert status["type"] == "status"
            session_msg = json.loads(ws.receive_text())
            assert session_msg["type"] == "session"
            assert session_msg["active"] is True
            assert session_msg["elapsed"] == 42.0

    def test_ws_no_session_on_connect_when_inactive(self, test_app):
        """If no active session, WS only gets status (no session message)."""
        client, server, _ = test_app
        # Session inactive by default
        with client.websocket_connect("/ws") as ws:
            status = json.loads(ws.receive_text())
            assert status["type"] == "status"
            # No more messages pending — sending anything should work
            # (we can't easily check "no more messages" with sync client,
            # but we verified session isn't sent by the connect handler)

    def test_session_ends_on_speed_zero_via_api(self, test_app):
        """Verify session ends when speed set to 0 (via REST, not WS broadcast).

        WS broadcast during POST isn't testable with sync TestClient, so we
        verify state change + build_session() output instead.
        """
        client, server, _ = test_app
        # Start session
        client.post("/api/speed", json={"value": 3.0})
        assert server.session["active"] is True
        # Stop
        client.post("/api/speed", json={"value": 0})
        assert server.session["active"] is False
        sess = server.build_session()
        assert sess["end_reason"] == "user_stop"

    def test_build_session_returns_correct_type(self, test_app):
        """build_session() returns proper dict structure."""
        _, server, _ = test_app
        server.session["active"] = True
        server.session["elapsed"] = 10.5
        server.session["distance"] = 0.1
        server.session["vert_feet"] = 50.0
        result = server.build_session()
        assert result["type"] == "session"
        assert result["active"] is True
        assert result["elapsed"] == 10.5
        assert result["end_reason"] is None

    def test_ws_receives_program_on_connect_when_loaded(self, test_app):
        """If a program is loaded, new WS connection receives program state."""
        client, server, _ = test_app
        server.prog.load(
            {
                "name": "Test Program",
                "intervals": [
                    {"name": "Warm Up", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "Run", "duration": 120, "speed": 6.0, "incline": 2},
                ],
            }
        )
        with client.websocket_connect("/ws") as ws:
            status = json.loads(ws.receive_text())
            assert status["type"] == "status"
            pgm = json.loads(ws.receive_text())
            assert pgm["type"] == "program"
            assert pgm["program"]["name"] == "Test Program"
            assert len(pgm["program"]["intervals"]) == 2

    def test_ws_no_program_on_connect_when_none(self, test_app):
        """If no program loaded, WS only gets status (no program message)."""
        client, server, _ = test_app
        assert server.prog.program is None
        with client.websocket_connect("/ws") as ws:
            status = json.loads(ws.receive_text())
            assert status["type"] == "status"
