"""Hardware integration tests — only run on Raspberry Pi with treadmill connected.

Run with: pytest tests/test_hardware_integration.py -v -s -m hardware
"""

import time

import pytest

pytestmark = pytest.mark.hardware

POLL_INTERVAL = 0.5  # seconds between checks
POLL_TIMEOUT = 15  # max seconds to wait for expected value
SPEED_TOLERANCE = 10  # hundredths of mph (0.1 mph)


def wait_for(received, key, expected, timeout=POLL_TIMEOUT):
    """Poll received dict until key matches expected value or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if received.get(key) == expected:
            return True
        time.sleep(POLL_INTERVAL)
    return False


def wait_for_speed_approx(received, target_hundredths, tolerance=SPEED_TOLERANCE, timeout=POLL_TIMEOUT):
    """Poll until motor hmph is within tolerance of target (in hundredths)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hmph = received.get("hmph")
        if hmph:
            actual = int(hmph, 16)
            if abs(actual - target_hundredths) <= tolerance:
                return True
        time.sleep(POLL_INTERVAL)
    return False


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
        """Set 3.0mph, poll until motor hmph is ~300 hundredths."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)
        client.set_speed(3.0)
        target = 300  # 3.0 mph * 100
        assert wait_for_speed_approx(
            received, target
        ), f"motor hmph never reached ~{target}, got {received.get('hmph')}"


class TestEmulateIncline:
    def test_emulate_sends_incline(self, treadmill):
        """Set 5%, poll until motor inc = 5."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)
        client.set_incline(5)
        assert wait_for(received, "inc", "5"), f"motor inc never reached 5, got {received.get('inc')}"


class TestProgramChangesMotor:
    def test_program_changes_motor(self, treadmill):
        """Set two different speeds, verify motor reports distinct values."""
        client, received = treadmill
        client.set_emulate(True)
        time.sleep(1)

        client.set_speed(2.0)
        assert wait_for_speed_approx(received, 200), f"motor hmph never reached ~200, got {received.get('hmph')}"
        speed1 = int(received["hmph"], 16)

        client.set_speed(5.0)
        assert wait_for_speed_approx(received, 500), f"motor hmph never reached ~500, got {received.get('hmph')}"
        speed2 = int(received["hmph"], 16)

        assert abs(speed1 - speed2) > 100, f"speeds too similar: {speed1} vs {speed2}"
