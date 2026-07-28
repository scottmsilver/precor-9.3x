"""TLS client for the scenarios — the device serves HTTPS on :8000 only.

Verification is DISABLED on purpose and that is not a loosened assertion: the
certificate is self-signed by the device itself, exactly as the Pi's is, and
both shipping clients (`TrustAllDelegate` on iOS, `trustAllTls` on Android) do
the same. What a successful call here proves is the thing that was previously
unprovable — that a real TLS handshake completed against a key the device
generated for itself, and that the app-facing JSON came back over it.

It deliberately does NOT paper over a TLS failure by retrying on plain HTTP.
If the handshake breaks, these tests must go red.
"""

from __future__ import annotations

import json
import ssl
import urllib.request


def tls_context() -> ssl.SSLContext:
    """A context that completes the handshake but checks no names or chains."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def request(sess, method: str, path: str, body=None, timeout: float = 20):
    """Issue one HTTPS request against the guest, returning (status, json)."""
    url = f"https://127.0.0.1:{sess.http_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=tls_context()) as r:
        return r.status, json.loads(r.read().decode())


def peer_certificate(sess, timeout: float = 20) -> bytes:
    """Complete a handshake and return the server's certificate in DER.

    Used to prove the identity is the device's own and that it is STABLE — the
    same bytes on a later connection means a client could have pinned it.
    """
    import socket

    with socket.create_connection(("127.0.0.1", sess.http_port), timeout=timeout) as raw:
        with tls_context().wrap_socket(raw, server_hostname="esp32tap") as tls:
            return tls.getpeercert(binary_form=True)
