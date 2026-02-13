#!/usr/bin/env python3
"""
Python client for treadmill_io C binary.

Connects to the Unix domain socket, sends JSON commands,
and receives a stream of JSON event lines.

Usage:
    from treadmill_client import TreadmillClient

    client = TreadmillClient()
    client.on_message = my_handler
    client.connect()
    client.set_proxy(True)
    ...
    client.close()
"""

import json
import socket
import threading

SOCK_PATH = "/tmp/treadmill_io.sock"
MAX_SPEED_TENTHS = 120  # 12.0 mph max, in tenths
MAX_INCLINE = 99


class TreadmillClient:
    def __init__(self, sock_path=SOCK_PATH):
        self.sock_path = sock_path
        self._sock = None
        self._reader_thread = None
        self._running = False
        self.on_message = None  # callback(msg_dict)

    def connect(self):
        """Connect to the treadmill_io Unix socket."""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self.sock_path)
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def close(self):
        """Disconnect from the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _send(self, msg):
        """Send a JSON command line."""
        if self._sock:
            data = json.dumps(msg, separators=(",", ":")) + "\n"
            self._sock.sendall(data.encode())

    def set_proxy(self, enabled):
        self._send({"cmd": "proxy", "enabled": enabled})

    def set_emulate(self, enabled):
        self._send({"cmd": "emulate", "enabled": enabled})

    def set_speed(self, mph):
        """Set emulation speed in mph (float)."""
        self._send({"cmd": "speed", "value": mph})

    def set_incline(self, value):
        """Set emulation incline (int 0-99)."""
        self._send({"cmd": "incline", "value": value})

    def request_status(self):
        self._send({"cmd": "status"})

    def quit_server(self):
        self._send({"cmd": "quit"})

    def _reader_loop(self):
        """Background thread: read JSON lines from socket, dispatch."""
        buf = b""
        while self._running:
            try:
                data = self._sock.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self.on_message:
                        try:
                            self.on_message(msg)
                        except Exception:
                            pass
            except OSError:
                break
