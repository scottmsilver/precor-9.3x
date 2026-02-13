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

from treadmill_client import TreadmillClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("treadmill")


@asynccontextmanager
async def lifespan(application):
    global loop, msg_queue, client

    loop = asyncio.get_event_loop()
    msg_queue = asyncio.Queue(maxsize=500)

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
    client.close()
    log.info("Server stopped")


app = FastAPI(title="Treadmill Controller", lifespan=lifespan)

# --- Async bridge ---
loop: asyncio.AbstractEventLoop = None
msg_queue: asyncio.Queue = None
client: TreadmillClient = None

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
    mph = state["emu_speed"] / 10
    return {
        "type": "status",
        "proxy": state["proxy"],
        "emulate": state["emulate"],
        "emu_speed_mph": mph,
        "emu_incline": state["emu_incline"],
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


# --- REST endpoints ---


@app.get("/api/status")
async def get_status():
    return build_status()


@app.post("/api/speed")
async def set_speed(req: SpeedRequest):
    state["emu_speed"] = max(0, min(int(req.value * 10), 120))
    client.set_speed(req.value)
    await broadcast_status()
    return build_status()


@app.post("/api/incline")
async def set_incline(req: InclineRequest):
    state["emu_incline"] = max(0, min(req.value, 99))
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
