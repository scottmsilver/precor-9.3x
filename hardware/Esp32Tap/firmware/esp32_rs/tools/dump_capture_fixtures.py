#!/usr/bin/env python3
"""Dump the real logic-analyzer capture streams into difftest/fixtures/.

READ-ONLY reuse of the committed harness helper
`esp32/tools/qemu_harness/capture_streams.py` (which itself read-only-reuses
the decoders in `cpp/captures/`). Nothing under `cpp/` or `esp32/` is written.

The fixtures are raw decoded byte streams — exactly what came off pin 6
(console) and pin 3 (motor) of a real treadmill — so the D1 differential runs
the two KV parsers over genuine wire data rather than synthetic frames.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parent
HARNESS = ESP32_RS.parent / "esp32" / "tools" / "qemu_harness"
FIXTURES = ESP32_RS / "difftest" / "fixtures"

sys.path.insert(0, str(HARNESS))
from capture_streams import capture_streams  # noqa: E402

CAPTURES = ["try2", "try3", "try5", "try6", "try7"]


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in CAPTURES:
        try:
            console, motor = capture_streams(name)
        except AssertionError as e:
            # Not every capture is a full clean 14-key session; skip with a
            # note rather than failing the dump.
            print(f"{name}: SKIPPED ({e})")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"{name}: SKIPPED (decode failed: {e})")
            continue
        for label, stream in (("console", console), ("motor", motor)):
            data = b"".join(b for _, b in stream)
            out = FIXTURES / f"{name}.{label}.bin"
            out.write_bytes(data)
            total += len(data)
            print(f"{name}.{label}: {len(data)} bytes, {len(stream)} bursts -> {out.name}")
    print(f"total {total} bytes of real capture data in {FIXTURES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
