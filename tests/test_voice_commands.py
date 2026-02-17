"""Voice command test harness -- end-to-end Gemini Live function calling tests.

Generates audio from test case prompts via Gemini TTS, sends the audio through
the Gemini Live WebSocket (BidiGenerateContent), and verifies that the correct
function calls are produced.

Requires:
  - Gemini API key in .gemini_key or GEMINI_API_KEY env var
  - Network access to generativelanguage.googleapis.com
  - websockets library (pip install websockets)

Usage:
    python3 -m pytest tests/test_voice_commands.py -v -m voice
    python3 -m pytest tests/test_voice_commands.py -v --voice-category=speed
    python3 -m pytest tests/test_voice_commands.py -v -k "speed_set_5"
"""

import asyncio
import base64
import json
import os
import ssl
import struct
import sys

import pytest

# Add project root to path so we can import program_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from program_engine import CHAT_SYSTEM_PROMPT, TOOL_DECLARATIONS, call_gemini, extract_intent_from_text, read_api_key
from tests.generate_voice_audio import AUDIO_DIR, generate_audio
from tests.voice_test_cases import ALL_TEST_CASES, CATEGORIES

try:
    import websockets
except ImportError:
    websockets = None

# Gemini Live constants
GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
VOICE = "Kore"

# TTS produces 24kHz PCM, but Gemini Live expects 16kHz input.
TTS_SAMPLE_RATE = 24000
LIVE_INPUT_RATE = 16000

# Timeouts
SETUP_TIMEOUT = 15  # seconds to wait for setupComplete
TURN_TIMEOUT = 30  # seconds to wait for turnComplete after sending audio

# Retry configuration for non-deterministic model responses
MAX_RETRIES = 2  # total attempts = MAX_RETRIES + 1

# Simulated treadmill state for the system prompt
MOCK_STATE_CONTEXT = """Speed: 4.0 mph
Incline: 2%
Mode: emulate
Program: "Morning Run" running
Current interval: "Warmup" (4.0 mph, 2%)
Interval time: 30/120s
Total: 30/1800s"""


# ---------------------------------------------------------------------------
# pytest configuration
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    parser.addoption(
        "--voice-category",
        action="store",
        default=None,
        help="Run only voice tests in this category: " + ", ".join(CATEGORIES.keys()),
    )


def pytest_collection_modifyitems(config, items):
    """Filter voice tests by --voice-category if provided."""
    category = config.getoption("--voice-category", default=None)
    if not category or category not in CATEGORIES:
        return
    allowed_ids = {tc["id"] for tc in CATEGORIES[category]}
    deselected = []
    selected = []
    for item in items:
        # Check if this is a parametrized voice test
        if hasattr(item, "callspec") and "test_case" in item.callspec.params:
            tc = item.callspec.params["test_case"]
            if tc.get("id") in allowed_ids:
                selected.append(item)
            else:
                deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


# ---------------------------------------------------------------------------
# Audio resampling: 24kHz -> 16kHz
# ---------------------------------------------------------------------------


def _resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Downsample 24kHz 16-bit PCM to 16kHz using linear interpolation.

    Ratio: 16000/24000 = 2/3. For each output sample at position i,
    the corresponding input position is i * 1.5.
    """
    sample_size = 2
    num_samples = len(pcm_24k) // sample_size
    if num_samples == 0:
        return b""

    samples = struct.unpack(f"<{num_samples}h", pcm_24k[: num_samples * sample_size])
    out_count = int(num_samples * LIVE_INPUT_RATE / TTS_SAMPLE_RATE)
    ratio = TTS_SAMPLE_RATE / LIVE_INPUT_RATE  # 1.5

    out = []
    for i in range(out_count):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < num_samples:
            val = samples[idx] * (1.0 - frac) + samples[idx + 1] * frac
        else:
            val = samples[min(idx, num_samples - 1)]
        out.append(int(max(-32768, min(32767, round(val)))))

    return struct.pack(f"<{len(out)}h", *out)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_audio_path(test_id: str) -> str:
    return os.path.join(AUDIO_DIR, f"{test_id}.pcm")


def _build_system_prompt() -> str:
    """Build the system prompt matching what the real voice client sends."""
    return f"{CHAT_SYSTEM_PROMPT}\n\nCurrent treadmill state:\n{MOCK_STATE_CONTEXT}"


def _build_setup_message() -> dict:
    """Build the Gemini Live setup message matching GeminiLiveClient.ts."""
    # program_engine wraps as [{"functionDeclarations": [...]}],
    # but Live API expects tools: [{"function_declarations": [...]}]
    func_decls = TOOL_DECLARATIONS[0]["functionDeclarations"]

    return {
        "setup": {
            "model": f"models/{LIVE_MODEL}",
            "system_instruction": {"parts": [{"text": _build_system_prompt()}]},
            "tools": [{"function_declarations": func_decls}],
            "generation_config": {
                "temperature": 0.3,
                "speech_config": {
                    "voice_config": {"prebuilt_voice_config": {"voice_name": VOICE}},
                },
                "response_modalities": ["AUDIO"],
            },
        },
    }


def _build_audio_chunks(pcm_16k: bytes, chunk_size: int = 8000) -> list[dict]:
    """Split PCM audio into base64-encoded realtimeInput messages.

    Default chunk_size=8000 bytes = 4000 samples = 0.25s at 16kHz.
    """
    messages = []
    for i in range(0, len(pcm_16k), chunk_size):
        chunk = pcm_16k[i : i + chunk_size]
        b64 = base64.b64encode(chunk).decode("ascii")
        messages.append(
            {
                "realtimeInput": {
                    "mediaChunks": [{"mimeType": "audio/pcm;rate=16000", "data": b64}],
                },
            }
        )
    return messages


# ---------------------------------------------------------------------------
# Gemini Live WebSocket client
# ---------------------------------------------------------------------------


async def run_voice_test(api_key: str, audio_path: str) -> dict:
    """Send audio through Gemini Live and collect function calls.

    Returns:
        {
            "tool_calls": [{"name": str, "args": dict}, ...],
            "text_parts": [str, ...],
        }
    """
    if websockets is None:
        pytest.skip("websockets package required: pip install websockets")

    # Load, resample, and add trailing silence for VAD
    with open(audio_path, "rb") as f:
        pcm_24k = f.read()
    pcm_16k = _resample_24k_to_16k(pcm_24k)
    # 1 second of silence so Gemini's VAD detects end of speech
    pcm_16k += b"\x00" * (LIVE_INPUT_RATE * 2)

    url = f"{GEMINI_WS_URL}?key={api_key}"
    ssl_ctx = ssl.create_default_context()

    tool_calls = []
    text_parts = []

    async with websockets.connect(url, ssl=ssl_ctx, max_size=10 * 1024 * 1024) as ws:
        # 1. Send setup
        await ws.send(json.dumps(_build_setup_message()))

        # 2. Wait for setupComplete
        deadline = asyncio.get_event_loop().time() + SETUP_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for setupComplete")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if "setupComplete" in msg or "setup_complete" in msg:
                break

        # 3. Send audio chunks at near real-time pace (no turnComplete for audio)
        chunks = _build_audio_chunks(pcm_16k)
        for chunk_msg in chunks:
            await ws.send(json.dumps(chunk_msg))
            # ~real-time: 4096 bytes = 2048 samples at 16kHz = 128ms
            await asyncio.sleep(0.064)

        # 4. Collect responses until turnComplete
        deadline = asyncio.get_event_loop().time() + TURN_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)

            # Tool calls
            tc = msg.get("toolCall") or msg.get("tool_call")
            if tc:
                fcs = tc.get("functionCalls") or tc.get("function_calls") or []
                for fc in fcs:
                    tool_calls.append(
                        {
                            "name": fc["name"],
                            "args": fc.get("args", {}),
                        }
                    )
                # Send mock tool responses so Gemini can continue
                responses = [
                    {
                        "name": fc["name"],
                        "response": {"result": "OK"},
                    }
                    for fc in fcs
                ]
                await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
                continue

            # Server content
            sc = msg.get("serverContent") or msg.get("server_content")
            if sc:
                if sc.get("turnComplete") or sc.get("turn_complete"):
                    break
                model_turn = sc.get("modelTurn") or sc.get("model_turn")
                if model_turn and "parts" in model_turn:
                    for part in model_turn["parts"]:
                        if "text" in part:
                            text_parts.append(part["text"])

    return {"tool_calls": tool_calls, "text_parts": text_parts}


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _assert_calls_match(
    expected_calls: list[dict],
    actual_calls: list[dict],
    test_id: str,
):
    """Assert actual function calls match expected, with appropriate tolerances.

    - For compound commands, order doesn't matter (greedy matching).
    - Numeric tolerances: speed +/-0.5, incline +/-1, seconds +/-10.
    - start_workout: just check function name + description arg exists.
    - add_time: check function name + intervals arg exists.
    - no_action: assert zero function calls.
    - edge_speed_over_max: accept either raw value (15) or clamped (12).
    """
    if not expected_calls:
        assert not actual_calls, (
            f"[{test_id}] Expected no function calls but got: " f"{[c['name'] for c in actual_calls]}"
        )
        return

    assert len(actual_calls) >= len(expected_calls), (
        f"[{test_id}] Expected {len(expected_calls)} call(s) "
        f"({[c['name'] for c in expected_calls]}) "
        f"but got {len(actual_calls)} "
        f"({[c['name'] for c in actual_calls]})"
    )

    # Greedy matching: for each expected call, find a matching actual call
    remaining = list(actual_calls)
    for exp in expected_calls:
        matched = False
        for i, act in enumerate(remaining):
            if act["name"] != exp["name"]:
                continue
            if _args_ok(exp, act, test_id):
                remaining.pop(i)
                matched = True
                break
        assert matched, (
            f"[{test_id}] Expected call {exp['name']}({exp['args']}) " f"not found in actual calls: {actual_calls}"
        )


def _args_ok(expected: dict, actual: dict, test_id: str) -> bool:
    """Check if actual args satisfy expected, with per-function tolerances."""
    name = expected["name"]
    exp_args = expected.get("args", {})
    act_args = actual.get("args", {})

    if name == "set_speed":
        exp_mph = exp_args.get("mph", 0)
        act_mph = act_args.get("mph", -999)
        if test_id == "edge_speed_over_max":
            # Gemini might pass raw 15 or clamp to 12; accept either
            return act_mph >= 12.0 or abs(act_mph - 15.0) <= 0.5
        return abs(act_mph - exp_mph) <= 0.5

    if name == "set_incline":
        exp_inc = exp_args.get("incline", 0)
        act_inc = act_args.get("incline", -999)
        return abs(act_inc - exp_inc) <= 1

    if name == "start_workout":
        # Just check function name matched and description arg exists
        return "description" in act_args

    if name == "extend_interval":
        exp_sec = exp_args.get("seconds", 0)
        act_sec = act_args.get("seconds", -999)
        return abs(act_sec - exp_sec) <= 10

    if name == "add_time":
        # Check intervals arg exists and is a list
        intervals = act_args.get("intervals")
        return isinstance(intervals, list) and len(intervals) > 0

    # For stop_treadmill, pause_program, resume_program, skip_interval:
    # name match is sufficient, no args to verify.
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def api_key():
    """Provide Gemini API key or skip all voice tests."""
    key = read_api_key()
    if not key:
        pytest.skip("No Gemini API key available (set GEMINI_API_KEY or create .gemini_key)")
    return key


@pytest.fixture(scope="module")
def audio_files():
    """Generate audio for all test cases up front (cached on disk).

    TTS failures are recorded but don't block other tests.
    """
    os.makedirs(AUDIO_DIR, exist_ok=True)
    paths = {}
    for tc in ALL_TEST_CASES:
        test_id = tc["id"]
        output_path = _get_audio_path(test_id)
        if os.path.exists(output_path):
            paths[test_id] = output_path
            continue
        try:
            generate_audio(tc["prompt"], output_path)
            paths[test_id] = output_path
        except Exception as e:
            print(f"  [TTS failed] {test_id}: {e}")
    return paths


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.voice
@pytest.mark.slow
@pytest.mark.parametrize(
    "test_case",
    ALL_TEST_CASES,
    ids=[tc["id"] for tc in ALL_TEST_CASES],
)
def test_voice_command(test_case, api_key, audio_files):
    """Send a voice command through Gemini Live and verify function calls.

    Retries up to MAX_RETRIES times since Gemini Live responses are
    non-deterministic — the same audio input can produce different
    results across attempts.
    """
    test_id = test_case["id"]
    expected_calls = test_case["expected_calls"]
    alt_calls = test_case.get("alt_calls")

    pcm_path = audio_files.get(test_id)
    if not pcm_path or not os.path.exists(pcm_path):
        pytest.skip(f"Audio generation failed for {test_id}")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        result = asyncio.run(run_voice_test(api_key, pcm_path))

        # Log diagnostics for debugging
        actual = result["tool_calls"]
        text = result["text_parts"]
        prefix = f"  [{test_id}] attempt {attempt + 1}/{MAX_RETRIES + 1}"
        print(f"\n{prefix} tool_calls={[c['name'] + str(c['args']) for c in actual]}")
        if text:
            print(f"{prefix} text={' '.join(text)[:200]}")

        # Intent extraction fallback: if Live produced text and either:
        # - zero tool calls (full "thinks aloud" bug), or
        # - fewer tool calls than expected (partial compound execution)
        needs_fallback = text and expected_calls and (not actual or len(actual) < len(expected_calls))
        if needs_fallback:
            joined = " ".join(text)
            already = [c["name"] for c in actual]
            label = "FALLBACK (partial)" if actual else "FALLBACK (full)"
            print(f"{prefix} {label}: extracting intent from text via Flash (already={already})...")
            try:
                fallback_calls = asyncio.run(extract_intent_from_text(joined, already))
                # Merge: keep Live's calls + add new ones from Flash (skip duplicates)
                for fc in fallback_calls:
                    if fc["name"] not in already:
                        actual.append(fc)
                if fallback_calls:
                    print(f"{prefix} {label} merged: {[c['name'] + str(c['args']) for c in actual]}")
            except Exception as e:
                print(f"{prefix} {label} failed: {e}")

        # Try primary expectations first, fall back to alt_calls if provided
        try:
            _assert_calls_match(expected_calls, actual, test_id)
            return  # passed
        except AssertionError:
            if alt_calls is not None:
                try:
                    _assert_calls_match(alt_calls, actual, test_id)
                    return  # passed on alt
                except AssertionError as e:
                    last_error = e
            else:
                last_error = AssertionError(
                    f"[{test_id}] Expected {[c['name'] for c in expected_calls]} "
                    f"but got {[c['name'] for c in actual]}"
                )

        if attempt < MAX_RETRIES:
            print(f"{prefix} RETRY (model non-determinism)")

    raise last_error
