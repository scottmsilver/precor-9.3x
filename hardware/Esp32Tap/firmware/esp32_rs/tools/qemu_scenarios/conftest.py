"""Fixtures for the Rust-image-only QEMU scenarios.

These scenarios live OUTSIDE `firmware/esp32/tools/qemu_harness/` on purpose.
That directory is the COMMITTED gate and the mandate is that it runs against
the Rust image byte-for-byte unmodified; adding a file to it — even a purely
additive one — would make `git diff` non-empty and make the "unchanged
harness" claim unverifiable at a glance. So the committed harness is reused as
a LIBRARY (`QemuSession`, `synth`, and the scenario helpers) and the extra
coverage sits here.

The fixture is a deliberate ~20-line copy of the committed `qemu` factory
rather than a plugin import: it keeps this directory's collection independent
of the harness's own conftest, which is what lets `run_harness.sh` invoke the
committed harness in exactly its documented form and this directory
separately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ESP32_RS = HERE.parents[1]
REPO_ROOT = ESP32_RS.parents[3]
sys.path.insert(0, str(HERE.parent))
# The VERIFIED COPY of the committed harness (tools/qemu_harness/), whose ten
# files are asserted byte-identical to `git show HEAD:` on every run by
# tools/verify_harness_copy.py. Importing it from here rather than from the
# C++ tree is what lets `Path(__file__).parents[1]` inside the harness resolve
# to esp32_rs/ with NO environment variable and NO edit to a committed file.
HARNESS = ESP32_RS / "tools" / "qemu_harness"
sys.path.insert(0, str(HARNESS))

from artifact_provenance import shared_bundle  # noqa: E402
from qemu_session import QemuSession, _verify_current  # noqa: E402

ESP32_DIR = ESP32_RS
TEST_BUILD = "build_qemu_test"


@pytest.fixture(scope="session", autouse=True)
def _verified_test_bundle():
    with shared_bundle(REPO_ROOT, "qemu-test") as bundle:
        result = _verify_current(REPO_ROOT, "qemu-test", bundle)
        if not result.ok:
            pytest.fail(
                f"qemu-test artifact provenance failed: {result.message}",
                pytrace=False,
            )
        yield bundle


@pytest.fixture
def qemu(request, _verified_test_bundle):
    """Factory: boot the qemu-test image. Sessions are closed on teardown, and
    dumped first if the test failed."""
    sessions: list[QemuSession] = []

    def factory(**kwargs) -> QemuSession:
        s = QemuSession(ESP32_DIR, TEST_BUILD, **kwargs)
        sessions.append(s)
        return s

    yield factory

    failed = getattr(request.node, "rep_call", None)
    crashes: list[str] = []
    for s in sessions:
        try:
            if failed is not None and failed.failed:
                print(s.debug_dump())
            crashes += _crash_lines(s)
        finally:
            s.close()
    # A CRASH IS NAMED, NOT INFERRED. A stack overflow in the httpd task once
    # reached a test as `RemoteDisconnected: Remote end closed connection
    # without response` — which reads like a flaky socket and is in fact the
    # device REBOOTING, which drops the relay mid-run. Every session is checked
    # for the guest's own crash banners so the failure says what happened, and
    # so a crash during a test that otherwise PASSED cannot go unnoticed.
    if crashes:
        raise AssertionError("the guest crashed:\n  " + "\n  ".join(crashes))


# Substrings the guest only ever prints when something went badly wrong. Kept
# narrow on purpose: `qemu_smoke.sh` owns the exhaustive forbidden-string list,
# and a broad match here would fire on ordinary ESP_LOGE lines (the emulated
# NIC logs a benign multicast-filter error on every boot).
_CRASH_MARKERS = (
    "A stack overflow in task",
    "Guru Meditation Error",
    "Task watchdog got triggered",
    "assert failed:",
)


def _crash_lines(session) -> list[str]:
    try:
        lines = session.lines()
    except Exception:  # noqa: BLE001 — a session that cannot be read is the
        return []  # close() path's problem, not this check's
    return [ln.strip() for ln in lines if any(m in ln for m in _CRASH_MARKERS)]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
