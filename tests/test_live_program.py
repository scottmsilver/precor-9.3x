"""Live program execution tests — no mocks, real asyncio.sleep, real timers.

These tests actually run the program engine in real time with short intervals
and verify transitions happen when they should. Takes ~15-20s to run.
"""

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from program_engine import ProgramState


class TestLiveFullExecution:
    """Run real programs with real sleep and verify everything."""

    @pytest.mark.asyncio
    async def test_three_interval_program_runs_to_completion(self):
        """Run a 3-interval program (3s each = 9s total) in real time.
        Verify each interval fires the correct speed/incline and the
        program completes with final (0,0)."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Live Test",
                "intervals": [
                    {"name": "Walk", "duration": 3, "speed": 2.0, "incline": 1},
                    {"name": "Run", "duration": 3, "speed": 6.0, "incline": 5},
                    {"name": "Cool", "duration": 3, "speed": 1.5, "incline": 0},
                ],
            }
        )

        change_log = []

        async def on_change(speed, incline):
            change_log.append((time.monotonic(), speed, incline))

        broadcast_count = 0

        async def on_update(state):
            nonlocal broadcast_count
            broadcast_count += 1

        t0 = time.monotonic()
        await prog.start(on_change, on_update)

        # Wait for program to finish (9s + margin)
        for _ in range(130):  # 13s max
            await asyncio.sleep(0.1)
            if prog.completed:
                break

        elapsed_wall = time.monotonic() - t0

        # --- Assertions ---
        assert prog.completed is True, "Program should have completed"
        assert prog.running is False

        # Total elapsed should be 9 (3+3+3)
        assert prog.total_elapsed == 9, f"Expected 9s total, got {prog.total_elapsed}"

        # Wall clock should be ~9s (allow 1s tolerance for scheduling)
        assert 8.0 < elapsed_wall < 12.0, f"Wall clock {elapsed_wall:.1f}s not near 9s"

        # on_change should have fired 4 times:
        #   Walk start (2.0, 1), Run transition (6.0, 5),
        #   Cool transition (1.5, 0), finish (0, 0)
        speeds = [s for _, s, _ in change_log]
        inclines = [i for _, _, i in change_log]
        assert speeds == [2.0, 6.0, 1.5, 0], f"Speed sequence: {speeds}"
        assert inclines == [1, 5, 0, 0], f"Incline sequence: {inclines}"

        # Transitions should happen ~3s apart
        times = [t - t0 for t, _, _ in change_log]
        assert times[0] < 0.5, f"Walk should start immediately, started at {times[0]:.1f}s"
        assert 2.5 < times[1] < 4.5, f"Run transition at {times[1]:.1f}s, expected ~3s"
        assert 5.5 < times[2] < 7.5, f"Cool transition at {times[2]:.1f}s, expected ~6s"
        assert 8.0 < times[3] < 11.0, f"Finish at {times[3]:.1f}s, expected ~9s"

        # Should have broadcast updates every tick
        assert broadcast_count >= 9, f"Expected >= 9 broadcasts, got {broadcast_count}"

    @pytest.mark.asyncio
    async def test_timer_counts_correctly(self):
        """Run a 5s single interval, sample total_elapsed each second,
        verify it counts 1, 2, 3, 4, 5."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Timer Test",
                "intervals": [
                    {"name": "Steady", "duration": 5, "speed": 3.0, "incline": 2},
                ],
            }
        )

        elapsed_samples = []

        async def on_change(speed, incline):
            pass

        async def on_update(state):
            pass

        await prog.start(on_change, on_update)

        # Sample every 0.5s for 7s
        for _ in range(14):
            await asyncio.sleep(0.5)
            elapsed_samples.append(prog.total_elapsed)
            if prog.completed:
                break

        assert prog.completed is True
        assert prog.total_elapsed == 5

        # Elapsed should monotonically increase to 5
        assert elapsed_samples[-1] == 5
        # Should have seen 1, 2, 3, 4, 5 at some point
        for expected in [1, 2, 3, 4, 5]:
            assert expected in elapsed_samples, f"Never saw total_elapsed={expected}"

    @pytest.mark.asyncio
    async def test_pause_freezes_timer(self):
        """Start a program, pause after 2s, wait 3s, resume, verify
        the timer only counted the unpaused time."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Pause Test",
                "intervals": [
                    {"name": "Go", "duration": 5, "speed": 4.0, "incline": 0},
                ],
            }
        )

        async def on_change(speed, incline):
            pass

        async def on_update(state):
            pass

        await prog.start(on_change, on_update)

        # Let it run 2 seconds
        await asyncio.sleep(2.5)
        elapsed_before_pause = prog.total_elapsed
        assert elapsed_before_pause >= 2, f"Should have ticked at least 2s, got {elapsed_before_pause}"

        # Pause
        await prog.toggle_pause()
        assert prog.paused is True

        # Wait 3 seconds while paused
        await asyncio.sleep(3.0)
        elapsed_while_paused = prog.total_elapsed
        assert (
            elapsed_while_paused == elapsed_before_pause
        ), f"Timer should freeze during pause: was {elapsed_before_pause}, now {elapsed_while_paused}"

        # Resume
        await prog.toggle_pause()
        assert prog.paused is False

        # Wait for completion (need remaining ~3s + margin)
        for _ in range(50):
            await asyncio.sleep(0.1)
            if prog.completed:
                break

        assert prog.completed is True
        assert prog.total_elapsed == 5, f"Total should be 5s, got {prog.total_elapsed}"

    @pytest.mark.asyncio
    async def test_skip_advances_to_next_interval_live(self):
        """Start a 3-interval program, skip the first after 1s,
        verify we're now on interval 2 with the right speed."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Skip Test",
                "intervals": [
                    {"name": "A", "duration": 60, "speed": 2.0, "incline": 0},
                    {"name": "B", "duration": 3, "speed": 5.0, "incline": 3},
                    {"name": "C", "duration": 3, "speed": 1.0, "incline": 0},
                ],
            }
        )

        change_log = []

        async def on_change(speed, incline):
            change_log.append((speed, incline))

        async def on_update(state):
            pass

        await prog.start(on_change, on_update)
        await asyncio.sleep(1.5)

        assert prog.current_interval == 0
        assert change_log[-1] == (2.0, 0)

        # Skip to B
        await prog.skip()
        assert prog.current_interval == 1
        assert prog.interval_elapsed == 0
        assert change_log[-1] == (5.0, 3)

        # Let B and C complete naturally (6s + margin)
        for _ in range(80):
            await asyncio.sleep(0.1)
            if prog.completed:
                break

        assert prog.completed is True
        # Final command should be (0, 0)
        assert change_log[-1] == (0, 0)

    @pytest.mark.asyncio
    async def test_extend_adds_real_time(self):
        """Start a 10s interval, extend by 3s after 2s, verify it runs for ~13s.
        Uses 10s base because extend_current clamps minimum duration to 10."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Extend Test",
                "intervals": [
                    {"name": "Only", "duration": 10, "speed": 3.0, "incline": 0},
                ],
            }
        )

        async def on_change(speed, incline):
            pass

        async def on_update(state):
            pass

        t0 = time.monotonic()
        await prog.start(on_change, on_update)

        # After 2s, extend by 3s (duration 10 -> 13)
        await asyncio.sleep(2.5)
        assert prog.running is True, "Program should still be running at 2.5s"
        ok = await prog.extend_current(3)
        assert ok is True
        assert prog.current_iv["duration"] == 13

        # Wait for completion
        for _ in range(150):
            await asyncio.sleep(0.1)
            if prog.completed:
                break

        wall = time.monotonic() - t0

        assert prog.completed is True
        assert prog.total_elapsed == 13
        assert 12.0 < wall < 15.0, f"Wall clock {wall:.1f}s, expected ~13s"

    @pytest.mark.asyncio
    async def test_stop_halts_immediately(self):
        """Start a 60s program, stop after 2s, verify it halts."""
        prog = ProgramState()
        prog.load(
            {
                "name": "Stop Test",
                "intervals": [
                    {"name": "Long", "duration": 60, "speed": 5.0, "incline": 3},
                ],
            }
        )

        stopped_at = []

        async def on_change(speed, incline):
            if speed == 0 and incline == 0:
                stopped_at.append(prog.total_elapsed)

        async def on_update(state):
            pass

        await prog.start(on_change, on_update)
        await asyncio.sleep(2.5)

        assert prog.running is True
        elapsed_before = prog.total_elapsed
        assert elapsed_before >= 2

        await prog.stop()

        assert prog.running is False
        assert len(stopped_at) == 1
        assert stopped_at[0] == elapsed_before  # stopped right where we were

        # Timer should not advance further
        await asyncio.sleep(1.0)
        assert prog.total_elapsed == elapsed_before
