"""S6 — default-build regression guard.

(a) The UNMODIFIED tools/qemu_smoke.sh still passes against the default
    build/ image (boot, three tasks, PROXY boot state, no WDT/panic/
    reboot, >=15 s guest uptime).
(b) The production image provably contains none of the test surface
    (QTAUDIT / QTSTATE / qemu_test strings absent), with the test image
    as a positive control that the gate actually detects the surface.
"""

from __future__ import annotations

import subprocess

import pytest
from harness_env import esp32_dir

pytestmark = pytest.mark.qemu

ESP32_DIR = esp32_dir()

TEST_SURFACE_STRINGS = (b"QTAUDIT", b"QTSTATE", b"qemu_test")


def test_s6_default_build_qemu_smoke(default_build_bin):
    proc = subprocess.run(
        ["bash", str(ESP32_DIR / "tools" / "qemu_smoke_prof.sh")],
        cwd=ESP32_DIR,
        capture_output=True,
        text=True,
        timeout=420,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"qemu_smoke.sh failed:\n{out}"
    assert "qemu_smoke: PASS" in out, out


def test_s6_production_image_has_no_test_surface(default_build_bin, test_build_bin):
    prod = default_build_bin.read_bytes()
    test = test_build_bin.read_bytes()
    for needle in TEST_SURFACE_STRINGS:
        assert needle not in prod, (
            f"test-surface string {needle!r} leaked into the production " f"image {default_build_bin}"
        )
        # Positive control: the same scan does find the surface in the
        # ESP32TAP_QEMU_TEST image, so an empty result above is meaningful.
        assert needle in test, (
            f"{needle!r} missing from the test image — strings gate broken "
            f"or test image built without ESP32TAP_QEMU_TEST"
        )
