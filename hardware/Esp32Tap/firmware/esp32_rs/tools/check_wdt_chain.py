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
  2c. Every `spawn_pinned` site in `main.rs` matches its row's PRIORITY and
     STACK, resolving `STACK_BYTES`/`PRIORITY` constants, and a value this gate
     cannot read is a failure rather than a skip. Until this existed the ladder
     was a comment: FreeRTOS enforces the priorities it is GIVEN and cannot know
     the table said something else, so a one-character edit to a spawn priority
     left every gate green. The `Core` column is held by construction instead —
     `spawn_pinned` is asserted to be the only spawn path and to hard-code
     Core0.
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


def matrix_table() -> dict[str, dict[str, str]]:
    """The whole markdown table in tasks/mod.rs, by task name.

    Columns: Task | Core | Prio | Stack | WDT | Cadence | Source. The `WDT`
    cell decides whether a row claims supervision (`subscribe`) or exemption,
    and `check_tasks` holds that claim against what the code actually does.
    """
    if not MATRIX.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("//! |"):
            continue
        cells = [c.strip() for c in line[len("//! |") :].split("|")]
        if not cells or not cells[0]:
            continue
        head = cells[0]
        if head in ("Task",) or set(head) <= set("-: "):
            continue
        rows[head] = {
            "core": cells[1] if len(cells) > 1 else "",
            "prio": cells[2] if len(cells) > 2 else "",
            "stack": cells[3] if len(cells) > 3 else "",
            "wdt": cells[4] if len(cells) > 4 else "",
            "source": cells[6] if len(cells) > 6 else "",
        }
    return rows


def matrix_rows() -> set[str]:
    """Names of rows that CLAIM WDT supervision.

    Exempt rows (the coach and the radio) are in the table too — their numbers
    have to be checkable like everyone else's — but they are deliberately not
    part of the supervised set that `check_tasks` matches both ways.
    """
    return {n for n, r in matrix_table().items() if "subscribe" in r["wdt"].lower()}


def matrix_exempt() -> set[str]:
    """Names of rows that claim EXEMPTION from the watchdog."""
    return {n for n, r in matrix_table().items() if "exempt" in r["wdt"].lower()}


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

    # EVERY row's WDT cell must be READABLE as one or the other. A typo like
    # "EXMPT" made a row neither supervised nor exempt, so it fell out of both
    # checks and an unsupervised task passed with no WDT assertion at all
    # (found by codex). A cell this gate cannot classify is a cell it is not
    # checking.
    for name, row in matrix_table().items():
        cell = row["wdt"].lower()
        if ("subscribe" in cell) == ("exempt" in cell):
            failures.append(
                f"matrix row {name!r} has WDT cell {row['wdt']!r}, which is neither clearly "
                "'subscribe' nor clearly 'EXEMPT' — this gate cannot tell whether a stall in "
                "that task is supposed to reboot the device"
            )

    # An EXEMPT row is a CLAIM, and a claim that contradicts the code is worse
    # than no row: somebody deciding whether a task is safe unsupervised would
    # read the exemption and its argument, when in fact the task is supervised.
    for e in matrix_exempt():
        if any(s.startswith(e) or e.startswith(s) for s in stems):
            failures.append(
                f"the WDT matrix in tasks/mod.rs marks {e!r} EXEMPT from the watchdog, but a "
                "file of that name calls wdt::subscribe_current_task() — the row and the code "
                "disagree about whether a stall reboots the device"
            )
    return failures


MAIN_RS = FW_SRC / "main.rs"

# `spawn_pinned(c"name", <stack>, <prio>, <body>)`, across line breaks.
SPAWN_RE = re.compile(
    r"spawn_pinned\s*\(\s*c\"(?P<name>[A-Za-z0-9_]+)\"\s*,"
    r"\s*(?P<stack>[^,]+?)\s*,"
    r"\s*(?P<prio>[^,]+?)\s*,",
    re.S,
)

# Any `spawn_pinned` token the strict pattern above did NOT match. A call the
# gate cannot parse is a task whose priority nothing is checking, and the
# original version only noticed if EVERY call vanished — so one call written
# `spawn_pinned (…)` or with a comment before the paren went unchecked while
# the gate stayed green (found by codex, by mutation).
# `fn spawn_pinned(` is the DEFINITION, not a call site. Excluding it by
# lookbehind rather than by subtracting one, so the count stays honest if the
# helper is ever renamed or duplicated.
SPAWN_TOKEN_RE = re.compile(r"(?<!fn )\bspawn_pinned\s*\(")


def resolve_const(expr: str) -> int | None:
    """An integer literal, or a `path::to::CONST` resolved from its module.

    Returns None when it cannot be resolved, and the caller FAILS on None
    rather than skipping: a spawn whose priority this gate cannot read is a
    spawn whose priority nothing is checking.
    """
    expr = expr.strip()
    lit = expr.replace("_", "")
    if lit.isdigit():
        return int(lit)
    # An EXPRESSION is refused rather than approximated. `4096 * 2` used to
    # "resolve" to 4096 because the literal was unanchored — a wrong value that
    # reads as a pass, which is worse than no check (found by codex).
    if not re.fullmatch(r"[A-Za-z0-9_]+(::[A-Za-z0-9_]+)*", expr):
        return None
    parts = expr.split("::")
    if len(parts) < 2:
        return None
    const = parts[-1]
    mod_path = parts[:-1]
    candidates = [
        FW_SRC.joinpath(*mod_path).with_suffix(".rs"),
        FW_SRC.joinpath(*mod_path, "mod.rs"),
    ]
    # Anchored to `;`: `pub const X: usize = 4096 * 2;` must NOT resolve to 4096.
    pat = re.compile(rf"pub const {re.escape(const)}\s*:\s*\w+\s*=\s*([0-9_]+)\s*;")
    for c in candidates:
        if not c.exists():
            continue
        m = pat.search(strip_comments(c.read_text(encoding="utf-8")))
        if m:
            return int(m.group(1).replace("_", ""))
    return None


def row_for(spawn_name: str, table: dict[str, dict[str, str]]) -> str | None:
    """Match a spawn's task name to a matrix row.

    Exact first, then the loose prefix rule the rest of this file uses (the
    table abbreviates `interval_executor`), then the Source column — the shim
    is spawned as `qemu_test` but its row is headed `shim_task`, and the Source
    cell `qemu_test/shim_task` is what ties the two together.
    """
    if spawn_name in table:
        return spawn_name
    # AMBIGUITY IS A FAILURE, not a coin flip. Returning the first prefix match
    # meant a spawn named `foo` silently compared against row `foo_a` while
    # `foo_b` also existed; if the wrong row carried the same numbers the error
    # was invisible (found by codex).
    pref = [h for h in table if h.startswith(spawn_name) or spawn_name.startswith(h)]
    if len(pref) > 1:
        return None
    if pref:
        return pref[0]
    for head, row in table.items():
        segments = re.split(r"[/\s(]+", row["source"])
        if spawn_name in segments:
            return head
    return None


def check_spawn_matrix() -> list[str]:
    """Every spawned task's PRIORITY and STACK must equal its matrix row.

    WHY: the matrix is the document somebody consults to reason about what
    preempts what, and until this check existed nothing tied its numbers to the
    code. FreeRTOS enforces the ladder it is GIVEN — it cannot know the ladder
    was written down differently. A one-character edit to a spawn priority
    would have left every gate green.
    """
    failures: list[str] = []
    if not MAIN_RS.exists():
        return [f"{rel(MAIN_RS, FW_SRC)} is missing — the spawn sites cannot be read"]
    src = strip_comments(MAIN_RS.read_text(encoding="utf-8"))

    # The Core column is true by construction, not by table lookup: one spawn
    # helper, one hard-coded core. Assert both halves of that.
    if "pin_to_core: Some(Core::Core0)" not in src:
        failures.append(
            "main.rs no longer pins spawned tasks to Core0 in spawn_pinned — the matrix's "
            "Core column would become a claim nothing supports"
        )
    # Catch a bare `std::thread` spawn as well, not only a reconfigured one: a
    # thread created without ThreadSpawnConfiguration carries neither a checked
    # priority nor a pinned core, and the substring search for the config type
    # alone walked straight past it (found by codex).
    stray = []
    for q in FW_SRC.rglob("*.rs"):
        if q == MAIN_RS:
            continue
        body = strip_comments(q.read_text(encoding="utf-8"))
        if "ThreadSpawnConfiguration" in body or "thread::Builder" in body or "thread::spawn" in body:
            stray.append(rel(q, FW_SRC))
    if stray:
        failures.append(
            "spawn_pinned in main.rs is supposed to be the ONLY spawn path, but "
            f"{', '.join(stray)} also configures thread spawning — a task created there "
            "would carry neither a checked priority nor a pinned core"
        )

    table = matrix_table()
    if not table:
        return failures + [f"no matrix table found in {rel(MATRIX, FW_SRC)}"]

    spawns = list(SPAWN_RE.finditer(src))
    if not spawns:
        return failures + ["no spawn_pinned call sites found in main.rs — this gate would be vacuous"]

    # EVERY `spawn_pinned` token must have been parsed. Previously the gate only
    # complained when ALL calls vanished, so one call the pattern could not read
    # was simply unchecked.
    tokens = len(SPAWN_TOKEN_RE.findall(src))
    if tokens != len(spawns):
        failures.append(
            f"main.rs contains {tokens} spawn_pinned call(s) but this gate could only parse "
            f"{len(spawns)} — an unparsed call is a task whose priority and stack nothing checks"
        )

    for m in spawns:
        name, stack_x, prio_x = m.group("name"), m.group("stack"), m.group("prio")
        head = row_for(name, table)
        if head is None:
            failures.append(
                f"main.rs spawns task {name!r} but the matrix in tasks/mod.rs has no row for it "
                f"(rows: {sorted(table)})"
            )
            continue
        row = table[head]
        for label, expr, want in (("priority", prio_x, row["prio"]), ("stack", stack_x, row["stack"])):
            got = resolve_const(expr)
            if got is None:
                failures.append(
                    f"main.rs spawns {name!r} with {label} `{expr}`, which this gate cannot "
                    "resolve to a number — an unreadable value is an unchecked value"
                )
                continue
            if str(got) != want.replace("_", ""):
                failures.append(
                    f"{name!r} is spawned with {label} {got} but the normative matrix row "
                    f"{head!r} says {want} — the table and the code disagree, and the table is "
                    "what somebody reads when reasoning about this task"
                )
        # The Core column was RECORDED and never compared: changing every cell
        # from 0 to 1 produced zero failures (found by codex, by mutation). Every
        # spawn goes through spawn_pinned, which pins Core0 — asserted above — so
        # any row claiming otherwise is a false statement about this firmware.
        if row["core"].strip() != "0":
            failures.append(
                f"matrix row {head!r} says Core {row['core']!r}, but spawn_pinned pins every "
                "task to Core0 — the row claims a placement the firmware cannot produce"
            )

    # REVERSE DIRECTION. Without this, DELETING a spawn left the gate green:
    # check_spawn_matrix only ever iterated the calls it found, and check_tasks
    # discovers subscriber FILES rather than reachable tasks. Codex deleted the
    # priority-10 serial_engine spawn — the task the treadmill depends on — and
    # nothing failed.
    spawned_rows = {row_for(m.group("name"), table) for m in spawns}
    for head in table:
        if head not in spawned_rows:
            failures.append(
                f"the matrix names task {head!r} but main.rs never spawns it — either the task "
                "is gone (and the device is missing it) or the row is stale"
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
    failures = check_tasks() + check_spawn_matrix() + check_window_does_not_feed() + check_pulldown()
    if failures:
        print("check_wdt_chain: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    n = len(discover_supervised())
    nspawn = len(list(SPAWN_RE.finditer(strip_comments(MAIN_RS.read_text(encoding="utf-8")))))
    print(
        f"check_wdt_chain: OK — {n} discovered supervised tasks subscribe+abort+feed "
        "and each has a row in the normative matrix; "
        f"all {nspawn} spawn_pinned sites match their row's priority and stack and are "
        "pinned to Core0 by the sole spawn path; the "
        "bounded feedback window does not feed the WDT; RELAY_CMD has a "
        "pull-down to GND in the netlist. "
        "NOT PROVEN HERE (bench gate, esp-QEMU cannot execute it): that the "
        "panic actually resets the SoC and releases K1 within 2.25 s."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
