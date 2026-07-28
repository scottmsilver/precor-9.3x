"""Slice 3: the surface the Android app needs to get past ProfilePickerScreen.

Asserts the shapes the Kotlin models actually decode, not merely that the
endpoints answer 200.
"""
import json, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))
from conftest import *  # noqa: F401,F403,E402


def http(sess, method, path, body=None):
    url = f"http://127.0.0.1:{sess.http_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def test_app_can_get_past_the_profile_picker(qemu):
    s = qemu(net=True)
    s.wait_log(r"http server up on :8000", timeout=120)

    # The picker lists profiles.
    st, profiles = http(s, "GET", "/api/profiles")
    assert st == 200, st
    assert isinstance(profiles, list) and profiles, profiles
    p = profiles[0]
    # Every field the Kotlin Profile model renders must be present and typed
    # as it expects — defaults exist, but a wrong TYPE would still break it.
    assert isinstance(p["id"], str) and p["id"]
    assert isinstance(p["name"], str) and p["name"]
    assert isinstance(p["initials"], str)
    assert p["color"].startswith("#")
    assert isinstance(p["weight_lbs"], (int, float))
    assert isinstance(p["vest_lbs"], (int, float))
    assert isinstance(p["has_avatar"], bool)

    # It asks which one is active.
    st, active = http(s, "GET", "/api/profile/active")
    assert st == 200, st
    assert active["guest_mode"] is False
    assert active["profile"]["id"] == p["id"], active

    # Selecting it is what unblocks the Lobby.
    st, sel = http(s, "POST", "/api/profile/select", {"id": p["id"]})
    assert st == 200, st
    assert sel["ok"] is True, sel
    assert sel["profile"]["id"] == p["id"], sel

    # ...and the device is still controllable afterwards.
    st, status = http(s, "GET", "/api/status")
    assert st == 200 and status["mode"] == "proxy", status
