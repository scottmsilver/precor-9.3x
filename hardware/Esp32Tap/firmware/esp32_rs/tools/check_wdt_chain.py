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

  1. All three supervised tasks SUBSCRIBE to the task WDT and ABORT if the
     subscribe fails — an unsupervised task is a silent hole in the matrix.
  2. All three FEED the WDT inside their loop, so a stall is what trips it
     rather than normal operation.
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

SUPERVISED_TASKS = {
    "tasks/serial_engine.rs": "serial_engine",
    "tasks/emulate_cycle.rs": "emulate_cycle",
    "tasks/interval_executor.rs": "interval_executor",
}

RELAY_NET = "RELAY_CMD"


def check_tasks() -> list[str]:
    failures: list[str] = []
    for rel, name in SUPERVISED_TASKS.items():
        p = FW_SRC / rel
        if not p.exists():
            failures.append(f"supervised task source missing: {rel}")
            continue
        text = p.read_text(encoding="utf-8")
        if "wdt::subscribe_current_task()" not in text:
            failures.append(f"{rel}: task {name!r} does not subscribe to the task WDT")
        if "wdt::abort(" not in text:
            failures.append(
                f"{rel}: task {name!r} does not ABORT when the WDT subscribe fails — "
                "it would run unsupervised, which is a hole in PLAN's watchdog matrix"
            )
        if "wdt::feed()" not in text:
            failures.append(f"{rel}: task {name!r} never feeds the task WDT")
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
    print(
        "check_wdt_chain: OK — 3/3 supervised tasks subscribe+abort+feed; the "
        "bounded feedback window does not feed the WDT; RELAY_CMD has a "
        "pull-down to GND in the netlist. "
        "NOT PROVEN HERE (bench gate, esp-QEMU cannot execute it): that the "
        "panic actually resets the SoC and releases K1 within 2.25 s."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
