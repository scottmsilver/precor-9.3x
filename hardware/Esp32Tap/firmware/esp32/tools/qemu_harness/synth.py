"""Canonical synthetic Precor console/motor KV stream generator.

Encodings are Python ports of cpp/protocol/kv_protocol.cpp (proven against
real hardware); test_encoders.py asserts them against a golden table
transcribed from the cpp doctest vectors — do NOT re-derive hex math here.

Wire truth (root CLAUDE.md + RS485_DISCOVERY.md):
  - console -> motor (pin 6): ``[key:value]\\xff`` or ``[key]\\xff``,
    14-key cycle in 5 bursts, 9600 8N1;
  - motor -> console (pin 3): ``[key:value]`` replies, NO 0xFF delimiter;
  - hmph = mph*100 uppercase hex, inc = half-percent uppercase hex.

All bytes handed to QEMU are LOGICAL (the chardev path does not model the
line inversion — see tools/qemu_harness/README.md).
"""

from __future__ import annotations

# 14-key console cycle (engine/emulation_cycle.h KV_CYCLE order).
KEY_CYCLE = [
    "inc",
    "hmph",
    "amps",
    "err",
    "belt",
    "vbus",
    "lift",
    "lfts",
    "lftg",
    "part",
    "ver",
    "type",
    "diag",
    "loop",
]

# engine/emulation_cycle.h BURSTS: which cycle indices form each of the 5
# bursts the firmware transmits while emulating.
BURSTS = [
    [0, 1],
    [2, 3, 4],
    [5, 6, 7, 8],
    [9, 10, 11],
    [12, 13],
]


def encode_speed_hex(tenths_mph: int) -> str:
    """Port of kv_protocol.cpp encode_speed_hex: mph*100, uppercase hex."""
    return format(tenths_mph * 10, "X")


def encode_incline_hex(half_pct: int) -> str:
    """Port of kv_protocol.cpp encode_incline_hex: half-pct, uppercase hex."""
    return format(half_pct, "X")


def build_console_frame(key: str, value: str | None = None) -> bytes:
    """One console frame: [key]\\xff (query) or [key:value]\\xff (set)."""
    body = f"[{key}]" if value is None else f"[{key}:{value}]"
    return body.encode("ascii") + b"\xff"


def console_cycle(
    speed_tenths: int = 0, incline_half_pct: int = 0, static_values: dict[str, str] | None = None
) -> list[bytes]:
    """One full 14-key console cycle: hmph/inc valued, the other 12 keys
    query-form ([key]\\xff), matching the captured protocol."""
    vals: dict[str, str] = {
        "hmph": encode_speed_hex(speed_tenths),
        "inc": encode_incline_hex(incline_half_pct),
    }
    vals.update(static_values or {})
    return [build_console_frame(k, vals.get(k)) for k in KEY_CYCLE]


def console_cycle_bytes(speed_tenths: int = 0, incline_half_pct: int = 0) -> bytes:
    return b"".join(console_cycle(speed_tenths, incline_half_pct))


def motor_reply(key: str, value: str) -> bytes:
    """Motor pin-3 reply: [key:value], NO 0xFF terminator."""
    return f"[{key}:{value}]".encode("ascii")


# --- malformed-frame helpers (fuzz smoke; kv_parse documented tolerance) --


def fuzz_frames() -> list[bytes]:
    return [
        b"[hmph:7",  # truncated frame (no closing bracket yet)
        b"[inc",  # truncated even earlier
        b"hmph:78]",  # missing opening bracket
        b"[hm\xffph:78]\xff",  # embedded 0xFF mid-frame
        b"[b\x01elt:1]\xff",  # non-printable byte inside frame
        b"\x00\xff\xff\x00",  # bare delimiters
        b"]]][[[",  # bracket noise
    ]


# --- model-faithful complete-frame counter --------------------------------


def count_complete_frames(data: bytes) -> int:
    """Count frames SafetyController::observe_console_bytes would log as
    complete_console_frame. Direct port of the scanner: '[' always
    restarts the candidate; non-printable resets; >100 bytes resets;
    ']' closes; content must match key:value with key ~ [A-Za-z][A-Za-z0-9_]{0,31}
    and value length <= 64 (colon REQUIRED — bare [key] does not count)."""
    complete = 0
    candidate = bytearray()
    active = False
    for b in data:
        if b == 0x5B:  # '['
            candidate = bytearray(b"[")
            active = True
            continue
        if not active:
            continue
        if b < 0x20 or b > 0x7E:
            active = False
            continue
        candidate.append(b)
        if len(candidate) > 101:
            active = False
            continue
        if b != 0x5D:  # ']'
            continue
        content = candidate[1:-1].decode("ascii")
        active = False
        if ":" not in content:
            continue
        key, value = content.split(":", 1)
        if not (1 <= len(key) <= 32) or len(value) > 64:
            continue
        if not (key[0].isascii() and key[0].isalpha()):
            continue
        if not all(c.isalnum() or c == "_" for c in key[1:]):
            continue
        complete += 1
    return complete


def split_frames(burst: bytes) -> list[bytes]:
    """Split a firmware TX burst into 0xFF-terminated frames; raises if
    the burst is not an exact concatenation of frame+0xFF units."""
    if not burst.endswith(b"\xff"):
        raise ValueError(f"burst not 0xFF-terminated: {burst!r}")
    frames = burst.split(b"\xff")
    assert frames[-1] == b""
    return [f for f in frames[:-1]]


def frame_key(frame: bytes) -> str:
    """Key of a [key] / [key:value] frame."""
    if not (frame.startswith(b"[") and frame.endswith(b"]")):
        raise ValueError(f"not a bracketed frame: {frame!r}")
    content = frame[1:-1].decode("ascii")
    return content.split(":", 1)[0]
