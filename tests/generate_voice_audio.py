"""Generate test audio files from text using Gemini TTS.

Standalone script and importable module. Generates PCM audio (24kHz, 16-bit mono)
from text prompts via the Gemini TTS API and caches results on disk.

Usage:
    python3 tests/generate_voice_audio.py                    # generate all test cases
    python3 tests/generate_voice_audio.py "set speed to 5"   # generate single phrase
"""

import base64
import json
import os
import sys
import urllib.request

# Add project root to path so we can import program_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from program_engine import read_api_key

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"  # default voice

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "voice_audio")

# Import test cases from the canonical list.
# Support both `python3 -m pytest` (tests.voice_test_cases) and
# standalone `python3 tests/generate_voice_audio.py` (voice_test_cases).
try:
    from tests.voice_test_cases import ALL_TEST_CASES
except ImportError:
    from voice_test_cases import ALL_TEST_CASES

# Test cases: (test_id, text) -- derived from ALL_TEST_CASES
TEST_CASES = [(tc["id"], tc["prompt"]) for tc in ALL_TEST_CASES]


def generate_audio(text: str, output_path: str, voice: str = TTS_VOICE) -> str:
    """Generate PCM audio from text using Gemini TTS and save to output_path.

    Args:
        text: The text to synthesize.
        output_path: Where to save the PCM file (24kHz, 16-bit mono).
        voice: Gemini TTS voice name.

    Returns:
        The output_path on success.

    Raises:
        RuntimeError: If API key is missing or API call fails.
    """
    api_key = read_api_key()
    if not api_key:
        raise RuntimeError("No Gemini API key found. Set GEMINI_API_KEY or create .gemini_key")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    url = f"{GEMINI_API_BASE}/{TTS_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": f"Say the following aloud exactly as written: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice,
                    }
                }
            },
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    try:
        audio_b64 = result["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected API response: {e}\n{json.dumps(result, indent=2)}")

    pcm_bytes = base64.b64decode(audio_b64)
    with open(output_path, "wb") as f:
        f.write(pcm_bytes)

    return output_path


def generate_test_case(test_id: str, text: str, force: bool = False) -> str:
    """Generate audio for a single test case, skipping if cached.

    Returns the output path.
    """
    output_path = os.path.join(AUDIO_DIR, f"{test_id}.pcm")
    if os.path.exists(output_path) and not force:
        print(f"  [cached] {test_id}: {output_path}")
        return output_path

    print(f'  [generating] {test_id}: "{text}"')
    generate_audio(text, output_path)
    size_kb = os.path.getsize(output_path) / 1024
    print(f"  [done] {test_id}: {size_kb:.1f} KB")
    return output_path


def generate_all(force: bool = False) -> list[str]:
    """Generate audio for all test cases. Returns list of output paths."""
    paths = []
    for test_id, text in TEST_CASES:
        paths.append(generate_test_case(test_id, text, force=force))
    return paths


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Single phrase mode
        text = " ".join(sys.argv[1:])
        slug = text.lower().replace(" ", "_")[:40]
        out = os.path.join(AUDIO_DIR, f"{slug}.pcm")
        print(f'Generating audio for: "{text}"')
        generate_audio(text, out)
        size_kb = os.path.getsize(out) / 1024
        print(f"Saved: {out} ({size_kb:.1f} KB)")
    else:
        print(f"Generating {len(TEST_CASES)} test audio files...")
        generate_all()
        print("Done.")
