#!/usr/bin/env python3
"""
Python client for the HRM (Heart Rate Monitor) daemon.

Connects to the hrm-daemon Unix domain socket, sends JSON commands,
and receives a stream of JSON event lines (heart rate, scan results).

Supports auto-reconnection with backoff when the daemon restarts.

Usage:
    from hrm_client import HrmClient

    client = HrmClient()
    client.on_message = my_handler
    client.on_disconnect = lambda: print("lost connection")
    client.on_reconnect = lambda: print("reconnected")
    client.connect()
    client.select_device("AA:BB:CC:DD:EE:FF")
    ...
    client.close()
"""

import json
import logging
import socket
import threading
import time

HRM_SOCK_PATH = "/tmp/hrm.sock"
MAX_BUF = 65536

log = logging.getLogger("hrm_client")


class HrmClient:
    def __init__(self, sock_path=HRM_SOCK_PATH):
        self.sock_path = sock_path
        self._sock = None
        self._lock = threading.Lock()
        self._reader_thread = None
        self._reconnect_thread = None
        self._running = False
        self._connected = False
        self.on_message = None  # callback(msg_dict)
        self.on_disconnect = None  # callback()
        self.on_reconnect = None  # callback()

    @property
    def connected(self):
        return self._connected

    def connect(self):
        """Connect to the hrm-daemon Unix socket."""
        self._running = True
        self._do_connect()

    def ensure_connecting(self):
        """Start background reconnection without blocking.

        Use this when the daemon isn't available yet but you want the
        client to keep trying in the background.
        """
        self._running = True
        self._start_reconnect()

    def _do_connect(self):
        """Internal: establish socket connection and start reader."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.sock_path)
        with self._lock:
            self._sock = sock
            self._connected = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def close(self):
        """Disconnect from the socket. Stops reconnection."""
        self._running = False
        self._close_socket()

    def _close_socket(self):
        """Close the socket without stopping the reconnect loop."""
        with self._lock:
            self._connected = False
            sock = self._sock
            self._sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _send(self, msg):
        """Send a JSON command line. Raises ConnectionError if disconnected."""
        with self._lock:
            sock = self._sock
        if not sock:
            raise ConnectionError("Not connected to hrm-daemon")
        try:
            data = json.dumps(msg, separators=(",", ":")) + "\n"
            sock.sendall(data.encode())
        except OSError as e:
            raise ConnectionError(f"Send failed: {e}") from e

    def select_device(self, address):
        """Connect to a specific BLE heart rate device by address."""
        self._send({"cmd": "connect", "address": address})

    def forget_device(self):
        """Forget the saved device so it won't auto-connect."""
        self._send({"cmd": "forget"})

    def scan(self):
        """Start scanning for BLE heart rate devices."""
        self._send({"cmd": "scan"})

    def disconnect_device(self):
        """Disconnect from the currently connected device."""
        self._send({"cmd": "disconnect"})

    def _reader_loop(self):
        """Background thread: read JSON lines from socket, dispatch."""
        buf = b""
        while self._running:
            with self._lock:
                sock = self._sock
            if not sock:
                break
            try:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
                if len(buf) > MAX_BUF:
                    log.warning("HRM read buffer exceeded %d bytes, resetting", MAX_BUF)
                    buf = b""
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
                            log.debug("on_message callback error", exc_info=True)
            except OSError:
                break

        # Reader exited -- connection lost
        if self._running:
            self._close_socket()
            log.warning("Connection to hrm-daemon lost")
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    log.debug("on_disconnect callback error", exc_info=True)
            self._start_reconnect()

    def _start_reconnect(self):
        """Start background reconnection loop."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        """Try to reconnect with exponential backoff."""
        delay = 1.0
        while self._running and not self._connected:
            try:
                log.info(f"Reconnecting to hrm-daemon in {delay:.0f}s...")
                time.sleep(delay)
                if not self._running:
                    break
                self._do_connect()
                log.info("Reconnected to hrm-daemon")
                if self.on_reconnect:
                    try:
                        self.on_reconnect()
                    except Exception:
                        pass
                return
            except (OSError, ConnectionError):
                delay = min(delay * 2, 10.0)
