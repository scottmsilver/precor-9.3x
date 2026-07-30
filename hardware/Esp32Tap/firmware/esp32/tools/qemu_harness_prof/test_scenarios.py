"""S1–S5, S7 — behavioral QEMU scenarios against the ESP32TAP_QEMU_TEST image.

Each scenario boots a fresh firmware instance under QEMU (via the `qemu`
factory fixture), injects Precor console/motor KV byte streams on the
chardev-wired UARTs, and asserts protocol/safety behavior through the
SafetyController audit ring (QTAUDIT), QTSTATE snapshots, and the captured
UART1 firmware TX.

Timing philosophy: QEMU wall/guest time is elastic, so protocol deadlines
are asserted via audit-event presence/order plus GUEST-clock bounds taken
from QTSTATE's t_us field (both endpoints are guest samples, so the
wall/guest ratio cancels out); wall clocks only pace injection and bound
waits generously. The 1.5 s console-freshness deadline (S2b) and the
100 ms emulate burst cadence (S3) get hard guest-time bounds this way;
the wall-clock mean-gap check in S3 stays advisory (warn-only).
"""

from __future__ import annotations

import time
import warnings

import pytest
import synth
from capture_streams import capture_streams
from qemu_session import QemuSession

pytestmark = pytest.mark.qemu

PACER_INTERVAL = 0.15  # wall s between console cycles: > GAP_QUALIFY 20 ms
# guest, << CONSOLE_FRESH 1.5 s guest

# Exact model event strings (safety_controller.cpp, proven by host suite),
# in the normative gap-safe entry order. Evidentiary honesty: the first
# five labels (command_zero .. wait_entry_gap) are batch-emitted by
# request_emulate as INTENT markers — e.g. "configure_inverted_uart" does
# not itself attest a UART reconfiguration happened. Actuation evidence
# comes from the later events (relay_cmd_on -> feedback_candidate ->
# feedback_emulate_stable proves real set_relay_cmd edges through the
# command-coupled K1 model), the io_relay/io_tx IO-boundary levels in
# QTSTATE, and the zero-first frames captured byte-level on UART1 TX.
ENTRY_SEQUENCE = [
    "command_zero",
    "configure_inverted_uart",
    "verify_physical_idle_low",
    "tx_enable_on",
    "wait_entry_gap",
    "relay_cmd_on",
    "feedback_candidate",
    "feedback_emulate_stable",
    "send_first_complete_zero_frame",
]


def assert_state(s: QemuSession, **want) -> dict:
    st = s.state()
    for k, v in want.items():
        assert st[k] == v, f"{k}={st[k]!r}, want {v!r} (full: {st})"
    return st


def assert_boot_proxy(s: QemuSession) -> None:
    # Unlike the default build's floating-GPIO BOTH_CLOSED quirk, the shim
    # boots BYPASS/TREAD_OK: PROXY, relay released, fault clear.
    # io_relay/io_tx are the shim-OBSERVED IO-boundary levels.
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=0, io_relay=0, io_tx=0)


def tx_complete_frames(s: QemuSession) -> int:
    """Number of complete (0xFF-terminated) frames captured on UART1 TX."""
    return len(s.tx_bytes().split(b"\xff")) - 1


def enter_emulating(s: QemuSession, speed_tenths: int = 12, incline_half: int = 10) -> list[int]:
    """S3 helper (shared by S2b/S4): constant console pacer + QT lease +
    QT emulate; waits for the full ordered entry audit subsequence.
    Returns the ring indexes of ENTRY_SEQUENCE."""
    s.start_pacer(synth.console_cycle_bytes(speed_tenths, incline_half), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    lease_line = s.cmd_ok("QT lease")
    assert "connect=1 acquire=1" in lease_line, lease_line
    lease_idx = s.wait_audit("lease_acquired:EXECUTOR:1:", prefix=True, timeout=15)
    emu_line = s.cmd_ok("QT emulate")
    assert "ok=1" in emu_line, emu_line
    idxs = s.wait_audit_sequence(ENTRY_SEQUENCE, since=lease_idx, timeout=45)
    # tx_enable_on strictly before relay_cmd_on is the entry ordering
    # guarantee. NOTE: this assert is implied by the ordered-subsequence
    # match above (it can never fail independently) — kept as executable
    # documentation of the one ordering PLAN calls out by name.
    tx_on = idxs[ENTRY_SEQUENCE.index("tx_enable_on")]
    relay_on = idxs[ENTRY_SEQUENCE.index("relay_cmd_on")]
    assert tx_on < relay_on
    # No aborts/emergencies inside the entry window.
    s.assert_no_audit(
        lambda t: t.startswith("emergency:") or t.startswith("entry_abort") or t.startswith("entry_rejected"),
        since=lease_idx,
        label="during emulate entry",
    )
    return idxs


def assert_tx_ceases(s: QemuSession, t_event: float, grace_s: float = 1.0, settle_s: float = 2.5) -> None:
    """No UART1 TX burst may arrive later than grace_s after t_event."""
    time.sleep(settle_s)
    late = [(t, d) for t, d in s.tx_chunks() if t > t_event + grace_s]
    assert not late, f"TX continued after stop: {late[:3]}"


def _replay(send_fn, stream, duration_s: float, compress: float = 2.0, floor_s: float = 0.03) -> bytes:
    """Replay a timed-burst stream at recorded cadence (compressed, gaps
    floored) for duration_s wall, looping if the stream is shorter.
    Returns the injected bytes."""
    injected = bytearray()
    t_end = time.monotonic() + duration_s
    prev_t_us = None
    i = 0
    while time.monotonic() < t_end:
        t_us, data = stream[i % len(stream)]
        if prev_t_us is not None and t_us > prev_t_us:
            gap = (t_us - prev_t_us) / 1e6 / compress
        else:
            gap = floor_s  # stream wrap or first burst
        time.sleep(max(gap, floor_s))
        send_fn(data)
        injected += data
        prev_t_us = t_us
        i += 1
    return bytes(injected)


def test_s1_proxy_passive_decode(qemu):
    s = qemu()
    assert_boot_proxy(s)

    # (a) Real-capture replay (try5), console + motor concurrently, >=10 s.
    console_stream, motor_stream = capture_streams("try5")
    import threading

    results: dict[str, bytes] = {}

    def run_motor():
        results["motor"] = _replay(s.send_motor, motor_stream, 11.0)

    tm = threading.Thread(target=run_motor)
    tm.start()
    injected_console = _replay(s.send_console, console_stream, 11.0)
    tm.join()
    injected_motor = results["motor"]

    # (b) Synthetic pass: 20 cycles + interleaved motor replies.
    for _ in range(20):
        c = synth.console_cycle_bytes(12, 10)
        s.send_console(c)
        injected_console += c
        m = synth.motor_reply("hmph", "78") + synth.motor_reply("inc", "A")
        s.send_motor(m)
        injected_motor += m
        time.sleep(0.15)

    expected_frames = synth.count_complete_frames(injected_console)
    assert expected_frames > 50  # capture sanity

    # Byte counters reach the injected totals (poll: RX drain + 100 ms
    # shim cadence).
    deadline = time.monotonic() + 30
    while True:
        st = s.state()
        if st["cons_bytes"] >= len(injected_console) and st["motor_bytes"] >= len(injected_motor):
            break
        assert time.monotonic() < deadline, (
            f"byte counters stalled: {st} vs console={len(injected_console)} " f"motor={len(injected_motor)}"
        )
        time.sleep(0.5)

    # >= 90% of injected complete console frames decoded + audited.
    deadline = time.monotonic() + 20
    while s.audit_count("complete_console_frame") < 0.9 * expected_frames:
        assert time.monotonic() < deadline, (
            f"complete_console_frame count " f"{s.audit_count('complete_console_frame')} < 0.9 * " f"{expected_frames}"
        )
        time.sleep(0.5)

    # Proxy invariants: no emergencies, relay never commanded, no TX.
    s.assert_no_audit(lambda t: t.startswith("emergency:"), label="in proxy")
    assert s.audit_count("relay_cmd_on") == 0
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=0)
    assert s.tx_bytes() == b"", "proxy must never transmit on UART1"

    # (c) Fuzz smoke tail: malformed console frames must not panic,
    # reboot, or trip an emergency (kv_parse documented tolerance).
    fuzz_since = s.audit_events()[-1][0] if s.audit_events() else 0
    for f in synth.fuzz_frames():
        s.send_console(f)
        time.sleep(0.05)
    s.send_console(synth.console_cycle_bytes(12, 10))
    s.wait_guest_uptime_delta(3, timeout=60)  # heartbeats keep coming
    assert s.raw0().count(b"ESP-ROM:esp32s3") == 1, "reboot detected"
    s.assert_no_audit(lambda t: t.startswith("emergency:"), since=fuzz_since, label="during fuzz")
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=0)
    assert s.tx_bytes() == b""


def test_s2a_proxy_silence_is_benign(qemu):
    s = qemu()
    assert_boot_proxy(s)
    s.start_pacer(synth.console_cycle_bytes(12, 10), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    time.sleep(3.0)
    s.stop_pacer()
    time.sleep(1.0)  # drain in-flight RX + audit
    events0 = s.audit_events()
    last_idx = events0[-1][0] if events0 else -1
    st0 = s.state()
    assert st0["mode"] == "PROXY"

    # >= 3 s of guest-time console silence in PROXY: no new audit events,
    # state unchanged (silence only gates Emulate entry).
    s.wait_guest_uptime_delta(4, timeout=90)
    new = [e for e in s.audit_events() if e[0] > last_idx]
    assert not new, f"unexpected audit events during proxy silence: {new}"
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=st0["fault"])


def test_s2b_emulating_console_silence_is_fatal(qemu):
    s = qemu()
    assert_boot_proxy(s)
    idxs = enter_emulating(s)
    s.wait_tx_contains(b"[hmph:0]", timeout=20)

    # Pre-stop evidence: still EMULATING (relay energized at the IO
    # boundary) and no emergency has fired while the pacer was healthy —
    # so the stale event awaited below can only be caused by the silence
    # created next, not a premature spontaneous stop.
    st0 = assert_state(s, mode="EMULATING", relay=1, tx=1, io_relay=1, io_tx=1)
    s.assert_no_audit(lambda t: t.startswith("emergency:"), since=idxs[-1], label="while pacer healthy")
    t0_us = st0["t_us"]  # guest clock, sampled before the pacer stops

    s.stop_pacer()
    # CONSOLE_FRESH is 1.5 s guest; generous 10 s wall bound.
    em_idx = s.wait_audit("emergency:console_stale", since=idxs[-1], timeout=10.0)
    t_em = time.monotonic()
    assert em_idx > idxs[-1]
    st1 = assert_state(s, mode="PROXY", relay=0, tx=0, speed=0, incline=0, io_relay=0, io_tx=0)
    # (fault is deliberately not asserted here: one stale-relay feedback
    # sample during the emergency's same engine iteration may latch the
    # PROXY-feedback fault — the model's conservative fail-safe.)

    # Guest-time bracket of the 1.5 s CONSOLE_FRESH_US deadline (QTSTATE
    # and QTAUDIT come from the same FIFO shim task, so t0 predates the
    # pacer stop and t1 postdates the stale event):
    #   lower: the last console frame lands no earlier than t0 minus one
    #          pacer interval (0.15 s), so the event cannot legally land
    #          before t0 + 1.35 s guest — a premature freshness kill
    #          (e.g. 0.15 s) cannot pass 1.2;
    #   upper: event <= t0 + pacer overhang + 1.5 s, and the observe +
    #          QTSTATE round-trips add well under a second of guest time,
    #          so a CONSOLE_FRESH_US regression to >= 4 s cannot pass.
    guest_elapsed = (st1["t_us"] - t0_us) / 1e6
    assert 1.2 <= guest_elapsed <= 4.0, (
        f"console_stale landed {guest_elapsed:.2f}s guest after the " f"pre-stop sample; CONSOLE_FRESH deadline is 1.5s"
    )
    assert_tx_ceases(s, t_em)


def test_s3_emulate_entry_happy_path(qemu):
    s = qemu()
    assert_boot_proxy(s)
    idxs = enter_emulating(s, 12, 10)

    # Wire: wait for one full 5-burst 14-key cycle on UART1 TX. Frame
    # integrity is asserted on the reassembled byte STREAM (QEMU trickles
    # TX bytes, so wall-time chunk boundaries can fall mid-frame); wall
    # grouping is used only for the cadence check below.
    for k in synth.KEY_CYCLE:
        s.wait_tx_contains(b"[" + k.encode("ascii"), timeout=20)
    parts = s.tx_bytes().split(b"\xff")
    frames = parts[:-1]  # every complete frame is 0xFF-terminated
    assert len(frames) >= 14, frames
    for f in frames:
        assert f.startswith(b"[") and f.endswith(b"]"), f
    first14 = frames[:14]
    keys = [synth.frame_key(f) for f in first14]
    # One full cycle covers all 14 keys exactly once, in the 5-burst
    # order of emulation_cycle.h BURSTS (flattened == KV_CYCLE order).
    expected = [synth.KEY_CYCLE[i] for burst in synth.BURSTS for i in burst]
    assert keys == expected, keys
    # First burst is the entry zero frame (PLAN entry step 6).
    assert first14[0] == b"[inc:0]" and first14[1] == b"[hmph:0]", first14

    # Cadence, wall clock: hard = wall burst grouping exists (>=5 distinct
    # bursts at >=30 ms separation, EMU_BURST_GAP_MS=100 nominal);
    # advisory = mean inter-burst gap 50-300 ms (QEMU wall/guest ratio is
    # elastic).
    bursts = s.tx_bursts()
    assert len(bursts) >= 5, f"expected >=5 distinct bursts, got {bursts}"
    gaps = [bursts[i][0] - bursts[i - 1][1] for i in range(1, len(bursts))]
    mean_gap = sum(gaps) / len(gaps)
    if not 0.05 <= mean_gap <= 0.30:
        warnings.warn(
            f"advisory: mean inter-burst gap {mean_gap:.3f}s " f"outside 50-300 ms (QEMU wall/guest elasticity)"
        )

    # Cadence, HARD guest-time upper bound (QTSTATE t_us — both endpoints
    # are guest samples, so wall/guest elasticity cancels out): 70 further
    # frames = 25 bursts = >= 24 inter-burst gaps of EMU_BURST_GAP_MS =
    # 100 ms, ~2.4 s guest nominal plus <~1 s of sampling round-trips. A
    # firmware pacing bursts at >= ~330 ms would need > 8 s guest here.
    t0_us = s.state()["t_us"]
    n0 = tx_complete_frames(s)
    deadline = time.monotonic() + 120
    while tx_complete_frames(s) < n0 + 70:
        assert time.monotonic() < deadline, f"TX stalled at {tx_complete_frames(s)} frames " f"waiting for {n0 + 70}"
        time.sleep(0.1)
    guest_elapsed = (s.state()["t_us"] - t0_us) / 1e6
    assert guest_elapsed <= 8.0, (
        f"25 emulate bursts took {guest_elapsed:.2f}s guest — burst " f"cadence regressed (nominal 24 x 0.1s gaps)"
    )

    # Owner motion mirrors onto the wire only after the zero cycle.
    pre = len(s.tx_bytes())
    line = s.cmd_ok("QT motion 50 30")
    assert "ok=1" in line, line
    s.wait_audit("owner_motion", since=idxs[-1], timeout=15)
    s.wait_tx_contains(b"[hmph:1F4]", timeout=20, offset=pre)
    s.wait_tx_contains(b"[inc:1E]", timeout=20, offset=pre)
    assert b"[hmph:0]" in s.tx_bytes()[:pre], "zero cycle must precede mirrored motion"

    assert_state(s, mode="EMULATING", relay=1, tx=1, fault=0, speed=50, incline=30, io_relay=1, io_tx=1)


def test_s4_console_takeover(qemu):
    s = qemu()
    assert_boot_proxy(s)
    idxs = enter_emulating(s, 12, 10)  # console hmph value "78"
    s.wait_tx_contains(b"[hmph:0]", timeout=20)

    # Console button press: hmph changes 78 -> C8 (2.0 mph) at unchanged
    # cadence.
    s.set_pacer_payload(synth.console_cycle_bytes(20, 10))
    em_idx = s.wait_audit("emergency:console_takeover", since=idxs[-1], timeout=5.0 + PACER_INTERVAL)
    t_em = time.monotonic()
    assert em_idx > idxs[-1]
    # Takeover is an emergency stop, NOT a latched fault.
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=0, speed=0, incline=0, io_relay=0, io_tx=0)
    assert_tx_ceases(s, t_em)


def test_s5_clamp_enforcement(qemu):
    s = qemu()
    assert_boot_proxy(s)
    s.start_pacer(synth.console_cycle_bytes(0, 0), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    lease_line = s.cmd_ok("QT lease")
    assert "connect=1 acquire=1" in lease_line, lease_line
    lease_idx = s.wait_audit("lease_acquired:EXECUTOR:1:", prefix=True, timeout=15)

    # Speed 121 tenths > SPEED_MAX_TENTHS=120 -> rejected wholesale.
    line = s.cmd_ok("QT motion 121 0")
    assert "ok=0" in line, line
    r1 = s.wait_audit("motion_rejected:speed_range", since=lease_idx, timeout=15)
    assert s.audit_count("owner_motion", since=lease_idx) == 0
    assert_state(s, speed=0, incline=0)

    # Incline 31 half-pct > INCLINE_APP_MAX_HALF=30 -> rejected wholesale.
    line = s.cmd_ok("QT motion 50 31")
    assert "ok=0" in line, line
    s.wait_audit("motion_rejected:incline_range", since=r1, timeout=15)
    assert s.audit_count("owner_motion", since=lease_idx) == 0
    assert_state(s, speed=0, incline=0)

    # Exact limits are accepted (identical code path future native-server
    # / BLE tiers will call).
    line = s.cmd_ok("QT motion 120 30")
    assert "ok=1" in line, line
    s.wait_audit("owner_motion", since=lease_idx, timeout=15)
    assert_state(s, speed=120, incline=30)


def test_s7a_entry_feedback_timeout_fails_closed(qemu):
    """Negative feedback path: K1 stuck (poles frozen in BYPASS; the coil
    command no longer moves them) -> the 10 ms RELAY_FEEDBACK_DEADLINE
    fails the entry closed with a latched fault and NOTHING is ever
    transmitted on the wire. Exercises under QEMU the fail-closed path the
    always-succeeding K1 model can never reach (previously host-suite
    only)."""
    s = qemu()
    assert_boot_proxy(s)
    s.start_pacer(synth.console_cycle_bytes(12, 10), PACER_INTERVAL)
    s.wait_audit("complete_console_frame", timeout=30)
    lease_line = s.cmd_ok("QT lease")
    assert "connect=1 acquire=1" in lease_line, lease_line
    lease_idx = s.wait_audit("lease_acquired:EXECUTOR:1:", prefix=True, timeout=15)

    k1_line = s.cmd_ok("QT k1 stuck")
    assert "mode=stuck" in k1_line, k1_line
    emu_line = s.cmd_ok("QT emulate")
    assert "ok=1" in emu_line, emu_line  # entry starts; failure is downstream

    # Entry proceeds to the relay command; the poles never leave BYPASS,
    # so the transfer fails closed at the 10 ms feedback deadline.
    relay_idx = s.wait_audit("relay_cmd_on", since=lease_idx, timeout=30)
    em_idx = s.wait_audit("emergency:entry_feedback_timeout", since=relay_idx, timeout=15)
    assert em_idx > relay_idx
    # Never reached EMULATING, never armed the wire.
    assert s.audit_count("feedback_emulate_stable", since=lease_idx) == 0
    assert s.audit_count("send_first_complete_zero_frame", since=lease_idx) == 0
    assert s.tx_bytes() == b"", "no bytes may reach the motor on a failed entry"
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=1, speed=0, incline=0, io_relay=0, io_tx=0)

    # The fault is LATCHED: even after K1 heals and a fresh lease is
    # taken, re-entry is refused.
    s.cmd_ok("QT k1 auto")
    lease2 = s.cmd_ok("QT lease")
    assert "connect=1 acquire=1" in lease2, lease2
    rej = s.cmd_ok("QT emulate")
    assert "ok=0" in rej, rej
    s.wait_audit("entry_rejected:fault_latched", since=em_idx, timeout=15)
    assert_state(s, mode="PROXY", relay=0, fault=1, io_relay=0, io_tx=0)
    assert s.tx_bytes() == b""


def test_s7b_emulating_feedback_loss_fails_closed(qemu):
    """Negative feedback path: mid-EMULATING relay drop / wiring fault
    (poles forced back to BYPASS while the coil stays commanded) ->
    emergency:relay_feedback_invalid, latched fault, relay + TX released
    at the IO boundary, wire TX ceases."""
    s = qemu()
    assert_boot_proxy(s)
    idxs = enter_emulating(s)
    s.wait_tx_contains(b"[hmph:0]", timeout=20)
    assert_state(s, mode="EMULATING", relay=1, tx=1, fault=0, io_relay=1, io_tx=1)

    s.cmd_ok("QT k1 bypass")
    em_idx = s.wait_audit("emergency:relay_feedback_invalid", since=idxs[-1], timeout=10)
    t_em = time.monotonic()
    assert em_idx > idxs[-1]
    assert_state(s, mode="PROXY", relay=0, tx=0, fault=1, speed=0, incline=0, io_relay=0, io_tx=0)
    assert_tx_ceases(s, t_em)
