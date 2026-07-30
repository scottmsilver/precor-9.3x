"""Slice 3 proof: the device puts a REAL DNS-SD announcement on the wire.

Not "the firmware logged that it called mdns_service_add" — the Ethernet frames
the device transmits are captured with QEMU's `filter-dump` and the DNS records
inside them are decoded. Every field asserted here is one Android's
`NsdManager` uses, and each is checked against `deploy/treadmill.avahi-service`,
the record the Pi publishes, so that ONE discovery implementation finds either
box.

See mdnsq.py for why this is a capture rather than a query, and for the measured
reason a query cannot work through slirp.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdnsq  # noqa: E402
import pytest  # noqa: E402

ESP32_RS = HERE.parents[1]
SERVICE = "_treadmill._tcp.local"

# The responder announces on a timer, roughly 1 s / 2 s / 3 s of GUEST time
# after the service is added, and then stops until the TTL refresh. The capture
# runs from power-on, so all we have to do is not stop the guest too early.
#
# WAITING ON WALL TIME HERE WOULD BE AN INTERMITTENT WAITING TO HAPPEN: under
# xdist the emulator shares the machine with several other QEMUs, and guest time
# can lag wall time by a large factor. So we wait on the guest's own 5-second
# heartbeat instead. Two of them bound the announcement window in the same clock
# the announcements are scheduled by, whatever the host is doing.
HEARTBEATS_TO_WAIT = 2


@pytest.fixture
def mdns_capture():
    """Boot the net image with a pcap filter on the emulated NIC."""
    sessions = []

    def factory(**kw):
        s = mdnsq.MdnsCaptureSession(ESP32_RS, "build_qemu_test", net=True, **kw)
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        s.close()


def capture(session):
    """Wait for the advertisement, then stop the guest and read the pcap."""
    session.wait_log(r"mdns: _treadmill\._tcp on :8000", timeout=180)
    mark = session.line_count()
    for _ in range(HEARTBEATS_TO_WAIT):
        line = session.wait_log(r"heartbeat uptime=\d+s", timeout=180, since_line=mark)
        mark = session.line_count()
        assert line  # a heartbeat is guest-clock progress, not host-clock
    return session.flush_capture()


def by_type(records):
    out = {}
    for r in records:
        out.setdefault(r["type"], []).append(r)
    return out


def test_device_announces_a_real_dnssd_record(mdns_capture):
    s = mdns_capture()
    pcap = capture(s)

    payloads = mdnsq.mdns_payloads(pcap)
    assert payloads, "no mDNS frames were transmitted at all"

    # It is a MULTICAST announcement, not a reply to one listener. This is what
    # lets a browsing client see the device without asking.
    dests = mdnsq.multicast_destinations(pcap)
    assert "224.0.0.251" in dests, dests

    records = mdnsq.announced_records(pcap)
    groups = by_type(records)

    # (1) PTR — the service instance exists under the type the app browses for.
    ptrs = groups.get(mdnsq.TYPE_PTR, [])
    assert ptrs, f"no PTR record announced: {records}"
    inst = next((p["target"] for p in ptrs if p["name"] == SERVICE), None)
    assert inst is not None, f"no PTR for {SERVICE}: {records}"
    assert inst.endswith(SERVICE), inst

    # (2) SRV — the PORT. This is the number the app connects to, and it must
    # be 8000, matching <port>8000</port> in the Pi's Avahi record.
    srvs = groups.get(mdnsq.TYPE_SRV, [])
    assert srvs, f"no SRV record: {records}"
    srv = next((r for r in srvs if r["name"] == inst), None)
    assert srv is not None, f"no SRV for {inst}: {records}"
    assert srv["port"] == 8000, srv
    assert srv["target"].startswith("esp32tap"), srv

    # (3) TXT — scheme and path, byte-for-byte the Pi's two txt-records.
    #     `scheme=https` is only honest because the server really is TLS; the
    #     handshake itself is proven in test_tls.py.
    txts = groups.get(mdnsq.TYPE_TXT, [])
    assert txts, f"no TXT record: {records}"
    txt = next((r["txt"] for r in txts if r["name"] == inst), None)
    assert txt is not None, f"no TXT for {inst}: {records}"
    assert txt.get("scheme") == "https", txt
    assert txt.get("path") == "/", txt

    # (4) A — the SRV target resolves to the address DHCP actually handed out,
    #     so a client that follows the record reaches this device.
    addrs = {r["addr"] for r in groups.get(mdnsq.TYPE_A, []) if r["name"] == srv["target"]}
    assert addrs, f"no A record for {srv['target']}: {records}"
    assert "10.0.2.15" in addrs, addrs


def test_announced_record_matches_the_pi(mdns_capture):
    """The Pi's Avahi file is the contract; parse it and compare field by field.

    Hard-coding the expected values here would let the two drift apart silently,
    which is the entire failure mode this pairing exists to prevent.
    """
    import xml.etree.ElementTree as ET

    avahi = ESP32_RS.parents[3] / "deploy" / "treadmill.avahi-service"
    assert avahi.exists(), f"the Pi's service file is missing: {avahi}"
    root = ET.parse(avahi).getroot()
    svc = root.find("service")
    pi_type = svc.findtext("type")
    pi_port = int(svc.findtext("port"))
    pi_txt = dict(t.text.split("=", 1) for t in svc.findall("txt-record"))

    s = mdns_capture()
    records = mdnsq.announced_records(capture(s))
    groups = by_type(records)

    ptr = next(
        (r for r in groups.get(mdnsq.TYPE_PTR, []) if r["name"] == f"{pi_type}.local"),
        None,
    )
    assert ptr is not None, f"device does not advertise {pi_type}: {records}"
    inst = ptr["target"]

    srv = next((r for r in groups.get(mdnsq.TYPE_SRV, []) if r["name"] == inst), None)
    txt = next((r for r in groups.get(mdnsq.TYPE_TXT, []) if r["name"] == inst), None)
    assert srv is not None and txt is not None, records
    assert srv["port"] == pi_port, (srv["port"], pi_port)
    for k, v in pi_txt.items():
        assert txt["txt"].get(k) == v, (k, txt["txt"].get(k), v)
