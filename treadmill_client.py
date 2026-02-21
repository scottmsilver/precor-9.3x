#!/usr/bin/env python3
"""
Python client for treadmill_io C binary.

Connects to the Unix domain socket, sends JSON commands,
and receives a stream of JSON event lines.

Supports auto-reconnection with backoff when the C binary
restarts. Provides on_disconnect/on_reconnect callbacks
for the server to track connection state.

Usage:
    from treadmill_client import TreadmillClient

    client = TreadmillClient()
    client.on_message = my_handler
    client.on_disconnect = lambda: print("lost connection")
    client.on_reconnect = lambda: print("reconnected")
    client.connect()
    client.set_proxy(True)
    ...
    client.close()
"""

import json
import logging
import socket
import threading
import time

SOCK_PATH = "/tmp/treadmill_io.sock"
MAX_SPEED_TENTHS = 120  # 12.0 mph max, in tenths
MAX_INCLINE = 99
MAX_BUF = 65536

log = logging.getLogger("treadmill_client")


class TreadmillClient:
    def __init__(self, sock_path=SOCK_PATH):
        self.sock_path = sock_path
        self._sock = None
        self._lock = threading.Lock()
        self._reader_thread = None
        self._reconnect_thread = None
        self._heartbeat_thread = None
        self._heartbeat_running = False
        self._running = False
        self._connected = False
        self.on_message = None  # callback(msg_dict)
        self.on_disconnect = None  # callback()
        self.on_reconnect = None  # callback()

    @property
    def connected(self):
        return self._connected

    def connect(self):
        """Connect to the treadmill_io Unix socket."""
        self._running = True
        self._do_connect()

    def _do_connect(self):
        """Internal: establish socket connection and start reader."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.sock_path)
        with self._lock:
            self._sock = sock
            self._connected = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def start_heartbeat(self, interval=1.0):
        """Start a dedicated OS thread that sends heartbeats via time.sleep.

        Immune to asyncio event loop stalls (e.g. serving large static files).
        """
        self.stop_heartbeat()
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, args=(interval,), daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        """Stop the heartbeat thread and wait for it to finish."""
        self._heartbeat_running = False
        t = self._heartbeat_thread
        if t and t.is_alive():
            t.join(timeout=3.0)
        self._heartbeat_thread = None

    def _heartbeat_loop(self, interval):
        """Background thread: send heartbeats at fixed interval using OS sleep."""
        while self._heartbeat_running and self._running:
            try:
                if self._connected:
                    self.heartbeat()
            except (ConnectionError, OSError):
                pass
            except Exception:
                pass
            time.sleep(interval)

    def close(self):
        """Disconnect from the socket. Stops reconnection and heartbeat."""
        self._running = False
        self.stop_heartbeat()
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
            raise ConnectionError("Not connected to treadmill_io")
        try:
            data = json.dumps(msg, separators=(",", ":")) + "\n"
            sock.sendall(data.encode())
        except OSError as e:
            raise ConnectionError(f"Send failed: {e}") from e

    def heartbeat(self):
        """Send a heartbeat to keep the watchdog alive."""
        self._send({"cmd": "heartbeat"})

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
                    log.warning("Buffer overflow, discarding")
                    buf = b""
                    continue
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

        # Reader exited — connection lost
        if self._running:
            self._close_socket()
            log.warning("Connection to treadmill_io lost")
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass
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
                log.info(f"Reconnecting to treadmill_io in {delay:.0f}s...")
                time.sleep(delay)
                if not self._running:
                    break
                self._do_connect()
                log.info("Reconnected to treadmill_io")
                if self.on_reconnect:
                    try:
                        self.on_reconnect()
                    except Exception:
                        pass
                return
            except (OSError, ConnectionError):
                delay = min(delay * 2, 10.0)
