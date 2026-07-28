"""Slice 3: the surface the Android app needs to get past ProfilePickerScreen.

Asserts the shapes the Kotlin models actually decode, not merely that the
endpoints answer 200.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

# HTTPS-only since Slice 3 — see httpc.py for why verification is off.
http = httpc.request


def test_app_can_get_past_the_profile_picker(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

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
