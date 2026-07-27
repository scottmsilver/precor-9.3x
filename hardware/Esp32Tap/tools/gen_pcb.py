#!/usr/bin/python3
"""Generate the Esp32Tap Rev E four-layer PCB from ``design.py``.

Critical current loops, feedback, and USB are explicit routes.  Remaining
low-speed nets use a deterministic two-routing-layer grid search: vertical
and horizontal freedom comes from B.Cu/In2.Cu while In1.Cu remains a solid
ground reference.  Connectivity is always regenerated from the design
tables; the checked-in board is never edited as an input.
"""

from __future__ import annotations

import argparse
import heapq
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import design
import pcbnew
import power_intent
from pcbnew import VECTOR2I
from pcbnew import FromMM as MM

design.validate()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "kicad" / "Esp32Tap.kicad_pcb"
FPLIB = Path(design.FPLIB)
OX, OY = 100.0, 100.0
BOARD_W, BOARD_H, BOARD_TOP = 95.0, 55.0, -3.0
F = pcbnew.F_Cu
IN1 = pcbnew.In1_Cu
IN2 = pcbnew.In2_Cu
B = pcbnew.B_Cu
ROUTING_LAYERS = (IN2, B)
GRID = 0.4
CLEARANCE = 0.20
U1_PROFILE_SHIFT_Y = 6.6
USB_ROUTE_SHIFT_Y = 6.6
U1_COUPLED_REFS = (
    "U1",
    "R7",
    "R8",
    "R13",
    "R15",
    "R16",
    "C8",
    "C9",
    "C10",
    "C13",
    "C14",
)
U1_COUPLED_ESCAPE_KEYS = {
    ("C10", "1"),
    ("R13", "2"),
    ("C9", "2"),
    ("R8", "2"),
}
SAFETY_RELAY_PRIORITY = (
    "CONS6",
    "MOT6",
    "TX_DRV",
    "TX_BUF",
    "TREAD_OK",
    "TREAD_OK_MCU",
    "RELAY_CMD",
    "RELAY_GATE",
    "RELAY_SW",
    "TX_ENABLE",
    "TX_GATE",
    "K1_NC_FB",
    "K1_NO_FB",
    "PIN3",
    "PIN3_RX",
    "PIN5_SAFETY",
    "PIN4_PASS",
    "CONS_RX",
)
ANTENNA_KEEPOUT = (
    68.0,
    U1_PROFILE_SHIFT_Y,
    88.5,
    4.2 + U1_PROFILE_SHIFT_Y,
)
FIXTURE_KEEPOUT = (47.0, 33.8, 77.0, 38.2)
USB_NETS = {
    "USB_DN",
    "USB_DP",
    "USB_DN_MCU",
    "USB_DP_MCU",
    "USB_DN_R",
    "USB_DP_R",
}
USB_CONTROLLED_WIDTH = 0.2906
USB_EDGE_GAP = 0.2000
USB_CENTER_SPACING = USB_CONTROLLED_WIDTH + USB_EDGE_GAP
USB_UNRELATED_CLEARANCE = 0.80
ROUTER_VIA_RADIUS = 0.30
MANUAL_NETS = USB_NETS | {
    "GND",
    "+8V_RAW",
    "+8V_F",
    "SW_NODE",
    "BST",
    "FB",
    "UV_SENSE",
    "OV_SENSE",
    "CC1",
    "CC2",
    "VBUS",
    "CONS6",
    "VBUS_PRESENT_N",
}
U1_CLUSTER_PRIORITY = tuple(
    net
    for net, endpoints in design.NETS.items()
    if any(ref == "U1" or ref in U1_COUPLED_REFS for ref, _ in endpoints)
    and net not in MANUAL_NETS
    and net not in SAFETY_RELAY_PRIORITY
    and net not in {"+8V_RAW", "+8V_F", "VIN", "+3V3"}
)
WIDTHS = {
    "+8V_RAW": 0.60,
    "+8V_F": 0.80,
    "VIN": 0.60,
    "+3V3": 0.60,
    "+5V_RLY": 0.50,
    "RELAY_SW": 0.40,
}
KIID_SEED = 0x45535032
PLANNED_U6_ESCAPES = {
    "1": ((44.8, 20.8), "RELAY_CMD"),
    "2": ((44.0, 21.6), "TREAD_OK"),
    "3": ((44.8, 22.4), "TX_GATE"),
    "4": ((44.0, 23.2), "GND"),
    "5": ((50.0, 23.2), "TX_ENABLE"),
    "6": ((51.2, 22.4), "TREAD_OK"),
    "7": ((52.0, 21.6), "RELAY_GATE"),
    "8": ((51.2, 20.8), "+3V3"),
}
# Rev E: the +8V_RAW trunk plus the relocated J2 turn the supervisor belt
# (x 44-70, y 20-27) and its two north-south crossing windows into scarce
# resources.  The high-priority safety nets blanket them first, so the
# known-late nets that must cross get deterministic pre-reserved slots
# (marked as owned grid cells, exactly like PLANNED_U6_ESCAPES).
# In2-only: the belt's B rows stay free so other east-west traffic can
# still cross these columns on B.Cu.
PLANNED_ROUTE_RESERVATIONS = (
    ("TX_ENABLE", ((50.0, 27.2), (50.0, 23.2))),
    ("+5V_RLY", ((52.2, 33.2), (52.2, 28.2), (46.0, 28.2), (46.0, 24.2), (40.4, 24.2), (40.4, 26.4))),
    ("TX_GATE", ((63.2, 33.6), (63.2, 26.4))),
    ("+3V3", ((46.4, 20.0), (50.8, 20.0))),
    ("VIN", ((37.6, 42.4), (37.6, 43.0))),
    ("VIN", ((39.8, 38.6), (39.8, 38.6))),
    ("Q1_B", ((37.6, 17.2), (36.4, 14.4), (34.4, 13.2))),
    ("K1_NO_FB", ((35.2, 32.8), (35.2, 32.8))),
    ("EN", ((66.4, 12.4), (61.6, 12.4), (60.4, 14.8))),
)

# U1's west pad column escapes into the narrow x~68 alley between the
# C13/C14 DNP stubs and the module's own pad field; assign the slots
# deterministically so late cluster nets are not stranded.
PLANNED_U1_WEST_ESCAPES = {
    "3": (68.4, 12.8),
    "4": (67.2, 12.0),
    "6": (68.0, 14.8),
    "8": (68.0, 16.4),
    "9": (68.0, 17.6),
    "10": (68.4, 19.2),
    "11": (68.4, 20.4),
    "12": (68.0, 21.2),
}

# U7 sits in the most contested part of the supervisor belt; its four
# signal pads get planned escapes exactly like U6's so the early bulk
# routes cannot strand them (pad 3 is GND and uses the generic stitcher).
PLANNED_U7_ESCAPES = {
    "1": ((55.6, 23.2), "TX_GATE"),
    "2": ((54.8, 22.2), "ESP_TX"),
    "4": ((60.4, 24.8), "TX_BUF"),
    "5": ((60.4, 22.4), "+3V3"),
}

# J2's four signal pads escape east UNDER the jack body into the box
# bounded by the +8V_RAW bus (x=79.55, vias need >=1.5 mm), the GND
# meet/rowtie column (x=85.1), the gnd-strip-a tail (y=35.6, vias need
# >=1.49 mm), and the J2.1 contact stub (y=41.45).  Four L-shaped
# doglegs fit exactly when the vertical legs step east with pad depth
# (so no leg crosses another pad's horizontal); plan the slots like
# U6's so route order cannot strand the last net.
PLANNED_J2_ESCAPES = {
    "3": (81.2, 38.8),
    "4": (81.6, 38.0),
    "5": (82.4, 37.6),
    "6": (83.2, 37.2),
}

# JLC DFM reports same-net pad/via proximity and mask-opening artefacts even
# when KiCad's electrical DRC accepts them.  Lock only the reported endpoint
# escapes so the manufacturing geometry stays deterministic while preserving
# J1's connector footprint and plated-pad geometry.
LOCKED_DFM_ESCAPES = {
    ("C10", "1"): (61.2, 11.2),
    ("R13", "2"): (61.2, 11.2),
    ("C9", "2"): (65.2, 1.2),
    ("R22", "1"): (43.6, 40.8),
    ("R8", "2"): (64.4, 9.2),
    ("L1", "2"): (67.2, 51.2),
}

# The CONSOLE silkscreen label (23.0, 37.0, vertical) sits in the busy
# corridor east of J1's pad row where the router legitimately wants
# layer-change vias; a via HOLE under a silk label violates the
# fabrication-clearance audit (>=0.25 mm from mask/hole openings).
# Reserve the label's bounding box (with hole-radius+clearance margin
# rounded to the 0.4 mm routing grid) against via placement only --
# tracks are mask-covered and may run beneath silk freely.
SILK_VIA_KEEPOUTS = ((21.8, 33.0, 24.3, 41.0),)

CUSTOM_FOOTPRINT_SOURCES: dict[tuple[str, str], tuple[str, str]] = {}


# Coordinates are board-local millimetres.  The groups deliberately preserve
# the RJ45/enclosure geometry, keep RF/USB clear of power, and make the
# supervisor, relay, and converter loops probeable.
PLACE = {
    # Rev D introduced the Molex 441440003 right-angle SMD RJ45 (LCSC
    # C585890).  The jack's mating opening faces local +Y -- the deep-body
    # side, verified against both the vendor 3D model and the Molex
    # SD-44144-001 drawing (the signal-pad row at local Y=-5.9 is the
    # REAR; the port cavity opens through the local Y=+11.69 face).  At
    # rotation=270 local +Y maps to world -X (off the left edge) and at
    # rotation=90 to world +X (off the right edge).  X anchors put the
    # F.Fab body-front bounding box (centreline +11.85 plus the 0.05 mm
    # half line width) exactly on the board edge -- the same
    # fab-body-exactly-inside-outline discipline as J3 -- so the physical
    # mating face sits flush (0.21 mm inside): the pad rows land ~17.8 mm
    # INBOARD of each edge and the entry copper lives under the jack
    # shells (see power_intent.py).  Rev E: J2 (MOTOR) is on the RIGHT
    # short edge so the board reads console -> motor left to right, and
    # both jacks share Y=37.0 -- a straight passthrough centred between
    # the antenna keepout (top) and MH3 (bottom-right).
    "J1": (11.9, 37.0, 270),
    "J2": (83.1, 37.0, 90),
    # Rev E: USB-C moved to the bottom edge (opening faces +Y / world
    # bottom).  X=83.6 keeps its courtyard clear of MH3's courtyard
    # (x>=89.0); Y=51.2 puts the fabrication body exactly inside the
    # Y=55 outline.
    "J3": (83.6, 51.2, 0),
    "U1": (78.0, 6.45, 0),
    "K1": (30.2, 23.0, 0),
    "U2": (60.0, 48.5, 180),
    # Rev E: ESD array west of J3, input pads (1/2/3) on its east column
    # facing the connector, MCU pads (4/5/6) facing the west corridor.
    "U3": (76.0, 47.3, 180),
    "U4": (35.0, 42.0, 0),
    "U5": (37.0, 25.0, 0),
    "U6": (48.0, 22.0, 0),
    "U7": (58.0, 24.0, 0),
    "Q1": (35.0, 15.5, 0),
    "Q2": (76.4, 51.2, 90),
    "D1": (27.5, 52.5, 180),
    "D3": (35.0, 52.0, 0),
    "D4": (30.0, 11.5, 0),
    "D5": (27.0, 15.0, 270),
    "D6": (29.0, 47.0, 270),
    "D7": (38.0, 38.0, 270),
    "LED1": (93.0, 10.0, 180),
    # Rev E: LED2/R12 move to the vacated left-middle area (old J2 zone).
    "LED2": (41.2, 52.4, 0),
    "SW1": (42.0, 4.0, 0),
    "SW2": (94.0, 17.0, 0),
    "F1": (20.5, 52.5, 0),
    "L1": (65.0, 48.5, 0),
    "R1": (57.0, 45.0, 0),
    "R2": (55.0, 47.5, 180),
    "R3": (50.0, 45.0, 0),
    "R14": (50.0, 48.0, 0),
    "R4": (92.0, 46.8, 90),
    "R5": (74.6, 54.0, 0),
    "R6": (39.0, 14.0, 0),
    "R7": (64.0, 13.0, 0),
    "R8": (64.0, 10.5, 0),
    "R9": (38.5, 19.0, 90),
    "R10": (42.0, 18.5, 0),
    "R11": (90.0, 10.0, 0),
    "R12": (44.4, 52.4, 0),
    "R13": (60.0, 12.0, 90),
    "R15": (66.5, 15.5, 0),
    "R16": (66.5, 17.5, 0),
    "R17": (29.0, 39.0, 0),
    "R18": (29.0, 42.0, 180),
    "R19": (41.0, 39.0, 0),
    "R20": (41.0, 42.0, 180),
    "R21": (45.0, 39.0, 0),
    "R22": (45.0, 42.0, 0),
    "R23": (47.0, 27.0, 0),
    "R24": (47.0, 30.0, 0),
    # Rev E: horizontal, west of K1, so its escape vias sit beside the
    # +8V_RAW B.Cu trunk corridor instead of inside it.
    "R25": (26.6, 33.0, 0),
    "R26": (35.2, 35.0, 90),
    "R27": (52.0, 27.0, 0),
    "R28": (52.0, 30.0, 0),
    "R29": (69.8, 44.4, 0),
    "R30": (69.8, 49.5, 0),
    "R31": (90.0, 13.0, 90),
    # TREAD_OK -> IO6 series isolation resistor (review finding B2). Placed
    # next to the U6/R27/R28 TREAD_OK cluster with clear courtyard spacing
    # from R28, U7, and C21; the single TREAD_OK_MCU stub runs out to U1.6.
    "R32": (56.5, 30.0, 0),
    "C1": (43.5, 49.5, 0),
    "C2": (50.0, 52.0, 0),
    "C3": (63.5, 43.5, 0),
    "C4": (62.0, 45.5, 0),
    "C5": (61.1, 51.1, 0),
    # Rev E: 0.2 west so the USB pair vertical corridor clears pad 2.
    "C6": (69.8, 47.0, 0),
    "C7": (70.0, 52.0, 0),
    "C8": (66.5, 6.5, 90),
    "C9": (66.5, 2.0, 90),
    "C10": (60.0, 8.0, 90),
    "C11": (90.2, 46.4, 90),
    "C12": (57.0, 43.0, 0),
    "C13": (67.3, 12.8, 90),
    "C14": (67.3, 20.0, 270),
    "C15": (37.0, 21.5, 0),
    "C16": (41.0, 25.0, 90),
    "C17": (35.0, 39.4, 0),
    "C18": (32.0, 45.0, 0),
    "C19": (38.0, 45.0, 0),
    "C20": (48.0, 19.5, 0),
    "C21": (58.0, 21.5, 0),
    # Rev E: the UART probe pads sit in the clear strip east of U1 (below
    # SW2's courtyard, above J2's) so U0TXD/U0RXD stay short local links.
    # Their old home (35.0, 3.0/6.0) forced two full-width crossings of
    # the top edge, which the module's stock antenna keepout (blocked in
    # initialise_router_obstacles) no longer permits.
    "TP1": (92.0, 24.4, 0),
    "TP2": (92.0, 27.2, 0),
    "TP3": (22.0, 44.0, 0),
    "TP4": (22.0, 47.6, 0),
    "TP5": (54.0, 36.0, 0),
    "TP6": (57.2, 36.0, 0),
    "TP7": (60.4, 36.0, 0),
    "TP8": (63.6, 36.0, 0),
    "TP9": (66.8, 36.0, 0),
    "TP10": (70.0, 36.0, 0),
    "TP11": (73.2, 36.0, 0),
    "TP12": (76.4, 36.0, 0),
    "TP13": (79.6, 36.0, 0),
}

COMPACT_X_DELTAS = {
    "SW2": -3.0,
    **{f"TP{number}": -5.0 for number in range(5, 14)},
    # Rev E: J2's deep courtyard reaches X=75.275 and the USB pair's
    # vertical corridor threads the TP12/TP13 gap; both test pads shift
    # west a little further so pad copper, courtyards, and the 90-ohm
    # pair all clear each other.
    "TP12": -5.44,
    "TP13": -5.68,
}

# Preserve every qualified Rev B coordinate that already fits the compact
# outline. Only footprints whose physical bounds cross the new edge move.
PLACE = {
    ref: (
        x + COMPACT_X_DELTAS.get(ref, 0.0),
        y,
        rotation,
    )
    for ref, (x, y, rotation) in PLACE.items()
}
PLACE = {
    ref: (
        x,
        y + (U1_PROFILE_SHIFT_Y if ref in U1_COUPLED_REFS else 0.0),
        rotation,
    )
    for ref, (x, y, rotation) in PLACE.items()
}
LOCKED_DFM_ESCAPES = {
    key: (
        point[0],
        round(
            math.floor((point[1] + (U1_PROFILE_SHIFT_Y if key in U1_COUPLED_ESCAPE_KEYS else 0.0)) / GRID) * GRID,
            6,
        ),
    )
    for key, point in LOCKED_DFM_ESCAPES.items()
}
# R8.2 must escape toward the antenna-side channel.  The directly translated
# grid cell is consumed deterministically by the earlier RELAY_CMD route.
LOCKED_DFM_ESCAPES[("R8", "2")] = (64.4, 14.8)
LOCKED_DFM_ESCAPES[("R13", "2")] = (60.0, 16.0)
LOCKED_DFM_ESCAPES[("C10", "1")] = (60.0, 16.0)
# The fixture corridor forbids B.Cu and through vias through y=33.8..38.2.
# Reserve TX_GATE immediately above it so the safety route is not displaced
# by the later U1 cluster or the shifted USB pair.
LOCKED_DFM_ESCAPES[("TP10", "1")] = (63.2, 39.2)
# U1.1's generic GND stitch used to land just west of the antenna keepout;
# the keepout width-margin now blocks that cell and the ring search would
# otherwise drift onto C9.2's pad (JLC same-net pad/via proximity).  Pin it
# in the clear pocket south-west of C9.2, outside the module keepout.
LOCKED_DFM_ESCAPES[("U1", "1")] = (66.8, 6.8)


def local(point: Any) -> tuple[float, float]:
    return (
        pcbnew.ToMM(point.x) - OX,
        pcbnew.ToMM(point.y) - OY,
    )


def absolute(point: tuple[float, float]) -> VECTOR2I:
    return VECTOR2I(MM(OX + point[0]), MM(OY + point[1]))


def grid_index(value: float) -> int:
    return round(value / GRID)


def grid_point(index: tuple[int, int]) -> tuple[float, float]:
    return (round(index[0] * GRID, 6), round(index[1] * GRID, 6))


def slow_net_order() -> list[str]:
    priority = {net: index for index, net in enumerate(SAFETY_RELAY_PRIORITY)}
    cluster = {net: index for index, net in enumerate(U1_CLUSTER_PRIORITY)}
    power = {"+8V_RAW", "+8V_F", "VIN", "+3V3"}
    nets = [net for net in design.NETS if net not in MANUAL_NETS]
    nets.sort(
        key=lambda net: (
            (0 if net in priority else 1 if net in cluster else 2 if net in power else 3),
            priority.get(net, cluster.get(net, len(priority))),
            -len(design.NETS[net]),
            net,
        )
    )
    return nets


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, start)
    fraction = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared,
        ),
    )
    projection = (
        start[0] + fraction * dx,
        start[1] + fraction * dy,
    )
    return math.dist(point, projection)


def segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> bool:
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
        )

    values = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) <= 1e-9 and on_segment(start, point, end)
        for value, start, point, end in (
            (values[0], a, c, b),
            (values[1], a, d, b),
            (values[2], c, a, d),
            (values[3], c, b, d),
        )
    )


def segment_distance(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


class Generator:
    def __init__(self, output: Path):
        self.output = output
        pcbnew.KIID.SeedGenerator(KIID_SEED)
        # Build in memory; a failed route must never truncate the last known
        # good checked-in board before the atomic save in ``fill_and_save``.
        self.board = pcbnew.NewBoard("")
        self.board.SetCopperLayerCount(4)
        settings = self.board.GetDesignSettings()
        settings.m_CopperEdgeClearance = MM(0.25)
        settings.m_MinThroughDrill = MM(0.20)
        settings.m_MinTrackWidth = MM(0.20)
        title = self.board.GetTitleBlock()
        title.SetTitle("Esp32Tap - ESP32-S3 Precor serial-bus tap")
        title.SetRevision("E")
        title.SetCompany("precor-9.3x")
        title.SetComment(0, "STACKUP: JLC04161H-7628")
        title.SetComment(
            1,
            "90 ohm differential USB on F.Cu referenced to In1.Cu",
        )

        self.nets: dict[str, Any] = {}
        for name in design.NETS:
            item = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(item)
            self.nets[name] = item
        for ref, pad in design.NC:
            pin_name = design.COMPONENTS[ref][7][pad]
            name = f"unconnected-({ref}-{pin_name}-Pad{pad})"
            item = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(item)
            self.nets[name] = item
        self.pad_net = {endpoint: name for name, endpoints in design.NETS.items() for endpoint in endpoints}
        self.pad_net.update(
            {
                (ref, pad): (f"unconnected-({ref}-" f"{design.COMPONENTS[ref][7][pad]}-Pad{pad})")
                for ref, pad in design.NC
            }
        )
        self.footprints: dict[str, Any] = {}
        self.track_records: list[tuple[tuple[float, float], tuple[float, float], str, float, int]] = []
        self.via_records: list[tuple[tuple[float, float], str]] = []
        self.via_geometry: dict[tuple[tuple[float, float], str], tuple[float, float]] = {}
        self.pad_objects: dict[tuple[str, str], list[Any]] = defaultdict(list)
        self.occupied: dict[int, dict[tuple[int, int], set[str]]] = {
            IN2: defaultdict(set),
            B: defaultdict(set),
        }
        self.blocked: dict[int, set[tuple[int, int]]] = {
            IN2: set(),
            B: set(),
        }
        self.front_pad_obstacles: list[tuple[float, float, float, float, str]] = []

    def custom_switch_footprint(self, name: str) -> Any:
        """Build the exact Alps SKRP land pattern from the official drawing."""
        footprint = pcbnew.FootprintLoad(
            str(FPLIB / "Button_Switch_SMD.pretty"),
            "SW_Push_1P1T_NO_CK_KMR2",
        )
        if footprint is None:
            raise ValueError("cannot load deterministic SKRP footprint base")
        footprint.SetFPIDAsString(f"Button_Switch_SMD:{name}")

        # Official SKRP land pattern: 5.2 x 2.8 mm outside span and
        # 3.1 x 1.5 mm inside span. This yields 1.05 x 0.65 mm lands
        # centred at (+/-2.075, +/-1.075) mm.
        pad_data = iter(
            (
                ("1", -2.075, -1.075),
                ("1", -2.075, 1.075),
                ("2", 2.075, -1.075),
                ("2", 2.075, 1.075),
            )
        )
        for pad, (number, x, y) in zip(footprint.Pads(), pad_data):
            pad.SetNumber(number)
            pad.SetSize(VECTOR2I(MM(1.05), MM(0.65)))
            pad.SetPosition(VECTOR2I(MM(x), MM(y)))
        for graphic in list(footprint.GraphicalItems()):
            if graphic.GetLayer() == pcbnew.F_Fab:
                footprint.Remove(graphic)
        body = (
            (-2.05, -1.55),
            (2.05, -1.55),
            (2.05, 1.55),
            (-2.05, 1.55),
        )
        for start, end in zip(body, (*body[1:], body[0])):
            segment = pcbnew.PCB_SHAPE(footprint)
            segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
            segment.SetLayer(pcbnew.F_Fab)
            segment.SetStart(VECTOR2I(MM(start[0]), MM(start[1])))
            segment.SetEnd(VECTOR2I(MM(end[0]), MM(end[1])))
            segment.SetWidth(MM(0.10))
            footprint.Add(segment)
        return footprint

    def load_footprint(self, library: str, name: str) -> Any:
        local_library = ROOT / "kicad" / f"{library}.pretty"
        if (local_library / f"{name}.kicad_mod").is_file():
            footprint = pcbnew.FootprintLoad(str(local_library), name)
            if footprint is not None:
                footprint.SetFPIDAsString(f"{library}:{name}")
                return footprint
        if name == "SW_SPST_SKRPACE010":
            return self.custom_switch_footprint(name)
        source_library, source_name = CUSTOM_FOOTPRINT_SOURCES.get(
            (library, name),
            (library, name),
        )
        footprint = pcbnew.FootprintLoad(
            str(FPLIB / f"{source_library}.pretty"),
            source_name,
        )
        if footprint is not None:
            footprint.SetFPIDAsString(f"{library}:{name}")
        return footprint

    def add_footprints(self) -> None:
        missing = set(design.COMPONENTS) - set(PLACE)
        extra = set(PLACE) - set(design.COMPONENTS)
        if missing or extra:
            raise ValueError(f"placement mismatch missing={sorted(missing)} " f"extra={sorted(extra)}")
        for ref, component in design.COMPONENTS.items():
            value, library, name, lcsc, jlc_class, cost, description, _ = component
            footprint = self.load_footprint(library, name)
            if footprint is None:
                raise ValueError(f"cannot load {library}:{name}")
            footprint.SetFPIDAsString(f"{library}:{name}")
            footprint.SetReference(ref)
            footprint.SetValue(value)
            footprint.SetDNP(ref in design.DNP)
            footprint.SetExcludedFromBOM(ref in design.DNP or jlc_class == "none")
            footprint.SetField("LCSC", lcsc)
            footprint.SetField("JLC Class", jlc_class)
            footprint.SetField("Unit Cost USD", f"{cost:.3f}")
            footprint.SetField("Description", description)
            for field in footprint.GetFields():
                field.SetVisible(False)
                field.SetLayer(pcbnew.F_Fab)
            x, y, rotation = PLACE[ref]
            footprint.SetPosition(absolute((x, y)))
            footprint.SetOrientationDegrees(rotation)
            self.board.Add(footprint)
            self.footprints[ref] = footprint

        for ref, footprint in self.footprints.items():
            for pad in footprint.Pads():
                number = str(pad.GetNumber())
                if not number:
                    continue
                net = self.pad_net.get((ref, number))
                if net:
                    pad.SetNet(self.nets[net])
                self.pad_objects[(ref, number)].append(pad)

        for reference, position in (
            ("MH1", (20.0, 3.0)),
            ("MH2", (48.0, 3.0)),
            ("MH3", (92.0, 52.0)),
        ):
            footprint = pcbnew.FootprintLoad(
                str(FPLIB / "MountingHole.pretty"),
                "MountingHole_2.7mm_M2.5",
            )
            if footprint is None:
                raise ValueError("cannot load M2.5 mounting-hole footprint")
            footprint.SetFPIDAsString("MountingHole:MountingHole_2.7mm_M2.5")
            footprint.SetReference(reference)
            footprint.SetValue("M2.5")
            footprint.SetPosition(absolute(position))
            footprint.SetBoardOnly(True)
            for field in footprint.GetFields():
                field.SetVisible(False)
                field.SetLayer(pcbnew.F_Fab)
            self.board.Add(footprint)
            self.footprints[reference] = footprint

    def pad(self, ref: str, number: str) -> tuple[float, float]:
        pads = self.pad_objects[(ref, number)]
        if not pads:
            raise KeyError((ref, number))
        return local(pads[0].GetPosition())

    def pads(self, ref: str, number: str) -> list[tuple[float, float]]:
        return [local(pad.GetPosition()) for pad in self.pad_objects[(ref, number)]]

    def normalize_silkscreen_graphics(self) -> None:
        for footprint in self.footprints.values():
            for graphic in footprint.GraphicalItems():
                if graphic.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                    continue
                if graphic.GetWidth() < MM(0.16):
                    graphic.SetWidth(MM(0.16))

    def add_outline(self) -> None:
        corners = [
            (0, BOARD_TOP),
            (BOARD_W, BOARD_TOP),
            (BOARD_W, BOARD_H),
            (0, BOARD_H),
        ]
        for start, end in zip(corners, corners[1:] + corners[:1]):
            segment = pcbnew.PCB_SHAPE(self.board)
            segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
            segment.SetStart(absolute(start))
            segment.SetEnd(absolute(end))
            segment.SetLayer(pcbnew.Edge_Cuts)
            segment.SetWidth(MM(0.10))
            self.board.Add(segment)

    def add_track(
        self,
        points: Iterable[tuple[float, float]],
        net: str,
        width: float = 0.20,
        layer: int = F,
    ) -> None:
        point_list = list(points)
        for start, end in zip(point_list, point_list[1:]):
            if math.dist(start, end) < 1e-6:
                continue
            segment = pcbnew.PCB_TRACK(self.board)
            segment.SetStart(absolute(start))
            segment.SetEnd(absolute(end))
            segment.SetWidth(MM(width))
            segment.SetLayer(layer)
            segment.SetNet(self.nets[net])
            self.board.Add(segment)
            self.track_records.append((start, end, net, width, layer))

    def add_via(
        self,
        point: tuple[float, float],
        net: str,
        size: float = 0.60,
        drill: float = 0.30,
    ) -> None:
        for existing, existing_net in self.via_records:
            if math.dist(point, existing) < 1e-6:
                if existing_net == net:
                    return
                raise ValueError(f"conflicting vias at {point}: {existing_net} and {net}")
        via = pcbnew.PCB_VIA(self.board)
        via.SetPosition(absolute(point))
        via.SetWidth(MM(size))
        via.SetDrill(MM(drill))
        via.SetLayerPair(F, B)
        via.SetNet(self.nets[net])
        self.board.Add(via)
        self.via_records.append((point, net))
        self.via_geometry[(point, net)] = (size, drill)

    def add_zone(
        self,
        layer: int,
        net: str,
        points: Iterable[tuple[float, float]],
        name: str,
    ) -> None:
        zone = pcbnew.ZONE(self.board)
        zone.SetLayer(layer)
        zone.SetNet(self.nets[net])
        zone.SetZoneName(name)
        zone.SetLocalClearance(MM(0.20))
        zone.SetMinThickness(MM(0.20))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        chain = pcbnew.SHAPE_LINE_CHAIN()
        for x, y in points:
            chain.Append(MM(OX + x), MM(OY + y))
        chain.SetClosed(True)
        zone.Outline().AddOutline(chain)
        self.board.Add(zone)

    def add_rule_area(
        self,
        name: str,
        points: Iterable[tuple[float, float]],
        layers: Iterable[int],
        *,
        footprints: bool,
        pads: bool,
        tracks: bool,
        vias: bool,
        fills: bool,
    ) -> None:
        area = pcbnew.ZONE(self.board)
        area.SetIsRuleArea(True)
        area.SetZoneName(name)
        layer_set = pcbnew.LSET()
        for layer in layers:
            layer_set.AddLayer(layer)
        area.SetLayerSet(layer_set)
        area.SetDoNotAllowFootprints(footprints)
        area.SetDoNotAllowPads(pads)
        area.SetDoNotAllowTracks(tracks)
        area.SetDoNotAllowVias(vias)
        area.SetDoNotAllowZoneFills(fills)
        chain = pcbnew.SHAPE_LINE_CHAIN()
        for x, y in points:
            chain.Append(MM(OX + x), MM(OY + y))
        chain.SetClosed(True)
        area.Outline().AddOutline(chain)
        self.board.Add(area)

    def add_planes_and_keepouts(self) -> None:
        outline = [
            (0.5, 0.5),
            (BOARD_W - 0.5, 0.5),
            (BOARD_W - 0.5, BOARD_H - 0.5),
            (0.5, BOARD_H - 0.5),
        ]
        self.add_zone(IN1, "GND", outline, "IN1_SOLID_GND_REFERENCE")
        self.add_zone(B, "GND", outline, "BOTTOM_GND_FILL")
        ax0, ay0, ax1, ay1 = ANTENNA_KEEPOUT
        self.add_rule_area(
            "ESP32_ANTENNA_ALL_COPPER_KEEPOUT",
            [(ax0, ay0), (ax1, ay0), (ax1, ay1), (ax0, ay1)],
            (F, IN1, IN2, B),
            footprints=False,
            pads=False,
            tracks=True,
            vias=True,
            fills=True,
        )
        fx0, fy0, fx1, fy1 = FIXTURE_KEEPOUT
        self.add_rule_area(
            "TP5_TP13_BOTTOM_FIXTURE",
            [(fx0, fy0), (fx1, fy0), (fx1, fy1), (fx0, fy1)],
            (B,),
            footprints=True,
            pads=True,
            tracks=True,
            vias=True,
            fills=True,
        )
        self.add_rule_area(
            "USB_90R_CONTROLLED_CORRIDOR",
            [
                (71.75, 33.0),
                (73.15, 33.0),
                (73.15, 37.0),
                (71.75, 37.0),
            ],
            (F,),
            footprints=False,
            pads=False,
            tracks=False,
            vias=False,
            fills=False,
        )
        for ref, footprint in sorted(self.footprints.items()):
            index = 0
            for pad in footprint.Pads():
                if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                    continue
                index += 1
                position = local(pad.GetPosition())
                drill = pad.GetDrillSize()
                half = max(pcbnew.ToMM(drill.x), pcbnew.ToMM(drill.y)) / 2 + 0.30
                self.add_rule_area(
                    f"NPTH_CLEARANCE_{ref}_{index}",
                    [
                        (position[0] - half, position[1] - half),
                        (position[0] + half, position[1] - half),
                        (position[0] + half, position[1] + half),
                        (position[0] - half, position[1] + half),
                    ],
                    (F, IN1, IN2, B),
                    footprints=False,
                    pads=False,
                    tracks=True,
                    vias=True,
                    fills=True,
                )

    def add_manual_buck_routes(self) -> None:
        self.add_track(
            [self.pad("F1", "2"), self.pad("D1", "2")],
            "+8V_F",
            0.80,
        )
        self.add_track(
            [self.pad("U2", "3"), self.pad("C4", "1")],
            "VIN",
            0.60,
        )
        self.add_track(
            [self.pad("C4", "1"), self.pad("C3", "1")],
            "VIN",
            0.60,
        )
        sw = self.pad("U2", "2")
        l1 = self.pad("L1", "1")
        c5_sw = self.pad("C5", "2")
        self.add_track([sw, l1], "SW_NODE", 0.50)
        self.add_track(
            [c5_sw, (62.4, c5_sw[1]), (62.4, sw[1]), sw],
            "SW_NODE",
            0.30,
        )
        self.add_track(
            [self.pad("U2", "6"), self.pad("C5", "1")],
            "BST",
            0.25,
        )

        fb = self.pad("U2", "4")
        r1 = self.pad("R1", "2")
        r2 = self.pad("R2", "1")
        c12 = self.pad("C12", "2")
        junction = (57.4, 45.8)
        self.add_track(
            [fb, (junction[0], fb[1]), junction],
            "FB",
            0.20,
        )
        self.add_track([junction, r1], "FB", 0.20)
        self.add_track([junction, r2], "FB", 0.20)
        self.add_track([junction, c12], "FB", 0.20)

        uv_top = self.pad("R17", "2")
        uv_bottom = self.pad("R18", "1")
        uv_cap = self.pad("C18", "1")
        uv_junction = (uv_cap[0], 43.4)
        self.add_track([uv_top, uv_bottom], "UV_SENSE", 0.20)
        self.add_track(
            [
                uv_bottom,
                (uv_bottom[0], uv_junction[1]),
                uv_junction,
                (33.0, uv_junction[1]),
                self.pad("U4", "3"),
            ],
            "UV_SENSE",
            0.20,
        )
        self.add_track([uv_junction, uv_cap], "UV_SENSE", 0.20)

        ov_top = self.pad("R19", "2")
        ov_bottom = self.pad("R20", "1")
        ov_cap = self.pad("C19", "1")
        ov_junction = (38.5, 43.4)
        self.add_track([ov_top, ov_bottom], "OV_SENSE", 0.20)
        self.add_track(
            [
                ov_bottom,
                (ov_bottom[0], ov_junction[1]),
                ov_junction,
                (37.0, ov_junction[1]),
                self.pad("U4", "4"),
            ],
            "OV_SENSE",
            0.20,
        )
        self.add_track(
            [ov_junction, (ov_cap[0], ov_junction[1]), ov_cap],
            "OV_SENSE",
            0.20,
        )

    def add_usb_routes(self) -> None:
        # Rev E: J3 sits on the bottom edge at (83.6, 51.2) rot 0 with U3
        # directly north-west at (76.0, 47.3) rot 180.  Connector fan-out
        # keeps the interleaved reversible contacts on F.Cu: D- (B7/A7)
        # joins on the outboard (south) side and dives under the connector
        # NPTH keepouts to reach U3.1 on the ESD array's east column; D+
        # (A6/B6) joins on the inboard (north) side and runs along y=45.4
        # to U3.3.  The two paths never cross despite the reversible A/B
        # contact ordering.
        a7, b7 = self.pad("J3", "A7"), self.pad("J3", "B7")
        a6, b6 = self.pad("J3", "A6"), self.pad("J3", "B6")
        u31, u33 = self.pad("U3", "1"), self.pad("U3", "3")
        dn_join_y = 48.9
        self.add_track([b7, (b7[0], dn_join_y)], "USB_DN", 0.20)
        self.add_track([a7, (a7[0], dn_join_y)], "USB_DN", 0.20)
        self.add_track(
            [
                (b7[0], dn_join_y),
                (a7[0], dn_join_y),
            ],
            "USB_DN",
            USB_CONTROLLED_WIDTH,
        )
        self.add_track(
            [
                (b7[0], dn_join_y),
                (b7[0], 49.9),
                (78.2, 49.9),
                (78.2, u31[1]),
                u31,
            ],
            "USB_DN",
            USB_CONTROLLED_WIDTH,
        )
        dp_join_y = 46.0
        self.add_track([a6, (a6[0], dp_join_y)], "USB_DP", 0.20)
        self.add_track([b6, (b6[0], dp_join_y)], "USB_DP", 0.20)
        self.add_track(
            [
                (b6[0], dp_join_y),
                (a6[0], dp_join_y),
            ],
            "USB_DP",
            USB_CONTROLLED_WIDTH,
        )
        self.add_track(
            [
                (b6[0], dp_join_y),
                (b6[0], 45.4),
                (79.4, 45.4),
                (79.4, u33[1]),
                u33,
            ],
            "USB_DP",
            USB_CONTROLLED_WIDTH,
        )

        # Long post-ESD pair.  The central parallel run uses the official
        # JLC04161H-7628 90-ohm result: 0.2906 mm copper plus a 0.2000 mm
        # edge gap gives 0.4906 mm centre-to-centre separation.  From U3
        # the pair exits west, turns north through the TP12/TP13 gap
        # (verticals at x=72.755 / 73.245), and rejoins the qualified Rev D
        # west-side geometry at y=30.6 / 31.09.  Length matching happens on
        # the D- member (serpentine east of R15) because the D+ member's
        # J3-side and U3-side legs are both shorter.
        u36, u34 = self.pad("U3", "6"), self.pad("U3", "4")
        r151, r161 = self.pad("R15", "1"), self.pad("R16", "1")
        dp_y = 24.000 + USB_ROUTE_SHIFT_Y
        dn_y = dp_y + USB_CENTER_SPACING
        dp_x = 72.45 + USB_CENTER_SPACING / 2
        dn_x = 72.45 - USB_CENTER_SPACING / 2
        self.add_track(
            [
                u36,
                (dn_x, u36[1]),
                (dn_x, 45.0),
                (dn_x, 42.4),
                (dn_x, 39.2),
                (dn_x, dn_y),
                (69.5, dn_y),
                (68.4, dn_y),
                (65.0, dn_y),
                (61.99, dn_y),
                (61.99, r151[1]),
                r151,
            ],
            "USB_DN_MCU",
            USB_CONTROLLED_WIDTH,
        )
        self.add_track(
            [
                u34,
                (dp_x, u34[1]),
                (dp_x, 45.0),
                (dp_x, 42.4),
                (dp_x, 39.2),
                (dp_x, dp_y),
                (71.6, dp_y),
                (71.6, 28.9),
                (70.8, 28.9),
                (70.8, 29.8),
                (70.0, 29.8),
                (70.0, 28.9),
                (69.2, 28.9),
                (69.2, 29.8),
                (68.4, 29.8),
                (68.4, dp_y),
                (65.0, dp_y),
                (62.483742, dp_y),
                (62.483742, r161[1]),
                r161,
            ],
            "USB_DP_MCU",
            USB_CONTROLLED_WIDTH,
        )

        r152, r162 = self.pad("R15", "2"), self.pad("R16", "2")
        u113, u114 = self.pad("U1", "13"), self.pad("U1", "14")
        c131, c141 = self.pad("C13", "1"), self.pad("C14", "1")
        self.add_track([r152, u113], "USB_DN_R", USB_CONTROLLED_WIDTH)
        self.add_track([r162, u114], "USB_DP_R", USB_CONTROLLED_WIDTH)
        self.add_track(
            [c131, (c131[0], r152[1]), r152],
            "USB_DN_R",
            USB_CONTROLLED_WIDTH,
        )
        self.add_track(
            [c141, (c141[0], r162[1]), r162],
            "USB_DP_R",
            USB_CONTROLLED_WIDTH,
        )
        # The paired A/B ground contacts share copper and tie directly to the
        # plated shell stakes; those stakes provide the plane connection.
        shell = self.pads("J3", "S1")
        for number in ("A1", "A12", "B1", "B12"):
            contact = self.pad("J3", number)
            nearest = min(shell, key=lambda candidate: math.dist(contact, candidate))
            self.add_track([contact, nearest], "GND", 0.30)

        # VBUS is only sensed by U3.  Both reversible connector contact
        # groups drop onto a B.Cu spine that runs below the connector,
        # feeding U3.5, C11, Q2's gate, and the R29 bleed.
        a4 = self.pad("J3", "A4")
        a9 = self.pad("J3", "A9")
        vbus_west = (a4[0], 46.05)
        # Rev E flush-jack: 44.9 -> 45.3 keeps the via barrel 0.2 mm clear
        # of J2's relocated mechanical tab pad 9 (copper to (86.445, 44.62)).
        vbus_east = (a9[0], 45.3)
        self.add_track([a4, vbus_west], "VBUS", 0.30)
        self.add_track([a9, vbus_east], "VBUS", 0.30)
        u35 = self.pad("U3", "5")
        u35_via = (73.8, 47.3)
        self.add_track([u35, u35_via], "VBUS", 0.30)
        for point in (vbus_west, vbus_east, u35_via):
            self.add_via(point, "VBUS")
        # B.Cu spine: U3.5 -> south lane (y=49.9, below the connector NPTH
        # keepouts, between the shell-stake rows) -> east riser to the two
        # connector contact vias, with spurs to Q2.1, C11.1, and R29.1.
        q21 = self.pad("Q2", "1")
        q21_via = (76.4, 52.9)
        self.add_track([q21, (q21[0], 52.9), q21_via], "VBUS", 0.25)
        self.add_via(q21_via, "VBUS")
        c111 = self.pad("C11", "1")
        c111_via = (90.2, 47.9)
        self.add_track([c111, c111_via], "VBUS", 0.25)
        self.add_via(c111_via, "VBUS")
        r291 = self.pad("R29", "1")
        r291_via = (67.8, 44.9)
        self.add_track([r291, (67.8, r291[1]), r291_via], "VBUS", 0.25)
        self.add_via(r291_via, "VBUS")
        self.add_track(
            [
                r291_via,
                (67.8, 45.4),
                u35_via,
                (73.8, 49.9),
                (76.4, 49.9),
                (85.0, 49.9),
                (88.9, 49.9),
                (88.9, 45.9),
                (86.05, 45.9),
                vbus_east,
            ],
            "VBUS",
            0.30,
            B,
        )
        self.add_track([q21_via, (76.4, 49.9)], "VBUS", 0.30, B)
        self.add_track([(88.9, 47.9), c111_via], "VBUS", 0.30, B)
        self.add_track(
            [
                vbus_east,
                (82.6, 44.9),
                (81.15, 45.75),
                vbus_west,
            ],
            "VBUS",
            0.30,
            B,
        )

        # CC pull-downs: R4 east of the connector, R5 south-west of it.
        # CC2 crosses the VBUS spine on In2.Cu (CC nets carry no layer
        # policy restriction).
        a5 = self.pad("J3", "A5")
        cc1_contact_via = (82.1, 49.0)
        self.add_track([a5, (a5[0], 48.7), cc1_contact_via], "CC1", 0.20)
        self.add_via(cc1_contact_via, "CC1")
        r41 = self.pad("R4", "1")
        cc1_r_via = (91.6, 47.9)
        self.add_track([r41, (92.0, 47.9), cc1_r_via], "CC1", 0.20)
        self.add_via(cc1_r_via, "CC1")
        self.add_track(
            [
                cc1_contact_via,
                (82.1, 44.0),
                (90.8, 44.0),
                (91.6, 45.0),
                cc1_r_via,
            ],
            "CC1",
            0.20,
            IN2,
        )
        b5 = self.pad("J3", "B5")
        cc2_contact_via = (b5[0], 46.0)
        self.add_track([b5, cc2_contact_via], "CC2", 0.20)
        self.add_via(cc2_contact_via, "CC2")
        r51 = self.pad("R5", "1")
        cc2_r_via = (74.4, 54.0)
        self.add_track([r51, cc2_r_via], "CC2", 0.20)
        self.add_via(cc2_r_via, "CC2")
        self.add_track(
            [
                cc2_contact_via,
                (85.35, 53.8),
                (74.4, 53.8),
                cc2_r_via,
            ],
            "CC2",
            0.20,
            IN2,
        )

    def add_shifted_u1_bus_route(self) -> None:
        """Preserve the qualified CONS6 tree after the coupled U1 shift."""
        escapes = {
            # J1 pad 6 sits in the tight 1.27 mm RJ45 pitch row (see
            # power_intent.py); escape straight WEST under the jack body
            # at the pad's own Y.  X=13.2 threads the under-body via
            # field: >=1.5 mm from the +8V_RAW B.Cu bus (X=15.45) and the
            # GND branch column (X=11.4), and leaves the X~13.6 escape
            # column free for J1 pads 3-5.  Onward routing is on IN2 (see
            # below): west to X=8.6 (between the GND via columns and the
            # SMD tab pad), then north past the whole cluster.
            ("J1", "6"): (13.2, 38.9),
            ("D5", "1"): (27.2, 13.2),
            ("K1", "2"): (25.6, 22.4),
            # Rev E: lifted from (62.0, 19.6) so the CONS6 In2 leg rides
            # y~18.4 and leaves the y 19.2-20.4 rows to the U1<->U6 bus.
            ("R7", "1"): (62.4, 18.4),
        }
        for endpoint, via in escapes.items():
            self.add_track([self.pad(*endpoint), via], "CONS6", 0.20)
            self.add_via(via, "CONS6")
        junction = (31.2, 18.0)
        self.add_track(
            [
                escapes[("J1", "6")],
                (8.6, 38.9),
                (8.6, 16.0),
                (20.0, 16.0),
                junction,
                escapes[("R7", "1")],
            ],
            "CONS6",
            0.20,
            IN2,
        )
        self.add_track(
            [escapes[("D5", "1")], junction],
            "CONS6",
            0.20,
            IN2,
        )
        self.add_track(
            [escapes[("K1", "2")], junction],
            "CONS6",
            0.20,
            IN2,
        )

    def add_two_amp_power_routes(self) -> None:
        """Emit only the exact, reviewable redundant power-copper intent."""
        # Rev E: the full-board pass-through adds In2.Cu twins (+8V_RAW)
        # and In1.Cu strips (GND, merging with the plane).
        layer_ids = {"F.Cu": F, "In1.Cu": IN1, "In2.Cu": IN2, "B.Cu": B}
        for route in power_intent.ROUTES:
            self.add_track(
                route["points"],
                route["net"],
                route["width_mm"],
                layer_ids[route["layer"]],
            )
        for via in power_intent.VIAS:
            self.add_via(
                via["at"],
                via["net"],
                size=via["size_mm"],
                drill=via["drill_mm"],
            )

    def add_vbus_present_route(self) -> None:
        # Rev E: Q2/R30 live south of the relocated USB cluster.  The In2
        # spine runs north at x=75.8 (through the U1 escape row -- laid
        # before the router, so those escapes spiral around it) and turns
        # west at y=23.0 to U1.7's via.
        escapes = {
            ("U1", "7"): (
                (70.8, 15.6),
                [self.pad("U1", "7"), (70.8, 15.41), (70.8, 15.6)],
            ),
            ("R30", "2"): (
                (72.0, 49.5),
                [self.pad("R30", "2"), (72.0, 49.5)],
            ),
            ("Q2", "3"): (
                (76.4, 49.2),
                [self.pad("Q2", "3"), (76.4, 49.2)],
            ),
        }
        for via, dogleg in escapes.values():
            self.add_track(dogleg, "VBUS_PRESENT_N", 0.20)
            self.add_via(via, "VBUS_PRESENT_N")
        self.add_track(
            [
                escapes[("R30", "2")][0],
                (75.8, 49.5),
                (75.8, 23.0),
                (70.8, 23.0),
                escapes[("U1", "7")][0],
            ],
            "VBUS_PRESENT_N",
            0.20,
            IN2,
        )
        self.add_track(
            [
                escapes[("Q2", "3")][0],
                (75.8, 49.2),
            ],
            "VBUS_PRESENT_N",
            0.20,
            IN2,
        )

    def outward_escape(
        self,
        ref: str,
        pad: Any,
        net: str,
    ) -> tuple[tuple[float, float], list[tuple[float, float]]]:
        point = local(pad.GetPosition())
        escape_width = min(WIDTHS.get(net, 0.20), 0.25)
        locked = LOCKED_DFM_ESCAPES.get((ref, str(pad.GetNumber())))
        if locked is not None:
            node = (grid_index(locked[0]), grid_index(locked[1]))
            target = grid_point(node)
            if target != locked:
                raise RuntimeError(f"locked escape for {ref}.{pad.GetNumber()} is off-grid")
            checks = {
                "inside": self.node_inside(node),
                "in2": self.cell_allowed(node, IN2, net),
                "bottom": self.cell_allowed(node, B, net),
                "via": self.via_allowed(target, net),
                "front": self.front_segment_allowed(
                    point,
                    target,
                    net,
                    escape_width,
                ),
            }
            if not all(checks.values()):
                raise RuntimeError(
                    f"locked escape unavailable for " f"{ref}.{pad.GetNumber()} {net} at {target}: {checks}"
                )
            return target, [point, target]
        if ref == "J2" and str(pad.GetNumber()) in PLANNED_J2_ESCAPES:
            # J2's pads escape +X -- under the jack's own body -- because
            # its interior side is walled by the fixture keepout (no
            # through vias at x<77, y 33.8-38.2); the slots are planned
            # (see PLANNED_J2_ESCAPES).
            desired = PLANNED_J2_ESCAPES[str(pad.GetNumber())]
        elif ref == "J1" and str(pad.GetNumber()) in {"3", "4", "5"}:
            # The RJ45's 1.27 mm pad pitch (see power_intent.py) makes the
            # generic center-relative heuristic below pick a too-close
            # escape (it measures "outward" from the footprint center,
            # which sits deep inside the jack shell, not from the pad row)
            # -- push straight out at the pad's own Y instead, comfortably
            # past the JLC vendor pad-to-via clearance.  J1's mating face
            # is flush with the left edge, so its pad row faces the OPEN
            # interior: escape +X (the under-body side is a walled pocket:
            # +8V_RAW twin/bus columns plus the GND via rows would strand
            # the vias there).  Pad 6 (CONS6) is routed manually in
            # add_shifted_u1_bus_route.
            desired = (point[0] + 4.0, point[1])
        elif (ref, str(pad.GetNumber())) == ("D7", "2"):
            # Keep D7's GND stitch out of U4.5's only escape gap between
            # the D and F ground strips.
            desired = (38.6, 38.6)
        elif (ref, str(pad.GetNumber())) == ("C13", "2"):
            # Keep C13's GND stitch out of the single-file U1 west escape
            # column (it otherwise walls U1.9/U1.10 in).
            desired = (66.0, 18.0)
        elif (ref, str(pad.GetNumber())) == ("U4", "5"):
            # VIN pad boxed between the D/F ground strips' via-exclusion
            # bands; escape east into the local gap opened by strip F's
            # dodge around U4.
            desired = (37.6, 42.8)
        elif (ref, str(pad.GetNumber())) == ("R26", "2"):
            desired = (35.2, 32.8)
        elif ref == "U1" and str(pad.GetNumber()) in PLANNED_U1_WEST_ESCAPES:
            desired = PLANNED_U1_WEST_ESCAPES[str(pad.GetNumber())]
        elif (ref, str(pad.GetNumber())) == ("U1", "5"):
            desired = (68.4, 18.0 + U1_PROFILE_SHIFT_Y)
        elif (ref, str(pad.GetNumber())) == ("U1", "7"):
            desired = (70.8, 15.6)
        elif ref == "U6":
            desired = PLANNED_U6_ESCAPES[str(pad.GetNumber())][0]
        elif ref == "U7" and str(pad.GetNumber()) in PLANNED_U7_ESCAPES:
            desired = PLANNED_U7_ESCAPES[str(pad.GetNumber())][0]
        elif ref.startswith("TP") and 5 <= int(ref[2:]) <= 13:
            desired = (point[0], 32.8)
        else:
            center = local(self.footprints[ref].GetPosition())
            dx, dy = point[0] - center[0], point[1] - center[1]
            if abs(dx) >= abs(dy):
                direction = 1.0 if dx >= 0 else -1.0
                desired = (point[0] + direction * 1.2, point[1])
            else:
                direction = 1.0 if dy >= 0 else -1.0
                desired = (point[0], point[1] + direction * 1.2)

        preferred = (grid_index(desired[0]), grid_index(desired[1]))
        candidates: list[tuple[int, int, int]] = []
        for radius in range(0, 30):
            for dx in range(-radius, radius + 1):
                for dy in (-radius, radius):
                    candidates.append((abs(dx) + abs(dy), preferred[0] + dx, preferred[1] + dy))
            for dy in range(-radius + 1, radius):
                for dx in (-radius, radius):
                    candidates.append((abs(dx) + abs(dy), preferred[0] + dx, preferred[1] + dy))
            for _, ix, iy in sorted(set(candidates)):
                candidate = (ix, iy)
                if not self.node_inside(candidate):
                    continue
                if self.cell_allowed(candidate, IN2, net) and self.cell_allowed(candidate, B, net):
                    target = grid_point(candidate)
                    if not self.via_allowed(target, net):
                        continue
                    doglegs = (
                        [point, (target[0], point[1]), target],
                        [point, (point[0], target[1]), target],
                    )
                    if abs(point[0] - target[0]) < abs(point[1] - target[1]):
                        doglegs = tuple(reversed(doglegs))
                    for dogleg in doglegs:
                        if all(
                            self.front_segment_allowed(
                                start,
                                end,
                                net,
                                escape_width,
                            )
                            for start, end in zip(dogleg, dogleg[1:])
                        ):
                            return target, dogleg
        raise RuntimeError(f"no via escape for {ref}.{pad.GetNumber()} {net}")

    def via_allowed(
        self,
        point: tuple[float, float],
        net: str,
    ) -> bool:
        for x0, y0, x1, y1 in SILK_VIA_KEEPOUTS:
            if x0 <= point[0] <= x1 and y0 <= point[1] <= y1:
                return False

        for x, y, half_width, half_height, pad_net in self.front_pad_obstacles:
            if pad_net == net:
                continue
            if abs(point[0] - x) < half_width + 0.50 and abs(point[1] - y) < half_height + 0.50:
                return False

        for footprint in self.footprints.values():
            for pad in footprint.Pads():
                drill = pcbnew.ToMM(pad.GetDrillSize().x)
                if drill <= 0:
                    continue
                pad_point = local(pad.GetPosition())
                if math.dist(point, pad_point) < 0.15 + drill / 2 + 0.25:
                    return False

        for existing, existing_net in self.via_records:
            distance = math.dist(point, existing)
            if distance < 1e-6 and existing_net == net:
                continue
            existing_size, existing_drill = self.via_geometry[(existing, existing_net)]
            if (
                distance
                < max(
                    0.30 + existing_size / 2 + CLEARANCE,
                    0.15 + existing_drill / 2 + 0.25,
                )
                - 1e-6
            ):
                return False

        # Every via this generator places is a full through-hole (F.Cu to
        # B.Cu), so it carries copper on every layer of the stackup, not
        # just F.Cu. Clearance to a manually-routed track on an inner layer
        # (e.g. a MANUAL_NETS pass-through on In2.Cu) matters exactly as
        # much as clearance to a front-copper track; checking only F here
        # let a via land on top of an inner-layer track with no margin.
        for start, end, track_net, width, layer in self.track_records:
            distance = point_segment_distance(point, start, end)
            if track_net == net:
                if 1e-6 < distance < 0.30 + width / 2:
                    return False
                continue
            if distance < 0.30 + width / 2 + CLEARANCE - 1e-6:
                return False
        return True

    def front_segment_allowed(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        net: str,
        width: float,
    ) -> bool:
        expansion = width / 2 + CLEARANCE
        length = math.dist(start, end)
        samples = max(1, math.ceil(length / 0.08))
        for index in range(samples + 1):
            fraction = index / samples
            point = (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
            for x, y, half_width, half_height, pad_net in self.front_pad_obstacles:
                if pad_net == net:
                    continue
                if abs(point[0] - x) < half_width + expansion and abs(point[1] - y) < half_height + expansion:
                    return False

        for other_start, other_end, track_net, other_width, layer in self.track_records:
            if layer != F or track_net == net:
                continue
            if segment_distance(start, end, other_start, other_end) < (width / 2 + other_width / 2 + CLEARANCE) - 1e-6:
                return False

        for via, via_net in self.via_records:
            if via_net == net:
                continue
            via_size, _ = self.via_geometry[(via, via_net)]
            if point_segment_distance(via, start, end) < (via_size / 2 + width / 2 + CLEARANCE):
                return False
        return True

    def node_inside(self, node: tuple[int, int]) -> bool:
        x, y = grid_point(node)
        return 0.8 <= x <= BOARD_W - 0.8 and 0.8 <= y <= BOARD_H - 0.8

    def cell_allowed(
        self,
        node: tuple[int, int],
        layer: int,
        net: str,
    ) -> bool:
        if node in self.blocked[layer]:
            return False
        owners = self.occupied[layer].get(node, set())
        return not owners or owners == {net}

    def mark_circle(
        self,
        point: tuple[float, float],
        radius: float,
        layers: Iterable[int],
        owner: str | None,
    ) -> None:
        cx, cy = grid_index(point[0]), grid_index(point[1])
        cells = math.ceil(radius / GRID)
        for ix in range(cx - cells, cx + cells + 1):
            for iy in range(cy - cells, cy + cells + 1):
                candidate = grid_point((ix, iy))
                if math.dist(point, candidate) > radius + GRID * 0.72:
                    continue
                for layer in layers:
                    if owner is None:
                        self.blocked[layer].add((ix, iy))
                    else:
                        self.occupied[layer][(ix, iy)].add(owner)

    def mark_segment(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        width: float,
        layers: Iterable[int],
        owner: str | None,
        extra: float = CLEARANCE,
    ) -> None:
        length = math.dist(start, end)
        samples = max(1, math.ceil(length / (GRID / 2)))
        for index in range(samples + 1):
            fraction = index / samples
            point = (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
            self.mark_circle(point, width / 2 + extra, layers, owner)

    def initialise_router_obstacles(self) -> None:
        ax0, ay0, ax1, ay1 = ANTENNA_KEEPOUT
        fx0, fy0, fx1, fy1 = FIXTURE_KEEPOUT
        # The ESP32-S3-WROOM-1 module (and any other stock footprint) embeds
        # the manufacturer's own all-copper-layer rule areas — for U1 that is
        # the antenna keepout spanning the whole top edge strip.  KiCad DRC
        # does not flag rule-area track intrusions in this project, so the
        # grid router must honour those zones itself or it will lay tracks
        # under the inverted-F antenna (this happened in the first Rev E
        # reroute: U0TXD/U0RXD crossed the antenna at y=5.6).
        module_keepouts: list[tuple[float, float, float, float, tuple[int, ...]]] = []
        for footprint in self.footprints.values():
            for zone in footprint.Zones():
                if not zone.GetIsRuleArea() or not zone.GetDoNotAllowTracks():
                    continue
                layers = tuple(layer for layer in ROUTING_LAYERS if zone.IsOnLayer(layer))
                if not layers:
                    continue
                chain = zone.Outline().COutline(0)
                xs = [pcbnew.ToMM(chain.CPoint(i).x) - OX for i in range(chain.PointCount())]
                ys = [pcbnew.ToMM(chain.CPoint(i).y) - OY for i in range(chain.PointCount())]
                module_keepouts.append((min(xs), min(ys), max(xs), max(ys), layers))
        # The router places track CENTRES on grid cells, so a cell just
        # outside an all-copper keepout can still push a wide track's edge
        # (up to 0.3 mm for the 0.6 mm power nets) inside the zone.  One
        # grid step of margin around the antenna zones keeps every routed
        # width's copper fully outside (the audit measures true copper
        # extents, not centrelines).
        margin = GRID
        for ix in range(grid_index(0.8), grid_index(BOARD_W - 0.8) + 1):
            for iy in range(grid_index(0.8), grid_index(BOARD_H - 0.8) + 1):
                x, y = grid_point((ix, iy))
                if ax0 - margin <= x <= ax1 + margin and ay0 - margin <= y <= ay1 + margin:
                    self.blocked[IN2].add((ix, iy))
                    self.blocked[B].add((ix, iy))
                if fx0 <= x <= fx1 and fy0 <= y <= fy1:
                    self.blocked[B].add((ix, iy))
                for kx0, ky0, kx1, ky1, layers in module_keepouts:
                    if kx0 - margin <= x <= kx1 + margin and ky0 - margin <= y <= ky1 + margin:
                        for layer in layers:
                            self.blocked[layer].add((ix, iy))

        for footprint in self.footprints.values():
            for pad in footprint.Pads():
                if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    drill = pad.GetDrillSize()
                    self.mark_circle(
                        local(pad.GetPosition()),
                        max(
                            pcbnew.ToMM(drill.x),
                            pcbnew.ToMM(drill.y),
                        )
                        / 2
                        + 0.25,
                        ROUTING_LAYERS,
                        None,
                    )
                if not pad.IsOnLayer(F):
                    continue
                position = local(pad.GetPosition())
                bounds = pad.GetBoundingBox()
                self.front_pad_obstacles.append(
                    (
                        position[0],
                        position[1],
                        pcbnew.ToMM(bounds.GetWidth()) / 2,
                        pcbnew.ToMM(bounds.GetHeight()) / 2,
                        pad.GetNetname(),
                    )
                )

        for point, net in PLANNED_U6_ESCAPES.values():
            self.mark_circle(point, 0.50, ROUTING_LAYERS, net)
        for net, points in PLANNED_ROUTE_RESERVATIONS:
            for start, end in zip(points, points[1:]):
                self.mark_segment(start, end, 0.20, (IN2,), net, extra=0.30)
        for point, net in PLANNED_U7_ESCAPES.values():
            self.mark_circle(point, 0.50, ROUTING_LAYERS, net)
        for number, point in PLANNED_J2_ESCAPES.items():
            net = self.pad_net.get(("J2", number))
            if net:
                self.mark_circle(point, 0.50, ROUTING_LAYERS, net)
        for number, point in PLANNED_U1_WEST_ESCAPES.items():
            net = self.pad_net.get(("U1", number))
            if net:
                self.mark_circle(point, 0.50, ROUTING_LAYERS, net)
        for (ref, number), point in LOCKED_DFM_ESCAPES.items():
            net = self.pad_net.get((ref, number))
            if net:
                self.mark_circle(point, 0.50, ROUTING_LAYERS, net)

        for (ref, number), pads in self.pad_objects.items():
            net = self.pad_net.get((ref, number))
            if not net:
                continue
            for pad in pads:
                position = local(pad.GetPosition())
                bounds = pad.GetBoundingBox()
                width = pcbnew.ToMM(bounds.GetWidth())
                height = pcbnew.ToMM(bounds.GetHeight())
                for layer in ROUTING_LAYERS:
                    if not pad.IsOnLayer(layer):
                        continue
                    rx = width / 2 + CLEARANCE + 0.15
                    ry = height / 2 + CLEARANCE + 0.15
                    for ix in range(
                        grid_index(position[0] - rx),
                        grid_index(position[0] + rx) + 1,
                    ):
                        for iy in range(
                            grid_index(position[1] - ry),
                            grid_index(position[1] + ry) + 1,
                        ):
                            self.occupied[layer][(ix, iy)].add(net)

        for start, end, net, width, layer in self.track_records:
            if layer in ROUTING_LAYERS:
                self.mark_segment(start, end, width, (layer,), net)
            elif layer == F and net in USB_NETS and min(start[0], end[0]) >= 71.0 and max(start[0], end[0]) <= 75.5:
                # Only the controlled vertical corridor (x~72.4-73.6 plus
                # its U3-side entry stubs) carries the 0.8 mm
                # unrelated-copper contract, and only across the corridor
                # window's y-span; reserving every USB stub at this width
                # would wall off the U1 escape region and the south-east
                # crossing columns.  The low-speed router places 0.60 mm
                # vias on this grid, so the fat portion reserves the
                # complete USB-to-via centre distance required by the
                # 0.8 mm unrelated-copper rule, not merely the USB trace
                # width.  Outside the window the thin reservation plus the
                # normal DRC clearance checks suffice.
                if max(start[1], end[1]) >= 32.5 and min(start[1], end[1]) <= 37.5:
                    clip_a = (start[0], min(max(start[1], 32.5), 43.2))
                    clip_b = (end[0], min(max(end[1], 32.5), 43.2))
                    self.mark_segment(
                        clip_a,
                        clip_b,
                        width,
                        ROUTING_LAYERS,
                        net,
                        extra=USB_UNRELATED_CLEARANCE + ROUTER_VIA_RADIUS,
                    )
                self.mark_segment(start, end, width, ROUTING_LAYERS, net)
        for point, net in self.via_records:
            size, _ = self.via_geometry[(point, net)]
            self.mark_circle(
                point,
                size / 2 + CLEARANCE,
                ROUTING_LAYERS,
                net,
            )

    def endpoint_nodes(
        self,
        net: str,
    ) -> list[tuple[int, int]]:
        endpoints: list[tuple[int, int]] = []
        for ref, number in design.NETS[net]:
            for pad in self.pad_objects[(ref, number)]:
                target, dogleg = self.outward_escape(ref, pad, net)
                self.add_track(
                    dogleg,
                    net,
                    min(WIDTHS.get(net, 0.20), 0.25),
                    F,
                )
                self.add_via(target, net)
                node = (grid_index(target[0]), grid_index(target[1]))
                endpoints.append(node)
                self.mark_circle(target, 0.50, ROUTING_LAYERS, net)
        return endpoints

    def astar(
        self,
        starts: set[tuple[int, int, int]],
        goals: set[tuple[int, int, int]],
        net: str,
        allowed_layers: tuple[int, ...],
    ) -> list[tuple[int, int, int]]:
        if starts & goals:
            return [next(iter(starts & goals))]
        min_x = min(node[0] for node in goals)
        max_x = max(node[0] for node in goals)
        min_y = min(node[1] for node in goals)
        max_y = max(node[1] for node in goals)

        def heuristic(state: tuple[int, int, int]) -> float:
            x, y, _ = state
            dx = max(min_x - x, 0, x - max_x)
            dy = max(min_y - y, 0, y - max_y)
            return dx + dy

        queue: list[tuple[float, float, tuple[int, int, int]]] = []
        distance: dict[tuple[int, int, int], float] = {}
        previous: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        for start in sorted(starts):
            distance[start] = 0.0
            heapq.heappush(queue, (heuristic(start), 0.0, start))
        visited = 0
        while queue:
            _, cost, state = heapq.heappop(queue)
            if cost != distance.get(state):
                continue
            if state in goals:
                path = [state]
                while state in previous:
                    state = previous[state]
                    path.append(state)
                return list(reversed(path))
            visited += 1
            if visited > 500_000:
                break
            x, y, layer_index = state
            candidates = [
                (x + 1, y, layer_index, 1.0),
                (x - 1, y, layer_index, 1.0),
                (x, y + 1, layer_index, 1.0),
                (x, y - 1, layer_index, 1.0),
            ]
            for other_index in range(len(allowed_layers)):
                if other_index != layer_index:
                    candidates.append((x, y, other_index, 7.0))
            for nx, ny, next_layer_index, step_cost in candidates:
                node = (nx, ny)
                layer = allowed_layers[next_layer_index]
                if not self.node_inside(node):
                    continue
                if not self.cell_allowed(node, layer, net):
                    continue
                if next_layer_index != layer_index and not self.via_allowed(grid_point(node), net):
                    continue
                next_state = (nx, ny, next_layer_index)
                new_cost = cost + step_cost
                if new_cost >= distance.get(next_state, math.inf):
                    continue
                distance[next_state] = new_cost
                previous[next_state] = state
                heapq.heappush(
                    queue,
                    (
                        new_cost + heuristic(next_state),
                        new_cost,
                        next_state,
                    ),
                )
        start_points = sorted({grid_point((state[0], state[1])) for state in starts})
        raise RuntimeError(
            f"autorouter cannot connect {net}; starts={len(starts)} "
            f"goals={len(goals)} visited={visited} "
            f"start_points={start_points[:6]}"
        )

    def emit_grid_path(
        self,
        path: list[tuple[int, int, int]],
        net: str,
        allowed_layers: tuple[int, ...],
    ) -> set[tuple[int, int, int]]:
        width = WIDTHS.get(net, 0.20)
        tree_additions: set[tuple[int, int, int]] = set(path)
        run: list[tuple[float, float]] = []
        run_layer: int | None = None
        previous_direction: tuple[int, int] | None = None

        def flush() -> None:
            nonlocal run
            if len(run) >= 2 and run_layer is not None:
                endpoints = [run[0], run[-1]]
                self.add_track(endpoints, net, width, run_layer)
                self.mark_segment(
                    endpoints[0],
                    endpoints[1],
                    width,
                    (run_layer,),
                    net,
                )
            run = []

        for index, state in enumerate(path):
            x, y, layer_index = state
            layer = allowed_layers[layer_index]
            point = grid_point((x, y))
            if index and path[index - 1][2] != layer_index:
                flush()
                self.add_via(point, net)
                self.mark_circle(point, 0.50, ROUTING_LAYERS, net)
                previous_direction = None
            direction = None
            if index + 1 < len(path) and path[index + 1][2] == layer_index:
                direction = (
                    path[index + 1][0] - x,
                    path[index + 1][1] - y,
                )
            if not run:
                run = [point]
                run_layer = layer
            elif previous_direction is not None and direction != previous_direction:
                run.append(point)
                flush()
                run = [point]
                run_layer = layer
            if run[-1] != point:
                run.append(point)
            previous_direction = direction
        flush()
        return tree_additions

    def route_net(self, net: str) -> None:
        endpoint_nodes = self.endpoint_nodes(net)
        unique = sorted(set(endpoint_nodes))
        if len(unique) < 2:
            return
        allowed_layers = ROUTING_LAYERS
        tree: set[tuple[int, int, int]] = {(unique[0][0], unique[0][1], index) for index in range(len(allowed_layers))}
        remaining = set(unique[1:])
        while remaining:
            endpoint = min(
                remaining,
                key=lambda node: min(abs(node[0] - tree_node[0]) + abs(node[1] - tree_node[1]) for tree_node in tree),
            )
            starts = {(endpoint[0], endpoint[1], index) for index in range(len(allowed_layers))}
            path = self.astar(starts, tree, net, allowed_layers)
            additions = self.emit_grid_path(path, net, allowed_layers)
            tree.update(additions)
            tree.update((endpoint[0], endpoint[1], index) for index in range(len(allowed_layers)))
            remaining.remove(endpoint)

    def route_slow_nets(self) -> None:
        # GEN_PCB_DEBUG_DUMP is a router-debugging aid: set it to any value
        # to dump the emitted copper and the grid ownership maps to /tmp on
        # a routing failure, or set it to a net name to stop deterministically
        # just before that net routes (the dump then shows the exact state
        # its A* search would see).  It never affects a successful build.
        debug = os.environ.get("GEN_PCB_DEBUG_DUMP")
        try:
            for net in slow_net_order():
                if debug == net:
                    raise RuntimeError(f"debug stop before routing {net}")
                self.route_net(net)
        except RuntimeError:
            if debug:
                with open("/tmp/genpcb_tracks.txt", "w") as handle:
                    for start, end, net, width, layer in self.track_records:
                        handle.write(f"{net}\t{layer}\t{start}\t{end}\t{width}\n")
                    for point, net in self.via_records:
                        handle.write(f"VIA\t{net}\t{point}\n")
                with open("/tmp/genpcb_cells.txt", "w") as handle:
                    for layer in ROUTING_LAYERS:
                        for node in sorted(self.blocked[layer]):
                            handle.write(f"BLOCKED\t{layer}\t{grid_point(node)}\n")
                        for node, owners in sorted(self.occupied[layer].items()):
                            handle.write(f"OWNED\t{layer}\t{grid_point(node)}\t{sorted(owners)}\n")
            raise

    def add_ground_connections(self) -> None:
        for ref, number in design.NETS["GND"]:
            if ref in {"J1", "J2", "J3"}:
                continue
            for pad in self.pad_objects[(ref, number)]:
                if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                    continue
                point = local(pad.GetPosition())
                if ref == "U3" and number == "2":
                    target = point
                    dogleg = [point]
                else:
                    target, dogleg = self.outward_escape(ref, pad, "GND")
                self.add_track(dogleg, "GND", 0.30, F)
                self.add_via(target, "GND")
                self.mark_circle(target, 0.50, ROUTING_LAYERS, "GND")

    def add_silkscreen(self) -> None:
        def text(
            x: float,
            y: float,
            value: str,
            size: float = 1.0,
            rotation: float = 0.0,
        ) -> None:
            item = pcbnew.PCB_TEXT(self.board)
            item.SetText(value)
            item.SetPosition(absolute((x, y)))
            item.SetLayer(pcbnew.F_SilkS)
            item.SetTextSize(VECTOR2I(MM(size), MM(size)))
            item.SetTextThickness(MM(0.20))
            item.SetTextAngle(pcbnew.EDA_ANGLE(rotation, pcbnew.DEGREES_T))
            self.board.Add(item)

        # Rev E: both jacks are flush with their short edges at Y=37, pad
        # rows facing the interior.  CONSOLE sits east of J1's pad row
        # and its x~21.6 escape-via column, vertical to fit the corridor
        # before TP3/R25.
        text(23.0, 37.0, "CONSOLE", 1.0, 90.0)
        # MOTOR sits in the corridor between the USB pair verticals and
        # J2's pad row.
        text(72.6, 40.6, "MOTOR", 1.0, 90.0)
        # J2's pad 1 is the BOTTOM of its row (Y=41.45); mark it from the
        # clear pocket south-west of the pad row, east of the USB pair
        # verticals and MOTOR.
        text(74.9, 43.3, "P1", 1.0)
        text(58.0, 3.0, "Esp32Tap rev E", 1.2)
        # Placement locks keep every fabrication label at least 0.5 mm from
        # the nominal installed component bodies as well as mask openings.
        text(42.0, 10.0, "BYPASS", 1.0)
        text(36.0, 10.0, "NC", 1.0)
        text(29.5, 37.0, "EMULATE", 1.0)
        text(42.0, 35.0, "NO", 1.0)
        text(50.0, 8.0, "USB DATA ONLY", 1.0)
        # J1's pad 1 is the TOP of its row (Y=32.55); mark it from the
        # clear pocket north-east of the pad row, west of R25/K1.
        text(21.5, 31.0, "PIN 1", 1.0)
        text(26.5, 48.0, "D1 K", 1.0, 90.0)
        text(35.0, 48.0, "K D3", 1.0)
        # Rev D: 22.5 -> 23.0 -- J1's RJ45 fabrication body now reaches to
        # X=19.9 (its rear mechanical-tab cap, deeper than Rev C's
        # Micro-Fit body), so the old X left only 0.07 mm component-body
        # clearance instead of the required 0.5 mm; 23.0 threads the gap
        # between that and K1 pad 1's soldermask (>=0.25 mm needed there).
        text(23.0, 19.2, "K1 P1", 1.0)
        text(91.0, 21.0, "LED1 K", 1.0)
        # Rev E flush-jack: (14.0, 30.0) is now inside J1's body; the
        # label moves to the free strip north of the jack.
        text(14.0, 15.0, "K LED2", 1.0)

    def fill_and_save(self) -> None:
        filler = pcbnew.ZONE_FILLER(self.board)
        filler.Fill(self.board.Zones())
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{self.output.stem}-",
            dir=self.output.parent,
        ) as staging:
            temporary = Path(staging) / self.output.name
            pcbnew.SaveBoard(str(temporary), self.board)
            source = temporary.read_text(encoding="utf-8")
            source = re.sub(
                r"\(general\s*\n\s*\(thickness [^\)]+\)",
                "(general\n\t\t(thickness 1.59)",
                source,
                count=1,
            )
            stackup = """\
\t\t(stackup
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.2104)
\t\t\t\t(material "7628 RC49%") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.065)
\t\t\t\t(material "NP-155F") (epsilon_r 4.38) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.2104)
\t\t\t\t(material "7628 RC49%") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(copper_finish "ENIG")
\t\t\t(dielectric_constraints yes)
\t\t)
"""
            if "(stackup" in source:
                raise ValueError("pcbnew unexpectedly emitted stackup metadata")
            source = source.replace("\t(setup\n", "\t(setup\n" + stackup, 1)
            temporary.write_text(source, encoding="utf-8")
            check = pcbnew.LoadBoard(str(temporary))
            if check is None or check.GetCopperLayerCount() != 4:
                raise ValueError("generated board failed KiCad round-trip")
            os.replace(temporary, self.output)

    def generate(self) -> None:
        self.add_footprints()
        self.normalize_silkscreen_graphics()
        self.add_outline()
        self.add_planes_and_keepouts()
        self.add_manual_buck_routes()
        self.add_usb_routes()
        self.add_shifted_u1_bus_route()
        self.add_two_amp_power_routes()
        self.add_vbus_present_route()
        self.initialise_router_obstacles()
        self.add_ground_connections()
        self.route_slow_nets()
        self.add_silkscreen()
        self.fill_and_save()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    generator = Generator(args.output.resolve())
    generator.generate()
    print(f"wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
