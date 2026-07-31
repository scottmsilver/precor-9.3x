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
import os
import stat
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
    "conftest.py": (
        "2424ac204e21d954e59b3298a9d0390efd28783351cd8c563b2e520026eecf99",
        "ARTIFACT PROVENANCE: both production and qemu-test bundles are "
        "mandatory session fixtures, verified while shared locks remain "
        "held through every S6 read; Docker availability is checked only "
        "after artifact rejection. No scenario assertion or bound changes.",
    ),
    "qemu_session.py": (
        "0847de505627f89b2359f6964510796b8f52f16a543722fa10b30f0976afffee",
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
        "was found. Many sessions still run at once; a build waits for them, and "
        "the lease is released on EVERY construction failure — the boot waits are "
        "inside the protected region and close() frees leases in a `finally` — "
        "because a stranded shared lock turned one boot timeout into a 30-minute "
        "hang for the next build. "
        "(f) ARTIFACT PROVENANCE is verified before port allocation and its "
        "shared lease is held through flash assembly and teardown, including "
        "every constructor-failure path. None of (a)-(f) touches an assertion, "
        "a bound, a comparison or a control flow of any scenario.",
    ),
    "run.sh": (
        "ad706374322bf1ca1bdf257fc5ef42d774dd1887b44820949bcbff8c0619aea4",
        "ARTIFACT PROVENANCE: the historical harness entrypoint now verifies "
        "and leases both bundles before checked delegation to the Rust "
        "run_harness.sh. It changes no scenario assertion or selection.",
    ),
}

# The Rust smoke path is a pinned executable provenance wrapper. Its delegated
# C++ smoke gate remains a separately type/mode/byte-checked HEAD anchor.
SMOKE_REL = "hardware/Esp32Tap/firmware/esp32/tools/qemu_smoke.sh"
SMOKE_STRENGTHENING = (
    "aac4ffa7b931d070b93ad12e22b001c9db871a7f3e14a67746d9f28ee21749ad",
    "ARTIFACT PROVENANCE: the Rust path is an executable regular wrapper "
    "which leases and verifies production before sourcing the separately "
    "HEAD-anchored, byte-unchanged C++ smoke gate with a task-private $0. "
    "Its positional ESP32_DIR resolves to a private copy of the five leased "
    "Rust members, so qemu_flash.bin cannot mutate the sealed generation. "
    "Private workspaces are isolated and trap-cleaned; arguments, environment "
    "and exit status pass through unchanged.",
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


def head_modes() -> dict[str, str]:
    rows = subprocess.run(
        ["git", "ls-tree", "HEAD", f"{HEAD_DIR}/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    result: dict[str, str] = {}
    for row in rows:
        metadata, path = row.split("\t", 1)
        mode, object_type, _object_id = metadata.split()
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError(f"unsupported harness tree entry: {row}")
        result[Path(path).name] = mode
    return result


def head_blob_at(rel: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout


def head_mode_at(rel: str) -> str:
    row = subprocess.run(
        ["git", "ls-tree", "HEAD", rel],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    metadata, path = row.split("\t", 1)
    mode, object_type, _object_id = metadata.split()
    if path != rel or object_type != "blob" or mode not in {"100644", "100755"}:
        raise RuntimeError(f"unsupported tracked entry: {row}")
    return mode


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
        p.name
        for p in d.iterdir()
        if p.name not in IGNORE_NAMES and not p.name.startswith(".")
    )


def _tracked_mode(info: os.stat_result) -> str | None:
    if not stat.S_ISREG(info.st_mode):
        return None
    return "100755" if info.st_mode & 0o111 else "100644"


def _regular_bytes(
    path: Path,
    label: str,
    expected_mode: str,
    problems: list[str],
) -> bytes | None:
    """Read one exact regular entry without following or racing a symlink."""
    try:
        before = path.lstat()
    except OSError as exc:
        problems.append(f"{label} MISSING — {exc}")
        return None
    actual_mode = _tracked_mode(before)
    if actual_mode != expected_mode:
        shown = actual_mode or f"type {stat.S_IFMT(before.st_mode):#o}"
        problems.append(
            f"{label} TYPE/MODE — expected regular {expected_mode}, got {shown}"
        )
        return None
    fd = -1
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(fd)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or _tracked_mode(opened) != expected_mode
        ):
            problems.append(f"{label} TYPE/MODE — entry changed before read")
            return None
        chunks = []
        size = 0
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        if (
            (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or size != opened.st_size
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or _tracked_mode(after) != expected_mode
        ):
            problems.append(f"{label} TYPE/MODE — entry changed during read")
            return None
        return b"".join(chunks)
    except OSError as exc:
        problems.append(f"{label} TYPE/MODE — safe read failed: {exc}")
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _real_directory(path: Path, label: str) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        print(f"verify_harness_copy: FAIL — {label} missing: {exc}", file=sys.stderr)
        return False
    if not stat.S_ISDIR(info.st_mode):
        print(
            f"verify_harness_copy: FAIL — {label} must be a real directory",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    problems: list[str] = []

    if not _real_directory(COPY_DIR, str(COPY_DIR)):
        return 1
    if not _real_directory(LIVE_DIR, str(LIVE_DIR)):
        return 1

    expected = head_files()
    expected_modes = head_modes()
    if set(expected_modes) != set(expected):
        print(
            "verify_harness_copy: FAIL — tracked harness names/modes disagree",
            file=sys.stderr,
        )
        return 1

    # --- LEG 1: the copy IS the live harness, modulo the pinned allowlist --
    deviations: list[str] = []
    live_bytes: dict[str, bytes] = {}
    for name in expected:
        live, copy = LIVE_DIR / name, COPY_DIR / name
        mode = expected_modes[name]
        live_data = _regular_bytes(
            live, f"LEG1 LIVE {name}", mode, problems
        )
        copy_data = _regular_bytes(
            copy, f"LEG1 COPY {name}", mode, problems
        )
        if live_data is None or copy_data is None:
            continue
        live_bytes[name] = live_data
        live_sha, copy_sha = _sha(live_data), _sha(copy_data)
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
            live_data.decode("utf-8", errors="replace"),
            copy_data.decode("utf-8", errors="replace"),
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
        if name not in live_bytes:
            continue
        want, got = _sha(head_blob(name)), _sha(live_bytes[name])
        if want != got:
            problems.append(
                f"LEG2 EDITED {name} — firmware/esp32/ is read-only to this tree but "
                f"this file differs from HEAD\n           HEAD {want}\n           live {got}"
            )

    for name in listing(LIVE_DIR):
        if name not in expected:
            problems.append(f"LEG2 EXTRA {name} — a new file appeared in the committed harness")

    # --- Rust smoke strengthening: exact bytes, type and executable mode ---
    rust_smoke = HERE / "qemu_smoke.sh"
    pinned_smoke, smoke_reason = SMOKE_STRENGTHENING
    rust_smoke_data = _regular_bytes(
        rust_smoke,
        "LEG1 COPY qemu_smoke.sh",
        "100755",
        problems,
    )
    if rust_smoke_data is None:
        pass
    elif _sha(rust_smoke_data) != pinned_smoke:
        problems.append(
            "LEG1 PIN MISMATCH qemu_smoke.sh — Rust provenance wrapper "
            f"differs from approved bytes\n           pinned {pinned_smoke}\n"
            f"           copy   {_sha(rust_smoke_data)}"
        )
    else:
        print("verify_harness_copy: APPROVED STRENGTHENING — qemu_smoke.sh")
        print(f"  reason: {smoke_reason}")

    # --- LEG 2b: the OTHER committed gate script, anchored the same way ----
    smoke_live = REPO_ROOT / SMOKE_REL
    live_smoke_data = _regular_bytes(
        smoke_live,
        "LEG2 LIVE qemu_smoke.sh",
        head_mode_at(SMOKE_REL),
        problems,
    )
    if live_smoke_data is not None:
        want, got = _sha(head_blob_at(SMOKE_REL)), _sha(live_smoke_data)
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
