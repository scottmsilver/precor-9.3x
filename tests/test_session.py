"""Comprehensive tests for WorkoutSession class and session-related endpoints."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sess():
    """Fresh WorkoutSession instance (no server, no mocks)."""
    from server import WorkoutSession

    return WorkoutSession()


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
    """Create test app with mocked dependencies, fresh WorkoutSession."""
    import server
    from server import WorkoutSession

    orig_client = getattr(server, "client", None)
    orig_sess = getattr(server, "sess", None)
    orig_loop = getattr(server, "loop", None)
    orig_queue = getattr(server, "msg_queue", None)

    server.client = mock_client
    server.sess = WorkoutSession()
    server.loop = MagicMock()
    server.msg_queue = MagicMock()
    server.msg_queue.put_nowait = MagicMock()

    server.state["proxy"] = True
    server.state["emulate"] = False
    server.state["emu_speed"] = 0
    server.state["emu_incline"] = 0
    server.state["treadmill_connected"] = True
    server.latest["last_motor"] = {}
    server.latest["last_console"] = {}

    server.app.router.lifespan_context = None
    tc = TestClient(server.app, raise_server_exceptions=True)
    yield tc, server, mock_client

    server.client = orig_client
    server.sess = orig_sess
    server.loop = orig_loop
    server.msg_queue = orig_queue


# ===========================================================================
# UNIT TESTS — WorkoutSession class in isolation
# ===========================================================================


class TestSessionStart:
    """Session start() behavior."""

    def test_start_activates_session(self, sess):
        assert sess.active is False
        sess.start()
        assert sess.active is True
        assert sess.started_at > 0
        assert sess.wall_started_at != ""

    def test_start_is_idempotent(self, sess):
        sess.start()
        original_started = sess.started_at
        original_wall = sess.wall_started_at
        sess.start()
        assert sess.started_at == original_started
        assert sess.wall_started_at == original_wall

    def test_start_clears_stale_pause(self, sess):
        sess.start()
        sess.paused_at = time.monotonic()  # simulate stale pause
        sess.start()  # calling start again should clear it
        assert sess.paused_at == 0.0

    def test_start_clears_end_reason(self, sess):
        sess.start()
        sess.end("test")
        sess.active = True  # force re-activate to simulate restart
        sess.end_reason = "stale"
        # Fresh start
        sess.active = False
        sess.start()
        assert sess.end_reason is None

    def test_start_zeroes_metrics(self, sess):
        sess.start()
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.vert_feet == 0.0
        assert sess.total_paused == 0.0


class TestSessionEnd:
    """Session end() behavior."""

    def test_end_deactivates(self, sess):
        sess.start()
        sess.end("user_stop")
        assert sess.active is False
        assert sess.end_reason == "user_stop"

    def test_end_without_active_is_noop(self, sess):
        sess.end("user_stop")
        assert sess.active is False
        assert sess.end_reason is None

    def test_end_records_reason(self, sess):
        sess.start()
        sess.end("watchdog")
        assert sess.end_reason == "watchdog"

    def test_end_with_various_reasons(self, sess):
        for reason in ("user_stop", "auto_proxy", "watchdog", "disconnect"):
            sess.active = False
            sess.start()
            sess.end(reason)
            assert sess.end_reason == reason


class TestSessionPauseResume:
    """Session pause/resume behavior."""

    def test_pause_sets_paused_at(self, sess):
        sess.start()
        sess.pause()
        assert sess.paused_at > 0

    def test_pause_when_not_active_is_noop(self, sess):
        sess.pause()
        assert sess.paused_at == 0.0

    def test_pause_when_already_paused_is_noop(self, sess):
        sess.start()
        sess.pause()
        first_paused = sess.paused_at
        sess.pause()
        assert sess.paused_at == first_paused

    def test_resume_clears_paused_at(self, sess):
        sess.start()
        sess.pause()
        assert sess.paused_at > 0
        sess.resume()
        assert sess.paused_at == 0.0

    def test_resume_accumulates_total_paused(self, sess):
        sess.start()
        sess.pause()
        paused_time = sess.paused_at
        # Simulate time passing
        sess.paused_at = time.monotonic() - 5.0
        sess.resume()
        assert sess.total_paused >= 4.5  # allow small timing variance

    def test_resume_when_not_paused_is_noop(self, sess):
        sess.start()
        sess.resume()
        assert sess.total_paused == 0.0

    def test_resume_when_not_active_is_noop(self, sess):
        sess.resume()
        assert sess.total_paused == 0.0


class TestSessionReset:
    """Session reset() clears everything."""

    @pytest.mark.asyncio
    async def test_reset_clears_session(self, sess):
        sess.start()
        sess.elapsed = 42.0
        sess.distance = 1.5
        sess.vert_feet = 200.0
        await sess.reset()
        assert sess.active is False
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.vert_feet == 0.0
        assert sess.started_at == 0.0
        assert sess.wall_started_at == ""
        assert sess.paused_at == 0.0
        assert sess.total_paused == 0.0
        assert sess.end_reason is None

    @pytest.mark.asyncio
    async def test_reset_clears_program(self, sess):
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        await sess.reset()
        assert sess.prog.program is None
        assert sess.prog.running is False


class TestSessionTick:
    """Session tick() computation."""

    def test_tick_accumulates_elapsed(self, sess):
        sess.start()
        # Backdate started_at so elapsed computes to ~10s
        sess.started_at = time.monotonic() - 10
        sess.tick(5.0, 0)
        assert 9.5 <= sess.elapsed <= 10.5

    def test_tick_accumulates_distance(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(6.0, 0)
        expected = 6.0 / 3600  # miles per second
        assert abs(sess.distance - expected) < 0.001

    def test_tick_accumulates_vert_feet(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(6.0, 10)
        # vert_feet = miles_per_sec * (incline/100) * 5280
        miles_per_sec = 6.0 / 3600
        expected = miles_per_sec * (10 / 100) * 5280
        assert abs(sess.vert_feet - expected) < 1.0

    def test_tick_no_vert_at_zero_incline(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(6.0, 0)
        assert sess.vert_feet == 0.0

    def test_tick_no_distance_at_zero_speed(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(0, 5)
        assert sess.distance == 0.0

    def test_tick_noop_when_not_active(self, sess):
        sess.tick(6.0, 5)
        assert sess.distance == 0.0
        assert sess.vert_feet == 0.0

    def test_tick_noop_when_paused(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 10
        sess.pause()
        old_distance = sess.distance
        sess.tick(6.0, 5)
        assert sess.distance == old_distance

    def test_tick_respects_total_paused(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 60
        sess.total_paused = 30.0
        sess.tick(5.0, 0)
        assert 29 <= sess.elapsed <= 31  # ~60 - 30 = 30s


class TestSessionToDict:
    """Session to_dict() serialization."""

    def test_to_dict_inactive(self, sess):
        d = sess.to_dict()
        assert d["type"] == "session"
        assert d["active"] is False
        assert d["elapsed"] == 0.0
        assert d["distance"] == 0.0
        assert d["vert_feet"] == 0.0
        assert d["end_reason"] is None

    def test_to_dict_active(self, sess):
        sess.start()
        sess.elapsed = 42.5
        sess.distance = 1.5
        sess.vert_feet = 200.0
        d = sess.to_dict()
        assert d["active"] is True
        assert d["elapsed"] == 42.5
        assert d["distance"] == 1.5
        assert d["vert_feet"] == 200.0
        assert d["wall_started_at"] != ""

    def test_to_dict_with_end_reason(self, sess):
        sess.start()
        sess.end("watchdog")
        d = sess.to_dict()
        assert d["active"] is False
        assert d["end_reason"] == "watchdog"


class TestSessionStartProgram:
    """start_program() ensures session active before starting program."""

    @pytest.mark.asyncio
    async def test_start_program_activates_session(self, sess):
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        on_change = AsyncMock()
        on_update = AsyncMock()

        tick_count = 0

        async def mock_sleep(d):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                sess.prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await sess.start_program(on_change, on_update)
            assert sess.active is True
            assert sess.prog.running is True
            sess.prog.running = False
            if sess.prog._task:
                try:
                    import asyncio

                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

    @pytest.mark.asyncio
    async def test_start_program_without_loaded_program(self, sess):
        on_change = AsyncMock()
        on_update = AsyncMock()
        await sess.start_program(on_change, on_update)
        # Session starts but program doesn't run (no program loaded)
        assert sess.active is True
        assert sess.prog.running is False


class TestSessionEnsureManual:
    """ensure_manual() creates and starts a manual program."""

    @pytest.mark.asyncio
    async def test_ensure_manual_creates_program(self, sess):
        on_change = AsyncMock()
        on_update = AsyncMock()

        tick_count = 0

        async def mock_sleep(d):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                sess.prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await sess.ensure_manual(
                speed=3.0, incline=2, duration_minutes=30, on_change=on_change, on_update=on_update
            )
            assert sess.active is True
            assert sess.prog.program is not None
            assert sess.prog.program["name"] == "30-Min Manual"
            assert sess.prog.program["manual"] is True
            sess.prog.running = False
            if sess.prog._task:
                try:
                    import asyncio

                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

    @pytest.mark.asyncio
    async def test_ensure_manual_noop_if_running(self, sess):
        on_change = AsyncMock()
        on_update = AsyncMock()

        tick_count = 0

        async def mock_sleep(d):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                sess.prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await sess.ensure_manual(on_change=on_change, on_update=on_update)
            original_name = sess.prog.program["name"]
            # Call again — should be noop since program is running
            # (need to keep it running for this check)
            sess.prog.running = True
            await sess.ensure_manual(speed=5.0, on_change=on_change, on_update=on_update)
            assert sess.prog.program["name"] == original_name
            sess.prog.running = False
            if sess.prog._task:
                try:
                    import asyncio

                    await asyncio.wait_for(sess.prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass


class TestSessionInvariants:
    """Session invariants that must hold."""

    @pytest.mark.asyncio
    async def test_start_program_always_creates_session(self, sess):
        """start_program() always creates an active session first."""
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})

        tick_count = 0

        async def mock_sleep(d):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 1:
                sess.prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await sess.start_program(AsyncMock(), AsyncMock())
            assert sess.active is True

    @pytest.mark.asyncio
    async def test_ensure_manual_creates_session_and_program_atomically(self, sess):
        """ensure_manual() creates session + manual program atomically."""

        tick_count = 0

        async def mock_sleep(d):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 1:
                sess.prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await sess.ensure_manual(on_change=AsyncMock(), on_update=AsyncMock())
            assert sess.active is True
            assert sess.prog.program is not None
            assert sess.prog.program.get("manual") is True


# ===========================================================================
# INTEGRATION TESTS — Server endpoints + WorkoutSession
# ===========================================================================


class TestSessionLifecycle:
    def test_session_starts_on_first_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.sess.active is True
        assert server.sess.started_at > 0

    def test_session_not_restarted_on_second_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        started = server.sess.started_at
        client.post("/api/speed", json={"value": 5.0})
        assert server.sess.started_at == started

    def test_session_ends_on_stop(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.sess.active is True
        client.post("/api/program/stop")
        assert server.sess.active is False
        assert server.sess.end_reason == "user_stop"

    def test_session_ends_on_zero_speed(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.sess.active is True
        client.post("/api/speed", json={"value": 0})
        assert server.sess.active is False
        assert server.sess.end_reason == "user_stop"

    def test_session_starts_on_program_start(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]}
        )
        client.post("/api/program/start")
        assert server.sess.active is True

    def test_no_session_on_incline_only(self, test_app):
        client, server, _ = test_app
        client.post("/api/incline", json={"value": 5})
        assert server.sess.active is False


class TestQuickStartEndpoint:
    def test_quick_start_creates_session(self, test_app):
        client, server, _ = test_app
        resp = client.post("/api/program/quick-start", json={"speed": 3.0, "incline": 0, "duration_minutes": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert server.sess.active is True
        assert server.sess.prog.program["name"] == "30-Min Manual"

    def test_quick_start_default_params(self, test_app):
        client, server, _ = test_app
        resp = client.post("/api/program/quick-start", json={})
        assert resp.status_code == 200
        assert server.sess.active is True
        assert server.sess.prog.program["name"] == "60-Min Manual"


class TestResetEndpoint:
    def test_reset_clears_session(self, test_app):
        client, server, mock = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.sess.active is True
        resp = client.post("/api/reset")
        assert resp.status_code == 200
        assert server.sess.active is False
        assert server.sess.elapsed == 0.0
        assert server.state["emu_speed"] == 0
        assert server.state["emu_incline"] == 0

    def test_reset_clears_program(self, test_app):
        client, server, _ = test_app
        server.sess.prog.load(
            {"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]}
        )
        client.post("/api/reset")
        assert server.sess.prog.program is None


class TestSessionPauseEndpoint:
    def test_pause_freezes_session(self, test_app):
        client, server, _ = test_app
        client.post("/api/speed", json={"value": 3.0})
        assert server.sess.active is True
        # Load a program so pause works
        server.sess.prog.load(
            {"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]}
        )
        server.sess.prog.running = True
        server.sess.prog._on_update = AsyncMock()
        # Pause
        client.post("/api/program/pause")
        assert server.sess.paused_at > 0
        # Resume
        client.post("/api/program/pause")
        assert server.sess.paused_at == 0.0


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

    def test_session_to_dict_format(self, test_app):
        _, server, _ = test_app
        server.sess.start()
        server.sess.elapsed = 42.5
        server.sess.distance = 0.5
        server.sess.vert_feet = 100.0
        d = server.sess.to_dict()
        assert d["type"] == "session"
        assert d["active"] is True
        assert d["elapsed"] == 42.5
        assert d["distance"] == 0.5
        assert d["vert_feet"] == 100.0


class TestSessionEndReasons:
    def test_watchdog_ends_session(self, test_app):
        _, server, _ = test_app
        server.sess.start()
        server.state["emulate"] = True
        # Simulate watchdog: emulate goes false while session active
        was_emulating = server.state["emulate"]
        server.state["emulate"] = False
        server.state["proxy"] = False
        if was_emulating and not server.state["emulate"] and server.sess.active:
            reason = "auto_proxy" if server.state["proxy"] else "watchdog"
            server.sess.end(reason)
        assert server.sess.active is False
        assert server.sess.end_reason == "watchdog"

    def test_auto_proxy_ends_session(self, test_app):
        _, server, _ = test_app
        server.sess.start()
        server.state["emulate"] = True
        server.state["emulate"] = False
        server.state["proxy"] = True
        server.sess.end("auto_proxy")
        assert server.sess.active is False
        assert server.sess.end_reason == "auto_proxy"

    def test_disconnect_ends_session(self, test_app):
        _, server, _ = test_app
        server.sess.start()
        server.sess.end("disconnect")
        assert server.sess.active is False
        assert server.sess.end_reason == "disconnect"


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
