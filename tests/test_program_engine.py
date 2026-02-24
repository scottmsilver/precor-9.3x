"""Unit tests for ProgramState interval engine."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from tests.helpers import FakeClock, make_program

from program_engine import ProgramState


class TestLoadProgram:
    def test_load_program(self, prog):
        program = make_program()
        prog.load(program)
        assert prog.program == program
        assert prog.running is False
        assert prog.current_interval == 0

    def test_load_resets_previous(self, loaded_prog):
        loaded_prog.running = True
        loaded_prog.current_interval = 2
        loaded_prog.total_elapsed = 100
        loaded_prog.load(make_program(name="New"))
        assert loaded_prog.program["name"] == "New"
        assert loaded_prog.running is False
        assert loaded_prog.current_interval == 0
        assert loaded_prog.total_elapsed == 0


class TestStart:
    @pytest.mark.asyncio
    async def test_start_begins_execution(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0
        clock = FakeClock()
        loaded_prog._clock = clock

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count >= 2:
                loaded_prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await loaded_prog.start(on_change, on_update)
            # Let the task run
            if loaded_prog._task:
                try:
                    await asyncio.wait_for(loaded_prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        on_change.assert_called()
        assert on_change.call_args_list[0].args == (2.0, 0)

    @pytest.mark.asyncio
    async def test_start_without_program_is_noop(self, prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        await prog.start(on_change, on_update)
        assert prog.running is False
        on_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_stops_previous(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        call_count = 0
        clock = FakeClock()
        loaded_prog._clock = clock

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            clock.advance(1)
            # Don't stop running — let the second start() call stop() while
            # the program is still "running" so on_change(0,0) fires.

        with patch("asyncio.sleep", side_effect=mock_sleep):
            # First start — sets running=True, spawns tick loop
            await loaded_prog.start(on_change, on_update)
            # running is True from start(); immediately start again
            # This calls stop() internally, which sees was_running=True
            call_count = 0
            on_change.reset_mock()
            await loaded_prog.start(on_change, on_update)
            loaded_prog.running = False
            if loaded_prog._task:
                try:
                    await asyncio.wait_for(loaded_prog._task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        # The second start() called stop() first, which found was_running=True
        # and called on_change(0, 0)
        assert any(c.args == (0, 0) for c in on_change.call_args_list)


class TestTick:
    @pytest.mark.asyncio
    async def test_tick_advances_elapsed(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0
        clock = FakeClock()
        loaded_prog._clock = clock

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count >= 5:
                loaded_prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await loaded_prog.start(on_change, on_update)
            if loaded_prog._task:
                try:
                    await asyncio.wait_for(loaded_prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        assert loaded_prog.total_elapsed == 5
        assert loaded_prog.interval_elapsed == 5

    @pytest.mark.asyncio
    async def test_interval_transition(self):
        """3-interval program with short durations — verify transition."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 3, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 3, "speed": 4.0, "incline": 2},
                    {"name": "C", "duration": 3, "speed": 2.0, "incline": 0},
                ]
            )
        )
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count >= 5:
                prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        # After 3 ticks: transition from A to B
        # on_change should have been called with B's speed/incline
        calls = [(c.args[0], c.args[1]) for c in on_change.call_args_list]
        assert (4.0, 2) in calls  # B's values

    @pytest.mark.asyncio
    async def test_all_intervals_complete(self):
        """Short program completes, calls on_change(0,0)."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 2, "speed": 3.0, "incline": 1},
                ]
            )
        )
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count >= 5:
                prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        assert prog.completed is True
        assert prog.running is False
        # Final on_change should be (0, 0)
        assert on_change.call_args_list[-1].args == (0, 0)

    @pytest.mark.asyncio
    async def test_finish_broadcasts_completed_state(self):
        """on_update must be called with completed=True when program finishes.

        Regression test: _finish() was not calling _broadcast(), so server.py
        never learned the program completed and the session timer ran forever.
        """
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(make_program([{"name": "A", "duration": 2, "speed": 3.0, "incline": 1}]))
        on_change = AsyncMock()
        on_update = AsyncMock()

        async def mock_sleep(duration):
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert prog.completed is True
        # The final on_update call must contain completed=True so server.py
        # can end the session. Without the _broadcast() in _finish(), this
        # final call never happens.
        final_states = [c.args[0] for c in on_update.call_args_list if c.args[0].get("completed")]
        assert len(final_states) > 0, (
            "on_update was never called with completed=True — " "session timer will run forever"
        )


class TestPause:
    @pytest.mark.asyncio
    async def test_pause_stops_progress(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0
        clock = FakeClock()
        loaded_prog._clock = clock

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count == 3:
                loaded_prog.paused = True
                loaded_prog._pause_start = clock()
            if tick_count >= 6:
                loaded_prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await loaded_prog.start(on_change, on_update)
            if loaded_prog._task:
                try:
                    await asyncio.wait_for(loaded_prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        # tick_loop: sleep then check paused then compute elapsed.
        # Ticks 1-2 advance clock (paused=False), tick 3 sets paused so
        # ticks 3-5 are paused (time passes but _pause_accumulated isn't
        # updated since we set paused directly without toggle_pause),
        # tick 6 exits. Since pause_start is set, elapsed = 6 - 0 - 0 = 6
        # but paused ticks don't compute elapsed, so last computed = 2.
        assert loaded_prog.total_elapsed == 2

    @pytest.mark.asyncio
    async def test_toggle_pause(self, loaded_prog):
        await loaded_prog.toggle_pause()
        assert loaded_prog.paused is True
        await loaded_prog.toggle_pause()
        assert loaded_prog.paused is False


class TestSkip:
    @pytest.mark.asyncio
    async def test_skip_advances_interval(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        await loaded_prog.skip()
        assert loaded_prog.current_interval == 1
        assert loaded_prog.interval_elapsed == 0
        on_change.assert_called_with(6.0, 3)

    @pytest.mark.asyncio
    async def test_skip_jumps_total_elapsed(self, loaded_prog):
        """After skip, total_elapsed should jump to the cumulative duration of skipped intervals."""
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        # Default program: Warmup(60s), Run(120s), Cooldown(60s)
        # Skip from interval 0 → interval 1, total_elapsed should be 60
        await loaded_prog.skip()
        assert loaded_prog.current_interval == 1
        assert loaded_prog.total_elapsed == 60
        assert loaded_prog.interval_elapsed == 0

    @pytest.mark.asyncio
    async def test_skip_last_finishes(self):
        prog = ProgramState()
        prog.load(
            make_program(
                [
                    {"name": "Only", "duration": 60, "speed": 3.0, "incline": 0},
                ]
            )
        )
        on_change = AsyncMock()
        on_update = AsyncMock()
        prog.running = True
        prog._on_change = on_change
        prog._on_update = on_update
        await prog.skip()
        assert prog.completed is True
        assert prog.running is False


class TestPrev:
    @pytest.mark.asyncio
    async def test_prev_goes_back(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        # Start at interval 1
        loaded_prog.current_interval = 1
        loaded_prog.interval_elapsed = 30
        await loaded_prog.prev()
        assert loaded_prog.current_interval == 0
        assert loaded_prog.interval_elapsed == 0
        on_change.assert_called_with(2.0, 0)  # Warmup speed/incline

    @pytest.mark.asyncio
    async def test_prev_at_first_stays(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        loaded_prog.current_interval = 0
        loaded_prog.interval_elapsed = 15
        await loaded_prog.prev()
        assert loaded_prog.current_interval == 0
        assert loaded_prog.interval_elapsed == 0
        on_change.assert_called_with(2.0, 0)  # Warmup, restarted

    @pytest.mark.asyncio
    async def test_prev_rewinds_total_elapsed(self):
        """After prev, total_elapsed should rewind to the start of the previous interval."""
        prog = ProgramState()
        prog.load(make_program())
        on_change = AsyncMock()
        on_update = AsyncMock()
        prog.running = True
        prog._on_change = on_change
        prog._on_update = on_update
        # Start at interval 2 (Cooldown, cumulative=180s)
        prog.current_interval = 2
        prog.total_elapsed = 200
        prog.interval_elapsed = 20
        await prog.prev()
        # Should go back to interval 1 (Run), total_elapsed = 60
        assert prog.current_interval == 1
        assert prog.total_elapsed == 60
        assert prog.interval_elapsed == 0

    @pytest.mark.asyncio
    async def test_prev_when_not_running_is_noop(self, loaded_prog):
        on_change = AsyncMock()
        loaded_prog._on_change = on_change
        loaded_prog.running = False
        loaded_prog.current_interval = 1
        await loaded_prog.prev()
        assert loaded_prog.current_interval == 1
        on_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_then_prev_roundtrip(self, loaded_prog):
        """Skip forward then prev back should return to original interval."""
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        assert loaded_prog.current_interval == 0
        await loaded_prog.skip()
        assert loaded_prog.current_interval == 1
        await loaded_prog.prev()
        assert loaded_prog.current_interval == 0
        assert loaded_prog.interval_elapsed == 0


class TestSkipWhilePaused:
    @pytest.mark.asyncio
    async def test_skip_while_paused_preserves_timing(self):
        """Skip during a pause must not cause timer drift on resume.

        Regression: _pause_accumulated didn't include the in-progress pause,
        so _loop_start was miscalculated and the timer jumped on resume.
        """
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 60, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 120, "speed": 6.0, "incline": 3},
                    {"name": "C", "duration": 60, "speed": 2.0, "incline": 0},
                ]
            )
        )
        on_change = AsyncMock()
        on_update = AsyncMock()
        tick_count = 0

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)
            if tick_count == 10:
                # Pause at t=10
                await prog.toggle_pause()
            if tick_count == 15:
                # Skip while paused (5s into pause)
                await prog.skip()
            if tick_count == 20:
                # Resume after 10s of pause
                await prog.toggle_pause()
            if tick_count >= 25:
                prog.running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # After skip from A→B, total_elapsed should be cumulative_at(1) = 60
        # After resume and 5 more non-paused ticks, total_elapsed ≈ 65
        # Key check: elapsed should NOT include the 10s of paused time
        assert prog.current_interval == 1
        assert 63 <= prog.total_elapsed <= 67, f"Expected ~65 (60 + 5 ticks after resume), got {prog.total_elapsed}"

    @pytest.mark.asyncio
    async def test_prev_while_paused_preserves_timing(self):
        """Prev during a pause must not cause timer drift on resume."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 60, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 120, "speed": 6.0, "incline": 3},
                    {"name": "C", "duration": 60, "speed": 2.0, "incline": 0},
                ]
            )
        )
        on_change = AsyncMock()
        on_update = AsyncMock()

        # Manually set up at interval 2 with some elapsed time
        prog.running = True
        prog._on_change = on_change
        prog._on_update = on_update
        prog.current_interval = 2
        prog.total_elapsed = 200
        prog._interval_start_elapsed = 180
        prog.interval_elapsed = 20
        prog._loop_start = clock() - 200  # simulated start

        # Pause
        prog.paused = True
        prog._pause_start = clock()

        # Advance clock during pause
        clock.advance(10)

        # Prev while paused
        await prog.prev()

        # Should be at interval 1, total_elapsed = 60
        assert prog.current_interval == 1
        assert prog.total_elapsed == 60
        assert prog.interval_elapsed == 0

        # Resume and check clock is consistent
        await prog.toggle_pause()
        # After resume, _loop_start should be set so that
        # real_elapsed = clock() - _loop_start - _pause_accumulated = 60
        real_elapsed = clock() - prog._loop_start - prog._pause_accumulated
        assert 59 <= real_elapsed <= 61, f"Expected real_elapsed ~60 after resume, got {real_elapsed}"


class TestExtend:
    @pytest.mark.asyncio
    async def test_extend_adds_time(self, loaded_prog):
        loaded_prog.running = True
        loaded_prog._on_update = AsyncMock()
        ok = await loaded_prog.extend_current(30)
        assert ok is True
        assert loaded_prog.current_iv["duration"] == 90

    @pytest.mark.asyncio
    async def test_extend_minimum_clamp(self, loaded_prog):
        loaded_prog.running = True
        loaded_prog._on_update = AsyncMock()
        ok = await loaded_prog.extend_current(-100)
        assert ok is True
        assert loaded_prog.current_iv["duration"] == 10

    @pytest.mark.asyncio
    async def test_extend_when_not_running(self, loaded_prog):
        ok = await loaded_prog.extend_current(30)
        assert ok is False


class TestAddIntervals:
    @pytest.mark.asyncio
    async def test_add_intervals(self, loaded_prog):
        loaded_prog._on_update = AsyncMock()
        new_ivs = [{"name": "Extra", "duration": 120, "speed": 15.0, "incline": 20}]
        ok = await loaded_prog.add_intervals(new_ivs)
        assert ok is True
        assert len(loaded_prog.program["intervals"]) == 4
        added = loaded_prog.program["intervals"][-1]
        assert added["speed"] == 12.0  # clamped
        assert added["incline"] == 15  # clamped

    @pytest.mark.asyncio
    async def test_add_intervals_without_program(self, prog):
        ok = await prog.add_intervals([{"name": "X", "duration": 60, "speed": 3.0, "incline": 0}])
        assert ok is False


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_resets(self, loaded_prog):
        on_change = AsyncMock()
        on_update = AsyncMock()
        loaded_prog.running = True
        loaded_prog._on_change = on_change
        loaded_prog._on_update = on_update
        await loaded_prog.stop()
        assert loaded_prog.running is False
        on_change.assert_called_with(0, 0)

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, loaded_prog):
        on_change = AsyncMock()
        loaded_prog._on_change = on_change
        await loaded_prog.stop()
        on_change.assert_not_called()


class TestFullProgramExecution:
    """End-to-end: run a complete multi-interval program and verify
    every transition, the timer, and the final state — the automated
    version of the live observation test."""

    @pytest.mark.asyncio
    async def test_full_run_three_intervals(self):
        """Run a 3-interval program to completion, verify each interval
        sent the correct speed/incline and total elapsed is correct."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "Warmup", "duration": 5, "speed": 2.0, "incline": 0},
                    {"name": "Jog", "duration": 5, "speed": 4.0, "incline": 5},
                    {"name": "Cooldown", "duration": 5, "speed": 1.5, "incline": 0},
                ]
            )
        )

        on_change = AsyncMock()
        on_update = AsyncMock()

        # Record what on_change was called with at each tick
        change_log = []
        original_on_change = on_change

        async def tracking_on_change(speed, incline):
            change_log.append((speed, incline))

        # Let it run the full 15 ticks to completion
        tick_count = 0

        async def mock_sleep(duration):
            nonlocal tick_count
            tick_count += 1
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(tracking_on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # Verify final state
        assert prog.completed is True
        assert prog.running is False
        assert prog.total_elapsed == 15

        # Verify the sequence of on_change calls:
        # 1) start() fires initial interval: (2.0, 0)
        # 2) tick 5: transition to Jog: (4.0, 5)
        # 3) tick 10: transition to Cooldown: (1.5, 0)
        # 4) tick 15: program complete: (0, 0)
        assert change_log[0] == (2.0, 0), f"Warmup start: expected (2.0, 0), got {change_log[0]}"
        assert (4.0, 5) in change_log, "Jog transition (4.0, 5) not found"
        assert change_log[-1] == (0, 0), f"Final stop: expected (0, 0), got {change_log[-1]}"

        # Verify ordering: warmup before jog before cooldown before stop
        jog_idx = change_log.index((4.0, 5))
        cooldown_idx = change_log.index((1.5, 0))
        stop_idx = len(change_log) - 1
        assert 0 < jog_idx < cooldown_idx < stop_idx

    @pytest.mark.asyncio
    async def test_timer_accuracy_across_intervals(self):
        """Verify interval_elapsed resets at each transition and
        total_elapsed accumulates correctly."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 3, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 4, "speed": 5.0, "incline": 3},
                    {"name": "C", "duration": 3, "speed": 1.5, "incline": 0},
                ]
            )
        )

        on_change = AsyncMock()

        # Capture state at each tick
        snapshots = []

        async def on_update(state):
            snapshots.append(
                {
                    "interval": state["current_interval"],
                    "iv_elapsed": state["interval_elapsed"],
                    "total": state["total_elapsed"],
                    "running": state["running"],
                    "completed": state["completed"],
                }
            )

        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # Filter to running ticks only (exclude the initial broadcast from start)
        running = [s for s in snapshots if s["running"]]

        # Check interval_elapsed resets at transitions
        # After tick 3 of interval A, should transition to B with iv_elapsed=0
        transition_to_b = [s for s in running if s["interval"] == 1 and s["iv_elapsed"] == 0]
        assert len(transition_to_b) >= 1, "interval_elapsed should reset to 0 at transition to B"

        # After tick 7 (3+4), transition to C with iv_elapsed=0
        transition_to_c = [s for s in running if s["interval"] == 2 and s["iv_elapsed"] == 0]
        assert len(transition_to_c) >= 1, "interval_elapsed should reset to 0 at transition to C"

        # Total elapsed should reach 10 (3+4+3)
        assert prog.total_elapsed == 10

    @pytest.mark.asyncio
    async def test_speed_incline_sent_to_treadmill(self):
        """Simulate the server's on_change callback to verify the treadmill
        would receive correct speed/incline commands for each interval."""
        from unittest.mock import MagicMock

        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(
            make_program(
                [
                    {"name": "Walk", "duration": 3, "speed": 2.5, "incline": 2},
                    {"name": "Run", "duration": 3, "speed": 7.0, "incline": 8},
                    {"name": "Cool", "duration": 3, "speed": 1.0, "incline": 0},
                ]
            )
        )

        # Simulate what server.py does: _prog_on_change tracks commands
        treadmill_commands = []
        mock_client = MagicMock()
        mock_client.set_speed = MagicMock(side_effect=lambda mph: treadmill_commands.append(("speed", mph)))
        mock_client.set_incline = MagicMock(side_effect=lambda inc: treadmill_commands.append(("incline", inc)))

        async def on_change(speed, incline):
            mock_client.set_speed(speed)
            mock_client.set_incline(incline)

        on_update = AsyncMock()
        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # Extract the speed commands in order
        speeds = [v for cmd, v in treadmill_commands if cmd == "speed"]
        inclines = [v for cmd, v in treadmill_commands if cmd == "incline"]

        # Should be: Walk(2.5), Run(7.0), Cool(1.0), Stop(0)
        assert speeds == [2.5, 7.0, 1.0, 0], f"Speed sequence: {speeds}"
        assert inclines == [2, 8, 0, 0], f"Incline sequence: {inclines}"

    @pytest.mark.asyncio
    async def test_encouragement_fires_at_milestones(self):
        """Verify encouragement messages appear at 25/50/75% milestones."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        # 4 intervals of 10s each = 40s total
        prog.load(
            make_program(
                [
                    {"name": "A", "duration": 10, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 10, "speed": 3.0, "incline": 1},
                    {"name": "C", "duration": 10, "speed": 4.0, "incline": 2},
                    {"name": "D", "duration": 10, "speed": 2.0, "incline": 0},
                ]
            )
        )

        on_change = AsyncMock()
        encouragements = []

        async def on_update(state):
            e = state.get("encouragement")
            if e:
                encouragements.append((state["total_elapsed"], e))

        tick = 0

        async def mock_sleep(d):
            nonlocal tick
            tick += 1
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # Should have encouragements at ~25% (10s), ~50% (20s), ~75% (30s)
        assert (
            len(encouragements) >= 3
        ), f"Expected at least 3 encouragements, got {len(encouragements)}: {encouragements}"
        times = [t for t, _ in encouragements]
        assert any(t == 10 for t in times), f"Expected encouragement at 25% (t=10), got times: {times}"
        assert any(t == 20 for t in times), f"Expected encouragement at 50% (t=20), got times: {times}"
        assert any(t == 30 for t in times), f"Expected encouragement at 75% (t=30), got times: {times}"


class TestProperties:
    def test_total_duration(self, loaded_prog):
        assert loaded_prog.total_duration == 240  # 60 + 120 + 60

    def test_current_iv(self, loaded_prog):
        assert loaded_prog.current_iv["name"] == "Warmup"

    def test_to_dict_serialization(self, loaded_prog):
        d = loaded_prog.to_dict()
        assert d["type"] == "program"
        assert d["program"]["name"] == "Test Workout"
        assert d["running"] is False
        assert d["total_duration"] == 240


class TestEncouragement:
    """Test encouragement message system."""

    def test_milestone_messages_at_boundaries(self, loaded_prog):
        """Verify _check_encouragement sets messages at 25/50/75%."""
        loaded_prog.running = True
        td = loaded_prog.total_duration  # 240

        # At 25% (60s)
        loaded_prog.total_elapsed = 60
        loaded_prog._check_encouragement()
        assert loaded_prog._pending_encouragement is not None
        assert "quarter" in loaded_prog._pending_encouragement.lower() or "25" in str(
            loaded_prog._encouragement_milestones
        )
        loaded_prog._pending_encouragement = None

        # At 50% (120s)
        loaded_prog.total_elapsed = 120
        loaded_prog._check_encouragement()
        assert loaded_prog._pending_encouragement is not None
        loaded_prog._pending_encouragement = None

        # At 75% (180s)
        loaded_prog.total_elapsed = 180
        loaded_prog._check_encouragement()
        assert loaded_prog._pending_encouragement is not None

    def test_no_encouragement_when_not_running(self, loaded_prog):
        loaded_prog.running = False
        loaded_prog.total_elapsed = 120
        loaded_prog._check_encouragement()
        assert loaded_prog._pending_encouragement is None

    def test_encouragement_included_in_to_dict(self, loaded_prog):
        loaded_prog._pending_encouragement = "Test message"
        d = loaded_prog.to_dict()
        assert d["encouragement"] == "Test message"
        # to_dict() should NOT clear encouragement (read without consume)
        d2 = loaded_prog.to_dict()
        assert d2["encouragement"] == "Test message"
        # drain_encouragement() clears it
        loaded_prog.drain_encouragement()
        d3 = loaded_prog.to_dict()
        assert "encouragement" not in d3

    def test_encouragement_every_3_intervals(self):
        """Every 3 intervals should trigger encouragement."""
        prog = ProgramState()
        prog.load(make_program([{"name": f"I{i}", "duration": 10, "speed": 3.0, "incline": 0} for i in range(12)]))
        prog.running = True
        prog.total_elapsed = 30  # past 25% milestone

        # At interval 3, elapsed=0 should trigger
        prog.current_interval = 3
        prog.interval_elapsed = 0
        prog._encouragement_milestones = {25}  # already got 25%
        prog._check_encouragement()
        assert prog._pending_encouragement is not None


class TestValidateInterval:
    """Test interval validation and clamping."""

    def test_clamps_speed(self):
        from program_engine import validate_interval

        iv = {"speed": 99.0, "incline": 0, "duration": 60}
        validate_interval(iv)
        assert iv["speed"] == 12.0

    def test_clamps_speed_minimum(self):
        from program_engine import validate_interval

        iv = {"speed": -1.0, "incline": 0, "duration": 60}
        validate_interval(iv)
        assert iv["speed"] == 0.5

    def test_clamps_incline(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 99, "duration": 60}
        validate_interval(iv)
        assert iv["incline"] == 15

    def test_incline_float_half_step(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 5.5, "duration": 60}
        validate_interval(iv)
        assert iv["incline"] == 5.5

    def test_incline_snaps_to_half_step(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 5.3, "duration": 60}
        validate_interval(iv)
        assert iv["incline"] == 5.5

    def test_incline_snaps_down(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 5.2, "duration": 60}
        validate_interval(iv)
        assert iv["incline"] == 5.0

    def test_clamps_duration(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 0, "duration": 1}
        validate_interval(iv)
        assert iv["duration"] == 10

    def test_missing_field_raises(self):
        from program_engine import validate_interval

        with pytest.raises(ValueError, match="missing"):
            validate_interval({"speed": 3.0, "incline": 0})

    def test_adds_default_name(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 0, "duration": 60}
        validate_interval(iv)
        assert "name" in iv

    def test_index_in_name(self):
        from program_engine import validate_interval

        iv = {"speed": 3.0, "incline": 0, "duration": 60}
        validate_interval(iv, index=2)
        assert iv["name"] == "Interval 3"


class TestWallClockTiming:
    """Tests specific to the wall-clock timing fix (59:18 bug)."""

    @pytest.mark.asyncio
    async def test_10s_program_completes_at_10(self):
        """A 10-second single-interval program must end with total_elapsed == 10."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(make_program([{"name": "Test", "duration": 10, "speed": 3.0, "incline": 0}]))
        on_change = AsyncMock()
        on_update = AsyncMock()

        async def mock_sleep(d):
            clock.advance(1)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert prog.completed is True
        assert prog.total_elapsed == 10

    @pytest.mark.asyncio
    async def test_drift_resilience(self):
        """Simulate sleep overshoot (1.007s per tick) — wall clock still accurate."""
        prog = ProgramState()
        clock = FakeClock()
        prog._clock = clock
        prog.load(make_program([{"name": "Test", "duration": 10, "speed": 3.0, "incline": 0}]))
        on_change = AsyncMock()
        on_update = AsyncMock()

        async def mock_sleep(d):
            clock.advance(1.007)  # realistic overshoot

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await prog.start(on_change, on_update)
            if prog._task:
                try:
                    await asyncio.wait_for(prog._task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        assert prog.completed is True
        assert prog.total_elapsed == 10
