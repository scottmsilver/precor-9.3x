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


def _try(s, method, path, body=None):
    """Like `http`, but returns the status even when it is an error."""
    import json as _json
    import urllib.error

    try:
        return http(s, method, path, body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, _json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def test_every_button_on_the_picker_gets_an_answer_it_can_render(qemu):
    """No raw HTTP status may reach the app's FIRST screen.

    `ProfilePickerScreen` builds its list as `profiles + Guest + AddProfile`
    UNCONDITIONALLY, so both buttons are always on screen. MEASURED before this:
    `POST /api/profile/guest` -> 404 and `POST /api/profiles` -> 405, and the
    picker rendered `HTTP 404 Not Found` in red above the avatars because
    `startGuest`'s `onError` receives `it.message`. `startGuest`,
    `deleteProfile`, `uploadAvatar` and `deleteAvatar` all check `it.ok` and
    render `it.error`, so an honest 200 body is READ ALOUD where a status code
    was not.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

    for method, path in (
        ("POST", "/api/profile/guest"),
        ("DELETE", "/api/profiles/local"),
        ("POST", "/api/profiles/local/avatar"),
        ("DELETE", "/api/profiles/local/avatar"),
    ):
        st, body = _try(s, method, path)
        assert st == 200, (method, path, st, body)
        assert body["ok"] is False, (method, path, body)
        # A SENTENCE, not a status line: this is what the user reads.
        assert isinstance(body["error"], str) and len(body["error"]) > 10, (method, path, body)

    # `createProfile` and `convertGuest` are typed `Profile` by the app and have
    # no error channel at all, so they answer with the profile that exists.
    for path in ("/api/profiles", "/api/profile/guest/convert"):
        st, body = _try(s, "POST", path, {"name": "Someone"})
        assert st == 200, (path, st, body)
        assert body["id"] == "local" and isinstance(body["name"], str), (path, body)

    # ...and none of it created a second profile.
    st, profiles = http(s, "GET", "/api/profiles")
    assert len(profiles) == 1, profiles


def test_the_settings_sheet_can_read_and_write_the_weight(qemu):
    """`/api/user` is the SAME profile, and its numbers are INTEGERS.

    `SettingsSheet.kt` opens with `api.getUser()` inside a `runCatching`, so the
    404 this used to return was swallowed and both fields rendered EMPTY; saving
    went to `api.updateUser(...)`, also 404, also swallowed. `weight_grams()`
    feeds the ACSM accumulator for every run, so every stored calorie figure was
    computed for 154 lb with no way to correct it from the app.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)

    st, u = _try(s, "GET", "/api/user")
    assert st == 200, (st, u)
    # `UserProfile` declares these `Int` with NO lenient serializer: `154.0`
    # fails to decode there, while `Profile` declares them `Double`. Two
    # endpoints, two types.
    assert isinstance(u["weight_lbs"], int) and not isinstance(u["weight_lbs"], bool), u
    assert isinstance(u["vest_lbs"], int) and not isinstance(u["vest_lbs"], bool), u
    assert u["weight_lbs"] == 154, u

    st, u = _try(s, "PUT", "/api/user", {"weight_lbs": 205, "vest_lbs": 12})
    assert st == 200 and u["weight_lbs"] == 205 and u["vest_lbs"] == 12, u

    # It is ONE profile, so the other endpoint agrees — and there it is a
    # Double, because that model declares it one.
    st, profiles = http(s, "GET", "/api/profiles")
    assert profiles[0]["weight_lbs"] == 205.0, profiles[0]

    # ...and it survives a reboot, which is the only reason the number is worth
    # anything to the calorie maths.
    before = s.line_count()
    s.cmd("QT reboot")
    s.wait_log(r"https server up on :8000", timeout=180, since_line=before)
    st, u = _try(s, "GET", "/api/user")
    assert u["weight_lbs"] == 205 and u["vest_lbs"] == 12, u


def test_an_empty_initials_string_does_not_erase_the_initials(qemu):
    """`name` and `color` were guarded and `initials` was not — inside one function.

    The app's avatar fallback renders from initials, and the cleared value was
    persisted to NVS and re-emitted by every profile endpoint.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    st, before = http(s, "GET", "/api/profiles")
    initials = before[0]["initials"]
    assert initials, before[0]

    st, body = _try(s, "PUT", "/api/profiles/local", {"initials": ""})
    assert st == 200, (st, body)
    st, after = http(s, "GET", "/api/profiles")
    assert after[0]["initials"] == initials, (before[0], after[0])

    # A real change still applies.
    st, body = _try(s, "PUT", "/api/profiles/local", {"initials": "ZZ"})
    assert st == 200 and body["profile"]["initials"] == "ZZ", body


def test_a_key_inside_a_value_does_not_set_the_weight(qemu):
    """The number scanner is anchored as a JSON member, not a substring search.

    `{"note":"weight_lbs","x":500}` used to set the weight to 500: the key
    matched inside a VALUE and the scanner then took "the next colon anywhere".
    That weight feeds the ACSM accumulator for every run.
    """
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    st, u = _try(s, "GET", "/api/user")
    before = u["weight_lbs"]

    for body in (
        {"note": "weight_lbs", "x": 500},
        {"weight_lbs_extra": 400},
        {"xweight_lbs": 399},
    ):
        st, u = _try(s, "PUT", "/api/user", body)
        assert st == 200, (body, st, u)
        assert u["weight_lbs"] == before, (body, u)

    # The real member still works, spaces and all.
    st, u = _try(s, "PUT", "/api/user", {"weight_lbs": 199})
    assert u["weight_lbs"] == 199, u
