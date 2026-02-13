#!/usr/bin/env python3
"""
Treadmill Web Server — FastAPI + WebSocket bridge to GPIO serial I/O.

Reads treadmill KV protocol via GPIO pins (RS-485, inverted polarity)
and serves a web UI for monitoring and control.

Usage:
    sudo pigpiod
    python3 server.py
    # Open http://<pi-ip>:8000 on phone
"""

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager

import pigpio
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gpio_pins import (
    get_gpio, BAUD, parse_kv_stream, build_kv_cmd,
    gpio_read_open, gpio_read_close, gpio_write_open, gpio_write_close,
    gpio_write_bytes, KV_CYCLE, KV_BURSTS,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("treadmill")



@asynccontextmanager
async def lifespan(application):
    global loop, msg_queue, pi

    loop = asyncio.get_event_loop()
    msg_queue = asyncio.Queue(maxsize=500)

    # Connect to pigpiod
    pi = pigpio.pi()
    if not pi.connected:
        log.error("Cannot connect to pigpiod. Run: sudo pigpiod")
        raise RuntimeError("pigpiod not running")

    gpio_console = get_gpio('console_read')
    gpio_write = get_gpio('motor_write')
    gpio_motor = get_gpio('motor_read')

    # Setup read pins (inverted for RS-485)
    gpio_read_open(pi, gpio_console)
    log.info(f"Console read: GPIO {gpio_console}")

    gpio_read_open(pi, gpio_motor)
    log.info(f"Motor read: GPIO {gpio_motor}")

    # Setup write pin
    gpio_write_open(pi, gpio_write)
    log.info(f"Motor write: GPIO {gpio_write}")

    state['start'] = time.time()

    # Start reader threads
    threading.Thread(target=console_read_thread,
                     args=(pi, gpio_console, gpio_write),
                     daemon=True).start()
    threading.Thread(target=motor_read_thread,
                     args=(pi, gpio_motor),
                     daemon=True).start()

    broadcast_task = asyncio.create_task(broadcast_loop())

    log.info("Server started — open http://<host>:8000 in browser")

    yield

    # Shutdown
    state['running'] = False
    broadcast_task.cancel()
    gpio_read_close(pi, gpio_console)
    gpio_read_close(pi, gpio_motor)
    gpio_write_close(pi, gpio_write)
    pi.stop()
    log.info("Server stopped")


app = FastAPI(title="Treadmill Controller", lifespan=lifespan)

# --- Async bridge ---
loop: asyncio.AbstractEventLoop = None
msg_queue: asyncio.Queue = None
pi: pigpio.pi = None
write_lock = threading.Lock()

# --- Shared state ---
state = {
    'running': True,
    'proxy': True,
    'emulate': False,
    'emu_speed': 0,       # tenths of mph
    'emu_speed_raw': 0,   # hundredths, sent as hex
    'emu_incline': 0,
    'start': time.time(),
    'console_bytes': 0,
    'motor_bytes': 0,
}

latest = {
    'last_console': {},
    'last_motor': {},
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


# --- GPIO threads ---

def console_read_thread(pi_inst, gpio_read, gpio_write):
    """Read KV from console side. Proxy-forward to motor if enabled."""
    buf = bytearray()
    while state['running']:
        count, data = pi_inst.bb_serial_read(gpio_read)
        if count > 0:
            state['console_bytes'] += count
            if state['proxy']:
                gpio_write_bytes(pi_inst, gpio_write, data, write_lock)
            buf.extend(data)
            pairs, buf = parse_kv_stream(buf)
            if pairs:
                now = time.time() - state['start']
                for key, val in pairs:
                    latest['last_console'][key] = val
                    push_msg({
                        'type': 'kv',
                        'ts': round(now, 2),
                        'key': key,
                        'value': val,
                        'source': 'console',
                    })
        else:
            time.sleep(0.02)


def motor_read_thread(pi_inst, gpio):
    """Read KV responses from motor on pin 3."""
    buf = bytearray()
    while state['running']:
        count, data = pi_inst.bb_serial_read(gpio)
        if count > 0:
            state['motor_bytes'] += count
            buf.extend(data)
            pairs, buf = parse_kv_stream(buf)
            if pairs:
                now = time.time() - state['start']
                for key, val in pairs:
                    latest['last_motor'][key] = val
                    push_msg({
                        'type': 'motor',
                        'ts': round(now, 2),
                        'key': key,
                        'value': val,
                    })
        else:
            time.sleep(0.02)


def emulate_thread_fn():
    """Send KV cycle to motor, emulating the controller."""
    gpio_write = get_gpio('motor_write')
    while state['running'] and state['emulate']:
        for burst in KV_BURSTS:
            if not state['running'] or not state['emulate']:
                return
            for idx in burst:
                if not state['running'] or not state['emulate']:
                    return
                key, val_fn = KV_CYCLE[idx]
                value = val_fn(state) if val_fn else None
                cmd = build_kv_cmd(key, value)
                gpio_write_bytes(pi, gpio_write, cmd, write_lock)
                now = time.time() - state['start']
                val_str = f'{value}' if value is not None else ''
                push_msg({
                    'type': 'kv',
                    'ts': round(now, 2),
                    'key': key,
                    'value': val_str,
                    'source': 'emulate',
                })
            time.sleep(0.1)


emu_thread_ref = None


def start_emulate():
    global emu_thread_ref
    if emu_thread_ref and emu_thread_ref.is_alive():
        return
    emu_thread_ref = threading.Thread(target=emulate_thread_fn, daemon=True)
    emu_thread_ref.start()


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
    mph = state['emu_speed'] / 10
    return {
        'type': 'status',
        'proxy': state['proxy'],
        'emulate': state['emulate'],
        'emu_speed_mph': mph,
        'emu_incline': state['emu_incline'],
        'motor': latest['last_motor'],
    }


async def broadcast_status():
    await manager.broadcast(build_status())


async def broadcast_loop():
    while state['running']:
        try:
            msg = await asyncio.wait_for(msg_queue.get(), timeout=0.5)
            await manager.broadcast(msg)
        except asyncio.TimeoutError:
            pass
        except Exception:
            await asyncio.sleep(0.1)


# --- Pydantic models ---

class SpeedRequest(BaseModel):
    value: float   # mph

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
    state['emu_speed'] = max(0, min(int(req.value * 10), 120))
    state['emu_speed_raw'] = state['emu_speed'] * 10
    await broadcast_status()
    return build_status()


@app.post("/api/incline")
async def set_incline(req: InclineRequest):
    state['emu_incline'] = max(0, min(req.value, 99))
    await broadcast_status()
    return build_status()


@app.post("/api/emulate")
async def set_emulate(req: EmulateRequest):
    if req.enabled:
        state['proxy'] = False
        state['emulate'] = True
        start_emulate()
    else:
        state['emulate'] = False
    await broadcast_status()
    return build_status()


@app.post("/api/proxy")
async def set_proxy(req: ProxyRequest):
    if req.enabled:
        state['emulate'] = False
        state['proxy'] = True
    else:
        state['proxy'] = False
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
