"""Integration test for hrm_client.py using a mock HRM daemon."""

import json
import os
import socket

# Add project root to path
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from hrm_client import HrmClient


class MockHrmDaemon:
    """Minimal mock of the hrm-daemon Unix socket server."""

    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.server = None
        self.clients = []
        self.running = False
        self.received_commands = []
        self._thread = None
        self._bpm = 72
        self._connected = True

    def start(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.sock_path)
        self.server.listen(5)
        self.server.settimeout(0.5)
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        for c in self.clients:
            try:
                c.close()
            except OSError:
                pass
        if self.server:
            self.server.close()
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

    def _run(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
                self.clients.append(conn)
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle(self, conn):
        buf = b""
        # Send initial HR message
        hr_msg = (
            json.dumps(
                {
                    "type": "hr",
                    "bpm": self._bpm,
                    "connected": self._connected,
                    "device": "Mock HRM",
                    "address": "AA:BB:CC:DD:EE:FF",
                }
            )
            + "\n"
        )
        try:
            conn.sendall(hr_msg.encode())
        except OSError:
            return

        conn.settimeout(0.5)
        while self.running:
            try:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            cmd = json.loads(line)
                            self.received_commands.append(cmd)
                        except json.JSONDecodeError:
                            pass
            except socket.timeout:
                continue
            except OSError:
                break


@pytest.fixture
def mock_daemon():
    sock_path = os.path.join(tempfile.gettempdir(), f"hrm_test_{os.getpid()}.sock")
    daemon = MockHrmDaemon(sock_path)
    daemon.start()
    yield daemon
    daemon.stop()


def test_connect_and_receive_hr(mock_daemon):
    """Client connects and receives HR data."""
    messages = []
    client = HrmClient(sock_path=mock_daemon.sock_path)
    client.on_message = lambda msg: messages.append(msg)
    client.connect()

    # Wait for initial HR message
    deadline = time.time() + 2.0
    while not messages and time.time() < deadline:
        time.sleep(0.05)

    client.close()

    assert len(messages) >= 1
    msg = messages[0]
    assert msg["type"] == "hr"
    assert msg["bpm"] == 72
    assert msg["connected"] is True
    assert msg["device"] == "Mock HRM"


def test_send_select_device(mock_daemon):
    """Client sends select_device command."""
    client = HrmClient(sock_path=mock_daemon.sock_path)
    client.on_message = lambda msg: None
    client.connect()
    time.sleep(0.1)

    client.select_device("11:22:33:44:55:66")
    time.sleep(0.2)
    client.close()

    cmds = mock_daemon.received_commands
    assert any(c.get("cmd") == "connect" and c.get("address") == "11:22:33:44:55:66" for c in cmds)


def test_send_forget(mock_daemon):
    """Client sends forget command."""
    client = HrmClient(sock_path=mock_daemon.sock_path)
    client.on_message = lambda msg: None
    client.connect()
    time.sleep(0.1)

    client.forget_device()
    time.sleep(0.2)
    client.close()

    cmds = mock_daemon.received_commands
    assert any(c.get("cmd") == "forget" for c in cmds)


def test_send_scan(mock_daemon):
    """Client sends scan command."""
    client = HrmClient(sock_path=mock_daemon.sock_path)
    client.on_message = lambda msg: None
    client.connect()
    time.sleep(0.1)

    client.scan()
    time.sleep(0.2)
    client.close()

    cmds = mock_daemon.received_commands
    assert any(c.get("cmd") == "scan" for c in cmds)


def test_disconnect_callback(mock_daemon):
    """on_disconnect fires when daemon stops."""
    disconnected = threading.Event()
    client = HrmClient(sock_path=mock_daemon.sock_path)
    client.on_message = lambda msg: None
    client.on_disconnect = lambda: disconnected.set()
    client.connect()
    time.sleep(0.1)

    # Kill the daemon
    mock_daemon.stop()

    assert disconnected.wait(timeout=3.0), "on_disconnect not called within 3s"
    client.close()


def test_reconnect(mock_daemon):
    """Client auto-reconnects after daemon restart."""
    reconnected = threading.Event()
    messages = []
    sock_path = mock_daemon.sock_path

    client = HrmClient(sock_path=sock_path)
    client.on_message = lambda msg: messages.append(msg)
    client.on_reconnect = lambda: reconnected.set()
    client.connect()
    time.sleep(0.2)

    # Kill daemon
    mock_daemon.stop()
    time.sleep(0.5)

    # Restart daemon on same path
    daemon2 = MockHrmDaemon(sock_path)
    daemon2._bpm = 150  # different BPM to prove reconnect
    daemon2.start()

    assert reconnected.wait(timeout=5.0), "on_reconnect not called within 5s"
    time.sleep(0.2)

    client.close()
    daemon2.stop()

    # Should have received messages from both connections
    bpms = [m.get("bpm") for m in messages if m.get("type") == "hr"]
    assert 72 in bpms, "should have seen original 72 bpm"
    assert 150 in bpms, "should have seen reconnected 150 bpm"
