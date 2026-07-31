"""Regression tests for the mDNS capture's narrow QEMU spawn interception."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "qemu_scenarios"))

import mdnsq  # noqa: E402


class _Process:
    pass


def _session(tmp_path: Path, name: str) -> mdnsq.MdnsCaptureSession:
    session = mdnsq.MdnsCaptureSession.__new__(mdnsq.MdnsCaptureSession)
    session.capture_dir = tmp_path / name
    session.capture_dir.mkdir()
    session.name = name
    return session


def _docker_argv(name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network=host",
        "-v",
        "/repo:/project",
        "-w",
        "/project/hardware/Esp32Tap/firmware/esp32_rs",
        "image",
        "bash",
        "-c",
        "exec qemu-system-xtensa -drive file=/tmp/qemu.bin,if=mtd,format=raw "
        "-serial tcp:127.0.0.1:21001,server=on,wait=on "
        "-serial tcp:127.0.0.1:21002,server=on,wait=on "
        "-nic user,model=open_eth,hostfwd=tcp::21000-:8000",
    ]


def test_invalid_provenance_subprocess_passes_through_without_qemu_rewrite(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[list[str]] = []

    def leaf(argv, **_kwargs):
        calls.append(list(argv))
        return _Process()

    def reject_before_qemu(_self, _boot_timeout):
        mdnsq.qemu_session.subprocess.Popen(
            ["git", "status", "--porcelain"],
            stdout=subprocess.PIPE,
        )
        raise mdnsq.HarnessError("qemu-test artifact provenance failed: stale")

    original_module = mdnsq.qemu_session.subprocess
    original_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", leaf)
    monkeypatch.setattr(mdnsq.QemuSession, "_start", reject_before_qemu)

    with pytest.raises(mdnsq.HarnessError, match="artifact provenance failed"):
        mdnsq.MdnsCaptureSession._start(_session(tmp_path, "invalid"), 1)

    assert calls == [["git", "status", "--porcelain"]]
    assert mdnsq.qemu_session.subprocess is original_module
    assert subprocess.Popen is leaf
    assert original_popen is not leaf


def test_valid_capture_rewrites_only_exact_qemu_docker_run(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[list[str]] = []

    def leaf(argv, **_kwargs):
        calls.append(list(argv))
        return _Process()

    session = _session(tmp_path, "valid")
    unrelated_docker = ["docker", "run", "--rm", "alpine", "true"]

    def start(_self, _boot_timeout):
        mdnsq.qemu_session.subprocess.Popen(["git", "rev-parse", "HEAD"])
        mdnsq.qemu_session.subprocess.Popen(unrelated_docker)
        mdnsq.qemu_session.subprocess.Popen(_docker_argv(session.name))

    monkeypatch.setattr(subprocess, "Popen", leaf)
    monkeypatch.setattr(mdnsq.QemuSession, "_start", start)

    mdnsq.MdnsCaptureSession._start(session, 1)

    assert calls[0] == ["git", "rev-parse", "HEAD"]
    assert calls[1] == unrelated_docker
    docker = calls[2]
    assert docker[:3] == ["docker", "run", "--rm"]
    assert docker.count("-v") == 2
    assert f"{session.capture_dir}:/pcap" in docker
    assert "-nic user,id=n0,model=open_eth," in docker[-1]
    assert docker[-1].endswith(
        " -object filter-dump,id=f0,netdev=n0,file=/pcap/wire.pcap"
    )


def test_exact_qemu_spawn_without_expected_nic_fails_closed(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[list[str]] = []

    def leaf(argv, **_kwargs):
        calls.append(list(argv))
        return _Process()

    session = _session(tmp_path, "missing-nic")
    malformed = _docker_argv(session.name)
    malformed[-1] = malformed[-1].replace(
        "-nic user,model=open_eth,hostfwd=",
        "-nic tap,model=open_eth,hostfwd=",
    )

    def start(_self, _boot_timeout):
        mdnsq.qemu_session.subprocess.Popen(malformed)

    monkeypatch.setattr(subprocess, "Popen", leaf)
    monkeypatch.setattr(mdnsq.QemuSession, "_start", start)

    with pytest.raises(mdnsq.HarnessError, match="expected -nic"):
        mdnsq.MdnsCaptureSession._start(session, 1)
    assert calls == []


def test_capture_hook_restores_module_on_baseexception(tmp_path: Path, monkeypatch):
    original_module = mdnsq.qemu_session.subprocess
    original_popen = subprocess.Popen

    def interrupt(_self, _boot_timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(mdnsq.QemuSession, "_start", interrupt)
    with pytest.raises(KeyboardInterrupt):
        mdnsq.MdnsCaptureSession._start(_session(tmp_path, "interrupt"), 1)

    assert mdnsq.qemu_session.subprocess is original_module
    assert subprocess.Popen is original_popen


def test_concurrent_capture_starts_do_not_cross_session_rewrites(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[list[str]] = []
    calls_lock = threading.Lock()
    errors: list[BaseException] = []

    def leaf(argv, **_kwargs):
        with calls_lock:
            calls.append(list(argv))
        return _Process()

    def start(self, _boot_timeout):
        # Make the old nested global-Popen patches overlap deterministically.
        time.sleep(0.03)
        mdnsq.qemu_session.subprocess.Popen(_docker_argv(self.name))

    original_module = mdnsq.qemu_session.subprocess
    monkeypatch.setattr(subprocess, "Popen", leaf)
    monkeypatch.setattr(mdnsq.QemuSession, "_start", start)
    sessions = [_session(tmp_path, name) for name in ("first", "second")]

    def run(session):
        try:
            mdnsq.MdnsCaptureSession._start(session, 1)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(session,)) for session in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(calls) == 2
    for session in sessions:
        matching = [
            argv
            for argv in calls
            if f"{session.capture_dir}:/pcap" in argv
        ]
        assert len(matching) == 1
        assert matching[0][-1].count("id=n0") == 1
        assert matching[0][-1].count("filter-dump") == 1
    assert mdnsq.qemu_session.subprocess is original_module
    assert subprocess.Popen is leaf
