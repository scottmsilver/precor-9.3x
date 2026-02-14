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
