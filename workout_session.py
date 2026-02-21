"""
WorkoutSession — owns workout session + program lifecycle.

Invariant: a program can only run within an active session.
All program starts go through start_program().

This module has NO dependencies on server.py, FastAPI, or treadmill I/O.
Callbacks (on_change, on_update) are passed in by the caller.
"""

import logging
import time

from program_engine import ProgramState

log = logging.getLogger("treadmill")


class WorkoutSession:
    """Manages workout session + program lifecycle.

    Invariant: a program can only run within an active session.
    All program starts go through start_program().
    """

    def __init__(self):
        self.prog = ProgramState()
        self.active = False
        self.started_at = 0.0
        self.wall_started_at = ""
        self.paused_at = 0.0
        self.total_paused = 0.0
        self.elapsed = 0.0
        self.distance = 0.0
        self.vert_feet = 0.0
        self.last_tick = 0.0
        self.end_reason = None

    def start(self):
        """Begin session. Idempotent if already active."""
        if self.active:
            self.paused_at = 0.0
            return
        self.active = True
        self.started_at = time.monotonic()
        self.wall_started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.paused_at = 0.0
        self.total_paused = 0.0
        self.elapsed = 0.0
        self.distance = 0.0
        self.vert_feet = 0.0
        self.last_tick = time.monotonic()
        self.end_reason = None
        log.info("Session started")

    async def start_program(self, on_change, on_update):
        """Start loaded program within a session. Ensures session active."""
        self.start()
        await self.prog.start(on_change, on_update)

    async def ensure_manual(self, speed=3.0, incline=0, duration_minutes=60, *, on_change, on_update):
        """Auto-create manual program if none running, start it."""
        if self.prog.running:
            return
        program = {
            "name": f"{duration_minutes}-Min Manual",
            "manual": True,
            "intervals": [{"name": "Seg 1", "duration": duration_minutes * 60, "speed": speed, "incline": incline}],
        }
        self.prog.load(program)
        await self.start_program(on_change, on_update)

    def end(self, reason):
        """End the current session with a reason."""
        if not self.active:
            return
        self.tick(0, 0)  # final elapsed update
        self.active = False
        self.end_reason = reason
        log.info(f"Session ended: {reason}")

    def pause(self):
        """Pause session timer."""
        if self.active and self.paused_at == 0:
            self.paused_at = time.monotonic()

    def resume(self):
        """Resume session timer."""
        if self.active and self.paused_at > 0:
            self.total_paused += time.monotonic() - self.paused_at
            self.paused_at = 0.0

    async def reset(self):
        """Full reset: stop program, zero session."""
        await self.prog.reset()
        self.active = False
        self.started_at = 0.0
        self.wall_started_at = ""
        self.paused_at = 0.0
        self.total_paused = 0.0
        self.elapsed = 0.0
        self.distance = 0.0
        self.vert_feet = 0.0
        self.last_tick = 0.0
        self.end_reason = None
        log.info("Session reset")

    def tick(self, speed_mph, incline):
        """Compute elapsed/distance/vert from monotonic clock and current speed/incline."""
        if not self.active or self.paused_at > 0:
            return
        now = time.monotonic()
        self.elapsed = max(0.0, now - self.started_at - self.total_paused)
        dt = now - self.last_tick if self.last_tick > 0 else 1.0
        self.last_tick = now
        if speed_mph > 0:
            miles_this_tick = (speed_mph / 3600) * dt
            self.distance += miles_this_tick
            if incline > 0:
                self.vert_feet += miles_this_tick * (incline / 100) * 5280

    def to_dict(self):
        """Build session state dict for WebSocket broadcast."""
        return {
            "type": "session",
            "active": self.active,
            "elapsed": self.elapsed,
            "distance": self.distance,
            "vert_feet": self.vert_feet,
            "wall_started_at": self.wall_started_at,
            "end_reason": self.end_reason,
        }
