"""Capture the device's REAL mDNS frames off the emulated wire, and decode them.

WHY A CAPTURE AND NOT A QUERY (this changed, and the reason is measured, not
assumed). The first version of this module sent a UDP query into the guest
through a slirp `hostfwd` and parsed the reply. It never got one, and the cause
is structural — it cannot be fixed by waiting longer or retrying:

  * slirp does NOT translate the source address of a `hostfwd`-ed packet. Its
    `sotranslate_in()` only rewrites when the socket's `so_faddr` is inside the
    virtual network, and a hostfwd listener leaves `so_faddr` unset. The query
    therefore arrives at the guest still claiming to come from 127.0.0.1.
  * `espressif/mdns` drops that packet before it is even parsed.
    `mdns_networking_lwip.c:_udp_recv()` matches the receiving interface and
    then requires the SOURCE to be in that interface's subnet
    (`src & netmask != ip & netmask` -> "packet source is not in the same
    subnet" -> `break`, packet freed). 127.0.0.0 is not 10.0.2.0, so it is
    discarded silently, with no log and no reply.

  Confirmed by building the component with `CONFIG_MDNS_ENABLE_DEBUG_PRINTS=y`:
  the responder printed its own announcement (`TX[0][0]: To: 224.0.0.251:5353`)
  with the full record set, and printed NO `RX` line at all for the query. The
  responder was healthy; the query never reached it.

The earlier note here also claimed a capture was impossible because `-nic`
leaves no netdev id for `-object filter-dump` to bind to. That is not true, and
it was never tested: `-nic user,id=n0,model=open_eth,...` accepts an id, and
`-object filter-dump,id=f0,netdev=n0,file=...` attaches to it and writes an
ordinary pcap. Verified directly against the pinned esp-QEMU build.

So this module takes the STRONGER evidence the first attempt reached for and
gave up on: the actual Ethernet frames the device transmits. What the tests
decode is the announcement `espressif/mdns` puts on the wire — the same
multicast DNS-SD records Android's `NsdManager` receives — not a log line
claiming it did.

The capture is flushed by stopping QEMU with SIGTERM. `filter-dump` writes
through buffered stdio and only `fclose`s during QEMU's normal cleanup, so the
SIGKILL the base harness uses would throw the buffer away.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))

import qemu_session  # noqa: E402
from qemu_session import HarnessError, QemuSession  # noqa: E402

MDNS_PORT = 5353
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33


_CAPTURE_START_LOCK = threading.RLock()


class _CaptureSubprocessProxy:
    """Per-start subprocess dependency for the byte-pinned base harness.

    `qemu_session.subprocess` normally refers to Python's process-global
    ``subprocess`` module. Replacing ``subprocess.Popen`` through that alias
    therefore replaces it for artifact provenance, Git, Docker inspection,
    and every other thread too. This proxy leaves the shared module untouched:
    it delegates every operation and every non-QEMU spawn byte-for-byte, and
    rewrites only the exact Docker/QEMU command emitted by ``QemuSession``.
    """

    def __init__(self, delegate, session: "MdnsCaptureSession"):
        self._delegate = delegate
        self._session = session

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def Popen(self, argv, *args, **kwargs):  # noqa: N802 - mirror subprocess API
        if not self._is_session_qemu_run(argv):
            return self._delegate.Popen(argv, *args, **kwargs)
        rewritten = self._rewrite_qemu_run(argv)
        return self._delegate.Popen(rewritten, *args, **kwargs)

    def _is_session_qemu_run(self, argv) -> bool:
        if not isinstance(argv, (list, tuple)) or not all(
            isinstance(value, str) for value in argv
        ):
            return False
        if len(argv) < 10 or list(argv[:3]) != ["docker", "run", "--rm"]:
            return False
        try:
            name_index = argv.index("--name")
        except ValueError:
            return False
        if name_index + 1 >= len(argv) or argv[name_index + 1] != self._session.name:
            return False
        return (
            list(argv[-3:-1]) == ["bash", "-c"]
            and "exec qemu-system-xtensa " in argv[-1]
            and "-drive file=" in argv[-1]
            and argv[-1].count("-serial tcp:127.0.0.1:") == 2
        )

    def _rewrite_qemu_run(self, argv) -> list[str]:
        rewritten = list(argv)
        script = rewritten[-1]
        expected_nic = "-nic user,model=open_eth,"
        if script.count(expected_nic) != 1:
            raise HarnessError("harness did not emit the expected -nic option")
        if "id=n0" in script or "filter-dump" in script or "/pcap" in script:
            raise HarnessError("harness QEMU command already contains capture options")
        rewritten[-1] = (
            script.replace(
                expected_nic,
                "-nic user,id=n0,model=open_eth,",
                1,
            )
            + " -object filter-dump,id=f0,netdev=n0,file=/pcap/wire.pcap"
        )
        # Insert among Docker flags, before the image name.
        rewritten[3:3] = ["-v", f"{self._session.capture_dir}:/pcap"]
        return rewritten


class MdnsCaptureSession(QemuSession):
    """`QemuSession` plus a `filter-dump` pcap of everything the NIC carries.

    The committed harness is byte-locked (`tools/verify_harness_copy.py` asserts
    every file against `git show HEAD:`), so the extra QEMU arguments cannot be
    added by editing it. They are injected at the process-spawn boundary
    instead: the harness builds one `bash -c` script string as the last element
    of its docker argv, and this rewrites that string and adds one bind mount.
    Nothing about the harness's own behaviour changes.
    """

    def __init__(self, *args, **kwargs):
        # A per-session directory on the host, bind-mounted into the container.
        # Per-session because sessions run in parallel under xdist, and a shared
        # path is exactly the race the harness was fixed to remove.
        self.capture_dir = Path(tempfile.mkdtemp(prefix="esp32tap-pcap-"))
        self.pcap = self.capture_dir / "wire.pcap"
        super().__init__(*args, **kwargs)

    def _start(self, boot_timeout):
        # The base harness is sha-pinned, so inject its subprocess dependency
        # without editing it. Serializing this short injection scope prevents
        # two capture sessions from nesting proxies; ordinary concurrent
        # QemuSessions remain safe because the proxy passes their calls through.
        # BaseException is intentional: Ctrl-C and pytest timeouts must restore
        # the module alias just as reliably as normal construction failures.
        with _CAPTURE_START_LOCK:
            real_subprocess = qemu_session.subprocess
            proxy = _CaptureSubprocessProxy(real_subprocess, self)
            qemu_session.subprocess = proxy
            try:
                super()._start(boot_timeout)
            finally:
                qemu_session.subprocess = real_subprocess

    def flush_capture(self, timeout: float = 30.0) -> bytes:
        """Stop the guest cleanly and return the finished pcap.

        SIGTERM, not SIGKILL: `filter-dump` uses buffered stdio and only flushes
        when QEMU's object cleanup runs `fclose`. Killing outright loses every
        packet still in the buffer, which reads as "the device never announced"
        — a false negative indistinguishable from a real bug.
        """
        subprocess.run(
            ["docker", "kill", "--signal=TERM", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(0.2)
        else:
            raise HarnessError("QEMU did not exit on SIGTERM; capture not flushed")
        if not self.pcap.exists():
            raise HarnessError(f"no capture written at {self.pcap}")
        return self.pcap.read_bytes()

    def close(self) -> None:
        try:
            super().close()
        finally:
            shutil.rmtree(self.capture_dir, ignore_errors=True)


# --- pcap -> mDNS payloads --------------------------------------------------


def mdns_payloads(pcap: bytes) -> list[bytes]:
    """Every UDP/5353 payload in an Ethernet pcap, in capture order.

    Deliberately hand-rolled rather than pulled from a library: the parse is a
    dozen fixed-offset reads and this must run wherever the harness runs.
    """
    if len(pcap) < 24:
        raise ValueError("truncated pcap")
    magic = struct.unpack("<I", pcap[:4])[0]
    if magic == 0xA1B2C3D4:
        endian = "<"
    elif magic == 0xD4C3B2A1:
        endian = ">"
    else:
        raise ValueError(f"not a pcap file (magic {magic:#x})")
    linktype = struct.unpack(endian + "I", pcap[20:24])[0]
    if linktype != 1:  # LINKTYPE_ETHERNET
        raise ValueError(f"unexpected pcap linktype {linktype}")

    out: list[bytes] = []
    off = 24
    while off + 16 <= len(pcap):
        _ts, _us, caplen, _origlen = struct.unpack(endian + "IIII", pcap[off : off + 16])
        off += 16
        frame = pcap[off : off + caplen]
        off += caplen
        if len(frame) < 14:
            continue
        if struct.unpack("!H", frame[12:14])[0] != 0x0800:  # IPv4
            continue
        ip = frame[14:]
        if len(ip) < 20 or (ip[0] >> 4) != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 17:  # UDP
            continue
        udp = ip[ihl:]
        if len(udp) < 8:
            continue
        sport, dport, ulen = struct.unpack("!HHH", udp[:6])
        if MDNS_PORT not in (sport, dport):
            continue
        out.append(udp[8:ulen])
    return out


def multicast_destinations(pcap: bytes) -> set[str]:
    """Destination IPs of the captured mDNS frames.

    Used to assert the announcement really went to the DNS-SD group rather than
    to one host that happened to be listening.
    """
    dests: set[str] = set()
    if len(pcap) < 24:
        return dests
    endian = "<" if struct.unpack("<I", pcap[:4])[0] == 0xA1B2C3D4 else ">"
    off = 24
    while off + 16 <= len(pcap):
        _ts, _us, caplen, _origlen = struct.unpack(endian + "IIII", pcap[off : off + 16])
        off += 16
        frame = pcap[off : off + caplen]
        off += caplen
        if len(frame) < 34 or struct.unpack("!H", frame[12:14])[0] != 0x0800:
            continue
        ip = frame[14:]
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 17 or len(ip) < ihl + 8:
            continue
        if MDNS_PORT not in struct.unpack("!HH", ip[ihl : ihl + 4]):
            continue
        dests.add(".".join(str(b) for b in ip[16:20]))
    return dests


# --- minimal DNS wire codec -------------------------------------------------


def _read_name(buf: bytes, off: int) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name. Returns (name, next_offset)."""
    labels, jumped, next_off = [], False, off
    guard = 0
    while True:
        guard += 1
        if guard > 128 or off >= len(buf):
            raise ValueError("malformed DNS name")
        ln = buf[off]
        if ln == 0:
            off += 1
            if not jumped:
                next_off = off
            break
        if ln & 0xC0 == 0xC0:  # compression pointer
            ptr = struct.unpack("!H", buf[off : off + 2])[0] & 0x3FFF
            if not jumped:
                next_off = off + 2
            off, jumped = ptr, True
            continue
        labels.append(buf[off + 1 : off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
        if not jumped:
            next_off = off
    return ".".join(labels), next_off


def parse_records(buf: bytes) -> list[dict]:
    """Parse every resource record in a DNS message into dicts."""
    _txid, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = _read_name(buf, off)
        off += 4
    out = []
    for _ in range(an + ns + ar):
        name, off = _read_name(buf, off)
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", buf[off : off + 10])
        off += 10
        rdata = buf[off : off + rdlen]
        rec = {"name": name, "type": rtype}
        if rtype == TYPE_PTR:
            rec["target"], _ = _read_name(buf, off)
        elif rtype == TYPE_SRV:
            _prio, _w, port = struct.unpack("!HHH", rdata[:6])
            rec["port"] = port
            rec["target"], _ = _read_name(buf, off + 6)
        elif rtype == TYPE_TXT:
            txt, i = {}, 0
            while i < len(rdata):
                ln = rdata[i]
                item = rdata[i + 1 : i + 1 + ln].decode("utf-8", "replace")
                k, _, v = item.partition("=")
                txt[k] = v
                i += 1 + ln
            rec["txt"] = txt
        elif rtype == TYPE_A:
            rec["addr"] = ".".join(str(b) for b in rdata)
        out.append(rec)
        off += rdlen
    return out


def announced_records(pcap: bytes) -> list[dict]:
    """Every record the device announced, across all captured mDNS messages.

    A message that will not parse is DROPPED, never repaired: the assertions
    must operate on records that were genuinely on the wire, and a lenient
    decoder is how a test starts passing on garbage.
    """
    records: list[dict] = []
    for payload in mdns_payloads(pcap):
        records.extend(parse_records(payload))
    return records
