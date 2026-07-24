from __future__ import annotations

import copy
import hashlib
import json
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
    controller.tick(now=now + 0.106)
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


def test_only_owner_mutates_or_renews_the_single_four_second_lease() -> None:
    owner = identity()
    other = identity(handle="socket-b")
    controller = connected_controller(owner)
    assert controller.connect(other)

    assert controller.command_motion(
        owner,
        speed_tenths=30,
        incline_half_percent=4,
        now=1.0,
    )
    assert controller.lease_expires_at == pytest.approx(5.0)
    assert not controller.command_motion(
        other,
        speed_tenths=90,
        incline_half_percent=8,
        now=2.0,
    )
    assert not controller.heartbeat(other, now=3.9)
    assert controller.lease_expires_at == pytest.approx(5.0)
    assert controller.heartbeat(owner, now=4.0)
    assert controller.lease_expires_at == pytest.approx(8.0)

    controller.tick(now=7.999)
    assert controller.owner == owner
    controller.tick(now=8.0)
    assert controller.owner is None
    assert controller.mode is Mode.PROXY
    assert controller.speed_tenths == 0
    assert controller.incline_half_percent == 0
    assert not controller.relay_cmd


def test_manual_lease_cannot_be_renewed_at_or_after_its_deadline() -> None:
    owner = identity()
    controller = connected_controller(owner)

    assert not controller.heartbeat(owner, now=4.0)
    assert controller.owner is None
    assert controller.lease_expires_at is None
    assert controller.mode is Mode.PROXY
    assert controller.events[-1] == "emergency:lease_expired"


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

    assert not controller.disconnect(other, now=0.5)
    assert controller.mode is Mode.EMULATING
    assert controller.disconnect(owner, now=0.6)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
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


def test_executor_owns_locally_and_network_loss_does_not_renew_or_end_it() -> None:
    executor = identity(Transport.EXECUTOR, "program-17", 3)
    wss = identity()
    controller = connected_controller(executor)
    assert controller.connect(wss)
    enter_emulate(controller, executor)

    assert controller.lease_expires_at is None
    assert not controller.heartbeat(wss, now=100.0)
    assert not controller.disconnect(wss, now=100.1)
    controller.observe_console_bytes(b"[loop:5550]", now=9_999.9)
    controller.tick(now=10_000.0)
    assert controller.owner == executor
    assert controller.mode is Mode.EMULATING


@pytest.mark.parametrize(
    ("source", "failure", "must_proxy"),
    (
        (Transport.WSS, "silence", True),
        (Transport.WSS, "wss_drop", True),
        (Transport.WSS, "ble_drop", False),
        (Transport.BLE, "silence", True),
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
        controller.observe_console_bytes(b"[loop:5550]", now=3.9)
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

    if failure == "reset":
        controller.reset(now=1.0, reason="brownout")
    else:
        controller.watchdog_stall(now=1.0)

    assert controller.mode is Mode.PROXY
    assert controller.owner is None
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
    assert controller.feedback is Feedback.BYPASS
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


@pytest.mark.parametrize("age", (1.500001, 20.0))
def test_stale_console_forces_immediate_zero_and_bypass(age: float) -> None:
    owner = (
        identity()
        if age < Controller.MANUAL_LEASE_SECONDS
        else identity(Transport.EXECUTOR, "program-stale", 1)
    )
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
    controller.tick(now=0.206)
    assert controller.events[-2:] == [
        "feedback_emulate_stable",
        "send_first_complete_zero_frame",
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
        now=0.2,
        uart_idle_low=True,
    )
    assert controller.mode is Mode.ENTRY_WAIT_FEEDBACK
    controller.tick(now=0.210)
    assert controller.mode is Mode.PROXY
    assert controller.owner is None
    assert not controller.relay_cmd
    assert not controller.tx_enable


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
    ((False, True), (False, False), (True, True)),
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
    controller.tick(now=0.706)
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
    ((True, False), (False, False), (True, True)),
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
        controller.tick(now=0.106)
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
    controller.tick(now=0.106)
    assert controller.mode is Mode.EMULATING
    assert not controller.fault_latched


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
        "lease_expired",
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
    before = len(controller.events)

    controller.emergency_stop(reason=reason, now=0.5)

    assert controller.mode is Mode.PROXY
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


def test_model_constants_are_the_normative_deadlines() -> None:
    assert Controller.MANUAL_LEASE_SECONDS == 4.0
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
CONFIG_ESP_TASK_WDT_EN=y
CONFIG_ESP_TASK_WDT_INIT=y
CONFIG_ESP_TASK_WDT_TIMEOUT_S=2
CONFIG_ESP_TASK_WDT_PANIC=y
CONFIG_ESP_SYSTEM_PANIC_SILENT_REBOOT=y
CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0
CONFIG_ESP_BROWNOUT_DET=y
CONFIG_ESP_BROWNOUT_DET_LVL_SEL_3=y
CONFIG_ESP_BROWNOUT_DET_LVL=3
# CONFIG_ESP_SYSTEM_PANIC_PRINT_HALT is not set
# CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT is not set
# CONFIG_ESP_SYSTEM_PANIC_GDBSTUB is not set
# CONFIG_ESP_SYSTEM_GDBSTUB_RUNTIME is not set
# CONFIG_ESP_DEBUG_OCDAWARE is not set
# CONFIG_ESP_DEBUG_STUBS_ENABLE is not set
"""


def _manifest_inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "application": tmp_path / "app.bin",
        "bootloader": tmp_path / "bootloader.bin",
        "partition_table": tmp_path / "partition-table.bin",
        "sdkconfig": tmp_path / "sdkconfig",
    }
    for label, path in paths.items():
        path.write_bytes(
            _safe_sdkconfig().encode("utf-8")
            if label == "sdkconfig"
            else f"{label}-payload".encode("ascii")
        )
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
        "manual_lease_seconds": 4.0,
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
