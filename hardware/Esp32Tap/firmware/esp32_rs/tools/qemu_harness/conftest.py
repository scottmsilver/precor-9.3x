"""Fixtures for the Esp32Tap QEMU behavioral harness."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ESP32_DIR = HERE.parents[1]
REPO_ROOT = ESP32_DIR.parents[3]
sys.path.insert(0, str(HERE.parent))

from artifact_provenance import shared_bundle  # noqa: E402
from qemu_session import QemuSession, _verify_current  # noqa: E402

DEFAULT_BUILD = "build"
TEST_BUILD = "build_qemu_test"


@pytest.fixture(scope="session", autouse=True)
def _verified_bundles():
    """Lease and attest both S6 inputs for every fixture read in the session."""
    bundles: dict[str, Path] = {}
    with contextlib.ExitStack() as leases:
        for kind in ("production", "qemu-test"):
            bundle = leases.enter_context(shared_bundle(REPO_ROOT, kind))
            result = _verify_current(REPO_ROOT, kind, bundle)
            if not result.ok:
                pytest.fail(
                    f"{kind} artifact provenance failed: {result.message}",
                    pytrace=False,
                )
            bundles[kind] = bundle
        yield bundles


@pytest.fixture(scope="session")
def default_build_bin(_verified_bundles) -> Path:
    return _verified_bundles["production"] / "esp32tap.bin"


@pytest.fixture(scope="session")
def test_build_bin(_verified_bundles) -> Path:
    return _verified_bundles["qemu-test"] / "esp32tap.bin"


@pytest.fixture(scope="session", autouse=True)
def _require_docker(_verified_bundles):
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
