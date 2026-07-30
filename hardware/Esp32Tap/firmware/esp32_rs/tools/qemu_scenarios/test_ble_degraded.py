"""The device with NO WORKING RADIO is still a treadmill.

WHAT THIS FILE IS, AND WHAT IT IS NOT
=====================================

It is NOT proof that Bluetooth works. **QEMU has no BLE radio**, so nothing in
this repository can advertise, connect, pair, notify or indicate, and no test
here pretends to. Bead `precor-9_3x-l0h` names every item that stays unproven
until a board exists.

It IS proof of the property that actually keeps a treadmill safe when the radio
is not there — and that property was NOT free. MEASURED, on the first
BLE-enabled image:

    I (15990) BLE_INIT: BT controller compile version [b7de11e]
    I (15991) BLE_INIT: Using main XTAL as clock source
    assert failed: 0x4206ea5c <cached disabled>:1753
    Backtrace: ...
    Rebooting...

`nimble_port_init` does not return an error when the controller cannot come up.
It `assert()`s inside the closed-source BT blob, which is a panic, which under
this firmware's PLAN-normative `CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT` is an
immediate reset — **and a reset drops the relay mid-run**. The device
reboot-looped forever: every 1.8 seconds it booted, served HTTPS for a moment,
and died. So "the radio failing is survivable" could not be written as a
`match` on a return code; it had to become a guard in FRONT of the call
(`ble::identity_address`, which refuses to hand the controller a part whose
eFuse identity address is not a factory unicast MAC).

Everything below is what that guard buys, asserted rather than asserted-to.

A NOTE ON WHERE THE REAL COVERAGE IS
====================================

The BLE feature is carried by the WHOLE qemu-test image (`tools/build.sh`
builds it `--features qemu-test,net,ble`), so every one of the ~20 scenarios in
this directory already runs against a device whose radio failed to come up. If
the failing radio disturbed the belt, the store, TLS, mDNS or `/ws`, they would
all go red together. This file states the property explicitly and pins the
things that would otherwise only be implied.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
import wsc  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10

# The EXACT line `ble::run` prints when bring-up is refused or fails. Matched
# as a whole so a future edit that quietly drops the "unaffected" promise from
# the message has to come through here.
UNAVAILABLE = re.compile(r"ble: unavailable \(err -?\d+\) — HTTPS, /ws, the belt and the console are unaffected")
# The line it prints when the host DOES come up. Under QEMU it never should;
# if it ever does, this suite is no longer testing what it says it is and must
# fail loudly rather than pass quietly.
HOST_STARTED = re.compile(r"ble: nimble host started")


def http(s, method, path, body=None, timeout=20):
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def served(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    return s


def armed(qemu):
    """A device that is being fed console frames, so the belt can be commanded."""
    s = served(qemu)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


# ---------------------------------------------------------------------------
# The radio really is absent, and the device really did survive it
# ---------------------------------------------------------------------------


def test_the_radio_is_refused_and_the_device_does_not_reboot(qemu):
    s = served(qemu)
    # The refusal is reported. Waiting on it also proves `ble::run` ran at all:
    # the task is spawned last, after the HTTPS banner, on purpose.
    s.wait_log(UNAVAILABLE.pattern, timeout=60)

    # Give it well past the point where a reboot loop would show itself. The
    # measured loop period was ~1.8 s, so 12 s is ~6 iterations of it.
    time.sleep(12)
    log = "\n".join(s.lines())

    # THE HEADLINE ASSERTION. One boot, not many. `Rebooting...` is what the
    # panic handler prints on its way to the reset that drops the relay.
    assert "Rebooting..." not in log, "the device rebooted — the BLE guard did not hold"
    assert "assert failed:" not in log, "an assert() fired — the controller was entered"
    assert "Guru Meditation" not in log
    boots = log.count("main_task: Calling app_main()")
    assert boots == 1, f"expected a single boot, saw {boots}"

    # And the guard's reason is the honest one, not a silent skip.
    assert "ble: eFuse identity address is not a factory unicast MAC" in log

    # If NimBLE ever DOES come up under QEMU, this file is no longer testing
    # degradation and must say so rather than pass.
    assert not HOST_STARTED.search(log), (
        "the NimBLE host started under QEMU — this suite asserts the DEGRADED "
        "path and is now vacuous; re-read what it claims to prove"
    )


def test_the_https_tier_is_untouched(qemu):
    s = served(qemu)
    s.wait_log(UNAVAILABLE.pattern, timeout=60)

    # The banner, over a real TLS handshake, AFTER the radio failed.
    st, body = http(s, "GET", "/")
    assert st == 200, st
    assert body == {"service": "precor-treadmill", "api": "/api", "ws": "/ws"}, body

    st, body = http(s, "GET", "/api/status")
    assert st == 200, st
    # The status frame still carries every field the app requires — including
    # the three heart-rate ones, which a radioless device answers rather than
    # omits.
    for k in (
        "proxy",
        "emulate",
        "emu_speed",
        "emu_speed_mph",
        "emu_incline",
        "treadmill_connected",
        "heart_rate",
        "hrm_connected",
        "hrm_device",
    ):
        assert k in body, f"/api/status lost {k}: {body}"
    assert body["heart_rate"] == 0
    assert body["hrm_connected"] is False


def test_the_ws_push_is_untouched(qemu):
    s = armed(qemu)
    s.wait_log(UNAVAILABLE.pattern, timeout=60)
    with wsc.WsClient(s) as ws:
        frames = ws.collect(4.0)
    kinds = {f.get("type") for f in frames}
    # The three the Running screen is fed from, plus the hr frame this slice
    # added. A device with no radio still sends `hr` — otherwise the last live
    # bpm would freeze on screen forever after a strap walked away.
    assert "status" in kinds, kinds
    assert "session" in kinds, kinds
    assert "hr" in kinds, kinds

    hr = [f for f in frames if f.get("type") == "hr"][-1]
    # `bpm` is non-nullable with NO kotlinx default in `HRMessage`, so its
    # absence throws MissingFieldException in the client and kills the frame.
    assert "bpm" in hr and isinstance(hr["bpm"], int), hr
    assert hr["bpm"] == 0
    assert hr["connected"] is False


def test_the_belt_still_moves(qemu):
    """The point of the whole device, with the radio dead."""
    s = armed(qemu)
    s.wait_log(UNAVAILABLE.pattern, timeout=60)

    st, body = http(s, "POST", "/api/speed", {"value": 3.0})
    assert st == 200, (st, body)
    assert body.get("ok") is True, body
    assert body["emu_speed"] == 30, body

    # THE MODE IS ASSERTED AS "NOT PROXY", NOT AS "emulate". `emulate` is
    # `mode == Emulating`, and the entry choreography passes through
    # EntryWaitGap/EntryWaitFeedback first — so whether the reply to the POST
    # already says `emulate` depends on where in the emulate cycle the request
    # landed. This test caught itself on that: it passed standalone and failed
    # inside the sweep, which is the shape of an intermittent, and the fix is
    # the mechanism rather than a sleep. What this file is entitled to assert
    # is that the command REACHED THE BELT with the radio dead; the timing of
    # the transfer is `test_http_entry.py`'s subject and is asserted there.
    assert body["mode"] in ("entering", "emulate"), body

    st, body = http(s, "GET", "/api/status")
    assert st == 200
    assert body["emu_speed"] == 30, body

    # And it stops.
    st, body = http(s, "POST", "/api/speed", {"value": 0})
    assert st == 200, (st, body)
    assert body["emu_speed"] == 0, body


# ---------------------------------------------------------------------------
# `/api/hrm*` answers WITHOUT a radio — the Pi's contract with hrm-daemon down
# ---------------------------------------------------------------------------


def test_hrm_routes_answer_rather_than_404(qemu):
    s = served(qemu)
    st, body = http(s, "GET", "/api/hrm")
    assert st == 200, st
    # `HrmStatusResponse` in ApiModels.kt.
    assert body["heart_rate"] == 0, body
    assert body["connected"] is False, body
    assert body["device"] == "", body
    assert body["available_devices"] == [], body

    # The three the picker calls. A 404 on any of them is a dead settings
    # screen, which is what "no radio" must NOT look like to a client.
    for path in ("/api/hrm/forget", "/api/hrm/scan"):
        st, body = http(s, "POST", path)
        assert st == 200, (path, st, body)
        assert body.get("ok") is True, (path, body)

    # `select` refuses an address the device has never seen advertise, rather
    # than guessing an address type and connecting to a stranger.
    st, body = http(s, "POST", "/api/hrm/select", {"address": "AA:BB:CC:DD:EE:FF"})
    assert st == 404, (st, body)
    assert body.get("ok") is False, body


def test_a_malformed_address_is_rejected_and_nothing_wedges(qemu):
    """`POST /api/hrm/select` takes a string off the LAN. It must be total."""
    s = served(qemu)
    for bad in [
        {},
        {"address": ""},
        {"address": "AA:BB:CC:DD:EE"},
        {"address": "ZZ:BB:CC:DD:EE:FF"},
        {"address": "A" * 400},
        {"address": '"},{"x":1'},
        {"notaddress": "AA:BB:CC:DD:EE:FF"},
    ]:
        st, _ = http(s, "POST", "/api/hrm/select", bad)
        assert st in (400, 404, 413), (bad, st)

    # The surface is still alive afterwards — a rejected request costs nothing
    # permanent, which is the same claim `test_mem_review.py` makes of the
    # other endpoints.
    st, body = http(s, "GET", "/api/hrm")
    assert st == 200, st
    assert body["connected"] is False


# ---------------------------------------------------------------------------
# Memory — reported, not asserted at a made-up threshold
# ---------------------------------------------------------------------------


def test_heap_is_reported_and_does_not_drift_while_the_ble_task_runs(qemu):
    """The BLE task ticks forever after a refused bring-up. It must cost nothing.

    NO THRESHOLD IS INVENTED HERE for the size of the heap: what the flashed
    device has is a hardware fact and this suite is not the place to legislate
    it. What IS asserted is the property this slice could break — that the
    parked BLE task, and the `hr`/`status` renderers that now run once a second
    with it, do not leak. The absolute figures are PRINTED so the README's
    numbers come off a running image rather than an estimate.
    """
    s = served(qemu)
    s.wait_log(UNAVAILABLE.pattern, timeout=60)

    def heap():
        line = s.cmd_ok("QT heap")
        m = re.search(r"free=(\d+) minfree=(\d+) largest=(\d+)", line)
        assert m, line
        return tuple(int(x) for x in m.groups())

    first = heap()
    # Exercise the paths the BLE tier added, repeatedly.
    for _ in range(30):
        http(s, "GET", "/api/hrm")
        http(s, "GET", "/api/status")
    time.sleep(6)  # several BLE task ticks
    second = heap()

    print(f"heap after boot:      free={first[0]} minfree={first[1]} largest={first[2]}")
    print(f"heap after 60 reqs:   free={second[0]} minfree={second[1]} largest={second[2]}")

    # Converged, not merely non-fatal. A per-request or per-tick leak shows up
    # as a monotone slide; a few hundred bytes of allocator noise does not.
    assert second[0] >= first[0] - 4096, (first, second)
