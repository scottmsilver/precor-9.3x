"""Slice 3 proof: the device answers a REAL DNS-SD query on the wire.

Not "the firmware logged that it called mdns_service_add" — an actual mDNS
packet is sent from the host to the responder and the returned DNS records are
decoded. Every field asserted here is one Android's `NsdManager` uses, and each
is checked against `deploy/treadmill.avahi-service`, the record the Pi
publishes, so that ONE discovery implementation finds either box.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mdnsq  # noqa: E402
import pytest  # noqa: E402

ESP32_RS = HERE.parents[1]
SERVICE = "_treadmill._tcp.local"


@pytest.fixture
def mdns_qemu(request):
    """Boot the net image with a UDP port forwarded to the guest's mDNS port."""
    sessions = []

    def factory(**kw):
        s = mdnsq.MdnsQemuSession(ESP32_RS, "build_qemu_test", net=True, **kw)
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        s.close()


def test_device_answers_a_real_dnssd_query(mdns_qemu):
    s = mdns_qemu()
    s.wait_log(r"mdns: _treadmill\._tcp on :8000", timeout=180)

    raw, records = mdnsq.query(s.mdns_port)
    assert raw, "no mDNS response"

    by_type = {}
    for r in records:
        by_type.setdefault(r["type"], []).append(r)

    # (1) PTR — the service instance exists under the type the app browses for.
    ptrs = by_type.get(mdnsq.TYPE_PTR, [])
    assert ptrs, f"no PTR record in response: {records}"
    inst = next((p["target"] for p in ptrs if p["name"] == SERVICE), None)
    assert inst is not None, f"no PTR for {SERVICE}: {records}"
    assert inst.endswith(SERVICE), inst

    # (2) SRV — the PORT. This is the number the app connects to, and it must
    # be 8000, matching <port>8000</port> in the Pi's Avahi record.
    srvs = by_type.get(mdnsq.TYPE_SRV, [])
    assert srvs, f"no SRV record: {records}"
    srv = next((r for r in srvs if r["name"] == inst), srvs[0])
    assert srv["port"] == 8000, srv
    assert srv["target"].startswith("esp32tap"), srv

    # (3) TXT — scheme and path, byte-for-byte the Pi's two txt-records.
    #     `scheme=https` is only honest because the server really is TLS; the
    #     handshake itself is proven in test_tls.py.
    txts = by_type.get(mdnsq.TYPE_TXT, [])
    assert txts, f"no TXT record: {records}"
    txt = next((r["txt"] for r in txts if r["name"] == inst), txts[0]["txt"])
    assert txt.get("scheme") == "https", txt
    assert txt.get("path") == "/", txt

    # (4) A — the instance resolves to the address DHCP actually handed out,
    #     so a client that follows the record reaches this device.
    addrs = [r["addr"] for r in by_type.get(mdnsq.TYPE_A, [])]
    assert addrs, f"no A record: {records}"
    assert "10.0.2.15" in addrs, addrs


def test_advertised_record_matches_the_pi(mdns_qemu):
    """The Pi's Avahi file is the contract; parse it and compare field by field.

    Hard-coding the expected values in this test would let the two drift apart
    silently, which is the entire failure mode this pairing exists to prevent.
    """
    import xml.etree.ElementTree as ET

    avahi = ESP32_RS.parents[3] / "deploy" / "treadmill.avahi-service"
    assert avahi.exists(), f"the Pi's service file is missing: {avahi}"
    root = ET.parse(avahi).getroot()
    svc = root.find("service")
    pi_type = svc.findtext("type")
    pi_port = int(svc.findtext("port"))
    pi_txt = dict(t.text.split("=", 1) for t in svc.findall("txt-record"))

    s = mdns_qemu()
    s.wait_log(r"mdns: _treadmill\._tcp on :8000", timeout=180)
    _, records = mdnsq.query(s.mdns_port, service=f"{pi_type}.local")

    srv = next((r for r in records if r["type"] == mdnsq.TYPE_SRV), None)
    txt = next((r for r in records if r["type"] == mdnsq.TYPE_TXT), None)
    assert srv is not None and txt is not None, records
    assert srv["port"] == pi_port, (srv["port"], pi_port)
    for k, v in pi_txt.items():
        assert txt["txt"].get(k) == v, (k, txt["txt"].get(k), v)
