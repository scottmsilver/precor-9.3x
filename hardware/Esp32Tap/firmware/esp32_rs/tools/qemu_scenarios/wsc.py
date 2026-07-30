"""A real RFC6455 client for the scenarios.

WHY A REAL ONE. `/ws` is where the Android app gets ITS ENTIRE live UI: every
program-endpoint response body is discarded by `TreadmillViewModel`, and
`_status`/`_session`/`_program` are only ever written from `handleMessage()`.
A test that asserted "the handshake completed" would therefore have passed for
a firmware that never sent a second frame — which is exactly the state the
device shipped in. So this speaks the framing, and the assertions are about
frames that ARRIVE.

Deliberately minimal and deliberately strict: server-to-client frames must be
UNMASKED (RFC6455 §5.1), and anything that is not a text or close frame is a
protocol error here rather than something to skip past.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import time

import httpc

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WsClient:
    """One open WebSocket against the guest. Use as a context manager."""

    def __init__(self, sess, path: str = "/ws", timeout: float = 30):
        self.sess = sess
        self.path = path
        self.timeout = timeout
        self.tls: ssl.SSLSocket | None = None
        self._buf = b""

    def __enter__(self) -> "WsClient":
        raw = socket.create_connection(("127.0.0.1", self.sess.http_port), timeout=self.timeout)
        self.tls = httpc.tls_context().wrap_socket(raw, server_hostname="esp32tap")
        key = base64.b64encode(os.urandom(16)).decode()
        self.tls.sendall(
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        # Read exactly the handshake response; anything after it is frame data
        # and must be kept.
        while b"\r\n\r\n" not in self._buf:
            self._buf += self._recv()
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise AssertionError(f"/ws did not upgrade: {head!r}")
        return self

    def __exit__(self, *exc):
        try:
            if self.tls is not None:
                self.tls.close()
        except OSError:
            pass
        return False

    def _recv(self) -> bytes:
        assert self.tls is not None
        b = self.tls.recv(4096)
        if not b:
            raise ConnectionError("server closed the WebSocket")
        return b

    def _need(self, n: int, deadline: float) -> bytes:
        while len(self._buf) < n:
            assert self.tls is not None
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no frame in time")
            self.tls.settimeout(min(left, 1.0))
            try:
                self._buf += self._recv()
            except (socket.timeout, ssl.SSLError):
                continue
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def frame(self, timeout: float = 5):
        """The next TEXT frame's payload as parsed JSON, or None on close."""
        deadline = time.monotonic() + timeout
        while True:
            h = self._need(2, deadline)
            op = h[0] & 0x0F
            masked = bool(h[1] & 0x80)
            ln = h[1] & 0x7F
            if masked:
                raise AssertionError("server MUST NOT mask frames (RFC6455 5.1)")
            if ln == 126:
                ln = int.from_bytes(self._need(2, deadline), "big")
            elif ln == 127:
                ln = int.from_bytes(self._need(8, deadline), "big")
            payload = self._need(ln, deadline) if ln else b""
            if op == _OP_CLOSE:
                return None
            if op in (_OP_PING, _OP_PONG):
                continue
            if op != _OP_TEXT:
                raise AssertionError(f"unexpected WebSocket opcode {op}")
            return json.loads(payload.decode())

    def collect(self, seconds: float) -> list[dict]:
        """Every text frame that arrives in the next `seconds`."""
        out: list[dict] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                f = self.frame(timeout=max(0.2, end - time.monotonic()))
            except TimeoutError:
                break
            if f is None:
                break
            out.append(f)
        return out
