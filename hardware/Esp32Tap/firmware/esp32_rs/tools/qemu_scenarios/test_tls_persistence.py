"""Slice 3 proof: the TLS identity SURVIVES A REBOOT.

This is the claim that makes trust-on-first-use mean anything. A key that is
regenerated on every boot is not an identity — "first use" would be every power
cycle, and a client that pinned the certificate would break every time the
treadmill was switched off. Asserting that `nvs_set_blob` returned ESP_OK proves
none of that.

So the guest is actually RESET, in the same QEMU process, and the certificate is
compared byte-for-byte across the reset. QEMU's flash is a real file
(`-drive file=...,if=mtd,format=raw`) that outlives a guest reset, so the second
boot reads the same NVS partition a real power cycle would.

WHAT THIS DOES NOT PROVE: that the bytes survive loss of power to a physical
part, or a flash-wear/corruption case. Those need a board.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import httpc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

BANNER = {"service": "precor-treadmill", "api": "/api", "ws": "/ws"}


def test_identity_survives_a_reboot(qemu):
    s = qemu(net=True)

    # First boot: nothing is stored yet, so the identity is made and persisted.
    s.wait_log(r"tls: identity persisted to NVS \(readback ok\)", timeout=180)
    s.wait_log(r"tls: identity generated this boot", timeout=10)
    s.wait_log(r"https server up on :8000", timeout=60)
    first = httpc.peer_certificate(s)
    assert first, "no peer certificate on the first boot"

    # Reset the SoC. Everything in RAM is gone; only flash survives.
    mark = s.line_count()
    s.cmd_ok("QT reboot")
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120, since_line=mark)

    # Second boot: the identity must come BACK OUT of NVS, not be made again.
    s.wait_log(r"tls: identity loaded from NVS", timeout=120, since_line=mark)
    s.wait_log(r"https server up on :8000", timeout=60, since_line=mark)

    # ...and it must be the SAME certificate, byte for byte. A client that
    # pinned `first` still trusts the device.
    second = httpc.peer_certificate(s)
    assert second == first, (
        "certificate changed across a reboot — persistence is not working, "
        f"{len(first)} bytes before vs {len(second)} after"
    )

    # The device is fully serving again, not merely presenting a certificate.
    st, body = httpc.request(s, "GET", "/")
    assert st == 200 and body == BANNER, (st, body)
