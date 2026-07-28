"""NetSession — QemuSession + the openeth NIC, HTTPS/WS clients, and
pcap access for the native-server-tier network scenarios (N1..N7).

Stdlib only: http.client + ssl (unverified — the guest generates a
self-signed EC P-256 cert at first boot; negotiating TLS at all proves
that path), plus a ~40-line raw-socket WebSocket client.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import ssl
import time
from pathlib import Path

from qemu_session import HarnessError, QemuSession


class NetSession(QemuSession):
    def __init__(self, esp32_dir: Path, build_dir: str = "build_qemu_test", merge: bool = True, **kw):
        super().__init__(esp32_dir, build_dir, net=True, merge=merge, **kw)

    # --- HTTPS ----------------------------------------------------------

    def wait_server_up(self, timeout: float = 180.0) -> None:
        self.wait_log(r"https server up on :8000", timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        timeout: float = 15.0,
        raw_body: str | None = None,
    ) -> tuple[int, dict | list | None]:
        """One HTTPS request; retries transport errors until timeout
        (TLS accept can lag the log line under QEMU). raw_body sends an
        arbitrary (possibly malformed/oversized) payload verbatim."""
        assert self.http_port is not None
        if raw_body is not None:
            payload = raw_body
        else:
            payload = None if body is None else json.dumps(body)
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            ctx = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection("127.0.0.1", self.http_port, timeout=10.0, context=ctx)
            try:
                headers = {"Content-Type": "application/json"} if payload else {}
                conn.request(method, path, body=payload, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
                data = json.loads(raw) if raw else None
                return resp.status, data
            except (OSError, ssl.SSLError, http.client.HTTPException, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(1.0)
            finally:
                conn.close()
        raise HarnessError(f"HTTPS {method} {path} never succeeded: {last_err}")

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, body: dict | None = None, **kw):
        return self.request("POST", path, body=body, **kw)

    # --- WebSocket ------------------------------------------------------

    def ws_connect(self, timeout: float = 30.0) -> "WsClient":
        assert self.http_port is not None
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return WsClient("127.0.0.1", self.http_port)
            except (OSError, ssl.SSLError, HarnessError) as e:
                last_err = e
                time.sleep(1.0)
        raise HarnessError(f"WS connect failed: {last_err}")

    # --- pcap -----------------------------------------------------------

    def pcap_bytes(self) -> bytes:
        path = self.esp32_dir / self.build_dir / "net.pcap"
        try:
            return path.read_bytes()
        except OSError:
            return b""


class WsClient:
    """Raw-socket WSS client (server frames are unmasked text)."""

    def __init__(self, host: str, port: int):
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection((host, port), timeout=10.0)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(10.0)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /ws HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise HarnessError("WS handshake: connection closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise HarnessError(f"WS handshake rejected: {head[:120]!r}")
        self._buf = bytearray(rest)

    def _recv_more(self) -> None:
        chunk = self.sock.recv(4096)
        if not chunk:
            raise HarnessError("WS: connection closed")
        self._buf += chunk

    def recv_text(self, timeout: float = 20.0) -> dict:
        """Next complete text frame parsed as JSON."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._try_parse()
            if frame is not None:
                opcode, payload = frame
                if opcode == 0x1:
                    return json.loads(payload.decode("utf-8"))
                continue  # skip ping/pong/binary
            if time.monotonic() > deadline:
                raise HarnessError("WS: no text frame before timeout")
            self.sock.settimeout(max(0.2, deadline - time.monotonic()))
            try:
                self._recv_more()
            except socket.timeout:
                raise HarnessError("WS: no text frame before timeout")

    def _try_parse(self):
        buf = self._buf
        if len(buf) < 2:
            return None
        opcode = buf[0] & 0x0F
        length = buf[1] & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < 4:
                return None
            length = int.from_bytes(buf[2:4], "big")
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length = int.from_bytes(buf[2:10], "big")
            offset = 10
        if buf[1] & 0x80:  # masked server frame: not expected
            offset += 4
        if len(buf) < offset + length:
            return None
        payload = bytes(buf[offset : offset + length])
        del buf[: offset + length]
        return opcode, payload

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class RawHttpsConn:
    """A hand-driven HTTPS connection for hostile-input scenarios the
    stdlib client cannot express (dribbled bodies, single oversized TLS
    records, headers without a body)."""

    def __init__(self, host: str, port: int, timeout: float = 20.0):
        ctx = ssl._create_unverified_context()
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(timeout)

    def send_headers(self, method: str, path: str, content_length: int) -> None:
        head = (
            f"{method} {path} HTTP/1.1\r\nHost: h\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {content_length}\r\nConnection: close\r\n\r\n"
        )
        self.sock.sendall(head.encode())

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_all(self, timeout: float = 20.0) -> bytes:
        self.sock.settimeout(timeout)
        out = b""
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                out += chunk
        except (OSError, ssl.SSLError):
            pass
        return out

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
