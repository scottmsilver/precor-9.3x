#!/usr/bin/env python3
"""
Interval training program engine with Gemini AI generation.

Manages program generation via Google Gemini API and real-time
program execution with timed speed/incline changes.
"""

import asyncio
import json
import logging
import os
import urllib.request

log = logging.getLogger("program")

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = """You are a treadmill interval training program designer. Generate structured workout programs as JSON.

Output a JSON object with these fields:
- "name": short motivating name (max 40 chars)
- "intervals": array of objects, each with:
  - "name": short label (e.g. "Warmup", "Sprint", "Hill Climb", "Recovery", "Cooldown")
  - "duration": seconds (integer, min 10)
  - "speed": mph (float, 0.5 to 12.0)
  - "incline": percent (integer, 0 to 15)

Rules:
- Always start with a warmup (2-5 min, low speed/incline)
- Always end with a cooldown (2-5 min, decreasing speed)
- Speed range: 0.5-12.0 mph. Incline range: 0-15
- Match the requested total duration closely
- Give intervals short, motivating names
- For walking workouts (<=4 mph), vary incline for intensity
- For running (>4 mph), vary speed and incline
- Return ONLY valid JSON"""


def _read_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip()
    for path in [".gemini_key", os.path.expanduser("~/.gemini_key")]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return None


class ProgramState:
    """Manages interval training program execution."""

    def __init__(self):
        self.program = None
        self.running = False
        self.paused = False
        self.completed = False
        self.current_interval = 0
        self.interval_elapsed = 0
        self.total_elapsed = 0
        self._task = None
        self._on_change = None
        self._on_update = None

    @property
    def total_duration(self):
        if not self.program:
            return 0
        return sum(iv["duration"] for iv in self.program["intervals"])

    @property
    def current_iv(self):
        if not self.program or self.current_interval >= len(self.program["intervals"]):
            return None
        return self.program["intervals"][self.current_interval]

    def to_dict(self):
        return {
            "type": "program",
            "program": self.program,
            "running": self.running,
            "paused": self.paused,
            "completed": self.completed,
            "current_interval": self.current_interval,
            "interval_elapsed": self.interval_elapsed,
            "total_elapsed": self.total_elapsed,
            "total_duration": self.total_duration,
        }

    def load(self, program):
        self._cancel_task()
        self.program = program
        self.running = False
        self.paused = False
        self.completed = False
        self.current_interval = 0
        self.interval_elapsed = 0
        self.total_elapsed = 0

    async def start(self, on_change, on_update):
        await self.stop()
        if not self.program:
            return
        self._on_change = on_change
        self._on_update = on_update
        self.running = True
        self.paused = False
        self.completed = False
        self.current_interval = 0
        self.interval_elapsed = 0
        self.total_elapsed = 0
        iv = self.current_iv
        if iv and self._on_change:
            await self._on_change(iv["speed"], iv["incline"])
        await self._broadcast()
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self):
        self._cancel_task()
        was_running = self.running
        self.running = False
        self.paused = False
        if was_running and self._on_change:
            await self._on_change(0, 0)
        await self._broadcast()

    async def toggle_pause(self):
        self.paused = not self.paused
        await self._broadcast()

    async def skip(self):
        if not self.running:
            return
        self.current_interval += 1
        self.interval_elapsed = 0
        iv = self.current_iv
        if iv:
            if self._on_change:
                await self._on_change(iv["speed"], iv["incline"])
        else:
            await self._finish()
        await self._broadcast()

    async def _finish(self):
        self._cancel_task()
        self.running = False
        self.completed = True
        if self._on_change:
            await self._on_change(0, 0)

    async def _broadcast(self):
        if self._on_update:
            await self._on_update(self.to_dict())

    def _cancel_task(self):
        if self._task:
            self._task.cancel()
            self._task = None

    async def _tick_loop(self):
        try:
            while self.running:
                await asyncio.sleep(1)
                if self.paused:
                    await self._broadcast()
                    continue

                self.interval_elapsed += 1
                self.total_elapsed += 1

                iv = self.current_iv
                if not iv:
                    await self._finish()
                    break

                if self.interval_elapsed >= iv["duration"]:
                    self.current_interval += 1
                    self.interval_elapsed = 0
                    nxt = self.current_iv
                    if nxt:
                        if self._on_change:
                            await self._on_change(nxt["speed"], nxt["incline"])
                    else:
                        await self._finish()
                        break

                await self._broadcast()
        except asyncio.CancelledError:
            pass


async def generate_program(prompt, api_key=None):
    """Call Gemini to generate an interval training program."""
    if not api_key:
        api_key = _read_api_key()
    if not api_key:
        raise ValueError("No Gemini API key. Set GEMINI_API_KEY or create .gemini_key file.")

    url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    def _call():
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    result = await asyncio.to_thread(_call)

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        log.error(f"Gemini response format error: {e}, response: {json.dumps(result)[:500]}")
        raise ValueError(f"Bad Gemini response: {e}")
    try:
        program = json.loads(text)
    except json.JSONDecodeError:
        # Try to salvage truncated JSON by finding the last complete interval
        text = text.rstrip()
        if not text.endswith("}"):
            last_brace = text.rfind("}")
            if last_brace > 0:
                text = text[: last_brace + 1] + "]}"
        program = json.loads(text)

    if "intervals" not in program or not program["intervals"]:
        raise ValueError("Program has no intervals")

    for i, iv in enumerate(program["intervals"]):
        for field in ("duration", "speed", "incline"):
            if field not in iv:
                raise ValueError(f"Interval {i} missing '{field}'")
        iv["speed"] = round(max(0.5, min(12.0, float(iv["speed"]))), 1)
        iv["incline"] = max(0, min(15, int(iv["incline"])))
        iv["duration"] = max(10, int(iv["duration"]))
        if "name" not in iv:
            iv["name"] = f"Interval {i + 1}"

    if "name" not in program:
        program["name"] = "Custom Workout"

    return program


# --- Chat / Agentic Function Calling ---

CHAT_SYSTEM_PROMPT = """You are an AI treadmill coach. You control a Precor treadmill via function calls.
Be brief, friendly, motivating. Respond in 1-3 short sentences max.

Tools:
- set_speed: change speed (mph). Use 0 to stop belt.
- set_incline: change incline (0-15%)
- start_workout: create & start an interval program from a description
- stop_treadmill: emergency stop (speed 0, incline 0, end program)
- pause_program / resume_program: pause/resume interval programs
- skip_interval: skip to next interval

Guidelines:
- For workout requests, use start_workout with a detailed description
- For simple adjustments ("faster", "more incline"), use set_speed/set_incline
- Walking: 2-4 mph. Jogging: 4-6 mph. Running: 6+ mph
- If user says "stop", use stop_treadmill immediately
- Always confirm what you did briefly"""

TOOL_DECLARATIONS = [
    {
        "functionDeclarations": [
            {
                "name": "set_speed",
                "description": "Set treadmill belt speed",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"mph": {"type": "NUMBER", "description": "Speed in mph (0-12)"}},
                    "required": ["mph"],
                },
            },
            {
                "name": "set_incline",
                "description": "Set treadmill incline grade",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"incline": {"type": "NUMBER", "description": "Incline percent (0-15)"}},
                    "required": ["incline"],
                },
            },
            {
                "name": "start_workout",
                "description": "Generate and start an interval training program",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"description": {"type": "STRING", "description": "Workout description"}},
                    "required": ["description"],
                },
            },
            {
                "name": "stop_treadmill",
                "description": "Stop the treadmill and end any running program",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "pause_program",
                "description": "Pause the running interval program",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "resume_program",
                "description": "Resume a paused program",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "skip_interval",
                "description": "Skip to next interval in program",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
        ]
    }
]


async def call_gemini(contents, system_prompt, tools=None, api_key=None):
    """Low-level Gemini API call with optional function calling."""
    if not api_key:
        api_key = _read_api_key()
    if not api_key:
        raise ValueError("No Gemini API key")

    url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
        },
    }
    if tools:
        payload["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    def _call():
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    return await asyncio.to_thread(_call)
