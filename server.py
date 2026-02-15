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
import subprocess
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from program_engine import (
    CHAT_SYSTEM_PROMPT,
    TOOL_DECLARATIONS,
    ProgramState,
    call_gemini,
    generate_program,
    validate_interval,
)
from treadmill_client import MAX_INCLINE, MAX_SPEED_TENTHS, TreadmillClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("treadmill")


@asynccontextmanager
async def lifespan(application):
    global loop, msg_queue, client, prog

    loop = asyncio.get_event_loop()
    msg_queue = asyncio.Queue(maxsize=500)
    prog = ProgramState()

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
            state["emu_speed"] = msg.get("emu_speed", 0)
            state["emu_incline"] = msg.get("emu_incline", 0)
            # Detect watchdog / auto-proxy killing emulate while session active
            if was_emulating and not state["emulate"] and session["active"]:
                reason = "auto_proxy" if state["proxy"] else "watchdog"
                _end_session(reason)
                push_msg(build_session())
            push_msg(msg)

    client.on_message = on_message

    def on_disconnect():
        state["treadmill_connected"] = False
        log.warning("treadmill_io disconnected")
        # End active session
        if session["active"]:
            _end_session("disconnect")
            push_msg(build_session())
        # Push disconnect event to WebSocket clients
        push_msg({"type": "connection", "connected": False})
        # Auto-pause program if running
        if prog and prog.running and not prog.paused:
            asyncio.run_coroutine_threadsafe(prog.toggle_pause(), loop)

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
    if prog and prog.running:
        await prog.stop()
    client.close()
    log.info("Server stopped")


app = FastAPI(title="Treadmill Controller", lifespan=lifespan)

# --- Async bridge ---
loop: asyncio.AbstractEventLoop = None
msg_queue: asyncio.Queue = None
client: TreadmillClient = None
prog: ProgramState = None

# --- Shared state ---
state = {
    "running": True,
    "proxy": True,
    "emulate": False,
    "emu_speed": 0,  # tenths of mph
    "emu_incline": 0,
    "treadmill_connected": False,
}

latest = {
    "last_console": {},
    "last_motor": {},
}

# --- Session state (server-authoritative) ---
session = {
    "active": False,
    "started_at": 0.0,  # monotonic
    "wall_started_at": "",  # ISO wall clock for display
    "paused_at": 0.0,  # monotonic when paused, 0 if not paused
    "total_paused": 0.0,  # accumulated pause seconds
    "elapsed": 0.0,
    "distance": 0.0,  # miles
    "vert_feet": 0.0,
    "end_reason": None,  # "user_stop" | "watchdog" | "auto_proxy" | "disconnect"
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


# --- Session lifecycle ---


def _start_session():
    """Begin a new workout session. Idempotent if already active."""
    if session["active"]:
        return
    session["active"] = True
    session["started_at"] = time.monotonic()
    session["wall_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    session["paused_at"] = 0.0
    session["total_paused"] = 0.0
    session["elapsed"] = 0.0
    session["distance"] = 0.0
    session["vert_feet"] = 0.0
    session["end_reason"] = None
    log.info("Session started")


def _end_session(reason):
    """End the current workout session with a reason."""
    if not session["active"]:
        return
    # Final tick to capture last elapsed/distance
    _session_tick_compute()
    session["active"] = False
    session["end_reason"] = reason
    log.info(f"Session ended: {reason}")


def _session_tick_compute():
    """Compute elapsed/distance/vert from monotonic clock and current speed/incline."""
    if not session["active"]:
        return
    now = time.monotonic()
    if session["paused_at"] > 0:
        # Paused — don't advance
        return
    raw_elapsed = now - session["started_at"] - session["total_paused"]
    session["elapsed"] = max(0.0, raw_elapsed)

    # Accumulate distance and vert from current speed/incline
    speed_mph = state["emu_speed"] / 10
    if speed_mph > 0:
        miles_per_sec = speed_mph / 3600
        session["distance"] += miles_per_sec
        inc = state["emu_incline"]
        if inc > 0:
            session["vert_feet"] += miles_per_sec * (inc / 100) * 5280


def build_session():
    """Build session state dict for WebSocket broadcast."""
    return {
        "type": "session",
        "active": session["active"],
        "elapsed": session["elapsed"],
        "distance": session["distance"],
        "vert_feet": session["vert_feet"],
        "wall_started_at": session["wall_started_at"],
        "end_reason": session["end_reason"],
    }


async def _session_tick_loop():
    """1/sec loop: compute session metrics and broadcast to all WS clients."""
    while state["running"]:
        if session["active"]:
            _session_tick_compute()
            await manager.broadcast(build_session())
        await asyncio.sleep(1)


def _pause_session():
    """Pause session timer (for program pauses)."""
    if session["active"] and session["paused_at"] == 0:
        session["paused_at"] = time.monotonic()


def _resume_session():
    """Resume session timer after pause."""
    if session["active"] and session["paused_at"] > 0:
        session["total_paused"] += time.monotonic() - session["paused_at"]
        session["paused_at"] = 0.0


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


class VoiceChatRequest(BaseModel):
    audio: str  # base64-encoded audio
    mime_type: str = "audio/webm"


class TTSRequest(BaseModel):
    text: str
    voice: str = "Kore"


# --- Shared control helpers ---


async def _apply_speed(mph):
    """Core speed logic shared by REST endpoint and Gemini function calls."""
    state["emu_speed"] = max(0, min(int(mph * 10), MAX_SPEED_TENTHS))
    # Mirror C binary's auto-emulate: sending a speed command enables emulate mode
    if mph > 0:
        state["emulate"] = True
        state["proxy"] = False
        _start_session()
    elif mph == 0 and session["active"]:
        _end_session("user_stop")
        await manager.broadcast(build_session())
    try:
        client.set_speed(mph)
    except ConnectionError:
        log.warning("Cannot set speed: treadmill_io disconnected")
    await broadcast_status()


async def _apply_incline(inc):
    """Core incline logic shared by REST endpoint and Gemini function calls."""
    state["emu_incline"] = max(0, min(inc, MAX_INCLINE))
    # Mirror C binary's auto-emulate: sending an incline command enables emulate mode
    if inc > 0:
        state["emulate"] = True
        state["proxy"] = False
    try:
        client.set_incline(inc)
    except ConnectionError:
        log.warning("Cannot set incline: treadmill_io disconnected")
    await broadcast_status()


async def _apply_stop():
    """Core stop logic shared by REST endpoint and Gemini function calls."""
    if prog and prog.running:
        await prog.stop()
    state["emu_speed"] = 0
    state["emu_incline"] = 0
    if session["active"]:
        _end_session("user_stop")
        await manager.broadcast(build_session())
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
    return build_session()


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


# --- Program endpoints ---


@app.post("/api/program/generate")
async def api_generate_program(req: GenerateRequest):
    try:
        program = await generate_program(req.prompt)
        prog.load(program)
        _add_to_history(program, req.prompt)
        return {"ok": True, "program": program}
    except Exception as e:
        log.error(f"Program generation failed: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/program/start")
async def api_start_program():
    if not prog.program:
        return {"ok": False, "error": "No program loaded"}
    _start_session()
    await prog.start(_prog_on_change(), _prog_on_update())
    return prog.to_dict()


@app.post("/api/program/stop")
async def api_stop_program():
    await _apply_stop()
    return prog.to_dict()


@app.post("/api/program/pause")
async def api_pause_program():
    await prog.toggle_pause()
    if prog.paused:
        _pause_session()
    else:
        _resume_session()
    return prog.to_dict()


@app.post("/api/program/skip")
async def api_skip_program():
    await prog.skip()
    return prog.to_dict()


class ExtendRequest(BaseModel):
    seconds: int


@app.post("/api/program/extend")
async def api_extend_interval(req: ExtendRequest):
    if not prog or not prog.running:
        return {"ok": False, "error": "No program running"}
    ok = await prog.extend_current(req.seconds)
    if ok:
        await manager.broadcast(prog.to_dict())
    return prog.to_dict()


@app.get("/api/program")
async def api_get_program():
    return prog.to_dict()


@app.get("/api/programs/history")
async def api_get_history():
    return _load_history()


@app.post("/api/programs/history/{entry_id}/load")
async def api_load_from_history(entry_id: str):
    history = _load_history()
    entry = next((h for h in history if h["id"] == entry_id), None)
    if not entry:
        return {"ok": False, "error": "Not found"}
    prog.load(entry["program"])
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
        prog.load(program)
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
            prog.load(program)
            _add_to_history(program, desc)
            await prog.start(_prog_on_change(), _prog_on_update())
            n = len(program["intervals"])
            mins = sum(iv["duration"] for iv in program["intervals"]) // 60
            return f"Started '{program['name']}': {n} intervals, {mins} min"
        except Exception as e:
            return f"Failed: {e}"

    elif name == "stop_treadmill":
        await _apply_stop()
        return "Treadmill stopped"

    elif name == "pause_program":
        if prog and prog.running:
            await prog.toggle_pause()
            return "Program paused" if prog.paused else "Program resumed"
        return "No program running"

    elif name == "resume_program":
        if prog and prog.paused:
            await prog.toggle_pause()
            return "Program resumed"
        return "No paused program"

    elif name == "skip_interval":
        if prog and prog.running:
            await prog.skip()
            iv = prog.current_iv
            return f"Skipped to: {iv['name']}" if iv else "Program complete"
        return "No program running"

    elif name == "extend_interval":
        secs = int(args.get("seconds", 0))
        if prog and prog.running:
            ok = await prog.extend_current(secs)
            if ok:
                iv = prog.current_iv
                return f"Interval now {iv['duration']}s ({'+' if secs > 0 else ''}{secs}s)"
            return "No current interval"
        return "No program running"

    elif name == "add_time":
        intervals = args.get("intervals", [])
        if not intervals:
            return "No intervals provided"
        if prog and prog.program:
            ok = await prog.add_intervals(intervals)
            if ok:
                added = sum(iv.get("duration", 0) for iv in intervals)
                return f"Added {len(intervals)} interval(s), {added}s total. Program now {prog.total_duration}s."
            return "Failed to add intervals"
        return "No program loaded"

    return f"Unknown function: {name}"


def _build_chat_system():
    """Build the system prompt with current treadmill state context."""
    treadmill_state = {
        "speed_mph": state["emu_speed"] / 10,
        "incline_pct": state["emu_incline"],
        "mode": "emulate" if state["emulate"] else "proxy" if state["proxy"] else "off",
    }
    if prog and prog.program:
        treadmill_state["program"] = {
            "name": prog.program.get("name"),
            "running": prog.running,
            "paused": prog.paused,
            "current_interval_index": prog.current_interval,
            "interval": prog.current_iv.get("name") if prog.current_iv else None,
            "interval_remaining_s": (prog.current_iv["duration"] - prog.interval_elapsed) if prog.current_iv else 0,
            "elapsed": prog.total_elapsed,
            "remaining": prog.total_duration - prog.total_elapsed,
            "total_intervals": len(prog.program.get("intervals", [])),
        }

    history = _load_history()
    history_summary = ""
    if history:
        names = [h["program"].get("name", "?") for h in history[:5]]
        history_summary = f"\n\nRecent programs: {', '.join(names)}"

    return f"{CHAT_SYSTEM_PROMPT}{history_summary}\n\nCurrent state:\n{json.dumps(treadmill_state)}"


async def _run_chat_core():
    """Run the Gemini function-calling loop using chat_history. Returns response dict."""
    global chat_history

    system = _build_chat_system()
    executed = []

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
        # Clean up broken history
        chat_history = [
            m for m in chat_history if m.get("role") == "user" and "parts" in m and any("text" in p for p in m["parts"])
        ]
        if len(chat_history) > 10:
            chat_history = chat_history[-10:]
        return {"text": f"Error: {e}", "actions": executed}


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    chat_history.append({"role": "user", "parts": [{"text": req.message}]})
    return await _run_chat_core()


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

    result = await _run_chat_core()

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
    from program_engine import GEMINI_API_BASE, GEMINI_MODEL, _read_api_key

    api_key = _read_api_key()
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


TTS_MODEL = "gemini-2.5-flash-preview-tts"


@app.post("/api/tts")
async def api_tts(req: TTSRequest):
    """Generate speech audio from text using Gemini TTS."""
    from program_engine import GEMINI_API_BASE, _read_api_key

    api_key = _read_api_key()
    if not api_key:
        return {"ok": False, "error": "No API key"}

    url = f"{GEMINI_API_BASE}/{TTS_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": req.text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": req.voice,
                    }
                }
            },
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    def _call():
        import urllib.request

        data = json.dumps(payload).encode()
        http_req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(http_req, timeout=30) as resp:
            return json.loads(resp.read())

    try:
        result = await asyncio.to_thread(_call)
        audio_data = result["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        return {
            "ok": True,
            "audio": audio_data,  # base64-encoded PCM 24kHz 16-bit mono
            "sample_rate": 24000,
            "channels": 1,
            "bit_depth": 16,
        }
    except Exception as e:
        log.error(f"TTS failed: {e}")
        return {"ok": False, "error": str(e)}


# --- WebSocket endpoint ---


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(build_status()))
        if session["active"]:
            await ws.send_text(json.dumps(build_session()))
        if prog and prog.program:
            await ws.send_text(json.dumps(prog.to_dict()))
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# Mount static files AFTER api routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
