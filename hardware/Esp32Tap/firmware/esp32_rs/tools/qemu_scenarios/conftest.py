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
# The VERIFIED COPY of the committed harness (tools/qemu_harness/), whose ten
# files are asserted byte-identical to `git show HEAD:` on every run by
# tools/verify_harness_copy.py. Importing it from here rather than from the
# C++ tree is what lets `Path(__file__).parents[1]` inside the harness resolve
# to esp32_rs/ with NO environment variable and NO edit to a committed file.
HARNESS = ESP32_RS / "tools" / "qemu_harness"
sys.path.insert(0, str(HARNESS))

from qemu_session import QemuSession  # noqa: E402  (after sys.path setup)

ESP32_DIR = ESP32_RS
TEST_BUILD = "build_qemu_test"


@pytest.fixture(scope="session", autouse=True)
def _require_image():
    binary = ESP32_DIR / TEST_BUILD / "esp32tap.bin"
    if not binary.exists():
        pytest.skip(f"{binary} missing — run tools/build.sh first")


@pytest.fixture
def qemu(request):
    """Factory: boot the qemu-test image. Sessions are closed on teardown, and
    dumped first if the test failed."""
    sessions: list[QemuSession] = []

    def factory(**kwargs) -> QemuSession:
        s = QemuSession(ESP32_DIR, TEST_BUILD, **kwargs)
        sessions.append(s)
        return s

    yield factory

    failed = getattr(request.node, "rep_call", None)
    for s in sessions:
        try:
            if failed is not None and failed.failed:
                print(s.debug_dump())
        finally:
            s.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
