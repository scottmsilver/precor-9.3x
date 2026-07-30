"""Convert logic-analyzer capture CSVs (cpp/captures/) into replayable
timed-burst JSONL streams.

Read-only reuse of the proven decoders — decode_uart() from
cpp/captures/decode_inverted.py (inverted polarity, binary-search edge
decode) and group_by_idle_gap() from cpp/captures/analyze_logic.py — via
sys.path insertion; nothing in cpp/ is copied or modified.

Channel map (cpp/captures/RS485_DISCOVERY.md): ch5 = pin 6
(console -> motor), ch2 = pin 3 (motor -> console). Only stop_ok bytes are
kept (a False stop bit is a decode artifact, never real wire data). Bursts
are grouped at a 3 ms idle gap. Output: one JSONL file per channel,
lines {"t_us": <burst start offset us>, "bytes": "<hex>"}, cached in /tmp
keyed by CSV mtime (nothing is generated inside the repo tree).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
ESP32_DIR = _HERE.parents[2]
REPO_ROOT = ESP32_DIR.parents[3]
CAPTURES_DIR = REPO_ROOT / "cpp" / "captures"
CACHE_DIR = Path("/tmp/esp32tap_qemu_harness_cache")

sys.path.insert(0, str(CAPTURES_DIR))
from analyze_logic import group_by_idle_gap  # noqa: E402
from decode_inverted import decode_uart, extract_edges, load_csv  # noqa: E402

KEY_CYCLE_14 = [
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


def _decode_channel(rows, channel: int):
    edges = extract_edges(rows, channel)
    if len(edges) < 4:
        return []
    decoded = decode_uart(edges, inverted=True)
    return [d for d in decoded if d[3]]  # stop_ok only


def _bursts_to_jsonl(bursts, t0: float, out_path: Path) -> None:
    with out_path.open("w") as f:
        for start_t, _end_t, byte_vals in bursts:
            f.write(
                json.dumps(
                    {
                        "t_us": int((start_t - t0) * 1_000_000),
                        "bytes": bytes(byte_vals).hex(),
                    }
                )
                + "\n"
            )


def convert(csv_path: Path) -> tuple[Path, Path]:
    """Decode ch5/ch2 of a capture into cached console/motor JSONL files;
    returns (console_path, motor_path)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{csv_path.stem}.{int(csv_path.stat().st_mtime)}"
    console_p = CACHE_DIR / f"{key}.console.jsonl"
    motor_p = CACHE_DIR / f"{key}.motor.jsonl"
    if console_p.exists() and motor_p.exists():
        return console_p, motor_p

    rows = load_csv(str(csv_path))
    t0 = rows[0][0]
    console_bytes = _decode_channel(rows, 5)
    motor_bytes = _decode_channel(rows, 2)
    _bursts_to_jsonl(group_by_idle_gap(console_bytes, gap_threshold_ms=3.0), t0, console_p)
    _bursts_to_jsonl(group_by_idle_gap(motor_bytes, gap_threshold_ms=3.0), t0, motor_p)
    return console_p, motor_p


def load_stream(jsonl_path: Path) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    with jsonl_path.open() as f:
        for line in f:
            obj = json.loads(line)
            out.append((int(obj["t_us"]), bytes.fromhex(obj["bytes"])))
    return out


def validate_streams(console: list[tuple[int, bytes]], motor: list[tuple[int, bytes]]) -> None:
    """Shape sanity before replay (RS485_DISCOVERY.md ground truth):
    console frames are 0xFF-delimited KV text covering the 14-key cycle;
    motor replies carry ~no 0xFF."""
    cdata = b"".join(b for _, b in console)
    mdata = b"".join(b for _, b in motor)
    assert cdata, "console stream empty"
    assert mdata, "motor stream empty"
    kv = cdata.count(b"[")
    ff = cdata.count(b"\xff")
    assert kv > 50, f"console stream implausibly small ({kv} frames)"
    assert ff >= 0.8 * kv, f"console ff_count {ff} << kv_count {kv}"
    for k in KEY_CYCLE_14:
        assert f"[{k}".encode() in cdata, f"console missing key {k}"
    mff = mdata.count(b"\xff")
    assert mff <= max(2, 0.02 * mdata.count(b"[")), f"motor stream has unexpected 0xFF delimiters ({mff})"


def capture_streams(name: str = "try5"):
    """Console+motor timed-burst streams for a named capture, validated."""
    console_p, motor_p = convert(CAPTURES_DIR / f"{name}.csv")
    console = load_stream(console_p)
    motor = load_stream(motor_p)
    validate_streams(console, motor)
    return console, motor


if __name__ == "__main__":
    c, m = capture_streams(sys.argv[1] if len(sys.argv) > 1 else "try5")
    cd = b"".join(b for _, b in c)
    md = b"".join(b for _, b in m)
    print(f"console: {len(c)} bursts, {len(cd)} bytes, " f"span {c[-1][0] / 1e6:.2f}s")
    print(f"motor:   {len(m)} bursts, {len(md)} bytes, " f"span {m[-1][0] / 1e6:.2f}s")
