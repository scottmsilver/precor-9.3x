#!/usr/bin/env python3
"""Gate a GENERATED sdkconfig with build_safety_manifest.py's own rules.

Why this exists
---------------
tools/build.sh used to gate the generated sdkconfig with two hand-picked
greps (CONFIG_ESP_TASK_WDT_PANIC=y, CONFIG_FREERTOS_HZ=1000). That is not
the mandated gate — it is a subset of it, and the subset has a hole:
CONFIG_ESP_DEBUG_OCDAWARE defaults to =y in ESP-IDF, PLAN.md forbids it in
an Emulate-capable build (with OCDAWARE on, the panic handler consults
esp_cpu_dbgr_is_attached() and breaks/halts instead of resetting, so GPIO21
stays driven and the relay stays energized), and build_safety_manifest.py
fail-closes on it. The Rust sdkconfig.defaults omitted the
`# CONFIG_ESP_DEBUG_OCDAWARE is not set` line the C++ core carries, and the
two greps passed anyway. This script closes that.

It deliberately does NOT restate the rules: every key set is read out of
build_safety_manifest.py, which stays the single source of truth. The read
is done with `ast.literal_eval` over the parsed source rather than by
importing the module, because build_safety_manifest.py imports `jsonschema`
and this gate has to run inside the ESP-IDF build container, whose Python
has no third-party packages. A renamed or deleted constant is a HARD ERROR
here, so the indirection can never silently disable the gate.

Scope: everything in the manifest's sdkconfig validation that does not
require a physical measurement. The brownout SELECTOR check is the one
deferred rule — it needs the measured minimum +3V3 of the exact production
artifact, which does not exist yet (PLAN "Exact production artifact
identity", project status HOLD). That gate is not weakened here, it is
still owned by build_safety_manifest.py at manifest time; this script
reports it as OUTSTANDING so it cannot be forgotten.

Usage:
    check_sdkconfig.py <generated-sdkconfig> [--label NAME] [--allow-qemu]

--allow-qemu relaxes exactly the keys sdkconfig.defaults.qemu deliberately
flips for the TEST image (panic print-reboot, so a harness dump shows why a
guest died). The test image is never flashed to hardware; the production
image is checked with no relaxations at all.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRMWARE_DIR = HERE.parents[1]  # hardware/Esp32Tap/firmware
MANIFEST_SRC = FIRMWARE_DIR / "build_safety_manifest.py"

WANTED = (
    "REQUIRED_SDKCONFIG",
    "FORBIDDEN_ENABLED_SDKCONFIG",
    "OPTIONAL_ZERO_SDKCONFIG",
    "PANIC_SELECTORS",
    "COREDUMP_SELECTORS",
    "APPTRACE_PRIMARY_SELECTORS",
    "APPTRACE_UART_SELECTORS",
)

# Keys the QEMU-TEST image is allowed to differ on, and only it.
QEMU_RELAXED_REQUIRED = ("CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT",)
QEMU_RELAXED_FORBIDDEN = ("CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT",)


def load_rules() -> dict:
    """Read the manifest's own key sets without importing it."""
    tree = ast.parse(MANIFEST_SRC.read_text(encoding="utf-8"))
    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in WANTED:
            continue
        out[target.id] = ast.literal_eval(node.value)
    missing = [name for name in WANTED if name not in out]
    if missing:
        raise SystemExit(
            f"check_sdkconfig: FATAL — {MANIFEST_SRC.name} no longer defines "
            f"{missing}. The gate cannot be derived from the source of truth; "
            "fix the names rather than skipping the check."
        )
    return out


def parse_sdkconfig(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; `# ... is not set` comments mean 'absent'.

    Mirrors build_safety_manifest._parse_sdkconfig. Eight lines of trivial
    parsing is the price of not importing a jsonschema-dependent module into
    the build container.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Duplicates are REJECTED, not last-wins, exactly as the manifest
        # parser does. Last-wins would let `CONFIG_ESP_DEBUG_OCDAWARE=y`
        # followed by a later `=n` pass this gate while the manifest builder
        # refuses the same file — i.e. a way to slip a forbidden key past the
        # build without the artifact ever being shippable.
        if key in values:
            raise SystemExit(f"check_sdkconfig: FAIL ({path}) — duplicate sdkconfig key " f"{key} at line {number}")
        values[key] = value
    return values


def check(path: Path, rules: dict, *, allow_qemu: bool) -> list[str]:
    values = parse_sdkconfig(path)
    problems: list[str] = []

    for key, expected in rules["REQUIRED_SDKCONFIG"].items():
        if allow_qemu and key in QEMU_RELAXED_REQUIRED:
            continue
        if values.get(key) != expected:
            problems.append(f"require {key}={expected} (got {values.get(key)!r})")

    for key in rules["FORBIDDEN_ENABLED_SDKCONFIG"]:
        if allow_qemu and key in QEMU_RELAXED_FORBIDDEN:
            continue
        if values.get(key) == "y":
            problems.append(f"{key}=y is forbidden")

    for key in rules["OPTIONAL_ZERO_SDKCONFIG"]:
        if key in values and values[key] != "0":
            problems.append(f"{key} must be absent or zero (got {values[key]!r})")

    targets = sorted(
        key
        for key, value in values.items()
        if key.startswith("CONFIG_IDF_TARGET_") and not key.startswith("CONFIG_IDF_TARGET_ARCH_") and value == "y"
    )
    if targets != ["CONFIG_IDF_TARGET_ESP32S3"]:
        problems.append(f"target selector must be exactly ESP32S3 (got {targets})")

    def exact(choices, expected, label):
        enabled = sorted(k for k in choices if values.get(k) == "y")
        if enabled != [expected]:
            problems.append(f"{label} must select only {expected} (got {enabled})")

    # Panic behaviour: production must be exactly SILENT_REBOOT. The QEMU-test
    # image is allowed PRINT_REBOOT (sdkconfig.defaults.qemu flips it so a
    # harness dump shows WHY a guest died) — but it must still select EXACTLY
    # ONE, and it must still be a reboot. Skipping the check for --allow-qemu
    # would let a config with NO panic selector, or with a HALT/GDBSTUB
    # selector alongside, pass; PRINT_HALT and GDBSTUB never release the relay.
    panic_allowed = ["CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT"]
    if allow_qemu:
        panic_allowed.append("CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT")
    panic_on = sorted(k for k in rules["PANIC_SELECTORS"] if values.get(k) == "y")
    if len(panic_on) != 1 or panic_on[0] not in panic_allowed:
        problems.append(f"panic behavior must select exactly one of {panic_allowed} " f"(got {panic_on})")
    exact(
        rules["COREDUMP_SELECTORS"],
        "CONFIG_ESP_COREDUMP_ENABLE_TO_NONE",
        "core dump destination",
    )
    exact(
        rules["APPTRACE_PRIMARY_SELECTORS"],
        "CONFIG_APPTRACE_DEST_NONE",
        "apptrace destination",
    )
    exact(
        rules["APPTRACE_UART_SELECTORS"],
        "CONFIG_APPTRACE_DEST_UART_NONE",
        "apptrace UART destination",
    )

    # Not a manifest rule, but PLAN-normative for this firmware and cheap to
    # assert on the same pass (it is also the second of the two greps this
    # script replaces).
    if values.get("CONFIG_FREERTOS_HZ") != "1000":
        problems.append(f"require CONFIG_FREERTOS_HZ=1000 (got {values.get('CONFIG_FREERTOS_HZ')!r})")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sdkconfig", type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--allow-qemu", action="store_true")
    args = ap.parse_args(argv)

    label = args.label or str(args.sdkconfig)
    problems = check(args.sdkconfig, load_rules(), allow_qemu=args.allow_qemu)
    if problems:
        print(f"check_sdkconfig: FAIL ({label})")
        for p in problems:
            print(f"  - unsafe sdkconfig: {p}")
        return 1
    kind = "qemu-test" if args.allow_qemu else "production"
    print(f"check_sdkconfig: OK ({label}, {kind} rules)")
    if not args.allow_qemu:
        print(
            "check_sdkconfig: OUTSTANDING — the brownout SELECTOR gate still "
            "requires the measured minimum +3V3 of the production artifact "
            "and is enforced by build_safety_manifest.py at manifest time."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
