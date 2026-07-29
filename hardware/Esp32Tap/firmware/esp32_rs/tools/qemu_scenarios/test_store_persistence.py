"""The persistence tier's only claim that matters: it survives a reboot.

Written against the STORE, not the HTTP API, because a round-trip through
endpoints would prove the endpoints work — not that anything reached flash.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))
from conftest import *  # noqa: F401,F403,E402


def test_records_survive_a_reboot(qemu):
    s = qemu()
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120)

    # Empty to begin with — otherwise a stale image could fake a pass.
    line = s.cmd_ok("QT store_stat")
    assert "history=0" in line, line

    for i in range(3):
        line = s.cmd_ok(f"QT store_put run-{i}")
        assert "QTOK store_put" in line, line

    line = s.cmd_ok("QT store_stat")
    assert "history=3" in line, line

    # REBOOT. Same flash image, fresh RAM: anything that survives came from
    # flash, and the index was rebuilt by scanning rather than remembered.
    # since_line is NOT optional here: wait_log searches from the start of the
    # capture, so without it both waits match the FIRST boot's banners and
    # return instantly — the test would never actually wait for the reboot.
    before = s.line_count()
    s.cmd("QT reboot")
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120, since_line=before)
    # And the SHIM comes up after the safety banner; it is what answers QT
    # commands, so waiting only for the safety banner sends the next command
    # into a device with nothing listening yet.
    s.wait_log(r"qemu_test task started", timeout=60, since_line=before)

    line = s.cmd_ok("QT store_stat")
    assert "history=3" in line, f"records did not survive the reboot: {line}"

    # Newest first, by SEQUENCE — position in flash says nothing about age.
    line = s.cmd_ok("QT store_get 0")
    assert "body=run-2" in line, line
    line = s.cmd_ok("QT store_get 2")
    assert "body=run-0" in line, line
    line = s.cmd_ok("QT store_get 3")
    assert "absent" in line, line


def test_resident_memory_does_not_grow_with_stored_volume(qemu):
    s = qemu()
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120)

    def resident():
        line = s.cmd_ok("QT store_stat")
        return int(line.split("resident=")[1].split()[0])

    before = resident()
    for i in range(12):
        s.cmd_ok(f"QT store_put filler-{i}")
    after = resident()
    # This is the property whose absence let ~15 requests reboot the C++ tier.
    assert before == after, f"resident memory grew with stored volume: {before} -> {after}"
