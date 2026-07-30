"""Slice 3 proof: the device serves HTTPS on :8000 with its OWN certificate.

This is the test the recorded blocker made impossible to write. `httpd_ssl_*`
generated zero symbols, so `httpd_ssl_start` could not be called at all; the
identity in net/tls.rs compiled and nothing used it.

What is asserted here is a REAL handshake — python's ssl module negotiating
with mbedtls on the guest — and then the app-facing banner arriving over that
channel. Certificate verification is off (self-signed, same as the Pi, same as
both shipping clients), but negotiating at all is the proof: a plaintext server
cannot answer a ClientHello.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import httpc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

BANNER = {"service": "precor-treadmill", "api": "/api", "ws": "/ws"}


def test_https_handshake_and_banner(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

    # (1) A real TLS handshake completes and yields a certificate.
    der = httpc.peer_certificate(s)
    assert der, "no peer certificate — no handshake happened"
    # A P-256 self-signed cert is a few hundred bytes; anything tiny would mean
    # we parsed something that is not a certificate.
    assert 200 < len(der) < 1024, len(der)

    # (2) The identity is the DEVICE'S OWN, not a build-time constant baked
    # into the image. The subject is what net/tls.rs writes.
    text = der.decode("latin-1")
    assert "esp32tap" in text, "certificate subject is not the device identity"
    assert "precor-treadmill" in text, text[:200]

    # (3) The app-facing surface actually works over that channel — the same
    # byte-identical banner python/server.py returns.
    st, body = httpc.request(s, "GET", "/")
    assert st == 200, st
    assert body == BANNER, body

    # (4) The identity is STABLE within a boot: a second, independent
    # connection presents the same certificate. (Cross-boot stability is what
    # NVS persistence buys and is asserted in test_tls_persistence.py.)
    assert httpc.peer_certificate(s) == der, "certificate changed between connections"


def test_no_plaintext_listener_on_8000(qemu):
    """The advertised scheme is `https`; there must be no HTTP fallback.

    A plaintext listener alongside TLS would mean a client that ignored the TXT
    record still worked, and every later test would silently stop proving
    anything about TLS.
    """
    import http.client

    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

    conn = http.client.HTTPConnection("127.0.0.1", s.http_port, timeout=15)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
    except Exception:
        # Expected: the TLS server cannot parse a plaintext request line.
        return
    finally:
        conn.close()
    raise AssertionError(f"plaintext HTTP was answered on :8000 (status {resp.status}, {body[:80]!r})")
