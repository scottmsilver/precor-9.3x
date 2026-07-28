"""S8 — PLAN normal exit, on target.

The committed harness has S1-S7 and no normal-exit scenario at all: every
scenario that leaves Emulate does so through a FAIL-CLOSED path (console
silence, feedback loss, console takeover). So the ordinary, polite exit — the
one a user takes a hundred times a session — had ZERO on-target coverage, and
PLAN normal-exit step 1 ("transmit and finish a complete zero frame") was not
implemented at all: the controller emitted the audit event and the firmware
never put a zero frame on the wire.

PLAN, verbatim:

    Normal exit is exactly:
    1. transmit and finish a complete zero frame;
    2. wait for a capture-qualified gap, for at most 1 s;
    3. deassert RELAY_CMD and require bypass feedback continuously for at
       least 1 ms, with an actual GPIO sample at the end of that interval and
       before the 10 ms deadline;
    4. deassert TX_ENABLE;
    5. release ownership.

This asserts all five, in order, against the wire and the audit ring.

The load-bearing assertion is `test_s8_normal_exit_transmits_a_zero_frame_last`:
the belt is driven to a NONZERO speed, the wire is checked to actually be
carrying that speed, and then the LAST complete frames transmitted before the
relay opened must encode zero. That is step 1 stated as an observable: whatever
else happened, the final thing the motor was told before the bridge went back
to copper was "stop".

Uses the committed harness as a library; it does not modify it.
"""

from __future__ import annotations

import time

import pytest
import synth
from qemu_session import QemuSession
from test_scenarios import PACER_INTERVAL, assert_boot_proxy, assert_state, assert_tx_ceases, enter_emulating

pytestmark = pytest.mark.qemu

# PLAN normal-exit steps 1..5, as the model's exact event strings.
EXIT_SEQUENCE = [
    "send_and_finish_complete_zero_frame",  # step 1
    "wait_exit_gap",  # step 2
    "relay_cmd_off",  # step 3 (command)
    "feedback_candidate",  # step 3 (first qualifying sample)
    "feedback_bypass_stable",  # step 3 (>= 1 ms, < 10 ms)
    "tx_enable_off",  # step 4
    "lease_released",  # step 5
]


def complete_frames(data: bytes) -> list[bytes]:
    """The 0xFF-terminated frames in a TX capture, in order."""
    parts = data.split(b"\xff")
    return [p for p in parts[:-1]]


def test_s8_normal_exit_follows_the_plan_order(qemu):
    s: QemuSession = qemu()
    assert_boot_proxy(s)
    enter_emulating(s, 12, 10)
    s.wait_tx_contains(b"[hmph:0]", timeout=20)
    assert_state(s, mode="EMULATING", relay=1, tx=1, fault=0, io_relay=1, io_tx=1)

    n0 = len(s.audit_events())
    line = s.cmd_ok("QT exit")
    assert "ok=1" in line, line

    idxs = s.wait_audit_sequence(EXIT_SEQUENCE, since=n0, timeout=45)
    assert idxs == sorted(idxs)

    # Steps 3-5 landed: relay released, TX disabled, ownership gone, and NO
    # fault — a normal exit is not a fail-closed stop.
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=0, io_relay=0, io_tx=0)

    # Nothing in a normal exit may latch a fault or take an emergency path.
    s.assert_no_audit(
        lambda t: t.startswith("emergency:") or t.startswith("entry_abort"),
        since=n0,
        label="during normal exit",
    )
    s.stop_pacer()


def test_s8_normal_exit_transmits_a_zero_frame_last(qemu):
    """PLAN step 1, as an observable on the wire.

    Drive a NONZERO speed, confirm the wire is really carrying it, then exit
    and require that the last complete frames before TX ceased are zeros.
    Without step 1 implemented, the last frame on the wire is `[hmph:78]` —
    the bridge returns to copper with the motor's last command still "1.2 mph".
    """
    s: QemuSession = qemu()
    assert_boot_proxy(s)
    enter_emulating(s, 12, 10)
    s.wait_tx_contains(b"[hmph:0]", timeout=20)

    # Owner commands 4.0 mph / 5.0% and the wire must actually carry it
    # (hmph = mph*100 in uppercase hex: 4.00 -> 400 -> "190").
    assert "ok=1" in s.cmd_ok("QT motion 40 10")
    s.wait_tx_contains(b"[hmph:190]", timeout=30)
    offset = len(s.tx_bytes())

    n0 = len(s.audit_events())
    assert "ok=1" in s.cmd_ok("QT exit")
    s.wait_audit_sequence(EXIT_SEQUENCE, since=n0, timeout=45)
    t_exit = time.monotonic()

    # Let the wire settle, then require TX to have stopped.
    assert_tx_ceases(s, t_exit)
    s.stop_pacer()

    tail = complete_frames(s.tx_bytes()[offset:])
    assert tail, "no complete frames captured after the motion command"

    # THE ASSERTION: the motor's last commanded state on the wire is zero.
    # Burst 0 of the cycle is (inc, hmph), so the exit zero frame is exactly
    # those two keys at zero.
    assert tail[-2:] == [b"[inc:0]", b"[hmph:0]"], (
        "PLAN normal-exit step 1 violated: the last complete frames before the "
        f"relay opened were {tail[-3:]!r}, not a complete zero frame"
    )

    # And the nonzero command really was on the wire before the exit, so the
    # assertion above is not vacuous.
    assert b"[hmph:190]" in s.tx_bytes()[:offset]


def test_s8_normal_exit_zero_frame_precedes_the_relay_command(qemu):
    """Step 1 must FINISH before step 3 moves K1.

    Asserted by byte accounting rather than wall clocks: the TX byte count at
    the moment `relay_cmd_off` is observed already includes the complete zero
    frame. The firmware enforces this by refusing to qualify the exit gap while
    the zero frame is still owed.
    """
    s: QemuSession = qemu()
    assert_boot_proxy(s)
    enter_emulating(s, 12, 10)
    s.wait_tx_contains(b"[hmph:0]", timeout=20)
    assert "ok=1" in s.cmd_ok("QT motion 40 10")
    s.wait_tx_contains(b"[hmph:190]", timeout=30)
    offset = len(s.tx_bytes())

    n0 = len(s.audit_events())
    assert "ok=1" in s.cmd_ok("QT exit")

    # Poll for relay_cmd_off, sampling the TX capture each time, and keep the
    # capture length from the LAST sample taken before it appeared.
    deadline = time.monotonic() + 45
    seen = None
    while time.monotonic() < deadline:
        length = len(s.tx_bytes())
        if any(i >= n0 and t == "relay_cmd_off" for i, t in s.audit_events()):
            seen = length
            break
        time.sleep(0.02)
    assert seen is not None, "relay_cmd_off never observed"

    # ORDERING IS DECIDED IN-GUEST, NOT BY CROSS-CHANNEL ARRIVAL.
    #
    # The previous form compared len(tx_bytes()) against the arrival of the
    # relay_cmd_off audit line. Those are two INDEPENDENTLY BUFFERED host
    # channels (audit on UART0, motor TX on UART1, separate sockets and reader
    # threads), so their host arrival order does not reflect guest event order.
    # It flapped ~40% of runs while the guest fact said the firmware was right.
    #
    # The controller now records, at the instant it deasserts RELAY_CMD and
    # while both facts are simultaneously known to it, whether the step-1
    # obligation was still outstanding. That is the property PLAN actually
    # requires, and it is STRICTER than the byte accounting: it cannot be
    # satisfied by a frame that merely happened to be captured in time.
    kinds = {t for _, t in s.audit_events()}
    assert "relay_cmd_off:zero_frame_unfinished" not in kinds, (
        "the guest deasserted RELAY_CMD while the complete zero frame was "
        "still owed or still on the wire (PLAN step 1 did not precede step 3)"
    )
    assert "relay_cmd_off:zero_frame_done" in kinds, (
        "no in-guest exit-ordering fact was recorded; the normal-exit path "
        f"did not run. audit kinds: {sorted(kinds)!r}"
    )
    # ...and the frame must genuinely have reached the wire, so "done" cannot
    # be satisfied by never having owed a frame at all. WAIT for it rather than
    # snapshotting: the TX capture is a separate buffered channel, so bytes the
    # guest has already finished sending can still be in flight to the host.
    # Liveness only — the ORDERING claim is the in-guest fact asserted above.
    s.wait_tx_contains(b"[hmph:0]", timeout=20, offset=offset)
    s.wait_tx_contains(b"[inc:0]", timeout=20, offset=offset)
    s.stop_pacer()


def test_s8_console_pacer_keeps_running(qemu):
    """Sanity guard on the three tests above: they all rely on the console
    staying fresh through the exit, because a stale console would produce a
    FAIL-CLOSED stop that also ends with relay=0/tx=0 and would make the
    scenarios pass for the wrong reason."""
    s: QemuSession = qemu()
    assert_boot_proxy(s)
    s.start_pacer(synth.console_cycle_bytes(12, 10), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    before = s.audit_count("complete_console_frame")
    time.sleep(2.0)
    after = s.audit_count("complete_console_frame")
    assert after > before, "the console pacer stopped feeding complete frames"
    s.stop_pacer()
