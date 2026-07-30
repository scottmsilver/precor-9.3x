"""A POWER CUT IN THE MIDDLE OF A WRITE. The property the swap to LittleFS was
performed to get, measured on a real guest instead of argued from the
component's design.

WHY THIS FILE EXISTS. `recstore` — the hand-rolled flash store this tier
replaced — carried two tests that cut a write at every byte offset
(`torn_write_is_ignored_not_recovered`, `a_replace_torn_at_every_offset_never
_damages_a_neighbour`), and those two tests are what caught BOTH of its original
defects. Deleting the store deleted them, which left the persistence tier with
ZERO power-loss coverage and left `net/store.rs`'s central claim — "a power cut
leaves the slot at its PREVIOUS content — never half-written, never absent,
never ambiguous" — resting on littlefs's reputation. Adopting a filesystem is a
good reason to BELIEVE that claim; it is not a reason to stop testing it.

WHAT AN INTERRUPTION IS, HERE. `QT store_tear <point> <off> <n> <payload>`
replaces the nth-newest history record in place and resets the SoC inside
`write_slot`. `esp_restart` is a SOFT reset and QEMU's flash image outlives it
(`-drive file=...,if=mtd`), so the next boot re-mounts exactly the littlefs a
real power cut would leave behind — the same mechanism `QT reboot` already uses
to prove persistence. (Killing the container would not do: its /tmp, where the
emulated flash lives, dies with it.)

WHY THREE POINTS AND NOT N BYTE OFFSETS. `recstore` needed a byte sweep because
it wrote records IN PLACE, so every offset was a different half-written record.
Here the only in-place mutation of a slot is ONE `lfs_rename` metadata commit;
the record itself is staged in a temp file that is not a slot. So the
interruption space is: during the staging write (swept across byte offsets
anyway, because the claim is cheap to over-trust), after the staging write is
synced but before the rename, and immediately after the rename. The IN-PLACE
path is used deliberately: an interrupted append can only leave a free slot
empty, while a replace is the case where a cut could destroy a record that was
already committed — which is what `recstore` got wrong twice.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qemu_harness"))
from conftest import *  # noqa: F401,F403,E402

OLD = "OLDoldOLDold"
NEW = "NEWnewNEWnew"

# (point, offset) — see the module docstring. Offsets sweep the staging write:
# nothing staged, one byte, halfway, one byte short of the whole payload.
CUTS = [
    (1, 0),
    (1, 1),
    (1, len(NEW) // 2),
    (1, len(NEW) - 1),
    (2, 0),
    (3, 0),
]


def _boot(s):
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120)
    s.wait_log(r"qemu_test task started", timeout=60)


def _body(s):
    line = s.cmd_ok("QT store_get 0")
    assert "body=" in line, line
    return line.split("body=")[1].strip()


def _reboot_after_cut(s, point, off):
    """Send the interrupted write and wait for the guest to come back.

    `since_line` is not optional: wait_log searches from the start of the
    capture, so without it the waits match the PREVIOUS boot's banners and
    return instantly — the test would never wait for the reset at all.
    """
    before = s.line_count()
    s.cmd(f"QT store_tear {point} {off} 0 {NEW}")
    s.wait_log(r"esp32tap phase-1 safety core started", timeout=120, since_line=before)
    s.wait_log(r"qemu_test task started", timeout=60, since_line=before)


def test_an_uninterrupted_in_place_write_is_the_baseline(qemu):
    """The tear verb with point=0 must simply work.

    Without this, a cut test that "passes" because the write never happened at
    all would be indistinguishable from one that passes because the rename is
    atomic.
    """
    s = qemu()
    _boot(s)
    assert "history=0" in s.cmd_ok("QT store_stat")

    assert "QTOK store_put" in s.cmd_ok(f"QT store_put {OLD}")
    assert _body(s) == OLD

    line = s.cmd_ok(f"QT store_tear 0 0 0 {NEW}")
    assert "ok=1" in line, line
    assert _body(s) == NEW
    assert "history=1" in s.cmd_ok("QT store_stat")


@pytest.mark.parametrize("point,off", CUTS)
def test_a_cut_write_leaves_the_old_record_or_the_new_one(qemu, point, off):
    """THE ASSERTION THAT MATTERS, at every interruption point.

    After the cut the slot must read as EXACTLY the old record or EXACTLY the
    new one — never a prefix of either, never absent, never two records. And the
    store must still MOUNT: a filesystem that survives the record but not its
    own metadata would be no better than the store it replaced.
    """
    s = qemu()
    _boot(s)
    assert "history=0" in s.cmd_ok("QT store_stat")
    assert "QTOK store_put" in s.cmd_ok(f"QT store_put {OLD}")
    assert _body(s) == OLD

    _reboot_after_cut(s, point, off)

    # The store re-mounted (a QTERR here would say `no_partition`) and holds
    # exactly the one record it held before the cut.
    line = s.cmd_ok("QT store_stat")
    assert "history=1" in line, f"the cut changed the record COUNT: {line}"

    body = _body(s)
    assert body in (OLD, NEW), (
        f"point {point} off {off} left the slot at neither the old record nor " f"the new one: {body!r}"
    )

    # And where the outcome is DETERMINED by where the cut landed, assert that
    # too — "either one" would also be satisfied by a store that silently threw
    # every write away.
    if point == 3:
        assert body == NEW, f"the cut landed AFTER the rename commit: {body!r}"
    else:
        assert body == OLD, f"the cut landed before the rename, so the destination must be " f"untouched: {body!r}"

    # The tier is not wedged by the cut: the next write still lands.
    line = s.cmd_ok(f"QT store_tear 0 0 0 {NEW}")
    assert "ok=1" in line, line
    assert _body(s) == NEW
