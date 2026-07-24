from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest


@pytest.fixture(scope="session")
def esp32tap_dir() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def design_path(esp32tap_dir: Path) -> Path:
    return esp32tap_dir / "tools" / "design.py"


@pytest.fixture(scope="session")
def load_design(
    design_path: Path,
) -> Callable[[], SimpleNamespace]:
    def load() -> SimpleNamespace:
        namespace = runpy.run_path(
            str(design_path),
            run_name="esp32tap_design_test",
        )
        return SimpleNamespace(**namespace)

    return load


@pytest.fixture(scope="session")
def design(load_design: Callable[[], SimpleNamespace]) -> SimpleNamespace:
    return load_design()
