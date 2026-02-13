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
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from program_engine import CHAT_SYSTEM_PROMPT, TOOL_DECLARATIONS, ProgramState, call_gemini, generate_program
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
            state["proxy"] = msg.get("proxy", False)
            state["emulate"] = msg.get("emulate", False)
            state["emu_speed"] = msg.get("emu_speed", 0)
            state["emu_incline"] = msg.get("emu_incline", 0)
            push_msg(msg)

    client.on_message = on_message

    try:
        client.connect()
        log.info("Connected to treadmill_io")
    except Exception as e:
        log.error(f"Cannot connect to treadmill_io: {e}")
        raise RuntimeError("treadmill_io not running. Start: sudo ./treadmill_io")

    broadcast_task = asyncio.create_task(broadcast_loop())

    log.info("Server started — open http://<host>:8000 in browser")

    yield

    # Shutdown
    state["running"] = False
    broadcast_task.cancel()
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
}

latest = {
    "last_console": {},
    "last_motor": {},
}

chat_history: list = []


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


# --- REST endpoints ---


@app.get("/api/status")
async def get_status():
    return build_status()


@app.post("/api/speed")
async def set_speed(req: SpeedRequest):
    state["emu_speed"] = max(0, min(int(req.value * 10), MAX_SPEED_TENTHS))
    client.set_speed(req.value)
    await broadcast_status()
    return build_status()


@app.post("/api/incline")
async def set_incline(req: InclineRequest):
    state["emu_incline"] = max(0, min(req.value, MAX_INCLINE))
    client.set_incline(req.value)
    await broadcast_status()
    return build_status()


@app.post("/api/emulate")
async def set_emulate(req: EmulateRequest):
    if req.enabled:
        state["proxy"] = False
        state["emulate"] = True
        client.set_emulate(True)
    else:
        state["emulate"] = False
        client.set_emulate(False)
    await broadcast_status()
    return build_status()


@app.post("/api/proxy")
async def set_proxy(req: ProxyRequest):
    if req.enabled:
        state["emulate"] = False
        state["proxy"] = True
        client.set_proxy(True)
    else:
        state["proxy"] = False
        client.set_proxy(False)
    await broadcast_status()
    return build_status()


# --- Program endpoints ---


@app.post("/api/program/generate")
async def api_generate_program(req: GenerateRequest):
    try:
        program = await generate_program(req.prompt)
        prog.load(program)
        return {"ok": True, "program": program}
    except Exception as e:
        log.error(f"Program generation failed: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/program/start")
async def api_start_program():
    if not prog.program:
        return {"ok": False, "error": "No program loaded"}
    state["proxy"] = False
    state["emulate"] = True
    client.set_emulate(True)
    await broadcast_status()
    await prog.start(_prog_on_change(), _prog_on_update())
    return prog.to_dict()


@app.post("/api/program/stop")
async def api_stop_program():
    await prog.stop()
    state["emu_speed"] = 0
    state["emu_incline"] = 0
    client.set_speed(0)
    client.set_incline(0)
    await broadcast_status()
    return prog.to_dict()


@app.post("/api/program/pause")
async def api_pause_program():
    await prog.toggle_pause()
    return prog.to_dict()


@app.post("/api/program/skip")
async def api_skip_program():
    await prog.skip()
    return prog.to_dict()


@app.get("/api/program")
async def api_get_program():
    return prog.to_dict()


# --- Chat endpoint (agentic Gemini) ---


def _prog_on_change():
    """Return an on_change callback for program execution."""

    async def on_change(speed, incline):
        state["emu_speed"] = max(0, min(int(speed * 10), MAX_SPEED_TENTHS))
        state["emu_incline"] = max(0, min(int(incline), MAX_INCLINE))
        client.set_speed(speed)
        client.set_incline(incline)
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
        state["emu_speed"] = max(0, min(int(mph * 10), MAX_SPEED_TENTHS))
        client.set_speed(mph)
        if not state["emulate"]:
            state["proxy"] = False
            state["emulate"] = True
            client.set_emulate(True)
        await broadcast_status()
        return f"Speed set to {mph} mph"

    elif name == "set_incline":
        inc = int(args.get("incline", 0))
        state["emu_incline"] = max(0, min(inc, MAX_INCLINE))
        client.set_incline(inc)
        if not state["emulate"]:
            state["proxy"] = False
            state["emulate"] = True
            client.set_emulate(True)
        await broadcast_status()
        return f"Incline set to {inc}%"

    elif name == "start_workout":
        desc = args.get("description", "")
        try:
            program = await generate_program(desc)
            prog.load(program)
            state["proxy"] = False
            state["emulate"] = True
            client.set_emulate(True)
            await broadcast_status()
            await prog.start(_prog_on_change(), _prog_on_update())
            n = len(program["intervals"])
            mins = sum(iv["duration"] for iv in program["intervals"]) // 60
            return f"Started '{program['name']}': {n} intervals, {mins} min"
        except Exception as e:
            return f"Failed: {e}"

    elif name == "stop_treadmill":
        if prog and prog.running:
            await prog.stop()
        state["emu_speed"] = 0
        state["emu_incline"] = 0
        client.set_speed(0)
        client.set_incline(0)
        await broadcast_status()
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

    return f"Unknown function: {name}"


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    global chat_history

    # Build treadmill state context
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
            "interval": prog.current_iv.get("name") if prog.current_iv else None,
            "elapsed": prog.total_elapsed,
            "remaining": prog.total_duration - prog.total_elapsed,
        }

    system = f"{CHAT_SYSTEM_PROMPT}\n\nCurrent state:\n{json.dumps(treadmill_state)}"

    chat_history.append({"role": "user", "parts": [{"text": req.message}]})

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


# --- WebSocket endpoint ---


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(build_status()))
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
