"""Shared test helpers for treadmill tests."""


def make_program(intervals=None, name="Test Workout"):
    """Factory for creating test programs."""
    if intervals is None:
        intervals = [
            {"name": "Warmup", "duration": 60, "speed": 2.0, "incline": 0},
            {"name": "Run", "duration": 120, "speed": 6.0, "incline": 3},
            {"name": "Cooldown", "duration": 60, "speed": 2.0, "incline": 0},
        ]
    return {"name": name, "intervals": intervals}


class FakeClock:
    """Fake monotonic clock for testing wall-clock timing in ProgramState.

    Install on a ProgramState with ``prog._clock = clock``.
    Advance by calling ``clock.advance(seconds)`` (typically from mock_sleep).
    """

    def __init__(self, start=0.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds
