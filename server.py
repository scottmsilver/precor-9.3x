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
import json
import logging
import os
import re
import subprocess
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel, field_validator

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
from treadmill_client import MAX_INCLINE, MAX_SPEED_TENTHS, TreadmillClient
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


@asynccontextmanager
async def lifespan(application):
    global loop, msg_queue, client, hrm, sess

    loop = asyncio.get_event_loop()
    msg_queue = asyncio.Queue(maxsize=500)
    sess = WorkoutSession()

    # Connect to treadmill_io C binary
    client = TreadmillClient()

    def on_message(msg):
        msg_type = msg.get("type")
        if msg_type == "kv":
            source = msg.get("source", "")
            key = msg.get("key", "")
            value = msg.get("value", "")
            if source == "motor":
                latest["last_motor"][key] = value
            elif source in ("console", "emulate"):
                latest["last_console"][key] = value
            push_msg(msg)
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
            # Detect watchdog / auto-proxy killing emulate while session active
            if was_emulating and not state["emulate"] and sess.active:
                reason = "auto_proxy" if state["proxy"] else "watchdog"
                sess.end(reason)
                push_msg(sess.to_dict())
            push_msg(msg)

    client.on_message = on_message

    def on_disconnect():
        state["treadmill_connected"] = False
        log.warning("treadmill_io disconnected")

        # All session mutations must happen on the event loop thread
        def _handle_disconnect():
            if sess.active:
                sess.end("disconnect")
                push_msg(sess.to_dict())
            push_msg({"type": "connection", "connected": False})

        loop.call_soon_threadsafe(_handle_disconnect)
        # Auto-pause program if running (async, so use coroutine)
        if sess.prog.running and not sess.prog.paused:
            asyncio.run_coroutine_threadsafe(sess.prog.toggle_pause(), loop)

    def on_reconnect():
        state["treadmill_connected"] = True
        log.info("treadmill_io reconnected")
        push_msg({"type": "connection", "connected": True})
        # Request fresh status from C binary
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
    hrm = HrmClient()

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
        state["hrm_connected"] = False
        state["heart_rate"] = 0
        state["hrm_device"] = ""
        state["hrm_devices"] = []
        push_msg({"type": "hr", "bpm": 0, "connected": False, "device": ""})

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
    log.info("Server stopped")


app = FastAPI(title="Treadmill Controller", lifespan=lifespan)

# CORS for Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
}

latest = {
    "last_console": {},
    "last_motor": {},
}

chat_history: list = []

HISTORY_FILE = "program_history.json"
MAX_HISTORY = 10


def _load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def _add_to_history(program, prompt=""):
    history = _load_history()
    entry = {
        "id": f"{int(time.time())}",
        "prompt": prompt,
        "program": program,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_duration": sum(iv["duration"] for iv in program.get("intervals", [])),
    }
    # Deduplicate by name - replace if same name exists
    history = [h for h in history if h["program"].get("name") != program.get("name")]
    history.insert(0, entry)
    history = history[:MAX_HISTORY]
    _save_history(history)
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


async def _session_tick_loop():
    """1/sec loop: compute session metrics and broadcast to all WS clients."""
    while state["running"]:
        if sess.active:
            sess.tick(state["emu_speed"] / 10, state["emu_incline"])
            await manager.broadcast(sess.to_dict())
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
    # Decode live speed from motor hmph response (hex mph*100)
    speed = None
    hmph = latest["last_motor"].get("hmph")
    if hmph:
        try:
            speed = int(hmph, 16) / 100
        except ValueError:
            pass
    # Decode live incline from motor inc response
    incline = None
    inc = latest["last_motor"].get("inc")
    if inc:
        try:
            incline = float(inc)
        except ValueError:
            pass
    return {
        "type": "status",
        "proxy": state["proxy"],
        "emulate": state["emulate"],
        "emu_speed": state["emu_speed"],
        "emu_speed_mph": emu_mph,
        "emu_incline": state["emu_incline"],
        "speed": speed,
        "incline": incline,
        "motor": latest["last_motor"],
        "treadmill_connected": state["treadmill_connected"],
        "heart_rate": state["heart_rate"],
        "hrm_connected": state["hrm_connected"],
        "hrm_device": state["hrm_device"],
    }


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
            await asyncio.sleep(0.1)


# --- Pydantic models ---


class SpeedRequest(BaseModel):
    value: float  # mph


class InclineRequest(BaseModel):
    value: int


class EmulateRequest(BaseModel):
    enabled: bool


class ProxyRequest(BaseModel):
    enabled: bool


class GenerateRequest(BaseModel):
    prompt: str


class ChatRequest(BaseModel):
    message: str
    smartass: bool = False


class VoiceChatRequest(BaseModel):
    audio: str  # base64-encoded audio
    mime_type: str = "audio/webm"
    smartass: bool = False


class TTSRequest(BaseModel):
    text: str
    voice: str = "Kore"


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
            speed=mph, incline=state["emu_incline"], on_change=_prog_on_change(), on_update=_prog_on_update()
        )
    elif mph == 0 and sess.active:
        if sess.prog.running:
            await sess.prog.stop()
        sess.end("user_stop")
        await manager.broadcast(sess.to_dict())
    # Split manual program interval to record course
    if sess.prog.is_manual and sess.prog.running and mph > 0:
        await sess.prog.split_for_manual(mph, state["emu_incline"])
    try:
        client.set_speed(mph)
    except ConnectionError:
        log.warning("Cannot set speed: treadmill_io disconnected")
    await broadcast_status()


MAX_SAFE_INCLINE = 15  # Application-layer limit (hardware allows 0-99)


async def _apply_incline(inc):
    """Core incline logic shared by REST endpoint and Gemini function calls."""
    global _dirty_incline_until
    _dirty_incline_until = time.monotonic() + _DIRTY_GRACE_SEC
    state["emu_incline"] = max(0, min(inc, MAX_SAFE_INCLINE))
    # Mirror C binary's auto-emulate: sending an incline command enables emulate mode
    if inc > 0:
        state["emulate"] = True
        state["proxy"] = False
    # Use the clamped value for all downstream operations
    clamped_inc = state["emu_incline"]
    # Split manual program interval to record course
    if sess.prog.is_manual and sess.prog.running:
        await sess.prog.split_for_manual(state["emu_speed"] / 10, clamped_inc)
    try:
        client.set_incline(clamped_inc)
    except ConnectionError:
        log.warning("Cannot set incline: treadmill_io disconnected")
    await broadcast_status()


async def _apply_stop():
    """Core stop logic shared by REST endpoint and Gemini function calls."""
    if sess.prog.running:
        await sess.prog.stop()
    state["emu_speed"] = 0
    state["emu_incline"] = 0
    if sess.active:
        sess.end("user_stop")
        await manager.broadcast(sess.to_dict())
    try:
        client.set_speed(0)
        client.set_incline(0)
    except ConnectionError:
        log.warning("Cannot send stop: treadmill_io disconnected")
    await broadcast_status()


# --- REST endpoints ---


@app.get("/api/status")
async def get_status():
    return build_status()


@app.get("/api/session")
async def get_session():
    return sess.to_dict()


GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"

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
                "uses": 1,
                "expire_time": now + datetime.timedelta(minutes=30),
                "new_session_expire_time": now + datetime.timedelta(minutes=2),
                "http_options": {"api_version": "v1alpha"},
            }
        )
        return token.name
    except Exception:
        log.exception("Failed to create ephemeral token")
        return None


@app.get("/api/config")
async def get_config():
    """Return client config with ephemeral token for Gemini Live."""
    token = await asyncio.to_thread(_create_ephemeral_token)
    return {
        "gemini_api_key": token or "",
        "gemini_model": GEMINI_MODEL,
        "gemini_live_model": GEMINI_LIVE_MODEL,
        "gemini_voice": "Kore",
    }


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
    try:
        if req.enabled:
            state["proxy"] = False
            state["emulate"] = True
            client.set_emulate(True)
        else:
            state["emulate"] = False
            client.set_emulate(False)
    except ConnectionError:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    await broadcast_status()
    return build_status()


@app.post("/api/proxy")
async def set_proxy(req: ProxyRequest):
    if not state["treadmill_connected"]:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
    try:
        if req.enabled:
            state["emulate"] = False
            state["proxy"] = True
            client.set_proxy(True)
        else:
            state["proxy"] = False
            client.set_proxy(False)
    except ConnectionError:
        return JSONResponse({"error": "treadmill_io disconnected"}, status_code=503)
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
    speed: float = 3.0
    incline: int = 0
    duration_minutes: int = 60


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
    try:
        client.set_speed(0)
        client.set_incline(0)
    except ConnectionError:
        log.warning("Cannot send stop: treadmill_io disconnected")
    await manager.broadcast(sess.to_dict())
    await broadcast_status()
    return {"ok": True}


@app.post("/api/program/pause")
async def api_pause_program():
    await sess.prog.toggle_pause()
    if sess.prog.paused:
        # Pause: stop the belt, remember speed for resume
        sess.pause()
        state["_paused_speed"] = state["emu_speed"]
        state["emu_speed"] = 0
        try:
            client.set_speed(0)
        except ConnectionError:
            pass
        await broadcast_status()
    else:
        # Resume: session timer resumes, speed restored by program engine's on_change
        sess.resume()
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
    seconds: int


@app.post("/api/program/extend")
async def api_extend_interval(req: ExtendRequest):
    if not sess.prog.running:
        return {"ok": False, "error": "No program running"}
    ok = await sess.prog.extend_current(req.seconds)
    if ok:
        await manager.broadcast(sess.prog.to_dict())
    return sess.prog.to_dict()


class DurationAdjustRequest(BaseModel):
    delta_seconds: int


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
    return _load_history()


@app.post("/api/programs/history/{entry_id}/load")
async def api_load_from_history(entry_id: str):
    history = _load_history()
    entry = next((h for h in history if h["id"] == entry_id), None)
    if not entry:
        return {"ok": False, "error": "Not found"}
    sess.prog.load(entry["program"])
    return {"ok": True, "program": entry["program"]}


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
    try:
        gpx_bytes = await file.read()
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
        state["emu_incline"] = max(0, min(int(incline), MAX_INCLINE))
        try:
            client.set_speed(speed)
            client.set_incline(incline)
        except ConnectionError:
            log.warning("Cannot apply program change: treadmill_io disconnected")
        await broadcast_status()

    return on_change


def _prog_on_update():
    """Return an on_update callback for program execution."""

    async def on_update(prog_state):
        await manager.broadcast(prog_state)
        # When program completes, stop the treadmill and end the session
        if prog_state.get("completed") and not prog_state.get("running"):
            state["emu_speed"] = 0
            state["emu_incline"] = 0
            try:
                client.set_speed(0)
                client.set_incline(0)
            except ConnectionError:
                pass
            if sess.active:
                sess.end("user_stop")
                await manager.broadcast(sess.to_dict())
            await broadcast_status()

    return on_update


async def _exec_fn(name, args):
    """Execute a treadmill function call from Gemini."""
    if name == "set_speed":
        mph = float(args.get("mph", 0))
        await _apply_speed(mph)
        return f"Speed set to {mph} mph"

    elif name == "set_incline":
        inc = int(args.get("incline", 0))
        await _apply_incline(inc)
        return f"Incline set to {inc}%"

    elif name == "start_workout":
        desc = args.get("description", "")
        try:
            program = await generate_program(desc)
            sess.prog.load(program)
            _add_to_history(program, desc)
            await sess.start_program(_prog_on_change(), _prog_on_update())
            n = len(program["intervals"])
            mins = sum(iv["duration"] for iv in program["intervals"]) // 60
            return f"Started '{program['name']}': {n} intervals, {mins} min"
        except Exception as e:
            return f"Failed: {e}"

    elif name == "stop_treadmill":
        await _apply_stop()
        return "Treadmill stopped"

    elif name == "pause_program":
        if sess.prog.running:
            await sess.prog.toggle_pause()
            if sess.prog.paused:
                # Same as api_pause_program: stop belt, pause session timer
                sess.pause()
                state["_paused_speed"] = state["emu_speed"]
                state["emu_speed"] = 0
                try:
                    client.set_speed(0)
                except ConnectionError:
                    pass
                await broadcast_status()
                return "Program paused"
            else:
                # Resume: session timer resumes, speed restored by on_change
                sess.resume()
                return "Program resumed"
        return "No program running"

    elif name == "resume_program":
        if sess.prog.paused:
            await sess.prog.toggle_pause()
            sess.resume()
            return "Program resumed"
        return "No paused program"

    elif name == "skip_interval":
        if sess.prog.running:
            await sess.prog.skip()
            iv = sess.prog.current_iv
            return f"Skipped to: {iv['name']}" if iv else "Program complete"
        return "No program running"

    elif name == "extend_interval":
        secs = int(args.get("seconds", 0))
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

    return f"Unknown function: {name}"


def _build_chat_system(smartass=False):
    """Build the system prompt with current treadmill state context."""
    treadmill_state = {
        "speed_mph": state["emu_speed"] / 10,
        "incline_pct": state["emu_incline"],
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
        }

    history = _load_history()
    history_summary = ""
    if history:
        names = [h["program"].get("name", "?") for h in history[:5]]
        history_summary = f"\n\nRecent programs: {', '.join(names)}"

    base_prompt = CHAT_SYSTEM_PROMPT + (SMARTASS_ADDENDUM if smartass else "")
    return f"{base_prompt}{history_summary}\n\nCurrent state:\n{json.dumps(treadmill_state)}"


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
        import base64

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
    static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__) or ".", "static"))
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
    cert = os.path.join(os.path.dirname(__file__) or ".", "cert.pem")
    key = os.path.join(os.path.dirname(__file__) or ".", "key.pem")
    if os.path.isfile(cert) and os.path.isfile(key):
        ssl_args = {"ssl_keyfile": key, "ssl_certfile": cert}
        log.info("HTTPS enabled (cert.pem + key.pem)")
    uvicorn.run(app, host="0.0.0.0", port=8000, **ssl_args)
