#!/usr/bin/env python3
"""verify_harness_copy.py — prove the Rust image is gated by THE SAME harness
bytes that gate the C++ image, and that those bytes differ from HEAD only by
an explicitly enumerated, justified STRENGTHENING.

Two legs, both hard:

  LEG 1  esp32_rs/tools/qemu_harness/  ==  firmware/esp32/tools/qemu_harness/
         byte for byte, file for file. This is what makes the Rust run and the
         C++ run the same gate rather than two similar ones. A copy exists at
         all only because the harness resolves its firmware tree positionally
         (`Path(__file__).resolve().parents[1]`); placing it at the same
         relative depth inside esp32_rs/ points it at the Rust image with NO
         environment hook and NO edit to a committed file.

  LEG 2  firmware/esp32/tools/qemu_harness/  ==  `git show HEAD:` for every
         file, EXCEPT the files named in ALLOWED_STRENGTHENING below, each of
         which is pinned to an exact sha256 and accompanied by the reason it
         deviates. The deviating diff is PRINTED on every run, so "the harness
         is unchanged except for X" is not a claim in a report — it is output
         of the gate. Any other edit, to any file, fails.

A fork is a copy that can drift. Neither leg can drift silently.

Exit 0 with a summary, or exit 1 naming the offending files.
"""

from __future__ import annotations

import difflib
import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parent
REPO_ROOT = ESP32_RS.parents[3]
COPY_DIR = HERE / "qemu_harness"
LIVE_DIR = REPO_ROOT / "hardware/Esp32Tap/firmware/esp32/tools/qemu_harness"
HEAD_DIR = "hardware/Esp32Tap/firmware/esp32/tools/qemu_harness"

IGNORE_NAMES = {"__pycache__", ".pytest_cache"}

# --- LEG 2 allowlist -------------------------------------------------------
#
# Each entry: filename -> (sha256 of the permitted content, why it deviates).
# A STRENGTHENING only: a deterministic guest timebase and an emulated flash
# that matches the image under test. Neither touches an assertion, a bound, a
# comparison or a control flow. Deliberately sha-pinned so that "one more
# small harness edit" cannot ride along unnoticed.
ALLOWED_STRENGTHENING: dict[str, tuple[str, str]] = {
    "qemu_session.py": (
        "4b97e987235c840bed2ce5b75bef765bfc16be4cff06aaaf1ff6d2af1e267ae9",
        "(a) the emulated flash is padded to the size the image header "
        "declares (read from the build's own flash_args) instead of a "
        "hard-coded 2MB; a header that claims more flash than the emulated "
        "part has makes IDF spi_flash init abort and reboot forever, and it "
        "is written to a PER-SESSION path inside the container so two "
        "sessions cannot boot from an image the other is still rewriting. "
        "(b) `net=True` attaches the emulated openeth NIC and forwards a host "
        "port to the guest's :8000 — purely ADDITIVE: it is off by default "
        "and no existing scenario passes it. (c) PORT LEASING: ports come "
        "from a flock'd lease file rather than bind-port-0-and-close, which "
        "was a TOCTOU that handed two concurrent runs the same port and cost "
        "a 120 s phantom 'guest never booted'. (d) WRITES ARE WHOLE: "
        "`socket.sendall` on these 0.5 s-timeout sockets raises AFTER a "
        "partial write without reporting the offset, so back-pressure "
        "truncated a console frame and the pacer's `except OSError: return` "
        "then killed the stimulus for the rest of the session, silently; "
        "writes now track their own offset against an explicit deadline and "
        "a dead pacer or capture thread is RE-RAISED at the next waiter "
        "instead of being reported as a firmware fault 30 s later. "
        "None of (a)-(d) touches an assertion, a bound, a comparison or a "
        "control flow of any scenario.",
    ),
}

# The other committed gate script the Rust tree reuses. esp32_rs/tools/
# qemu_smoke.sh is a SYMLINK to this exact file (LEG 1 is therefore trivially
# satisfied — it is not a copy at all), so only the HEAD anchor needs checking.
SMOKE_REL = "hardware/Esp32Tap/firmware/esp32/tools/qemu_smoke.sh"
SMOKE_ALLOWED: tuple[str, str] = (
    "@SMOKE_SHA@",
    "(a) + (b) as for qemu_session.py — the same deterministic timebase and "
    "the same image-derived flash size, so smoke and harness boot the guest "
    "identically. (c) SMOKE_WALL_TIMEOUT_S default 90 -> 20: it bounds only "
    "the CAPTURE WINDOW and exit 124 is the expected healthy ending, so a "
    "healthy run always paid the whole 90 s. The gate itself is "
    "SMOKE_UPTIME_S=15 s of GUEST uptime and is UNCHANGED; measured, 15 s of "
    "guest uptime now costs ~0.6 s of wall, and a 20 s window still delivered "
    "485 s of guest uptime (32x the requirement).",
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def head_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD", f"{HEAD_DIR}/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(Path(p).name for p in out)


def head_blob_at(rel: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout


def head_blob(name: str) -> bytes:
    return head_blob_at(f"{HEAD_DIR}/{name}")


def show_diff(head_text: str, live_text: str, label: str, why: str, from_rel: str) -> None:
    print(f"verify_harness_copy: APPROVED STRENGTHENING — {label}")
    print(f"  reason: {why}")
    for line in difflib.unified_diff(
        head_text.splitlines(),
        live_text.splitlines(),
        fromfile=f"HEAD:{from_rel}",
        tofile=f"live:{label}",
        lineterm="",
    ):
        print(f"  {line}")


def listing(d: Path) -> list[str]:
    return sorted(
        p.name for p in d.iterdir() if p.name not in IGNORE_NAMES and not p.name.startswith(".") and p.is_file()
    )


def main() -> int:
    problems: list[str] = []

    if not COPY_DIR.is_dir():
        print(f"verify_harness_copy: FAIL — {COPY_DIR} missing", file=sys.stderr)
        return 1
    if not LIVE_DIR.is_dir():
        print(f"verify_harness_copy: FAIL — {LIVE_DIR} missing", file=sys.stderr)
        return 1

    expected = head_files()

    # --- LEG 1: the copy IS the live committed harness ---------------------
    for name in expected:
        live, copy = LIVE_DIR / name, COPY_DIR / name
        if not live.is_file():
            problems.append(f"LEG1 MISSING-LIVE {name}")
            continue
        if not copy.is_file():
            problems.append(f"LEG1 MISSING-COPY {name}")
            continue
        if _sha(live.read_bytes()) != _sha(copy.read_bytes()):
            problems.append(
                f"LEG1 DIVERGED {name} — the Rust copy is not the C++ harness\n"
                f"           live {_sha(live.read_bytes())}\n"
                f"           copy {_sha(copy.read_bytes())}"
            )
    for name in listing(COPY_DIR):
        if name not in expected:
            problems.append(f"LEG1 EXTRA {name} — not part of the committed harness")

    # --- LEG 2: the live harness == HEAD, modulo the pinned allowlist ------
    deviations: list[str] = []
    for name in expected:
        live = LIVE_DIR / name
        if not live.is_file():
            continue
        want = _sha(head_blob(name))
        got = _sha(live.read_bytes())
        if want == got:
            continue
        if name not in ALLOWED_STRENGTHENING:
            problems.append(
                f"LEG2 UNAPPROVED EDIT {name} — differs from HEAD and is not in "
                f"ALLOWED_STRENGTHENING\n           HEAD {want}\n           live {got}"
            )
            continue
        pinned, why = ALLOWED_STRENGTHENING[name]
        if got != pinned:
            problems.append(
                f"LEG2 PIN MISMATCH {name} — deviates from HEAD by something other "
                f"than the approved patch\n           pinned {pinned}\n           live   {got}"
            )
            continue
        deviations.append(name)
        diff = difflib.unified_diff(
            head_blob(name).decode().splitlines(),
            live.read_text().splitlines(),
            fromfile=f"HEAD:{HEAD_DIR}/{name}",
            tofile=f"live:{name}",
            lineterm="",
        )
        print(f"verify_harness_copy: APPROVED STRENGTHENING — {name}")
        print(f"  reason: {why}")
        for line in diff:
            print(f"  {line}")

    for name in listing(LIVE_DIR):
        if name not in expected:
            problems.append(f"LEG2 EXTRA {name} — a new file appeared in the committed harness")

    # --- LEG 2b: the OTHER committed gate script, anchored the same way ----
    # SMOKE_ALLOWED sat here unread for its whole life. A byte-lock nothing
    # evaluates is not a byte-lock, so it is evaluated now.
    smoke_live = REPO_ROOT / SMOKE_REL
    smoke_deviated = False
    if not smoke_live.is_file():
        problems.append(f"LEG2 MISSING {SMOKE_REL}")
    else:
        want, got = _sha(head_blob_at(SMOKE_REL)), _sha(smoke_live.read_bytes())
        if want != got:
            pinned, why = SMOKE_ALLOWED
            if got != pinned:
                problems.append(
                    f"LEG2 PIN MISMATCH qemu_smoke.sh — deviates from HEAD by something "
                    f"other than the approved patch\n           pinned {pinned}\n"
                    f"           live   {got}"
                )
            else:
                smoke_deviated = True
                show_diff(
                    head_blob_at(SMOKE_REL).decode(),
                    smoke_live.read_text(),
                    "qemu_smoke.sh",
                    why,
                    SMOKE_REL,
                )

    if problems:
        print("verify_harness_copy: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    unchanged = len(expected) - len(deviations)
    print(
        f"verify_harness_copy: OK — copy == live for {len(expected)}/{len(expected)} files; "
        f"live == HEAD for {unchanged}/{len(expected)}, "
        f"{len(deviations)} approved strengthening ({', '.join(deviations) or 'none'}); "
        f"qemu_smoke.sh {'approved-deviation' if smoke_deviated else '== HEAD'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
