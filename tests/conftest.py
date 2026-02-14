"""Shared test fixtures for treadmill tests."""

import os
import sys

# Add project root to path so tests can import program_engine, server, etc.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from program_engine import ProgramState
from tests.helpers import make_program


@pytest.fixture
def prog():
    """Fresh ProgramState instance."""
    return ProgramState()


@pytest.fixture
def loaded_prog(prog):
    """ProgramState with a 3-interval program loaded."""
    prog.load(make_program())
    return prog
