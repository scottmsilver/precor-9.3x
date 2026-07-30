"""Slice 5 proof: the device keeps its own data, and keeping it costs nothing.

What is proven here, and only here — `program_core`'s host tests prove the
codec and the wire shapes in 0.00 s, and `recstore`'s prove the ring; none of
them can prove that a record written through an HTTP endpoint reaches real
flash, survives a real SoC reset, and does so without the memory growing.

  * A saved workout survives a REBOOT, read back through the same endpoint.
  * History honours its cap of 20 and its dedup-by-name.
  * A run record is CREATED once a session passes 5 s, CHECKPOINTED in the same
    slot (so 30-second checkpoints cannot evict the other runs), and FINALISED
    with the real end reason.
  * Resident memory is UNCHANGED after hundreds of writes — the property whose
    absence let ~15 requests exhaust the C++ tier's heap and reboot it mid-run.
  * A profile rename survives a reboot, which is what makes offering rename
    honest at all.
  * The JSON is the shape the Kotlin models decode — asserted by TYPE, because
    every field there has a default so an omission passes silently while a
    wrong type breaks the screen.

WHY THERE IS NO `time.sleep(n)` STANDING IN FOR GUEST TIME: guest time under
QEMU lags wall time, badly and variably under xdist. Every wait below is on a
guest-observed fact — a log line, or a value read back from the device.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qemu_harness"))
import httpc  # noqa: E402
import synth  # noqa: E402
from conftest import *  # noqa: F401,F403,E402

PACER_INTERVAL = 0.10


def http(s, method, path, body=None, timeout=30):
    """`httpc.request`, but a 4xx/5xx is a RESULT rather than an exception —
    half of what this file proves is that the device REFUSES things."""
    try:
        return httpc.request(s, method, path, body, timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def program(name, speed=2.0, incline=0.0, duration=600):
    return {
        "name": name,
        "intervals": [{"name": "Seg 1", "duration": duration, "speed": speed, "incline": incline}],
    }


def booted(qemu):
    s = qemu(net=True)
    s.wait_log(r"https server up on :8000", timeout=180)
    return s


def armed(qemu):
    """Booted, with the HARDWARE preconditions for emulate entry met, so the
    belt can actually be commanded and a session can actually happen."""
    s = booted(qemu)
    s.cmd_ok("QT tread 1")
    s.cmd_ok("QT k1 auto")
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    return s


def poll(fn, predicate, what, timeout=120.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if predicate(last):
            return last
        time.sleep(0.3)
    raise AssertionError(f"never observed {what}; last was {last!r}")


# ---------------------------------------------------------------------------


def test_the_lists_start_empty_and_are_json_arrays(qemu):
    s = booted(qemu)
    for path in ("/api/programs/history", "/api/workouts", "/api/runs"):
        st, body = http(s, "GET", path)
        assert st == 200, (path, st, body)
        # A BARE ARRAY, not an envelope: the Kotlin API types these as
        # `List<T>`, and an object here fails to decode outright.
        assert isinstance(body, list), (path, body)
        assert body == [], (path, body)


def test_loading_a_program_writes_history_with_the_shape_the_app_decodes(qemu):
    s = booted(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Hill Repeats"))
    assert st == 200

    st, hist = http(s, "GET", "/api/programs/history")
    assert st == 200 and len(hist) == 1, hist
    e = hist[0]
    # TYPES, not merely presence — see the module header.
    assert isinstance(e["id"], str) and e["id"], e
    assert isinstance(e["prompt"], str), e
    assert isinstance(e["program"], dict), e
    # `Program.name` and `Program.intervals` have NO kotlinx default: dropping
    # either throws MissingFieldException rather than degrading.
    assert e["program"]["name"] == "Hill Repeats", e
    assert isinstance(e["program"]["intervals"], list) and e["program"]["intervals"], e
    assert isinstance(e["program"]["manual"], bool), e
    assert isinstance(e["created_at"], str), e
    assert isinstance(e["total_duration"], int) and e["total_duration"] == 600, e
    assert e["completed"] is False, e
    assert isinstance(e["last_interval"], int), e
    assert isinstance(e["last_elapsed"], (int, float)), e
    assert e["saved"] is False and e["saved_workout_id"] is None, e
    assert e["last_run"] is None, e
    assert isinstance(e["last_run_text"], str), e


def test_history_dedups_by_name_and_holds_its_cap_of_twenty(qemu):
    s = booted(qemu)

    # The SAME name three times is ONE entry — `db.add_to_history` deletes the
    # previous row of that name before inserting.
    for speed in (2.0, 3.0, 4.0):
        st, _ = http(s, "POST", "/api/program/load", program("Same Name", speed=speed))
        assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 1, [h["program"]["name"] for h in hist]
    assert hist[0]["program"]["intervals"][0]["speed"] == 4.0, hist[0]

    # 25 DIFFERENT names is 20 entries, and it is the OLDEST that is lost.
    for i in range(25):
        st, _ = http(s, "POST", "/api/program/load", program(f"W{i:02d}"))
        assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 20, len(hist)
    names = [h["program"]["name"] for h in hist]
    assert names[0] == "W24", names
    assert "W04" not in names and "Same Name" not in names, names
    # Newest first.
    assert names == [f"W{i:02d}" for i in range(24, 4, -1)], names


def test_a_saved_workout_survives_a_reboot(qemu):
    s = booted(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Tempo 20"))
    assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]

    st, body = http(s, "POST", "/api/workouts", {"history_id": hid})
    assert st == 200 and body["ok"] is True, body
    w = body["workout"]
    assert w["name"] == "Tempo 20", w
    assert isinstance(w["program"], dict) and w["program"]["name"] == "Tempo 20", w
    assert w["times_used"] == 0 and w["last_used"] is None, w
    assert w["total_duration"] == 600, w
    assert w["source"] in ("manual", "generated", "gpx"), w
    assert isinstance(w["usage_text"], str), w
    wid = w["id"]

    # Saving it back-fills the history entry's heart icon.
    st, hist = http(s, "GET", "/api/programs/history")
    assert hist[0]["saved"] is True and hist[0]["saved_workout_id"] == wid, hist[0]

    # REBOOT. Same flash image, fresh RAM: anything that comes back came from
    # flash and its index was rebuilt by scanning.
    before = s.line_count()
    s.cmd("QT reboot")
    s.wait_log(r"https server up on :8000", timeout=180, since_line=before)

    st, workouts = http(s, "GET", "/api/workouts")
    assert st == 200 and len(workouts) == 1, workouts
    assert workouts[0]["id"] == wid, workouts[0]
    assert workouts[0]["program"]["name"] == "Tempo 20", workouts[0]
    assert workouts[0]["program"]["intervals"][0]["duration"] == 600, workouts[0]
    # ...and so did the history entry that produced it.
    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 1 and hist[0]["saved"] is True, hist


def test_a_workout_can_be_renamed_deleted_and_loaded(qemu):
    s = booted(qemu)
    st, body = http(s, "POST", "/api/workouts", {"program": program("Original"), "source": "manual"})
    assert st == 200 and body["ok"] is True, body
    wid = body["workout"]["id"]

    # RENAME rewrites the name INSIDE the stored program too — on the Pi those
    # are two columns and a desync is possible; here there is one name.
    st, body = http(s, "PUT", f"/api/workouts/{wid}", {"name": "Renamed"})
    assert st == 200 and body["ok"] is True, body
    assert body["workout"]["name"] == "Renamed", body
    assert body["workout"]["program"]["name"] == "Renamed", body
    st, workouts = http(s, "GET", "/api/workouts")
    assert workouts[0]["name"] == "Renamed" and workouts[0]["program"]["name"] == "Renamed"

    # An empty name is refused rather than stored.
    st, body = http(s, "PUT", f"/api/workouts/{wid}", {"name": ""})
    assert st == 400, (st, body)

    # LOAD installs it, counts the use, and writes a history entry — all three
    # are what `server.py::api_load_workout` does.
    st, body = http(s, "POST", f"/api/workouts/{wid}/load")
    assert st == 200 and body["ok"] is True, body
    assert body["program"]["name"] == "Renamed", body
    st, prog = http(s, "GET", "/api/program")
    assert prog["program"]["name"] == "Renamed", prog
    st, workouts = http(s, "GET", "/api/workouts")
    assert workouts[0]["times_used"] == 1, workouts[0]
    assert workouts[0]["usage_text"] == "Used once", workouts[0]
    st, hist = http(s, "GET", "/api/programs/history")
    assert [h["program"]["name"] for h in hist] == ["Renamed"], hist

    # DELETE removes it, and the history entry stops claiming it is saved.
    st, body = http(s, "DELETE", f"/api/workouts/{wid}")
    assert st == 200 and body["ok"] is True, body
    st, workouts = http(s, "GET", "/api/workouts")
    assert workouts == [], workouts
    st, hist = http(s, "GET", "/api/programs/history")
    assert hist[0]["saved"] is False and hist[0]["saved_workout_id"] is None, hist[0]

    # A second delete is `ok:false`, not a crash and not a 500.
    st, body = http(s, "DELETE", f"/api/workouts/{wid}")
    assert st == 200 and body["ok"] is False, (st, body)


def test_an_unknown_or_oversized_id_is_refused_without_touching_the_store(qemu):
    s = booted(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Keep Me"))
    assert st == 200

    st, body = http(s, "POST", "/api/programs/history/nope/load")
    assert st == 200 and body["ok"] is False, (st, body)
    # An id longer than a record id is REFUSED, never truncated: a truncated id
    # would silently address a DIFFERENT record.
    st, body = http(s, "DELETE", "/api/workouts/" + "x" * 64)
    assert st == 404, (st, body)
    st, body = http(s, "PUT", "/api/workouts/" + "x" * 64, {"name": "n"})
    assert st == 404, (st, body)

    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 1 and hist[0]["program"]["name"] == "Keep Me", hist


def test_resume_is_gated_by_completion_and_picks_up_where_it_stopped(qemu):
    s = armed(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Resumable"))
    assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]

    st, body = http(s, "POST", f"/api/programs/history/{hid}/load")
    assert st == 200 and body["ok"] is True, body
    assert body["program"]["name"] == "Resumable", body

    st, body = http(s, "POST", f"/api/programs/history/{hid}/resume")
    assert st == 200 and body["ok"] is True, body
    assert body["running"] is True, body
    # Stop again so the belt is not left owned by a program.
    http(s, "POST", "/api/program/stop")


def test_history_resume_rolls_back_on_safety_refusal_and_can_be_retried(qemu):
    s = armed(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Transactional Resume"))
    assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]

    s.cmd_ok("QT tread 0")
    st, body = http(s, "POST", f"/api/programs/history/{hid}/resume")
    assert st == 409 and body["ok"] is False, body
    st, state = http(s, "GET", "/api/program")
    assert st == 200 and state["running"] is False and state["paused"] is False, state

    # Restored manual control proves the failed transaction did not strand an
    # executor lease. Stop releases that manual owner, then a fresh explicit
    # history Resume is allowed to recover and commit.
    s.cmd_ok("QT tread 1")
    st, body = http(s, "POST", "/api/speed", {"value": 2.0})
    assert st == 200 and body["ok"] is True, body
    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200 and body["running"] is False, body

    st, body = http(s, "POST", f"/api/programs/history/{hid}/resume")
    assert st == 200 and body["ok"] is True and body["running"] is True, body
    http(s, "POST", "/api/program/stop")
    s.stop_pacer()


def test_a_run_is_created_checkpointed_in_place_and_finalised(qemu):
    s = armed(qemu)

    def runs():
        st, body = http(s, "GET", "/api/runs")
        assert st == 200, body
        return body

    assert runs() == []

    # A PROGRAM, not a bare `POST /api/speed`. A manual command holds the belt
    # only for its lease: the safety controller exits emulate a couple of
    # seconds after the last command and the belt goes to zero, so a session
    # driven that way is over before it is five seconds old. The interval
    # executor re-commands every tick, which is what a real run looks like.
    st, body = http(s, "POST", "/api/program/start", program("Long Run", speed=3.0, duration=3600))
    assert st == 200 and body["running"] is True, body

    # CREATED once the session passes 5 s — `server.py` writes no record for a
    # shorter one either.
    rec = poll(runs, lambda r: len(r) == 1, "a run record to appear")[0]
    assert rec["end_reason"] == "in_progress", rec
    assert rec["started_at"] is None and rec["ended_at"] is None, rec
    assert isinstance(rec["elapsed"], (int, float)) and rec["elapsed"] >= 5, rec
    assert isinstance(rec["distance"], (int, float)), rec
    assert isinstance(rec["vert_feet"], (int, float)), rec
    assert isinstance(rec["calories"], (int, float)), rec
    assert rec["program_completed"] is False and isinstance(rec["is_manual"], bool), rec
    rid = rec["id"]

    # CHECKPOINTED IN PLACE. Past the 30 s checkpoint the record must have
    # grown and there must still be exactly ONE of it — appending instead would
    # empty this 4-slot ring in two minutes and evict every earlier run.
    later = poll(
        runs,
        lambda r: len(r) == 1 and r[0]["elapsed"] >= 31,
        "the run to pass its first 30 s checkpoint",
        timeout=180,
    )[0]
    assert later["id"] == rid, (rec, later)
    assert later["end_reason"] == "in_progress", later
    assert later["distance"] > 0, later
    assert later["calories"] > 0, later

    # FINALISED with the real reason when the program stops.
    st, body = http(s, "POST", "/api/program/stop")
    assert st == 200, body
    final = poll(runs, lambda r: r and r[0]["end_reason"] != "in_progress", "the run to finalise")[0]
    assert final["id"] == rid, final
    assert final["end_reason"] == "user_stop", final
    assert final["elapsed"] >= later["elapsed"], (later, final)

    # ...and it survives a reboot, like everything else in this tier.
    #
    # THE PACER IS STOPPED FIRST, and that is a statement about this test
    # image rather than a convenience. Under `qemu-test` the motor tap is
    # remapped onto UART0 because the pinned esp-QEMU cannot wire UART2 — so
    # the pacer's console bytes arrive on the SAME port the bootloader and
    # ESP_LOG use. Rebooting into that stream is a collision the production
    # wiring (console on UART1, log on UART0) does not have, and it hung the
    # guest between `Calling app_main()` and the safety banner under load.
    # What this test is about is a run record surviving a power cycle; driving
    # a test-only port aliasing through the boot is a different claim, and one
    # this image cannot make faithfully.
    s.stop_pacer()
    before = s.line_count()
    s.cmd("QT reboot")
    s.wait_log(r"https server up on :8000", timeout=180, since_line=before)
    after = runs()
    assert len(after) == 1 and after[0]["id"] == rid, after
    assert after[0]["end_reason"] == "user_stop", after[0]


def test_a_completed_program_is_recorded_as_program_complete(qemu):
    """The end reason is the REAL one, not a default.

    A run that ends because the workout finished must not read `user_stop` —
    the app distinguishes them, and `program_completed` is also what gates
    `/api/programs/history/{id}/resume`.
    """
    s = armed(qemu)

    def runs():
        st, body = http(s, "GET", "/api/runs")
        assert st == 200, body
        return body

    # Two 10 s intervals: `MIN_DURATION` is 10, so this is the shortest
    # program the device will accept, and it outlives the 5 s floor.
    short = {
        "name": "Short One",
        "intervals": [
            {"name": "A", "duration": 10, "speed": 2.0, "incline": 0},
            {"name": "B", "duration": 10, "speed": 3.0, "incline": 1.0},
        ],
    }
    st, body = http(s, "POST", "/api/program/start", short)
    assert st == 200 and body["running"] is True, body

    final = poll(
        runs,
        lambda r: r and r[0]["end_reason"] != "in_progress",
        "the run to finalise",
        timeout=180,
    )[0]
    assert final["end_reason"] == "program_complete", final
    assert final["program_completed"] is True, final
    assert final["program_name"] == "Short One", final
    assert final["elapsed"] >= 5, final

    # ...and the history entry it came from is marked completed, which is what
    # makes `/resume` refuse it.
    st, hist = http(s, "GET", "/api/programs/history")
    assert hist[0]["completed"] is True, hist[0]
    hid = hist[0]["id"]
    st, body = http(s, "POST", f"/api/programs/history/{hid}/resume")
    assert st == 200 and body["ok"] is False, (st, body)
    assert "completed" in body["error"], body


def test_resident_memory_does_not_grow_with_writes_or_with_requests(qemu):
    s = booted(qemu)

    def resident():
        return int(s.cmd_ok("QT store_stat").split("resident=")[1].split()[0])

    def heap():
        line = s.cmd_ok("QT heap")
        return int(line.split("free=")[1].split()[0])

    r0 = resident()
    # Warm the request path first: the FIRST TLS session allocates buffers that
    # are then reused, so measuring from a cold heap would report that
    # one-time cost as growth.
    for i in range(5):
        http(s, "POST", "/api/program/load", program(f"warm{i}"))
    h0 = heap()

    # Hundreds of writes: 240 loads over 30 names, so every record is
    # rewritten many times and the ring is evicted through repeatedly.
    for i in range(240):
        st, _ = http(s, "POST", "/api/program/load", program(f"Prog{i % 30:02d}"))
        assert st == 200

    r1 = resident()
    h1 = heap()
    assert r0 == r1, f"resident store memory grew with stored volume: {r0} -> {r1}"
    st, hist = http(s, "GET", "/api/programs/history")
    assert len(hist) == 20, len(hist)

    # THE PROPERTY THE C++ TIER LACKED. A tolerance, not an equality: IDF's own
    # allocator moves a little around a TLS session. Growth proportional to 240
    # requests would be tens of KB.
    assert h1 >= h0 - 8192, f"heap fell {h0 - h1} bytes across 240 writes ({h0} -> {h1})"

    # ...and reading the whole store back is bounded too: the list response is
    # chunked, so a 20-entry body never exists in memory at once.
    h2 = heap()
    for _ in range(10):
        st, body = http(s, "GET", "/api/programs/history")
        assert st == 200 and len(body) == 20
    h3 = heap()
    assert h3 >= h2 - 4096, f"heap fell {h2 - h3} bytes across 10 list reads"


def test_a_profile_rename_survives_a_reboot(qemu):
    s = booted(qemu)
    st, active = http(s, "GET", "/api/profile/active")
    assert st == 200 and active["profile"]["name"] == "Runner", active
    pid = active["profile"]["id"]

    st, body = http(s, "PUT", f"/api/profiles/{pid}", {"name": "Scott", "initials": "S", "weight_lbs": 180})
    assert st == 200 and body["ok"] is True, body
    assert body["profile"]["name"] == "Scott", body
    assert body["profile"]["weight_lbs"] == 180.0, body

    before = s.line_count()
    s.cmd("QT reboot")
    s.wait_log(r"https server up on :8000", timeout=180, since_line=before)

    st, profiles = http(s, "GET", "/api/profiles")
    assert st == 200 and len(profiles) == 1, profiles
    p = profiles[0]
    assert p["name"] == "Scott", p
    assert p["initials"] == "S", p
    # The Kotlin model types these `Double`; the Pi stores whole pounds.
    assert isinstance(p["weight_lbs"], (int, float)) and p["weight_lbs"] == 180.0, p
    assert isinstance(p["vest_lbs"], (int, float)), p
    assert p["has_avatar"] is False, p
    assert p["color"].startswith("#"), p


def test_the_action_in_the_path_is_matched_exactly(qemu):
    """A wildcard route hands the handler EVERYTHING under its prefix.

    Treating "not `resume`" as "load" made `POST .../h1/delete` load a program,
    and ignoring the action on DELETE made `DELETE /api/workouts/w1/load`
    delete the workout. Both are reachable by any client on the LAN.
    """
    s = booted(qemu)
    st, _ = http(s, "POST", "/api/program/load", program("Guarded"))
    assert st == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]
    st, body = http(s, "POST", "/api/workouts", {"program": program("Keeper"), "source": "manual"})
    wid = body["workout"]["id"]

    for path in (
        f"/api/programs/history/{hid}/delete",
        f"/api/programs/history/{hid}/resume/extra",
        f"/api/workouts/{wid}/anything",
    ):
        st, body = http(s, "POST", path)
        assert st == 404, (path, st, body)

    # A verb that ignores its action would destroy the workout here.
    st, body = http(s, "DELETE", f"/api/workouts/{wid}/load")
    assert st == 404, (st, body)
    st, body = http(s, "PUT", f"/api/workouts/{wid}/load", {"name": "hijacked"})
    assert st == 404, (st, body)

    st, workouts = http(s, "GET", "/api/workouts")
    assert len(workouts) == 1 and workouts[0]["name"] == "Keeper", workouts

    # ...and the routes that ARE spelled correctly still work.
    st, body = http(s, "POST", f"/api/workouts/{wid}/load")
    assert st == 200 and body["ok"] is True, body


def test_a_profile_id_that_is_not_ours_is_refused(qemu):
    """`/api/profiles/*` matches any id; without a check, every one of them
    rewrote the single local profile and answered as though it had not."""
    s = booted(qemu)
    st, body = http(s, "PUT", "/api/profiles/someone-else", {"name": "Intruder"})
    assert st == 404, (st, body)
    st, profiles = http(s, "GET", "/api/profiles")
    assert profiles[0]["name"] == "Runner", profiles


def test_a_hostile_number_does_not_wrap_the_incline(qemu):
    """`{"value":21474836.47}` parses to i32::MAX; `* 2` on that wraps to a
    NEGATIVE incline in release and panics in debug — and a panic is a reboot,
    which drops the relay."""
    s = armed(qemu)
    st, body = http(s, "POST", "/api/incline", {"value": 21474836.47})
    assert st in (200, 409), (st, body)
    # Whatever the controller decided, the device must still be alive and the
    # incline must not be negative.
    st, status = http(s, "GET", "/api/status")
    assert st == 200, status
    assert status["incline"] >= 0, status
    assert status["emu_incline"] >= 0, status


def test_a_body_that_is_not_json_does_not_rename_a_workout(qemu):
    """The field scanner takes the first `"key":` in the body; without the
    colon requirement, `not-json "name" "evil"` renamed a workout."""
    s = booted(qemu)
    st, body = http(s, "POST", "/api/workouts", {"program": program("Original"), "source": "manual"})
    wid = body["workout"]["id"]
    import urllib.request

    url = f"https://127.0.0.1:{s.http_port}/api/workouts/{wid}"
    req = urllib.request.Request(url, data=b'not-json "name" "evil"', method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=20, context=httpc.tls_context()) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 400, code
    st, workouts = http(s, "GET", "/api/workouts")
    assert workouts[0]["name"] == "Original", workouts


def test_a_rename_keeps_the_saved_link_and_does_not_create_a_duplicate(qemu):
    """The heart icon must survive a rename — keying it on the NAME did not.

    MEASURED before the fix: save from history gave
    `saved:true, saved_workout_id:"w1"`; `PUT /api/workouts/w1 {"name":...}`
    gave `saved:false, saved_workout_id:null`; `HistoryList.kt` then drew an
    OUTLINE heart and `handleToggleSave` took its else branch and POSTed
    `/api/workouts` again — two saved workouts for one program, the older
    unreachable from the row that created it, and another on every rename.
    `python/server.py` keys this on `_program_fingerprint`, which ignores the
    name, so a rename cannot break it there either.
    """
    s = booted(qemu)
    assert http(s, "POST", "/api/program/load", program("Tempo"))[0] == 200
    st, hist = http(s, "GET", "/api/programs/history")
    hid = hist[0]["id"]

    st, body = http(s, "POST", "/api/workouts", {"history_id": hid})
    assert st == 200 and body["ok"] is True, body
    wid = body["workout"]["id"]
    st, hist = http(s, "GET", "/api/programs/history")
    assert hist[0]["saved"] is True and hist[0]["saved_workout_id"] == wid, hist[0]

    st, body = http(s, "PUT", f"/api/workouts/{wid}", {"name": "Tempo Redux"})
    assert st == 200 and body["workout"]["name"] == "Tempo Redux", body

    st, hist = http(s, "GET", "/api/programs/history")
    assert hist[0]["saved"] is True, (
        "a rename desynced the history row's heart icon; the app's next tap "
        f"would create a duplicate workout: {hist[0]}"
    )
    assert hist[0]["saved_workout_id"] == wid, hist[0]

    # ...and a DIFFERENT program is still not saved. The join must not have
    # become "anything matches".
    assert http(s, "POST", "/api/program/load", program("Other", speed=9.0))[0] == 200
    st, hist = http(s, "GET", "/api/programs/history")
    other = [h for h in hist if h["program"]["name"] == "Other"][0]
    assert other["saved"] is False and other["saved_workout_id"] is None, other


def test_a_finished_run_reaches_the_history_row_and_the_workout_that_saved_it(qemu):
    """`last_run` / `last_run_text` / `usage_text` — the app's only view of a run.

    `TreadmillApi` declares no `/api/runs` method at all, so the ONLY two places
    run data can reach a screen are `HistoryList.kt`'s
    `if (entry.lastRunText.isNotBlank())` and `WorkoutList.kt`'s
    `usageText.ifBlank { "Never used" }`. Both were fed `null`/`""`
    unconditionally, so a device that recorded every run perfectly could never
    show one.
    """
    s = armed(qemu)
    short = {
        "name": "Joinable",
        "intervals": [
            {"name": "A", "duration": 10, "speed": 2.0, "incline": 0},
            {"name": "B", "duration": 10, "speed": 3.0, "incline": 1.0},
        ],
    }
    st, body = http(s, "POST", "/api/program/start", short)
    assert st == 200 and body["running"] is True, body

    def runs():
        return http(s, "GET", "/api/runs")[1]

    final = poll(
        runs,
        lambda r: r and r[0]["end_reason"] != "in_progress",
        "the run to finalise",
        timeout=180,
    )[0]
    assert final["end_reason"] == "program_complete", final
    s.stop_pacer()

    st, hist = http(s, "GET", "/api/programs/history")
    e = [h for h in hist if h["program"]["name"] == "Joinable"][0]
    # OBJECT-shaped or null — `HistoryEntry.lastRun` is a `RunRecord?`, and a
    # scalar throws rather than degrading.
    assert isinstance(e["last_run"], dict), e
    assert e["last_run"]["id"] == final["id"], e
    assert e["last_run_text"].startswith("Last run: "), e

    # ...and the saved workout made from it says the same thing.
    st, body = http(s, "POST", "/api/workouts", {"history_id": e["id"]})
    assert st == 200 and body["ok"] is True, body
    st, workouts = http(s, "GET", "/api/workouts")
    w = workouts[0]
    assert isinstance(w["last_run"], dict) and w["last_run"]["id"] == final["id"], w
    assert w["usage_text"].startswith("Last run: "), w
    # A never-run program keeps the blank the app turns into "Never used".
    assert http(s, "POST", "/api/workouts", {"program": program("Unrun"), "source": "manual"})[0] == 200
    st, workouts = http(s, "GET", "/api/workouts")
    unrun = [x for x in workouts if x["name"] == "Unrun"][0]
    assert unrun["last_run"] is None and unrun["usage_text"] == "", unrun
    # An explicit source on the direct-program path is honoured...
    assert unrun["source"] == "manual", unrun
    # ...and a non-manual program saved FROM HISTORY infers "generated", as on
    # the Pi. It used to infer "manual" for everything, because the device's
    # inference carried an empty-prompt clause `server.py` does not have and
    # every program stored here has an empty prompt.
    st, hist = http(s, "GET", "/api/programs/history")
    gen = [h for h in hist if h["program"]["name"] == "Joinable"][0]
    st, body = http(s, "POST", "/api/workouts", {"history_id": gen["id"]})
    assert body["workout"]["source"] == "generated", body["workout"]
