#!/usr/bin/env python3
"""verify_harness_copy.py — prove the Rust image is gated by the SAME harness
bytes that gate the C++ image, save for an explicitly enumerated, sha-pinned,
diff-printed STRENGTHENING.

Two legs, both hard:

  LEG 1  esp32_rs/tools/qemu_harness/  ==  firmware/esp32/tools/qemu_harness/
         byte for byte, file for file, EXCEPT the files named in
         ALLOWED_STRENGTHENING below, each pinned to an exact sha256 and
         accompanied by the reason it deviates. The deviating diff is PRINTED
         on every run, so "the copy is the C++ harness except for X" is not a
         claim in a report — it is output of the gate. Any other edit, to any
         file, fails. A copy exists at all only because the harness resolves
         its firmware tree positionally (`Path(__file__).resolve().parents[1]`);
         placing it at the same relative depth inside esp32_rs/ points it at
         the Rust image with NO environment hook.

  LEG 2  firmware/esp32/tools/qemu_harness/  ==  `git show HEAD:` for every
         file, and likewise firmware/esp32/tools/qemu_smoke.sh. Plain equality,
         no allowlist: firmware/esp32/ is the C++ tier and is READ-ONLY to this
         tree, so any deviation there — committed or not — is drift, and the
         anchor the Rust copy is measured against has moved.

WHY THE ALLOWLIST HANGS OFF LEG 1 AND NOT LEG 2 — this gate FAILED for three
sweeps in a row, deterministically, and the reason is the shape below. It used
to anchor the allowlist on LEG 2 (live-vs-HEAD) and demand byte equality on
LEG 1. That only ever holds while the strengthening sits UNCOMMITTED in the
working tree of a file this tree is forbidden to edit: the instant it is
committed (or, as happened here, only ever applied to the Rust side), live ==
HEAD makes LEG 2 fall silent and LEG 1 reports the divergence it can never
resolve. The strengthening lives in the Rust copy, permanently and in git, so
that is where it must be pinned. LEG 2 keeps its teeth as plain equality.

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

# --- LEG 1 allowlist -------------------------------------------------------
#
# Each entry: filename -> (sha256 of the permitted content OF THE RUST COPY,
# why it deviates from the C++ live file).
# A STRENGTHENING only: a deterministic guest timebase and an emulated flash
# that matches the image under test. Neither touches an assertion, a bound, a
# comparison or a control flow. Deliberately sha-pinned so that "one more
# small harness edit" cannot ride along unnoticed.
ALLOWED_STRENGTHENING: dict[str, tuple[str, str]] = {
    "qemu_session.py": (
        "0b0e0e1b5056d8291f0228d312f871b67d5a9cbaefaf7c9bd76c5cfe110f5d6e",
        "(a) the emulated flash is padded to the size the image header "
        "declares (read from the build's own flash_args) instead of a "
        "hard-coded 2MB; a header that claims more flash than the emulated "
        "part has makes IDF spi_flash init abort and reboot forever, and it "
        "is written to a UNIQUELY NAMED path inside the CONTAINER's own "
        "writable layer — only the repo is bind-mounted, and --rm "
        "destroys that layer — rather than to the bind-mounted repo, so "
        "two sessions cannot boot from an image the other is still "
        "rewriting and nothing a guest commits to NVS outlives its "
        "session. "
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
        "(e) THE BUILD DIRECTORY IS LEASED SHARED for the session's life, "
        "against `tools/build.sh` which now takes the same lock EXCLUSIVE. "
        "Sessions read build_qemu_test/ off the bind-mounted repo to merge "
        "their image; (a) isolated the OUTPUT but nothing stopped a second "
        "builder rewriting the INPUT mid-read. On 2026-07-29 that happened "
        "and the resulting DEEP failure was diagnosed twice wrongly — as a "
        "firmware bug, then as a QEMU clock artifact — before the real cause "
        "was found. Many sessions still run at once; a build waits for them. "
        "None of (a)-(e) touches an assertion, a bound, a comparison or a "
        "control flow of any scenario.",
    ),
}

# The other committed gate script the Rust tree reuses. esp32_rs/tools/
# qemu_smoke.sh is a SYMLINK to this exact file — not a copy at all, so LEG 1
# is satisfied by construction and only the HEAD anchor needs checking.
#
# There used to be a SMOKE_ALLOWED pin here whose sha was the literal string
# "@SMOKE_SHA@". It was unreachable (it is only consulted when live != HEAD,
# which cannot happen for a read-only tier) and it would have failed closed
# with a nonsense message if it ever had been reached. A byte-lock pinned to a
# placeholder is not a byte-lock; the requirement is plain equality.
SMOKE_REL = "hardware/Esp32Tap/firmware/esp32/tools/qemu_smoke.sh"


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


def show_diff(base_text: str, derived_text: str, label: str, why: str, from_rel: str) -> None:
    print(f"verify_harness_copy: APPROVED STRENGTHENING — {label}")
    print(f"  reason: {why}")
    for line in difflib.unified_diff(
        base_text.splitlines(),
        derived_text.splitlines(),
        fromfile=from_rel,
        tofile=label,
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

    # --- LEG 1: the copy IS the live harness, modulo the pinned allowlist --
    deviations: list[str] = []
    for name in expected:
        live, copy = LIVE_DIR / name, COPY_DIR / name
        if not live.is_file():
            problems.append(f"LEG1 MISSING-LIVE {name}")
            continue
        if not copy.is_file():
            problems.append(f"LEG1 MISSING-COPY {name}")
            continue
        live_sha, copy_sha = _sha(live.read_bytes()), _sha(copy.read_bytes())
        if live_sha == copy_sha:
            continue
        if name not in ALLOWED_STRENGTHENING:
            problems.append(
                f"LEG1 DIVERGED {name} — the Rust copy is not the C++ harness and is "
                f"not in ALLOWED_STRENGTHENING\n           live {live_sha}\n"
                f"           copy {copy_sha}"
            )
            continue
        pinned, why = ALLOWED_STRENGTHENING[name]
        if copy_sha != pinned:
            problems.append(
                f"LEG1 PIN MISMATCH {name} — the copy deviates from the C++ harness by "
                f"something other than the approved patch\n           pinned {pinned}\n"
                f"           copy   {copy_sha}"
            )
            continue
        deviations.append(name)
        show_diff(
            live.read_text(),
            copy.read_text(),
            f"esp32_rs copy of {name}",
            why,
            f"{HEAD_DIR}/{name}",
        )
    for name in listing(COPY_DIR):
        if name not in expected:
            problems.append(f"LEG1 EXTRA {name} — not part of the committed harness")

    # --- LEG 2: the live harness == HEAD, plainly -------------------------
    # firmware/esp32/ is read-only to this tree. Any deviation, committed or
    # not, has moved the anchor LEG 1 measures the copy against.
    for name in expected:
        live = LIVE_DIR / name
        if not live.is_file():
            continue
        want, got = _sha(head_blob(name)), _sha(live.read_bytes())
        if want != got:
            problems.append(
                f"LEG2 EDITED {name} — firmware/esp32/ is read-only to this tree but "
                f"this file differs from HEAD\n           HEAD {want}\n           live {got}"
            )

    for name in listing(LIVE_DIR):
        if name not in expected:
            problems.append(f"LEG2 EXTRA {name} — a new file appeared in the committed harness")

    # --- LEG 2b: the OTHER committed gate script, anchored the same way ----
    smoke_live = REPO_ROOT / SMOKE_REL
    if not smoke_live.is_file():
        problems.append(f"LEG2 MISSING {SMOKE_REL}")
    else:
        want, got = _sha(head_blob_at(SMOKE_REL)), _sha(smoke_live.read_bytes())
        if want != got:
            problems.append(
                f"LEG2 EDITED qemu_smoke.sh — firmware/esp32/ is read-only to this tree "
                f"but this file differs from HEAD\n           HEAD {want}\n           live {got}"
            )

    if problems:
        print("verify_harness_copy: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    identical = len(expected) - len(deviations)
    print(
        f"verify_harness_copy: OK — copy == live for {identical}/{len(expected)} files, "
        f"{len(deviations)} approved strengthening ({', '.join(deviations) or 'none'}); "
        f"live == HEAD for {len(expected)}/{len(expected)} files and for qemu_smoke.sh"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
