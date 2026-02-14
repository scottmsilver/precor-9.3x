"""Hardware integration tests — only run on Raspberry Pi with treadmill connected.

Run with: pytest tests/test_hardware_integration.py -v -s -m hardware
"""

import asyncio
import time

import pytest

pytestmark = pytest.mark.hardware


@pytest.fixture
def treadmill():
    """Connect to treadmill_io on the Pi."""
    from treadmill_client import TreadmillClient

    client = TreadmillClient()
    client.connect()
    received = {}

    def on_msg(msg):
        if msg.get("type") == "kv" and msg.get("source") == "motor":
            received[msg["key"]] = msg["value"]

    client.on_message = on_msg
    yield client, received
    # Cleanup: stop emulate
    client.set_speed(0)
    client.set_incline(0)
    time.sleep(1)
    client.set_emulate(False)
    client.close()


class TestEmulateSpeed:
    def test_emulate_sends_speed(self, treadmill):
        """Set 3.0mph, verify motor hmph = hex(300)."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)
        client.set_speed(3.0)
        time.sleep(3)  # wait for emulate cycle
        assert "hmph" in received
        assert received["hmph"] == hex(300)[2:].upper()  # "12C"


class TestEmulateIncline:
    def test_emulate_sends_incline(self, treadmill):
        """Set 5%, verify motor inc = 5."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)
        client.set_incline(5)
        time.sleep(3)
        assert "inc" in received
        assert received["inc"] == "5"


class TestProgramChangesMotor:
    def test_program_changes_motor(self, treadmill):
        """Set two different speeds, verify both appear in motor responses."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)

        client.set_speed(2.0)
        time.sleep(3)
        speed1 = received.get("hmph")

        client.set_speed(5.0)
        time.sleep(3)
        speed2 = received.get("hmph")

        assert speed1 is not None
        assert speed2 is not None
        assert speed1 != speed2
