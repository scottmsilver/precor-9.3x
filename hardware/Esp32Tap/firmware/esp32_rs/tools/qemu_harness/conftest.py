"""Fixtures for the Esp32Tap QEMU behavioral harness."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from qemu_session import QemuSession

HERE = Path(__file__).resolve().parent
ESP32_DIR = HERE.parents[1]
DEFAULT_BUILD = "build"
TEST_BUILD = "build_qemu_test"


def _require_image(build_dir: str) -> Path:
    binary = ESP32_DIR / build_dir / "esp32tap.bin"
    if not binary.exists():
        pytest.skip(
            f"{binary} missing — build it first (tools/qemu_harness/run.sh, "
            f"or: docker run --rm -v $PWD:/project -w /project "
            f"espressif/idf:release-v5.5 idf.py -B {build_dir}"
            f"{' -DESP32TAP_QEMU_TEST=1' if build_dir == TEST_BUILD else ''}"
            f" build)"
        )
    return binary


@pytest.fixture(scope="session")
def default_build_bin() -> Path:
    return _require_image(DEFAULT_BUILD)


@pytest.fixture(scope="session")
def test_build_bin() -> Path:
    return _require_image(TEST_BUILD)


@pytest.fixture(scope="session", autouse=True)
def _require_docker():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    rc = subprocess.run(
        ["docker", "image", "inspect", "espressif/idf:release-v5.5"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode
    if rc != 0:
        pytest.skip("pinned espressif/idf:release-v5.5 image not present")


# Detect test outcome so the session fixture can dump diagnostics.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture
def qemu(request, test_build_bin):
    """Factory: boot the ESP32TAP_QEMU_TEST image under QEMU. Sessions are
    torn down at test end; on failure the console/audit/TX capture is
    dumped into the report."""
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
