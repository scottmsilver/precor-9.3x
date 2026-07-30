"""qprof_plugin — non-invasive wall-clock instrumentation for the Esp32Tap
QEMU harness.

Records, per test:
  * total wall time
  * QemuSession boot phases (docker launch -> serial0 connect -> serial1
    connect -> app_main -> shim ready) and teardown
  * time spent blocked inside the harness' wait_*/state polling helpers
  * time spent in bare time.sleep() inside the test body

Writes one JSON object per test to $QPROF_OUT (default /tmp/qprof/suite.jsonl).
Nothing here changes an assertion, a bound, or a timeout.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import qemu_session as qs

OUT = Path(os.environ.get("QPROF_OUT", "/tmp/qprof/suite.jsonl"))

_cur: dict | None = None
_real_sleep = time.sleep
_depth = [0]


def _acc(key: str, dt: float, outermost: bool) -> None:
    if _cur is not None:
        _cur["wait"][key] = round(_cur["wait"].get(key, 0.0) + dt, 3)
        if outermost:
            _cur["wait_total"] = round(_cur.get("wait_total", 0.0) + dt, 3)


def _patched_sleep(sec):  # noqa: ANN001
    outer = _depth[0] == 0
    _depth[0] += 1
    t0 = time.monotonic()
    try:
        _real_sleep(sec)
    finally:
        _depth[0] -= 1
        _acc("time.sleep", time.monotonic() - t0, outer)


def _wrap(cls, name):
    orig = getattr(cls, name)

    def wrapper(self, *a, **k):
        outer = _depth[0] == 0
        _depth[0] += 1
        t0 = time.monotonic()
        try:
            return orig(self, *a, **k)
        finally:
            _depth[0] -= 1
            _acc(name, time.monotonic() - t0, outer)

    wrapper.__name__ = name
    setattr(cls, name, wrapper)


def pytest_configure(config):  # noqa: ARG001
    time.sleep = _patched_sleep
    for m in (
        "wait_log",
        "wait_audit",
        "wait_audit_sequence",
        "wait_guest_uptime_delta",
        "wait_tx_contains",
        "state",
        "cmd_ok",
    ):
        _wrap(qs.QemuSession, m)

    # Boot-phase timestamps on the session itself.
    orig_start = qs.QemuSession._start
    orig_close = qs.QemuSession.close
    orig_connect = qs.QemuSession._connect

    def start(self, boot_timeout):
        _depth[0] += 1
        self.qprof = {}
        t0 = time.monotonic()
        self.qprof["_t0"] = t0
        self._qprof_conn = []
        try:
            orig_start(self, boot_timeout)
        finally:
            t = time.monotonic()
            self.qprof["boot_total_s"] = round(t - t0, 2)
            conns = self._qprof_conn
            if conns:
                self.qprof["docker_to_serial0_s"] = round(conns[0] - t0, 2)
            if len(conns) > 1:
                self.qprof["serial0_to_serial1_s"] = round(conns[1] - conns[0], 2)
                self.qprof["serial1_to_ready_s"] = round(t - conns[1], 2)
            _depth[0] -= 1
            if _cur is not None:
                _cur["sessions"].append(dict(self.qprof))

    def connect(self, port, timeout):
        s = orig_connect(self, port, timeout)
        self._qprof_conn.append(time.monotonic())
        return s

    def close(self):
        _depth[0] += 1
        t0 = time.monotonic()
        orig_close(self)
        _depth[0] -= 1
        dt = time.monotonic() - t0
        if _cur is not None:
            _cur["teardown_s"] = round(_cur.get("teardown_s", 0.0) + dt, 2)

    qs.QemuSession._start = start
    qs.QemuSession._connect = connect
    qs.QemuSession.close = close


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):  # noqa: ARG001
    global _cur
    _cur = {
        "test": item.nodeid.split("::")[-1],
        "file": item.nodeid.split("::")[0],
        "wait": {},
        "wait_total": 0.0,
        "sessions": [],
        "teardown_s": 0.0,
    }
    t0 = time.monotonic()
    yield
    _cur["total_s"] = round(time.monotonic() - t0, 2)
    boot = sum(s.get("boot_total_s", 0.0) for s in _cur["sessions"])
    _cur["boot_s"] = round(boot, 2)
    _cur["other_s"] = round(_cur["total_s"] - boot - _cur["wait_total"] - _cur["teardown_s"], 2)
    OUT.open("a").write(json.dumps(_cur) + "\n")
    _cur = None
