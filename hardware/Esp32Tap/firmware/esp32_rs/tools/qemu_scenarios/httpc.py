"""TLS client for the scenarios — the device serves HTTPS on :8000 only.

Verification is DISABLED on purpose and that is not a loosened assertion: the
certificate is self-signed by the device itself, exactly as the Pi's is, and
both shipping clients (`TrustAllDelegate` on iOS, `trustAllTls` on Android) do
the same. What a successful call here proves is the thing that was previously
unprovable — that a real TLS handshake completed against a key the device
generated for itself, and that the app-facing JSON came back over it.

It deliberately does NOT paper over a TLS failure by retrying on plain HTTP.
If the handshake breaks, these tests must go red.

WHEN THE HANDSHAKE BREAKS, IT SAYS WHOSE FAULT IT WAS. A refused handshake
reaches Python as `SSL: UNEXPECTED_EOF_WHILE_READING`, which reads like a
broken or crashed server and is often neither: the device runs
`esp_tls_create_server_session` on its single httpd worker under a deliberate
`tls_handshake_timeout_ms = 500` budget, so a CLIENT that goes silent for
longer than one `recv_wait_timeout` mid-handshake is dropped ON PURPOSE — that
budget is what keeps a half-open peer from parking the worker with the belt
moving, and raising it to the IDF default took `POST /api/program/stop` from
0.25 s to 15.25 s. `request` therefore reads the guest console on failure and,
if the device logged that exact refusal, re-raises saying so.

That is ATTRIBUTION, not tolerance: nothing is retried, no assertion is
weakened, and a handshake that failed for any other reason still surfaces
unchanged. See bead precor-9_3x-9aj.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


def tls_context() -> ssl.SSLContext:
    """A context that completes the handshake but checks no names or chains."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# The device's own words for "I dropped your handshake because you went quiet".
# `esp_tls_create_server_session failed, 0xffff7ff7` is -0x8009, i.e.
# -ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT (32777).
_HANDSHAKE_REFUSED = "esp_tls_create_server_session failed, 0xffff7ff7"


def request(sess, method: str, path: str, body=None, timeout: float = 20):
    """Issue one HTTPS request against the guest, returning (status, json)."""
    url = f"https://127.0.0.1:{sess.http_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=tls_context()) as r:
            return r.status, json.loads(r.read().decode())
    except ssl.SSLError as e:
        raise _attributed(sess, e) from e
    except urllib.error.URLError as e:
        # urllib wraps the SSL error; an HTTPError is a real ANSWER and must
        # pass through untouched (callers treat 4xx/5xx as data).
        if isinstance(e, urllib.error.HTTPError):
            raise
        raise _attributed(sess, e) from e


def _attributed(sess, exc: BaseException) -> BaseException:
    """Return the exception to raise, naming the device's refusal if that is
    what happened. Returns `exc` unchanged otherwise."""
    try:
        lines = sess.lines()
    except Exception:  # noqa: BLE001 — a session we cannot read tells us nothing
        return exc
    if not any(_HANDSHAKE_REFUSED in ln for ln in lines):
        return exc
    return AssertionError(
        f"the DEVICE refused this TLS handshake: {exc!r}\n"
        "  It logged `esp_tls_create_server_session failed, 0xffff7ff7` = "
        "-ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT.\n"
        "  That is the deliberate `tls_handshake_timeout_ms = 500` budget in "
        "net/http.rs firing, which grants a peer exactly ONE "
        "`recv_wait_timeout` of silence mid-handshake. The usual cause under "
        "QEMU is the PYTHON CLIENT being descheduled on a loaded host, not a "
        "device fault — see bead precor-9_3x-9aj.\n"
        "  DO NOT raise that budget to make this pass: at the IDF default it "
        "took `POST /api/program/stop` from 0.25 s to 15.25 s with the belt "
        "moving."
    )


def peer_certificate(sess, timeout: float = 20) -> bytes:
    """Complete a handshake and return the server's certificate in DER.

    Used to prove the identity is the device's own and that it is STABLE — the
    same bytes on a later connection means a client could have pinned it.
    """
    import socket

    with socket.create_connection(("127.0.0.1", sess.http_port), timeout=timeout) as raw:
        with tls_context().wrap_socket(raw, server_hostname="esp32tap") as tls:
            return tls.getpeercert(binary_form=True)
