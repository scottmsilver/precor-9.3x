#!/usr/bin/env python3
"""Validate the checked-in Rev B manufacturing artifacts without pcbnew.

The physical board is inspected across a JSON boundary by ``inspect_kicad.py``
under KiCad's system Python.  Everything in this program uses only the normal
Python standard library, making it suitable for CI and pre-order checks.
"""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import design


ROOT = Path(__file__).resolve().parent.parent
KICAD = ROOT / "kicad"
SYSTEM_PYTHON = Path("/usr/bin/python3")
EXPECTED_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
EXPECTED_STACKUP = [
    ("F.Cu", "copper", 0.035, None),
    ("dielectric 1", "prepreg", 0.2104, 4.4),
    ("In1.Cu", "copper", 0.0152, None),
    ("dielectric 2", "core", 1.065, 4.38),
    ("In2.Cu", "copper", 0.0152, None),
    ("dielectric 3", "prepreg", 0.2104, 4.4),
    ("B.Cu", "copper", 0.035, None),
]
EXPECTED_GERBERS = {
    "Esp32Tap-F_Cu.gtl",
    "Esp32Tap-In1_Cu.g1",
    "Esp32Tap-In2_Cu.g2",
    "Esp32Tap-B_Cu.gbl",
    "Esp32Tap-F_Paste.gtp",
    "Esp32Tap-B_Paste.gbp",
    "Esp32Tap-F_Silkscreen.gto",
    "Esp32Tap-B_Silkscreen.gbo",
    "Esp32Tap-F_Mask.gts",
    "Esp32Tap-B_Mask.gbs",
    "Esp32Tap-Edge_Cuts.gm1",
    "Esp32Tap.drl",
}
OFFICIAL_EXTENDED_LCSC = {
    "C14860",
    "C23354",
    "C342541",
    "C354262",
    "C39148",
    "C106858",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def near(actual: float, expected: float, tolerance: float = 0.001) -> bool:
    return math.isclose(actual, expected, abs_tol=tolerance)


def inspect_board() -> dict[str, Any]:
    command = [
        str(SYSTEM_PYTHON),
        str(ROOT / "tools" / "inspect_kicad.py"),
        "--board",
        str(KICAD / "Esp32Tap.kicad_pcb"),
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    require(
        completed.returncode == 0,
        "physical inspector failed:\n"
        + completed.stdout
        + completed.stderr,
    )
    report = json.loads(completed.stdout)
    require(report.get("schema_version") == 1, "unexpected inspector schema")
    board = report.get("board")
    require(isinstance(board, dict), "inspector report has no board object")
    return board


def validate_geometry(board: dict[str, Any]) -> None:
    require(board["revision"] == "C", "PCB revision is not C")
    require(
        board["copper_layers"] == EXPECTED_LAYERS,
        f"copper layer set differs: {board['copper_layers']}",
    )
    outline = board["outline"]
    require(near(outline["width_mm"], 95.0), "board width is not 95 mm")
    require(near(outline["height_mm"], 55.0), "board height is not 55 mm")

    stackup = board["stackup"]
    require(
        stackup["name"] == "JLC04161H-7628",
        f"wrong stackup name: {stackup['name']}",
    )
    require(
        near(stackup["finished_thickness_mm"], 1.59),
        "finished thickness is not 1.59 mm",
    )
    observed_stackup = [
        (
            layer["name"],
            layer["type"],
            layer["thickness_mm"],
            layer["epsilon_r"],
        )
        for layer in stackup["layers"]
    ]
    require(
        len(observed_stackup) == len(EXPECTED_STACKUP),
        "stackup layer count differs",
    )
    for actual, expected in zip(observed_stackup, EXPECTED_STACKUP):
        require(actual[:2] == expected[:2], f"stackup layer differs: {actual}")
        require(near(actual[2], expected[2]), f"stackup thickness differs: {actual}")
        if expected[3] is None:
            require(actual[3] is None, f"unexpected copper epsilon_r: {actual}")
        else:
            require(
                actual[3] is not None and near(actual[3], expected[3]),
                f"stackup epsilon_r differs: {actual}",
            )

    footprints = board["footprints"]
    expected_refs = set(design.COMPONENTS) | {"MH1", "MH2", "MH3"}
    require(
        set(footprints) == expected_refs,
        "PCB footprint set differs from design.py + mounting holes",
    )
    origin = outline["min"]
    mounting = {
        "MH1": [2.9, 26.5],
        "MH2": [97.0, 3.0],
        "MH3": [97.0, 52.0],
    }
    for reference, local_position in mounting.items():
        footprint = footprints[reference]
        expected = [
            origin[0] + local_position[0],
            origin[1] + local_position[1],
        ]
        require(
            all(near(a, b) for a, b in zip(footprint["at"], expected)),
            f"{reference} mounting position differs",
        )
        require(footprint["board_only"], f"{reference} is not board-only")

    antenna = board["antenna"]
    require(antenna["reference"] == "U1", "antenna reference is not U1")
    require(
        near(origin[1] - antenna["physical_edge_y_mm"], 6.3),
        "antenna overhang is not 6.3 mm",
    )
    require(
        all(
            near(actual, expected)
            for actual, expected in zip(
                antenna["span_x_mm"],
                [169.0, 187.0],
            )
        ),
        "antenna physical span differs",
    )


def validate_connectivity_and_routing(board: dict[str, Any]) -> None:
    footprints = board["footprints"]
    for reference in design.DNP:
        footprint = footprints[reference]
        require(footprint["dnp"], f"{reference} lacks PCB DNP marking")
        require(
            footprint["excluded_from_bom"],
            f"{reference} is not excluded from PCB BOM",
        )
    for reference, pad in design.NC:
        pin_name = design.COMPONENTS[reference][7][pad]
        expected = f"unconnected-({reference}-{pin_name}-Pad{pad})"
        require(
            footprints[reference]["pads"][pad]["net"] == expected,
            f"{reference}.{pad} NC parity net differs",
        )

    tracks = board["tracks"]
    require(
        not [
            track
            for track in tracks
            if track["layer"] == "In1.Cu" and track["net"] != "GND"
        ],
        "In1.Cu contains non-ground routing",
    )
    usb_tracks = [
        track for track in tracks if track["net"].startswith("USB_D")
    ]
    require(usb_tracks, "USB has no routed copper")
    require(
        all(track["layer"] == "F.Cu" for track in usb_tracks),
        "USB leaves F.Cu",
    )
    require(
        not [
            via
            for via in board["vias"]
            if via["net"].startswith("USB_D")
        ],
        "USB contains vias",
    )
    breakouts = [
        track
        for track in usb_tracks
        if track.get("role") == "CONNECTOR_BREAKOUT"
    ]
    require(len(breakouts) == 4, "USB connector breakout count is not four")
    require(
        all(near(track["width_mm"], 0.20) for track in breakouts),
        "USB connector breakout is not 0.20 mm",
    )
    require(
        all(
            near(track["width_mm"], 0.2906)
            for track in usb_tracks
            if track.get("role") != "CONNECTOR_BREAKOUT"
        ),
        "controlled USB trace width is not 0.2906 mm",
    )
    pair_sections = {
        track.get("pair_section")
        for track in usb_tracks
        if track.get("pair_section")
    }
    require(pair_sections, "inspector found no controlled USB pair section")
    require(
        all(
            track.get("reference_plane") == "In1.Cu:GND"
            for track in usb_tracks
            if track.get("pair_section")
        ),
        "controlled USB pair lacks In1.Cu ground reference",
    )
    area_names = {area["name"] for area in board["rule_areas"]}
    require(
        {
            "ESP32_ANTENNA_ALL_COPPER_KEEPOUT",
            "USB_90R_CONTROLLED_CORRIDOR",
            "TP5_TP13_BOTTOM_FIXTURE",
        }
        <= area_names,
        "required named physical rule areas are missing",
    )


def csv_references(path: Path, column: str) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return {
            reference.strip()
            for row in rows
            for reference in row[column].split(",")
            if reference.strip()
        }


def validate_assembly(board: dict[str, Any]) -> None:
    expected = {
        reference
        for reference, component in design.COMPONENTS.items()
        if reference not in design.DNP and component[4] != "none"
    }
    bom_path = ROOT / "bom" / "BOM.csv"
    cpl_path = ROOT / "bom" / "CPL-positions.csv"
    require(
        csv_references(bom_path, "Designator") == expected,
        "BOM designator set differs from populated design",
    )
    require(
        csv_references(cpl_path, "Designator") == expected,
        "CPL designator set differs from populated design",
    )

    with bom_path.open(newline="", encoding="utf-8") as handle:
        bom_rows = list(csv.DictReader(handle))
    for row in bom_rows:
        if row["LCSC Part #"] in OFFICIAL_EXTENDED_LCSC:
            require(
                row["JLC class"] == "Extended",
                f"{row['LCSC Part #']} is not marked Extended in BOM",
            )
    r9_rows = [
        row
        for row in bom_rows
        if "R9" in row["Designator"].split(",")
    ]
    require(len(r9_rows) == 1, "R9 is absent or duplicated in BOM")
    require(r9_rows[0]["Comment"] == "560R", "R9 BOM value is not 560R")
    require(
        r9_rows[0]["LCSC Part #"] == "C23204",
        "R9 BOM LCSC part differs",
    )

    with cpl_path.open(newline="", encoding="utf-8") as handle:
        cpl_rows = {row["Designator"]: row for row in csv.DictReader(handle)}
    min_x = board["outline"]["min"][0]
    max_y = board["outline"]["max"][1]
    for reference in expected:
        footprint = board["footprints"][reference]
        row = cpl_rows[reference]
        x = float(row["Mid X"].removesuffix("mm"))
        y = float(row["Mid Y"].removesuffix("mm"))
        require(near(x, footprint["at"][0] - min_x, 0.01), f"{reference} CPL X differs")
        require(near(y, max_y - footprint["at"][1], 0.01), f"{reference} CPL Y differs")
        require(
            near(float(row["Rotation"]), footprint["rotation_deg"], 0.01),
            f"{reference} CPL rotation differs",
        )
        require(row["Layer"] == "Top", f"{reference} CPL layer is not Top")


def validate_reports_and_fab() -> None:
    name = "drc.rpt"
    report = (KICAD / name).read_text(encoding="utf-8")
    for marker in (
        "Found 0 DRC violations",
        "Found 0 unconnected pads",
        "Found 0 Footprint errors",
    ):
        require(marker in report, f"{name} is not clean: missing {marker}")
    ignored_section = report.partition("** Ignored checks **")[2].partition(
        "** End of Report **"
    )[0]
    ignored = {
        line.strip().removeprefix("- ")
        for line in ignored_section.splitlines()
        if line.strip().startswith("- ")
    }
    require(
        ignored == {"Silkscreen clipped by board edge"},
        f"{name} has unexpected ignored checks: {sorted(ignored)}",
    )

    gerber_dir = KICAD / "gerbers"
    actual = {path.name for path in gerber_dir.iterdir() if path.is_file()}
    require(EXPECTED_GERBERS <= actual, "fabrication directory is incomplete")
    with zipfile.ZipFile(KICAD / "Esp32Tap-gerbers.zip") as archive:
        archived = {Path(name).name for name in archive.namelist()}
    require(EXPECTED_GERBERS <= archived, "fabrication ZIP is incomplete")


def main() -> int:
    try:
        design.validate()
        board = inspect_board()
        validate_geometry(board)
        validate_connectivity_and_routing(board)
        validate_assembly(board)
        validate_reports_and_fab()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: Rev B board, stackup, routing, parity, assembly, "
        "and fabrication artifacts are internally consistent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
