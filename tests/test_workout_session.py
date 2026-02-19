"""Unit tests for WorkoutSession — standalone, no server dependencies."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from workout_session import WorkoutSession


@pytest.fixture
def sess():
    return WorkoutSession()


def run(coro):
    """Run async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- Session lifecycle ---


class TestSessionStart:
    def test_start_activates(self, sess):
        assert not sess.active
        sess.start()
        assert sess.active
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.end_reason is None
        assert sess.wall_started_at != ""

    def test_start_idempotent(self, sess):
        sess.start()
        first_started = sess.started_at
        sess.start()
        assert sess.started_at == first_started  # didn't reset

    def test_start_clears_stale_pause(self, sess):
        sess.start()
        sess.pause()
        assert sess.paused_at > 0
        sess.start()  # idempotent, but should clear pause
        assert sess.paused_at == 0.0


class TestSessionEnd:
    def test_end_deactivates(self, sess):
        sess.start()
        sess.end("user_stop")
        assert not sess.active
        assert sess.end_reason == "user_stop"

    def test_end_noop_when_inactive(self, sess):
        sess.end("user_stop")  # no-op
        assert not sess.active
        assert sess.end_reason is None

    def test_end_records_reason(self, sess):
        sess.start()
        sess.end("watchdog")
        assert sess.end_reason == "watchdog"

    def test_end_preserves_elapsed(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 10  # fake 10s ago
        sess.tick(5.0, 0)  # accumulate some data
        elapsed_before = sess.elapsed
        sess.end("user_stop")
        assert sess.elapsed >= elapsed_before


class TestSessionPauseResume:
    def test_pause_sets_timestamp(self, sess):
        sess.start()
        sess.pause()
        assert sess.paused_at > 0

    def test_pause_noop_when_inactive(self, sess):
        sess.pause()
        assert sess.paused_at == 0.0

    def test_pause_noop_when_already_paused(self, sess):
        sess.start()
        sess.pause()
        first_pause = sess.paused_at
        sess.pause()  # should not change
        assert sess.paused_at == first_pause

    def test_resume_clears_pause(self, sess):
        sess.start()
        sess.pause()
        assert sess.paused_at > 0
        sess.resume()
        assert sess.paused_at == 0.0
        assert sess.total_paused > 0  # some tiny amount

    def test_resume_noop_when_not_paused(self, sess):
        sess.start()
        sess.resume()  # no-op
        assert sess.total_paused == 0.0


class TestSessionReset:
    def test_reset_clears_everything(self, sess):
        sess.start()
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        run(sess.reset())
        assert not sess.active
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.vert_feet == 0.0
        assert sess.wall_started_at == ""
        assert sess.end_reason is None
        assert sess.prog.program is None


# --- Tick computation ---


class TestTick:
    def test_tick_advances_elapsed(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 5  # fake 5s ago
        sess.tick(0, 0)
        assert sess.elapsed >= 4.9

    def test_tick_accumulates_distance(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(6.0, 0)  # 6 mph
        assert sess.distance > 0

    def test_tick_accumulates_vert(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 1
        sess.tick(6.0, 5)  # 6 mph, 5% incline
        assert sess.vert_feet > 0

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

    def test_tick_noop_when_inactive(self, sess):
        sess.tick(6.0, 5)
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0

    def test_tick_noop_when_paused(self, sess):
        sess.start()
        sess.started_at = time.monotonic() - 5
        sess.tick(6.0, 0)
        dist_before = sess.distance
        sess.pause()
        sess.tick(6.0, 0)  # should not advance
        assert sess.distance == dist_before


# --- Program lifecycle invariant ---


class TestStartProgram:
    def test_start_program_ensures_session(self, sess):
        """The key invariant: start_program always activates the session."""
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        on_change = AsyncMock()
        on_update = AsyncMock()
        run(sess.start_program(on_change, on_update))
        assert sess.active
        assert sess.prog.running

    def test_start_program_idempotent_session(self, sess):
        """Starting a second program keeps the same session."""
        sess.start()
        first_started = sess.started_at
        sess.prog.load({"name": "Test", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        run(sess.start_program(AsyncMock(), AsyncMock()))
        assert sess.started_at == first_started


class TestEnsureManual:
    def test_ensure_manual_creates_program_and_session(self, sess):
        on_change = AsyncMock()
        on_update = AsyncMock()
        run(sess.ensure_manual(speed=5.0, incline=2, on_change=on_change, on_update=on_update))
        assert sess.active
        assert sess.prog.running
        assert sess.prog.is_manual
        assert sess.prog.program["name"] == "60-Min Manual"

    def test_ensure_manual_noop_when_running(self, sess):
        """If a program is already running, don't replace it."""
        sess.prog.load({"name": "My Workout", "intervals": [{"name": "A", "duration": 60, "speed": 3.0, "incline": 0}]})
        run(sess.start_program(AsyncMock(), AsyncMock()))
        run(sess.ensure_manual(speed=5.0, incline=0, on_change=AsyncMock(), on_update=AsyncMock()))
        assert sess.prog.program["name"] == "My Workout"  # not replaced

    def test_ensure_manual_custom_duration(self, sess):
        run(sess.ensure_manual(duration_minutes=30, on_change=AsyncMock(), on_update=AsyncMock()))
        assert sess.prog.program["name"] == "30-Min Manual"
        assert sess.prog.program["intervals"][0]["duration"] == 1800


# --- to_dict ---


class TestToDict:
    def test_to_dict_format(self, sess):
        d = sess.to_dict()
        assert d["type"] == "session"
        assert "active" in d
        assert "elapsed" in d
        assert "distance" in d
        assert "vert_feet" in d
        assert "wall_started_at" in d
        assert "end_reason" in d

    def test_to_dict_reflects_state(self, sess):
        sess.start()
        d = sess.to_dict()
        assert d["active"] is True
        assert d["wall_started_at"] != ""

        sess.end("user_stop")
        d = sess.to_dict()
        assert d["active"] is False
        assert d["end_reason"] == "user_stop"


# --- Integration: full workflow ---


class TestFullWorkflow:
    def test_manual_start_stop(self, sess):
        """Full flow: ensure_manual → tick → end."""
        run(sess.ensure_manual(speed=5.0, incline=3, on_change=AsyncMock(), on_update=AsyncMock()))
        assert sess.active
        assert sess.prog.running

        # Simulate some ticks
        sess.started_at = time.monotonic() - 10
        for _ in range(5):
            sess.tick(5.0, 3)

        assert sess.elapsed > 0
        assert sess.distance > 0
        assert sess.vert_feet > 0

        # Stop
        sess.end("user_stop")
        assert not sess.active
        assert sess.end_reason == "user_stop"
        # Program still has data (not reset)
        assert sess.prog.program is not None

    def test_pause_resume_preserves_session(self, sess):
        run(sess.ensure_manual(speed=3.0, incline=0, on_change=AsyncMock(), on_update=AsyncMock()))
        sess.pause()
        assert sess.active  # session still active, just paused
        assert sess.paused_at > 0
        sess.resume()
        assert sess.paused_at == 0.0
        assert sess.active

    @pytest.mark.asyncio
    async def test_reset_after_workout(self, sess):
        await sess.ensure_manual(speed=3.0, incline=0, on_change=AsyncMock(), on_update=AsyncMock())
        sess.started_at = time.monotonic() - 60
        sess.tick(3.0, 0)
        await sess.reset()
        assert not sess.active
        assert sess.elapsed == 0.0
        assert sess.distance == 0.0
        assert sess.prog.program is None
