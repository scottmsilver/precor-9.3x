#!/usr/bin/env python3
"""Executable host reference for the Esp32Tap Rev B safety contract.

This module is intentionally independent of ESP-IDF.  It makes ownership,
deadlines, relay sequencing, and fail-safe outputs executable for review and
future production-firmware parity tests.  It is not production firmware and
does not claim physical contact timing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Transport(str, Enum):
    WSS = "WSS"
    BLE = "BLE"
    EXECUTOR = "EXECUTOR"


class Mode(str, Enum):
    PROXY = "PROXY"
    ENTRY_WAIT_GAP = "ENTRY_WAIT_GAP"
    ENTRY_WAIT_FEEDBACK = "ENTRY_WAIT_FEEDBACK"
    EMULATING = "EMULATING"
    EXIT_WAIT_GAP = "EXIT_WAIT_GAP"
    EXIT_WAIT_FEEDBACK = "EXIT_WAIT_FEEDBACK"


class Feedback(str, Enum):
    """Decoded state of K1's grounded dry-contact feedback pole."""

    UNKNOWN = "UNKNOWN"
    BYPASS = "NC_CLOSED_NO_OPEN"
    EMULATE = "NC_OPEN_NO_CLOSED"
    BOTH_CLOSED = "NC_CLOSED_NO_CLOSED"
    BOTH_OPEN = "NC_OPEN_NO_OPEN"

    @classmethod
    def from_gpio(cls, nc_high: bool, no_high: bool) -> "Feedback":
        return {
            (False, True): cls.BYPASS,
            (True, False): cls.EMULATE,
            (False, False): cls.BOTH_CLOSED,
            (True, True): cls.BOTH_OPEN,
        }[(bool(nc_high), bool(no_high))]


@dataclass(frozen=True, slots=True, eq=False)
class ConnectionIdentity:
    """A connection object/handle plus a non-reusable generation."""

    transport: Transport
    handle: object
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.transport, Transport):
            raise TypeError("transport must be a Transport")
        if self.handle is None or isinstance(self.handle, bool):
            raise TypeError("handle must identify a concrete connection")
        if self.transport is not Transport.WSS and not isinstance(
            self.handle, (str, int)
        ):
            raise TypeError("BLE/executor handle must be a string or integer")
        if self.handle == "":
            raise ValueError("handle cannot be empty")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")

    @property
    def connection_key(self) -> tuple[Transport, str, object]:
        """Key generations by object identity for WSS and value otherwise."""

        if self.transport is Transport.WSS:
            return (self.transport, "identity", id(self.handle))
        return (self.transport, "value", self.handle)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConnectionIdentity):
            return NotImplemented
        if (
            self.transport is not other.transport
            or self.generation != other.generation
        ):
            return False
        if self.transport is Transport.WSS:
            return self.handle is other.handle
        return self.handle == other.handle

    def __hash__(self) -> int:
        return hash((*self.connection_key, self.generation))


@dataclass(slots=True)
class Lease:
    owner: ConnectionIdentity


class Controller:
    """Deterministic safety state machine driven by monotonic timestamps."""

    CONSOLE_FRESH_SECONDS = 1.5
    TRANSFER_GAP_DEADLINE_SECONDS = 1.0
    RELAY_FEEDBACK_DEADLINE_SECONDS = 0.010
    RELAY_FEEDBACK_STABLE_SECONDS = 0.001
    WDT_SECONDS = 2.0
    TREAD_OK_TO_NC_MAX_SECONDS = 0.010
    SOFTWARE_TO_NC_MAX_SECONDS = 0.250
    WDT_TO_NC_MAX_SECONDS = 2.25
    NORMAL_TRANSITION_ACCEPTANCE_CYCLES = 1_000
    _TIME_EPSILON = 1e-12

    _FRAME = re.compile(
        rb"\[[A-Za-z][A-Za-z0-9_]{0,31}:[\x20-\x7e]{0,64}\]"
    )

    def __init__(self) -> None:
        self.mode = Mode.PROXY
        self.speed_tenths = 0
        self.incline_half_percent = 0
        self.tread_ok = True
        self.feedback = Feedback.UNKNOWN
        self.fault_latched = False
        self.relay_cmd = False
        self.tx_enable = False
        self.usb_pullup_enabled = False
        self.last_complete_console_frame_at: float | None = None
        self.events: list[str] = []

        self._lease: Lease | None = None
        self._active_connections: set[ConnectionIdentity] = set()
        self._highest_generation: dict[tuple[Transport, str, object], int] = {}
        self._console_candidate = bytearray()
        self._phase_deadline: float | None = None
        self._feedback_candidate_since: float | None = None
        self._bypass_since: float | None = None
        self._bypass_qualified = False

    @property
    def owner(self) -> ConnectionIdentity | None:
        return None if self._lease is None else self._lease.owner

    @property
    def lease_expires_at(self) -> float | None:
        """Compatibility projection: ownership has no command deadline."""

        return None

    def connect(self, connection: ConnectionIdentity) -> bool:
        key = connection.connection_key
        highest = self._highest_generation.get(key, -1)
        if connection.generation <= highest:
            self.events.append("connection_rejected:stale_generation")
            return False
        superseded = {
            active
            for active in self._active_connections
            if active.connection_key == key
        }
        self._active_connections.difference_update(superseded)
        if (
            self.owner is not None
            and self.owner.connection_key == key
            and self.owner.generation < connection.generation
        ):
            self.emergency_stop(reason="owner_superseded", now=0.0)
        self._highest_generation[key] = connection.generation
        self._active_connections.add(connection)
        self.events.append(
            f"connected:{connection.transport.value}:"
            f"{connection.handle}:{connection.generation}"
        )
        return True

    def acquire(self, connection: ConnectionIdentity, *, now: float) -> bool:
        self._enforce_due_safety(now=now)
        if self._lease is not None:
            self.events.append("lease_rejected:already_owned")
            return False
        if connection not in self._active_connections:
            self.events.append("lease_rejected:not_connected")
            return False
        self._lease = Lease(connection)
        self.events.append(
            f"lease_acquired:{connection.transport.value}:"
            f"{connection.handle}:{connection.generation}"
        )
        return True

    def _is_owner(self, connection: ConnectionIdentity) -> bool:
        return self._lease is not None and self._lease.owner == connection

    def _authorize_owner(
        self,
        connection: ConnectionIdentity,
        *,
        now: float,
        ignored_event: str,
    ) -> bool:
        if self._enforce_due_safety(now=now):
            return False
        if not self._is_owner(connection):
            self.events.append(ignored_event)
            return False
        return True

    def heartbeat(self, connection: ConnectionIdentity, *, now: float) -> bool:
        if not self._authorize_owner(
            connection,
            now=now,
            ignored_event="ignored_non_owner_heartbeat",
        ):
            return False
        self.events.append("owner_heartbeat")
        return True

    def command_motion(
        self,
        connection: ConnectionIdentity,
        *,
        speed_tenths: int,
        incline_half_percent: int,
        now: float,
    ) -> bool:
        if not self._authorize_owner(
            connection,
            now=now,
            ignored_event="ignored_non_owner_motion",
        ):
            return False
        if not 0 <= speed_tenths <= 120:
            self.events.append("motion_rejected:speed_range")
            return False
        if not 0 <= incline_half_percent <= 30:
            self.events.append("motion_rejected:incline_range")
            return False
        self.speed_tenths = speed_tenths
        self.incline_half_percent = incline_half_percent
        self.events.append("owner_motion")
        return True

    def disconnect(
        self,
        connection: ConnectionIdentity,
        *,
        now: float,
    ) -> bool:
        self._enforce_due_safety(now=now)
        self._active_connections.discard(connection)
        if not self._is_owner(connection):
            self.events.append("ignored_non_owner_disconnect")
            return False
        self.emergency_stop(reason="owner_disconnect", now=now)
        return True

    def disconnect_transport(self, transport: Transport, *, now: float) -> bool:
        self._enforce_due_safety(now=now)
        self._active_connections = {
            connection
            for connection in self._active_connections
            if connection.transport is not transport
        }
        if self.owner is None or self.owner.transport is not transport:
            self.events.append(f"ignored_{transport.value.lower()}_drop")
            return False
        self.emergency_stop(
            reason=f"{transport.value.lower()}_disconnect",
            now=now,
        )
        return True

    def observe_console_bytes(self, data: bytes, *, now: float) -> int:
        """Consume bytes and timestamp only syntactically complete KV frames."""

        if self._enforce_due_safety(now=now):
            return 0
        complete = 0
        for byte in data:
            if byte == ord("["):
                self._console_candidate = bytearray((byte,))
                continue
            if not self._console_candidate:
                continue
            if byte < 0x20 or byte > 0x7E:
                self._console_candidate.clear()
                continue
            self._console_candidate.append(byte)
            if len(self._console_candidate) > 100:
                self._console_candidate.clear()
                continue
            if byte != ord("]"):
                continue
            candidate = bytes(self._console_candidate)
            self._console_candidate.clear()
            if self._FRAME.fullmatch(candidate):
                self.last_complete_console_frame_at = now
                complete += 1
                self.events.append("complete_console_frame")
        return complete

    def _console_is_fresh(self, now: float) -> bool:
        timestamp = self.last_complete_console_frame_at
        return (
            timestamp is not None
            and 0.0 <= now - timestamp < self.CONSOLE_FRESH_SECONDS
        )

    def request_emulate(
        self,
        connection: ConnectionIdentity,
        *,
        now: float,
        uart_idle_low: bool,
    ) -> bool:
        if not self._authorize_owner(
            connection,
            now=now,
            ignored_event="entry_rejected:not_owner",
        ):
            return False
        if (
            self.mode is not Mode.PROXY
            or self.relay_cmd
            or self.tx_enable
        ):
            self.events.append("entry_rejected:not_proxy")
            return False
        if self.fault_latched:
            self.events.append("entry_rejected:fault_latched")
            return False
        if not self.tread_ok:
            self.events.append("entry_rejected:tread_not_ok")
            return False
        if self.feedback is not Feedback.BYPASS:
            self.events.append("entry_rejected:feedback_not_bypass")
            return False
        if not self._console_is_fresh(now):
            self.events.append("entry_rejected:console_not_fresh")
            return False
        if not uart_idle_low:
            self.events.append("entry_rejected:uart_not_idle_low")
            return False

        self._begin_emulate_entry(now=now)
        return True

    def request_emulate_recovering(
        self,
        connection: ConnectionIdentity,
        *,
        now: float,
        uart_idle_low: bool,
    ) -> bool:
        """Acknowledge a fault and enter only when released hardware is healthy."""

        if not self._authorize_owner(
            connection,
            now=now,
            ignored_event="entry_rejected:not_owner",
        ):
            return False
        if (
            self.mode is not Mode.PROXY
            or self.relay_cmd
            or self.tx_enable
        ):
            self.events.append("recovery_rejected:not_proxy")
            return False
        if not self.tread_ok:
            self.events.append("recovery_rejected:tread_not_ok")
            return False
        bypass_since = self._bypass_since
        if (
            self.feedback is not Feedback.BYPASS
            or bypass_since is None
            or not self._bypass_qualified
        ):
            self.events.append(
                "recovery_rejected:feedback_not_qualified_bypass"
            )
            return False
        if not self._console_is_fresh(now):
            self.events.append("recovery_rejected:console_not_fresh")
            return False
        if not uart_idle_low:
            self.events.append("recovery_rejected:uart_not_idle_low")
            return False

        self.fault_latched = False
        self.events.append("fault_recovery_accepted")
        self._begin_emulate_entry(now=now)
        return True

    def _begin_emulate_entry(self, *, now: float) -> None:
        self.speed_tenths = 0
        self.incline_half_percent = 0
        self.events.extend(
            (
                "command_zero",
                "configure_inverted_uart",
                "verify_physical_idle_low",
                "tx_enable_on",
                "wait_entry_gap",
            )
        )
        self.tx_enable = True
        self.mode = Mode.ENTRY_WAIT_GAP
        self._phase_deadline = now + self.TRANSFER_GAP_DEADLINE_SECONDS
        self._feedback_candidate_since = None

    def observe_interframe_gap(self, *, now: float) -> bool:
        if self._enforce_due_safety(now=now):
            return False
        if self._phase_deadline is None:
            return False
        if self.mode is Mode.ENTRY_WAIT_GAP:
            if self.feedback is not Feedback.BYPASS:
                self.fault_latched = True
                self.emergency_stop(
                    reason="entry_feedback_changed_before_transfer",
                    now=now,
                )
                return False
            self.relay_cmd = True
            self.mode = Mode.ENTRY_WAIT_FEEDBACK
            self._phase_deadline = now + self.RELAY_FEEDBACK_DEADLINE_SECONDS
            self._feedback_candidate_since = None
            self.events.append("relay_cmd_on")
            return True
        if self.mode is Mode.EXIT_WAIT_GAP:
            if self.feedback is not Feedback.EMULATE:
                self.fault_latched = True
                self.emergency_stop(
                    reason="exit_feedback_changed_before_transfer",
                    now=now,
                )
                return False
            self.relay_cmd = False
            self.mode = Mode.EXIT_WAIT_FEEDBACK
            self._phase_deadline = now + self.RELAY_FEEDBACK_DEADLINE_SECONDS
            self._feedback_candidate_since = None
            self.events.append("relay_cmd_off")
            return True
        return False

    def _feedback_expected(self) -> Feedback | None:
        if self.mode is Mode.ENTRY_WAIT_FEEDBACK:
            return Feedback.EMULATE
        if self.mode is Mode.EXIT_WAIT_FEEDBACK:
            return Feedback.BYPASS
        return None

    def _finish_feedback_transfer(self) -> None:
        if self.mode is Mode.ENTRY_WAIT_FEEDBACK:
            self.mode = Mode.EMULATING
            self._phase_deadline = None
            self._feedback_candidate_since = None
            self.events.extend(
                (
                    "feedback_emulate_stable",
                    "send_first_complete_zero_frame",
                )
            )
        elif self.mode is Mode.EXIT_WAIT_FEEDBACK:
            self.mode = Mode.PROXY
            self._phase_deadline = None
            self._feedback_candidate_since = None
            self.events.extend(
                (
                    "feedback_bypass_stable",
                    "tx_enable_off",
                )
            )
            self.tx_enable = False
            self._release_lease(log=True)

    def _qualify_feedback(self, *, now: float) -> bool:
        expected = self._feedback_expected()
        deadline = self._phase_deadline
        since = self._feedback_candidate_since
        if expected is None or deadline is None or since is None:
            return False
        qualification_time = since + self.RELAY_FEEDBACK_STABLE_SECONDS
        if (
            self.feedback is expected
            and qualification_time <= now + self._TIME_EPSILON
            and qualification_time < deadline - self._TIME_EPSILON
        ):
            self._finish_feedback_transfer()
            return True
        return False

    def observe_relay_feedback(
        self,
        *,
        nc_high: bool,
        no_high: bool,
        now: float,
    ) -> Feedback:
        self._enforce_due_safety(now=now)
        feedback = Feedback.from_gpio(nc_high, no_high)
        self.feedback = feedback
        if feedback is Feedback.BYPASS:
            if self._bypass_since is None:
                self._bypass_since = now
                self._bypass_qualified = False
            elif (
                not self._bypass_qualified
                and now >= self._bypass_since
                and now - self._bypass_since + self._TIME_EPSILON
                >= self.RELAY_FEEDBACK_STABLE_SECONDS
            ):
                self._bypass_qualified = True
        else:
            self._bypass_since = None
            self._bypass_qualified = False
        if feedback is Feedback.BOTH_CLOSED:
            self.fault_latched = True
            self.emergency_stop(
                reason="relay_feedback_both_closed",
                now=now,
            )
            return feedback

        expected = self._feedback_expected()
        if expected is not None:
            if feedback is expected:
                if self._feedback_candidate_since is None:
                    self._feedback_candidate_since = now
                    self.events.append("feedback_candidate")
                self._qualify_feedback(now=now)
            else:
                self._feedback_candidate_since = None
                self.events.append("feedback_transition")
        elif self.mode is Mode.ENTRY_WAIT_GAP and feedback is not Feedback.BYPASS:
            self.fault_latched = True
            self.emergency_stop(
                reason="entry_feedback_changed_before_gap",
                now=now,
            )
        elif self.mode is Mode.EXIT_WAIT_GAP and feedback is not Feedback.EMULATE:
            self.fault_latched = True
            self.emergency_stop(
                reason="exit_feedback_changed_before_gap",
                now=now,
            )
        elif self.mode is Mode.EMULATING and feedback is not Feedback.EMULATE:
            self.fault_latched = True
            self.emergency_stop(reason="relay_feedback_invalid", now=now)
        elif self.mode is Mode.PROXY and feedback is not Feedback.BYPASS:
            self.fault_latched = True
            self.events.append("proxy_feedback_invalid")
        return feedback

    def request_normal_exit(
        self,
        connection: ConnectionIdentity,
        *,
        now: float,
    ) -> bool:
        if not self._authorize_owner(
            connection,
            now=now,
            ignored_event="exit_rejected:not_owner",
        ):
            return False
        if self.mode is not Mode.EMULATING:
            self.events.append("exit_rejected:not_emulating")
            return False
        self.events.extend(
            (
                "send_and_finish_complete_zero_frame",
                "wait_exit_gap",
            )
        )
        self.speed_tenths = 0
        self.incline_half_percent = 0
        self.mode = Mode.EXIT_WAIT_GAP
        self._phase_deadline = now + self.TRANSFER_GAP_DEADLINE_SECONDS
        self._feedback_candidate_since = None
        return True

    def set_tread_ok(self, value: bool, *, now: float) -> None:
        self._enforce_due_safety(now=now)
        self.tread_ok = bool(value)
        if not self.tread_ok and (
            self.mode is not Mode.PROXY
            or self.relay_cmd
            or self.tx_enable
        ):
            self.emergency_stop(reason="tread_not_ok", now=now)

    def set_vbus_present_n(self, level_high: bool) -> None:
        """Apply active-low GPIO7 semantics to the native-USB pull-up."""

        self.usb_pullup_enabled = not bool(level_high)
        self.events.append(
            "usb_attach"
            if self.usb_pullup_enabled
            else "usb_detach"
        )

    def tick(self, *, now: float) -> None:
        self._enforce_due_safety(now=now)

    def _enforce_due_safety(self, *, now: float) -> bool:
        """Advance every due safety deadline before accepting timed input."""

        if self.mode is not Mode.PROXY:
            if not self.tread_ok:
                self.emergency_stop(reason="tread_not_ok", now=now)
                return True
            if not self._console_is_fresh(now):
                self.emergency_stop(reason="console_stale", now=now)
                return True
        if (
            self._phase_deadline is None
            or now < self._phase_deadline - self._TIME_EPSILON
        ):
            return False
        if self.mode is Mode.ENTRY_WAIT_GAP:
            self.emergency_stop(reason="entry_no_gap", now=now)
            self.events.append("entry_abort:no_gap")
            return True
        elif self.mode is Mode.ENTRY_WAIT_FEEDBACK:
            self.fault_latched = True
            self.emergency_stop(
                reason="entry_feedback_timeout",
                now=now,
            )
            return True
        elif self.mode is Mode.EXIT_WAIT_GAP:
            self.events.append("exit_gap_timeout")
            self.relay_cmd = False
            self.mode = Mode.EXIT_WAIT_FEEDBACK
            self._phase_deadline = now + self.RELAY_FEEDBACK_DEADLINE_SECONDS
            self._feedback_candidate_since = None
            self.events.append("relay_cmd_off")
            return False
        elif self.mode is Mode.EXIT_WAIT_FEEDBACK:
            self.fault_latched = True
            self.emergency_stop(
                reason="exit_feedback_timeout",
                now=now,
            )
            return True
        return False

    def _release_lease(self, *, log: bool) -> None:
        self._lease = None
        if log:
            self.events.append("lease_released")

    def emergency_stop(self, *, reason: str, now: float) -> None:
        del now  # The caller supplies monotonic time for parity and audit.
        self.speed_tenths = 0
        self.incline_half_percent = 0
        self.relay_cmd = False
        self.tx_enable = False
        self.mode = Mode.PROXY
        self._phase_deadline = None
        self._feedback_candidate_since = None
        self._release_lease(log=False)
        self.events.append(f"emergency:{reason}")

    def watchdog_stall(self, *, now: float) -> None:
        self._reset_class_stop(reason="watchdog", now=now)

    def reset(self, *, now: float, reason: str = "reset") -> None:
        self._reset_class_stop(reason=reason, now=now)

    def _reset_class_stop(self, *, reason: str, now: float) -> None:
        self.emergency_stop(reason=reason, now=now)
        self._active_connections.clear()
        self._console_candidate.clear()
        self.last_complete_console_frame_at = None
        self.feedback = Feedback.UNKNOWN
        self._bypass_since = None
        self._bypass_qualified = False
        self.usb_pullup_enabled = False


__all__ = [
    "ConnectionIdentity",
    "Controller",
    "Feedback",
    "Mode",
    "Transport",
]
