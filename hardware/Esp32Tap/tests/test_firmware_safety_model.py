from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


FIRMWARE_DIR = Path(__file__).resolve().parents[1] / "firmware"
sys.path.insert(0, str(FIRMWARE_DIR))

from safety_model import (  # noqa: E402
    ConnectionIdentity,
    Controller,
    Feedback,
    Mode,
    Transport,
)
import build_safety_manifest as manifest_builder  # noqa: E402


def identity(
    transport: Transport = Transport.WSS,
    handle: str | int = "socket-a",
    generation: int = 1,
) -> ConnectionIdentity:
    return ConnectionIdentity(transport, handle, generation)


def connected_controller(owner: ConnectionIdentity) -> Controller:
    controller = Controller()
    controller.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.0,
    )
    assert controller.connect(owner)
    assert controller.acquire(owner, now=0.0)
    return controller


def enter_emulate(
    controller: Controller,
    owner: ConnectionIdentity,
    *,
    now: float = 0.0,
) -> None:
    controller.observe_console_bytes(b"[hmph:0000]", now=now)
    assert controller.request_emulate(owner, now=now, uart_idle_low=True)
    assert controller.mode is Mode.ENTRY_WAIT_GAP
    assert controller.observe_interframe_gap(now=now + 0.1)
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=now + 0.105,
    )
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=now + 0.106,
    )
    assert controller.mode is Mode.EMULATING


@pytest.mark.parametrize(
    ("transport", "handle"),
    ((Transport.WSS, "wss-object-17"), (Transport.BLE, 42)),
)
def test_lease_uses_transport_handle_and_generation(
    transport: Transport,
    handle: str | int,
) -> None:
    owner = identity(transport, handle, 7)
    controller = connected_controller(owner)

    assert controller.owner == owner
    assert not controller.command_motion(
        identity(transport, handle, 6),
        speed_tenths=20,
        incline_half_percent=2,
        now=1.0,
    )
    assert not controller.command_motion(
        identity(transport, handle, 8),
        speed_tenths=20,
        incline_half_percent=2,
        now=1.0,
    )
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0


def test_manual_owner_persists_without_a_deadline() -> None:
    owner = identity()
    other = identity(handle="socket-b")
    controller = connected_controller(owner)
    assert controller.connect(other)

    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=0,
        now=0.0,
    )
    assert controller.lease_expires_at is None
    assert not controller.command_motion(
        other,
        speed_tenths=90,
        incline_half_percent=8,
        now=2.0,
    )
    assert not controller.heartbeat(other, now=9.0)
    assert controller.heartbeat(owner, now=10.0)
    controller.tick(now=10.0)
    assert controller.owner == owner
    assert controller.speed_tenths == 30
    assert controller.incline_half_percent == 0
    assert controller.lease_expires_at is None
    assert controller.mode is Mode.PROXY
    assert not controller.relay_cmd
    assert "emergency:lease_expired" not in controller.events


@pytest.mark.parametrize("transport", (Transport.WSS, Transport.BLE))
def test_manual_owner_persists_without_heartbeat(transport: Transport) -> None:
    owner = identity(transport, 23 if transport is Transport.BLE else "socket-a")
    controller = connected_controller(owner)
    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=0,
        now=0.0,
    )

    controller.tick(now=10.0)

    assert controller.owner == owner
    assert controller.speed_tenths == 30
    assert controller.lease_expires_at is None
    assert "emergency:lease_expired" not in controller.events


def test_unrelated_transport_drop_does_not_end_manual_owner() -> None:
    owner = identity(Transport.BLE, 23, 1)
    controller = connected_controller(owner)
    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=0,
        now=0.0,
    )

    assert not controller.disconnect_transport(Transport.WSS, now=10.0)
    assert controller.mode is Mode.PROXY
    assert controller.owner == owner
    assert controller.speed_tenths == 30
    assert controller.lease_expires_at is None
    assert not controller.relay_cmd
    assert "emergency:lease_expired" not in controller.events


def test_wss_owner_requires_the_same_concrete_handle_object() -> None:
    class EqualHandle:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, EqualHandle)

    concrete_handle = EqualHandle()
    equal_but_distinct = EqualHandle()
    owner = identity(Transport.WSS, concrete_handle, 3)
    impostor = identity(Transport.WSS, equal_but_distinct, 3)
    controller = connected_controller(owner)

    assert concrete_handle == equal_but_distinct
    assert concrete_handle is not equal_but_distinct
    assert not controller.command_motion(
        impostor,
        speed_tenths=20,
        incline_half_percent=2,
        now=1.0,
    )
    assert not controller.disconnect(impostor, now=1.1)
    assert controller.owner is owner


def test_owner_disconnect_is_immediate_but_non_owner_disconnect_is_ignored() -> None:
    owner = identity()
    other = identity(handle="socket-b")
    controller = connected_controller(owner)
    assert controller.connect(other)
    enter_emulate(controller, owner)
    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=4,
        now=0.2,
    )

    assert not controller.disconnect(other, now=0.5)
    assert controller.mode is Mode.EMULATING
    assert controller.owner == owner
    assert controller.speed_tenths == 30
    assert controller.incline_half_percent == 4
    assert controller.disconnect(owner, now=0.6)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert controller.events[-1] == "emergency:owner_disconnect"


def test_reconnect_and_handle_reuse_cannot_inherit_an_old_lease() -> None:
    old = identity(Transport.BLE, 23, 10)
    controller = connected_controller(old)
    assert controller.disconnect(old, now=0.1)

    assert not controller.connect(old)
    reused = identity(Transport.BLE, 23, 11)
    assert controller.connect(reused)
    assert controller.owner is None
    assert not controller.command_motion(
        reused,
        speed_tenths=10,
        incline_half_percent=0,
        now=0.2,
    )
    assert controller.acquire(reused, now=0.3)
    assert controller.owner == reused
    assert not controller.heartbeat(old, now=0.4)


def test_new_generation_invalidates_and_safely_stops_superseded_owner() -> None:
    old = identity(Transport.BLE, 23, 10)
    controller = connected_controller(old)
    enter_emulate(controller, old)

    fresh = identity(Transport.BLE, 23, 11)
    assert controller.connect(fresh)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert not controller.acquire(old, now=0.2)
    assert controller.acquire(fresh, now=0.2)


def test_executor_owns_locally_and_network_loss_does_not_renew_or_end_it() -> None:
    executor = identity(Transport.EXECUTOR, "program-17", 3)
    wss = identity()
    controller = connected_controller(executor)
    assert controller.connect(wss)
    enter_emulate(controller, executor)

    assert controller.lease_expires_at is None
    assert not controller.heartbeat(wss, now=0.5)
    assert not controller.disconnect(wss, now=0.6)
    controller.observe_console_bytes(b"[loop:5550]", now=0.7)
    controller.tick(now=0.8)
    assert controller.owner == executor
    assert controller.mode is Mode.EMULATING


@pytest.mark.parametrize(
    ("source", "failure", "must_proxy"),
    (
        (Transport.WSS, "silence", False),
        (Transport.WSS, "wss_drop", True),
        (Transport.WSS, "ble_drop", False),
        (Transport.BLE, "silence", False),
        (Transport.BLE, "wss_drop", False),
        (Transport.BLE, "ble_drop", True),
        (Transport.EXECUTOR, "silence", False),
        (Transport.EXECUTOR, "wss_drop", False),
        (Transport.EXECUTOR, "ble_drop", False),
    ),
)
def test_network_failure_matrix(
    source: Transport,
    failure: str,
    must_proxy: bool,
) -> None:
    handle: str | int = 17 if source is Transport.BLE else f"{source.value}-17"
    owner = identity(source, handle)
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    if failure == "silence":
        for now in (1.4, 2.8, 3.9):
            controller.observe_console_bytes(b"[loop:5550]", now=now)
        controller.tick(now=4.0)
    elif failure == "wss_drop":
        controller.disconnect_transport(Transport.WSS, now=1.0)
    else:
        controller.disconnect_transport(Transport.BLE, now=1.0)

    assert (controller.mode is Mode.PROXY) is must_proxy
    assert controller.relay_cmd is (not must_proxy)


@pytest.mark.parametrize("source", tuple(Transport))
@pytest.mark.parametrize("failure", ("reset", "watchdog"))
def test_reset_and_watchdog_matrix_always_returns_hardware_to_proxy(
    source: Transport,
    failure: str,
) -> None:
    handle: str | int = 17 if source is Transport.BLE else source.value
    owner = identity(source, handle)
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=4,
        now=0.5,
    )

    if failure == "reset":
        controller.reset(now=1.0, reason="brownout")
    else:
        controller.watchdog_stall(now=1.0)

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0
    assert not controller.relay_cmd
    assert not controller.tx_enable


@pytest.mark.parametrize("failure", ("reset", "watchdog"))
def test_reset_class_failures_invalidate_pre_reset_connections(
    failure: str,
) -> None:
    handle = object()
    old = identity(Transport.WSS, handle, 1)
    controller = connected_controller(old)
    if failure == "reset":
        controller.reset(now=1.0)
    else:
        controller.watchdog_stall(now=1.0)

    assert not controller.acquire(old, now=1.1)
    assert not controller.connect(old)
    fresh = identity(Transport.WSS, handle, 2)
    assert controller.connect(fresh)
    assert controller.acquire(fresh, now=1.2)
    assert controller.owner is fresh


def test_console_source_is_hardware_bridge_and_network_failures_do_nothing() -> None:
    controller = Controller()
    controller.disconnect_transport(Transport.WSS, now=1.0)
    controller.disconnect_transport(Transport.BLE, now=2.0)
    controller.tick(now=100.0)
    assert controller.mode is Mode.PROXY
    assert controller.feedback is Feedback.UNKNOWN
    assert not controller.relay_cmd


def test_console_freshness_requires_a_complete_valid_frame() -> None:
    controller = Controller()

    controller.observe_console_bytes(b"[hmph:0000", now=0.0)
    assert controller.last_complete_console_frame_at is None
    controller.observe_console_bytes(b"]", now=0.25)
    assert controller.last_complete_console_frame_at == pytest.approx(0.25)

    controller.observe_console_bytes(b"\xff[bad frame]\x00", now=0.5)
    assert controller.last_complete_console_frame_at == pytest.approx(0.25)
    controller.observe_console_bytes(b"[inc:0000]", now=1.0)
    assert controller.last_complete_console_frame_at == pytest.approx(1.0)


def test_late_console_frame_cannot_overwrite_missed_freshness_deadline() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    assert controller.observe_console_bytes(b"[loop:5550]", now=1.5) == 0
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.last_complete_console_frame_at == pytest.approx(0.0)
    assert controller.events[-1] == "emergency:console_stale"


@pytest.mark.parametrize("age", (1.500001, 20.0))
def test_stale_console_forces_immediate_zero_and_bypass(age: float) -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner, now=0.0)

    controller.tick(now=age)
    assert controller.mode is Mode.PROXY
    assert controller.events[-1] == "emergency:console_stale"


def test_exactly_one_point_five_seconds_is_stale() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner, now=0.0)

    controller.tick(now=1.499999)
    assert controller.mode is Mode.EMULATING
    controller.tick(now=1.5)
    assert controller.mode is Mode.PROXY


def test_motion_command_at_console_deadline_cannot_refresh_or_mutate() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    assert not controller.command_motion(
        owner,
        speed_tenths=60,
        incline_half_percent=10,
        now=1.5,
    )
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0
    assert controller.events[-1] == "emergency:console_stale"


def test_entry_order_and_first_zero_frame_follow_settled_transfer() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)

    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.events[-5:] == [
        "command_zero",
        "configure_inverted_uart",
        "verify_physical_idle_low",
        "tx_enable_on",
        "wait_entry_gap",
    ]
    assert not controller.relay_cmd
    assert controller.observe_interframe_gap(now=0.2)
    assert controller.events[-1] == "relay_cmd_on"
    assert "send_first_complete_zero_frame" not in controller.events

    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.205,
    )
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.206,
    )
    assert controller.events[-2:] == [
        "feedback_emulate_stable",
        "send_first_complete_zero_frame",
    ]


def test_health_gated_fault_recovery_entry() -> None:
    owner = identity()

    def faulted(feedback: Feedback, *, restore_bypass: bool) -> Controller:
        controller = connected_controller(owner)
        pins = {
            Feedback.BOTH_OPEN: (True, True),
            Feedback.BOTH_CLOSED: (False, False),
            Feedback.EMULATE: (True, False),
        }
        nc_high, no_high = pins[feedback]
        controller.observe_relay_feedback(
            nc_high=nc_high,
            no_high=no_high,
            now=0.000_100,
        )
        assert controller.fault_latched
        if feedback is Feedback.BOTH_CLOSED:
            assert controller.acquire(owner, now=0.000_150)
        if restore_bypass:
            controller.observe_relay_feedback(
                nc_high=False,
                no_high=True,
                now=0.000_200,
            )
        return controller

    def assert_safe_rejection(
        controller: Controller,
        event: str,
        *,
        now: float,
        uart_idle_low: bool = True,
    ) -> None:
        assert not controller.request_emulate_recovering(
            owner,
            now=now,
            uart_idle_low=uart_idle_low,
        )
        assert controller.events[-1] == event
        assert controller.fault_latched
        assert controller.speed_tenths == 0
        assert controller.incline_half_percent == 0
        assert controller.mode is Mode.PROXY
        assert not controller.relay_cmd
        assert not controller.tx_enable

    # Ordinary/background entry must remain unable to clear a recoverable
    # latch even after every health input has qualified.
    ordinary = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    ordinary.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert not ordinary.request_emulate(
        owner,
        now=0.001_200,
        uart_idle_low=True,
    )
    assert ordinary.events[-1] == "entry_rejected:fault_latched"
    assert ordinary.fault_latched

    # The first Bypass sample starts qualification. One microsecond before
    # the full interval is insufficient.
    early = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    early.observe_console_bytes(b"[hmph:0000]", now=0.001_199)
    assert_safe_rejection(
        early,
        "recovery_rejected:feedback_not_qualified_bypass",
        now=0.001_199,
    )

    # A non-Bypass sample restarts the continuous qualification interval.
    interrupted = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    interrupted.observe_relay_feedback(
        nc_high=True,
        no_high=True,
        now=0.000_900,
    )
    interrupted.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.001_000,
    )
    interrupted.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert_safe_rejection(
        interrupted,
        "recovery_rejected:feedback_not_qualified_bypass",
        now=0.001_200,
    )

    # Every unhealthy feedback encoding fails closed.
    for feedback in (Feedback.BOTH_OPEN, Feedback.BOTH_CLOSED, Feedback.EMULATE):
        unhealthy = faulted(feedback, restore_bypass=False)
        unhealthy.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
        assert_safe_rejection(
            unhealthy,
            "recovery_rejected:feedback_not_qualified_bypass",
            now=0.001_200,
        )

    tread = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    tread.set_tread_ok(False, now=0.001_100)
    tread.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert_safe_rejection(
        tread,
        "recovery_rejected:tread_not_ok",
        now=0.001_200,
    )

    stale = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    stale.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert_safe_rejection(
        stale,
        "recovery_rejected:console_not_fresh",
        now=2.0,
    )

    busy = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    busy.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert_safe_rejection(
        busy,
        "recovery_rejected:uart_not_idle_low",
        now=0.001_200,
        uart_idle_low=False,
    )

    # A latched reset-class stop must discard pre-reset Bypass history.
    reset = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    reset.reset(now=0.001_100)
    fresh_owner = identity(generation=2)
    assert reset.connect(fresh_owner)
    assert reset.acquire(fresh_owner, now=0.001_150)
    reset.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.001_200,
    )
    reset.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert not reset.request_emulate_recovering(
        fresh_owner,
        now=0.001_200,
        uart_idle_low=True,
    )
    assert reset.events[-1] == (
        "recovery_rejected:feedback_not_qualified_bypass"
    )
    assert reset.fault_latched

    # The not-Proxy/relay-on/TX-on states are reachable only while no fault is
    # latched: every fault path releases both outputs and returns to Proxy.
    active = connected_controller(owner)
    active.observe_console_bytes(b"[hmph:0000]", now=0.001)
    assert active.request_emulate(owner, now=0.001, uart_idle_low=True)
    assert active.tx_enable and not active.relay_cmd
    assert not active.request_emulate_recovering(
        owner,
        now=0.001_100,
        uart_idle_low=True,
    )
    assert active.events[-1] == "recovery_rejected:not_proxy"
    assert active.observe_interframe_gap(now=0.001_200)
    assert active.relay_cmd and active.tx_enable
    assert not active.request_emulate_recovering(
        owner,
        now=0.001_300,
        uart_idle_low=True,
    )
    assert active.events[-1] == "recovery_rejected:not_proxy"

    # Exact qualification deadline is accepted atomically. The acceptance
    # marker precedes the ordinary entry sequence in the same call.
    recovered = faulted(Feedback.BOTH_OPEN, restore_bypass=True)
    recovered.observe_console_bytes(b"[hmph:0000]", now=0.001_200)
    assert recovered.request_emulate_recovering(
        owner,
        now=0.001_200,
        uart_idle_low=True,
    )
    assert not recovered.fault_latched
    assert recovered.mode is Mode.ENTRY_WAIT_GAP
    assert not recovered.relay_cmd
    assert recovered.tx_enable
    assert recovered.events[-6:] == [
        "fault_recovery_accepted",
        "command_zero",
        "configure_inverted_uart",
        "verify_physical_idle_low",
        "tx_enable_on",
        "wait_entry_gap",
    ]


@pytest.mark.parametrize(
    ("setup", "event"),
    (
        ("tread_not_ok", "entry_rejected:tread_not_ok"),
        ("feedback_not_bypass", "entry_rejected:feedback_not_bypass"),
        ("console_unknown", "entry_rejected:console_not_fresh"),
        ("console_stale", "entry_rejected:console_not_fresh"),
        ("latched_fault", "entry_rejected:fault_latched"),
        ("idle_not_low", "entry_rejected:uart_not_idle_low"),
    ),
)
def test_entry_preconditions(setup: str, event: str) -> None:
    owner = identity()
    controller = connected_controller(owner)
    if setup != "console_unknown":
        controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    if setup == "tread_not_ok":
        controller.tread_ok = False
    elif setup == "feedback_not_bypass":
        controller.feedback = Feedback.BOTH_OPEN
    elif setup == "console_stale":
        pass
    elif setup == "latched_fault":
        controller.fault_latched = True

    now = 2.0 if setup == "console_stale" else 0.5
    assert not controller.request_emulate(
        owner,
        now=now,
        uart_idle_low=setup != "idle_not_low",
    )
    assert controller.events[-1] == event
    assert not controller.relay_cmd


def test_entry_gap_timeout_aborts_without_moving_relay() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)

    controller.tick(now=1.0)
    assert controller.mode is Mode.PROXY
    assert not controller.relay_cmd
    assert "relay_cmd_on" not in controller.events
    assert controller.events[-1] == "entry_abort:no_gap"


def test_gap_event_at_entry_deadline_cannot_leave_tx_enabled() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    controller.observe_console_bytes(b"[loop:5550]", now=0.99)

    assert not controller.observe_interframe_gap(now=1.0)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable


def test_reentrant_entry_request_cannot_rewind_an_active_transfer() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    assert controller.relay_cmd

    assert not controller.request_emulate(
        owner,
        now=0.105,
        uart_idle_low=True,
    )
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.tick(now=0.110)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable


def test_reentrant_entry_request_enforces_feedback_deadline_first() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)

    assert not controller.request_emulate(
        owner,
        now=0.110,
        uart_idle_low=True,
    )
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert controller.events[-1] == "emergency:entry_feedback_timeout"


def test_entry_feedback_timeout_releases_and_latches_fault() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.2)

    controller.tick(now=0.210)
    assert controller.mode is Mode.PROXY
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert controller.events[-1] == "emergency:entry_feedback_timeout"


@pytest.mark.parametrize(
    ("nc_high", "no_high"),
    ((False, True), (True, True)),
)
def test_entry_feedback_mismatch_releases_and_latches_fault(
    nc_high: bool,
    no_high: bool,
) -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.2)

    controller.observe_relay_feedback(
        nc_high=nc_high,
        no_high=no_high,
        now=0.205,
    )
    controller.tick(now=0.210)
    assert controller.mode is Mode.PROXY
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert controller.events[-1] == "emergency:entry_feedback_timeout"


def test_complete_normal_exit_order() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    assert controller.request_normal_exit(owner, now=0.5)
    assert controller.events[-2:] == [
        "send_and_finish_complete_zero_frame",
        "wait_exit_gap",
    ]
    assert controller.relay_cmd
    assert controller.observe_interframe_gap(now=0.7)
    assert controller.events[-1] == "relay_cmd_off"
    assert controller.tx_enable

    controller.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.705,
    )
    assert controller.mode is Mode.EXIT_WAIT_FEEDBACK
    controller.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.706,
    )
    assert controller.events[-3:] == [
        "feedback_bypass_stable",
        "tx_enable_off",
        "lease_released",
    ]
    assert controller.mode is Mode.PROXY
    assert controller.owner is None


def test_normal_exit_gap_timeout_bypasses_immediately_then_checks_feedback() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.request_normal_exit(owner, now=0.5)
    controller.observe_console_bytes(b"[loop:5550]", now=1.49)

    controller.tick(now=1.5)
    assert not controller.relay_cmd
    assert controller.mode is Mode.EXIT_WAIT_FEEDBACK
    assert controller.events[-2:] == ["exit_gap_timeout", "relay_cmd_off"]


def test_gap_event_at_exit_deadline_cannot_leave_relay_energized() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.request_normal_exit(owner, now=0.5)
    controller.observe_console_bytes(b"[loop:5550]", now=1.49)

    assert not controller.observe_interframe_gap(now=1.5)
    assert controller.mode is Mode.EXIT_WAIT_FEEDBACK
    assert not controller.relay_cmd
    assert controller.tx_enable


@pytest.mark.parametrize(
    ("nc_high", "no_high"),
    ((True, False), (True, True)),
)
def test_exit_feedback_mismatch_releases_and_latches_fault(
    nc_high: bool,
    no_high: bool,
) -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.request_normal_exit(owner, now=0.5)
    assert controller.observe_interframe_gap(now=0.7)

    controller.observe_relay_feedback(
        nc_high=nc_high,
        no_high=no_high,
        now=0.705,
    )
    controller.tick(now=0.710)
    assert controller.mode is Mode.PROXY
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert controller.events[-1] == "emergency:exit_feedback_timeout"


def test_exit_feedback_timeout_latches_fault() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.request_normal_exit(owner, now=0.5)
    assert controller.observe_interframe_gap(now=0.7)

    controller.tick(now=0.710)
    assert controller.mode is Mode.PROXY
    assert controller.fault_latched
    assert controller.events[-1] == "emergency:exit_feedback_timeout"


@pytest.mark.parametrize(
    "mode",
    (
        Mode.ENTRY_WAIT_GAP,
        Mode.ENTRY_WAIT_FEEDBACK,
        Mode.EXIT_WAIT_GAP,
        Mode.EXIT_WAIT_FEEDBACK,
    ),
)
def test_console_staleness_never_waits_for_a_transition_deadline(
    mode: Mode,
) -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    entry_mode = mode in {Mode.ENTRY_WAIT_GAP, Mode.ENTRY_WAIT_FEEDBACK}
    entry_time = 1.49 if entry_mode else 0.0
    assert controller.request_emulate(
        owner,
        now=entry_time,
        uart_idle_low=True,
    )
    if mode is Mode.ENTRY_WAIT_FEEDBACK:
        assert controller.observe_interframe_gap(now=1.495)
    if mode in {Mode.EXIT_WAIT_GAP, Mode.EXIT_WAIT_FEEDBACK}:
        assert controller.observe_interframe_gap(now=0.1)
        controller.observe_relay_feedback(
            nc_high=True,
            no_high=False,
            now=0.105,
        )
        controller.observe_relay_feedback(
            nc_high=True,
            no_high=False,
            now=0.106,
        )
        assert controller.request_normal_exit(owner, now=0.6)
    if mode is Mode.EXIT_WAIT_FEEDBACK:
        assert controller.observe_interframe_gap(now=1.495)
    assert controller.mode is mode

    controller.tick(now=1.5)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert controller.events[-1] == "emergency:console_stale"


def test_stale_console_cannot_be_raced_by_a_gap_observation() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=1.49, uart_idle_low=True)

    assert not controller.observe_interframe_gap(now=1.5)
    assert controller.mode is Mode.PROXY
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert controller.events[-1] == "emergency:console_stale"


def test_matching_feedback_requires_temporal_stability() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)

    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.101,
    )
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    assert "send_first_complete_zero_frame" not in controller.events
    controller.tick(now=0.101999)
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.tick(now=0.102)
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.102,
    )
    assert controller.mode is Mode.EMULATING
    assert controller.events[-2:] == [
        "feedback_emulate_stable",
        "send_first_complete_zero_frame",
    ]


def test_transition_feedback_may_pass_through_both_open_before_settling() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)

    controller.observe_relay_feedback(
        nc_high=True,
        no_high=True,
        now=0.101,
    )
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    assert not controller.fault_latched
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.105,
    )
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.106,
    )
    assert controller.mode is Mode.EMULATING
    assert not controller.fault_latched


def test_both_closed_feedback_faults_immediately_during_transfer() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)

    controller.observe_relay_feedback(
        nc_high=False,
        no_high=False,
        now=0.101,
    )

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert controller.events[-1] == "emergency:relay_feedback_both_closed"


def test_feedback_qualification_requires_a_sample_at_stability_time() -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.105,
    )

    controller.tick(now=0.106)
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.106,
    )
    assert controller.mode is Mode.EMULATING


@pytest.mark.parametrize("deadline_method", ("tick", "feedback"))
def test_feedback_at_exact_deadline_always_fails_closed(
    deadline_method: str,
) -> None:
    owner = identity()
    controller = connected_controller(owner)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.0)
    assert controller.request_emulate(owner, now=0.0, uart_idle_low=True)
    assert controller.observe_interframe_gap(now=0.1)
    controller.observe_relay_feedback(
        nc_high=True,
        no_high=False,
        now=0.108,
    )

    if deadline_method == "tick":
        controller.tick(now=0.110)
    else:
        controller.observe_relay_feedback(
            nc_high=True,
            no_high=False,
            now=0.110,
        )

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.fault_latched
    assert not controller.relay_cmd
    assert not controller.tx_enable


@pytest.mark.parametrize("failure", ("brownout", "watchdog"))
def test_console_bridge_failure_matrix_remains_hardware_proxy(
    failure: str,
) -> None:
    controller = Controller()
    if failure == "brownout":
        controller.reset(now=1.0, reason="brownout")
    else:
        controller.watchdog_stall(now=1.0)

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable


@pytest.mark.parametrize(
    "reason",
    (
        "tread_not_ok",
        "console_stale",
        "explicit_emergency_stop",
        "brownout",
        "reset",
        "watchdog",
    ),
)
def test_emergency_paths_never_wait_for_a_gap(reason: str) -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)
    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=4,
        now=0.2,
    )
    before = len(controller.events)

    controller.emergency_stop(reason=reason, now=0.5)

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0
    assert not controller.relay_cmd
    assert not controller.tx_enable
    assert not any(
        "wait" in event for event in controller.events[before:]
    )


@pytest.mark.parametrize(
    ("nc_high", "no_high", "feedback"),
    (
        (False, True, Feedback.BYPASS),
        (True, False, Feedback.EMULATE),
        (False, False, Feedback.BOTH_CLOSED),
        (True, True, Feedback.BOTH_OPEN),
    ),
)
def test_all_four_relay_feedback_states_are_decoded(
    nc_high: bool,
    no_high: bool,
    feedback: Feedback,
) -> None:
    assert Feedback.from_gpio(nc_high, no_high) is feedback


@pytest.mark.parametrize(
    ("nc_high", "no_high"),
    ((False, True), (False, False), (True, True)),
)
def test_any_non_emulate_feedback_while_emulating_is_a_fault(
    nc_high: bool,
    no_high: bool,
) -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    controller.observe_relay_feedback(
        nc_high=nc_high,
        no_high=no_high,
        now=0.5,
    )
    assert controller.mode is Mode.PROXY
    assert controller.fault_latched


def test_tread_ok_loss_is_hardware_permission_loss_and_immediate() -> None:
    owner = identity()
    controller = connected_controller(owner)
    enter_emulate(controller, owner)

    controller.set_tread_ok(False, now=0.5)
    assert controller.mode is Mode.PROXY
    assert not controller.relay_cmd
    assert controller.events[-1] == "emergency:tread_not_ok"


def test_native_usb_attach_is_active_low_and_defaults_detached() -> None:
    controller = Controller()
    assert not controller.usb_pullup_enabled

    controller.set_vbus_present_n(True)
    assert not controller.usb_pullup_enabled
    controller.set_vbus_present_n(False)
    assert controller.usb_pullup_enabled
    controller.set_vbus_present_n(True)
    assert not controller.usb_pullup_enabled


def test_reset_requires_an_actual_bypass_feedback_sample_before_entry() -> None:
    old = identity()
    controller = connected_controller(old)
    enter_emulate(controller, old)
    controller.reset(now=0.5)

    assert controller.feedback is Feedback.UNKNOWN
    fresh = identity(generation=2)
    assert controller.connect(fresh)
    assert controller.acquire(fresh, now=0.6)
    controller.observe_console_bytes(b"[hmph:0000]", now=0.6)
    assert not controller.request_emulate(
        fresh,
        now=0.6,
        uart_idle_low=True,
    )
    controller.observe_relay_feedback(
        nc_high=False,
        no_high=True,
        now=0.7,
    )
    assert controller.request_emulate(
        fresh,
        now=0.7,
        uart_idle_low=True,
    )


def test_model_constants_are_the_normative_deadlines() -> None:
    assert Controller.CONSOLE_FRESH_SECONDS == 1.5
    assert Controller.TRANSFER_GAP_DEADLINE_SECONDS == 1.0
    assert Controller.RELAY_FEEDBACK_DEADLINE_SECONDS == 0.010
    assert Controller.RELAY_FEEDBACK_STABLE_SECONDS == 0.001
    assert Controller.WDT_SECONDS == 2.0
    assert Controller.TREAD_OK_TO_NC_MAX_SECONDS == 0.010
    assert Controller.SOFTWARE_TO_NC_MAX_SECONDS == 0.250
    assert Controller.WDT_TO_NC_MAX_SECONDS == 2.25
    assert Controller.NORMAL_TRANSITION_ACCEPTANCE_CYCLES == 1_000


def _safe_sdkconfig() -> str:
    return """\
CONFIG_IDF_TARGET="esp32s3"
CONFIG_IDF_TARGET_ARCH_XTENSA=y
CONFIG_IDF_TARGET_ESP32S3=y
CONFIG_ESP_TASK_WDT_EN=y
CONFIG_ESP_TASK_WDT_INIT=y
CONFIG_ESP_TASK_WDT_TIMEOUT_S=2
CONFIG_ESP_TASK_WDT_PANIC=y
CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y
CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0
CONFIG_ESP_BROWNOUT_DET=y
CONFIG_ESP_BROWNOUT_DET_LVL_SEL_3=y
CONFIG_ESP_BROWNOUT_DET_LVL=3
CONFIG_ESP_COREDUMP_ENABLE_TO_NONE=y
CONFIG_APPTRACE_DEST_NONE=y
CONFIG_APPTRACE_DEST_UART_NONE=y
CONFIG_LOG_MAXIMUM_LEVEL=3
# CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT is not set
# CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT is not set
# CONFIG_ESP_SYSTEM_PANIC_GDBSTUB is not set
# CONFIG_ESP_SYSTEM_GDBSTUB_RUNTIME is not set
# CONFIG_ESP_DEBUG_OCDAWARE is not set
# CONFIG_ESP_DEBUG_STUBS_ENABLE is not set
"""


def _esp_image_payload(label: str) -> bytes:
    return b"\xe9\x01" + (b"\0" * 22) + label.encode("ascii")


def _partition_table_payload() -> bytes:
    return b"\xaa\x50" + (b"\xff" * (0xC00 - 2))


def _manifest_inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "application": tmp_path / "app.bin",
        "bootloader": tmp_path / "bootloader.bin",
        "partition_table": tmp_path / "partition-table.bin",
        "sdkconfig": tmp_path / "sdkconfig",
    }
    for label, path in paths.items():
        payload = {
            "application": _esp_image_payload("application"),
            "bootloader": _esp_image_payload("bootloader"),
            "partition_table": _partition_table_payload(),
            "sdkconfig": _safe_sdkconfig().encode("utf-8"),
        }[label]
        path.write_bytes(payload)
    return paths


def _run_manifest(
    tmp_path: Path,
    paths: dict[str, Path],
    *,
    output_name: str = "safety-manifest.json",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(FIRMWARE_DIR / "build_safety_manifest.py"),
            "--application",
            str(paths["application"]),
            "--bootloader",
            str(paths["bootloader"]),
            "--partition-table",
            str(paths["partition_table"]),
            "--sdkconfig",
            str(paths["sdkconfig"]),
            "--measured-min-3v3",
            "3.05",
            "--brownout-threshold",
            "2.98",
            "--output",
            str(tmp_path / output_name),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_safety_manifest_is_deterministic_and_hashes_every_artifact(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    first = _run_manifest(tmp_path, paths)
    assert first.returncode == 0, first.stderr
    output = tmp_path / "safety-manifest.json"
    first_bytes = output.read_bytes()
    second = _run_manifest(tmp_path, paths)
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == first_bytes

    manifest = json.loads(first_bytes)
    assert manifest["schema_version"] == 1
    assert manifest["safety_contract"] == {
        "console_fresh_seconds": 1.5,
        "manual_lease_seconds": None,
        "normal_transition_acceptance_cycles": 1000,
        "relay_feedback_seconds": 0.01,
        "relay_feedback_stable_seconds": 0.001,
        "software_to_nc_max_seconds": 0.25,
        "tread_ok_to_nc_max_seconds": 0.01,
        "transfer_gap_seconds": 1.0,
        "watchdog_seconds": 2.0,
        "watchdog_to_nc_max_seconds": 2.25,
    }
    assert set(manifest["artifacts"]) == {
        "application",
        "bootloader",
        "partition_table",
        "sdkconfig",
        "safety_model",
        "safety_builder",
        "safety_schema",
        "firmware_plan",
    }
    for label, entry in manifest["artifacts"].items():
        artifact = (
            paths[label]
            if label in paths
            else {
                "safety_model": FIRMWARE_DIR / "safety_model.py",
                "safety_builder": FIRMWARE_DIR / "build_safety_manifest.py",
                "safety_schema": FIRMWARE_DIR / "safety_manifest.schema.json",
                "firmware_plan": FIRMWARE_DIR / "PLAN.md",
            }[label]
        )
        assert entry["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert entry["size"] == artifact.stat().st_size
    assert len(manifest["contract_manifest_sha256"]) == 64
    assert len(manifest["bundle_sha256"]) == 64


def test_manifest_accepts_generated_silent_reboot_without_delay_key(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    sdkconfig = paths["sdkconfig"]
    sdkconfig.write_text(
        sdkconfig.read_text(encoding="utf-8").replace(
            "CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0\n",
            "",
        ),
        encoding="utf-8",
    )

    result = _run_manifest(tmp_path, paths)

    assert result.returncode == 0, result.stderr


def test_bundle_digest_depends_on_every_artifact_record(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    result = _run_manifest(tmp_path, paths)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "safety-manifest.json").read_bytes())
    baseline = manifest["bundle_sha256"]

    for label in manifest["artifacts"]:
        changed = copy.deepcopy(manifest)
        changed["artifacts"][label]["sha256"] = "0" * 64
        if label in manifest_builder.CONTRACT_ARTIFACTS:
            changed["contract_manifest_sha256"] = manifest_builder._digest(
                manifest_builder._contract_payload(changed)
            )
        assert (
            manifest_builder._digest(
                manifest_builder._bundle_payload(changed)
            )
            != baseline
        ), label


@pytest.mark.parametrize(
    "unsafe_change",
    (
        ("CONFIG_ESP_TASK_WDT_EN=y", "# CONFIG_ESP_TASK_WDT_EN is not set"),
        ("CONFIG_ESP_TASK_WDT_INIT=y", "# CONFIG_ESP_TASK_WDT_INIT is not set"),
        ("CONFIG_ESP_TASK_WDT_TIMEOUT_S=2", "CONFIG_ESP_TASK_WDT_TIMEOUT_S=3"),
        ("CONFIG_ESP_TASK_WDT_PANIC=y", "# CONFIG_ESP_TASK_WDT_PANIC is not set"),
        ("CONFIG_ESP_BROWNOUT_DET=y", "# CONFIG_ESP_BROWNOUT_DET is not set"),
        (
            "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_3=y",
            "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_7=y",
        ),
        (
            "# CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT is not set",
            "CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT=y",
        ),
        (
            "# CONFIG_ESP_SYSTEM_PANIC_GDBSTUB is not set",
            "CONFIG_ESP_SYSTEM_PANIC_GDBSTUB=y",
        ),
        (
            "# CONFIG_ESP_SYSTEM_GDBSTUB_RUNTIME is not set",
            "CONFIG_ESP_SYSTEM_GDBSTUB_RUNTIME=y",
        ),
        (
            "# CONFIG_ESP_DEBUG_OCDAWARE is not set",
            "CONFIG_ESP_DEBUG_OCDAWARE=y",
        ),
        (
            "# CONFIG_ESP_DEBUG_STUBS_ENABLE is not set",
            "CONFIG_ESP_DEBUG_STUBS_ENABLE=y",
        ),
        (
            "CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0",
            "CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=99",
        ),
        (
            "CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y",
            "# CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT is not set",
        ),
    ),
)
def test_manifest_rejects_unsafe_sdkconfig(
    tmp_path: Path,
    unsafe_change: tuple[str, str],
) -> None:
    paths = _manifest_inputs(tmp_path)
    sdkconfig = paths["sdkconfig"]
    sdkconfig.write_text(
        sdkconfig.read_text(encoding="utf-8").replace(*unsafe_change),
        encoding="utf-8",
    )

    result = _run_manifest(tmp_path, paths)
    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()


@pytest.mark.parametrize(
    "unsafe_change",
    (
        (
            'CONFIG_IDF_TARGET="esp32s3"',
            'CONFIG_IDF_TARGET="esp32"',
        ),
        (
            "CONFIG_IDF_TARGET_ESP32S3=y",
            "CONFIG_IDF_TARGET_ESP32=y",
        ),
        (
            "CONFIG_ESP_COREDUMP_ENABLE_TO_NONE=y",
            "CONFIG_ESP_COREDUMP_ENABLE_TO_UART=y\n"
            "CONFIG_ESP_COREDUMP_UART_DELAY=5000",
        ),
        (
            "CONFIG_APPTRACE_DEST_NONE=y",
            "CONFIG_APPTRACE_DEST_JTAG=y\n"
            "CONFIG_APPTRACE_ONPANIC_HOST_FLUSH_TMO=-1",
        ),
    ),
)
def test_manifest_rejects_wrong_target_or_delayed_panic_paths(
    tmp_path: Path,
    unsafe_change: tuple[str, str],
) -> None:
    paths = _manifest_inputs(tmp_path)
    sdkconfig = paths["sdkconfig"]
    sdkconfig.write_text(
        sdkconfig.read_text(encoding="utf-8").replace(*unsafe_change),
        encoding="utf-8",
    )

    result = _run_manifest(tmp_path, paths)

    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()


@pytest.mark.parametrize(
    ("label", "wrong_payload"),
    (
        ("application", _partition_table_payload()),
        ("bootloader", _partition_table_payload()),
        ("partition_table", _esp_image_payload("not-a-partition-table")),
    ),
)
def test_manifest_rejects_artifact_with_wrong_binary_role(
    tmp_path: Path,
    label: str,
    wrong_payload: bytes,
) -> None:
    paths = _manifest_inputs(tmp_path)
    paths[label].write_bytes(wrong_payload)

    result = _run_manifest(tmp_path, paths)

    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()


def test_manifest_refuses_hardlinked_input_identities(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    paths["bootloader"].unlink()
    os.link(paths["application"], paths["bootloader"])

    result = _run_manifest(tmp_path, paths)

    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()


@pytest.mark.parametrize(
    "input_label",
    ("application", "bootloader", "partition_table", "sdkconfig"),
)
def test_manifest_refuses_output_aliasing_an_input(
    tmp_path: Path,
    input_label: str,
) -> None:
    paths = _manifest_inputs(tmp_path)
    before = paths[input_label].read_bytes()

    result = _run_manifest(
        tmp_path,
        paths,
        output_name=paths[input_label].name,
    )

    assert result.returncode != 0
    assert paths[input_label].read_bytes() == before


def test_manifest_refuses_output_hardlink_aliasing_an_input(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    output = tmp_path / "safety-manifest.json"
    os.link(paths["application"], output)
    before = paths["application"].read_bytes()

    result = _run_manifest(tmp_path, paths)

    assert result.returncode != 0
    assert paths["application"].read_bytes() == before
    assert output.read_bytes() == before


def test_manifest_snapshots_sdkconfig_once(
    tmp_path: Path,
) -> None:
    class ChangingSdkconfig:
        name = "sdkconfig"

        def __init__(self) -> None:
            self.read_count = 0

        def read_bytes(self) -> bytes:
            self.read_count += 1
            if self.read_count == 1:
                return _safe_sdkconfig().encode("utf-8")
            return _safe_sdkconfig().replace(
                "CONFIG_ESP_TASK_WDT_PANIC=y",
                "# CONFIG_ESP_TASK_WDT_PANIC is not set",
            ).encode("utf-8")

        def __str__(self) -> str:
            return "<changing-sdkconfig>"

    paths = _manifest_inputs(tmp_path)
    sdkconfig = ChangingSdkconfig()
    manifest = manifest_builder.build_manifest(
        application=paths["application"],
        bootloader=paths["bootloader"],
        partition_table=paths["partition_table"],
        sdkconfig=sdkconfig,
        measured_min_3v3=3.05,
        brownout_threshold=2.98,
    )

    assert sdkconfig.read_count == 1
    assert manifest["artifacts"]["sdkconfig"]["sha256"] == hashlib.sha256(
        _safe_sdkconfig().encode("utf-8")
    ).hexdigest()


def test_manifest_snapshots_schema_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_schema = (
        FIRMWARE_DIR / "safety_manifest.schema.json"
    ).read_bytes()

    class ChangingSchema:
        name = "safety_manifest.schema.json"

        def __init__(self) -> None:
            self.read_count = 0

        def read_bytes(self) -> bytes:
            self.read_count += 1
            return original_schema

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            self.read_count += 1
            return "{}"

        def __str__(self) -> str:
            return "<changing-schema>"

    paths = _manifest_inputs(tmp_path)
    schema = ChangingSchema()
    monkeypatch.setattr(manifest_builder, "SCHEMA_PATH", schema)

    manifest = manifest_builder.build_manifest(
        application=paths["application"],
        bootloader=paths["bootloader"],
        partition_table=paths["partition_table"],
        sdkconfig=paths["sdkconfig"],
        measured_min_3v3=3.05,
        brownout_threshold=2.98,
    )

    assert schema.read_count == 1
    assert manifest["artifacts"]["safety_schema"]["sha256"] == hashlib.sha256(
        original_schema
    ).hexdigest()


def test_manifest_rejects_missing_artifact_and_bad_brownout_evidence(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    paths["application"].unlink()
    result = _run_manifest(tmp_path, paths)
    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()

    paths = _manifest_inputs(tmp_path)
    command = [
        sys.executable,
        str(FIRMWARE_DIR / "build_safety_manifest.py"),
        "--application",
        str(paths["application"]),
        "--bootloader",
        str(paths["bootloader"]),
        "--partition-table",
        str(paths["partition_table"]),
        "--sdkconfig",
        str(paths["sdkconfig"]),
        "--measured-min-3v3",
        "2.75",
        "--brownout-threshold",
        "2.98",
        "--output",
        str(tmp_path / "safety-manifest.json"),
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / "safety-manifest.json").exists()


def test_manifest_schema_rejects_tampering(tmp_path: Path) -> None:
    paths = _manifest_inputs(tmp_path)
    result = _run_manifest(tmp_path, paths)
    assert result.returncode == 0, result.stderr
    manifest_path = tmp_path / "safety-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extra"] = "not permitted"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    validate = subprocess.run(
        [
            sys.executable,
            str(FIRMWARE_DIR / "build_safety_manifest.py"),
            "--validate",
            str(manifest_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode != 0


def test_manifest_bundle_hash_rejects_metadata_tampering(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    result = _run_manifest(tmp_path, paths)
    assert result.returncode == 0, result.stderr
    manifest_path = tmp_path / "safety-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["application"]["size"] += 1
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    validate = subprocess.run(
        [
            sys.executable,
            str(FIRMWARE_DIR / "build_safety_manifest.py"),
            "--validate",
            str(manifest_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode != 0
    assert "bundle_sha256 does not match" in validate.stderr


def test_manifest_validation_rejects_rehashed_unsafe_power_evidence(
    tmp_path: Path,
) -> None:
    paths = _manifest_inputs(tmp_path)
    result = _run_manifest(tmp_path, paths)
    assert result.returncode == 0, result.stderr
    manifest_path = tmp_path / "safety-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["power_evidence"] = {
        "measured_min_3v3_volts": 2.75,
        "brownout_threshold_volts": 3.30,
        "brownout_selector": "CONFIG_ESP_BROWNOUT_DET_LVL_SEL_1",
    }
    manifest["contract_manifest_sha256"] = manifest_builder._digest(
        manifest_builder._contract_payload(manifest)
    )
    manifest["bundle_sha256"] = manifest_builder._digest(
        manifest_builder._bundle_payload(manifest)
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    validate = subprocess.run(
        [
            sys.executable,
            str(FIRMWARE_DIR / "build_safety_manifest.py"),
            "--validate",
            str(manifest_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode != 0
    assert "brownout threshold must be below" in validate.stderr
