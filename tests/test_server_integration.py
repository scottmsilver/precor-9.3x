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
    from workout_session import WorkoutSession

    # Save originals
    orig_client = getattr(server, "client", None)
    orig_sess = getattr(server, "sess", None)
    orig_loop = getattr(server, "loop", None)
    orig_queue = getattr(server, "msg_queue", None)

    # Set up mocks
    server.client = mock_client
    server.sess = WorkoutSession()
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

    from starlette.testclient import TestClient

    # We need to bypass the lifespan context manager for testing
    server.app.router.lifespan_context = None
    tc = TestClient(server.app, raise_server_exceptions=True)
    yield tc, server, mock_client

    # Restore
    server.client = orig_client
    server.sess = orig_sess
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
        assert "wrong" in data["text"].lower() or "error" in data["text"].lower()


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
        assert config["uses"] == 1
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
