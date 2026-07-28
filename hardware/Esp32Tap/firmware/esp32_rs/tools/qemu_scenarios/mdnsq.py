"""A real mDNS/DNS-SD querier, plus the QEMU plumbing to reach the guest.

WHY A QUERY AND NOT A PCAP. The strongest evidence would be capturing the
guest's unsolicited multicast announcement with `-object filter-dump`. That is
not available here and the reason is structural, not a missing flag: on the
`esp32s3` machine `open_eth` is wired in by the machine itself via `-nic`, so
there is no netdev id for a filter to bind to, and the pluggable form is
refused outright --

    qemu-system-xtensa: -device open_eth,netdev=n0:
        Parameter 'driver' expects a pluggable device type

-- while `-object filter-dump,netdev=<id>` against every id `-nic` might have
generated answers "Parameter 'netdev' expects a network backend id". QEMU's
user-mode (slirp) backend also does not route multicast out to the host, so a
host-side capture of the announcement is not possible either.

What IS possible, and is what this module does, is a genuine DNS-SD exchange:
a UDP port is forwarded to the guest's 5353 and a real PTR query for
`_treadmill._tcp.local` is sent to the responder. RFC 6762 §6.7 requires a
responder that receives a query from a source port other than 5353 -- which is
exactly what slirp's NAT produces -- to answer with a LEGACY UNICAST response,
so the reply comes straight back. The bytes parsed below are the responder's
actual DNS records: the same PTR/SRV/TXT that Android's `NsdManager` resolves.
"""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))

import qemu_session  # noqa: E402
from qemu_session import QemuSession, _free_port  # noqa: E402

MDNS_PORT = 5353
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33


class MdnsQemuSession(QemuSession):
    """`QemuSession` plus a host UDP port forwarded to the guest's mDNS port.

    The committed harness is byte-locked (`tools/verify_harness_copy.py`
    asserts every file against `git show HEAD:`), so the extra QEMU argument
    cannot be added by editing it. It is injected at the process-spawn boundary
    instead: the harness builds one `bash -c` script string as the last element
    of its docker argv, and this appends to that string. Nothing about the
    harness's own behaviour changes.
    """

    def __init__(self, *args, **kwargs):
        self.mdns_port = _free_port()
        super().__init__(*args, **kwargs)

    def _start(self, boot_timeout):
        real_popen = subprocess.Popen

        def patched(argv, **kw):
            argv = list(argv)
            # The harness already emitted `-nic user,model=open_eth,hostfwd=tcp:...`.
            # QEMU takes only one such NIC, so the UDP forward is MERGED into
            # that existing option rather than appended as a second one.
            argv[-1] = argv[-1].replace(
                "hostfwd=tcp::%d-:8000" % self.http_port,
                "hostfwd=tcp::%d-:8000,hostfwd=udp::%d-:%d" % (self.http_port, self.mdns_port, MDNS_PORT),
            )
            return real_popen(argv, **kw)

        qemu_session.subprocess.Popen = patched
        try:
            super()._start(boot_timeout)
        finally:
            qemu_session.subprocess.Popen = real_popen


# --- minimal DNS wire codec -------------------------------------------------


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        if label:
            out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def build_ptr_query(service: str, txid: int = 0x4242) -> bytes:
    """One standard PTR question. No QU bit: the source port alone (slirp NATs
    it away from 5353) is what obliges a legacy unicast reply."""
    header = struct.pack("!HHHHHH", txid, 0x0000, 1, 0, 0, 0)
    return header + encode_name(service) + struct.pack("!HH", TYPE_PTR, 1)


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
    """Parse every resource record in a response into dicts."""
    txid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = _read_name(buf, off)
        off += 4
    out = []
    for _ in range(an + ns + ar):
        name, off = _read_name(buf, off)
        rtype, rclass, _ttl, rdlen = struct.unpack("!HHIH", buf[off : off + 10])
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


def query(port: int, service: str = "_treadmill._tcp.local", timeout: float = 8.0):
    """Send one PTR query to 127.0.0.1:`port` and return the parsed records."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(build_ptr_query(service), ("127.0.0.1", port))
        data, _ = sock.recvfrom(4096)
        return data, parse_records(data)
    finally:
        sock.close()
