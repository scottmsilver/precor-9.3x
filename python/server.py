#!/usr/bin/env python3
"""
Treadmill Web Server — FastAPI + WebSocket bridge to treadmill_io.

Connects to the treadmill_io C binary via Unix socket for GPIO I/O,
and serves a web UI for monitoring and control.

Usage:
    sudo ./treadmill_io    # start C binary first
    python3 server.py
    # Open http://<pi-ip>:8000 on phone
"""

import asyncio
import base64
import datetime
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from contextlib import asynccontextmanager

import program_engine
import uvicorn
from db import DEFAULT_WEIGHT_LBS, GUEST_PROFILE_ID, TreadmillDB  # noqa: F401
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from hrm_client import HrmClient
from program_engine import (
    CHAT_SYSTEM_PROMPT,
    GEMINI_MODEL,
    SMARTASS_ADDENDUM,
    TOOL_DECLARATIONS,
    TTS_MODEL,
    build_tts_config,
    call_gemini,
    extract_intent_from_text,
    generate_program,
    get_client,
    read_api_key,
    validate_interval,
)
from pydantic import BaseModel, Field, field_validator
from treadmill_client import MAX_SPEED_TENTHS, TreadmillClient
from workout_db import WorkoutDB
from workout_session import WorkoutSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("treadmill")

# Server-side dirty guard — when the API sets emu_speed or emu_incline,
# we record a timestamp. The on_status callback from the C binary will
# skip overwriting these fields while the guard is active, so that the
# UI sees the *target* value (what the user requested), not the motor's
# in-progress position (which lags behind during physical movement).
_dirty_speed_until = 0.0
_dirty_incline_until = 0.0
_DIRTY_GRACE_SEC = 15.0  # motor can take 10+ seconds to reach target incline

# --- Database & profile globals ---
db: TreadmillDB = None
workout_db: WorkoutDB = None
_guest_mode = False


def _active_profile_id():
    """Return the active profile ID, or GUEST_PROFILE_ID in guest mode."""
    if _guest_mode or db is None:
        return GUEST_PROFILE_ID
    pid = db.get_active_profile_id()
    return pid if pid else GUEST_PROFILE_ID


def _user_weight_kg():
    """Get total weight in kg for calorie calculations (body + vest)."""
    if db is None:
        return DEFAULT_WEIGHT_LBS * 0.453592
    p = db.get_profile(_active_profile_id())
    if not p:
        return DEFAULT_WEIGHT_LBS * 0.453592
    total_lbs = (p.get("weight_lbs") or DEFAULT_WEIGHT_LBS) + (p.get("vest_lbs") or 0)
    return total_lbs * 0.453592


def _sync_workout_db():
    """Sync the read-only WorkoutDB with current profile data."""
    if workout_db:
        workout_db.sync(active_program=sess.prog.program if sess else None)


async def _broadcast_profile_changed():
    """Broadcast status after profile switch so all clients update."""
    await manager.broadcast(build_status())


@asynccontextmanager
async def lifespan(application):
    global loop, msg_queue, client, hrm, sess, db, workout_db

    loop = asyncio.get_event_loop()
    msg_queue = asyncio.Queue(maxsize=500)
    sess = WorkoutSession()

    # Init database
    db_path = os.environ.get("TREADMILL_DB", "treadmill.db")
    db = TreadmillDB(db_path)

    # Migrate from JSON files if this is first run (no real profiles yet)
    if db.profile_count() == 0 and any(
        os.path.exists(f)
        for f in ["program_history.json", "saved_workouts.json", "run_history.json", "user_profile.json"]
    ):
        import socket

        hostname = socket.gethostname()
        profile_name = hostname.capitalize() if hostname and hostname != "localhost" else "Owner"
        owner = db.create_profile(profile_name)
        db.set_active_profile_id(owner["id"])
        db.migrate_from_json(
            owner["id"],
            history_file="program_history.json",
            workouts_file="saved_workouts.json",
            runs_file="run_history.json",
            user_file="user_profile.json",
        )

    # Init workout query DB with dynamic lambdas using active profile
    workout_db = WorkoutDB(
        history_loader=lambda: db.get_program_history(_active_profile_id()),
        workouts_loader=lambda: db.get_saved_workouts(_active_profile_id()),
        runs_loader=lambda: db.get_runs(_active_profile_id()),
        fingerprint_fn=_program_fingerprint,
    )

    # Connect to treadmill_io C binary (or mock for UI-only dev)
    mock_mode = os.environ.get("TREADMILL_MOCK")
    if mock_mode:
        from mock_treadmill_client import MockTreadmillClient

        client = MockTreadmillClient()
        log.info("Mock mode — no Pi connection")
    else:
        sock = os.environ.get("TREADMILL_SOCK", "/tmp/treadmill_io.sock")
        client = TreadmillClient(sock_path=sock)

    def on_message(msg):
        def _apply():
            msg_type = msg.get("type")
            if msg_type == "kv":
                source = msg.get("source", "")
                key = msg.get("key", "")
                value = msg.get("value", "")
                if source == "motor":
                    latest["last_motor"][key] = value
                elif source in ("console", "emulate"):
                    latest["last_console"][key] = value
                _enqueue(msg)
            elif msg_type == "status":
                was_emulating = state["emulate"]
                state["proxy"] = msg.get("proxy", False)
                state["emulate"] = msg.get("emulate", False)
                # Only accept C binary's emu values if API hasn't set them recently
                now = time.monotonic()
                if now >= _dirty_speed_until:
                    state["emu_speed"] = msg.get("emu_speed", 0)
                if now >= _dirty_incline_until:
                    state["emu_incline"] = msg.get("emu_incline", 0)
                # Bus values from C++ motor KV parsing
                bs = msg.get("bus_speed")
                state["bus_speed"] = bs if bs is not None and bs >= 0 else None
                bi = msg.get("bus_incline")
                state["bus_incline"] = bi if bi is not None and bi >= 0 else None
                # Detect watchdog / auto-proxy killing emulate while session active
                if was_emulating and not state["emulate"] and sess.active:
                    reason = "auto_proxy" if state["proxy"] else "watchdog"
                    _handle_auto_proxy(reason)
                _enqueue(build_status())

        loop.call_soon_threadsafe(_apply)

    client.on_message = on_message

    def on_disconnect():
        log.warning("treadmill_io disconnected")

        def _handle_disconnect():
            state["treadmill_connected"] = False
            if sess.prog.running and not sess.prog.paused:
                task = asyncio.ensure_future(sess.prog.toggle_pause())
                task.add_done_callback(
                    lambda t: log.error(f"Auto-pause on disconnect failed: {t.exception()}") if t.exception() else None
                )
            if sess.active:
                _save_run_record("disconnect")
                sess.end("disconnect")
                _enqueue(sess.to_dict())
            _enqueue({"type": "connection", "connected": False})

        loop.call_soon_threadsafe(_handle_disconnect)

    def on_reconnect():
        log.info("treadmill_io reconnected")

        def _apply():
            state["treadmill_connected"] = True
            _enqueue({"type": "connection", "connected": True})

        loop.call_soon_threadsafe(_apply)
        # Request fresh status from C binary (socket write has its own lock)
        try:
            client.request_status()
        except ConnectionError:
            pass

    client.on_disconnect = on_disconnect
    client.on_reconnect = on_reconnect

    try:
        client.connect()
        state["treadmill_connected"] = True
        log.info("Connected to treadmill_io")
    except Exception as e:
        log.error(f"Cannot connect to treadmill_io: {e}")
        raise RuntimeError("treadmill_io not running. Start: sudo ./treadmill_io")

    # Connect to hrm-daemon (optional — server works without it)
    if mock_mode:
        from mock_hrm_client import MockHrmClient

        hrm = MockHrmClient()
        log.info("Mock HRM active")
    else:
        hrm_sock = os.environ.get("HRM_SOCK", "/tmp/hrm.sock")
        hrm = HrmClient(sock_path=hrm_sock)

    def on_hrm_message(msg):
        def _apply():
            msg_type = msg.get("type")
            if msg_type == "hr":
                state["heart_rate"] = msg.get("bpm", 0)
                state["hrm_connected"] = msg.get("connected", False)
                state["hrm_device"] = msg.get("device", "")
            elif msg_type == "scan_result":
                state["hrm_devices"] = msg.get("devices", [])
            _enqueue(msg)

        loop.call_soon_threadsafe(_apply)

    hrm.on_message = on_hrm_message

    def on_hrm_disconnect():
        def _apply():
            state["hrm_connected"] = False
            state["heart_rate"] = 0
            state["hrm_device"] = ""
            state["hrm_devices"] = []
            _enqueue({"type": "hr", "bpm": 0, "connected": False, "device": ""})

        loop.call_soon_threadsafe(_apply)

    hrm.on_disconnect = on_hrm_disconnect

    try:
        hrm.connect()
        log.info("Connected to hrm-daemon")
    except Exception:
        log.info("hrm-daemon not available yet, will retry in background")
        hrm.ensure_connecting()

    broadcast_task = asyncio.create_task(broadcast_loop())
    session_tick_task = asyncio.create_task(_session_tick_loop())
    client.start_heartbeat()

    log.info("Server started — open http://<host>:8000 in browser")

    yield

    # Shutdown
    state["running"] = False
    broadcast_task.cancel()
    session_tick_task.cancel()
    client.stop_heartbeat()
    if sess.prog.running:
        await sess.prog.stop()
    if hrm:
        hrm.close()
    client.close()
    if db:
        db.close()
    log.info("Server stopped")


app = FastAPI(title="Treadmill Controller", lifespan=lifespan)

# CORS — wide open in mock mode (Caddy handles same-origin), restricted otherwise
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if os.environ.get("TREADMILL_MOCK") else ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Async bridge ---
loop: asyncio.AbstractEventLoop = None
msg_queue: asyncio.Queue = None
client: TreadmillClient = None
hrm: HrmClient = None
sess: WorkoutSession = None

# --- Shared state ---
state = {
    "running": True,
    "proxy": True,
    "emulate": False,
    "emu_speed": 0,  # tenths of mph
    "emu_incline": 0,
    "treadmill_connected": False,
    "heart_rate": 0,
    "hrm_connected": False,
    "hrm_device": "",
    "hrm_devices": [],
    "bus_speed": None,  # from C++ status: motor speed in tenths mph, None if unknown
    "bus_incline": None,  # from C++ status: motor incline in half-pct units, None if unknown
}

latest = {
    "last_console": {},
    "last_motor": {},
}

chat_history: list = []
_chat_lock = asyncio.Lock()  # serializes chat history mutations


def _add_to_history(program, prompt=""):
    """Add program to history via db, return entry dict."""
    entry = db.add_to_history(_active_profile_id(), program, prompt=prompt)
    _sync_workout_db()
    return entry


def _update_history_position(program_name, interval, elapsed, completed=False):
    """Update the history entry for a program with its last position."""
    history = db.get_program_history(_active_profile_id())
    for entry in history:
        if entry["program"].get("name") == program_name:
            db.update_history_entry(entry["id"], completed=completed, last_interval=interval, last_elapsed=elapsed)
            return


def _program_fingerprint(program):
    """Stable fingerprint from interval data, ignoring name."""
    intervals = program.get("intervals", [])
    return "|".join(f"{iv.get('speed', 0)},{iv.get('incline', 0)},{iv.get('duration', 0)}" for iv in intervals)


# --- Run records (db-backed) ---

_active_run_id = None  # ID of the in-progress run record, if any


def _build_run_record(reason="in_progress"):
    """Build a run record dict from current session state."""
    prog = sess.prog.program
    return {
        "id": _active_run_id or f"{time.time_ns()}",
        "started_at": sess.wall_started_at,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S") if reason != "in_progress" else None,
        "elapsed": round(sess.elapsed, 1),
        "distance": round(sess.distance, 3),
        "vert_feet": round(sess.vert_feet, 1),
        "calories": round(sess.calories, 1),
        "end_reason": reason,
        "program_name": prog.get("name") if prog else None,
        "program_fingerprint": _program_fingerprint(prog) if prog else None,
        "program_completed": sess.prog.completed,
        "is_manual": sess.prog.is_manual,
    }


def _start_run_record():
    """Create an in-progress run record when a session starts."""
    global _active_run_id
    if not sess.active or sess.elapsed < 5:
        return
    record = _build_run_record("in_progress")
    _active_run_id = db.insert_run(_active_profile_id(), record)


def _update_run_record():
    """Update the in-progress run record with current metrics. Called periodically."""
    if not _active_run_id or not sess.active:
        return
    db.update_run(
        _active_run_id,
        elapsed=round(sess.elapsed, 1),
        distance=round(sess.distance, 3),
        vert_feet=round(sess.vert_feet, 1),
        calories=round(sess.calories, 1),
    )


def _save_run_record(reason):
    """Finalize the run record when a session ends. Call before sess.end()."""
    global _active_run_id
    if not sess.active or sess.elapsed < 5:
        _active_run_id = None
        return
    record = _build_run_record(reason)
    if _active_run_id:
        db.update_run(
            _active_run_id,
            ended_at=record["ended_at"],
            elapsed=record["elapsed"],
            distance=record["distance"],
            vert_feet=record["vert_feet"],
            calories=record["calories"],
            end_reason=reason,
            program_completed=record["program_completed"],
        )
    else:
        db.insert_run(_active_profile_id(), record)
    _active_run_id = None
    _sync_workout_db()


def _last_run_by_fingerprint():
    """Build a lookup of most recent run per program fingerprint."""
    runs = db.get_runs(_active_profile_id())
    by_fp = {}
    for r in runs:
        fp = r.get("program_fingerprint")
        if fp and fp not in by_fp:
            by_fp[fp] = r  # runs are newest-first, so first seen wins
    return by_fp


def _relative_time(date_str):
    """Relative time string like '2d ago', '3h ago'."""
    if not date_str:
        return ""
    try:
        then = datetime.datetime.fromisoformat(date_str)
        now = datetime.datetime.now()
        diff = now - then
        mins = int(diff.total_seconds() / 60)
    except (ValueError, TypeError):
        return ""
    if mins < 1:  # also handles negative (future dates from clock skew)
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    return f"{months}mo ago"


def _fmt_dur(secs):
    """Format seconds as m:ss or h:mm:ss."""
    s = max(0, int(secs or 0))
    m = s // 60
    sec = s % 60
    if m >= 60:
        h = m // 60
        return f"{h}:{(m % 60):02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _last_run_text(run):
    """Format a run record as 'Last run: 2d ago \u00b7 24:30 \u00b7 1.2 mi'."""
    if not run:
        return ""
    when = _relative_time(run.get("ended_at"))
    dist = f"{run.get('distance', 0):.2f} mi" if run.get("distance", 0) >= 0.01 else ""
    dur = _fmt_dur(run.get("elapsed", 0))
    parts = [p for p in (when, dur, dist) if p]
    sep = " \u00b7 "
    return f"Last run: {sep.join(parts)}" if parts else ""


def _usage_text(workout, run):
    """Compute usage text for a saved workout."""
    run_text = _last_run_text(run)
    times = workout.get("times_used", 0)
    if run_text:
        text = run_text
        if times > 1:
            text += f" \u00b7 {times} runs total"
        return text
    if times > 0:
        last = _relative_time(workout.get("last_used"))
        suffix = f" \u00b7 last {last}" if last else ""
        return f"Used {times} time{'s' if times != 1 else ''}{suffix}"
    return "Never used"


def _validate_program(program):
    """Check that a program dict has valid structure. Returns error string or None."""
    if not isinstance(program, dict):
        return "program must be a dict"
    intervals = program.get("intervals")
    if not isinstance(intervals, list):
        return "program must have an intervals list"
    for i, iv in enumerate(intervals):
        if not isinstance(iv, dict):
            return f"interval {i} must be a dict"
        if "duration" not in iv or not isinstance(iv["duration"], (int, float)):
            return f"interval {i} must have a numeric duration"
    return None


def _save_workout(program, source="generated", prompt=""):
    """Save a workout to the active profile. Returns None in guest mode."""
    if _guest_mode:
        return None
    entry = db.save_workout(_active_profile_id(), program, source=source, prompt=prompt)
    _sync_workout_db()
    return entry


def _enqueue(msg):
    try:
        msg_queue.put_nowait(msg)
    except asyncio.QueueFull:
        try:
            msg_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            msg_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def push_msg(msg):
    if loop and msg_queue:
        loop.call_soon_threadsafe(_enqueue, msg)


def _handle_auto_proxy(reason="auto_proxy"):
    """Handle emulate→proxy transition: pause program + session, send bounce message.

    Called from on_message when C++ auto-switches from emulate to proxy
    (hardware stop button) or when watchdog kills emulate mode.
    Pauses instead of ending so the user can resume where they left off.
    """
    if not sess.active:
        return
    # Pause program if running (not already paused).
    # Set paused directly + record pause_start (no async needed — we're in
    # a synchronous callback and don't want to re-apply speed/incline since
    # emulate is already off).
    if sess.prog.running and not sess.prog.paused:
        sess.prog.paused = True
        sess.prog._pause_start = sess.prog._clock()
    # Pause session timer (not end)
    sess.pause()
    # Send bounce message via program encouragement
    msg = "Console took over — paused" if reason == "auto_proxy" else "Belt stopped — heartbeat lost"
    sess.prog._pending_encouragement = msg
    _enqueue(sess.prog.to_dict())
    sess.prog.drain_encouragement()
    log.info(f"Auto-paused session: {reason}")


_run_save_counter = 0
_RUN_SAVE_INTERVAL = 30  # save every 30 seconds


async def _session_tick_loop():
    """1/sec loop: compute session metrics and broadcast to all WS clients."""
    global _run_save_counter
    while state["running"]:
        if sess.active:
            sess.tick(state["emu_speed"] / 10, state["emu_incline"] / 2.0, _user_weight_kg())
            await manager.broadcast(sess.to_dict())
            # Periodic run record save + workout_db active program sync
            _run_save_counter += 1
            if _run_save_counter >= _RUN_SAVE_INTERVAL:
                _run_save_counter = 0
                if not _active_run_id and sess.elapsed >= 5:
                    _start_run_record()
                elif _active_run_id:
                    _update_run_record()
                # Keep workout_db's active program current (handles mid-run
                # mutations like split_for_manual, extend, add_time)
                workout_db.sync(active_program=sess.prog.program)
        await asyncio.sleep(1)


# --- WebSocket manager ---


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, msg: dict):
        data = json.dumps(msg)
        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def build_status():
    emu_mph = state["emu_speed"] / 10

    # Decode live speed: prefer bus_speed from C++ status (negative = unknown)
    speed = None
    if state["bus_speed"] is not None and state["bus_speed"] >= 0:
        speed = state["bus_speed"] / 10.0
    else:
        # KV fallback (hex hundredths -> mph)
        hmph = latest["last_motor"].get("hmph")
        if hmph:
            try:
                speed = int(hmph, 16) / 100
            except ValueError:
                pass

    # Decode live incline: prefer bus_incline from C++ status (negative = unknown)
    # bus_incline is in half-percent units from C++
    incline = None
    if state["bus_incline"] is not None and state["bus_incline"] >= 0:
        incline = state["bus_incline"] / 2.0
    else:
        # KV fallback (hex half-percent -> percent)
        inc = latest["last_motor"].get("inc")
        if inc:
            try:
                incline = int(inc, 16) / 2.0
            except ValueError:
                pass
    result = {
        "type": "status",
        "proxy": state["proxy"],
        "emulate": state["emulate"],
        "emu_speed": state["emu_speed"],
        "emu_speed_mph": emu_mph,
        "emu_incline": state["emu_incline"] / 2.0,
        "speed": speed,
        "incline": incline,
        "motor": latest["last_motor"],
        "treadmill_connected": state["treadmill_connected"],
        "heart_rate": state["heart_rate"],
        "hrm_connected": state["hrm_connected"],
        "hrm_device": state["hrm_device"],
        "guest_mode": _guest_mode,
    }
    if db:
        pid = _active_profile_id()
        p = db.get_profile(pid)
        if p:
            result["active_profile"] = p
    return result


async def broadcast_status():
    await manager.broadcast(build_status())


async def broadcast_loop():
    while state["running"]:
        try:
            msg = await asyncio.wait_for(msg_queue.get(), timeout=0.5)
            await manager.broadcast(msg)
        except asyncio.TimeoutError:
            pass
        except Exception:
            log.exception("broadcast_loop error")
            await asyncio.sleep(0.1)


# --- Pydantic models ---


class SpeedRequest(BaseModel):
    value: float  # mph


class InclineRequest(BaseModel):
    value: float


class EmulateRequest(BaseModel):
    enabled: bool


class ProxyRequest(BaseModel):
    enabled: bool


class BackgroundAdviseRequest(BaseModel):
    image_hash: str = Field(max_length=128)
    image_b64: str | None = Field(default=None, max_length=8_000_000)


class GenerateRequest(BaseModel):
    prompt: str = Field(max_length=5000)


class ChatRequest(BaseModel):
    message: str = Field(max_length=10000)
    smartass: bool = False


class VoiceChatRequest(BaseModel):
    audio: str = Field(max_length=15_000_000)  # ~10MB decoded
    mime_type: str = "audio/webm"
    smartass: bool = False


class TTSRequest(BaseModel):
    text: str = Field(max_length=5000)
    voice: str = "Kore"


class SaveWorkoutRequest(BaseModel):
    history_id: str | None = Field(default=None, max_length=50)
    program: dict | None = None
    source: str = "generated"
    prompt: str = Field(default="", max_length=5000)

    @field_validator("source")
    @classmethod
    def valid_source(cls, v):
        if v not in ("generated", "gpx", "manual"):
            raise ValueError("source must be generated, gpx, or manual")
        return v


class RenameWorkoutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# --- Async wrappers for blocking treadmill_client calls ---
# TreadmillClient uses synchronous socket.sendall() — these wrappers
# prevent blocking the FastAPI event loop.


async def _hw_set_speed(mph):
    try:
        await asyncio.to_thread(client.set_speed, mph)
    except ConnectionError:
        log.warning("Cannot set speed: treadmill_io disconnected")


async def _hw_set_incline(val):
    try:
        await asyncio.to_thread(client.set_incline, val)
    except ConnectionError:
        log.warning("Cannot set incline: treadmill_io disconnected")


async def _hw_set_emulate(enabled):
    try:
        await asyncio.to_thread(client.set_emulate, enabled)
    except ConnectionError:
        log.warning("Cannot set emulate: treadmill_io disconnected")


async def _hw_set_proxy(enabled):
    try:
        await asyncio.to_thread(client.set_proxy, enabled)
    except ConnectionError:
        log.warning("Cannot set proxy: treadmill_io disconnected")


# --- Shared control helpers ---


async def _apply_speed(mph):
    """Core speed logic shared by REST endpoint and Gemini function calls."""
    global _dirty_speed_until
    _dirty_speed_until = time.monotonic() + _DIRTY_GRACE_SEC
    state["emu_speed"] = max(0, min(int(mph * 10), MAX_SPEED_TENTHS))
    # Mirror C binary's auto-emulate: sending a speed command enables emulate mode
    if mph > 0:
        state["emulate"] = True
        state["proxy"] = False
        # Every workout has a program — auto-create manual if none running
        await sess.ensure_manual(
            speed=mph, incline=state["emu_incline"] / 2.0, on_change=_prog_on_change(), on_update=_prog_on_update()
        )
    elif mph == 0 and sess.active:
        if sess.prog.running:
            await sess.prog.stop()
        _save_run_record("user_stop")
        sess.end("user_stop")
        await manager.broadcast(sess.to_dict())
    # Split manual program interval to record course
    if sess.prog.is_manual and sess.prog.running and mph > 0:
        await sess.prog.split_for_manual(mph, state["emu_incline"] / 2.0)
    await _hw_set_speed(mph)
    await broadcast_status()


MAX_SAFE_INCLINE = 15  # Application-layer limit (hardware allows 0-99)


async def _apply_incline(inc):
    """Core incline logic shared by REST endpoint and Gemini function calls.

    inc: float percent (0-15, resolution 0.5)
    Internally stores half-pct units in state["emu_incline"].
    """
    global _dirty_incline_until
    _dirty_incline_until = time.monotonic() + _DIRTY_GRACE_SEC
    # Clamp to safe range and snap to 0.5% steps
    clamped = max(0.0, min(float(inc), MAX_SAFE_INCLINE))
    clamped = round(clamped * 2) / 2  # snap to 0.5 steps
    # Store as half-pct units (what C++ now uses internally)
    state["emu_incline"] = int(clamped * 2)
    # Mirror C binary's auto-emulate: sending an incline command enables emulate mode
    if inc > 0:
        state["emulate"] = True
        state["proxy"] = False
    # Split manual program interval to record course
    if sess.prog.is_manual and sess.prog.running:
        await sess.prog.split_for_manual(state["emu_speed"] / 10, clamped)
    # Send float percent to C++
    await _hw_set_incline(clamped)
    await broadcast_status()


async def _apply_stop():
    """Core stop logic shared by REST endpoint and Gemini function calls."""
    # Save position to history before stopping (for resume)
    if sess.prog.running and sess.prog.program:
        _update_history_position(
            sess.prog.program.get("name", ""),
            sess.prog.current_interval,
            sess.prog.total_elapsed,
        )
    if sess.prog.running:
        await sess.prog.stop()
    state["emu_speed"] = 0
    state["emu_incline"] = 0
    if sess.active:
        _save_run_record("user_stop")
        sess.end("user_stop")
        await manager.broadcast(sess.to_dict())
    await _hw_set_speed(0)
    await _hw_set_incline(0)
    await broadcast_status()


async def _apply_pause_toggle():
    """Shared pause/resume toggle logic. Returns 'paused' or 'resumed'."""
    await sess.prog.toggle_pause()
    if sess.prog.paused:
        sess.pause()
        state["_paused_speed"] = state["emu_speed"]
        state["emu_speed"] = 0
        await _hw_set_speed(0)
        await broadcast_status()
        return "paused"
    else:
        # Resume: on_change callback restores belt speed
        sess.resume()
        await broadcast_status()
        return "resumed"


# --- REST endpoints ---


@app.get("/api/status")
async def get_status():
    return build_status()


@app.get("/api/session")
async def get_session():
    return sess.to_dict()


GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"

# Voice prompts — injected into Gemini Live session as user text turns
VOICE_PROMPTS = {
    "custom-workout": (
        "The user wants to create a custom workout program. "
        "Greet them warmly and ask what kind of workout they'd like — "
        "duration, intensity, hills, intervals, etc. "
        "Then use start_workout to create it."
    ),
}


@app.get("/api/voice/prompt/{prompt_id}")
async def get_voice_prompt(prompt_id: str):
    """Return a voice prompt by ID for injection into Gemini Live sessions."""
    text = VOICE_PROMPTS.get(prompt_id)
    if text is None:
        return JSONResponse({"error": "unknown prompt"}, status_code=404)
    return {"prompt": text}


def _create_ephemeral_token() -> str | None:
    """Create a short-lived Gemini API token for client-side Live sessions."""
    import datetime

    api_key = read_api_key()
    if not api_key:
        return None
    try:
        auth_client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1alpha"},
        )
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        token = auth_client.auth_tokens.create(
            config={
                "uses": 5,
                "expire_time": now + datetime.timedelta(minutes=30),
                "new_session_expire_time": now + datetime.timedelta(minutes=2),
                "http_options": {"api_version": "v1alpha"},
            }
        )
        return token.name
    except Exception:
        log.exception("Failed to create ephemeral token")
        return None


# --- User Profile API (proxies to active profile) ---


@app.get("/api/user")
async def api_get_user():
    """Return active profile as user dict for backward compat."""
    p = db.get_profile(_active_profile_id()) if db else None
    if p:
        return {"id": p["id"], "weight_lbs": p.get("weight_lbs", DEFAULT_WEIGHT_LBS), "vest_lbs": p.get("vest_lbs", 0)}
    return {"id": "1", "weight_lbs": DEFAULT_WEIGHT_LBS, "vest_lbs": 0}


class UpdateUserRequest(BaseModel):
    weight_lbs: int | None = Field(None, ge=0, le=500)
    vest_lbs: int | None = Field(None, ge=0, le=100)


@app.put("/api/user")
async def api_update_user(req: UpdateUserRequest):
    """Update active profile weight/vest."""
    pid = _active_profile_id()
    kwargs = {}
    if req.weight_lbs is not None:
        kwargs["weight_lbs"] = req.weight_lbs
    if req.vest_lbs is not None:
        kwargs["vest_lbs"] = req.vest_lbs
    if kwargs and db:
        db.update_profile(pid, **kwargs)
    p = db.get_profile(pid) if db else None
    if p:
        return {"id": p["id"], "weight_lbs": p.get("weight_lbs", DEFAULT_WEIGHT_LBS), "vest_lbs": p.get("vest_lbs", 0)}
    return {"id": "1", "weight_lbs": DEFAULT_WEIGHT_LBS, "vest_lbs": 0}


# --- Profile Management API ---


class CreateProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = "#4A90D9"
    weight_lbs: int = Field(default=DEFAULT_WEIGHT_LBS, ge=0, le=500)
    vest_lbs: int = Field(default=0, ge=0, le=100)


@app.get("/api/profiles")
async def api_list_profiles():
    return db.get_profiles() if db else []


@app.post("/api/profiles")
async def api_create_profile(req: CreateProfileRequest):
    p = db.create_profile(req.name, color=req.color, weight_lbs=req.weight_lbs, vest_lbs=req.vest_lbs)
    return {"ok": True, "profile": p}


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    color: str | None = None
    weight_lbs: int | None = Field(None, ge=0, le=500)
    vest_lbs: int | None = Field(None, ge=0, le=100)


@app.put("/api/profiles/{profile_id}")
async def api_update_profile(profile_id: str, req: UpdateProfileRequest):
    if not db.get_profile(profile_id):
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    kwargs = {}
    if req.name is not None:
        kwargs["name"] = req.name
        kwargs["initials"] = req.name[0].upper() if req.name else "?"
    if req.color is not None:
        kwargs["color"] = req.color
    if req.weight_lbs is not None:
        kwargs["weight_lbs"] = req.weight_lbs
    if req.vest_lbs is not None:
        kwargs["vest_lbs"] = req.vest_lbs
    p = db.update_profile(profile_id, **kwargs) if kwargs else db.get_profile(profile_id)
    if db.get_active_profile_id() == profile_id:
        await _broadcast_profile_changed()
    return {"ok": True, "profile": p}


@app.delete("/api/profiles/{profile_id}")
async def api_delete_profile(profile_id: str):
    if profile_id == GUEST_PROFILE_ID:
        return JSONResponse({"ok": False, "error": "Cannot delete guest"}, status_code=400)
    if profile_id == _active_profile_id():
        return JSONResponse({"ok": False, "error": "Cannot delete active profile"}, status_code=409)
    if not db.delete_profile(profile_id):
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return {"ok": True}


class SelectProfileRequest(BaseModel):
    id: str


@app.post("/api/profile/select")
async def api_select_profile(req: SelectProfileRequest):
    if sess.active:
        return JSONResponse({"ok": False, "error": "Cannot switch profiles during active session"}, status_code=409)
    p = db.get_profile(req.id)
    if not p:
        return JSONResponse({"ok": False, "error": "Profile not found"}, status_code=404)
    global _guest_mode
    _guest_mode = False
    db.set_active_profile_id(req.id)
    _sync_workout_db()
    await _broadcast_profile_changed()
    return {"ok": True, "profile": p}


@app.get("/api/profile/active")
async def api_get_active_profile():
    pid = _active_profile_id()
    p = db.get_profile(pid) if db else None
    return {"profile": p, "guest_mode": _guest_mode}


@app.post("/api/profile/guest")
async def api_enter_guest_mode():
    if sess.active:
        return JSONResponse({"ok": False, "error": "Cannot switch during active session"}, status_code=409)
    global _guest_mode
    _guest_mode = True
    _sync_workout_db()
    await _broadcast_profile_changed()
    return {"ok": True, "guest_mode": True}


@app.post("/api/profile/guest/convert")
async def api_convert_guest():
    """Transfer all guest data to the active non-guest profile."""
    global _guest_mode
    pid = db.get_active_profile_id()
    if not pid or pid == GUEST_PROFILE_ID:
        return JSONResponse({"ok": False, "error": "No active non-guest profile"}, status_code=400)
    db.convert_guest(pid)
    _guest_mode = False
    _sync_workout_db()
    return {"ok": True, "profile_id": pid}


@app.post("/api/profiles/{profile_id}/avatar")
async def api_upload_avatar(profile_id: str, file: UploadFile = File(...)):
    if not db.get_profile(profile_id):
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    data = await file.read(1_000_000)  # 1MB max
    db.set_avatar(profile_id, data)
    return {"ok": True}


@app.delete("/api/profiles/{profile_id}/avatar")
async def api_delete_avatar(profile_id: str):
    if not db.get_profile(profile_id):
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    db.clear_avatar(profile_id)
    return {"ok": True}


@app.get("/api/profiles/{profile_id}/avatar")
async def api_get_avatar(profile_id: str):
    data = db.get_avatar(profile_id)
    if not data:
        return JSONResponse({"ok": False, "error": "No avatar"}, status_code=404)
    from fastapi.responses import Response

    return Response(content=data, media_type="image/png")


@app.get("/api/config")
async def get_config():
    """Return client config with ephemeral token for Gemini Live."""
    token = await asyncio.to_thread(_create_ephemeral_token)
    system_prompt = _build_chat_system()
    return {
        "gemini_api_key": token or "",
        "gemini_model": GEMINI_MODEL,
        "gemini_live_model": GEMINI_LIVE_MODEL,
        "gemini_voice": "Kore",
        "tools": TOOL_DECLARATIONS,
        "system_prompt": system_prompt,
        "smartass_addendum": SMARTASS_ADDENDUM,
    }


_BACKGROUND_ADVICE_FILE = "background_advice.json"
MAX_ADVISE_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB cap on untrusted base64 image
# Reject oversized base64 by string length BEFORE allocating the decoded bytes.
MAX_ADVISE_B64_LEN = (MAX_ADVISE_IMAGE_BYTES // 3 + 1) * 4 + 4
MAX_ADVICE_CACHE_ENTRIES = 256  # bound on-disk cache growth (public endpoint)


def _load_background_advice_cache() -> dict:
    """Load the on-disk background-advice cache; tolerate missing/corrupt file."""
    try:
        with open(_BACKGROUND_ADVICE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_background_advice_cache() -> None:
    """Persist the background-advice cache to disk (best effort)."""
    try:
        with open(_BACKGROUND_ADVICE_FILE, "w") as f:
            json.dump(_background_advice_cache, f)
    except OSError:
        log.warning("failed to persist background advice cache")


_background_advice_cache: dict = _load_background_advice_cache()
_background_advice_lock = asyncio.Lock()  # serializes cache eviction + persist


@app.post("/api/background/advise")
async def background_advise(req: BackgroundAdviseRequest):
    """Return a cached Gemini overlay prior for a background image.

    Writes are keyed by a server-computed SHA-256 of the actual image bytes, so a
    client cannot poison an arbitrary hash with advice for unrelated bytes. To get
    a cache hit on a later image-less request, the client must send
    image_hash = sha256(image) (the same digest the server stores under). On a
    miss with no image, the neutral prior is returned without caching. The base64
    image is untrusted: capped by string length before decode and by byte length
    after, validated, never shelled out, never logged. The on-disk cache is
    bounded to MAX_ADVICE_CACHE_ENTRIES.
    """
    # Image-less request: read-only lookup by the client-supplied digest.
    if not req.image_b64:
        return _background_advice_cache.get(req.image_hash, program_engine.NEUTRAL_PRIOR)
    if len(req.image_b64) > MAX_ADVISE_B64_LEN:
        raise HTTPException(status_code=400, detail="image too large")
    try:
        image_bytes = base64.b64decode(req.image_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="image_b64 is not valid base64")
    if len(image_bytes) > MAX_ADVISE_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="image too large")
    key = hashlib.sha256(image_bytes).hexdigest()
    if key in _background_advice_cache:
        return _background_advice_cache[key]
    # Offload the blocking Gemini call so the event loop keeps serving other requests.
    prior = await asyncio.to_thread(program_engine.advise_background, image_bytes)
    # Serialize the eviction + insert + persist so concurrent requests can't corrupt the
    # bound or interleave file writes; double-check the key in case another request raced us.
    async with _background_advice_lock:
        if key not in _background_advice_cache:
            if len(_background_advice_cache) >= MAX_ADVICE_CACHE_ENTRIES:
                # Drop the oldest entry (dicts preserve insertion order) to bound growth.
                _background_advice_cache.pop(next(iter(_background_advice_cache)), None)
            _background_advice_cache[key] = prior
            _save_background_advice_cache()
        return _background_advice_cache[key]


@app.post("/api/device-log")
async def device_log(request: Request):
    """Receive debug logs from iOS/Android clients."""
    body = await request.json()
    msg = body.get("message", "")
    cat = body.get("category", "device")
    log.info("[%s] %s", cat, msg)
    return {"ok": True}


@app.get("/api/log")
async def get_log(lines: int = 100):
    """Return last N lines of /tmp/treadmill_io.log."""
    log_path = "/tmp/treadmill_io.log"

    def _read_log():
        try:
            result = subprocess.run(
                ["tail", "-n", str(lines), log_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.splitlines() if result.returncode == 0 else []
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

    log_lines = await asyncio.to_thread(_read_log)
    return {"lines": log_lines}


@app.post("/api/speed")
async def set_speed(req: SpeedRequest):
    if not state["treadmill_connected"]:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    await _apply_speed(req.value)
    return build_status()


@app.post("/api/incline")
async def set_incline(req: InclineRequest):
    if not state["treadmill_connected"]:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    await _apply_incline(req.value)
    return build_status()


@app.post("/api/emulate")
async def set_emulate(req: EmulateRequest):
    if not state["treadmill_connected"]:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    if req.enabled:
        state["proxy"] = False
        state["emulate"] = True
        await _hw_set_emulate(True)
    else:
        state["emulate"] = False
        await _hw_set_emulate(False)
    await broadcast_status()
    return build_status()


@app.post("/api/proxy")
async def set_proxy(req: ProxyRequest):
    if not state["treadmill_connected"]:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    if req.enabled:
        state["emulate"] = False
        state["proxy"] = True
        await _hw_set_proxy(True)
    else:
        state["proxy"] = False
        await _hw_set_proxy(False)
    await broadcast_status()
    return build_status()


# --- HRM endpoints ---


_BLE_ADDR_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


class HrmSelectRequest(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def validate_ble_address(cls, v: str) -> str:
        if not _BLE_ADDR_RE.match(v):
            raise ValueError("Invalid BLE MAC address (expected XX:XX:XX:XX:XX:XX)")
        return v


@app.get("/api/hrm")
async def get_hrm():
    return {
        "heart_rate": state["heart_rate"],
        "connected": state["hrm_connected"],
        "device": state["hrm_device"],
        "available_devices": state.get("hrm_devices", []),
    }


@app.post("/api/hrm/select")
async def select_hrm(req: HrmSelectRequest):
    try:
        hrm.select_device(req.address)
    except ConnectionError:
        return JSONResponse({"error": "hrm-daemon not connected"}, status_code=503)
    return {"ok": True}


@app.post("/api/hrm/forget")
async def forget_hrm():
    try:
        hrm.forget_device()
    except ConnectionError:
        return JSONResponse({"error": "hrm-daemon not connected"}, status_code=503)
    return {"ok": True}


@app.post("/api/hrm/scan")
async def scan_hrm():
    try:
        hrm.scan()
    except ConnectionError:
        return JSONResponse({"error": "hrm-daemon not connected"}, status_code=503)
    return {"ok": True}


# --- Program endpoints ---


@app.post("/api/program/generate")
async def api_generate_program(req: GenerateRequest):
    try:
        program = await generate_program(req.prompt)
        sess.prog.load(program)
        _add_to_history(program, req.prompt)
        return {"ok": True, "program": program}
    except Exception as e:
        log.error(f"Program generation failed: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/program/start")
async def api_start_program():
    if not sess.prog.program:
        return {"ok": False, "error": "No program loaded"}
    await sess.start_program(_prog_on_change(), _prog_on_update())
    return sess.prog.to_dict()


class QuickStartRequest(BaseModel):
    speed: float = Field(default=3.0, ge=0.5, le=12.0)
    incline: float = Field(default=0, ge=0, le=15)
    duration_minutes: int = Field(default=60, ge=1, le=300)


@app.post("/api/program/quick-start")
async def api_quick_start(req: QuickStartRequest):
    """Create a simple single-interval program and start it immediately."""
    await sess.ensure_manual(
        speed=req.speed,
        incline=req.incline,
        duration_minutes=req.duration_minutes,
        on_change=_prog_on_change(),
        on_update=_prog_on_update(),
    )
    return {"ok": True, **sess.prog.to_dict()}


@app.post("/api/program/stop")
async def api_stop_program():
    await _apply_stop()
    return sess.prog.to_dict()


@app.post("/api/reset")
async def api_reset():
    """Full reset: stop belt, clear program, zero session."""
    await sess.reset()
    state["emu_speed"] = 0
    state["emu_incline"] = 0
    await _hw_set_speed(0)
    await _hw_set_incline(0)
    await manager.broadcast(sess.to_dict())
    await broadcast_status()
    return {"ok": True}


@app.post("/api/program/pause")
async def api_pause_program():
    await _apply_pause_toggle()
    return sess.prog.to_dict()


@app.post("/api/program/skip")
async def api_skip_program():
    await sess.prog.skip()
    return sess.prog.to_dict()


@app.post("/api/program/prev")
async def api_prev_program():
    await sess.prog.prev()
    return sess.prog.to_dict()


class ExtendRequest(BaseModel):
    seconds: int = Field(ge=-3600, le=3600)


@app.post("/api/program/extend")
async def api_extend_interval(req: ExtendRequest):
    if not sess.prog.running:
        return {"ok": False, "error": "No program running"}
    ok = await sess.prog.extend_current(req.seconds)
    if ok:
        await manager.broadcast(sess.prog.to_dict())
    return sess.prog.to_dict()


class DurationAdjustRequest(BaseModel):
    delta_seconds: int = Field(ge=-3600, le=3600)


@app.post("/api/program/adjust-duration")
async def api_adjust_duration(req: DurationAdjustRequest):
    """Adjust manual program total duration by adding/removing time from last interval."""
    if not sess.prog.is_manual or not sess.prog.running:
        return {"ok": False, "error": "No manual program running"}
    ok = await sess.prog.adjust_duration(req.delta_seconds)
    if ok:
        await manager.broadcast(sess.prog.to_dict())
    return sess.prog.to_dict()


@app.get("/api/program")
async def api_get_program():
    return sess.prog.to_dict()


@app.get("/api/programs/history")
async def api_get_history():
    history = db.get_program_history(_active_profile_id())
    saved_by_fp = {_program_fingerprint(w["program"]): w["id"] for w in db.get_saved_workouts(_active_profile_id())}
    run_by_fp = _last_run_by_fingerprint()
    for entry in history:
        fp = _program_fingerprint(entry["program"])
        entry["saved"] = fp in saved_by_fp
        entry["saved_workout_id"] = saved_by_fp.get(fp)
        run = run_by_fp.get(fp)
        entry["last_run"] = run
        entry["last_run_text"] = _last_run_text(run)
    return history


@app.post("/api/programs/history/{entry_id}/load")
async def api_load_from_history(entry_id: str):
    entry = db.get_history_entry(entry_id)
    if not entry:
        return {"ok": False, "error": "Not found"}
    # Ownership check
    if entry.get("profile_id") != _active_profile_id():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    sess.prog.load(entry["program"])
    return {"ok": True, "program": entry["program"]}


@app.post("/api/programs/history/{entry_id}/resume")
async def api_resume_from_history(entry_id: str):
    """Load a program from history and start from the saved position."""
    entry = db.get_history_entry(entry_id)
    if not entry:
        return {"ok": False, "error": "Not found"}
    # Ownership check
    if entry.get("profile_id") != _active_profile_id():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    if entry.get("completed"):
        return {"ok": False, "error": "Program already completed — use load to start over"}
    sess.prog.load(entry["program"])
    resume_iv = entry.get("last_interval", 0)
    resume_elapsed = entry.get("last_elapsed", 0)
    await sess.start_program(
        _prog_on_change(),
        _prog_on_update(),
        resume_interval=resume_iv,
        resume_elapsed=resume_elapsed,
    )
    return {"ok": True, **sess.prog.to_dict()}


# --- Saved Workouts ---


@app.get("/api/workouts")
async def api_list_workouts():
    workouts = db.get_saved_workouts(_active_profile_id())
    run_by_fp = _last_run_by_fingerprint()
    for w in workouts:
        run = run_by_fp.get(_program_fingerprint(w["program"]))
        w["last_run"] = run
        w["last_run_text"] = _last_run_text(run)
        w["usage_text"] = _usage_text(w, run)
    return workouts


@app.get("/api/runs")
async def api_list_runs():
    return db.get_runs(_active_profile_id())


@app.post("/api/workouts")
async def api_save_workout(req: SaveWorkoutRequest):
    if req.history_id:
        entry = db.get_history_entry(req.history_id)
        if not entry:
            return {"ok": False, "error": "History entry not found"}
        # Ownership check
        if entry.get("profile_id") != _active_profile_id():
            return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
        program = entry["program"]
        prompt = entry.get("prompt", "")
        # Infer source
        if prompt and prompt.startswith("GPX:"):
            source = "gpx"
        elif program.get("manual"):
            source = "manual"
        else:
            source = "generated"
    elif req.program:
        err = _validate_program(req.program)
        if err:
            return {"ok": False, "error": err}
        program = req.program
        source = req.source
        prompt = req.prompt
    else:
        return {"ok": False, "error": "Provide history_id or program"}

    workout = _save_workout(program, source=source, prompt=prompt)
    if workout is None:
        return {"ok": False, "error": "Create a profile to save workouts"}
    return {"ok": True, "workout": workout}


@app.put("/api/workouts/{workout_id}")
async def api_rename_workout(workout_id: str, req: RenameWorkoutRequest):
    w = db.get_saved_workout(workout_id)
    if not w:
        return {"ok": False, "error": "Not found"}
    # Ownership check
    if w.get("profile_id") != _active_profile_id():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    workout = db.rename_workout(workout_id, req.name)
    _sync_workout_db()
    return {"ok": True, "workout": workout}


@app.delete("/api/workouts/{workout_id}")
async def api_delete_workout(workout_id: str):
    w = db.get_saved_workout(workout_id)
    if not w:
        return {"ok": False, "error": "Not found"}
    # Ownership check
    if w.get("profile_id") != _active_profile_id():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    db.delete_workout(workout_id)
    _sync_workout_db()
    return {"ok": True}


@app.post("/api/workouts/{workout_id}/load")
async def api_load_workout(workout_id: str):
    workout = db.get_saved_workout(workout_id)
    if not workout:
        return {"ok": False, "error": "Not found"}
    db.update_workout_usage(workout_id)
    sess.prog.load(workout["program"])
    _add_to_history(workout["program"], prompt=workout.get("prompt", ""))
    _sync_workout_db()
    return {"ok": True, "program": workout["program"]}


# --- Workout query (for voice function bridge) ---


class ToolCallRequest(BaseModel):
    name: str
    args: dict = {}
    context: str | None = None  # optional: why the tool was called (user utterance, model reasoning)


@app.post("/api/tool")
async def api_exec_tool(req: ToolCallRequest):
    """Generic tool execution endpoint. Forwards to _exec_fn(), the single source of truth."""
    if req.context:
        log.info("tool call: %s(%s) context: %s", req.name, req.args, req.context)
    else:
        log.info("tool call: %s(%s)", req.name, req.args)
    try:
        result = await _exec_fn(req.name, req.args)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- GPX upload ---


def _parse_gpx_to_intervals(gpx_bytes):
    """Parse a GPX file into treadmill interval program."""
    import math

    try:
        import gpxpy
    except ImportError:
        raise ValueError("gpxpy not installed — run: pip3 install gpxpy")

    gpx = gpxpy.parse(gpx_bytes.decode("utf-8"))

    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                if pt.elevation is not None:
                    points.append((pt.latitude, pt.longitude, pt.elevation))

    if len(points) < 2:
        raise ValueError("GPX file needs at least 2 points with elevation data")

    # Calculate segments with grade
    segments = []
    for i in range(1, len(points)):
        lat1, lon1, ele1 = points[i - 1]
        lat2, lon2, ele2 = points[i]
        # Haversine distance
        R = 6371000  # Earth radius in meters
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        horiz = 2 * R * math.asin(math.sqrt(a))
        if horiz < 1:
            continue  # skip negligible segments
        grade = ((ele2 - ele1) / horiz) * 100
        segments.append({"distance": horiz, "grade": grade, "elevation": ele2})

    if not segments:
        raise ValueError("No valid segments found in GPX")

    # Merge short segments (min 100m)
    merged = []
    accum_dist = 0
    accum_grade_dist = 0
    for seg in segments:
        accum_dist += seg["distance"]
        accum_grade_dist += seg["grade"] * seg["distance"]
        if accum_dist >= 100:
            avg_grade = accum_grade_dist / accum_dist if accum_dist > 0 else 0
            merged.append({"distance": accum_dist, "grade": avg_grade})
            accum_dist = 0
            accum_grade_dist = 0
    if accum_dist > 0:
        avg_grade = accum_grade_dist / accum_dist if accum_dist > 0 else 0
        merged.append({"distance": accum_dist, "grade": avg_grade})

    # Convert to time-based intervals at base walking pace
    BASE_SPEED_MPS = 3.1 * 0.44704  # 3.1 mph in m/s
    intervals = []
    for i, seg in enumerate(merged):
        duration = int(seg["distance"] / BASE_SPEED_MPS)
        incline = int(round(seg["grade"]))
        speed = 3.1

        # Label based on grade
        grade = seg["grade"]
        if i == 0:
            label = "Start"
        elif i == len(merged) - 1:
            label = "Finish"
        elif grade < 1:
            label = "Flat"
        elif grade < 3:
            label = "Rolling"
        elif grade < 6:
            label = "Hill"
        else:
            label = "Steep Climb"

        iv = {
            "name": label,
            "duration": duration,
            "speed": speed,
            "incline": incline,
        }
        validate_interval(iv)
        intervals.append(iv)

    total_dist = sum(s["distance"] for s in merged)
    return {
        "name": f"GPX Route ({total_dist/1000:.1f} km)",
        "intervals": intervals,
    }


@app.post("/api/gpx/upload")
async def api_gpx_upload(file: UploadFile = File(...)):
    MAX_GPX_SIZE = 10_000_000  # 10MB
    try:
        # Check Content-Length header first (fast reject), then enforce during read
        if file.size and file.size > MAX_GPX_SIZE:
            return {"ok": False, "error": "GPX file too large (max 10MB)"}
        chunks = []
        total = 0
        while chunk := await file.read(65536):
            total += len(chunk)
            if total > MAX_GPX_SIZE:
                return {"ok": False, "error": "GPX file too large (max 10MB)"}
            chunks.append(chunk)
        gpx_bytes = b"".join(chunks)
        program = _parse_gpx_to_intervals(gpx_bytes)
        sess.prog.load(program)
        _add_to_history(program, f"GPX: {file.filename}")
        return {"ok": True, "program": program}
    except Exception as e:
        log.error(f"GPX upload failed: {e}")
        return {"ok": False, "error": str(e)}


# --- Chat endpoint (agentic Gemini) ---


def _prog_on_change():
    """Return an on_change callback for program execution."""

    async def on_change(speed, incline):
        state["emu_speed"] = max(0, min(int(speed * 10), MAX_SPEED_TENTHS))
        # incline from program is in percent; store as half-pct units
        clamped_inc = max(0.0, min(float(incline), MAX_SAFE_INCLINE))
        clamped_inc = round(clamped_inc * 2) / 2  # snap to 0.5 steps
        state["emu_incline"] = int(clamped_inc * 2)
        await _hw_set_speed(speed)
        await _hw_set_incline(clamped_inc)
        await broadcast_status()

    return on_change


def _prog_on_update():
    """Return an on_update callback for program execution."""

    async def on_update(prog_state):
        await manager.broadcast(prog_state)
        # When program completes, stop the treadmill and end the session
        if prog_state.get("completed") and not prog_state.get("running"):
            # Mark completed in history
            prog = prog_state.get("program")
            if prog:
                _update_history_position(
                    prog.get("name", ""),
                    prog_state.get("current_interval", 0),
                    prog_state.get("total_elapsed", 0),
                    completed=True,
                )
            state["emu_speed"] = 0
            state["emu_incline"] = 0
            await _hw_set_speed(0)
            await _hw_set_incline(0)
            if sess.active:
                _save_run_record("program_complete")
                sess.end("program_complete")
                await manager.broadcast(sess.to_dict())
            await broadcast_status()

    return on_update


async def _exec_fn(name, args):
    """Execute a treadmill function call from Gemini."""
    if name == "set_speed":
        try:
            mph = float(args.get("mph", 0))
            if not (0 <= mph <= 12.0) or mph != mph:  # catches NaN
                mph = 0
        except (ValueError, TypeError):
            return "Invalid speed value"
        await _apply_speed(mph)
        return f"Speed set to {mph} mph"

    elif name == "set_incline":
        try:
            inc = float(args.get("incline", 0))
            inc = max(0.0, min(inc, MAX_SAFE_INCLINE))
            inc = round(inc * 2) / 2  # snap to 0.5 steps
        except (ValueError, TypeError):
            return "Invalid incline value"
        await _apply_incline(inc)
        return f"Incline set to {inc}%"

    elif name == "generate_workout":
        desc = args.get("description", "")
        try:
            program = await generate_program(desc)
            sess.prog.load(program)
            # Loaded but NOT started, NOT saved to history yet.
            # Gemini should describe it and ask user to confirm before calling start_workout.
            n = len(program["intervals"])
            mins = sum(iv["duration"] for iv in program["intervals"]) // 60
            intervals_summary = ", ".join(
                f"{iv['name']} ({iv['duration']//60}min, {iv['speed']}mph, {iv['incline']}%)"
                for iv in program["intervals"][:6]
            )
            if n > 6:
                intervals_summary += f", ... ({n - 6} more)"
            return f"Generated '{program['name']}': {n} intervals, {mins} min. Intervals: {intervals_summary}. Tell the user what you created and ask if they want to start."
        except Exception as e:
            return f"Failed: {e}"

    elif name == "start_workout":
        # Start the currently loaded program (from generate_workout or load_workout)
        if not sess.prog.program:
            return "No program loaded. Use generate_workout first."
        program = sess.prog.program
        _add_to_history(program, args.get("description", ""))
        await sess.start_program(_prog_on_change(), _prog_on_update())
        n = len(program.get("intervals", []))
        mins = sum(iv["duration"] for iv in program.get("intervals", [])) // 60
        return f"Started '{program.get('name')}': {n} intervals, {mins} min"

    elif name == "stop_treadmill":
        await _apply_stop()
        return "Treadmill stopped"

    elif name == "pause_program":
        if sess.prog.running:
            result = await _apply_pause_toggle()
            return f"Program {result}"
        return "No program running"

    elif name == "resume_program":
        if sess.prog.paused:
            result = await _apply_pause_toggle()
            return f"Program {result}"
        return "No paused program"

    elif name == "skip_interval":
        if sess.prog.running:
            await sess.prog.skip()
            iv = sess.prog.current_iv
            return f"Skipped to: {iv['name']}" if iv else "Program complete"
        return "No program running"

    elif name == "extend_interval":
        try:
            secs = int(args.get("seconds", 0))
            secs = max(-3600, min(secs, 3600))
        except (ValueError, TypeError):
            return "Invalid seconds value"
        if sess.prog.running:
            ok = await sess.prog.extend_current(secs)
            if ok:
                iv = sess.prog.current_iv
                return f"Interval now {iv['duration']}s ({'+' if secs > 0 else ''}{secs}s)"
            return "No current interval"
        return "No program running"

    elif name == "add_time":
        intervals = args.get("intervals", [])
        if not intervals:
            return "No intervals provided"
        if sess.prog.program:
            ok = await sess.prog.add_intervals(intervals)
            if ok:
                added = sum(iv.get("duration", 0) for iv in intervals)
                return f"Added {len(intervals)} interval(s), {added}s total. Program now {sess.prog.total_duration}s."
            return "Failed to add intervals"
        return "No program loaded"

    elif name == "load_workout":
        wid = str(args.get("id", ""))
        start = args.get("start", True)
        # Check saved workouts first, then history
        program = None
        workout = db.get_saved_workout(wid)
        if workout:
            db.update_workout_usage(wid)
            program = workout["program"]
        else:
            entry = db.get_history_entry(wid)
            if entry:
                program = entry["program"]
        if not program:
            return f"Workout id '{wid}' not found"
        sess.prog.load(program)
        _sync_workout_db()
        if start:
            await sess.start_program(_prog_on_change(), _prog_on_update())
            n = len(program.get("intervals", []))
            mins = sum(iv["duration"] for iv in program.get("intervals", [])) // 60
            return f"Loaded and started '{program.get('name')}': {n} intervals, {mins} min"
        return f"Loaded '{program.get('name')}' (not started yet)"

    elif name == "query_workout_data":
        sql = args.get("sql", "")
        if not sql:
            return "No SQL query provided"
        try:
            # DB is kept in sync via mutation hooks (no pre-query sync needed)
            rows = workout_db.query(sql)
            if not rows:
                return "No results"
            return json.dumps(rows, default=str)
        except Exception as e:
            return f"Query error: {e}"

    return f"Unknown function: {name}"


def _build_chat_system(smartass=False):
    """Build the system prompt with current treadmill state context."""
    treadmill_state = {
        "speed_mph": state["emu_speed"] / 10,
        "incline_pct": state["emu_incline"] / 2.0,
        "mode": "emulate" if state["emulate"] else "proxy" if state["proxy"] else "off",
    }
    if state["hrm_connected"]:
        treadmill_state["heart_rate_bpm"] = state["heart_rate"]
    if sess.prog.program:
        treadmill_state["program"] = {
            "name": sess.prog.program.get("name"),
            "running": sess.prog.running,
            "paused": sess.prog.paused,
            "current_interval_index": sess.prog.current_interval,
            "interval": sess.prog.current_iv.get("name") if sess.prog.current_iv else None,
            "interval_remaining_s": (
                (sess.prog.current_iv["duration"] - sess.prog.interval_elapsed) if sess.prog.current_iv else 0
            ),
            "elapsed": sess.prog.total_elapsed,
            "remaining": sess.prog.total_duration - sess.prog.total_elapsed,
            "total_intervals": len(sess.prog.program.get("intervals", [])),
            "current_workout_id": "__active__",
            "current_workout_fingerprint": _program_fingerprint(sess.prog.program),
        }

    history = db.get_program_history(_active_profile_id()) if db else []
    workouts = db.get_saved_workouts(_active_profile_id()) if db else []
    library_lines = []
    for w in workouts[:10]:
        wname = w.get("name") or w["program"].get("name", "?")
        mins = sum(iv.get("duration", 0) for iv in w["program"].get("intervals", [])) // 60
        library_lines.append(f'- "{wname}" ({mins} min, saved) id={w["id"]}')
    for h in history[:10]:
        hname = h["program"].get("name", "?")
        mins = sum(iv.get("duration", 0) for iv in h["program"].get("intervals", [])) // 60
        library_lines.append(f'- "{hname}" ({mins} min, history) id={h["id"]}')
    library_text = ""
    if library_lines:
        library_text = (
            "\n\nAvailable workouts. When the user asks to load a workout, "
            "present the list by name and duration only (never show IDs to the user). "
            "Let them choose, then call load_workout with the id internally.\n" + "\n".join(library_lines)
        )

    schema_text = (
        "\n\nYou have access to the workout database via the query_workout_data tool. "
        "You can write read-only SQL (SELECT only) against these tables:\n"
        "- workouts (id, fingerprint, name, source, prompt, total_duration, created_at, times_used, is_saved)\n"
        "- intervals (workout_id, position, name, duration_s, speed_mph, incline_pct)\n"
        "- runs (id, program_fingerprint, program_name, started_at, ended_at, elapsed, distance, "
        "vert_feet, calories, end_reason, program_completed, is_manual)\n\n"
        "Join runs to workouts via: runs.program_fingerprint = workouts.fingerprint\n"
        "The currently loaded workout has id='__active__'.\n\n"
        "Example queries:\n"
        "- SELECT * FROM intervals WHERE workout_id = '__active__' ORDER BY position\n"
        "- SELECT started_at, elapsed, distance, calories FROM runs "
        "WHERE program_fingerprint = '<fingerprint>' ORDER BY started_at DESC LIMIT 3\n"
        "- SELECT position, name, speed_mph, incline_pct, duration_s FROM intervals "
        "WHERE workout_id = '__active__' AND speed_mph = "
        "(SELECT MAX(speed_mph) FROM intervals WHERE workout_id = '__active__')\n\n"
        "Use this to understand workout structure, compare with past performance, "
        "and provide informed coaching. Query what you need, when you need it."
    )

    base_prompt = CHAT_SYSTEM_PROMPT + (SMARTASS_ADDENDUM if smartass else "")
    return f"{base_prompt}{library_text}{schema_text}\n\nCurrent state:\n{json.dumps(treadmill_state)}"


async def _run_chat_core(smartass=False):
    """Run the Gemini function-calling loop using chat_history. Returns response dict."""
    global chat_history

    system = _build_chat_system(smartass=smartass)
    executed = []
    history_len_before = len(chat_history)

    try:
        for _ in range(3):  # max function-calling turns
            result = await call_gemini(chat_history, system, TOOL_DECLARATIONS)
            candidates = result.get("candidates", [])
            if not candidates:
                return {"text": "AI had no response. Try again.", "actions": executed}
            candidate = candidates[0].get("content", {})
            parts = candidate.get("parts", [])

            func_calls = [p for p in parts if "functionCall" in p]
            text_parts = [p.get("text", "") for p in parts if "text" in p]

            if not func_calls:
                chat_history.append(candidate)
                if len(chat_history) > 20:
                    chat_history = chat_history[-20:]
                return {"text": " ".join(text_parts).strip(), "actions": executed}

            # Execute function calls
            chat_history.append(candidate)
            func_responses = []
            for fc in func_calls:
                call = fc["functionCall"]
                name = call["name"]
                args = call.get("args", {})
                result_str = await _exec_fn(name, args)
                executed.append({"name": name, "args": args, "result": result_str})
                func_responses.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {"result": result_str},
                        }
                    }
                )
            chat_history.append({"role": "user", "parts": func_responses})

        # Fell through max turns
        if len(chat_history) > 20:
            chat_history = chat_history[-20:]
        return {"text": "Done!", "actions": executed}

    except Exception as e:
        log.error(f"Chat error: {e}")
        # Roll back to pre-turn state instead of wiping everything
        chat_history = chat_history[:history_len_before]
        return {"text": "Something went wrong — try again.", "actions": executed}


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    async with _chat_lock:
        chat_history.append({"role": "user", "parts": [{"text": req.message}]})
        return await _run_chat_core(smartass=req.smartass)


@app.post("/api/chat/voice")
async def api_chat_voice(req: VoiceChatRequest):
    # Step 1: Transcribe the audio with a separate Gemini call so we can show
    # the user what was heard before the coach responds.
    transcription = ""
    try:
        transcription = await _transcribe_audio(req.audio, req.mime_type)
    except Exception as e:
        log.warning(f"Transcription failed (proceeding with audio): {e}")

    async with _chat_lock:
        # Step 2: Add audio as user message — Gemini natively understands speech
        audio_parts = [{"inlineData": {"mimeType": req.mime_type, "data": req.audio}}]
        chat_history.append({"role": "user", "parts": audio_parts})

        result = await _run_chat_core(smartass=req.smartass)

        # Replace the audio blob in history with transcribed text to save memory
        replacement_text = transcription if transcription else "[voice message]"
        for msg in chat_history:
            if msg.get("parts") is audio_parts:
                msg["parts"] = [{"text": replacement_text}]
                break

    # Include transcription in the response
    if transcription:
        result["transcription"] = transcription

    return result


async def _transcribe_audio(audio_b64, mime_type):
    """Transcribe audio using Gemini — returns the text that was spoken."""
    api_key = read_api_key()
    if not api_key:
        return ""

    contents = [
        {
            "parts": [
                {"inlineData": {"mimeType": mime_type, "data": audio_b64}},
                {
                    "text": "Transcribe exactly what was said in this audio. Return ONLY the transcribed text, nothing else. If the audio is unclear or empty, return an empty string."
                },
            ]
        }
    ]

    result = await call_gemini(
        contents,
        "You are a speech transcription tool. Return only the exact words spoken.",
        api_key=api_key,
        generation_config={"temperature": 0.1, "maxOutputTokens": 256},
    )

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip().strip('"').strip("'")
    except (KeyError, IndexError):
        return ""


@app.post("/api/tts")
async def api_tts(req: TTSRequest):
    """Generate speech audio from text using Gemini TTS."""
    try:
        genai_client = get_client()
        config = build_tts_config(voice=req.voice)
        resp = await genai_client.aio.models.generate_content(
            model=TTS_MODEL,
            contents=req.text,
            config=config,
        )
        audio_data = resp.candidates[0].content.parts[0].inline_data.data
        audio_b64 = base64.b64encode(audio_data).decode("ascii")
        return {
            "ok": True,
            "audio": audio_b64,  # base64-encoded PCM 24kHz 16-bit mono
            "sample_rate": 24000,
            "channels": 1,
            "bit_depth": 16,
        }
    except Exception as e:
        log.error(f"TTS failed: {e}")
        return {"ok": False, "error": str(e)}


# --- Voice intent extraction ---


class ExtractIntentRequest(BaseModel):
    text: str
    already_executed: list[str] = []  # function names already called by Live


@app.post("/api/voice/extract-intent")
async def api_extract_intent(req: ExtractIntentRequest):
    """Extract function calls from Gemini Live 'thinks aloud' text, then execute them."""
    log.info(f"[voice-fallback] text={req.text[:200]}")
    log.info(f"[voice-fallback] already_executed={req.already_executed}")

    try:
        actions = await extract_intent_from_text(req.text, req.already_executed)
    except Exception as e:
        log.error(f"[voice-fallback] Flash call failed: {e}")
        return {"actions": [], "text": f"Error: {e}"}

    log.info(f"[voice-fallback] extracted: {[a['name'] for a in actions]}")

    # Execute the extracted actions
    for action in actions:
        try:
            result_str = await _exec_fn(action["name"], action["args"])
            action["result"] = result_str
            log.info(f"[voice-fallback] EXEC: {action['name']}({action['args']}) -> {result_str}")
        except Exception as e:
            action["result"] = f"Error: {e}"
            log.error(f"[voice-fallback] EXEC failed: {action['name']}({action['args']}): {e}")

    if not actions:
        log.info("[voice-fallback] no actions extracted")

    return {"actions": actions, "text": ""}


# --- WebSocket endpoint ---


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(build_status()))
        if sess.active:
            await ws.send_text(json.dumps(sess.to_dict()))
        if sess.prog.program:
            await ws.send_text(json.dumps(sess.prog.to_dict()))
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# Mount static files AFTER api routes, then SPA catch-all
app.mount("/assets", StaticFiles(directory="static/assets"), name="static-assets")


@app.get("/{full_path:path}")
async def spa_catch_all(request: Request, full_path: str):
    """Serve static files or fall back to index.html for SPA routing."""
    static_dir = os.path.realpath("static")
    file_path = os.path.realpath(os.path.join(static_dir, full_path))
    # Prevent path traversal — file must be inside static_dir
    if not file_path.startswith(static_dir + os.sep) and file_path != static_dir:
        return JSONResponse({"error": "not found"}, status_code=404)
    if full_path and os.path.isfile(file_path):
        return FileResponse(file_path)
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return JSONResponse({"error": "not found"}, status_code=404)


if __name__ == "__main__":
    ssl_args = {}
    cert = "cert.pem"
    key = "key.pem"
    if os.path.isfile(cert) and os.path.isfile(key):
        ssl_args = {"ssl_keyfile": key, "ssl_certfile": cert}
        log.info("HTTPS enabled (cert.pem + key.pem)")
    port = int(os.environ.get("TREADMILL_SERVER_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, **ssl_args)
