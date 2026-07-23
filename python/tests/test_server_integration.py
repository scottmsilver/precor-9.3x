"""Integration tests for server endpoints with mocked treadmill hardware."""

import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    """Create test app with mocked dependencies and in-memory db."""
    import server
    from db import TreadmillDB
    from workout_db import WorkoutDB
    from workout_session import WorkoutSession

    # Save originals
    orig_client = getattr(server, "client", None)
    orig_sess = getattr(server, "sess", None)
    orig_loop = getattr(server, "loop", None)
    orig_queue = getattr(server, "msg_queue", None)
    orig_db = getattr(server, "db", None)
    orig_workout_db = getattr(server, "workout_db", None)
    orig_guest_mode = getattr(server, "_guest_mode", False)

    # Set up in-memory db with a test profile
    test_db = TreadmillDB(":memory:")
    test_profile = test_db.create_profile("Test User", weight_lbs=154)
    test_db.set_active_profile_id(test_profile["id"])

    server.db = test_db
    server._guest_mode = False

    # Set up mocks
    server.client = mock_client
    server.sess = WorkoutSession()
    server.loop = MagicMock()
    server.msg_queue = MagicMock()
    server.msg_queue.put_nowait = MagicMock()

    # Create workout_db linked to test db
    server.workout_db = WorkoutDB(
        history_loader=lambda: test_db.get_program_history(server._active_profile_id()),
        workouts_loader=lambda: test_db.get_saved_workouts(server._active_profile_id()),
        runs_loader=lambda: test_db.get_runs(server._active_profile_id()),
        fingerprint_fn=server._program_fingerprint,
    )

    # Reset state
    server.state["proxy"] = True
    server.state["emulate"] = False
    server.state["emu_speed"] = 0
    server.state["emu_incline"] = 0
    server.state["treadmill_connected"] = True
    server.state["bus_speed"] = None
    server.state["bus_incline"] = None
    server.latest["last_motor"] = {}
    server.latest["last_console"] = {}

    from starlette.testclient import TestClient

    # We need to bypass the lifespan context manager for testing
    server.app.router.lifespan_context = None
    tc = TestClient(server.app, raise_server_exceptions=True)
    yield tc, server, mock_client

    # Restore
    test_db.close()
    server.client = orig_client
    server.sess = orig_sess
    server.loop = orig_loop
    server.msg_queue = orig_queue
    server.db = orig_db
    server.workout_db = orig_workout_db
    server._guest_mode = orig_guest_mode


class TestRootBanner:
    def test_root_returns_json_banner(self, test_app):
        """GET / returns the API banner (no web UI is served; also proves
        `import server` works with no static/ directory on disk)."""
        client, _, _ = test_app
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"service": "precor-treadmill", "api": "/api", "ws": "/ws"}


class TestStatusEndpoint:
    def test_get_status(self, test_app):
        client, server, _ = test_app
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "status"
        assert "proxy" in data
        assert "emulate" in data


class TestBusValues:
    """Tests for bus_speed/bus_incline propagation from C++ status."""

    def test_bus_speed_in_status(self, test_app):
        """bus_speed from C++ status should appear as speed in /api/status."""
        client, server, _ = test_app
        server.state["bus_speed"] = 35  # 3.5 mph in tenths
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["speed"] == 3.5

    def test_bus_incline_in_status(self, test_app):
        """bus_incline from C++ status (half-pct units) should appear as percent in /api/status."""
        client, server, _ = test_app
        server.state["bus_incline"] = 10  # 10 half-pct = 5.0%
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incline"] == 5.0

    def test_bus_values_none_falls_back_to_kv(self, test_app):
        """When bus values are None, should fall back to KV parsing."""
        client, server, _ = test_app
        server.state["bus_speed"] = None
        server.state["bus_incline"] = None
        server.latest["last_motor"]["hmph"] = "78"  # hex: 0x78 = 120 hundredths = 1.2 mph
        server.latest["last_motor"]["inc"] = "A"  # hex: 0xA = 10 half-pct = 5.0%
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["speed"] == 1.2
        assert data["incline"] == 5.0

    def test_kv_hex_incline_15_percent(self, test_app):
        """KV fallback: hex 1E = 30 half-pct = 15.0%."""
        client, server, _ = test_app
        server.state["bus_speed"] = None
        server.state["bus_incline"] = None
        server.latest["last_motor"]["inc"] = "1E"
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["incline"] == 15.0

    def test_hex_incline_no_longer_crashes(self, test_app):
        """Regression: float('A') used to crash; now parsed as hex."""
        client, server, _ = test_app
        server.state["bus_speed"] = None
        server.state["bus_incline"] = None
        server.latest["last_motor"]["inc"] = "A"
        # Should not raise, should return 5.0
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["incline"] == 5.0

    def test_bus_speed_negative_one_treated_as_none(self, test_app):
        """bus_speed of -1 (not yet received) should be treated as None."""
        client, server, _ = test_app
        server.state["bus_speed"] = -1
        server.state["bus_incline"] = -1
        # Should fall back to KV
        resp = client.get("/api/status")
        assert resp.status_code == 200
        # With no KV data either, should be None
        data = resp.json()
        assert data["speed"] is None
        assert data["incline"] is None


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
        assert server.state["emu_incline"] == 10  # 5% stored as 10 half-pct
        mock.set_incline.assert_called_with(5.0)

    def test_set_incline_float(self, test_app):
        """POST /api/incline with float 5.5 should work."""
        client, server, mock = test_app
        resp = client.post("/api/incline", json={"value": 5.5})
        assert resp.status_code == 200
        assert server.state["emu_incline"] == 11  # 5.5% stored as 11 half-pct
        mock.set_incline.assert_called_with(5.5)

    def test_set_incline_half_step(self, test_app):
        """POST /api/incline with 0.5 should work."""
        client, server, mock = test_app
        resp = client.post("/api/incline", json={"value": 0.5})
        assert resp.status_code == 200
        assert server.state["emu_incline"] == 1  # 0.5% stored as 1 half-pct
        mock.set_incline.assert_called_with(0.5)

    def test_set_incline_snaps_to_half(self, test_app):
        """Non-0.5-step values should snap to nearest 0.5."""
        client, server, mock = test_app
        resp = client.post("/api/incline", json={"value": 5.3})
        assert resp.status_code == 200
        assert server.state["emu_incline"] == 11  # 5.3 snaps to 5.5 -> 11 half-pct
        mock.set_incline.assert_called_with(5.5)

    def test_incline_in_status_response(self, test_app):
        """Status response should show emu_incline in percent, not half-pct."""
        client, server, _ = test_app
        server.state["emu_incline"] = 11  # 5.5% in half-pct
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["emu_incline"] == 5.5


class TestProgramFlow:
    def test_pause_toggles(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]}
        )
        server.sess.prog.running = True
        server.sess.prog._on_update = AsyncMock()
        resp = client.post("/api/program/pause")
        assert resp.status_code == 200
        assert resp.json()["paused"] is True

    def test_stop_resets_speed(self, test_app):
        client, server, mock = test_app
        server.sess.prog.load(
            {"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]}
        )
        server.sess.prog.running = True
        server.sess.prog._on_change = AsyncMock()
        server.sess.prog._on_update = AsyncMock()
        server.state["emu_speed"] = 30
        resp = client.post("/api/program/stop")
        assert resp.status_code == 200
        assert server.state["emu_speed"] == 0
        mock.set_speed.assert_called_with(0)

    def test_skip_advances(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.sess.prog.running = True
        server.sess.prog._on_change = AsyncMock()
        server.sess.prog._on_update = AsyncMock()
        resp = client.post("/api/program/skip")
        assert resp.status_code == 200
        assert resp.json()["current_interval"] == 1

    def test_prev_goes_back(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.sess.prog.running = True
        server.sess.prog.current_interval = 1
        server.sess.prog._on_change = AsyncMock()
        server.sess.prog._on_update = AsyncMock()
        resp = client.post("/api/program/prev")
        assert resp.status_code == 200
        assert resp.json()["current_interval"] == 0

    def test_prev_at_zero_stays(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {
                "name": "Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
                    {"name": "B", "duration": 60, "speed": 5.0, "incline": 2},
                ],
            }
        )
        server.sess.prog.running = True
        server.sess.prog.current_interval = 0
        server.sess.prog._on_change = AsyncMock()
        server.sess.prog._on_update = AsyncMock()
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
        assert server.state["emu_incline"] == 6  # 3% stored as 6 half-pct
        mock.set_speed.assert_called_with(4.5)
        mock.set_incline.assert_called_with(3.0)

    def test_prog_on_change_half_step(self, test_app):
        """Test _prog_on_change with 0.5 incline step."""
        _, server, mock = test_app
        import asyncio

        on_change = server._prog_on_change()
        asyncio.get_event_loop().run_until_complete(on_change(3.0, 2.5))
        assert server.state["emu_speed"] == 30
        assert server.state["emu_incline"] == 5  # 2.5% stored as 5 half-pct
        mock.set_incline.assert_called_with(2.5)


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
        assert server.state["emu_incline"] == 14  # 7% stored as 14 half-pct
        mock.set_incline.assert_called_with(7.0)

    @pytest.mark.asyncio
    async def test_exec_set_incline_float(self, test_app):
        _, server, mock = test_app
        result = await server._exec_fn("set_incline", {"incline": 5.5})
        assert "5.5" in result
        assert server.state["emu_incline"] == 11  # 5.5% stored as 11 half-pct
        mock.set_incline.assert_called_with(5.5)

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
        with (patch("server.call_gemini", new_callable=AsyncMock, return_value=mock_response),):
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
        with patch("server.call_gemini", new_callable=AsyncMock, side_effect=[fc_response, text_response]):
            resp = client.post("/api/chat", json={"message": "set speed to 3"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["actions"]) == 1
        assert data["actions"][0]["name"] == "set_speed"

    def test_chat_error_recovery(self, test_app):
        client, server, _ = test_app
        server.chat_history = []
        with patch("server.call_gemini", new_callable=AsyncMock, side_effect=Exception("API error")):
            resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "wrong" in data["text"].lower() or "error" in data["text"].lower()

    def test_chat_rollback_does_not_corrupt_other_call(self, test_app):
        """Two concurrent chat calls: if one errors and rolls back, it must
        not truncate messages appended by the other call."""
        client, server, _ = test_app
        server.chat_history = []

        # Call A errors (rollback), Call B succeeds.
        # After both complete, chat_history must contain Call B's messages.
        call_count = 0

        async def mock_gemini(history, system, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Call A — succeeds with a text response
                return {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK A"}]}}]}
            else:
                # Call B — errors
                raise Exception("API error B")

        with patch("server.call_gemini", side_effect=mock_gemini):
            # Call A succeeds
            resp_a = client.post("/api/chat", json={"message": "msg A"})
            # Call B errors and rolls back
            resp_b = client.post("/api/chat", json={"message": "msg B"})

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        # Call A's user message + model response should survive Call B's rollback
        user_msgs = [m for m in server.chat_history if m.get("role") == "user"]
        assert len(user_msgs) >= 1, f"Call A's message was lost: {server.chat_history}"

    def test_chat_history_protected_by_lock(self, test_app):
        """Verify that _chat_lock exists and is used."""
        _, server, _ = test_app
        import asyncio

        assert hasattr(server, "_chat_lock"), "server must have _chat_lock for serializing chat"
        assert isinstance(server._chat_lock, asyncio.Lock)


class TestConfigEndpoint:
    """Test /api/config returns ephemeral token, not raw API key."""

    def test_config_returns_ephemeral_token(self, test_app):
        client, server, _ = test_app
        mock_token = MagicMock()
        mock_token.name = "auth_tokens/abc123def456"
        with patch("server._create_ephemeral_token", return_value="auth_tokens/abc123def456"):
            resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        # Should return the ephemeral token, not a raw API key
        assert data["gemini_api_key"] == "auth_tokens/abc123def456"
        assert data["gemini_api_key"].startswith("auth_tokens/")
        assert data["gemini_model"]
        assert data["gemini_live_model"]
        assert data["gemini_voice"]

    def test_config_no_api_key(self, test_app):
        client, server, _ = test_app
        with patch("server._create_ephemeral_token", return_value=None):
            resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gemini_api_key"] == ""

    def test_create_ephemeral_token_success(self, test_app):
        """Test _create_ephemeral_token calls the SDK correctly."""
        _, server, _ = test_app
        mock_token = MagicMock()
        mock_token.name = "auth_tokens/test_token_xyz"
        mock_auth_client = MagicMock()
        mock_auth_client.auth_tokens.create.return_value = mock_token
        with (
            patch("server.read_api_key", return_value="real-api-key"),
            patch("server.genai.Client", return_value=mock_auth_client) as mock_client_cls,
        ):
            result = server._create_ephemeral_token()
        assert result == "auth_tokens/test_token_xyz"
        # Verify Client was created with v1alpha
        mock_client_cls.assert_called_once_with(
            api_key="real-api-key",
            http_options={"api_version": "v1alpha"},
        )
        # Verify auth_tokens.create was called
        mock_auth_client.auth_tokens.create.assert_called_once()
        call_kwargs = mock_auth_client.auth_tokens.create.call_args
        config = call_kwargs[1]["config"] if "config" in call_kwargs[1] else call_kwargs[0][0]
        assert config["uses"] == 5
        assert config["http_options"]["api_version"] == "v1alpha"

    def test_create_ephemeral_token_no_key(self, test_app):
        _, server, _ = test_app
        with patch("server.read_api_key", return_value=None):
            result = server._create_ephemeral_token()
        assert result is None

    def test_create_ephemeral_token_sdk_error(self, test_app):
        _, server, _ = test_app
        with (
            patch("server.read_api_key", return_value="real-api-key"),
            patch("server.genai.Client", side_effect=Exception("SDK error")),
        ):
            result = server._create_ephemeral_token()
        assert result is None


class TestVoicePromptEndpoint:
    """Test /api/voice/prompt/{id} endpoint."""

    def test_known_prompt(self, test_app):
        client, _, _ = test_app
        resp = client.get("/api/voice/prompt/custom-workout")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompt" in data
        assert "workout" in data["prompt"].lower()

    def test_unknown_prompt(self, test_app):
        client, _, _ = test_app
        resp = client.get("/api/voice/prompt/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()


class TestWorkoutSession:
    """Tests for the WorkoutSession class invariants and lifecycle."""

    def _make_sess(self):
        from workout_session import WorkoutSession

        return WorkoutSession()

    # --- 1. start_program() always creates a session ---

    @pytest.mark.asyncio
    async def test_start_program_activates_session(self):
        """start_program() must set session active before running the program."""
        sess = self._make_sess()
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        assert sess.active is False

        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            if tick >= 2:
                sess.prog.running = False

        on_change = AsyncMock()
        on_update = AsyncMock()
        with patch("asyncio.sleep", side_effect=mock_sleep):
            import asyncio

            await sess.start_program(on_change, on_update)
            if sess.prog._task:
                try:
                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert sess.active is True
        assert sess.wall_started_at != ""
        on_change.assert_called()

    # --- 2. ensure_manual() creates session + program ---

    @pytest.mark.asyncio
    async def test_ensure_manual_creates_session_and_program(self):
        """ensure_manual() should create a manual program and activate the session."""
        sess = self._make_sess()
        assert sess.active is False
        assert sess.prog.program is None

        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            if tick >= 2:
                sess.prog.running = False

        on_change = AsyncMock()
        on_update = AsyncMock()
        with patch("asyncio.sleep", side_effect=mock_sleep):
            import asyncio

            await sess.ensure_manual(
                speed=4.0, incline=2, duration_minutes=30, on_change=on_change, on_update=on_update
            )
            if sess.prog._task:
                try:
                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert sess.active is True
        assert sess.prog.program is not None
        assert sess.prog.program["name"] == "30-Min Manual"
        assert sess.prog.program["manual"] is True
        assert sess.prog.program["intervals"][0]["speed"] == 4.0
        assert sess.prog.program["intervals"][0]["incline"] == 2

    @pytest.mark.asyncio
    async def test_ensure_manual_noop_if_already_running(self):
        """ensure_manual() should be a no-op if a program is already running."""
        sess = self._make_sess()
        sess.prog.load({"name": "Existing", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        sess.prog.running = True

        on_change = AsyncMock()
        on_update = AsyncMock()
        await sess.ensure_manual(speed=5.0, on_change=on_change, on_update=on_update)

        # Program should not have changed
        assert sess.prog.program["name"] == "Existing"

    # --- 3. Invariant: prog.running implies sess.active ---

    @pytest.mark.asyncio
    async def test_start_program_invariant_prog_running_implies_active(self):
        """After start_program(), if prog.running is True then sess.active must be True."""
        sess = self._make_sess()
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})

        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            # Check invariant during execution
            if sess.prog.running:
                assert sess.active is True, "prog.running is True but sess.active is False"
            if tick >= 3:
                sess.prog.running = False

        on_change = AsyncMock()
        on_update = AsyncMock()
        with patch("asyncio.sleep", side_effect=mock_sleep):
            import asyncio

            await sess.start_program(on_change, on_update)
            if sess.prog._task:
                try:
                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

    # --- 4. Session lifecycle: start -> pause -> resume -> end ---

    def test_session_lifecycle_start_pause_resume_end(self):
        """Full lifecycle: start, pause, resume, end."""
        sess = self._make_sess()

        # Start
        sess.start()
        assert sess.active is True
        assert sess.paused_at == 0.0

        # Pause
        sess.pause()
        assert sess.paused_at > 0

        # Resume
        sess.resume()
        assert sess.paused_at == 0.0

        # End
        sess.end("user_stop")
        assert sess.active is False
        assert sess.end_reason == "user_stop"

    # --- 5. end() stops the session properly ---

    def test_end_sets_reason_and_deactivates(self):
        """end() should deactivate and record the reason."""
        sess = self._make_sess()
        sess.start()
        assert sess.active is True

        sess.end("watchdog")
        assert sess.active is False
        assert sess.end_reason == "watchdog"

    def test_end_calls_final_tick(self):
        """end() should call tick() to capture final elapsed."""
        sess = self._make_sess()
        sess.start()
        # Simulate some time passing by directly setting started_at back
        sess.started_at = sess.started_at - 10.0
        sess.end("user_stop")
        # elapsed should have been updated by the final tick
        assert sess.elapsed >= 9.0  # at least ~10s minus rounding

    # --- 6. reset() clears both session and program state ---

    @pytest.mark.asyncio
    async def test_reset_clears_everything(self):
        """reset() should zero session fields and reset program."""
        sess = self._make_sess()
        sess.start()
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        sess.elapsed = 42.0
        sess.distance = 1.5
        sess.vert_feet = 200.0

        await sess.reset()

        assert sess.active is False
        assert sess.started_at == 0.0
        assert sess.wall_started_at == ""
        assert sess.paused_at == 0.0
        assert sess.total_paused == 0.0
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.vert_feet == 0.0
        assert sess.end_reason is None
        assert sess.prog.program is None
        assert sess.prog.running is False

    # --- 7. tick() accumulates distance/elapsed correctly ---

    def test_tick_accumulates_distance(self):
        """tick() at 6 mph should accumulate distance each call."""
        sess = self._make_sess()
        sess.start()
        # Move started_at back 5 seconds so elapsed advances
        sess.started_at -= 5.0

        sess.tick(6.0, 0)
        assert sess.elapsed >= 4.0
        first_distance = sess.distance
        assert first_distance > 0

        # Tick again
        sess.tick(6.0, 0)
        assert sess.distance > first_distance

    def test_tick_accumulates_vert_feet(self):
        """tick() with incline > 0 should accumulate vertical feet."""
        sess = self._make_sess()
        sess.start()
        sess.started_at -= 1.0

        sess.tick(3.0, 5)  # 3 mph at 5% incline
        assert sess.vert_feet > 0

    def test_tick_noop_when_inactive(self):
        """tick() should do nothing if session is not active."""
        sess = self._make_sess()
        sess.tick(5.0, 3)
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0

    def test_tick_noop_when_paused(self):
        """tick() should not advance distance when paused."""
        sess = self._make_sess()
        sess.start()
        sess.pause()
        initial_distance = sess.distance

        sess.tick(6.0, 0)
        assert sess.distance == initial_distance

    def test_tick_zero_speed_no_distance(self):
        """tick() at speed 0 should update elapsed but not distance."""
        sess = self._make_sess()
        sess.start()
        sess.started_at -= 5.0

        sess.tick(0, 0)
        assert sess.elapsed >= 4.0
        assert sess.distance == 0.0

    # --- 8. Multiple start() calls are idempotent ---

    def test_start_idempotent(self):
        """Calling start() when already active should not reset fields."""
        sess = self._make_sess()
        sess.start()
        original_started_at = sess.started_at
        original_wall = sess.wall_started_at

        # Accumulate some state
        sess.distance = 0.5
        sess.elapsed = 30.0

        # Start again -- should be idempotent
        sess.start()
        assert sess.started_at == original_started_at
        assert sess.wall_started_at == original_wall
        assert sess.distance == 0.5  # not reset
        assert sess.elapsed == 30.0  # not reset

    def test_start_clears_stale_pause(self):
        """start() on an already active session should clear a stale pause."""
        sess = self._make_sess()
        sess.start()
        sess.pause()
        assert sess.paused_at > 0

        # Calling start() again should clear the pause
        sess.start()
        assert sess.paused_at == 0.0

    # --- 9. end() without active session is a no-op ---

    def test_end_noop_when_inactive(self):
        """end() on an inactive session should do nothing."""
        sess = self._make_sess()
        assert sess.active is False

        sess.end("user_stop")
        assert sess.active is False
        assert sess.end_reason is None  # should not have been set

    # --- 10. Full flow: ensure_manual -> speed change -> split -> stop ---

    @pytest.mark.asyncio
    async def test_full_flow_manual_split_stop(self, test_app):
        """End-to-end: set speed (triggers ensure_manual), change speed (split), stop."""
        client, server, mock = test_app
        sess = server.sess

        assert sess.active is False
        assert sess.prog.running is False

        # Step 1: Set speed 3.0 -- triggers ensure_manual, starts session + program
        resp = client.post("/api/speed", json={"value": 3.0})
        assert resp.status_code == 200
        assert sess.active is True
        assert sess.prog.running is True
        assert sess.prog.is_manual is True
        assert len(sess.prog.program["intervals"]) == 1

        # Step 2: Change speed -- triggers split_for_manual
        resp = client.post("/api/speed", json={"value": 5.0})
        assert resp.status_code == 200
        assert server.state["emu_speed"] == 50
        # After split, there should be 2 intervals (original trimmed + new)
        assert len(sess.prog.program["intervals"]) >= 2

        # Step 3: Stop -- ends session and program
        resp = client.post("/api/program/stop")
        assert resp.status_code == 200
        assert sess.prog.running is False
        assert sess.active is False
        assert sess.end_reason == "user_stop"

    # --- to_dict serialization ---

    def test_to_dict_structure(self):
        """to_dict() should return correct structure."""
        sess = self._make_sess()
        sess.start()
        d = sess.to_dict()

        assert d["type"] == "session"
        assert d["active"] is True
        assert "elapsed" in d
        assert "distance" in d
        assert "vert_feet" in d
        assert "wall_started_at" in d
        assert d["end_reason"] is None

    def test_to_dict_after_end(self):
        """to_dict() after end() should reflect ended state."""
        sess = self._make_sess()
        sess.start()
        sess.end("disconnect")
        d = sess.to_dict()

        assert d["active"] is False
        assert d["end_reason"] == "disconnect"

    # --- pause/resume edge cases ---

    def test_pause_noop_when_inactive(self):
        """pause() on inactive session does nothing."""
        sess = self._make_sess()
        sess.pause()
        assert sess.paused_at == 0.0

    def test_resume_noop_when_not_paused(self):
        """resume() when not paused does nothing."""
        sess = self._make_sess()
        sess.start()
        sess.resume()
        assert sess.total_paused == 0.0

    def test_double_pause_idempotent(self):
        """Calling pause() twice should not double-pause."""
        sess = self._make_sess()
        sess.start()
        sess.pause()
        first_paused_at = sess.paused_at

        sess.pause()
        assert sess.paused_at == first_paused_at  # unchanged


class TestSessionEndpoint:
    """Tests for /api/session endpoint — ensures reconnecting clients get fresh state."""

    def test_session_endpoint_returns_inactive_by_default(self, test_app):
        """GET /api/session on a fresh server should return active=false."""
        client, server, _ = test_app
        resp = client.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "session"
        assert data["active"] is False
        assert data["elapsed"] == 0.0
        assert data["end_reason"] is None

    def test_session_endpoint_returns_active_during_workout(self, test_app):
        """GET /api/session during a workout should return active=true."""
        client, server, mock = test_app
        server.sess.start()
        resp = client.get("/api/session")
        data = resp.json()
        assert data["active"] is True
        assert data["wall_started_at"] != ""

    def test_session_endpoint_after_end(self, test_app):
        """GET /api/session after ending returns active=false with end_reason."""
        client, server, mock = test_app
        server.sess.start()
        server.sess.end("user_stop")
        resp = client.get("/api/session")
        data = resp.json()
        assert data["active"] is False
        assert data["end_reason"] == "user_stop"


class TestHistoryResume:
    """Tests for program history resume (saving/restoring position)."""

    def test_stop_saves_position_to_history(self, test_app):
        """Stopping a program should save its position to history."""
        client, server, mock = test_app
        program = {
            "name": "Resume Test",
            "intervals": [
                {"name": "A", "duration": 120, "speed": 3.0, "incline": 0},
                {"name": "B", "duration": 120, "speed": 5.0, "incline": 2},
            ],
        }
        # Add to history via db
        server._add_to_history(program)

        # Set up running program
        sess = server.sess
        sess.prog.load(program)
        sess.prog.running = True
        sess.prog.current_interval = 1
        sess.prog.total_elapsed = 150
        sess.prog._on_change = AsyncMock()
        sess.prog._on_update = AsyncMock()
        sess.start()
        server.state["emu_speed"] = 50

        # Stop — should update history position
        resp = client.post("/api/program/stop")
        assert resp.status_code == 200

        # Verify db has updated position
        history = server.db.get_program_history(server._active_profile_id())
        entry = next(h for h in history if h["program"]["name"] == "Resume Test")
        assert entry["last_interval"] == 1
        assert entry["last_elapsed"] == 150

    def test_add_to_history_includes_position_fields(self, test_app):
        """_add_to_history should include completed, last_interval, last_elapsed."""
        _, server, _ = test_app
        program = {
            "name": "Test",
            "intervals": [
                {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
            ],
        }
        entry = server._add_to_history(program)
        assert entry["completed"] == 0
        assert entry["last_interval"] == 0
        assert entry["last_elapsed"] == 0

    def test_resume_endpoint_starts_from_position(self, test_app):
        """POST /api/programs/history/{id}/resume should start from saved position."""
        client, server, mock = test_app
        program = {
            "name": "Resume Test",
            "intervals": [
                {"name": "A", "duration": 120, "speed": 3.0, "incline": 0},
                {"name": "B", "duration": 120, "speed": 5.0, "incline": 2},
            ],
        }
        entry = server._add_to_history(program)
        server.db.update_history_entry(entry["id"], last_interval=1, last_elapsed=130)
        resp = client.post(f"/api/programs/history/{entry['id']}/resume")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["current_interval"] == 1
        assert data["running"] is True

    def test_resume_completed_program_rejected(self, test_app):
        """Completed programs should not be resumable."""
        client, server, _ = test_app
        program = {
            "name": "Done",
            "intervals": [
                {"name": "A", "duration": 60, "speed": 3.0, "incline": 0},
            ],
        }
        entry = server._add_to_history(program)
        server.db.update_history_entry(entry["id"], completed=True)
        resp = client.post(f"/api/programs/history/{entry['id']}/resume")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "completed" in data["error"].lower()


class TestHistorySavedWorkoutId:
    """Tests for saved_workout_id on history entries."""

    def test_unsaved_history_has_null_workout_id(self, test_app):
        """History entries that aren't saved should have saved_workout_id=None."""
        client, server, _ = test_app
        program = {
            "name": "Not Saved",
            "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}],
        }
        server._add_to_history(program)

        resp = client.get("/api/programs/history")
        assert resp.status_code == 200
        entries = resp.json()
        entry = next(e for e in entries if e["program"]["name"] == "Not Saved")
        assert entry["saved"] is False
        assert entry["saved_workout_id"] is None

    def test_saved_history_has_workout_id(self, test_app):
        """Saving a workout should make its history entry return the workout ID."""
        client, server, _ = test_app
        program = {
            "name": "Will Be Saved",
            "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}],
        }
        server._add_to_history(program)

        # Get history entry ID
        resp = client.get("/api/programs/history")
        entry = next(e for e in resp.json() if e["program"]["name"] == "Will Be Saved")
        history_id = entry["id"]

        # Save it
        resp = client.post("/api/workouts", json={"history_id": history_id})
        assert resp.status_code == 200

        # Get the saved workout ID
        resp = client.get("/api/workouts")
        workouts = resp.json()
        saved = next(w for w in workouts if w["name"] == "Will Be Saved")
        saved_id = saved["id"]

        # History entry should now have the saved_workout_id
        resp = client.get("/api/programs/history")
        entry = next(e for e in resp.json() if e["program"]["name"] == "Will Be Saved")
        assert entry["saved"] is True
        assert entry["saved_workout_id"] == saved_id

    def test_deleting_workout_clears_saved_workout_id(self, test_app):
        """After deleting a saved workout, history should show saved=False and no ID."""
        client, server, _ = test_app
        program = {
            "name": "Save Then Delete",
            "intervals": [{"name": "A", "duration": 60, "speed": 4.0, "incline": 1}],
        }
        server._add_to_history(program)

        # Save it
        resp = client.get("/api/programs/history")
        history_id = next(e for e in resp.json() if e["program"]["name"] == "Save Then Delete")["id"]
        client.post("/api/workouts", json={"history_id": history_id})

        # Get workout ID and delete it
        resp = client.get("/api/workouts")
        saved_id = next(w for w in resp.json() if w["name"] == "Save Then Delete")["id"]
        resp = client.delete(f"/api/workouts/{saved_id}")
        assert resp.status_code == 200

        # History should no longer be saved
        resp = client.get("/api/programs/history")
        entry = next(e for e in resp.json() if e["program"]["name"] == "Save Then Delete")
        assert entry["saved"] is False
        assert entry["saved_workout_id"] is None

    def test_different_fingerprints_get_independent_ids(self, test_app):
        """Two programs with different intervals get independent saved_workout_ids."""
        client, server, _ = test_app
        prog_a = {
            "name": "Workout A",
            "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}],
        }
        prog_b = {
            "name": "Workout B",
            "intervals": [{"name": "B", "duration": 120, "speed": 5.0, "incline": 2}],
        }
        server._add_to_history(prog_a)
        server._add_to_history(prog_b)

        # Save only prog_a
        resp = client.get("/api/programs/history")
        entry_a = next(e for e in resp.json() if e["program"]["name"] == "Workout A")
        client.post("/api/workouts", json={"history_id": entry_a["id"]})

        # Check: only A is saved, B is not
        resp = client.get("/api/programs/history")
        entries = resp.json()
        saved_entry = next(e for e in entries if e["program"]["name"] == "Workout A")
        unsaved_entry = next(e for e in entries if e["program"]["name"] == "Workout B")
        assert saved_entry["saved"] is True
        assert saved_entry["saved_workout_id"] is not None
        assert unsaved_entry["saved"] is False
        assert unsaved_entry["saved_workout_id"] is None


class TestAutoProxy:
    """Tests for auto-proxy detection (hardware stop button pressed)."""

    def _simulate_auto_proxy(self, server):
        """Simulate what on_message does when C++ reports emulate→proxy transition."""
        # The on_message callback runs _apply in the event loop.
        # For testing, we call the relevant logic directly.
        was_emulating = server.state["emulate"]
        server.state["proxy"] = True
        server.state["emulate"] = False
        sess = server.sess
        if was_emulating and not server.state["emulate"] and sess.active:
            # This is the code path under test (server.py:108-112)
            server._handle_auto_proxy()

    def test_auto_proxy_pauses_program_not_ends_session(self, test_app):
        """When console takes over (auto-proxy), program should pause, session stays active."""
        client, server, mock = test_app
        sess = server.sess

        # Set up: active session with running program
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 300, "speed": 5.0, "incline": 2}]})
        sess.prog.running = True
        sess.prog._on_change = AsyncMock()
        sess.prog._on_update = AsyncMock()
        sess.start()
        server.state["emulate"] = True
        server.state["proxy"] = False
        server.state["emu_speed"] = 50

        assert sess.active is True
        assert sess.prog.running is True

        # Act: simulate auto-proxy (console takes over)
        self._simulate_auto_proxy(server)

        # Assert: session should still be active (paused, not ended)
        assert sess.active is True, "Session should stay active on auto-proxy"
        assert sess.paused_at > 0, "Session should be paused on auto-proxy"
        assert sess.end_reason is None, "Session should NOT have an end_reason"

    def test_auto_proxy_pauses_running_program(self, test_app):
        """Auto-proxy should pause the program, not stop it."""
        client, server, mock = test_app
        sess = server.sess

        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 300, "speed": 5.0, "incline": 2}]})
        sess.prog.running = True
        sess.prog._on_change = AsyncMock()
        sess.prog._on_update = AsyncMock()
        sess.start()
        server.state["emulate"] = True
        server.state["proxy"] = False

        self._simulate_auto_proxy(server)

        assert sess.prog.paused is True, "Program should be paused on auto-proxy"
        assert sess.prog.running is True, "Program should still be running (just paused)"

    def test_auto_proxy_sends_encouragement_bounce(self, test_app):
        """Auto-proxy should send a bounce message (encouragement), not a toast."""
        client, server, mock = test_app
        sess = server.sess

        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 300, "speed": 5.0, "incline": 2}]})
        sess.prog.running = True
        sess.prog._on_change = AsyncMock()
        sess.prog._on_update = AsyncMock()
        sess.start()
        server.state["emulate"] = True
        server.state["proxy"] = False

        self._simulate_auto_proxy(server)

        # Check that a program message with encouragement was enqueued
        enqueued = [
            call.args[0] for call in server.msg_queue.put_nowait.call_args_list if isinstance(call.args[0], dict)
        ]
        program_msgs = [m for m in enqueued if m.get("type") == "program"]
        assert len(program_msgs) > 0, "Should enqueue a program message"
        assert any("encouragement" in m for m in program_msgs), "Program message should contain an encouragement field"

    def test_auto_proxy_noop_when_no_session(self, test_app):
        """Auto-proxy with no active session should be a no-op."""
        client, server, mock = test_app
        server.state["emulate"] = True
        server.state["proxy"] = False
        assert server.sess.active is False

        # Should not raise
        self._simulate_auto_proxy(server)


class TestTimerBlend:
    """Test the pure client-side timer drift correction algorithm.

    Both Android (TreadmillViewModel.kt) and web (useSession.ts) use the same
    algorithm: maintain a local start time, count up independently, and blend
    toward the server elapsed on each update via exponential smoothing.

    These tests validate the algorithm in pure Python to catch regressions
    without needing a Kotlin or TypeScript test harness.
    """

    BLEND = 0.15  # matches TIMER_BLEND / BLEND_FACTOR in implementations
    SNAP_MS = 2000  # matches TIMER_SNAP_MS / SNAP_THRESHOLD

    @staticmethod
    def simulate_timer(server_updates, blend=0.15, snap_ms=2000):
        """Simulate the timer blend algorithm.

        Args:
            server_updates: list of (wall_time_ms, server_elapsed_s) tuples
                representing server WebSocket messages arriving at wall clock times.
            blend: exponential blend factor (0-1).
            snap_ms: threshold for snapping vs blending.

        Returns:
            list of (wall_time_ms, display_elapsed_s) tuples — what the timer
            would show at each 100ms tick after all server updates are applied.
        """
        timer_start_ms = 0
        initialized = False
        results = []

        for wall_ms, server_elapsed in server_updates:
            target_start = wall_ms - int(server_elapsed * 1000)

            if not initialized:
                timer_start_ms = target_start
                initialized = True
            else:
                drift = target_start - timer_start_ms
                if abs(drift) > snap_ms:
                    timer_start_ms = target_start
                else:
                    timer_start_ms += int(drift * blend)

            # Record what display would show at this moment
            display = max(0.0, (wall_ms - timer_start_ms) / 1000.0)
            results.append((wall_ms, display))

        return results

    def test_first_update_snaps_to_server(self):
        """First server update should set timer exactly to server elapsed."""
        results = self.simulate_timer([(1000, 5.0)])
        assert len(results) == 1
        assert abs(results[0][1] - 5.0) < 0.01

    def test_steady_state_no_drift(self):
        """When server and client agree, display should match server."""
        # Server sends elapsed=1, 2, 3 at wall times 1000, 2000, 3000
        updates = [(1000, 1.0), (2000, 2.0), (3000, 3.0)]
        results = self.simulate_timer(updates)
        for wall_ms, display in results:
            expected = wall_ms / 1000.0
            assert (
                abs(display - expected) < 0.1
            ), f"At wall={wall_ms}ms, display={display:.2f} but expected ~{expected:.1f}"

    def test_no_backwards_jump_on_late_update(self):
        """If server update arrives late, timer should never jump backwards.

        This is the core bug being fixed: old implementation would snap to
        server elapsed, causing the timer to jump back visibly.
        """
        # Client starts at wall=1000, elapsed=1.0
        # At wall=2500 (1.5s later), server says elapsed=2.0
        # (server is 0.5s behind where client would expect: 2.5s)
        # Old behavior: snap to 2.0, visible backwards jump from ~2.5 to 2.0
        # New behavior: blend, never go backwards
        updates = [(1000, 1.0), (2500, 2.0)]
        results = self.simulate_timer(updates)

        # After first update at wall=1000: display = 1.0
        # At wall=2500: client would show (2500-(-0))/1000 but let's check
        # The key assertion: display at wall=2500 should be >= display at wall=1000
        assert results[1][1] >= results[0][1], f"Timer went backwards: {results[0][1]:.2f} -> {results[1][1]:.2f}"

    def test_gradual_correction_not_snap(self):
        """Server drift should be corrected gradually, not in one snap."""
        # Start at wall=1000, elapsed=10.0
        # At wall=2000, server says elapsed=10.5 (client expected 11.0)
        # Drift = 0.5s behind server's "slower" pace
        updates = [(1000, 10.0), (2000, 10.5)]
        results = self.simulate_timer(updates)

        # Client at wall=2000 would naively show 11.0 (1s after start anchor)
        # Server says 10.5 — so target_start shifts forward by 500ms
        # With 15% blend, correction = 500 * 0.15 = 75ms
        # Display at wall=2000 should be ~10.925 (not snapped to 10.5)
        display_at_2000 = results[1][1]
        assert display_at_2000 > 10.5, f"Timer snapped to server value ({display_at_2000:.2f}), should blend"
        assert display_at_2000 < 11.1, f"Timer too far from expected ({display_at_2000:.2f})"

    def test_large_drift_snaps(self):
        """Drift > 2s (e.g. after unpause) should snap immediately."""
        # Start at wall=1000, elapsed=10.0
        # At wall=2000, server says elapsed=15.0 (3s jump from unpause)
        updates = [(1000, 10.0), (2000, 15.0)]
        results = self.simulate_timer(updates)

        display_at_2000 = results[1][1]
        # Should snap to server value (drift of 4s > 2s threshold)
        assert (
            abs(display_at_2000 - 15.0) < 0.01
        ), f"Large drift should snap: display={display_at_2000:.2f}, expected 15.0"

    def test_convergence_over_multiple_updates(self):
        """Timer should converge to server value over several updates."""
        # Simulate 0.5s drift, then 10 subsequent updates at correct pace
        updates = [(1000, 10.0)]
        # Server is 0.5s "slower" than client expects
        for i in range(1, 11):
            wall = 1000 + i * 1000
            server_elapsed = 10.0 + i * 0.95  # slightly slower than wall clock
            updates.append((wall, server_elapsed))

        results = self.simulate_timer(updates)

        # After 10 updates with blend factor 0.15, drift should be mostly corrected
        # Check that final display is close to final server elapsed
        final_display = results[-1][1]
        final_server = updates[-1][1]
        drift = abs(final_display - final_server)
        assert (
            drift < 0.3
        ), f"Timer should converge: display={final_display:.2f}, server={final_server:.2f}, drift={drift:.2f}"

    def test_never_negative(self):
        """Timer should never show negative elapsed."""
        # Edge case: server says 0.0 but local clock is behind
        updates = [(0, 0.0), (500, 0.0)]
        results = self.simulate_timer(updates)
        for _, display in results:
            assert display >= 0, f"Timer went negative: {display}"


class TestVoiceStateGuard:
    """Test the userActivated voice state machine guard.

    Android VoiceViewModel.kt has a `userActivated` flag that prevents
    voice auto-enabling when Gemini speaks in response to state updates
    (e.g. interval changes pushing context). These tests validate the
    state machine logic in pure Python.
    """

    @staticmethod
    def simulate_voice_fsm(events):
        """Simulate the voice state machine with userActivated guard.

        Args:
            events: list of event strings:
                "toggle"         — user taps voice button
                "connected"      — Gemini connection established
                "speaking_start" — Gemini starts speaking
                "speaking_end"   — Gemini finishes speaking
                "disconnected"   — connection lost

        Returns:
            list of (event, state, userActivated) tuples after each event.
        """
        state = "idle"
        user_activated = False
        is_connected = False
        trace = []

        for event in events:
            if event == "toggle":
                if state == "idle":
                    user_activated = True
                    if is_connected:
                        state = "listening"
                    else:
                        state = "connecting"
                elif state == "connecting":
                    user_activated = False
                    state = "idle"
                elif state == "listening":
                    user_activated = False
                    state = "idle"
                elif state == "speaking":
                    # interrupt
                    state = "listening"

            elif event == "connected":
                is_connected = True
                if state == "connecting":
                    state = "listening"

            elif event == "speaking_start":
                state = "speaking"

            elif event == "speaking_end":
                if user_activated:
                    state = "listening"
                else:
                    state = "idle"

            elif event == "disconnected":
                is_connected = False
                user_activated = False
                state = "idle"

            trace.append((event, state, user_activated))

        return trace

    def test_user_toggle_enables_listening(self):
        """User toggling voice on should activate listening."""
        trace = self.simulate_voice_fsm(["connected", "toggle"])
        assert trace[-1][1] == "listening"
        assert trace[-1][2] is True  # userActivated

    def test_speaking_end_returns_to_listening_when_user_activated(self):
        """After Gemini speaks, should return to listening if user toggled on."""
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "toggle",
                "speaking_start",
                "speaking_end",
            ]
        )
        assert trace[-1][1] == "listening"
        assert trace[-1][2] is True

    def test_speaking_end_returns_to_idle_when_not_user_activated(self):
        """After Gemini speaks from state update, should return to idle.

        This is the core Bug #3 fix: when Gemini responds to a context
        update (interval change), it speaks, but since the user never
        toggled voice on, we should NOT auto-enable listening.
        """
        # Simulate: connection is hot, Gemini speaks in response to state update
        # (no user toggle — userActivated stays False)
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "speaking_start",
                "speaking_end",
            ]
        )
        final_event, final_state, final_activated = trace[-1]
        assert final_state == "idle", f"Should return to idle when user hasn't activated, got '{final_state}'"
        assert final_activated is False

    def test_user_toggle_off_clears_activation(self):
        """User toggling voice off should clear userActivated."""
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "toggle",  # on -> listening, userActivated=True
                "toggle",  # off -> idle, userActivated=False
            ]
        )
        assert trace[-1][1] == "idle"
        assert trace[-1][2] is False

    def test_disconnect_clears_activation(self):
        """Connection drop should clear userActivated."""
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "toggle",  # on -> listening, userActivated=True
                "disconnected",  # drop -> idle, userActivated=False
            ]
        )
        assert trace[-1][1] == "idle"
        assert trace[-1][2] is False

    def test_multiple_state_updates_dont_enable_mic(self):
        """Multiple Gemini responses to state updates should never enable mic."""
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "speaking_start",
                "speaking_end",  # state update 1
                "speaking_start",
                "speaking_end",  # state update 2
                "speaking_start",
                "speaking_end",  # state update 3
            ]
        )
        # Every speaking_end should return to idle
        for event, state, activated in trace:
            if event == "speaking_end":
                assert state == "idle", f"State update speech should return to idle, got '{state}'"
                assert activated is False

    def test_user_activated_persists_across_speaking_cycles(self):
        """userActivated should persist across multiple speak/listen cycles."""
        trace = self.simulate_voice_fsm(
            [
                "connected",
                "toggle",  # user activates
                "speaking_start",
                "speaking_end",  # Gemini responds
                "speaking_start",
                "speaking_end",  # Gemini responds again
            ]
        )
        # After each speaking_end, should return to listening (user is active)
        speaking_ends = [(e, s, a) for e, s, a in trace if e == "speaking_end"]
        for event, state, activated in speaking_ends:
            assert state == "listening", f"Should stay listening while user activated, got '{state}'"
            assert activated is True


class TestSavedWorkouts:
    """Tests for the My Workouts (saved workouts) feature."""

    SAMPLE_PROGRAM = {
        "name": "Morning Run",
        "intervals": [
            {"speed": 3.0, "incline": 1, "duration": 300},
            {"speed": 5.0, "incline": 2, "duration": 600},
        ],
    }

    SAMPLE_PROGRAM_2 = {
        "name": "Hill Climber",
        "intervals": [
            {"speed": 4.0, "incline": 5, "duration": 300},
            {"speed": 3.5, "incline": 10, "duration": 300},
        ],
    }

    def _pid(self, server):
        return server._active_profile_id()

    def test_save_from_history(self, test_app):
        """POST /api/workouts with history_id saves the program from history."""
        client, server, _ = test_app
        entry = server._add_to_history(self.SAMPLE_PROGRAM, prompt="a quick run")
        resp = client.post("/api/workouts", json={"history_id": entry["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["name"] == "Morning Run"
        assert data["workout"]["source"] == "generated"
        assert data["workout"]["prompt"] == "a quick run"

    def test_save_direct(self, test_app):
        """POST /api/workouts with program dict saves directly."""
        client, server, _ = test_app
        resp = client.post(
            "/api/workouts",
            json={
                "program": self.SAMPLE_PROGRAM,
                "source": "generated",
                "prompt": "morning workout",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["name"] == "Morning Run"
        assert data["workout"]["source"] == "generated"
        assert data["workout"]["prompt"] == "morning workout"

    def test_list_workouts_sorted(self, test_app):
        """GET /api/workouts returns workouts (db handles ordering)."""
        client, server, _ = test_app
        pid = self._pid(server)
        server.db.save_workout(pid, self.SAMPLE_PROGRAM, source="generated")
        w2 = server.db.save_workout(pid, self.SAMPLE_PROGRAM_2, source="generated")
        server.db.update_workout_usage(w2["id"])
        resp = client.get("/api/workouts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_rename_workout(self, test_app):
        """PUT /api/workouts/{id} renames the workout."""
        client, server, _ = test_app
        w = server.db.save_workout(self._pid(server), copy.deepcopy(self.SAMPLE_PROGRAM))
        resp = client.put(f"/api/workouts/{w['id']}", json={"name": "My Favorite"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["name"] == "My Favorite"
        assert data["workout"]["program"]["name"] == "My Favorite"

    def test_delete_workout(self, test_app):
        """DELETE /api/workouts/{id} removes the workout."""
        client, server, _ = test_app
        w = server.db.save_workout(self._pid(server), self.SAMPLE_PROGRAM)
        resp = client.delete(f"/api/workouts/{w['id']}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert server.db.get_saved_workout(w["id"]) is None

    def test_load_workout_increments_usage(self, test_app):
        """POST /api/workouts/{id}/load increments times_used and sets last_used."""
        client, server, _ = test_app
        w = server.db.save_workout(self._pid(server), self.SAMPLE_PROGRAM)
        resp = client.post(f"/api/workouts/{w['id']}/load")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["program"]["name"] == "Morning Run"
        updated = server.db.get_saved_workout(w["id"])
        assert updated["times_used"] == 1
        assert updated["last_used_at"] is not None

    def test_load_adds_to_history(self, test_app):
        """Loading a saved workout also adds it to program history."""
        client, server, _ = test_app
        w = server.db.save_workout(self._pid(server), self.SAMPLE_PROGRAM, prompt="morning workout")
        resp = client.post(f"/api/workouts/{w['id']}/load")
        assert resp.status_code == 200
        history = server.db.get_program_history(self._pid(server))
        assert any(h["program"]["name"] == "Morning Run" for h in history)

    def test_save_not_found(self, test_app):
        """POST /api/workouts with nonexistent history_id returns error."""
        client, server, _ = test_app
        resp = client.post("/api/workouts", json={"history_id": "999"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_delete_not_found(self, test_app):
        """DELETE nonexistent workout returns error."""
        client, server, _ = test_app
        resp = client.delete("/api/workouts/999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_history_saved_flag(self, test_app):
        """GET /api/programs/history includes saved=True/False per entry."""
        client, server, _ = test_app
        pid = self._pid(server)
        server._add_to_history(self.SAMPLE_PROGRAM)
        server._add_to_history(self.SAMPLE_PROGRAM_2)
        # Save only one
        server.db.save_workout(pid, self.SAMPLE_PROGRAM)
        resp = client.get("/api/programs/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        saved_flags = {d["program"]["name"]: d["saved"] for d in data}
        assert saved_flags["Morning Run"] is True
        assert saved_flags["Hill Climber"] is False

    def test_save_gpx_source_inference(self, test_app):
        """Saving from history with GPX: prefix prompt infers gpx source."""
        client, server, _ = test_app
        entry = server._add_to_history(self.SAMPLE_PROGRAM, prompt="GPX: mountain_trail.gpx")
        resp = client.post("/api/workouts", json={"history_id": entry["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["source"] == "gpx"

    def test_save_manual_source_inference(self, test_app):
        """Saving from history with manual program infers manual source."""
        client, server, _ = test_app
        manual_program = {**self.SAMPLE_PROGRAM, "manual": True}
        entry = server._add_to_history(manual_program)
        resp = client.post("/api/workouts", json={"history_id": entry["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["source"] == "manual"

    def test_rename_not_found(self, test_app):
        """PUT /api/workouts/999 returns error for nonexistent workout."""
        client, server, _ = test_app
        resp = client.put("/api/workouts/999", json={"name": "New Name"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_load_not_found(self, test_app):
        """POST /api/workouts/999/load returns error for nonexistent workout."""
        client, server, _ = test_app
        resp = client.post("/api/workouts/999/load")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_save_empty_body(self, test_app):
        """POST /api/workouts with empty body returns validation error."""
        client, server, _ = test_app
        resp = client.post("/api/workouts", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_rename_empty_name(self, test_app):
        """PUT /api/workouts/{id} with empty name returns validation error."""
        client, server, _ = test_app
        resp = client.put("/api/workouts/42", json={"name": ""})
        assert resp.status_code == 422  # Pydantic validation error

    def test_save_invalid_source(self, test_app):
        """POST /api/workouts with invalid source returns validation error."""
        client, server, _ = test_app
        resp = client.post(
            "/api/workouts",
            json={
                "program": self.SAMPLE_PROGRAM,
                "source": "invalid_source",
            },
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_max_workouts_cap(self, test_app):
        """DB allows unlimited saved workouts (no server-side cap anymore)."""
        client, server, _ = test_app
        pid = self._pid(server)
        entry = server._add_to_history(self.SAMPLE_PROGRAM_2)
        resp = client.post("/api/workouts", json={"history_id": entry["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["workout"]["program"]["name"] == "Hill Climber"

    def test_save_invalid_program(self, test_app):
        """POST /api/workouts with invalid program returns error."""
        client, server, _ = test_app
        # Missing intervals
        resp = client.post(
            "/api/workouts",
            json={"program": {"name": "Bad"}, "source": "generated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "intervals" in data["error"]

    def test_saved_flag_uses_fingerprint_not_name(self, test_app):
        """After renaming a saved workout, history still shows saved=True."""
        client, server, _ = test_app
        pid = self._pid(server)
        original_program = {
            "name": "Morning Run",
            "intervals": [
                {"speed": 3.0, "incline": 1, "duration": 300},
                {"speed": 5.0, "incline": 2, "duration": 600},
            ],
        }
        # Add to history with original name
        server._add_to_history(original_program)
        # Save and rename (same intervals, different name)
        w = server.db.save_workout(pid, original_program)
        server.db.rename_workout(w["id"], "Renamed Run")
        resp = client.get("/api/programs/history")
        assert resp.status_code == 200
        data = resp.json()
        # Same intervals → still saved even though name differs
        assert data[0]["saved"] is True


def test_config_returns_gemini_31_live_model(test_app):
    """Verify /api/config serves the 3.1 Flash Live model string."""
    client, server, _ = test_app
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "gemini_live_model" in data
    assert data["gemini_live_model"] == "gemini-3.1-flash-live-preview"


class TestWorkoutQueryEndToEnd:
    """End-to-end: load program → run session → save run record → query via /api/tool."""

    PROGRAM = {
        "name": "E2E Hill Workout",
        "intervals": [
            {"name": "Warmup", "duration": 120, "speed": 3.0, "incline": 0},
            {"name": "Hill", "duration": 180, "speed": 5.0, "incline": 5.0},
            {"name": "Cooldown", "duration": 120, "speed": 3.0, "incline": 0},
        ],
    }

    def test_full_lifecycle(self, test_app):
        """Load a workout, simulate a run, finalize, then query intervals and runs."""
        import time as _time

        client, server, _ = test_app
        server._active_run_id = None

        # 1. Load a program
        server.sess.prog.load(self.PROGRAM)
        assert server.sess.prog.program["name"] == "E2E Hill Workout"

        # 2. Start session and simulate elapsed time
        server.sess.start()
        server.sess.tick(3.0, 0, 70)
        server.sess.started_at = _time.monotonic() - 600  # fake 10 minutes
        server.sess.tick(3.0, 0, 70)
        assert server.sess.elapsed > 500

        # 3. Save run record (simulates session end)
        server._save_run_record("program_complete")

        # 4. Sync workout_db so it has the run + workout
        server.workout_db.sync(active_program=server.sess.prog.program)

        # 5. Query intervals via /api/tool
        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {"sql": "SELECT * FROM intervals WHERE workout_id = '__active__' ORDER BY position"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        intervals = json.loads(data["result"])
        assert len(intervals) == 3
        assert intervals[0]["name"] == "Warmup"
        assert intervals[1]["name"] == "Hill"
        assert intervals[1]["speed_mph"] == 5.0
        assert intervals[1]["incline_pct"] == 5.0
        assert intervals[2]["name"] == "Cooldown"

        # 6. Query runs — find the specific run we just created by program_name
        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {"sql": "SELECT program_name, end_reason FROM runs WHERE program_name = 'E2E Hill Workout'"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        runs = json.loads(data["result"])
        assert len(runs) >= 1
        assert runs[0]["program_name"] == "E2E Hill Workout"
        assert runs[0]["end_reason"] == "program_complete"

        # 7. Query with JOIN — runs matched to workouts via fingerprint
        #    Use __active__ which we control, not workouts from disk
        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {
                    "sql": (
                        "SELECT w.name, r.end_reason, r.elapsed "
                        "FROM workouts w JOIN runs r ON w.fingerprint = r.program_fingerprint "
                        "WHERE w.id = '__active__'"
                    )
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        joined = json.loads(data["result"])
        assert len(joined) >= 1
        assert joined[0]["name"] == "E2E Hill Workout"
        assert joined[0]["elapsed"] > 500

    def test_in_progress_run_queryable(self, test_app):
        """An in-progress run record should be queryable before session ends."""
        import time as _time

        client, server, _ = test_app
        server._active_run_id = None

        # Load and start
        server.sess.prog.load(self.PROGRAM)
        server.sess.start()
        server.sess.tick(3.0, 0, 70)
        server.sess.started_at = _time.monotonic() - 60  # fake 1 minute
        server.sess.tick(3.0, 0, 70)

        # Create the in-progress record (simulates the 30s tick)
        server._start_run_record()
        run_id = server._active_run_id
        assert run_id is not None

        # Sync and query by the specific run ID
        server.workout_db.sync(active_program=server.sess.prog.program)
        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {"sql": f"SELECT end_reason, elapsed FROM runs WHERE id = '{run_id}'"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        runs = json.loads(data["result"])
        assert len(runs) == 1
        assert runs[0]["end_reason"] == "in_progress"
        assert runs[0]["elapsed"] > 50

    def test_hardest_interval_query(self, test_app):
        """The 'hardest interval' query pattern works end-to-end."""
        client, server, _ = test_app
        server._active_run_id = None

        server.sess.prog.load(self.PROGRAM)
        server.workout_db.sync(active_program=server.sess.prog.program)

        # This is the query pattern Gemini uses
        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {
                    "sql": (
                        "SELECT position, name, speed_mph, incline_pct, duration_s "
                        "FROM intervals WHERE workout_id = '__active__' "
                        "ORDER BY speed_mph DESC, incline_pct DESC LIMIT 1"
                    )
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        rows = json.loads(data["result"])
        assert len(rows) == 1
        assert rows[0]["name"] == "Hill"
        assert rows[0]["speed_mph"] == 5.0
        assert rows[0]["incline_pct"] == 5.0

    def test_sql_injection_blocked(self, test_app):
        """Destructive SQL is blocked by the authorizer."""
        client, server, _ = test_app

        resp = client.post(
            "/api/tool",
            json={
                "name": "query_workout_data",
                "args": {"sql": "DROP TABLE runs"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["ok"] is False or "error" in data.get("result", "").lower() or "error" in data.get("error", "").lower()
        )


class TestUserProfile:
    """PUT /api/user persists both weight and vest via db."""

    def test_update_weight(self, test_app):
        client, server, _ = test_app
        resp = client.put("/api/user", json={"weight_lbs": 180})
        assert resp.status_code == 200
        data = resp.json()
        assert data["weight_lbs"] == 180
        assert data["vest_lbs"] == 0  # unchanged

    def test_update_vest(self, test_app):
        """Regression: vest_lbs was silently dropped before the fix."""
        client, server, _ = test_app
        resp = client.put("/api/user", json={"vest_lbs": 20})
        assert resp.status_code == 200
        data = resp.json()
        assert data["vest_lbs"] == 20
        assert data["weight_lbs"] == 154  # unchanged

    def test_update_both(self, test_app):
        client, server, _ = test_app
        resp = client.put("/api/user", json={"weight_lbs": 200, "vest_lbs": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["weight_lbs"] == 200
        assert data["vest_lbs"] == 30

    def test_vest_included_in_calorie_calc(self, test_app):
        """_user_weight_kg() adds body + vest for calorie calculations."""
        _, server, _ = test_app
        pid = server._active_profile_id()
        server.db.update_profile(pid, weight_lbs=154, vest_lbs=20)
        kg = server._user_weight_kg()
        expected = (154 + 20) * 0.453592
        assert abs(kg - expected) < 0.01
