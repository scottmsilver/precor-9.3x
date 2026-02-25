#!/usr/bin/env python3
"""
Replay recorded PCM audio against Gemini Live to test VAD latency.

Records from Android: 16kHz mono PCM16 LE, saved to /sdcard/Download/voice_recording.pcm
Pull with: adb pull /sdcard/Download/voice_recording.pcm tests/

Usage:
    python3 tests/voice_latency_replay.py [options] <pcm_file>

Options:
    --silence-ms N       silence_duration_ms for VAD (default: 200)
    --end-sensitivity S  END_SENSITIVITY_HIGH or END_SENSITIVITY_LOW (default: HIGH)
    --chunk-samples N    samples per chunk sent to Gemini (default: 4096)
    --manual-vad         disable auto VAD, send activityStart/activityEnd based on RMS
    --rms-threshold N    RMS threshold for speech detection (default: 800)
    --model M            Gemini model (default: gemini-2.5-flash-native-audio-latest)
    --voice V            voice name (default: Kore)
    --no-think           disable thinking (set thinking_budget=0) to reduce latency
    --system-prompt S    override the default system prompt
"""

import argparse
import asyncio
import base64
import json
import math
import struct
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets", file=sys.stderr)
    sys.exit(1)


GEMINI_WS_BASE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
)
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

VOICE_SYSTEM_PROMPT = (
    "You are a helpful treadmill voice assistant. "
    "Keep responses very brief (1-2 sentences). "
    "The user is exercising and can't read long text."
)


def load_api_key():
    """Get ephemeral token from the treadmill server (same as Android app)."""
    import ssl
    import urllib.request

    # Try server first (ephemeral token required for Gemini Live)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for host in ["rpi:8000", "localhost:8000"]:
        try:
            url = f"https://{host}/api/config"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=5, context=ctx)
            config = json.loads(resp.read())
            token = config.get("gemini_api_key", "")
            if token:
                print(f"Got ephemeral token from {host}")
                return token
        except Exception:
            continue

    print("Could not get ephemeral token from server", file=sys.stderr)
    sys.exit(1)


def compute_rms(pcm_bytes: bytes) -> int:
    samples = struct.unpack(f"<{len(pcm_bytes) // 2}h", pcm_bytes)
    if not samples:
        return 0
    sum_sq = sum(s * s for s in samples)
    return int(math.sqrt(sum_sq / len(samples)))


def build_setup(args, api_key):
    gen_config = {
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": args.voice}}},
        "response_modalities": ["AUDIO"],
    }

    # Disable thinking/reasoning to reduce latency
    if args.no_think:
        gen_config["thinking_config"] = {"thinking_budget": 0}

    setup = {
        "setup": {
            "model": f"models/{args.model}",
            "system_instruction": {"parts": [{"text": args.system_prompt or VOICE_SYSTEM_PROMPT}]},
            "generation_config": gen_config,
        }
    }

    if args.manual_vad:
        setup["setup"]["realtime_input_config"] = {"automatic_activity_detection": {"disabled": True}}
    else:
        setup["setup"]["realtime_input_config"] = {
            "automatic_activity_detection": {
                "end_of_speech_sensitivity": f"END_SENSITIVITY_{args.end_sensitivity}",
                "silence_duration_ms": args.silence_ms,
            }
        }

    return setup


async def replay(args):
    api_key = load_api_key()
    pcm_data = Path(args.pcm_file).read_bytes()
    chunk_bytes = args.chunk_samples * BYTES_PER_SAMPLE
    total_chunks = len(pcm_data) // chunk_bytes
    chunk_duration_s = args.chunk_samples / SAMPLE_RATE
    total_duration_s = len(pcm_data) / (SAMPLE_RATE * BYTES_PER_SAMPLE)

    print(f"PCM file: {args.pcm_file}")
    print(
        f"  Duration: {total_duration_s:.1f}s, {total_chunks} chunks of {args.chunk_samples} samples ({chunk_duration_s*1000:.0f}ms)"
    )
    print(
        f"  VAD: {'manual' if args.manual_vad else 'auto'}, silence_ms={args.silence_ms}, end_sensitivity={args.end_sensitivity}"
    )
    print(f"  RMS threshold: {args.rms_threshold}")
    print()

    url = f"{GEMINI_WS_BASE}?access_token={api_key}"

    async with websockets.connect(url, max_size=10_000_000) as ws:
        # Send setup
        setup = build_setup(args, api_key)
        await ws.send(json.dumps(setup))

        # Wait for setupComplete
        while True:
            msg = json.loads(await ws.recv())
            if "setupComplete" in msg or "setup_complete" in msg:
                print("Setup complete, starting replay...")
                break

        # State tracking
        speech_end_time = 0.0
        was_speaking = False
        first_response_time = 0.0
        first_audio_time = 0.0
        turn_audio_chunks = 0
        waiting_for_response = False
        turn_number = 0

        async def receive_loop():
            nonlocal first_response_time, first_audio_time, turn_audio_chunks
            nonlocal waiting_for_response, turn_number, speech_end_time

            async for raw in ws:
                msg = json.loads(raw)
                now = time.monotonic()

                sc = msg.get("serverContent") or msg.get("server_content")
                if sc is None:
                    # Tool call or other message
                    tc = msg.get("toolCall") or msg.get("tool_call")
                    if tc:
                        print(f"  [tool_call received at +{now - speech_end_time:.3f}s]")
                    continue

                # Interrupted
                if sc.get("interrupted"):
                    print(f"  [INTERRUPTED]")
                    continue

                # Turn complete
                if sc.get("turnComplete") or sc.get("turn_complete"):
                    if first_audio_time > 0 and speech_end_time > 0:
                        perceived = first_audio_time - speech_end_time
                        total = now - speech_end_time
                        print(f"  TURN_COMPLETE: {total*1000:.0f}ms total, {turn_audio_chunks} audio chunks")
                    first_response_time = 0.0
                    first_audio_time = 0.0
                    turn_audio_chunks = 0
                    waiting_for_response = False
                    continue

                # Model turn
                mt = sc.get("modelTurn") or sc.get("model_turn")
                if mt:
                    parts = mt.get("parts", [])
                    for part in parts:
                        # First response of any kind
                        if first_response_time == 0.0 and waiting_for_response:
                            first_response_time = now
                            delta = (now - speech_end_time) * 1000 if speech_end_time > 0 else -1
                            print(f"  FIRST_RESPONSE: {delta:.0f}ms after speech ended")

                        # Text
                        text = part.get("text")
                        if text:
                            print(f"  [text: {text[:80]}]")

                        # Audio
                        inline = part.get("inlineData") or part.get("inline_data")
                        if inline and inline.get("data"):
                            turn_audio_chunks += 1
                            if first_audio_time == 0.0:
                                first_audio_time = now
                                delta = (now - speech_end_time) * 1000 if speech_end_time > 0 else -1
                                print(f"  FIRST_AUDIO: {delta:.0f}ms after speech ended  *** PERCEIVED LATENCY ***")

        # Start receiver
        recv_task = asyncio.create_task(receive_loop())

        # Stream audio chunks at real-time rate
        print()
        for i in range(total_chunks):
            offset = i * chunk_bytes
            chunk = pcm_data[offset : offset + chunk_bytes]
            rms = compute_rms(chunk)
            is_speech = rms >= args.rms_threshold

            # Speech/silence transitions
            if is_speech and not was_speaking:
                was_speaking = True
                turn_number += 1
                print(f"--- Turn {turn_number} ---")
                print(f"  SPEECH_START at chunk {i} (rms={rms})")
                if args.manual_vad:
                    await ws.send(json.dumps({"realtimeInput": {"activityStart": {}}}))

            elif not is_speech and was_speaking:
                # Check consecutive silence
                # Simple: mark end immediately (matches SILENCE_CHUNKS_REQUIRED=2 at ~512ms)
                # We already have the chunk, check the next one too
                next_offset = (i + 1) * chunk_bytes
                if next_offset + chunk_bytes <= len(pcm_data):
                    next_chunk = pcm_data[next_offset : next_offset + chunk_bytes]
                    next_rms = compute_rms(next_chunk)
                    if next_rms < args.rms_threshold:
                        was_speaking = False
                        speech_end_time = time.monotonic()
                        waiting_for_response = True
                        print(f"  SPEECH_END at chunk {i} (rms={rms})")
                        if args.manual_vad:
                            await ws.send(json.dumps({"realtimeInput": {"activityEnd": {}}}))

            # Send audio
            b64 = base64.b64encode(chunk).decode()
            msg = {
                "realtimeInput": {
                    "mediaChunks": [
                        {
                            "mimeType": "audio/pcm;rate=16000",
                            "data": b64,
                        }
                    ]
                }
            }
            await ws.send(json.dumps(msg))

            # Real-time pacing
            await asyncio.sleep(chunk_duration_s)

        # Wait for final response
        print("\n--- Audio stream complete, waiting for final response... ---")
        try:
            await asyncio.wait_for(asyncio.shield(recv_task), timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(description="Replay PCM audio against Gemini Live for latency testing")
    parser.add_argument("pcm_file", help="Path to 16kHz mono PCM16 LE file")
    parser.add_argument("--silence-ms", type=int, default=200, help="silence_duration_ms (default: 200)")
    parser.add_argument("--end-sensitivity", default="HIGH", choices=["HIGH", "LOW"], help="end_of_speech_sensitivity")
    parser.add_argument("--chunk-samples", type=int, default=4096, help="samples per chunk (default: 4096)")
    parser.add_argument("--manual-vad", action="store_true", help="disable auto VAD, send manual activity signals")
    parser.add_argument("--rms-threshold", type=int, default=800, help="RMS threshold for speech (default: 800)")
    parser.add_argument("--model", default="gemini-2.5-flash-native-audio-latest", help="Gemini model")
    parser.add_argument("--voice", default="Kore", help="Voice name")
    parser.add_argument("--no-think", action="store_true", help="disable thinking (thinking_budget=0)")
    parser.add_argument("--system-prompt", default=None, help="override system prompt")
    args = parser.parse_args()

    asyncio.run(replay(args))


if __name__ == "__main__":
    main()
