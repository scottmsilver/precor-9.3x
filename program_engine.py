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
import re

from google import genai
from google.genai import types

log = logging.getLogger("program")

ENCOURAGEMENT_MESSAGES = [
    "Keep it up! You're doing great!",
    "Strong effort! Stay with it!",
    "Looking good! Keep pushing!",
    "You've got this! Stay strong!",
    "Nice work! Keep that pace!",
    "Crushing it! Don't stop now!",
    "Great form! Keep going!",
    "Almost there! Stay focused!",
]

MILESTONE_MESSAGES = {
    25: "Quarter of the way done — strong start!",
    50: "Halfway there! You're killing it!",
    75: "Three quarters done — the finish line is in sight!",
}

GEMINI_MODEL = "gemini-2.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"


def build_tts_config(voice: str = "Kore") -> types.GenerateContentConfig:
    """Build a GenerateContentConfig for Gemini TTS. Shared by server and tests."""
    return types.GenerateContentConfig(
        responseModalities=["AUDIO"],
        speechConfig=types.SpeechConfig(
            voiceConfig=types.VoiceConfig(
                prebuiltVoiceConfig=types.PrebuiltVoiceConfig(
                    voiceName=voice,
                )
            )
        ),
    )


_client: genai.Client | None = None


def get_client() -> genai.Client:
    """Lazy singleton for the Gemini SDK client."""
    global _client
    if _client is None:
        api_key = read_api_key()
        if not api_key:
            raise ValueError("No Gemini API key. Set GEMINI_API_KEY or create .gemini_key file.")
        _client = genai.Client(api_key=api_key)
    return _client


# Application-level limits (hardware supports wider ranges)
MIN_SPEED = 0.5
MAX_SPEED = 12.0
MAX_INCLINE = 15
MIN_DURATION = 10

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


def read_api_key():
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


def validate_interval(iv, index=None):
    """Clamp speed/incline/duration to safe ranges and ensure required fields."""
    for field in ("duration", "speed", "incline"):
        if field not in iv:
            label = f"Interval {index}" if index is not None else "Interval"
            raise ValueError(f"{label} missing '{field}'")
    iv["speed"] = round(max(MIN_SPEED, min(MAX_SPEED, float(iv["speed"]))), 1)
    iv["incline"] = max(0, min(MAX_INCLINE, int(iv["incline"])))
    iv["duration"] = max(MIN_DURATION, int(iv["duration"]))
    if "name" not in iv:
        iv["name"] = f"Interval {index + 1}" if index is not None else "Added"
    return iv


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
        self._encouragement_milestones = set()
        self._last_encouragement_interval = -3
        self._pending_encouragement = None

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
        d = {
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
        if self._pending_encouragement:
            d["encouragement"] = self._pending_encouragement
            self._pending_encouragement = None
        return d

    def load(self, program):
        self._cancel_task()
        self.program = program
        self.running = False
        self.paused = False
        self.completed = False
        self.current_interval = 0
        self.interval_elapsed = 0
        self.total_elapsed = 0
        self._encouragement_milestones = set()
        self._last_encouragement_interval = -3
        self._pending_encouragement = None

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
        self._encouragement_milestones = set()
        self._last_encouragement_interval = -3
        self._pending_encouragement = None
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

    async def reset(self):
        """Full reset: stop, clear program and completed state."""
        self._cancel_task()
        was_running = self.running
        self.program = None
        self.running = False
        self.paused = False
        self.completed = False
        self.current_interval = 0
        self.interval_elapsed = 0
        self.total_elapsed = 0
        self._encouragement_milestones = set()
        self._last_encouragement_interval = -3
        self._pending_encouragement = None
        if was_running and self._on_change:
            await self._on_change(0, 0)
        await self._broadcast()

    async def toggle_pause(self):
        self.paused = not self.paused
        # On resume, re-apply current interval's speed/incline
        if not self.paused and self.running:
            iv = self.current_iv
            if iv and self._on_change:
                await self._on_change(iv["speed"], iv["incline"])
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

    async def prev(self):
        if not self.running:
            return
        if self.current_interval > 0:
            self.current_interval -= 1
        self.interval_elapsed = 0
        iv = self.current_iv
        if iv and self._on_change:
            await self._on_change(iv["speed"], iv["incline"])
        await self._broadcast()

    async def extend_current(self, seconds):
        """Add or subtract seconds from the current interval's duration."""
        if not self.running or not self.current_iv:
            return False
        iv = self.current_iv
        new_dur = iv["duration"] + seconds
        if new_dur < 10:
            new_dur = 10
        iv["duration"] = new_dur
        await self._broadcast()
        return True

    @property
    def is_manual(self):
        return bool(self.program and self.program.get("manual"))

    async def split_for_manual(self, speed, incline):
        """Split current interval in a manual program to record course changes."""
        if not self.running or not self.is_manual or not self.current_iv:
            return False
        iv = self.current_iv
        elapsed = max(1, int(self.interval_elapsed))
        remaining = iv["duration"] - elapsed
        if remaining < 1:
            return False
        # Same values — no split needed
        if abs(iv["speed"] - speed) < 0.05 and iv["incline"] == incline:
            return False
        # Trim current interval to what's been completed
        iv["duration"] = elapsed
        # Count existing manual segments for naming
        seg_num = self.current_interval + 2
        # Insert new interval with remaining time at new settings
        new_iv = {
            "name": f"Seg {seg_num}",
            "duration": remaining,
            "speed": speed,
            "incline": incline,
        }
        self.program["intervals"].insert(self.current_interval + 1, new_iv)
        # Advance to the new interval
        self.current_interval += 1
        self.interval_elapsed = 0
        await self._broadcast()
        return True

    async def adjust_duration(self, delta_seconds):
        """Add or remove time from the manual program's last interval."""
        if not self.running or not self.is_manual or not self.program:
            return False
        intervals = self.program["intervals"]
        if not intervals:
            return False
        last = intervals[-1]
        new_dur = last["duration"] + delta_seconds
        if new_dur < 10:
            new_dur = 10
        last["duration"] = new_dur
        await self._broadcast()
        return True

    async def add_intervals(self, intervals):
        """Append intervals to the running program."""
        if not self.program:
            return False
        for iv in intervals:
            iv.setdefault("speed", 3)
            iv.setdefault("incline", 0)
            iv.setdefault("duration", 60)
            validate_interval(iv)
        self.program["intervals"].extend(intervals)
        await self._broadcast()
        return True

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

    def _check_encouragement(self):
        """Set encouragement message at milestones or every 3 intervals."""
        if not self.program or not self.running:
            return
        td = self.total_duration
        if td <= 0:
            return

        # Milestone check (25/50/75%)
        pct = (self.total_elapsed / td) * 100
        for milestone, msg in MILESTONE_MESSAGES.items():
            if pct >= milestone and milestone not in self._encouragement_milestones:
                self._encouragement_milestones.add(milestone)
                self._pending_encouragement = msg
                return

        # Every 3 intervals
        if (
            (self.current_interval - self._last_encouragement_interval) >= 3
            and self.interval_elapsed == 0
            and self.current_interval > 0
        ):
            self._last_encouragement_interval = self.current_interval
            import random

            self._pending_encouragement = random.choice(ENCOURAGEMENT_MESSAGES)

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

                # Check encouragement milestones
                self._check_encouragement()

                await self._broadcast()
        except asyncio.CancelledError:
            pass


async def generate_program(prompt, api_key=None):
    """Call Gemini to generate an interval training program."""
    if not api_key:
        api_key = read_api_key()
    if not api_key:
        raise ValueError("No Gemini API key. Set GEMINI_API_KEY or create .gemini_key file.")

    contents = [{"parts": [{"text": prompt}]}]
    gen_config = {"responseMimeType": "application/json", "maxOutputTokens": 4096}
    result = await call_gemini(contents, SYSTEM_PROMPT, api_key=api_key, generation_config=gen_config)

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
        validate_interval(iv, index=i)

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
- extend_interval: add or subtract seconds from current interval (positive = longer, negative = shorter)
- add_time: add extra intervals at the end of the current program

CRITICAL RULE — never change speed, incline, or any treadmill setting unless the user explicitly asks you to. Do NOT proactively adjust settings to "push" or "challenge" the user. Only use tools in direct response to a clear user request.

Guidelines:
- For workout requests, use start_workout with a detailed description
- For simple adjustments ("faster", "more incline"), use set_speed/set_incline
- Walking: 2-4 mph. Jogging: 4-6 mph. Running: 6+ mph
- If user says "stop", use stop_treadmill immediately
- For "more time", "extend", "add 5 minutes" etc., use extend_interval or add_time
- extend_interval changes the CURRENT interval's duration (e.g. +60 adds 1 min)
- add_time appends new intervals at the END of the program
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
            {
                "name": "extend_interval",
                "description": "Add or subtract seconds from the current interval duration. Positive = longer, negative = shorter. Min 10s.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "seconds": {"type": "NUMBER", "description": "Seconds to add (positive) or subtract (negative)"}
                    },
                    "required": ["seconds"],
                },
            },
            {
                "name": "add_time",
                "description": "Add extra intervals at the end of the running program",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "intervals": {
                            "type": "ARRAY",
                            "description": "Array of interval objects with name, duration (seconds), speed (mph), incline (%)",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "name": {"type": "STRING"},
                                    "duration": {"type": "NUMBER"},
                                    "speed": {"type": "NUMBER"},
                                    "incline": {"type": "NUMBER"},
                                },
                            },
                        }
                    },
                    "required": ["intervals"],
                },
            },
        ]
    }
]


async def call_gemini(contents, system_prompt, tools=None, api_key=None, generation_config=None):
    """Low-level Gemini API call with optional function calling.

    Returns a dict matching the REST API camelCase format so callers
    don't need to change.
    """
    client = get_client()

    config_kwargs = {"temperature": 0.7, "maxOutputTokens": 1024}
    if generation_config:
        config_kwargs.update(generation_config)
    config_kwargs["systemInstruction"] = system_prompt
    if tools:
        config_kwargs["tools"] = tools

    config = types.GenerateContentConfig(**config_kwargs)

    resp = await client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    return resp.model_dump(by_alias=True, exclude_none=True)


# --- Voice intent extraction ---

INTENT_TOOL_SCHEMA = json.dumps(
    [
        {"name": "set_speed", "args": {"mph": "number (e.g. 3.5)"}},
        {"name": "set_incline", "args": {"incline": "integer 0-15"}},
        {"name": "start_workout", "args": {"description": "string"}},
        {"name": "stop_treadmill", "args": {}},
        {"name": "pause", "args": {}},
        {"name": "resume", "args": {}},
        {"name": "skip_interval", "args": {}},
    ],
    indent=2,
)


async def extract_intent_from_text(text: str, already_executed: list[str] | None = None) -> list[dict]:
    """Extract intended function calls from narration text via Gemini Flash JSON mode.

    When Gemini Live narrates its intent as text instead of emitting a toolCall,
    this function recovers by asking Flash to parse the intent using JSON extraction.

    Returns [{"name": ..., "args": {...}}, ...]. Does not execute anything.
    """
    already = set(already_executed or [])

    if already:
        already_str = f"\n\nThese functions were ALREADY executed — exclude them: {', '.join(already)}"
    else:
        already_str = ""

    prompt = (
        "A voice assistant intended to control a treadmill but narrated its intent as text "
        "instead of making function calls. Extract the intended function calls from the text below.\n\n"
        f"Available functions:\n{INTENT_TOOL_SCHEMA}\n\n"
        'Return a JSON array of objects with "name" and "args" fields. '
        "Return ALL intended function calls. If no functions are intended, return an empty array []."
        f"{already_str}\n\n"
        f'Text: "{text}"'
    )
    contents = [{"role": "user", "parts": [{"text": prompt}]}]

    result = await call_gemini(
        contents,
        "You extract structured data from text. Return valid JSON only.",
        None,
        generation_config={"responseMimeType": "application/json", "temperature": 0},
    )
    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    raw_text = " ".join(p.get("text", "") for p in parts).strip()

    actions = []
    try:
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "actions" in parsed:
            parsed = parsed["actions"]
        if not isinstance(parsed, list):
            parsed = [parsed]
        for item in parsed:
            if not isinstance(item, dict) or "name" not in item:
                continue
            name = item["name"]
            args = item.get("args", {})
            if name in already:
                continue
            # Coerce numeric types
            if name == "set_speed" and "mph" in args:
                args["mph"] = float(args["mph"])
            if name == "set_incline" and "incline" in args:
                args["incline"] = int(float(args["incline"]))
            actions.append({"name": name, "args": args})
    except json.JSONDecodeError:
        # Regex fallback for malformed JSON
        for m in re.finditer(r'"name"\s*:\s*"(\w+)"', raw_text):
            name = m.group(1)
            if name in already:
                continue
            region = raw_text[m.start() : m.start() + 200]
            args = {}
            if name == "set_speed":
                mph_m = re.search(r'"mph"\s*:\s*([\d.]+)', region)
                if mph_m:
                    args["mph"] = float(mph_m.group(1))
            elif name == "set_incline":
                inc_m = re.search(r'"incline"\s*:\s*([\d.]+)', region)
                if inc_m:
                    args["incline"] = int(float(inc_m.group(1)))
            actions.append({"name": name, "args": args})

    return actions
