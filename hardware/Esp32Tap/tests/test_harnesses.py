from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_harness_requirements_remain_unselected_while_current_is_unmeasured(
    esp32tap_dir: Path,
) -> None:
    requirements = json.loads(
        (esp32tap_dir / "harness" / "requirements.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(requirements) == {
        "revision",
        "status",
        "release_action",
        "interfaces",
        "owner_fabrication_allowed",
    }
    assert requirements["revision"] == "C"
    assert requirements["status"] == "HOLD_NOT_MEASURED"
    assert requirements["release_action"] == "connector_selection"
    assert requirements["interfaces"] == []
    assert requirements["owner_fabrication_allowed"] is False


def test_harness_validator_audits_hold_but_blocks_release(
    esp32tap_dir: Path,
) -> None:
    audit = subprocess.run(
        [sys.executable, "harness/validate_harnesses.py"],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    release = subprocess.run(
        [sys.executable, "harness/validate_harnesses.py", "--release"],
        cwd=esp32tap_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert audit.returncode == 0, audit.stderr
    assert "HOLD_NOT_MEASURED" in audit.stdout
    assert release.returncode != 0
    assert "NOT_MEASURED" in release.stderr
