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

import fcntl
import hashlib
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


# Port allocation must be COLLISION-FREE ACROSS EVERY PROCESS ON THIS MACHINE,
# not merely free-at-the-instant-we-looked and not merely disjoint between the
# xdist workers of ONE pytest run. Two bugs, both real, both cost hours:
#
#   1. bind(port 0) -> read the number -> close is a textbook TOCTOU. The port
#      is free again the moment it returns, so two xdist workers could be
#      handed the SAME port. QEMU then starts its serial socket with
#      `server=on,wait=on` and blocks forever waiting for a connection that
#      went to the other process, the boot banner never appears, and the test
#      fails 120 s later with "log pattern not seen". It reproduced roughly 1
#      run in 3 at -n 4 and looked exactly like CPU starvation, which it was
#      not — this machine has 20 cores.
#
#   2. The fix for (1) was a per-xdist-worker band. That is disjoint INSIDE one
#      pytest process and identical BETWEEN processes: every serial run took
#      band 0, so a second pytest (another sweep, another agent, a shell) got
#      the same ports and reproduced (1) exactly, including the 121 s phantom
#      "guest never booted" and a stranded container.
#
# The mechanism is now a KERNEL-HELD LEASE. Each port has a lock file and a
# session holds `flock(LOCK_EX|LOCK_NB)` on it for the session's whole life.
# Two holders are impossible by construction; the lease is dropped by the
# kernel if the holder dies, so a crashed run cannot strand a port; and it does
# not care whether the other holder is an xdist worker, a second sweep or a
# hand-run pytest. The bind probe is kept as a guard against a NON-harness
# process holding the port (and against a lingering TIME_WAIT), not as the
# mechanism.
# This checkout, mirroring build.sh's own ESP32_RS derivation.
ESP32_RS_DIR = Path(__file__).resolve().parents[2]

_PORT_BASE = 21000
_PORT_COUNT = 400  # ends at 21399, well clear of the ephemeral range
_LEASE_DIR = Path(os.environ.get("ESP32TAP_PORT_LEASES", "/tmp/esp32tap-qemu-ports"))


# THE BUILD DIRECTORY IS SHARED STATE TOO, and it bit exactly as hard as the
# ports did. Every session reads `build_qemu_test/` off the BIND-MOUNTED REPO to
# merge its flash image. Nothing stopped another session's `tools/build.sh` from
# rewriting that directory mid-read, and on 2026-07-29 that is precisely what
# happened: two workflows built concurrently, a DEEP sweep failed `memreview`,
# and the failure was first diagnosed as a firmware bug and then as a QEMU clock
# artifact before the real cause — a second builder — was found. The per-session
# flash image (below) already isolates the OUTPUT; this isolates the INPUT.
#
# Readers take SHARED, builders take EXCLUSIVE, both on one file keyed by this
# checkout so worktrees do not contend. Many sessions still run at once; a build
# waits for them, and a session cannot start mid-build. The cost is real and
# accepted: a build queues behind running sessions. "Do not run two builders" was
# the convention it replaces, and I broke that convention twice in one night,
# which is the argument for making it a mechanism.
_BUILD_LOCK = Path("/tmp") / (
    "esp32tap-build-%s.lock" % hashlib.md5(str(ESP32_RS_DIR).encode()).hexdigest()[:12]
)


def _lease_build_shared() -> "object":
    """Hold the build directory against a concurrent rebuild. Returns the lock
    file; the caller keeps it alive and closes it when done."""
    f = open(_BUILD_LOCK, "a+")
    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
    return f


class HarnessError(AssertionError):
    pass


def _lease_port() -> tuple[int, "object"]:
    """Reserve a port for the caller's lifetime. Returns (port, lease_file).

    The caller MUST keep the returned file object alive until the port is
    finished with, and close it afterwards — closing releases the flock.
    """
    _LEASE_DIR.mkdir(parents=True, exist_ok=True)
    # A pid-derived start offset only SPREADS concurrent runs so they do not
    # all contend on 21000; correctness comes from the flock, not from this.
    start = os.getpid() % _PORT_COUNT
    for k in range(_PORT_COUNT):
        port = _PORT_BASE + (start + k) % _PORT_COUNT
        f = open(_LEASE_DIR / f"{port}.lease", "a+")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            continue
        # No SO_REUSEADDR: a TIME_WAIT left by the previous session on this
        # port would let the bind succeed while QEMU's own bind (which does set
        # it) is fine, but a LISTENER outside the harness must make us move on.
        # Failing the probe simply advances to the next port — there are 400.
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                f.close()
                continue
        return port, f
    raise HarnessError(f"no free port in {_PORT_BASE}..{_PORT_BASE + _PORT_COUNT - 1}")


class QemuSession:
    """One booted firmware image under QEMU inside the pinned IDF docker
    image, with both serial chardevs connected over TCP."""

    def __init__(
        self, esp32_dir: Path, build_dir: str, boot_timeout: float = 120.0, expect_shim: bool = True, net: bool = False
    ):
        self.esp32_dir = Path(esp32_dir)
        self.build_dir = build_dir
        self.expect_shim = expect_shim
        # `net` attaches QEMU's emulated openeth NIC and forwards a host
        # port to the guest's :8000, so a scenario can drive the device
        # over real HTTP instead of only through the QT serial shim.
        self.net = net
        self.http_port = None
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
        # A stimulus that dies is worse than one that never started: the guest
        # simply goes quiet and the scenario fails 30 s later pointing at the
        # firmware. Both are recorded and re-raised by every waiter.
        self._pacer_error: BaseException | None = None
        self._pacer_sent = 0
        self._reader_error: BaseException | None = None
        self._leases: list[object] = []
        self.proc: subprocess.Popen | None = None
        self.sock0: socket.socket | None = None
        self.sock1: socket.socket | None = None
        self._start(boot_timeout)

    # --- lifecycle -------------------------------------------------------

    def _lease(self) -> int:
        port, f = _lease_port()
        self._leases.append(f)
        return port

    def _start(self, boot_timeout: float) -> None:
        # Leases are held (flock) until close(), so QEMU's own bind on these
        # ports cannot race any other harness process.
        p0, p1 = self._lease(), self._lease()
        # Shared lock on the build dir: see _lease_build_shared. Closed by
        # close() with the port leases, or by the kernel if we die.
        self._leases.append(_lease_build_shared())
        nic = ""
        if self.net:
            self.http_port = self._lease()
            nic = " -nic user,model=open_eth,hostfwd=tcp::%d-:8000" % self.http_port
        bdir = shlex.quote(self.build_dir)
        # PER-SESSION FLASH IMAGE. Every session used to merge into the SAME
        # build_qemu_test/qemu_flash.bin — a path on the BIND-MOUNTED REPO,
        # shared by every container — so under xdist one session could boot
        # from an image another was still rewriting: garbage image, no boot
        # banner, and a 120 s "log pattern not seen" that looked like CPU
        # starvation. It is not — this machine has 20 cores.
        #
        # WHY /tmp IS ALREADY PRIVATE, since "per-session" and a fixed name
        # read as a contradiction: only the repo is bind-mounted (-v
        # $REPO_ROOT:/project), so /tmp here is the CONTAINER's own writable
        # layer — not the host's — and `docker run --rm` destroys it on exit.
        # That is also what bounds the residency of anything the guest commits
        # to NVS during a session, including the operator's real API key in the
        # opt-in test_coach_live.py run.
        #
        # The name is made unique anyway. It costs one f-string, and it means
        # the isolation survives somebody adding `-v /tmp:/tmp` or running the
        # merge outside a container — neither of which should have to be
        # remembered for the comment above to stay true.
        flash_img = f"/tmp/qemu_flash_{self.name}.bin"
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
            "python -m esptool --chip esp32s3 merge_bin -o " + flash_img + " "
            '@flash_args --fill-flash-size "$FS" >/dev/null 2>&1 '
            "|| python -m esptool --chip esp32s3 merge-bin -o " + flash_img + " "
            '@flash_args --pad-to-size "$FS" >/dev/null || exit 3; cd ..; '
            "exec qemu-system-xtensa -nographic -machine esp32s3 "
            "-drive file=" + flash_img + ",if=mtd,format=raw "
            "-serial tcp:127.0.0.1:%d,server=on,wait=on "
            "-serial tcp:127.0.0.1:%d,server=on,wait=on"
            "%s"
        ) % (bdir, p0, p1, nic)
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
        # EVERYTHING from here to the end of __init__ is protected. The boot
        # waits used to sit OUTSIDE this try, and the pytest fixture only
        # registers the session AFTER construction returns — so a boot timeout
        # left the container running and, worse, left this session's SHARED
        # build lock and port leases held by live daemon threads with nothing
        # able to release them. One boot timeout then became a 30-minute hang
        # for the NEXT build. Found by codex.
        try:
            # serial0 first: with wait=on QEMU creates the serial1 listener
            # only after the serial0 client connects.
            self.sock0 = self._connect(p0, timeout=60.0)
            self.sock1 = self._connect(p1, timeout=30.0)
            for sock, fn in ((self.sock0, self._reader0), (self.sock1, self._reader1)):
                t = threading.Thread(target=fn, args=(sock,), daemon=True)
                t.start()
                self._threads.append(t)
            # Guest boots only now (both chardevs connected) — no lost output.
            self.wait_log(r"esp32tap phase-1 safety core started", timeout=boot_timeout)
            if self.expect_shim:
                self.wait_log(r"qemu_test task started", timeout=30.0)
                self.wait_log(r"QEMU-TEST build", timeout=10.0)
        except BaseException:
            # BaseException, not Exception: a KeyboardInterrupt or a pytest
            # timeout must not strand the lock either.
            self.close()
            raise

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
        try:
            for t in self._threads:
                t.join(timeout=5)
        finally:
            # Release the port leases and the shared build lock LAST — after
            # QEMU is gone, so the next session cannot lease a port this one's
            # QEMU is still listening on — but in a `finally`, because an
            # exception from docker kill, wait or stdout cleanup used to skip
            # this entirely and strand every lease this session held.
            for f in self._leases:
                try:
                    f.close()  # closing the fd drops the flock
                except OSError:
                    pass
            self._leases = []

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
            except OSError as e:
                if not self._stop.is_set():
                    self._reader_error = e
                return
            if not data:
                if not self._stop.is_set():
                    self._reader_error = HarnessError("UART1 capture socket closed by QEMU")
                return
            with self._u1_lock:
                self._chunks1.append((time.monotonic(), data))

    # --- injection -------------------------------------------------------

    def _write_whole(self, sock: socket.socket, data: bytes, what: str, timeout: float = 30.0) -> None:
        """Write EVERY byte of `data`, or raise saying how far it got.

        `socket.sendall` must not be used on these sockets. Both carry a 0.5 s
        timeout so the reader threads can poll the stop flag, and `sendall` on
        a timed socket raises `socket.timeout` — an OSError — AFTER a partial
        write, without reporting how much went out. One moment of back-pressure
        (QEMU drains a chardev only as fast as the guest consumes it) therefore
        truncated a console frame mid-way, and the pacer's `except OSError:
        return` then killed the writer thread for the rest of the session: the
        guest saw zero complete console frames while every other signal — boot,
        logs, QTOK acks, heartbeats — looked perfectly healthy, and the
        scenario failed 30 s later pointing at the firmware.
        `send` never reports a partial count on timeout, so tracking the offset
        here is exact. This is not a retry: the write is one write, bounded.
        """
        deadline = time.monotonic() + timeout
        sent = 0
        while sent < len(data):
            try:
                sent += sock.send(data[sent:])
            except socket.timeout:
                if time.monotonic() > deadline:
                    raise HarnessError(
                        f"{what}: only {sent}/{len(data)} bytes accepted in {timeout:.0f}s — "
                        "QEMU is not draining this chardev"
                    ) from None

    def send_console(self, data: bytes) -> None:
        assert self.sock1 is not None
        self._write_whole(self.sock1, data, "send_console (UART1 RX)")

    def send_motor(self, data: bytes) -> None:
        assert self.sock0 is not None
        with self._writer_lock:
            self._write_whole(self.sock0, data, "send_motor (UART0 RX)")

    def cmd(self, command: str) -> None:
        """Send a shim command, framed \\nQT ...\\n (single UART0 writer
        lock shared with send_motor so a command never interleaves inside
        a motor burst)."""
        assert command.startswith("QT ")
        assert self.sock0 is not None
        with self._writer_lock:
            self._write_whole(self.sock0, b"\n" + command.encode("ascii") + b"\n", "cmd (UART0 RX)")

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

        self._pacer_error = None
        self._pacer_sent = 0

        def run() -> None:
            while not self._pacer_stop.is_set():
                p = self._pacer_payload
                if p:
                    try:
                        self.send_console(p)
                    except BaseException as e:  # noqa: BLE001 — RECORDED, never swallowed
                        self._pacer_error = e
                        return
                    self._pacer_sent += 1
                self._pacer_stop.wait(interval)

        self._pacer_thread = threading.Thread(target=run, daemon=True)
        self._pacer_thread.start()

    def set_pacer_payload(self, payload: bytes) -> None:
        self._pacer_payload = payload

    def stop_pacer(self) -> None:
        # Deliberately silent: close() calls this on the teardown path. The
        # pacer's own failure is surfaced by _check_stimulus() at the waiter,
        # where the test is still able to attribute it.
        self._pacer_stop.set()
        if self._pacer_thread is not None:
            self._pacer_thread.join(timeout=5)
            self._pacer_thread = None

    def _check_stimulus(self) -> None:
        """Fail NOW, naming the byte path, if the stimulus or the capture died.

        Every wait_* call goes through here. Without it a dead pacer or a dead
        UART1 capture thread is indistinguishable from a firmware that stopped
        responding, and the scenario blames the guest after burning its whole
        timeout.
        """
        if self._pacer_error is not None:
            raise HarnessError(
                f"console pacer STOPPED after {self._pacer_sent} payloads: "
                f"{self._pacer_error!r} — the guest received no further console "
                "bytes, so any missing console/emulate event follows from this"
            )
        if self._reader_error is not None:
            raise HarnessError(f"UART1 capture reader STOPPED: {self._reader_error!r}")

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
            self._check_stimulus()
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
            self._check_stimulus()
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
            self._check_stimulus()
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
            self._check_stimulus()
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
            self._check_stimulus()
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
        # STIMULUS FIRST. "0 bytes captured" means something completely
        # different depending on whether anything was ever injected, and that
        # ambiguity cost a full investigation once already.
        out.append(
            f"=== stimulus === pacer payloads sent={self._pacer_sent} "
            f"pacer_error={self._pacer_error!r} reader_error={self._reader_error!r}"
        )
        out.append(f"=== UART1 TX capture ({len(tx)} bytes, last 2048) ===")
        out.append(tx[-2048:].hex(" "))
        out.append("=== audit tail ===")
        out += [f"{i} {t}" for i, t in self.audit_events()[-40:]]
        return "\n".join(out)
