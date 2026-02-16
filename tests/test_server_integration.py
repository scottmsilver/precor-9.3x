"""Integration tests for server endpoints with mocked treadmill hardware."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


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
    client.on_message = None
    return client


@pytest.fixture
def test_app(mock_client):
    """Create test app with mocked dependencies."""
    import server
    from program_engine import ProgramState

    # Save originals
    orig_client = getattr(server, "client", None)
    orig_prog = getattr(server, "prog", None)
    orig_loop = getattr(server, "loop", None)
    orig_queue = getattr(server, "msg_queue", None)

    # Set up mocks
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

    from starlette.testclient import TestClient

    # We need to bypass the lifespan context manager for testing
    server.app.router.lifespan_context = None
    tc = TestClient(server.app, raise_server_exceptions=True)
    yield tc, server, mock_client

    # Restore
    server.client = orig_client
    server.prog = orig_prog
    server.loop = orig_loop
    server.msg_queue = orig_queue


class TestStatusEndpoint:
    def test_get_status(self, test_app):
        client, server, _ = test_app
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "status"
        assert "proxy" in data
        assert "emulate" in data


class TestSpeedEndpoint:
    def test_set_speed(self, test_app):
        client, server, mock = test_app
        resp = client.post("/api/speed", json={"value": 5.0})
        assert resp.status_code == 200
        assert server.state["emu_speed"] == 50
        mock.set_speed.assert_called_with(5.0)

    def test_set_speed_clamped(self, test_app):
        client, server, mock = test_app
        resp = client.post("/api/speed", json={"value": 99.0})
        assert resp.status_code == 200
        assert server.state["emu_speed"] == 120  # MAX_SPEED_TENTHS


class TestInclineEndpoint:
    def test_set_incline(self, test_app):
        client, server, mock = test_app
        resp = client.post("/api/incline", json={"value": 5})
        assert resp.status_code == 200
        assert server.state["emu_incline"] == 5
        mock.set_incline.assert_called_with(5)


class TestProgramFlow:
    def test_pause_toggles(self, test_app):
        client, server, _ = test_app
        server.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        server.prog.running = True
        server.prog._on_update = AsyncMock()
        resp = client.post("/api/program/pause")
        assert resp.status_code == 200
        assert resp.json()["paused"] is True

    def test_stop_resets_speed(self, test_app):
        client, server, mock = test_app
        server.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        server.prog.running = True
        server.prog._on_change = AsyncMock()
        server.prog._on_update = AsyncMock()
        server.state["emu_speed"] = 30
        resp = client.post("/api/program/stop")
        assert resp.status_code == 200
        assert server.state["emu_speed"] == 0
        mock.set_speed.assert_called_with(0)

    def test_skip_advances(self, test_app):
        client, server, _ = test_app
        server.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.prog.running = True
        server.prog._on_change = AsyncMock()
        server.prog._on_update = AsyncMock()
        resp = client.post("/api/program/skip")
        assert resp.status_code == 200
        assert resp.json()["current_interval"] == 1

    def test_prev_goes_back(self, test_app):
        client, server, _ = test_app
        server.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.prog.running = True
        server.prog.current_interval = 1
        server.prog._on_change = AsyncMock()
        server.prog._on_update = AsyncMock()
        resp = client.post("/api/program/prev")
        assert resp.status_code == 200
        assert resp.json()["current_interval"] == 0

    def test_prev_at_zero_stays(self, test_app):
        client, server, _ = test_app
        server.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.prog.running = True
        server.prog.current_interval = 0
        server.prog._on_change = AsyncMock()
        server.prog._on_update = AsyncMock()
        resp = client.post("/api/program/prev")
        assert resp.status_code == 200
        assert resp.json()["current_interval"] == 0


class TestProgOnChange:
    def test_prog_on_change_calls_client(self, test_app):
        """Test _prog_on_change closure calls mock client."""
        _, server, mock = test_app
        import asyncio

        on_change = server._prog_on_change()
        asyncio.get_event_loop().run_until_complete(on_change(4.5, 3))
        assert server.state["emu_speed"] == 45
        assert server.state["emu_incline"] == 3
        mock.set_speed.assert_called_with(4.5)
        mock.set_incline.assert_called_with(3)


class TestExecFn:
    """Test Gemini function call dispatch."""

    @pytest.mark.asyncio
    async def test_exec_set_speed(self, test_app):
        _, server, mock = test_app
        result = await server._exec_fn("set_speed", {"mph": 4.5})
        assert "4.5" in result
        assert server.state["emu_speed"] == 45
        mock.set_speed.assert_called_with(4.5)

    @pytest.mark.asyncio
    async def test_exec_set_incline(self, test_app):
        _, server, mock = test_app
        result = await server._exec_fn("set_incline", {"incline": 7})
        assert "7" in result
        assert server.state["emu_incline"] == 7
        mock.set_incline.assert_called_with(7)

    @pytest.mark.asyncio
    async def test_exec_stop(self, test_app):
        _, server, mock = test_app
        server.state["emu_speed"] = 50
        result = await server._exec_fn("stop_treadmill", {})
        assert "stopped" in result.lower()
        assert server.state["emu_speed"] == 0
        mock.set_speed.assert_called_with(0)

    @pytest.mark.asyncio
    async def test_exec_pause_no_program(self, test_app):
        _, server, _ = test_app
        result = await server._exec_fn("pause_program", {})
        assert "no program" in result.lower()

    @pytest.mark.asyncio
    async def test_exec_unknown(self, test_app):
        _, server, _ = test_app
        result = await server._exec_fn("nonexistent", {})
        assert "unknown" in result.lower()


class TestGpxParsing:
    """Test GPX file parsing into interval programs."""

    def _make_gpx(self, points):
        """Generate minimal GPX XML from a list of (lat, lon, ele) tuples."""
        pts = "\n".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>' for lat, lon, ele in points)
        return f"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
<trk><trkseg>{pts}</trkseg></trk></gpx>""".encode()

    def test_basic_gpx_parsing(self, test_app):
        _, server, _ = test_app
        # ~500m apart with 50m elevation gain
        points = [
            (47.6062, -122.3321, 0),
            (47.6062, -122.3260, 50),
            (47.6062, -122.3200, 100),
            (47.6062, -122.3140, 50),
        ]
        gpx = self._make_gpx(points)
        program = server._parse_gpx_to_intervals(gpx)
        assert "intervals" in program
        assert len(program["intervals"]) >= 1
        assert "GPX Route" in program["name"]
        for iv in program["intervals"]:
            assert 0.5 <= iv["speed"] <= 12.0
            assert 0 <= iv["incline"] <= 15
            assert iv["duration"] >= 10

    def test_gpx_too_few_points(self, test_app):
        _, server, _ = test_app
        gpx = self._make_gpx([(47.6, -122.3, 0)])
        with pytest.raises(ValueError, match="at least 2 points"):
            server._parse_gpx_to_intervals(gpx)

    def test_gpx_upload_endpoint(self, test_app):
        client, server, _ = test_app
        points = [
            (47.6062, -122.3321, 0),
            (47.6062, -122.3260, 50),
            (47.6062, -122.3200, 30),
        ]
        gpx_bytes = self._make_gpx(points)
        with patch.object(server, "_add_to_history", return_value={}):
            resp = client.post("/api/gpx/upload", files={"file": ("test.gpx", gpx_bytes, "application/gpx+xml")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "GPX Route" in data["program"]["name"]


class TestChatEndpoint:
    """Test /api/chat endpoint with mocked Gemini API."""

    def test_chat_text_response(self, test_app):
        client, server, _ = test_app
        server.chat_history = []
        mock_response = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Hello! Ready to run?"}]}}]}
        with (
            patch("server.call_gemini", new_callable=AsyncMock, return_value=mock_response),
            patch("server._load_history", return_value=[]),
        ):
            resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Hello" in data["text"]

    def test_chat_function_call(self, test_app):
        client, server, mock = test_app
        server.chat_history = []
        # First response: function call, second: text
        fc_response = {
            "candidates": [
                {"content": {"role": "model", "parts": [{"functionCall": {"name": "set_speed", "args": {"mph": 3.0}}}]}}
            ]
        }
        text_response = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Speed set to 3 mph!"}]}}]}
        with (
            patch("server.call_gemini", new_callable=AsyncMock, side_effect=[fc_response, text_response]),
            patch("server._load_history", return_value=[]),
        ):
            resp = client.post("/api/chat", json={"message": "set speed to 3"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["actions"]) == 1
        assert data["actions"][0]["name"] == "set_speed"

    def test_chat_error_recovery(self, test_app):
        client, server, _ = test_app
        server.chat_history = []
        with (
            patch("server.call_gemini", new_callable=AsyncMock, side_effect=Exception("API error")),
            patch("server._load_history", return_value=[]),
        ):
            resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data["text"].lower()
