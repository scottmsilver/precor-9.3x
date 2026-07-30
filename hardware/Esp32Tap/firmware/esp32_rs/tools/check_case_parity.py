#!/usr/bin/env python3
"""check_case_parity.py — turn "we ported 149 cases" into a build failure.

Asserts, at CASE granularity:

  1. the set of doctest ``TEST_CASE`` names in the SEVEN COMMITTED C++ host
     test files equals the set of ``#[test] fn`` names in the seven Rust test
     files, after normalisation;
  2. the 57 controller vectors still name their 1:1 counterparts in
     ``hardware/Esp32Tap/tests/test_firmware_safety_model.py``, allowing only
     the documented C++-only rows;
  3. ``ALLOWED_DIVERGENCES`` is EMPTY at case granularity.

Extra Rust tests (properties with no C++ twin) must be listed in
``RUST_ONLY_EXTRA`` with a reason, so they can never mask a dropped case.

Exit status 0 = parity holds. Anything else is a gate failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parent
FIRMWARE = ESP32_RS.parent
CPP_TESTS = FIRMWARE / "esp32" / "host" / "tests"
RUST_TESTS = ESP32_RS / "safety_core" / "tests"
PY_MODEL_TESTS = FIRMWARE.parent / "tests" / "test_firmware_safety_model.py"

# C++ binary -> Rust test file. These SEVEN are the committed safety core; the
# five uncommitted net-tier binaries are deliberately out of scope.
FILE_MAP = {
    "test_kv_protocol.cpp": "kv_protocol.rs",
    "test_mode_state.cpp": "mode_state.rs",
    "test_ring_buffer.cpp": "ring_buffer.rs",
    "test_emulation.cpp": "emulation.rs",
    "test_safety_controller.cpp": "safety_controller.rs",
    "test_safety_boot_envelope.cpp": "safety_boot_envelope.rs",
    "test_key_cache.cpp": "key_cache.rs",
}

EXPECTED_TOTAL = 149

# Rust tests with no C++ twin. Each needs a reason. They are NOT counted
# toward the 149.
RUST_ONLY_EXTRA = {
    "key_cache_prev_value_survives_auto_proxy": (
        "Replacement property for the one deliberately non-ported ASSERTION "
        "(the C++-only `prev.data() == buf.data()` aliasing check in "
        "test_key_cache case 2). PrevValue is owned+Copy, so the dangling-view "
        "hazard is structurally impossible; this proves the value survives a "
        "later cache mutation and the subsequent auto_proxy call."
    ),
    "_zero_frame_builder_shape": (
        "Ignored documentation helper (asserts the zero-frame builder shape); "
        "marked #[ignore] and not part of the 149."
    ),
    "connect_raw_rejects_a_negative_generation": (
        "The C++ validates generation < 0 INSIDE connect(), so there is no C++ "
        "case to be 1:1 with. This port makes an invalid identity "
        "unrepresentable and moves the rejection to the boundary form "
        "connect_raw; without this vector that boundary check would be "
        "unexercised and the connection_rejected:invalid_identity event "
        "unreachable in the Rust firmware."
    ),
    "plan_entry_step_6_holds_for_a_re_entry_inside_one_emulate_task_period": (
        "Regression guard for a defect the C++ EmulateTaskPolicy still has: it "
        "edge-detects emulate entry on a BOOL, which aliases two sessions when "
        "a gap-safe exit + re-entry fits inside one 100 ms sample period, so "
        "the second session's first burst carries the owner's motion (PLAN "
        "entry step 6 violation). The Rust policy takes an EmulateSessionId; "
        "this proves it. No C++ twin because the C++ has no such coverage."
    ),
}

# Python model cases with NO firmware twin, with the reason they cannot port.
PY_ONLY = {
    "test_wss_owner_requires_the_same_concrete_handle_object": (
        "ConnectionIdentity.handle is int32 (PLAN D5 phase-1 stand-in), so "
        "object-identity keying collapses to integer identity. Re-flag when "
        "the real WSS tier lands (M5)."
    ),
}

# Python model cases that are OUT OF SCOPE for the firmware, with the reason.
# These test build_safety_manifest.py, a Python BUILD TOOL — there is no
# firmware behaviour to port. The Rust image is one of its hashed inputs and
# its sdkconfig gate still applies (see tools/check_sdkconfig.py).
PY_OUT_OF_SCOPE_PREFIXES = ("test_safety_manifest_", "test_manifest_", "test_bundle_digest_")


# C++/Rust controller cases with NO 1:1 Python name.
#
# CORRECTED 2026-07-28 (reviewer finding: this set OVER-CLAIMED). It previously
# also listed the seven `entry_rejected_*` cases and
# `console_bridge_failure_matrix_remains_hardware_proxy` — eight cases that DO
# have model twins (`test_entry_preconditions` and the identically named
# `test_console_bridge_failure_matrix_remains_hardware_proxy`). Because
# membership here SKIPS the forward leg of the 3-way chain, those eight of the
# 57 controller vectors were never actually checked against the model. They are
# now checked like every other case.
#
# `_assert_cpp_only_is_not_overclaiming` below makes the over-claim impossible
# to reintroduce: any name in this set whose `// py:` annotation resolves to a
# real model test is a hard failure.
CPP_ONLY_CONTROLLER = {
    "motion_clamps_accept_boundary_values_and_reject_outside",
    "both_closed_latches_a_fault_and_releases_in_every_mode",
    "boot_state_is_proxy_with_unknown_feedback_and_no_outputs",
    "watchdog_stall_clears_connections_console_and_feedback_state",
    "partial_corrupt_and_oversized_frames_never_refresh",
}

# MUST STAY EMPTY. A non-empty list here means a case was dropped.
ALLOWED_DIVERGENCES: list[str] = []

DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}


def normalise(name: str) -> str:
    """Lowercase; every non-alphanumeric run becomes a single underscore.

    A Rust fn cannot begin with a digit, so a leading digit is spelled out
    (``3-hour timeout ...`` -> ``three_hour_timeout_...``).
    """
    s = re.sub(r"[^A-Za-z0-9]+", "_", name.lower()).strip("_")
    if s and s[0].isdigit():
        s = DIGIT_WORDS[s[0]] + s[1:]
    return s


def cpp_case_names(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [normalise(m) for m in re.findall(r'TEST_CASE\("([^"]*)"\)', text)]


def rust_test_names(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    # `#[test]` possibly followed by other attributes, then `fn name(`.
    return [m.group(1) for m in re.finditer(r"#\[test\][^\n]*\n(?:\s*#\[[^\]]*\]\s*\n)*\s*fn\s+(\w+)", text)]


def rust_py_annotations(path: Path) -> dict[str, str]:
    """Map each Rust test fn -> the ``test_*`` name in its nearest preceding
    ``// py:`` comment.

    A parenthesised annotation (``// py: (clamps per PLAN)``) names no twin and
    is skipped; those tests must appear in ``CPP_ONLY_CONTROLLER``. The
    nearest-preceding rule is what lets the seven ``entry_rejected_*`` cases
    share the single ``// py: test_entry_preconditions`` annotation, exactly as
    the C++ file does.
    """
    out: dict[str, str] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        m = re.match(r"//\s*py:\s*(.*)$", stripped)
        if m:
            body = m.group(1).strip()
            twin = re.match(r"(test_\w+)", body)
            current = twin.group(1) if twin else None
            continue
        fn = re.match(r"fn\s+(\w+)", stripped)
        if fn and current is not None:
            out[fn.group(1)] = current
    return out


def py_test_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^def (test_\w+)", text, re.MULTILINE))


def main() -> int:
    failures: list[str] = []
    total_cpp = 0
    total_rust_ported = 0

    for cpp_name, rust_name in FILE_MAP.items():
        cpp_path = CPP_TESTS / cpp_name
        rust_path = RUST_TESTS / rust_name
        if not cpp_path.exists():
            failures.append(f"MISSING C++ test file: {cpp_path}")
            continue
        if not rust_path.exists():
            failures.append(f"MISSING Rust test file: {rust_path}")
            continue

        cpp = cpp_case_names(cpp_path)
        rust = rust_test_names(rust_path)
        total_cpp += len(cpp)

        cpp_set, rust_set = set(cpp), set(rust)
        if len(cpp_set) != len(cpp):
            dupes = sorted({n for n in cpp if cpp.count(n) > 1})
            failures.append(f"{cpp_name}: duplicate normalised names {dupes}")

        extra = rust_set - cpp_set
        unexplained_extra = extra - set(RUST_ONLY_EXTRA)
        missing = cpp_set - rust_set

        total_rust_ported += len(rust_set & cpp_set)

        for m in sorted(missing):
            failures.append(f"{cpp_name} -> {rust_name}: NOT PORTED: {m}")
        for e in sorted(unexplained_extra):
            failures.append(
                f"{rust_name}: extra Rust test {e!r} is not in RUST_ONLY_EXTRA "
                "(add it with a reason, or it may be masking a dropped case)"
            )

    if total_cpp != EXPECTED_TOTAL:
        failures.append(
            f"C++ case total is {total_cpp}, expected {EXPECTED_TOTAL} — the "
            "committed safety-core corpus changed; reconcile deliberately."
        )
    if total_rust_ported != EXPECTED_TOTAL:
        failures.append(f"Ported case total is {total_rust_ported}, expected {EXPECTED_TOTAL}.")

    # --- 3-way chain: the 57 controller vectors vs the Python model ---------
    py_names = py_test_names(PY_MODEL_TESTS)
    if not py_names:
        failures.append(f"could not read Python model tests at {PY_MODEL_TESTS}")
    else:
        rust_controller = set(rust_test_names(RUST_TESTS / "safety_controller.rs"))
        checked_forward = 0
        # The `// py:` annotation carried over from the C++ file is the
        # AUTHORITATIVE mapping — several C++ titles deliberately abbreviate
        # the Python name (e.g. "only owner mutates or renews the single 4 s
        # lease" <-> test_only_owner_mutates_or_renews_the_single_four_second_lease),
        # so name normalisation alone would produce false failures.
        annotated = rust_py_annotations(RUST_TESTS / "safety_controller.rs")

        # An entry in CPP_ONLY_CONTROLLER is a CLAIM that no model twin exists.
        # Verify the claim instead of trusting it: if the case carries a
        # `// py: test_*` annotation naming a real model test, the claim is
        # false and the forward leg was being skipped for nothing.
        for name in sorted(CPP_ONLY_CONTROLLER):
            if name not in rust_controller:
                failures.append(
                    f"CPP_ONLY_CONTROLLER lists {name!r}, which is not a test in "
                    "safety_controller.rs — stale entry, remove it"
                )
                continue
            twin = annotated.get(name)
            if twin is not None and twin in py_names:
                failures.append(
                    f"CPP_ONLY_CONTROLLER OVER-CLAIM: {name!r} is listed as having "
                    f"no model twin, but it is annotated `// py: {twin}` and that "
                    "model test EXISTS. Remove it from CPP_ONLY_CONTROLLER so the "
                    "forward leg of the 3-way chain actually covers it."
                )

        for name in sorted(rust_controller):
            if name in CPP_ONLY_CONTROLLER or name in RUST_ONLY_EXTRA:
                continue
            checked_forward += 1
            py_twin = annotated.get(name)
            if py_twin is None:
                failures.append(
                    f"safety_controller.rs: {name!r} carries no `// py:` "
                    "annotation naming its model twin, and is not listed in "
                    "CPP_ONLY_CONTROLLER"
                )
            elif py_twin not in py_names:
                failures.append(
                    f"safety_controller.rs: {name!r} claims Python twin "
                    f"{py_twin!r}, which does not exist in "
                    f"{PY_MODEL_TESTS.name}"
                )
        for py_only, reason in PY_ONLY.items():
            if py_only in annotated.values():
                failures.append(
                    f"{py_only} is listed as PY_ONLY but a Rust twin exists; "
                    f"remove it from PY_ONLY. (reason on file: {reason})"
                )

        # --- REVERSE direction: Python -> firmware -------------------------
        # Without this the chain is only two-and-a-half-way: a NEW vector added
        # to the normative model (safety_model.py IS the contract, so that is
        # the likely direction of future drift) could sit there forever with no
        # firmware twin and nothing would fail. Every model case must be
        # claimed by a `// py:` annotation somewhere in the Rust corpus, or be
        # explicitly excluded with a reason.
        claimed: set[str] = set()
        for rust_name in FILE_MAP.values():
            path = RUST_TESTS / rust_name
            if path.exists():
                claimed.update(rust_py_annotations(path).values())
        for py_name in sorted(py_names):
            if py_name in claimed or py_name in PY_ONLY:
                continue
            if py_name.startswith(PY_OUT_OF_SCOPE_PREFIXES):
                continue
            failures.append(
                f"{PY_MODEL_TESTS.name}: model case {py_name!r} has NO firmware "
                "twin — add a Rust vector with a `// py:` annotation naming it, "
                "or list it in PY_ONLY / PY_OUT_OF_SCOPE_PREFIXES with a reason"
            )

    if ALLOWED_DIVERGENCES:
        failures.append("ALLOWED_DIVERGENCES must be empty at case granularity; found: " f"{ALLOWED_DIVERGENCES}")

    if failures:
        print("check_case_parity: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        f"check_case_parity: OK — {total_rust_ported}/{EXPECTED_TOTAL} cases "
        f"ported 1:1 across {len(FILE_MAP)} files; "
        f"{len(RUST_ONLY_EXTRA)} documented Rust-only extras; "
        f"{len(PY_ONLY)} documented Python-only case(s); "
        f"{checked_forward}/{len(rust_controller)} controller vectors "
        f"forward-checked against the model "
        f"({len(CPP_ONLY_CONTROLLER)} verified twin-less)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
