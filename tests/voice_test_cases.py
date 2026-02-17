"""
Voice test cases for Gemini Live API function calling.

Each test case defines a natural language voice command (prompt) and the
expected function call(s) Gemini should produce. Used by the voice test
harness to generate audio, send it through Gemini Live, and verify the
resulting tool calls.

Structure:
  - id:             short unique identifier
  - prompt:         what the user says aloud
  - expected_calls: list of {name, args} dicts — the function calls we expect
  - description:    what the test verifies

An empty expected_calls list means we expect a text-only response with no
function calls (e.g. informational queries).
"""

# ---------------------------------------------------------------------------
# Speed commands
# ---------------------------------------------------------------------------
SPEED_CASES = [
    {
        "id": "speed_set_5",
        "prompt": "Set the speed to 5 miles per hour",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 5.0}}],
        "description": "Explicit speed set with integer value",
    },
    {
        "id": "speed_set_decimal",
        "prompt": "Set speed to 3.5 mph",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 3.5}}],
        "description": "Speed set with decimal value",
    },
    {
        "id": "speed_go_faster",
        "prompt": "Go faster, like 8 miles per hour",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 8.0}}],
        "description": "Colloquial speed increase with target",
    },
    {
        "id": "speed_slow_down",
        "prompt": "Slow down to 3 mph",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 3.0}}],
        "description": "Speed decrease to explicit target",
    },
    {
        "id": "speed_walking",
        "prompt": "Let's walk at 2 miles per hour",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 2.0}}],
        "description": "Walking pace request",
    },
    {
        "id": "speed_max",
        "prompt": "Set speed to 12",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 12.0}}],
        "description": "Maximum allowed speed",
    },
    {
        "id": "speed_zero",
        "prompt": "Set speed to zero",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 0.0}}],
        "alt_calls": [{"name": "stop_treadmill", "args": {}}],
        "description": "Zero speed — accept set_speed(0) or stop_treadmill",
    },
]

# ---------------------------------------------------------------------------
# Incline commands
# ---------------------------------------------------------------------------
INCLINE_CASES = [
    {
        "id": "incline_set_6",
        "prompt": "Set the incline to 6",
        "expected_calls": [{"name": "set_incline", "args": {"incline": 6}}],
        "description": "Explicit incline set",
    },
    {
        "id": "incline_set_10",
        "prompt": "Put the incline at 10 percent",
        "expected_calls": [{"name": "set_incline", "args": {"incline": 10}}],
        "description": "Incline set with percent phrasing",
    },
    {
        "id": "incline_flatten",
        "prompt": "Flatten it out, no incline",
        "expected_calls": [{"name": "set_incline", "args": {"incline": 0}}],
        "description": "Flatten out means incline 0",
    },
    {
        "id": "incline_max",
        "prompt": "Max incline please",
        "expected_calls": [{"name": "set_incline", "args": {"incline": 15}}],
        "description": "Maximum incline (15)",
    },
]

# ---------------------------------------------------------------------------
# Workout / program generation
# ---------------------------------------------------------------------------
WORKOUT_CASES = [
    {
        "id": "workout_hill",
        "prompt": "Start a 20 minute hill workout",
        "expected_calls": [
            {"name": "start_workout", "args": {"description": "20 minute hill workout"}},
        ],
        "description": "Hill workout program generation",
    },
    {
        "id": "workout_5k",
        "prompt": "Give me a 5K training run",
        "expected_calls": [
            {"name": "start_workout", "args": {"description": "5K training run"}},
        ],
        "description": "5K training program generation",
    },
    {
        "id": "workout_beginner",
        "prompt": "I want a beginner 30 minute walk with some hills",
        "expected_calls": [
            {"name": "start_workout", "args": {"description": "beginner 30 minute walk with some hills"}},
        ],
        "description": "Beginner walking workout with description",
    },
    {
        "id": "workout_hiit",
        "prompt": "Create a 15 minute HIIT sprint interval session",
        "expected_calls": [
            {"name": "start_workout", "args": {"description": "15 minute HIIT sprint interval session"}},
        ],
        "description": "HIIT workout generation",
    },
]

# ---------------------------------------------------------------------------
# Stop / pause / resume
# ---------------------------------------------------------------------------
STOP_PAUSE_CASES = [
    {
        "id": "stop",
        "prompt": "Stop",
        "expected_calls": [{"name": "stop_treadmill", "args": {}}],
        "description": "Simple stop command triggers stop_treadmill",
    },
    {
        "id": "stop_everything",
        "prompt": "Stop the treadmill",
        "expected_calls": [{"name": "stop_treadmill", "args": {}}],
        "description": "Explicit stop treadmill command",
    },
    {
        "id": "emergency_stop",
        "prompt": "Emergency stop now",
        "expected_calls": [{"name": "stop_treadmill", "args": {}}],
        "description": "Emergency stop urgency still uses stop_treadmill",
    },
    {
        "id": "pause",
        "prompt": "Pause the program",
        "expected_calls": [{"name": "pause_program", "args": {}}],
        "description": "Pause command",
    },
    {
        "id": "resume",
        "prompt": "Resume",
        "expected_calls": [{"name": "resume_program", "args": {}}],
        "alt_calls": [],
        "description": "Resume — accept call or no-op if model sees program already running",
    },
]

# ---------------------------------------------------------------------------
# Skip / extend
# ---------------------------------------------------------------------------
SKIP_EXTEND_CASES = [
    {
        "id": "skip_interval",
        "prompt": "Skip this interval",
        "expected_calls": [{"name": "skip_interval", "args": {}}],
        "description": "Skip to next interval",
    },
    {
        "id": "skip_next",
        "prompt": "Go to the next one",
        "expected_calls": [{"name": "skip_interval", "args": {}}],
        "description": "Colloquial skip phrasing",
    },
    {
        "id": "extend_30s",
        "prompt": "Add 30 seconds to this interval",
        "expected_calls": [{"name": "extend_interval", "args": {"seconds": 30}}],
        "description": "Extend current interval by 30 seconds",
    },
    {
        "id": "extend_1min",
        "prompt": "Extend this by a minute",
        "expected_calls": [{"name": "extend_interval", "args": {"seconds": 60}}],
        "description": "Extend current interval by 60 seconds",
    },
    {
        "id": "shorten_interval",
        "prompt": "Shorten this interval by 30 seconds",
        "expected_calls": [{"name": "extend_interval", "args": {"seconds": -30}}],
        "description": "Negative extend to shorten current interval",
    },
    {
        "id": "add_5min_end",
        "prompt": "Add 5 more minutes at the end at 4 mph",
        "expected_calls": [
            {
                "name": "add_time",
                "args": {
                    "intervals": [
                        {"name": "Extra", "duration": 300, "speed": 4.0, "incline": 0},
                    ],
                },
            },
        ],
        "description": "Add time at end of program via add_time",
    },
]

# ---------------------------------------------------------------------------
# Compound commands (multiple function calls expected)
# ---------------------------------------------------------------------------
COMPOUND_CASES = [
    {
        "id": "compound_speed_incline",
        "prompt": "Set speed to 6 and incline to 4",
        "expected_calls": [
            {"name": "set_speed", "args": {"mph": 6.0}},
            {"name": "set_incline", "args": {"incline": 4}},
        ],
        "alt_calls": [{"name": "set_speed", "args": {"mph": 6.0}}],
        "description": "Two adjustments — accept both calls or at least speed",
    },
    {
        "id": "compound_speed_incline_natural",
        "prompt": "I want to jog at 5 mph with a 3 percent incline",
        "expected_calls": [
            {"name": "set_speed", "args": {"mph": 5.0}},
            {"name": "set_incline", "args": {"incline": 3}},
        ],
        "alt_calls": [{"name": "set_speed", "args": {"mph": 5.0}}],
        "description": "Natural phrasing compound — accept both calls or at least speed",
    },
    {
        "id": "compound_speed_7_incline_5",
        "prompt": "Set speed to 7 and incline to 5",
        "expected_calls": [
            {"name": "set_speed", "args": {"mph": 7.0}},
            {"name": "set_incline", "args": {"incline": 5}},
        ],
        "alt_calls": [{"name": "set_speed", "args": {"mph": 7.0}}],
        "description": "Compound with fallback — Live may only do one, Flash should catch the other",
    },
]

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
EDGE_CASES = [
    {
        "id": "edge_speed_over_max",
        "prompt": "Set speed to 15 mph",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 12.0}}],
        "alt_calls": [],
        "description": "Speed above max — accept clamped set_speed(12) or refusal (no call)",
    },
    {
        "id": "edge_incline_zero",
        "prompt": "Set incline to zero percent",
        "expected_calls": [{"name": "set_incline", "args": {"incline": 0}}],
        "description": "Explicit zero incline",
    },
    {
        "id": "edge_half_mph",
        "prompt": "Set speed to half a mile per hour",
        "expected_calls": [{"name": "set_speed", "args": {"mph": 0.5}}],
        "description": "Minimum valid speed",
    },
]

# ---------------------------------------------------------------------------
# Non-action queries (expect NO function calls — text response only)
# ---------------------------------------------------------------------------
NO_ACTION_CASES = [
    {
        "id": "query_speed",
        "prompt": "How fast am I going?",
        "expected_calls": [],
        "description": "Informational query — no function call expected",
    },
    {
        "id": "query_program",
        "prompt": "What interval am I on?",
        "expected_calls": [],
        "description": "Program status query — no function call expected",
    },
    {
        "id": "query_time_left",
        "prompt": "How much time is left?",
        "expected_calls": [],
        "description": "Time remaining query — no function call expected",
    },
    {
        "id": "query_greeting",
        "prompt": "Hey, how's it going?",
        "expected_calls": [],
        "description": "Casual greeting — no function call expected",
    },
]

# ---------------------------------------------------------------------------
# All cases combined
# ---------------------------------------------------------------------------
ALL_TEST_CASES = (
    SPEED_CASES
    + INCLINE_CASES
    + WORKOUT_CASES
    + STOP_PAUSE_CASES
    + SKIP_EXTEND_CASES
    + COMPOUND_CASES
    + EDGE_CASES
    + NO_ACTION_CASES
)

# Category lookup for filtering
CATEGORIES = {
    "speed": SPEED_CASES,
    "incline": INCLINE_CASES,
    "workout": WORKOUT_CASES,
    "stop_pause": STOP_PAUSE_CASES,
    "skip_extend": SKIP_EXTEND_CASES,
    "compound": COMPOUND_CASES,
    "edge": EDGE_CASES,
    "no_action": NO_ACTION_CASES,
}
