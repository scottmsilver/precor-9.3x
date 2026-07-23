#!/usr/bin/env python3
"""Thin, read-first client for the JLCPCB OpenAPI (open.jlcpcb.com).

Credentials are NEVER hardcoded: they load from a gitignored key file
(default: tools/.jlc_key, KEY=VALUE lines) or JLC_* env vars. Signing is
HMAC-SHA256 over "METHOD\\ncanonical-uri\\ntimestamp\\nnonce\\nbody\\n" per the
JLCPCB OpenAPI scheme, sent in a JOP Authorization header.

Read-only subcommands (safe, no orders, no charges):
  stock C123 C456 ...   component assembly info by LCSC code
  bom   path/to/BOM.csv verify every LCSC Part # column entry

No order-placing subcommand exists here by design — ordering spends money and
must stay a deliberate, separately-reviewed step.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ENDPOINT = os.environ.get("JLC_ENDPOINT", "https://open.jlcpcb.com")
KEYFILE = os.path.join(os.path.dirname(__file__), ".jlc_key")
_NONCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def load_creds() -> tuple[str, str, str]:
    vals: dict[str, str] = {}
    if os.path.exists(KEYFILE):
        with open(KEYFILE) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    access = os.environ.get("JLC_ACCESS_KEY") or vals.get("JLC_ACCESS_KEY", "")
    secret = os.environ.get("JLC_SECRET_KEY") or vals.get("JLC_SECRET_KEY", "")
    # JLC's OpenAPI wants an appid; many accounts use the access key as appid.
    app = os.environ.get("JLC_APP_ID") or vals.get("JLC_APP_ID") or access
    if not access or not secret:
        sys.exit(f"missing JLC_ACCESS_KEY / JLC_SECRET_KEY (looked in env and {KEYFILE})")
    return app, access, secret


def auth_header(app: str, access: str, secret: str, method: str, url: str, body: str) -> str:
    nonce = "".join(secrets.choice(_NONCE) for _ in range(32))
    ts = int(time.time())
    split = urlsplit(url)
    uri = split.path + (f"?{split.query}" if split.query else "")
    sts = f"{method.upper()}\n{uri}\n{ts}\n{nonce}\n{body}\n"
    sig = base64.b64encode(hmac.new(secret.encode(), sts.encode(), hashlib.sha256).digest()).decode()
    return f'JOP appid="{app}",accesskey="{access}",' f'timestamp="{ts}",nonce="{nonce}",signature="{sig}"'


def post(uri: str, payload: dict) -> dict:
    app, access, secret = load_creds()
    url = f"{ENDPOINT.rstrip('/')}{uri}"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    req = urllib.request.Request(
        url,
        data=body.encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": auth_header(app, access, secret, "POST", url, body),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:800]}
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def cmd_stock(codes: list[str]) -> None:
    r = post("/overseas/openapi/component/getComponentDetailByCode", {"componentCodes": codes})
    print(json.dumps(r, indent=1, ensure_ascii=False))


def cmd_bom(path: str) -> None:
    codes = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            c = (row.get("LCSC Part #") or row.get("LCSC") or "").strip()
            if c:
                codes.append(c)
    print(f"{len(codes)} LCSC codes from {path}: {codes}", file=sys.stderr)
    r = post("/overseas/openapi/component/getComponentDetailByCode", {"componentCodes": codes})
    print(json.dumps(r, indent=1, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stock")
    s.add_argument("codes", nargs="+")
    b = sub.add_parser("bom")
    b.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "stock":
        cmd_stock(a.codes)
    elif a.cmd == "bom":
        cmd_bom(a.path)


if __name__ == "__main__":
    main()
