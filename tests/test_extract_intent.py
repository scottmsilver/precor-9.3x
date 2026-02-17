"""Unit tests for voice intent extraction from 'thinks aloud' text.

Tests the /api/voice/extract-intent endpoint logic using real text samples
observed from Gemini Live's 'thinks aloud' bug, where it narrates its intent
as text instead of emitting toolCall messages.

Runs against the real Gemini API (~2s per test).
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from program_engine import extract_intent_from_text, read_api_key

# Real text samples from Gemini Live sessions
SAMPLES = {
    "compound_speed_incline": {
        "text": (
            "**Setting Treadmill Parameters**\n\n"
            "Okay, I'm ready to proceed! I've determined that I need to utilize "
            "the `set_incline` tool, setting it to 5%, and the `set_speed` tool, "
            "setting it to 4 mph. I will utilize these functions and then I will "
            "confirm the settings to the user."
        ),
        "already_executed": [],
        "expected_names": {"set_speed", "set_incline"},
        "expected_args": {"set_speed": {"mph": 4.0}, "set_incline": {"incline": 5}},
    },
    "compound_partial_speed_done": {
        "text": (
            "**Setting Speed and Incline**\n\n"
            "I've determined I can use the `set_incline` and `set_speed` tools "
            "to meet the user's requirements. Although the current incline is "
            "already at the desired level, I'll explicitly set both incline to 5% "
            "and speed to 4 mph."
        ),
        "already_executed": ["set_speed"],
        "expected_names": {"set_incline"},
        "expected_args": {"set_incline": {"incline": 5}},
    },
    "single_speed": {
        "text": (
            "**Adjusting Speed to Target**\n\n"
            "I'm setting the treadmill speed to 3.5 mph. The current speed, "
            "4.0 mph, is within the acceptable range, and I'll use the "
            "`set_speed` tool with the target value."
        ),
        "already_executed": [],
        "expected_names": {"set_speed"},
        "expected_args": {"set_speed": {"mph": 3.5}},
    },
    "single_incline": {
        "text": (
            "**Setting Incline to 6**\n\n"
            "I've determined that the `set_incline` tool is the correct one to "
            "use. I need to call it with an `incline` parameter of 6."
        ),
        "already_executed": [],
        "expected_names": {"set_incline"},
        "expected_args": {"set_incline": {"incline": 6}},
    },
    "speed_slow_down": {
        "text": (
            "**Adjusting Speed to Target**\n\n"
            "I've decided to reduce my velocity. My current pace is a bit too "
            "brisk, and I intend to decelerate to 3 mph. I plan to use the "
            "`set_speed` tool to achieve this adjustment."
        ),
        "already_executed": [],
        "expected_names": {"set_speed"},
        "expected_args": {"set_speed": {"mph": 3.0}},
    },
    "no_action_greeting": {
        "text": "Hi there! Good morning. Keeping up the pace, I see? How are you feeling?",
        "already_executed": [],
        "expected_names": set(),
        "expected_args": {},
    },
    "vague_start_treadmill": {
        "text": (
            "**Initiating Treadmill Operation**\n\n"
            "I'm ready to start the treadmill! Based on my understanding of "
            "the user's instructions, I will set the speed to 3 mph, which is "
            "a comfortable walking pace."
        ),
        "already_executed": [],
        "expected_names": {"set_speed"},
        "expected_args": {"set_speed": {"mph": 3.0}},
    },
}


@pytest.fixture(scope="session", autouse=True)
def check_api_key():
    if not read_api_key():
        pytest.skip("No Gemini API key available")


@pytest.mark.voice
@pytest.mark.parametrize(
    "sample_name",
    list(SAMPLES.keys()),
)
def test_extract_intent(sample_name):
    """Test that extract_intent correctly recovers function calls from narration text.

    Retries up to 2 times since Gemini responses are non-deterministic.
    """
    sample = SAMPLES[sample_name]
    expected_names = sample["expected_names"]

    last_error = None
    for attempt in range(3):
        actions = asyncio.run(extract_intent_from_text(sample["text"], sample["already_executed"]))
        actual_names = {a["name"] for a in actions}

        try:
            # Check correct function names — expected must be present (extra actions are OK)
            if not expected_names:
                assert not actual_names, f"[{sample_name}] Expected no actions but got {actual_names}"
            else:
                assert (
                    expected_names <= actual_names
                ), f"[{sample_name}] Expected {expected_names} to be in {actual_names}"

            # Check args (with tolerances)
            for action in actions:
                name = action["name"]
                if name not in sample["expected_args"]:
                    continue
                expected = sample["expected_args"][name]
                actual = action["args"]
                for key, exp_val in expected.items():
                    act_val = actual.get(key)
                    assert act_val is not None, f"[{sample_name}] {name}: missing arg '{key}'"
                    if isinstance(exp_val, float):
                        assert (
                            abs(act_val - exp_val) <= 0.5
                        ), f"[{sample_name}] {name}.{key}: expected {exp_val}, got {act_val}"
                    elif isinstance(exp_val, int):
                        assert (
                            abs(act_val - exp_val) <= 1
                        ), f"[{sample_name}] {name}.{key}: expected {exp_val}, got {act_val}"
            return  # passed
        except AssertionError as e:
            last_error = e
            if attempt < 2:
                print(f"  [{sample_name}] attempt {attempt+1}/3 RETRY: {e}")

    raise last_error
