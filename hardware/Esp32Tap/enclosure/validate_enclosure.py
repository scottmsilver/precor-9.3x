#!/usr/bin/env python3
"""Validate the Rev E case against versioned PCB geometry and welded STLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import trimesh

OPENSCAD_IMAGE = "openscad/openscad@sha256:" "147e48525bec392bcf628d7a6d5ea4ccac71b16251952328f86e1061cbf47c37"
# Rev E flush-jack: both RJ45s share the Y=40 axis (straight passthrough)
# with mating faces flush with the short board edges; the footprint
# anchors sit ~11.9 mm inboard (the body extends from the edge inward).
EXPECTED_CONNECTOR_CENTERS = ((11.9, 40.0), (83.1, 40.0))
# J1 and J2 are the identical Molex 441440003 RJ45 jack, so both
# fabrication-body widths (from inspect_kicad) are the same value.
EXPECTED_CONNECTOR_BODY_WIDTHS = (15.48, 15.48)
EXPECTED_USB_CENTER = (83.6, 54.2)
EXPECTED_SWITCH_CENTERS = ((42.0, 7.0), (91.0, 20.0))
EXPECTED_MOUNTING_HOLES = ((20.0, 6.0), (48.0, 6.0), (92.0, 55.0))
REQUIRED_SCALARS = (
    "board_l",
    "board_w",
    "board_t",
    "ant_overhang",
    "ant_x0",
    "ant_x1",
    "ant_air_gap",
    "j1_yc",
    "j2_yc",
    "rj45_body_w",
    "rj45_body_depth",
    "rj45_body_h",
    "aperture_w",
    "aperture_h",
    "latch_clearance",
    "cable_bend_radius",
    "cable_exit_direction",
    "j2_cable_exit_direction",
    "usb_xc",
    "usb_h",
    "usb_om_w",
    "usb_om_h",
    "wall",
    "clr",
    "bot_clr",
    "standoff",
    "headroom",
    "lid_t",
    "lip",
    "lip_w",
    "post_d",
    "post_wall_overlap",
    "snap_clearance",
)


class ValidationError(ValueError):
    """The checked enclosure evidence is incomplete or inconsistent."""


def _number(source: str, name: str) -> float:
    match = re.search(
        rf"(?m)^\s*{re.escape(name)}\s*=\s*" r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*;",
        source,
    )
    if not match:
        raise ValidationError(f"SCAD is missing numeric assignment {name}")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise ValidationError(f"SCAD assignment {name} is not finite")
    return value


def parse_scad_parameters(source: str) -> dict[str, float]:
    """Read the small, auditable scalar interface at the top of the SCAD."""

    parameters = {name: _number(source, name) for name in REQUIRED_SCALARS}
    expression = re.search(
        r"(?m)^\s*post_inset\s*=\s*" r"post_d\s*/\s*2\s*-\s*post_wall_overlap\s*;",
        source,
    )
    if not expression:
        raise ValidationError("post_inset must be derived from post_d / 2 - " "post_wall_overlap")
    parameters["post_inset"] = parameters["post_d"] / 2 - parameters["post_wall_overlap"]
    return parameters


def _numeric_pairs(source: str, name: str) -> tuple[tuple[float, float], ...]:
    assignment = re.search(
        rf"(?ms)^\s*{re.escape(name)}\s*=\s*\[(.*?)\]\s*;",
        source,
    )
    if not assignment:
        raise ValidationError(f"SCAD is missing vector-array assignment {name}")
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    pair_pattern = re.compile(rf"\[\s*({number})\s*,\s*({number})\s*\]")
    pairs = tuple(
        (float(match.group(1)), float(match.group(2))) for match in pair_pattern.finditer(assignment.group(1))
    )
    residual = pair_pattern.sub("", assignment.group(1))
    if not pairs or residual.strip(" \t\r\n,"):
        raise ValidationError(f"SCAD {name} must contain only numeric [x, y] pairs")
    return pairs


def _numeric_pair(source: str, name: str) -> tuple[float, float]:
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    assignment = re.search(
        rf"(?m)^\s*{re.escape(name)}\s*=\s*" rf"\[\s*({number})\s*,\s*({number})\s*\]\s*;",
        source,
    )
    if not assignment:
        raise ValidationError(f"SCAD {name} must contain one numeric [x, y] pair")
    return float(assignment.group(1)), float(assignment.group(2))


def validate_rj45_aperture(
    *,
    body_width: float,
    aperture_width: float,
    aperture_height: float,
    body_height: float,
) -> dict[str, float]:
    """Rev D: both jacks are the identical, unshielded 8P8C part — there is
    no mechanical keying between console and motor any more (CONSOLE/MOTOR
    silkscreen is the only differentiator).  This only checks that the
    panel aperture actually clears the mating plug/jack body."""
    values = (body_width, aperture_width, aperture_height, body_height)
    if any(not math.isfinite(value) for value in values):
        raise ValidationError("RJ45 aperture dimensions must be finite")
    if min(values) <= 0:
        raise ValidationError("RJ45 aperture dimensions must be positive")
    width_clearance = (aperture_width - body_width) / 2
    if width_clearance < 0.2 - 1e-9:
        raise ValidationError("RJ45 aperture lacks 0.2 mm clearance around the jack body")
    return {"width_clearance_mm": width_clearance}


def expected_dimensions(parameters: dict[str, float]) -> dict[str, float]:
    interior_length = parameters["board_l"] + 2 * parameters["clr"]
    interior_width = (
        parameters["board_w"] + parameters["bot_clr"] + parameters["ant_overhang"] + parameters["ant_air_gap"]
    )
    interior_height = parameters["standoff"] + parameters["board_t"] + parameters["headroom"]
    return {
        "interior_length_mm": interior_length,
        "interior_width_mm": interior_width,
        "outer_length_mm": interior_length + 2 * parameters["wall"],
        "outer_width_mm": interior_width + 2 * parameters["wall"],
        "base_height_mm": parameters["wall"] + interior_height,
        "antenna_void_mm": parameters["ant_air_gap"],
        "post_wall_overlap_mm": (parameters["post_d"] / 2 - parameters["post_inset"]),
        "lid_post_relief_mm": parameters["post_d"] + 0.6,
    }


def _xy(value: Any, label: str) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)) or not math.isfinite(coordinate)
            for coordinate in value
        )
    ):
        raise ValidationError(f"{label} must be a finite [x, y] pair")
    return float(value[0]), float(value[1])


def _footprint_at(
    footprints: dict[str, Any],
    reference: str,
) -> tuple[float, float]:
    try:
        footprint = footprints[reference]
    except KeyError as error:
        raise ValidationError(f"inspector report is missing {reference}") from error
    if not isinstance(footprint, dict):
        raise ValidationError(f"{reference} footprint must be an object")
    return _xy(footprint.get("at"), f"{reference}.at")


def _rj45_center_y(
    footprints: dict[str, Any],
    reference: str,
    origin_y: float,
) -> float:
    footprint = footprints.get(reference)
    if not isinstance(footprint, dict):
        raise ValidationError(f"inspector report is missing {reference}")
    pads = footprint.get("pads")
    if not isinstance(pads, dict):
        raise ValidationError(f"{reference}.pads must be an object")
    positions: list[tuple[float, float]] = []
    for number in map(str, range(1, 9)):
        pad = pads.get(number)
        if not isinstance(pad, dict):
            raise ValidationError(f"{reference} is missing pad {number}")
        positions.append(_xy(pad.get("at"), f"{reference}.{number}.at"))
    return sum(position[1] for position in positions) / 8 - origin_y


def _fabrication_body_size(
    footprints: dict[str, Any],
    reference: str,
) -> tuple[float, float]:
    footprint = footprints.get(reference)
    if not isinstance(footprint, dict):
        raise ValidationError(f"inspector report is missing {reference}")
    body = footprint.get("fabrication_body_bbox")
    if not isinstance(body, dict) or set(body) != {"min", "max"}:
        raise ValidationError(f"{reference}.fabrication_body_bbox is incomplete")
    minimum = _xy(body["min"], f"{reference}.fabrication_body_bbox.min")
    maximum = _xy(body["max"], f"{reference}.fabrication_body_bbox.max")
    size = maximum[0] - minimum[0], maximum[1] - minimum[1]
    if min(size) <= 0:
        raise ValidationError(f"{reference} fabrication body is non-positive")
    return size


def _antenna_geometry(
    board: dict[str, Any],
) -> tuple[float, tuple[float, float]]:
    antenna = board.get("antenna")
    if not isinstance(antenna, dict):
        raise ValidationError("inspector schema must include board.antenna physical geometry")
    expected_keys = {"reference", "physical_edge_y_mm", "span_x_mm"}
    if set(antenna) != expected_keys:
        raise ValidationError("board.antenna must contain exactly reference, " "physical_edge_y_mm, and span_x_mm")
    if antenna.get("reference") != "U1":
        raise ValidationError("board.antenna.reference must be U1")
    edge = antenna.get("physical_edge_y_mm")
    if isinstance(edge, bool) or not isinstance(edge, (int, float)) or not math.isfinite(edge):
        raise ValidationError("board.antenna must include finite physical_edge_y_mm")
    span = antenna.get("span_x_mm")
    span_x = _xy(span, "board.antenna.span_x_mm")
    if span_x[1] <= span_x[0]:
        raise ValidationError("board.antenna span must have positive width")
    return float(edge), span_x


def derive_board_geometry(report: dict[str, Any]) -> dict[str, Any]:
    """Derive enclosure inputs exclusively from inspector schema version 1."""

    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValidationError("unsupported or missing inspector schema_version")
    board = report.get("board")
    if not isinstance(board, dict):
        raise ValidationError("inspector report is missing board")
    outline = board.get("outline")
    if not isinstance(outline, dict):
        raise ValidationError("inspector report is missing board.outline")
    origin = _xy(outline.get("min"), "board.outline.min")
    maximum = _xy(outline.get("max"), "board.outline.max")
    size = (maximum[0] - origin[0], maximum[1] - origin[1])
    if size[0] <= 0 or size[1] <= 0:
        raise ValidationError("board outline has non-positive size")
    footprints = board.get("footprints")
    if not isinstance(footprints, dict):
        raise ValidationError("inspector report is missing board.footprints")

    connector_centers = tuple(
        tuple(coordinate - origin[index] for index, coordinate in enumerate(_footprint_at(footprints, reference)))
        for reference in ("J1", "J2")
    )
    usb = _footprint_at(footprints, "J3")
    switches = tuple(
        tuple(coordinate - origin[index] for index, coordinate in enumerate(_footprint_at(footprints, reference)))
        for reference in ("SW1", "SW2")
    )
    connector_body_widths = tuple(_fabrication_body_size(footprints, reference)[1] for reference in ("J1", "J2"))
    mounting = tuple(
        tuple(coordinate - origin[index] for index, coordinate in enumerate(_footprint_at(footprints, reference)))
        for reference in ("MH1", "MH2", "MH3")
    )
    antenna_edge, antenna_span = _antenna_geometry(board)

    return {
        "board_size_mm": size,
        "connector_centers_mm": connector_centers,
        "connector_body_widths_mm": connector_body_widths,
        "rj45_centers_y_mm": tuple(center[1] for center in connector_centers),
        "usb_center_mm": (usb[0] - origin[0], usb[1] - origin[1]),
        "usb_center_x_mm": usb[0] - origin[0],
        "switch_centers_mm": switches,
        "mounting_holes_mm": mounting,
        "antenna_overhang_mm": origin[1] - antenna_edge,
        "antenna_span_x_mm": (
            antenna_span[0] - origin[0],
            antenna_span[1] - origin[0],
        ),
    }


def _close(actual: float, expected: float, label: str, tolerance: float) -> None:
    if not math.isclose(actual, expected, abs_tol=tolerance):
        raise ValidationError(f"{label}: expected {expected:.6f} mm, got {actual:.6f} mm")


def validate_fit(
    source: str,
    parameters: dict[str, float],
    geometry: dict[str, Any],
) -> None:
    _close(parameters["board_l"], geometry["board_size_mm"][0], "board X", 0.01)
    _close(parameters["board_w"], geometry["board_size_mm"][1], "board Y", 0.01)
    for index, expected in enumerate(geometry["connector_centers_mm"], start=1):
        _close(parameters[f"j{index}_yc"], expected[1], f"J{index} center", 0.01)
        for axis, actual in enumerate(expected):
            _close(
                actual,
                EXPECTED_CONNECTOR_CENTERS[index - 1][axis],
                f"J{index} PCB axis {axis}",
                0.01,
            )
        # Rev D: J1 and J2 are the identical RJ45 part, so both compare
        # against the single shared rj45_body_w SCAD parameter.
        _close(
            parameters["rj45_body_w"],
            geometry["connector_body_widths_mm"][index - 1],
            f"J{index} body width",
            0.01,
        )
        _close(
            parameters["rj45_body_w"],
            EXPECTED_CONNECTOR_BODY_WIDTHS[index - 1],
            f"J{index} expected body width",
            0.01,
        )
    for axis, actual in enumerate(geometry["usb_center_mm"]):
        _close(actual, EXPECTED_USB_CENTER[axis], f"J3 PCB axis {axis}", 0.01)
    _close(parameters["usb_xc"], geometry["usb_center_x_mm"], "J3 center", 0.01)
    for index, (actual, expected) in enumerate(
        zip(
            geometry["switch_centers_mm"],
            EXPECTED_SWITCH_CENTERS,
            strict=True,
        ),
        start=1,
    ):
        scad = _numeric_pair(source, f"sw{index}")
        for axis in range(2):
            _close(scad[axis], actual[axis], f"SW{index} SCAD axis {axis}", 0.01)
            _close(actual[axis], expected[axis], f"SW{index} PCB axis {axis}", 0.01)
    _close(
        parameters["ant_overhang"],
        geometry["antenna_overhang_mm"],
        "U1 antenna overhang",
        0.01,
    )
    _close(
        parameters["ant_x0"],
        geometry["antenna_span_x_mm"][0],
        "U1 antenna span start",
        0.01,
    )
    _close(
        parameters["ant_x1"],
        geometry["antenna_span_x_mm"][1],
        "U1 antenna span end",
        0.01,
    )
    if parameters["ant_air_gap"] < 15.0:
        raise ValidationError("antenna wall void is less than 15 mm")
    validate_rj45_aperture(
        body_width=parameters["rj45_body_w"],
        aperture_width=parameters["aperture_w"],
        aperture_height=parameters["aperture_h"],
        body_height=parameters["rj45_body_h"],
    )
    _close(parameters["rj45_body_h"], 13.4, "RJ45 jack body height", 1e-6)
    _close(parameters["aperture_h"], 14.0, "RJ45 aperture height", 1e-6)
    _close(parameters["aperture_w"], 16.0, "RJ45 aperture width", 1e-6)
    _close(
        parameters["cable_exit_direction"],
        -1.0,
        "J1 outward cable exit direction",
        1e-6,
    )
    _close(
        parameters["j2_cable_exit_direction"],
        1.0,
        "J2 outward cable exit direction",
        1e-6,
    )
    if parameters["latch_clearance"] < 6.0:
        raise ValidationError("connector latch clearance is below 6 mm")
    if parameters["cable_bend_radius"] < 18.0:
        raise ValidationError("external cable bend service radius is below 18 mm")
    _close(parameters["snap_clearance"], 0.3, "snap-latch clearance", 1e-6)
    for module_name in (
        "rj45_wall_aperture",
        "rj45_wall_aperture_right",
        "rj45_plug_service_envelope",
        "rj45_plug_service_envelope_right",
        "snap_latch",
    ):
        if f"module {module_name}" not in source:
            raise ValidationError(f"SCAD is missing {module_name}")
    _close(parameters["post_d"], 7.0, "lid post diameter", 1e-6)
    _close(
        parameters["post_wall_overlap"],
        0.25,
        "lid post wall overlap",
        1e-6,
    )
    if parameters["post_wall_overlap"] <= 0:
        raise ValidationError("lid posts must overlap the wall")
    if source.count("wall+post_inset") != 4:
        raise ValidationError("all four lid-post coordinates must derive from post_inset")
    if not re.search(r"\bd\s*=\s*post_d\s*\+\s*0\.6\b", source):
        raise ValidationError("lid post relief must be post_d + 0.6")

    scad_mounting = _numeric_pairs(source, "mh")
    if len(scad_mounting) != 3:
        raise ValidationError("SCAD mh array must contain MH1, MH2, and MH3")
    for index, (scad, actual, expected) in enumerate(
        zip(
            scad_mounting,
            geometry["mounting_holes_mm"],
            EXPECTED_MOUNTING_HOLES,
            strict=True,
        ),
        start=1,
    ):
        _close(scad[0], actual[0], f"MH{index} SCAD X", 0.01)
        _close(scad[1], actual[1], f"MH{index} SCAD Y", 0.01)
    for actual, expected in zip(
        geometry["mounting_holes_mm"],
        EXPECTED_MOUNTING_HOLES,
        strict=True,
    ):
        _close(actual[0], expected[0], "mounting-hole X", 0.01)
        _close(actual[1], expected[1], "mounting-hole Y", 0.01)

    wall = parameters["wall"]
    board_min = (
        wall + parameters["clr"],
        wall + parameters["ant_overhang"] + parameters["ant_air_gap"],
    )
    board_max = (
        board_min[0] + parameters["board_l"],
        board_min[1] + parameters["board_w"],
    )
    dimensions = expected_dimensions(parameters)
    inset = parameters["post_inset"]
    post_centers = (
        (wall + inset, wall + inset),
        (dimensions["outer_length_mm"] - wall - inset, wall + inset),
        (wall + inset, dimensions["outer_width_mm"] - wall - inset),
        (
            dimensions["outer_length_mm"] - wall - inset,
            dimensions["outer_width_mm"] - wall - inset,
        ),
    )
    radius = parameters["post_d"] / 2
    for index, center in enumerate(post_centers, start=1):
        dx = max(board_min[0] - center[0], 0.0, center[0] - board_max[0])
        dy = max(board_min[1] - center[1], 0.0, center[1] - board_max[1])
        clearance = math.hypot(dx, dy) - radius
        if clearance < 0.2:
            raise ValidationError(f"lid post {index} has only {clearance:.3f} mm board clearance")


def _load_mesh(path: Path, *, process: bool) -> trimesh.Trimesh:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValidationError(f"mesh is missing or empty: {path}")
    loaded = trimesh.load_mesh(path, process=process)
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValidationError(f"{path.name} is not one triangle mesh")
    return loaded


def validate_mesh(path: Path) -> dict[str, Any]:
    loaded = _load_mesh(path, process=True)
    loaded.merge_vertices(digits_vertex=6)
    bodies = loaded.split(only_watertight=False)
    if len(bodies) != 1:
        raise ValidationError(f"{path.name} has {len(bodies)} disconnected bodies after welding")
    if loaded.volume <= 0:
        raise ValidationError(f"{path.name} does not have positive volume")
    if not loaded.is_winding_consistent:
        raise ValidationError(f"{path.name} has inconsistent winding")
    if not loaded.is_watertight:
        raise ValidationError(f"{path.name} is not watertight")
    incidence = np.bincount(
        loaded.edges_unique_inverse,
        minlength=len(loaded.edges_unique),
    )
    if not np.all(incidence == 2):
        values, counts = np.unique(incidence, return_counts=True)
        histogram = ", ".join(f"{int(value)}:{int(count)}" for value, count in zip(values, counts, strict=True))
        raise ValidationError(f"{path.name} welded-edge incidence is not exactly two " f"({histogram})")
    return {
        "path": path.name,
        "body_count": 1,
        "volume_mm3": round(float(loaded.volume), 6),
        "watertight": True,
        "winding_consistent": True,
        "bounds_mm": np.round(loaded.bounds, 6).tolist(),
        "extents_mm": np.round(loaded.extents, 6).tolist(),
    }


def _mesh_geometry_digest(path: Path) -> str:
    """Hash triangle geometry independent of STL facet ordering."""

    loaded = _load_mesh(path, process=False)
    triangles = np.round(loaded.triangles, decimals=6)
    canonical = [tuple(sorted(tuple(float(value) for value in vertex) for vertex in face)) for face in triangles]
    canonical.sort()
    payload = json.dumps(canonical, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _render_canonical_meshes(enclosure: Path) -> dict[str, str]:
    """Render the live SCAD with the immutable OpenSCAD image."""

    with tempfile.TemporaryDirectory(prefix="esp32tap-enclosure-canonical-") as temporary:
        output = Path(temporary)
        digests: dict[str, str] = {}
        for part in ("base", "lid"):
            rendered = output / f"esp32tap_{part}.stl"
            command = [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{enclosure.resolve()}:/source:ro",
                "-v",
                f"{output.resolve()}:/output",
                "-w",
                "/source",
                OPENSCAD_IMAGE,
                "openscad",
                "--hardwarnings",
                "-D",
                f'part="{part}"',
                "-o",
                f"/output/{rendered.name}",
                "esp32tap_case.scad",
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValidationError(f"canonical render could not execute for {part}: {error}") from error
            if completed.returncode:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise ValidationError(f"canonical render failed for {part}: {detail}")
            validate_mesh(rendered)
            digests[part] = _mesh_geometry_digest(rendered)
        return digests


def _expect_occupancy(
    mesh: trimesh.Trimesh,
    points: list[list[float]],
    expected: list[bool],
    label: str,
) -> None:
    try:
        actual = mesh.contains(np.asarray(points, dtype=float)).tolist()
    except (ValueError, RuntimeError) as error:
        raise ValidationError(f"{label} occupancy probes could not run: {error}") from error
    if actual != expected:
        raise ValidationError(f"{label} geometry probes expected {expected}, got {actual}")


def _expect_vertices(
    mesh: trimesh.Trimesh,
    points: list[list[float]],
    label: str,
    tolerance: float = 0.02,
) -> None:
    vertices = np.asarray(mesh.vertices)
    for point in points:
        if not np.any(
            np.all(
                np.isclose(vertices, point, atol=tolerance, rtol=0.0),
                axis=1,
            )
        ):
            raise ValidationError(f"{label} is missing boundary vertex " f"{np.round(point, 6).tolist()}")


def validate_functional_geometry(
    base_path: Path,
    lid_path: Path,
    parameters: dict[str, float],
    geometry: dict[str, Any],
) -> dict[str, Any]:
    """Probe the manufactured solids at every safety-relevant feature."""

    base = _load_mesh(base_path, process=True)
    lid = _load_mesh(lid_path, process=True)
    dimensions = expected_dimensions(parameters)
    wall = parameters["wall"]
    outer_length = dimensions["outer_length_mm"]
    outer_width = dimensions["outer_width_mm"]
    base_height = dimensions["base_height_mm"]
    board_x = wall + parameters["clr"]
    board_y = wall + parameters["ant_overhang"] + parameters["ant_air_gap"]
    board_z = wall + parameters["standoff"]
    probe_count = 0

    cavity_points = [
        [outer_length / 2, outer_width / 2, wall + 1.5],
        [outer_length / 2, outer_width / 2, wall / 2],
        [wall - 0.2, wall + 7.5, 10.0],
        [wall + 0.2, wall + 7.5, 10.0],
        [outer_length - wall - 0.2, wall + 7.5, 10.0],
        [outer_length - wall + 0.2, wall + 7.5, 10.0],
        [outer_length / 2, wall - 0.2, 10.0],
        [outer_length / 2, wall + 0.2, 10.0],
        [outer_length / 2, outer_width - wall - 0.2, 10.0],
        [outer_length / 2, outer_width - wall + 0.2, 10.0],
    ]
    _expect_occupancy(
        base,
        cavity_points,
        [False, True, True, False, False, True, True, False, False, True],
        "main cavity",
    )
    probe_count += len(cavity_points)

    rj45_centers: list[list[float]] = []
    aperture_width = parameters["aperture_w"]
    aperture_height = parameters["aperture_h"]
    aperture_z = board_z + parameters["board_t"] - 0.3
    # Rev E: one jack per short wall -- J1 opens through X=0, J2 through
    # X=outer_length.
    jack_walls = ((1, 0.0, wall / 2), (2, outer_length, outer_length - wall / 2))
    for (index, shell_x, probe_x), center_y in zip(
        jack_walls,
        geometry["rj45_centers_y_mm"],
        strict=True,
    ):
        shell_y = wall + board_y - wall + center_y
        rj45_centers.append([shell_x, shell_y, aperture_z + aperture_height / 2])
        half_width = aperture_width / 2
        points = [
            [probe_x, shell_y, aperture_z + aperture_height / 2],
            [probe_x, shell_y - half_width + 0.3, aperture_z + 0.3],
            [
                probe_x,
                shell_y + half_width - 0.3,
                aperture_z + aperture_height - 0.3,
            ],
            [probe_x, shell_y + half_width + 0.3, aperture_z + aperture_height / 2],
            [probe_x, shell_y, aperture_z - 0.3],
            [probe_x, shell_y, aperture_z + aperture_height + 0.3],
        ]
        _expect_occupancy(
            base,
            points,
            [False, False, False, True, True, True],
            f"J{index} RJ45 aperture",
        )
        _expect_vertices(
            base,
            [
                [x, y, z]
                for x in ((0.0, wall) if shell_x == 0.0 else (outer_length - wall, outer_length))
                for y in (
                    shell_y - half_width,
                    shell_y + half_width,
                )
                for z in (
                    aperture_z,
                    aperture_z + aperture_height,
                )
            ],
            f"J{index} RJ45 aperture",
        )
        probe_count += len(points)

    # Rev E: USB-C opens through the Y=outer_width (bottom) wall.
    usb_x = board_x + geometry["usb_center_x_mm"]
    usb_z = board_z + parameters["board_t"] + parameters["usb_h"] / 2 - parameters["usb_om_h"] / 2
    usb_half_width = parameters["usb_om_w"] / 2
    usb_points = [
        [usb_x, outer_width - wall / 2, usb_z + parameters["usb_om_h"] / 2],
        [usb_x - usb_half_width + 0.3, outer_width - wall / 2, usb_z + 0.3],
        [
            usb_x + usb_half_width - 0.3,
            outer_width - wall / 2,
            usb_z + parameters["usb_om_h"] - 0.3,
        ],
        [
            usb_x + usb_half_width + 0.3,
            outer_width - wall / 2,
            usb_z + parameters["usb_om_h"] / 2,
        ],
        [usb_x, outer_width - wall / 2, usb_z - 0.3],
        [
            usb_x,
            outer_width - wall / 2,
            usb_z + parameters["usb_om_h"] + 0.3,
        ],
    ]
    _expect_occupancy(
        base,
        usb_points,
        [False, False, False, True, True, True],
        "J3 USB aperture",
    )
    _expect_vertices(
        base,
        [
            [x, y, z]
            for y in (outer_width - wall, outer_width)
            for x in (
                usb_x - usb_half_width,
                usb_x + usb_half_width,
            )
            for z in (
                usb_z,
                usb_z + parameters["usb_om_h"],
            )
        ],
        "J3 USB aperture",
    )
    probe_count += len(usb_points)

    for index, (switch_x, switch_y) in enumerate(
        geometry["switch_centers_mm"],
        start=1,
    ):
        center_x = board_x + switch_x
        center_y = board_y + switch_y
        switch_points = [
            [center_x, center_y, parameters["lid_t"] / 2],
            [center_x + 2.0, center_y, parameters["lid_t"] / 2],
        ]
        _expect_occupancy(
            lid,
            switch_points,
            [False, True],
            f"SW{index} tool access",
        )
        probe_count += len(switch_points)

    antenna_x = board_x + sum(geometry["antenna_span_x_mm"]) / 2
    antenna_edge_y = wall + parameters["ant_air_gap"]
    antenna_points = [
        [antenna_x, wall + 0.2, 10.0],
        [antenna_x, (wall + antenna_edge_y) / 2, 10.0],
        [antenna_x, antenna_edge_y - 0.2, 10.0],
        [antenna_x, wall - 0.2, 10.0],
    ]
    _expect_occupancy(
        base,
        antenna_points,
        [False, False, False, True],
        "U1 antenna wall void",
    )
    probe_count += len(antenna_points)

    mounting_centers: list[list[float]] = []
    for index, (mount_x, mount_y) in enumerate(
        geometry["mounting_holes_mm"],
        start=1,
    ):
        center = [board_x + mount_x, board_y + mount_y]
        mounting_centers.append(center)
        points = [
            [center[0], center[1], wall + parameters["standoff"] / 2],
            [center[0] + 1.8, center[1], wall + parameters["standoff"] / 2],
            [center[0] + 3.3, center[1], wall + parameters["standoff"] / 2],
        ]
        _expect_occupancy(
            base,
            points,
            [False, True, False],
            f"MH{index} mounting post",
        )
        inward = 1.0 if mount_x < parameters["board_l"] / 2 else -1.0
        _expect_vertices(
            base,
            [
                [center[0] + inward * radius, center[1], z]
                for radius in (1.0, 3.0)
                for z in (wall, wall + parameters["standoff"])
            ],
            f"MH{index} mounting post",
        )
        probe_count += len(points)

    inset = parameters["post_inset"]
    post_centers = (
        (wall + inset, wall + inset, 1.0, 1.0),
        (outer_length - wall - inset, wall + inset, -1.0, 1.0),
        (wall + inset, outer_width - wall - inset, 1.0, -1.0),
        (
            outer_length - wall - inset,
            outer_width - wall - inset,
            -1.0,
            -1.0,
        ),
    )
    for index, (center_x, center_y, direction_x, direction_y) in enumerate(
        post_centers,
        start=1,
    ):
        diagonal = math.sqrt(2)
        base_points = [
            [center_x, center_y, 10.0],
            [center_x + direction_x * 2.0, center_y, base_height - 5.6],
            [center_x, center_y, base_height - 5.6],
            [
                center_x + direction_x * 3.8 / diagonal,
                center_y + direction_y * 3.8 / diagonal,
                10.0,
            ],
        ]
        _expect_occupancy(
            base,
            base_points,
            [True, True, False, False],
            f"lid post {index}",
        )
        _expect_vertices(
            base,
            [
                [
                    center_x + direction_x * parameters["post_d"] / 2,
                    center_y,
                    base_height,
                ],
                [
                    center_x + direction_x * 1.25,
                    center_y,
                    base_height - 10.0,
                ],
            ],
            f"lid post {index}",
        )
        lid_points = [
            [center_x, center_y, parameters["lid_t"] + parameters["lip"] / 2],
            [
                center_x + direction_x * 3.65,
                center_y,
                parameters["lid_t"] + parameters["lip"] / 2,
            ],
            [
                center_x + direction_x * 4.05,
                center_y,
                parameters["lid_t"] + parameters["lip"] / 2,
            ],
            [center_x, center_y, 1.0],
            [center_x + direction_x * 3.3, center_y, 1.0],
        ]
        _expect_occupancy(
            lid,
            lid_points,
            [False, False, True, False, True],
            f"lid post {index} relief",
        )
        _expect_vertices(
            lid,
            [
                [
                    center_x + direction_x * (parameters["post_d"] + 0.6) / 2,
                    center_y,
                    z,
                ]
                for z in (
                    parameters["lid_t"],
                    parameters["lid_t"] + parameters["lip"],
                )
            ],
            f"lid post {index} relief",
        )
        probe_count += len(base_points) + len(lid_points)

    snap_points_base = [
        [outer_length / 2, -0.6, base_height - 1.5],
        [outer_length / 2, outer_width - wall / 2, base_height - 1.5],
    ]
    _expect_occupancy(
        base,
        snap_points_base,
        [True, True],
        "tool-less base latches",
    )
    snap_points_lid = [
        [outer_length / 2, wall - parameters["snap_clearance"], 1.0],
        [
            outer_length / 2,
            outer_width - wall + parameters["snap_clearance"],
            1.0,
        ],
    ]
    _expect_occupancy(
        lid,
        snap_points_lid,
        [False, False],
        "tool-less lid receivers",
    )
    probe_count += len(snap_points_base) + len(snap_points_lid)

    return {
        "probe_count": probe_count,
        "rj45_aperture_centers_shell_mm": rj45_centers,
        "usb_aperture_center_shell_mm": [
            usb_x,
            outer_width,
            usb_z + parameters["usb_om_h"] / 2,
        ],
        "mounting_post_centers_shell_mm": mounting_centers,
        "antenna_inner_wall_to_edge_mm": antenna_edge_y - wall,
        "rj45_apertures": validate_rj45_aperture(
            body_width=parameters["rj45_body_w"],
            aperture_width=parameters["aperture_w"],
            aperture_height=parameters["aperture_h"],
            body_height=parameters["rj45_body_h"],
        ),
        "external_cable_bend_service_radius_mm": parameters["cable_bend_radius"],
        "connector_latch_clearance_mm": parameters["latch_clearance"],
        "rj45_jacks_mm": [
            {
                "mpn": "441440003",
                "lcsc": "C585890",
                "body_width": parameters["rj45_body_w"],
                "body_depth": parameters["rj45_body_depth"],
                "body_height": parameters["rj45_body_h"],
                "aperture_width": parameters["aperture_w"],
                "aperture_height": parameters["aperture_h"],
                "extraction_clearance": parameters["latch_clearance"],
                "cable_exit_direction_x": direction,
            }
            for direction in (
                parameters["cable_exit_direction"],
                parameters["j2_cable_exit_direction"],
            )
        ],
        "closure": "TOOL_LESS_SNAP_LATCH_WITH_OPTIONAL_SUPPLIED_M3",
    }


def _inspection(project_dir: Path, supplied: Path | None) -> dict[str, Any]:
    if supplied is not None:
        try:
            return json.loads(supplied.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"cannot read inspection JSON {supplied}: {error}") from error
    inspector = project_dir / "tools" / "inspect_kicad.py"
    command = ["/usr/bin/python3", str(inspector), "--json"]
    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValidationError(f"cannot execute PCB inspector: {error}") from error
    if completed.returncode:
        raise ValidationError("PCB inspector failed: " + (completed.stderr.strip() or completed.stdout.strip()))
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValidationError(f"PCB inspector returned invalid JSON: {error}") from error


def validate(
    project_dir: Path,
    inspection_json: Path | None = None,
) -> dict[str, Any]:
    enclosure = project_dir / "enclosure"
    source_path = enclosure / "esp32tap_case.scad"
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValidationError(f"cannot read {source_path}: {error}") from error
    parameters = parse_scad_parameters(source)
    dimensions = expected_dimensions(parameters)
    geometry = derive_board_geometry(_inspection(project_dir, inspection_json))
    validate_fit(source, parameters, geometry)
    base_path = enclosure / "esp32tap_base.stl"
    lid_path = enclosure / "esp32tap_lid.stl"
    base = validate_mesh(base_path)
    lid = validate_mesh(lid_path)
    canonical = _render_canonical_meshes(enclosure)
    checked_digests = {
        "base": _mesh_geometry_digest(base_path),
        "lid": _mesh_geometry_digest(lid_path),
    }
    for part in ("base", "lid"):
        if checked_digests[part] != canonical[part]:
            raise ValidationError(f"{part} mesh does not match the pinned canonical render")
    features = validate_functional_geometry(
        base_path,
        lid_path,
        parameters,
        geometry,
    )

    expected_outer = (
        dimensions["outer_length_mm"],
        dimensions["outer_width_mm"],
    )
    _close(base["bounds_mm"][1][1], expected_outer[1], "base outer Y", 0.02)
    _close(base["bounds_mm"][1][2], dimensions["base_height_mm"], "base Z", 0.02)
    _close(lid["extents_mm"][0], expected_outer[0], "lid outer X", 0.02)
    _close(lid["extents_mm"][1], expected_outer[1], "lid outer Y", 0.02)
    _close(
        lid["extents_mm"][2],
        parameters["lid_t"] + parameters["lip"],
        "lid Z",
        0.02,
    )

    return {
        "status": "PASS",
        "schema_version": 1,
        "openscad_image": OPENSCAD_IMAGE,
        "antenna_void_mm": dimensions["antenna_void_mm"],
        "post_wall_overlap_mm": dimensions["post_wall_overlap_mm"],
        "board_geometry": geometry,
        "canonical_geometry_sha256": canonical,
        "functional_geometry": features,
        "meshes": {"base": base, "lid": lid},
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--inspection-json", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = validate(
            args.project_dir.resolve(),
            None if args.inspection_json is None else args.inspection_json.resolve(),
        )
    except ValidationError as error:
        print(f"validate_enclosure: {error}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(
            "PASS "
            f"antenna_void={result['antenna_void_mm']:.3f}mm "
            f"base={result['meshes']['base']['volume_mm3']:.3f}mm3 "
            f"lid={result['meshes']['lid']['volume_mm3']:.3f}mm3"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
