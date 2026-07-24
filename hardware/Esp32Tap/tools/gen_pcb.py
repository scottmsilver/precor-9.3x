#!/usr/bin/python3
"""Generate the Esp32Tap Rev B four-layer PCB from ``design.py``.

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
from pcbnew import FromMM as MM
from pcbnew import VECTOR2I


design.validate()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "kicad" / "Esp32Tap.kicad_pcb"
FPLIB = Path(design.FPLIB)
OX, OY = 100.0, 100.0
BOARD_W, BOARD_H = 100.0, 55.0
F = pcbnew.F_Cu
IN1 = pcbnew.In1_Cu
IN2 = pcbnew.In2_Cu
B = pcbnew.B_Cu
ROUTING_LAYERS = (IN2, B)
GRID = 0.4
CLEARANCE = 0.20
ANTENNA_KEEPOUT = (68.0, 0.0, 88.5, 4.2)
FIXTURE_KEEPOUT = (52.0, 33.8, 82.0, 38.2)
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
MANUAL_NETS = USB_NETS | {
    "GND",
    "+8V_F",
    "SW_NODE",
    "BST",
    "FB",
    "UV_SENSE",
    "OV_SENSE",
    "CC1",
    "CC2",
    "VBUS",
}
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
    "5": ((52.0, 23.2), "TX_ENABLE"),
    "6": ((51.2, 22.4), "TREAD_OK"),
    "7": ((52.0, 21.6), "RELAY_GATE"),
    "8": ((51.2, 20.8), "+3V3"),
}
# JLC DFM reports same-net pad/via proximity and mask-opening artefacts even
# when KiCad's electrical DRC accepts them.  Lock only the reported endpoint
# escapes so the manufacturing geometry stays deterministic without moving
# the intentional plated-pad/via geometry at J1.
LOCKED_DFM_ESCAPES = {
    ("R13", "2"): (61.2, 11.2),
    ("C9", "2"): (65.2, 1.2),
    ("R12", "2"): (75.2, 48.8),
    ("R22", "1"): (43.6, 40.8),
    ("R8", "2"): (64.4, 9.2),
    ("L1", "2"): (67.2, 51.2),
    ("R30", "2"): (81.6, 44.0),
    ("R26", "2"): (32.4, 31.2),
}


# Coordinates are board-local millimetres.  The groups deliberately preserve
# the RJ45/enclosure geometry, keep RF/USB clear of power, and make the
# supervisor, relay, and converter loops probeable.
PLACE = {
    "J1": (12.5, 8.0, 270),
    "J2": (12.5, 37.0, 270),
    "J3": (96.2, 36.5, 90),
    "U1": (78.0, 6.45, 0),
    "K1": (27.0, 26.0, 0),
    "U2": (60.0, 48.5, 180),
    "U3": (87.0, 35.0, 180),
    "U4": (35.0, 42.0, 0),
    "U5": (37.0, 25.0, 0),
    "U6": (48.0, 22.0, 0),
    "U7": (58.0, 24.0, 0),
    "Q1": (35.0, 19.0, 0),
    "Q2": (84.0, 46.5, 0),
    "D1": (27.5, 52.5, 180),
    "D3": (35.0, 52.0, 0),
    "D4": (27.0, 17.5, 0),
    "D5": (20.0, 15.0, 270),
    "D6": (20.0, 43.0, 270),
    "D7": (19.0, 38.0, 270),
    "LED1": (93.0, 10.0, 180),
    "LED2": (77.0, 50.0, 0),
    "SW1": (55.0, 4.0, 0),
    "SW2": (94.0, 17.0, 0),
    "F1": (20.5, 52.5, 0),
    "L1": (65.0, 48.5, 0),
    "R1": (57.0, 45.0, 0),
    "R2": (55.0, 47.5, 180),
    "R3": (50.0, 45.0, 0),
    "R14": (50.0, 48.0, 0),
    "R4": (94.0, 43.5, 270),
    "R5": (94.0, 29.0, 90),
    "R6": (39.0, 14.0, 0),
    "R7": (64.0, 13.0, 0),
    "R8": (64.0, 10.5, 0),
    "R9": (38.5, 19.0, 90),
    "R10": (42.0, 18.5, 0),
    "R11": (90.0, 10.0, 0),
    "R12": (74.0, 50.0, 0),
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
    "R25": (33.5, 29.0, 90),
    "R26": (33.5, 33.0, 90),
    "R27": (52.0, 27.0, 0),
    "R28": (52.0, 30.0, 0),
    "R29": (80.0, 48.0, 0),
    "R30": (80.0, 45.0, 0),
    "R31": (90.0, 15.0, 90),
    "C1": (43.5, 49.5, 0),
    "C2": (50.0, 52.0, 0),
    "C3": (63.5, 43.5, 0),
    "C4": (62.0, 45.5, 0),
    "C5": (61.1, 51.1, 0),
    "C6": (70.0, 47.0, 0),
    "C7": (70.0, 52.0, 0),
    "C8": (66.5, 6.5, 90),
    "C9": (66.5, 2.0, 90),
    "C10": (60.0, 8.0, 90),
    "C11": (87.0, 43.0, 0),
    "C12": (57.0, 43.0, 0),
    "C13": (67.3, 12.8, 90),
    "C14": (67.3, 20.0, 270),
    "C15": (37.0, 21.5, 0),
    "C16": (41.0, 25.0, 90),
    "C17": (35.0, 38.5, 0),
    "C18": (32.0, 45.0, 0),
    "C19": (38.0, 45.0, 0),
    "C20": (48.0, 19.5, 0),
    "C21": (58.0, 21.5, 0),
    "TP1": (92.0, 5.0, 0),
    "TP2": (92.0, 8.0, 0),
    "TP3": (74.0, 43.0, 0),
    "TP4": (77.0, 43.0, 0),
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
            (
                (point[0] - start[0]) * dx
                + (point[1] - start[1]) * dy
            )
            / length_squared,
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
        return (q[0] - p[0]) * (r[1] - p[1]) - (
            q[1] - p[1]
        ) * (r[0] - p[0])

    def on_segment(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> bool:
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9
            <= q[1]
            <= max(p[1], r[1]) + 1e-9
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
        title.SetRevision("B")
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
        self.pad_net = {
            endpoint: name
            for name, endpoints in design.NETS.items()
            for endpoint in endpoints
        }
        self.pad_net.update(
            {
                (ref, pad): (
                    f"unconnected-({ref}-"
                    f"{design.COMPONENTS[ref][7][pad]}-Pad{pad})"
                )
                for ref, pad in design.NC
            }
        )
        self.footprints: dict[str, Any] = {}
        self.track_records: list[
            tuple[tuple[float, float], tuple[float, float], str, float, int]
        ] = []
        self.via_records: list[tuple[tuple[float, float], str]] = []
        self.pad_objects: dict[tuple[str, str], list[Any]] = defaultdict(list)
        self.occupied: dict[int, dict[tuple[int, int], set[str]]] = {
            IN2: defaultdict(set),
            B: defaultdict(set),
        }
        self.blocked: dict[int, set[tuple[int, int]]] = {
            IN2: set(),
            B: set(),
        }
        self.front_pad_obstacles: list[
            tuple[float, float, float, float, str]
        ] = []

    def add_footprints(self) -> None:
        missing = set(design.COMPONENTS) - set(PLACE)
        extra = set(PLACE) - set(design.COMPONENTS)
        if missing or extra:
            raise ValueError(
                f"placement mismatch missing={sorted(missing)} "
                f"extra={sorted(extra)}"
            )
        for ref, component in design.COMPONENTS.items():
            value, library, name, lcsc, jlc_class, cost, description, _ = (
                component
            )
            footprint = pcbnew.FootprintLoad(
                str(FPLIB / f"{library}.pretty"),
                name,
            )
            if footprint is None:
                raise ValueError(f"cannot load {library}:{name}")
            footprint.SetFPIDAsString(f"{library}:{name}")
            footprint.SetReference(ref)
            footprint.SetValue(value)
            footprint.SetDNP(ref in design.DNP)
            footprint.SetExcludedFromBOM(
                ref in design.DNP or jlc_class == "none"
            )
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
            ("MH1", (2.9, 26.5)),
            ("MH2", (97.0, 3.0)),
            ("MH3", (97.0, 52.0)),
        ):
            footprint = pcbnew.FootprintLoad(
                str(FPLIB / "MountingHole.pretty"),
                "MountingHole_2.7mm_M2.5",
            )
            if footprint is None:
                raise ValueError("cannot load M2.5 mounting-hole footprint")
            footprint.SetFPIDAsString(
                "MountingHole:MountingHole_2.7mm_M2.5"
            )
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

    def add_outline(self) -> None:
        corners = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
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
                raise ValueError(
                    f"conflicting vias at {point}: {existing_net} and {net}"
                )
        via = pcbnew.PCB_VIA(self.board)
        via.SetPosition(absolute(point))
        via.SetWidth(MM(size))
        via.SetDrill(MM(drill))
        via.SetLayerPair(F, B)
        via.SetNet(self.nets[net])
        self.board.Add(via)
        self.via_records.append((point, net))

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
        outline = [(0.5, 0.5), (99.5, 0.5), (99.5, 54.5), (0.5, 54.5)]
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
            [(69.5, 23.3), (83.5, 23.3), (83.5, 25.2), (69.5, 25.2)],
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
                half = (
                    max(pcbnew.ToMM(drill.x), pcbnew.ToMM(drill.y)) / 2
                    + 0.30
                )
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
        # Connector fan-out keeps the interleaved reversible contacts on F.Cu.
        # D+ joins on the inboard side and leaves below the contact field; D-
        # joins on the outboard side and leaves above it.  The two paths never
        # cross despite the reversible A/B contact ordering.
        a7, b7 = self.pad("J3", "A7"), self.pad("J3", "B7")
        a6, b6 = self.pad("J3", "A6"), self.pad("J3", "B6")
        u31, u33 = self.pad("U3", "1"), self.pad("U3", "3")
        dn_x = 93.4
        self.add_track([a7, (dn_x, a7[1])], "USB_DN", 0.20)
        self.add_track([b7, (dn_x, b7[1])], "USB_DN", 0.20)
        self.add_track(
            [
                (dn_x, a7[1]),
                (dn_x, b7[1]),
                (89.8, b7[1]),
                (89.8, u31[1]),
                u31,
            ],
            "USB_DN",
            USB_CONTROLLED_WIDTH,
        )
        dp_x = 90.9
        self.add_track([a6, (dp_x, a6[1])], "USB_DP", 0.20)
        self.add_track([b6, (dp_x, b6[1])], "USB_DP", 0.20)
        self.add_track(
            [
                (dp_x, a6[1]),
                (dp_x, b6[1]),
                (90.4, b6[1]),
                (90.4, 34.6),
                (89.6, 34.6),
                (89.6, u33[1]),
                u33,
            ],
            "USB_DP",
            USB_CONTROLLED_WIDTH,
        )

        # Long post-ESD pair.  The central parallel run uses the official
        # JLC04161H-7628 90-ohm result: 0.2906 mm copper plus a 0.2000 mm
        # edge gap gives 0.4906 mm centre-to-centre separation.
        u36, u34 = self.pad("U3", "6"), self.pad("U3", "4")
        r151, r161 = self.pad("R15", "1"), self.pad("R16", "1")
        dp_y, dn_y = 24.000, 24.000 + USB_CENTER_SPACING
        dp_outer_x = 83.2 + USB_CENTER_SPACING
        dp_inner_x = 69.5 + USB_CENTER_SPACING
        self.add_track(
            [
                u36,
                (83.2, u36[1]),
                (83.2, dn_y),
                (69.5, dn_y),
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
                (dp_outer_x, u34[1]),
                (dp_outer_x, dp_y),
                (dp_inner_x, dp_y),
                (dp_inner_x, 22.0),
                (71.67, 22.0),
                (71.67, dp_y),
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

        # VBUS is only sensed by U3.  Bring both reversible connector contact
        # groups down to In2 immediately so they do not cut across the USB
        # pair fan-out on the front layer.
        vbus_lower = (90.4, 33.2)
        vbus_upper = (90.4, 39.6)
        vbus_esd = (85.2, self.pad("U3", "5")[1])
        self.add_track(
            [
                self.pad("J3", "A9"),
                (90.4, self.pad("J3", "A9")[1]),
                (90.4, vbus_lower[1]),
                vbus_lower,
            ],
            "VBUS",
            0.30,
        )
        self.add_track(
            [
                self.pad("J3", "A4"),
                (90.4, self.pad("J3", "A4")[1]),
                (90.4, vbus_upper[1]),
                vbus_upper,
            ],
            "VBUS",
            0.30,
        )
        self.add_track([self.pad("U3", "5"), vbus_esd], "VBUS", 0.30)
        for point in (vbus_lower, vbus_upper, vbus_esd):
            self.add_via(point, "VBUS")
        self.add_track(
            [vbus_lower, vbus_esd, vbus_upper],
            "VBUS",
            0.30,
            IN2,
        )

        cc_routes = (
            (
                "CC1",
                self.pad("J3", "A5"),
                (90.5, 38.2),
                self.pad("R4", "1"),
                (92.4, self.pad("R4", "1")[1]),
            ),
            (
                "CC2",
                self.pad("J3", "B5"),
                (93.4, self.pad("J3", "B5")[1]),
                self.pad("R5", "1"),
                (92.4, self.pad("R5", "1")[1]),
            ),
        )
        for net, contact, contact_via, resistor, resistor_via in cc_routes:
            self.add_track(
                [
                    contact,
                    (contact_via[0], contact[1]),
                    contact_via,
                ],
                net,
                0.20,
            )
            self.add_track([resistor, resistor_via], net, 0.20)
            self.add_via(contact_via, net)
            self.add_via(resistor_via, net)
            inner_points = [contact_via, resistor_via]
            if net == "CC2":
                inner_points = [
                    contact_via,
                    (89.0, contact_via[1]),
                    (89.0, resistor_via[1]),
                    resistor_via,
                ]
            self.add_track(inner_points, net, 0.20, B)

        vbus_loads = (
            (self.pad("C11", "1"), (84.8, self.pad("C11", "1")[1])),
            (self.pad("Q2", "1"), (81.8, self.pad("Q2", "1")[1])),
            (self.pad("R29", "1"), (78.0, self.pad("R29", "1")[1])),
        )
        load_vias: list[tuple[float, float]] = []
        for pad_point, via_point in vbus_loads:
            self.add_track([pad_point, via_point], "VBUS", 0.25)
            self.add_via(via_point, "VBUS")
            load_vias.append(via_point)
        self.add_track(
            [vbus_esd, *load_vias],
            "VBUS",
            0.30,
            B,
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
                raise RuntimeError(
                    f"locked escape for {ref}.{pad.GetNumber()} is off-grid"
                )
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
                    f"locked escape unavailable for "
                    f"{ref}.{pad.GetNumber()} {net} at {target}: {checks}"
                )
            return target, [point, target]
        if ref == "U6":
            desired = PLANNED_U6_ESCAPES[str(pad.GetNumber())][0]
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
                    candidates.append(
                        (abs(dx) + abs(dy), preferred[0] + dx, preferred[1] + dy)
                    )
            for dy in range(-radius + 1, radius):
                for dx in (-radius, radius):
                    candidates.append(
                        (abs(dx) + abs(dy), preferred[0] + dx, preferred[1] + dy)
                    )
            for _, ix, iy in sorted(set(candidates)):
                candidate = (ix, iy)
                if not self.node_inside(candidate):
                    continue
                if self.cell_allowed(candidate, IN2, net) and self.cell_allowed(
                    candidate, B, net
                ):
                    target = grid_point(candidate)
                    if not self.via_allowed(target, net):
                        continue
                    doglegs = (
                        [point, (target[0], point[1]), target],
                        [point, (point[0], target[1]), target],
                    )
                    if abs(point[0] - target[0]) < abs(
                        point[1] - target[1]
                    ):
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
        for x, y, half_width, half_height, pad_net in (
            self.front_pad_obstacles
        ):
            if pad_net == net:
                continue
            if (
                abs(point[0] - x) < half_width + 0.50
                and abs(point[1] - y) < half_height + 0.50
            ):
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
            if distance < 0.80:
                return False

        for start, end, track_net, width, layer in self.track_records:
            if layer != F or track_net == net:
                continue
            if point_segment_distance(point, start, end) < (
                0.30 + width / 2 + CLEARANCE
            ):
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
            for x, y, half_width, half_height, pad_net in (
                self.front_pad_obstacles
            ):
                if pad_net == net:
                    continue
                if (
                    abs(point[0] - x) < half_width + expansion
                    and abs(point[1] - y) < half_height + expansion
                ):
                    return False

        for other_start, other_end, track_net, other_width, layer in (
            self.track_records
        ):
            if layer != F or track_net == net:
                continue
            if segment_distance(start, end, other_start, other_end) < (
                width / 2 + other_width / 2 + CLEARANCE
            ):
                return False

        for via, via_net in self.via_records:
            if via_net == net:
                continue
            if point_segment_distance(via, start, end) < (
                0.30 + width / 2 + CLEARANCE
            ):
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
        for ix in range(grid_index(0.8), grid_index(BOARD_W - 0.8) + 1):
            for iy in range(grid_index(0.8), grid_index(BOARD_H - 0.8) + 1):
                x, y = grid_point((ix, iy))
                if ax0 <= x <= ax1 and ay0 <= y <= ay1:
                    self.blocked[IN2].add((ix, iy))
                    self.blocked[B].add((ix, iy))
                if fx0 <= x <= fx1 and fy0 <= y <= fy1:
                    self.blocked[B].add((ix, iy))

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
            elif (
                layer == F
                and net in USB_NETS
                and min(start[0], end[0]) >= 69.5
            ):
                self.mark_segment(
                    start,
                    end,
                    width,
                    ROUTING_LAYERS,
                    net,
                    extra=0.0,
                )
        for point, net in self.via_records:
            self.mark_circle(point, 0.50, ROUTING_LAYERS, net)

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
                if (
                    next_layer_index != layer_index
                    and not self.via_allowed(grid_point(node), net)
                ):
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
        raise RuntimeError(
            f"autorouter cannot connect {net}; starts={len(starts)} "
            f"goals={len(goals)} visited={visited}"
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
        tree: set[tuple[int, int, int]] = {
            (unique[0][0], unique[0][1], index)
            for index in range(len(allowed_layers))
        }
        remaining = set(unique[1:])
        while remaining:
            endpoint = min(
                remaining,
                key=lambda node: min(
                    abs(node[0] - tree_node[0])
                    + abs(node[1] - tree_node[1])
                    for tree_node in tree
                ),
            )
            starts = {
                (endpoint[0], endpoint[1], index)
                for index in range(len(allowed_layers))
            }
            path = self.astar(starts, tree, net, allowed_layers)
            additions = self.emit_grid_path(path, net, allowed_layers)
            tree.update(additions)
            tree.update(
                (endpoint[0], endpoint[1], index)
                for index in range(len(allowed_layers))
            )
            remaining.remove(endpoint)

    def route_slow_nets(self) -> None:
        fanout_order = {
            net: index
            for index, net in enumerate(
                (
                    "TX_ENABLE",
                    "RELAY_GATE",
                    "TX_GATE",
                    "RELAY_CMD",
                    "ESP_TX",
                    "TX_BUF",
                    "PIN3_RX",
                    "CONS_RX",
                    "TREAD_OK",
                    "K1_NC_FB",
                    "K1_NO_FB",
                    "VBUS_PRESENT_N",
                    "STATUS_LED",
                    "IO0",
                    "U0TXD",
                    "U0RXD",
                )
            )
        }
        nets = [
            net
            for net in design.NETS
            if net not in MANUAL_NETS
        ]
        nets.sort(
            key=lambda net: (
                0 if net in {"+8V_RAW", "+8V_F", "VIN", "+3V3"} else 1,
                fanout_order.get(net, len(fanout_order)),
                -len(design.NETS[net]),
                net,
            )
        )
        for net in nets:
            self.route_net(net)

    def add_ground_connections(self) -> None:
        for ref, number in design.NETS["GND"]:
            if ref == "J3":
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
        def text(x: float, y: float, value: str, size: float = 1.0) -> None:
            item = pcbnew.PCB_TEXT(self.board)
            item.SetText(value)
            item.SetPosition(absolute((x, y)))
            item.SetLayer(pcbnew.F_SilkS)
            item.SetTextSize(VECTOR2I(MM(size), MM(size)))
            item.SetTextThickness(MM(0.20))
            self.board.Add(item)

        text(6.5, 22.5, "CONSOLE", 1.0)
        text(6.5, 31.0, "MOTOR", 1.0)
        text(45.0, 2.0, "Esp32Tap rev B", 1.2)
        text(24.0, 13.0, "BYPASS", 1.0)
        text(30.0, 13.0, "NC", 1.0)
        text(28.0, 35.0, "EMULATE", 1.0)
        text(34.0, 35.0, "NO", 1.0)
        text(91.5, 47.5, "USB DATA ONLY", 1.0)
        text(7.5, 3.0, "PIN 1", 1.0)
        text(26.0, 48.0, "D1 K", 1.0)
        text(35.0, 48.0, "D3 K", 1.0)

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
        self.add_outline()
        self.add_planes_and_keepouts()
        self.add_manual_buck_routes()
        self.add_usb_routes()
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
