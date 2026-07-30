"""harness_env — which firmware tree this harness run is pointed at.

The harness used to resolve the firmware directory positionally
(`Path(__file__).resolve().parents[1]`), which hard-wires it to the C++ tree
it lives in. `ESP32TAP_FW_DIR` lets the SAME harness — same file, same
assertions, same timeouts, same bounds — be pointed at a different firmware
tree that has the identical `build/` + `build_qemu_test/` layout (today:
`firmware/esp32_rs/`, the Rust safety-core port).

This is deliberately a *lookup* hook and nothing else. It changes which
`esp32tap.bin` is booted; it changes no assertion, no timeout, no bound, no
comparison, and no control flow. With the variable unset — which is how the
mandated repo gate `python3 -m pytest hardware/Esp32Tap -q` always runs it —
`esp32_dir()` returns exactly what the positional expression returned, so the
committed gate is bit-for-bit the same run it was before the hook existed.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The C++ tree this harness is committed inside: firmware/esp32/.
DEFAULT_FW_DIR = HERE.parents[1]


def esp32_dir() -> Path:
    """The firmware tree to boot images from."""
    override = os.environ.get("ESP32TAP_FW_DIR")
    if not override:
        return DEFAULT_FW_DIR
    p = Path(override).resolve()
    if not p.is_dir():
        raise RuntimeError(f"ESP32TAP_FW_DIR={override!r} is not a directory")
    return p
