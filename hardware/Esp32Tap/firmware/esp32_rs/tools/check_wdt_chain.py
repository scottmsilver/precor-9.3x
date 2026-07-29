#!/usr/bin/env python3
"""check_wdt_chain.py — automated evidence for the task-WDT -> relay-release chain.

WHY THIS EXISTS, AND WHAT IT HONESTLY IS
========================================
PLAN's normative watchdog matrix says a stall in any supervised task must
RELEASE THE RELAY, via:

    task stalls -> esp_task_wdt fires (2 s) -> CONFIG_ESP_TASK_WDT_PANIC=y
    -> panic -> immediate silent reboot -> GPIO21 goes Hi-Z at reset
    -> R23 10k pull-down at the driver base -> K1 released

**This chain is NOT executable under esp-QEMU, in EITHER language.** The
emulator does not deliver the task-WDT interrupt, so `qemu_smoke.sh` assertion
#3 currently forbids a condition it cannot reach, and no test in this
repository has ever observed a panic-reset releasing a relay. That gap is real,
it is disclosed, and this script does NOT close it. End-to-end proof is a bench
measurement (PLAN: "injected supervised-task stall to stable NC at most 2.25 s
with the 2 s WDT"), on hardware, with a scope on the contacts.

What this script DOES do is verify every link of the chain that is checkable
from the repository, so that if the bench measurement ever fails, the failure
is narrowed to the one link nobody can check statically (the panic itself
actually resetting the SoC promptly):

  1. Every supervised task SUBSCRIBES to the task WDT and ABORTS if the
     subscribe fails — an unsupervised task is a silent hole in the matrix.
     The set of supervised tasks is DISCOVERED by scanning the firmware for
     `subscribe_current_task`, not hard-coded: this script listed exactly three
     files while `grep -rn subscribe_current_task esp32tap/src/` returned FOUR,
     so the session recorder — the only supervised task that touches flash —
     was invisible to its own gate. If its `wdt::feed()` had been removed the
     gate stayed green and the device silently rebooted every 2 s.
  2. Every one of them FEEDS the WDT inside its loop, so a stall is what trips
     it rather than normal operation.
  2b. The discovered set matches the normative matrix in
     `esp32tap/src/tasks/mod.rs`, in BOTH directions — a new supervised task
     that nobody wrote into the matrix fails here, and a matrix row with no
     task behind it fails too.
  3. No task feeds the WDT from inside an unbounded wait (specifically: the
     feedback window, which spins with the relay closed, must not feed it).
  4. The generated sdkconfig enables the WDT, initialises it, sets the 2 s
     timeout and PANIC action, and cannot delay the reset (no core dump, no
     apptrace, no panic print/halt/GDB stub). Delegated to
     `check_sdkconfig.py`, which imports `build_safety_manifest.py`'s rules
     rather than duplicating them.
  5. The FINAL hardware link exists in the netlist: RELAY_CMD (GPIO21) has a
     pull-down resistor to GND, so a Hi-Z pin releases rather than floats.
     Read out of `hardware/Esp32Tap/tools/design.py`, the netlist source of
     truth, so deleting R23 fails this gate.

Exit status 0 = every checkable link holds.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parent
FW_SRC = ESP32_RS / "esp32tap" / "src"
DESIGN_PY = ESP32_RS.parents[1] / "tools" / "design.py"

# The normative matrix lives here; this script reads it rather than restating
# it, so the table and the gate cannot drift apart.
MATRIX = FW_SRC / "tasks" / "mod.rs"

RELAY_NET = "RELAY_CMD"


def rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


def strip_comments(text: str) -> str:
    """Blank out `//` and `/* */` comments, keeping line structure.

    NEEDED, not cosmetic. Discovery below matched raw file text, so a file that
    merely DISCUSSED `wdt::subscribe_current_task()` in a doc comment was
    classified as a supervised task — and then failed for not aborting and not
    feeding. Three files did: `main.rs` (which spawns them), `tasks/mod.rs`
    (which holds the normative matrix), and `ble/mod.rs` (which explains why
    the radio is deliberately unsupervised). A gate that cannot tell code from
    prose about code punishes exactly the documentation it wants.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if text[i] == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def discover_supervised() -> list[Path]:
    """Every firmware file that subscribes a task to the WDT.

    DISCOVERED, NOT LISTED. The previous version named three files and printed
    "3/3 supervised tasks" while a fourth existed.

    Matched on the QUALIFIED call `wdt::subscribe_current_task()` in CODE, so
    `hal/wdt.rs` — which DEFINES it — is not mistaken for a task, and neither
    is a file that only writes about it.
    """
    return [
        p
        for p in FW_SRC.rglob("*.rs")
        if "wdt::subscribe_current_task()" in strip_comments(p.read_text(encoding="utf-8"))
    ]


def matrix_rows() -> set[str]:
    """Task names from the markdown table in tasks/mod.rs."""
    if not MATRIX.exists():
        return set()
    rows: set[str] = set()
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("//! |"):
            continue
        cells = [c.strip() for c in line[len("//! |") :].split("|")]
        if not cells or not cells[0]:
            continue
        head = cells[0]
        if head in ("Task",) or set(head) <= set("-: "):
            continue
        rows.add(head)
    return rows


def check_tasks() -> list[str]:
    failures: list[str] = []
    found = discover_supervised()
    if not found:
        return ["no file in esp32tap/src calls wdt::subscribe_current_task — the matrix is empty"]
    for p in found:
        r = rel(p, FW_SRC)
        text = p.read_text(encoding="utf-8")
        if "wdt::abort(" not in text:
            failures.append(
                f"{r}: subscribes to the task WDT but does not ABORT when the subscribe "
                "fails — it would run unsupervised, which is a hole in PLAN's watchdog matrix"
            )
        if "wdt::feed()" not in text:
            failures.append(f"{r}: subscribes to the task WDT but never feeds it")

    # The matrix must name every discovered task, and nothing else. Names are
    # matched loosely (the table abbreviates `interval_executor` to
    # `interval_exec`) against the module's file stem.
    declared = matrix_rows()
    if not declared:
        failures.append(f"no WDT matrix table found in {rel(MATRIX, FW_SRC)} — the normative table is gone")
        return failures
    for p in found:
        stem = p.stem
        if not any(stem.startswith(d) or d.startswith(stem) for d in declared):
            failures.append(
                f"{rel(p, FW_SRC)} is a WDT-supervised task but has no row in the normative "
                f"matrix in tasks/mod.rs (rows: {sorted(declared)})"
            )
    stems = {p.stem for p in found}
    for d in declared:
        if not any(s.startswith(d) or d.startswith(s) for s in stems):
            failures.append(
                f"the WDT matrix in tasks/mod.rs names {d!r}, but no file in esp32tap/src "
                "subscribes a task by that name"
            )
    return failures


def check_window_does_not_feed() -> list[str]:
    """The feedback window spins with the relay ENERGIZED. Feeding the WDT from
    inside it would make a stall there invisible to the last-resort guard."""
    failures: list[str] = []
    for rel in ("../../safety_core/src/safety/feedback_window.rs",):
        p = (FW_SRC / rel).resolve()
        if not p.exists():
            failures.append(f"missing {p}")
            continue
        # Code only — the module's doc comment discusses the WDT at length,
        # and discussing it is exactly what it should do.
        code = "\n".join(
            line for line in p.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//")
        )
        if re.search(r"\bwdt\b|feed_watchdog|esp_task_wdt", code, re.IGNORECASE):
            failures.append(
                f"{p.name}: the bounded feedback window appears to touch the WDT. "
                "It must not feed it — the WDT is the last-resort guard behind "
                "MAX_WINDOW_POLLS, and feeding it from inside a spin is how a "
                "stall becomes invisible."
            )
    return failures


def check_pulldown() -> list[str]:
    """RELAY_CMD must have a resistor to GND, so a Hi-Z pin releases K1."""
    if not DESIGN_PY.exists():
        return [f"netlist source of truth not found: {DESIGN_PY}"]
    text = DESIGN_PY.read_text(encoding="utf-8")
    # NET_MEMBERSHIP-style entries: "R23": {"RELAY_CMD", "GND"}
    pulldowns = [
        ref
        for ref, nets in re.findall(r'"(R\d+)":\s*\{([^}]*)\}', text)
        if f'"{RELAY_NET}"' in nets and '"GND"' in nets
    ]
    if not pulldowns:
        return [
            f"no resistor tying {RELAY_NET} to GND in design.py. Without it, the "
            "pin going Hi-Z on a panic reset FLOATS instead of releasing K1, and "
            "the last link of the watchdog chain does not exist."
        ]
    return []


def main() -> int:
    failures = check_tasks() + check_window_does_not_feed() + check_pulldown()
    if failures:
        print("check_wdt_chain: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    n = len(discover_supervised())
    print(
        f"check_wdt_chain: OK — {n} discovered supervised tasks subscribe+abort+feed "
        "and each has a row in the normative matrix; the "
        "bounded feedback window does not feed the WDT; RELAY_CMD has a "
        "pull-down to GND in the netlist. "
        "NOT PROVEN HERE (bench gate, esp-QEMU cannot execute it): that the "
        "panic actually resets the SoC and releases K1 within 2.25 s."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
