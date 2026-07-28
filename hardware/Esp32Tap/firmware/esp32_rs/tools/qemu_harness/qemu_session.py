"""QemuSession — docker/QEMU lifecycle + serial sockets for the Esp32Tap
behavioral harness.

Proven ground truth this encodes (see README.md):
  - the pinned espressif QEMU hard-wires serial0=UART0, serial1=UART1,
    UART2 unwireable -> the test image remaps the motor tap to UART0 RX;
  - chardev bytes are LOGICAL (uart_set_line_inverse is a no-op on this
    path) and unpaced (no baud emulation) -> the harness paces bursts in
    wall time itself;
  - `wait=on` on both chardevs so the guest does not boot (and no output
    is lost) until both sockets are connected.

Topology:
  serial0 / UART0: ESP log + QTAUDIT/QTSTATE out; motor-sim bytes and
                   "\\nQT ...\\n"-framed shim commands in.
  serial1 / UART1: console-sim bytes in; firmware motor-TX capture out.
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

IDF_IMAGE = os.environ.get("IDF_IMAGE", "espressif/idf:release-v5.5")

_QTAUDIT_RE = re.compile(r"QTAUDIT (\d+) (.*)$")
_QTSTATE_RE = re.compile(
    r"QTSTATE mode=(\S+) relay=(\d) tx=(\d) fault=(\d) speed=(-?\d+) "
    r"incline=(-?\d+) cons_bytes=(\d+) motor_bytes=(\d+) "
    r"io_relay=(\d) io_tx=(\d) t_us=(-?\d+)"
)
_HEARTBEAT_RE = re.compile(r"heartbeat uptime=(\d+)s")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HarnessError(AssertionError):
    pass


class QemuSession:
    """One booted firmware image under QEMU inside the pinned IDF docker
    image, with both serial chardevs connected over TCP."""

    def __init__(self, esp32_dir: Path, build_dir: str, boot_timeout: float = 120.0, expect_shim: bool = True):
        self.esp32_dir = Path(esp32_dir)
        self.build_dir = build_dir
        self.expect_shim = expect_shim
        # qemu_smoke.sh convention: repo root is 4 parents up from esp32/.
        self.repo_root = self.esp32_dir.parents[3]
        self.rel = self.esp32_dir.relative_to(self.repo_root)
        self.name = f"esp32tap-qh-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._u0_lock = threading.Lock()  # uart0 parsed state
        self._u1_lock = threading.Lock()  # uart1 capture state
        self._writer_lock = threading.Lock()  # single UART0 writer (motor+cmd)
        self._lines: list[str] = []
        self._raw0 = bytearray()
        self._audit: list[tuple[int, str]] = []
        self._qtstates: list[dict] = []
        self._chunks1: list[tuple[float, bytes]] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._pacer_stop = threading.Event()
        self._pacer_payload: bytes | None = None
        self._pacer_thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.sock0: socket.socket | None = None
        self.sock1: socket.socket | None = None
        self._start(boot_timeout)

    # --- lifecycle -------------------------------------------------------

    def _start(self, boot_timeout: float) -> None:
        p0, p1 = _free_port(), _free_port()
        bdir = shlex.quote(self.build_dir)
        # The emulated flash MUST be the size the app image header declares.
        # IDF's spi_flash init aborts ("Detected size(...) smaller than the
        # size in the binary image header(...). Probe failed.") and reboots
        # forever otherwise. Hard-coding 2MB silently assumed
        # CONFIG_ESPTOOLPY_FLASHSIZE=2MB; take it from the build's own
        # flash_args instead so the emulated part always matches the image.
        script = (
            "set -u; cd %s || exit 3; "
            "FS=$(sed -n 's/.*--flash_size \\([0-9A-Za-z]*\\).*/\\1/p' flash_args | head -1); "
            "[ -n \"$FS\" ] || { echo 'no --flash_size in flash_args' >&2; exit 3; }; "
            "python -m esptool --chip esp32s3 merge_bin -o qemu_flash.bin "
            '@flash_args --fill-flash-size "$FS" >/dev/null 2>&1 '
            "|| python -m esptool --chip esp32s3 merge-bin -o qemu_flash.bin "
            '@flash_args --pad-to-size "$FS" >/dev/null || exit 3; cd ..; '
            "exec qemu-system-xtensa -nographic -machine esp32s3 "
            "-drive file=%s/qemu_flash.bin,if=mtd,format=raw "
            "-serial tcp:127.0.0.1:%d,server=on,wait=on "
            "-serial tcp:127.0.0.1:%d,server=on,wait=on"
        ) % (bdir, bdir, p0, p1)
        self.proc = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                self.name,
                "--network=host",
                "-v",
                f"{self.repo_root}:/project",
                "-w",
                f"/project/{self.rel}",
                IDF_IMAGE,
                "bash",
                "-c",
                script,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            # serial0 first: with wait=on QEMU creates the serial1 listener
            # only after the serial0 client connects.
            self.sock0 = self._connect(p0, timeout=60.0)
            self.sock1 = self._connect(p1, timeout=30.0)
        except Exception:
            self.close()
            raise
        for sock, fn in ((self.sock0, self._reader0), (self.sock1, self._reader1)):
            t = threading.Thread(target=fn, args=(sock,), daemon=True)
            t.start()
            self._threads.append(t)
        # Guest boots only now (both chardevs connected) — no lost output.
        self.wait_log(r"esp32tap phase-1 safety core started", timeout=boot_timeout)
        if self.expect_shim:
            self.wait_log(r"qemu_test task started", timeout=30.0)
            self.wait_log(r"QEMU-TEST build", timeout=10.0)

    def _connect(self, port: int, timeout: float) -> socket.socket:
        deadline = time.monotonic() + timeout
        while True:
            if self.proc is not None and self.proc.poll() is not None:
                raise HarnessError(f"docker/qemu exited early (rc={self.proc.returncode})")
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2.0)
                s.settimeout(0.5)
                return s
            except OSError:
                if time.monotonic() > deadline:
                    raise HarnessError(f"could not connect to QEMU serial port {port}")
                time.sleep(0.3)

    def close(self) -> None:
        self._stop.set()
        self.stop_pacer()
        subprocess.run(["docker", "kill", self.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if self.proc is not None:
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        for s in (self.sock0, self.sock1):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        for t in self._threads:
            t.join(timeout=5)

    # --- readers ---------------------------------------------------------

    def _reader0(self, sock: socket.socket) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            with self._u0_lock:
                self._raw0 += data
                buf += data
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    # IDF console lines are CRLF-terminated — strip the
                    # trailing CR so QTAUDIT exact-text matches work.
                    line = buf[:nl].decode("utf-8", errors="replace").rstrip("\r")
                    del buf[: nl + 1]
                    self._lines.append(line)
                    m = _QTAUDIT_RE.search(line)
                    if m:
                        self._audit.append((int(m.group(1)), m.group(2)))
                        continue
                    m = _QTSTATE_RE.search(line)
                    if m:
                        self._qtstates.append(
                            {
                                "mode": m.group(1),
                                "relay": int(m.group(2)),
                                "tx": int(m.group(3)),
                                "fault": int(m.group(4)),
                                "speed": int(m.group(5)),
                                "incline": int(m.group(6)),
                                "cons_bytes": int(m.group(7)),
                                "motor_bytes": int(m.group(8)),
                                # Shim-observed IO-boundary levels (not
                                # controller self-reports).
                                "io_relay": int(m.group(9)),
                                "io_tx": int(m.group(10)),
                                # Guest monotonic clock (µs) at snapshot.
                                "t_us": int(m.group(11)),
                            }
                        )

    def _reader1(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            with self._u1_lock:
                self._chunks1.append((time.monotonic(), data))

    # --- injection -------------------------------------------------------

    def send_console(self, data: bytes) -> None:
        assert self.sock1 is not None
        self.sock1.sendall(data)

    def send_motor(self, data: bytes) -> None:
        assert self.sock0 is not None
        with self._writer_lock:
            self.sock0.sendall(data)

    def cmd(self, command: str) -> None:
        """Send a shim command, framed \\nQT ...\\n (single UART0 writer
        lock shared with send_motor so a command never interleaves inside
        a motor burst)."""
        assert command.startswith("QT ")
        assert self.sock0 is not None
        with self._writer_lock:
            self.sock0.sendall(b"\n" + command.encode("ascii") + b"\n")

    def cmd_ok(self, command: str, timeout: float = 15.0) -> str:
        """Send a command and wait for its QTOK echo line."""
        verb = command.split()[1]
        n0 = self.line_count()
        self.cmd(command)
        return self.wait_log(rf"QTOK {re.escape(verb)} ", timeout=timeout, since_line=n0)

    # --- console pacer ---------------------------------------------------

    def start_pacer(self, payload: bytes, interval: float = 0.15) -> None:
        """Background thread: send `payload` on UART1 every `interval` s
        wall (> GAP_QUALIFY 20 ms, < CONSOLE_FRESH 1.5 s in guest terms).
        Payload is swappable live via set_pacer_payload (S4 takeover)."""
        assert self._pacer_thread is None
        self._pacer_payload = payload
        self._pacer_stop.clear()

        def run() -> None:
            while not self._pacer_stop.is_set():
                p = self._pacer_payload
                if p:
                    try:
                        self.send_console(p)
                    except OSError:
                        return
                self._pacer_stop.wait(interval)

        self._pacer_thread = threading.Thread(target=run, daemon=True)
        self._pacer_thread.start()

    def set_pacer_payload(self, payload: bytes) -> None:
        self._pacer_payload = payload

    def stop_pacer(self) -> None:
        self._pacer_stop.set()
        if self._pacer_thread is not None:
            self._pacer_thread.join(timeout=5)
            self._pacer_thread = None

    # --- observation -----------------------------------------------------

    def line_count(self) -> int:
        with self._u0_lock:
            return len(self._lines)

    def lines(self) -> list[str]:
        with self._u0_lock:
            return list(self._lines)

    def raw0(self) -> bytes:
        with self._u0_lock:
            return bytes(self._raw0)

    def audit_events(self) -> list[tuple[int, str]]:
        with self._u0_lock:
            return list(self._audit)

    def audit_count(self, text: str, since: int = 0) -> int:
        return sum(1 for i, t in self.audit_events() if t == text and i >= since)

    def wait_log(self, pattern: str, timeout: float = 30.0, since_line: int = 0) -> str:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while True:
            with self._u0_lock:
                for line in self._lines[since_line:]:
                    if rx.search(line):
                        return line
            if time.monotonic() > deadline:
                raise HarnessError(f"log pattern not seen: {pattern}")
            time.sleep(0.05)

    def wait_audit(self, text: str, timeout: float = 30.0, since: int = 0, prefix: bool = False) -> int:
        """Wait for an audit event (exact text, or prefix match) with
        absolute ring index >= since. Returns the event's index."""
        deadline = time.monotonic() + timeout
        while True:
            for idx, t in self.audit_events():
                if idx >= since and (t.startswith(text) if prefix else t == text):
                    return idx
            if time.monotonic() > deadline:
                raise HarnessError(
                    f"audit event not seen: {text!r} (since {since}); " f"tail={self.audit_events()[-12:]}"
                )
            time.sleep(0.05)

    def wait_audit_sequence(self, sequence: list[str], timeout: float = 30.0, since: int = 0) -> list[int]:
        """Wait until `sequence` appears as an ordered (by ring index)
        subsequence of the audit stream at indexes >= since."""
        deadline = time.monotonic() + timeout
        while True:
            events = [e for e in self.audit_events() if e[0] >= since]
            idxs: list[int] = []
            want = 0
            for idx, t in events:
                if want < len(sequence) and t == sequence[want]:
                    idxs.append(idx)
                    want += 1
            if want == len(sequence):
                return idxs
            if time.monotonic() > deadline:
                raise HarnessError(
                    f"audit subsequence stalled at {sequence[want]!r} "
                    f"({want}/{len(sequence)}); events={events[-20:]}"
                )
            time.sleep(0.05)

    def assert_no_audit(self, predicate, since: int = 0, label: str = ""):
        bad = [e for e in self.audit_events() if e[0] >= since and predicate(e[1])]
        if bad:
            raise HarnessError(f"forbidden audit events {label}: {bad}")

    def state(self, timeout: float = 15.0) -> dict:
        with self._u0_lock:
            n0 = len(self._qtstates)
        self.cmd("QT state")
        deadline = time.monotonic() + timeout
        while True:
            with self._u0_lock:
                if len(self._qtstates) > n0:
                    return dict(self._qtstates[-1])
            if time.monotonic() > deadline:
                raise HarnessError("no QTSTATE reply")
            time.sleep(0.05)

    def guest_uptime(self) -> int:
        best = 0
        for line in self.lines():
            m = _HEARTBEAT_RE.search(line)
            if m:
                best = max(best, int(m.group(1)))
        return best

    def wait_guest_uptime_delta(self, delta: int, timeout: float = 60.0) -> None:
        base = self.guest_uptime()
        deadline = time.monotonic() + timeout
        while self.guest_uptime() < base + delta:
            if time.monotonic() > deadline:
                raise HarnessError(
                    f"guest uptime did not advance {delta}s " f"(base {base}, now {self.guest_uptime()})"
                )
            time.sleep(0.2)

    # --- UART1 TX capture ------------------------------------------------

    def tx_chunks(self) -> list[tuple[float, bytes]]:
        with self._u1_lock:
            return list(self._chunks1)

    def tx_bytes(self) -> bytes:
        return b"".join(c for _, c in self.tx_chunks())

    def tx_bursts(self, gap_s: float = 0.03) -> list[tuple[float, float, bytes]]:
        """Group captured firmware-TX chunks into bursts by >= gap_s
        arrival-time gaps (EMU_BURST_GAP_MS = 100 ms nominal)."""
        bursts: list[tuple[float, float, bytes]] = []
        cur: bytearray = bytearray()
        start = end = None
        for t, data in self.tx_chunks():
            if end is not None and t - end >= gap_s:
                bursts.append((start, end, bytes(cur)))
                cur = bytearray()
                start = None
            if start is None:
                start = t
            cur += data
            end = t
        if cur:
            bursts.append((start, end, bytes(cur)))
        return bursts

    def wait_tx_contains(self, needle: bytes, timeout: float = 30.0, offset: int = 0) -> None:
        deadline = time.monotonic() + timeout
        while needle not in self.tx_bytes()[offset:]:
            if time.monotonic() > deadline:
                raise HarnessError(
                    f"UART1 TX never contained {needle!r} after offset " f"{offset} (have {len(self.tx_bytes())} bytes)"
                )
            time.sleep(0.05)

    # --- diagnostics -----------------------------------------------------

    def debug_dump(self) -> str:
        lines = self.lines()[-200:]
        tx = self.tx_bytes()
        out = ["=== QemuSession dump: last UART0 lines ==="]
        out += lines
        out.append(f"=== UART1 TX capture ({len(tx)} bytes, last 2048) ===")
        out.append(tx[-2048:].hex(" "))
        out.append("=== audit tail ===")
        out += [f"{i} {t}" for i, t in self.audit_events()[-40:]]
        return "\n".join(out)
