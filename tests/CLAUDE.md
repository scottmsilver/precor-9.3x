# Tests

## Test Tiers

| Tier | Speed | Requires | Run with |
|------|-------|----------|----------|
| Unit | <1s | Nothing (mocked I/O) | `pytest tests/test_program_engine.py tests/test_server_integration.py -v` |
| Live | ~45s | Real `asyncio.sleep` | `pytest tests/test_live_program.py -v` |
| Hardware | ~10s | Pi + treadmill powered on | `make test-pi` |
| Voice | ~15min | Gemini API key | `pytest -m voice -v -s` |
| Intent | ~20s | Gemini API key | `pytest tests/test_extract_intent.py -v -s` |

Run all non-hardware Python tests: `pytest -m "not hardware" -v`

Filter by category: `pytest tests/test_voice_commands.py --voice-category=speed`

Filter by test name: `pytest -k "speed_set_5"`

## File Roles

| File | Purpose |
|------|---------|
| `conftest.py` | Shared fixtures (if present) |
| `voice_test_cases.py` | Canonical test case data (35 cases with prompts, expected calls, alt_calls) |
| `generate_voice_audio.py` | TTS audio generation script — generates and caches PCM audio |
| `test_voice_commands.py` | End-to-end Gemini Live voice tests with intent extraction fallback |
| `test_extract_intent.py` | Unit tests for `extract_intent_from_text()` using real narration samples |
| `test_program_engine.py` | Unit tests for ProgramState and interval logic |
| `test_server_integration.py` | Integration tests for server endpoints |
| `test_live_program.py` | Live timing tests with real `asyncio.sleep` |
| `voice_audio/` | Cached PCM audio files (24kHz, 16-bit mono) from TTS |

## C++ Tests

C++ tests live in `src/tests/` (not here). Built by `make test`, use doctest. See `src/tests/*.cpp`.

## Voice Test Conventions

- **Retries**: Tests retry up to 2 times due to Gemini non-determinism
- **Audio caching**: TTS output cached in `voice_audio/*.pcm` — delete to regenerate
- **`alt_calls`**: Some test cases accept alternate valid behaviors (e.g., `stop_treadmill` instead of `set_speed(0)`)
- **TTS tip**: Spell out numbers in prompts ("three point five" not "3.5") to avoid `finishReason=OTHER`
- **"Thinks aloud" bug**: ~25% of Gemini Live tests produce text narration instead of tool calls; tests use `extract_intent_from_text()` as fallback
