#!/usr/bin/env python3
"""Export and atomically publish the Esp32Tap Rev B fabrication package.

KiCad writes generation timestamps into Gerber, drill, and job files.  This
tool removes only those timestamps, validates the exact four-layer member set,
and writes a deterministic ZIP.  Checked-in artifacts are replaced only after
the complete staged package has passed validation.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KICAD_DIR = ROOT / "kicad"
BOARD = KICAD_DIR / "Esp32Tap.kicad_pcb"
GERBER_DIR = KICAD_DIR / "gerbers"
ARCHIVE = KICAD_DIR / "Esp32Tap-gerbers.zip"

LAYERS = (
    "F.Cu",
    "In1.Cu",
    "In2.Cu",
    "B.Cu",
    "F.Mask",
    "B.Mask",
    "F.Paste",
    "B.Paste",
    "F.Silkscreen",
    "B.Silkscreen",
    "Edge.Cuts",
)
GERBER_FUNCTIONS = {
    "Esp32Tap-F_Cu.gtl": "Copper,L1,Top",
    "Esp32Tap-In1_Cu.g1": "Copper,L2,Inr",
    "Esp32Tap-In2_Cu.g2": "Copper,L3,Inr",
    "Esp32Tap-B_Cu.gbl": "Copper,L4,Bot",
    "Esp32Tap-F_Mask.gts": "Soldermask,Top",
    "Esp32Tap-B_Mask.gbs": "Soldermask,Bot",
    "Esp32Tap-F_Paste.gtp": "Paste,Top",
    "Esp32Tap-B_Paste.gbp": "Paste,Bot",
    "Esp32Tap-F_Silkscreen.gto": "Legend,Top",
    "Esp32Tap-B_Silkscreen.gbo": "Legend,Bot",
    "Esp32Tap-Edge_Cuts.gm1": "Profile,NP",
}
GERBER_POLARITIES = {
    filename: (
        None
        if filename == "Esp32Tap-Edge_Cuts.gm1"
        else (
            "Negative"
            if filename in {"Esp32Tap-F_Mask.gts", "Esp32Tap-B_Mask.gbs"}
            else "Positive"
        )
    )
    for filename in GERBER_FUNCTIONS
}
JOB_FUNCTIONS = {
    "Esp32Tap-F_Cu.gtl": "Copper,L1,Top",
    "Esp32Tap-In1_Cu.g1": "Copper,L2,Inr",
    "Esp32Tap-In2_Cu.g2": "Copper,L3,Inr",
    "Esp32Tap-B_Cu.gbl": "Copper,L4,Bot",
    "Esp32Tap-F_Mask.gts": "SolderMask,Top",
    "Esp32Tap-B_Mask.gbs": "SolderMask,Bot",
    "Esp32Tap-F_Paste.gtp": "SolderPaste,Top",
    "Esp32Tap-B_Paste.gbp": "SolderPaste,Bot",
    "Esp32Tap-F_Silkscreen.gto": "Legend,Top",
    "Esp32Tap-B_Silkscreen.gbo": "Legend,Bot",
    "Esp32Tap-Edge_Cuts.gm1": "Profile",
}
JOB_POLARITIES = {
    filename: (
        "Negative"
        if filename in {"Esp32Tap-F_Mask.gts", "Esp32Tap-B_Mask.gbs"}
        else "Positive"
    )
    for filename in JOB_FUNCTIONS
}
REQUIRED_GENERAL_SPECS = {
    "Size": {"X": 100.1, "Y": 55.1},
    "LayerNumber": 4,
    "BoardThickness": 1.59,
    "Finish": "ENIG",
    "ImpedanceControlled": True,
}
EMPTY_ARTWORK_LAYERS = {
    "Esp32Tap-B_Paste.gbp",
    "Esp32Tap-B_Silkscreen.gbo",
}
KICAD_PROJECT_FILES = {
    "Esp32Tap.kicad_sch",
    "Esp32Tap.kicad_pcb",
    "Esp32Tap.kicad_pro",
    "Esp32Tap.kicad_dru",
    "esp32tap.kicad_sym",
    "sym-lib-table",
}
EXPECTED_FAB_FILES = set(GERBER_FUNCTIONS) | {
    "Esp32Tap-job.gbrjob",
    "Esp32Tap.drl",
}

NORMALIZED_ISO_DATE = "1970-01-01T00:00:00+00:00"
NORMALIZED_TEXT_DATE = "1970-01-01 00:00:00"
NORMALIZED_DRILL_DATE = "1970-01-01T00:00:00"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
BOM_FIELDS = {
    "Comment",
    "Designator",
    "Footprint",
    "LCSC Part #",
    "JLC class",
    "Qty",
    "Unit cost (USD)",
    "Ext cost (USD)",
    "Description",
}
CPL_FIELDS = {
    "Designator",
    "Val",
    "Package",
    "Mid X",
    "Mid Y",
    "Rotation",
    "Layer",
}


class FabExportError(RuntimeError):
    """Raised when fabrication output cannot be proven complete and coherent."""


def _exact_reference_set(
    label: str,
    expected: set[str],
    actual: set[str],
) -> None:
    if actual != expected:
        raise FabExportError(
            f"{label} references differ: "
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _parse_mm(value: str, label: str) -> float:
    match = re.fullmatch(
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))mm",
        value,
    )
    if match is None:
        raise FabExportError(f"{label} is not an explicit millimetre value")
    result = float(match.group(1))
    if not math.isfinite(result):
        raise FabExportError(f"{label} is not finite")
    return result


def validate_assembly_records(
    *,
    components: dict[str, tuple[Any, ...]],
    dnp: set[str],
    schematic: dict[str, dict[str, str]],
    board: dict[str, Any],
    bom_rows: list[dict[str, str]],
    cpl_rows: list[dict[str, str]],
) -> None:
    """Cross-check exact references and purchasing metadata across artifacts."""
    design_references = set(components)
    populated = {
        reference
        for reference, component in components.items()
        if reference not in dnp and component[4] != "none"
    }
    _exact_reference_set(
        "schematic",
        design_references,
        set(schematic),
    )
    try:
        footprints = board["footprints"]
        outline_min = board["outline"]["min"]
        outline_max = board["outline"]["max"]
    except (KeyError, TypeError) as error:
        raise FabExportError("PCB inspector lacks footprints/outline") from error
    if not isinstance(footprints, dict):
        raise FabExportError("PCB footprints must be an object")
    _exact_reference_set(
        "PCB",
        design_references | {"MH1", "MH2", "MH3"},
        set(footprints),
    )
    flag_names = ("dnp", "excluded_from_bom", "board_only")
    for reference, component in components.items():
        expected_flags = {
            "dnp": reference in dnp,
            "excluded_from_bom": (
                reference in dnp or component[4] == "none"
            ),
            "board_only": False,
        }
        actual_flags = {
            name: footprints[reference].get(name)
            for name in flag_names
        }
        if (
            any(type(value) is not bool for value in actual_flags.values())
            or actual_flags != expected_flags
        ):
            raise FabExportError(
                f"PCB {reference} assembly flags differ: "
                f"expected={expected_flags}, actual={actual_flags}"
            )
    for reference in ("MH1", "MH2", "MH3"):
        expected_flags = {
            "dnp": False,
            "excluded_from_bom": True,
            "board_only": True,
        }
        actual_flags = {
            name: footprints[reference].get(name)
            for name in flag_names
        }
        if (
            any(type(value) is not bool for value in actual_flags.values())
            or actual_flags != expected_flags
        ):
            raise FabExportError(
                f"PCB {reference} assembly flags differ: "
                f"expected={expected_flags}, actual={actual_flags}"
            )

    bom_by_reference: dict[str, dict[str, str]] = {}
    for row in bom_rows:
        if set(row) != BOM_FIELDS:
            raise FabExportError(
                "BOM columns differ: "
                f"missing={sorted(BOM_FIELDS - set(row))}, "
                f"extra={sorted(set(row) - BOM_FIELDS)}"
            )
        references = [
            reference.strip()
            for reference in row.get("Designator", "").split(",")
            if reference.strip()
        ]
        if not references:
            raise FabExportError("BOM contains a row without designators")
        try:
            quantity = int(row.get("Qty", ""))
        except ValueError as error:
            raise FabExportError(
                f"BOM {references} Qty is not an integer"
            ) from error
        if quantity != len(references):
            raise FabExportError(
                f"BOM {references} Qty={quantity} does not match designators"
            )
        for reference in references:
            if reference in bom_by_reference:
                raise FabExportError(f"BOM repeats reference {reference}")
            bom_by_reference[reference] = row
    _exact_reference_set("BOM", populated, set(bom_by_reference))

    cpl_by_reference: dict[str, dict[str, str]] = {}
    for row in cpl_rows:
        if set(row) != CPL_FIELDS:
            raise FabExportError(
                "CPL columns differ: "
                f"missing={sorted(CPL_FIELDS - set(row))}, "
                f"extra={sorted(set(row) - CPL_FIELDS)}"
            )
        reference = row.get("Designator", "").strip()
        if not reference:
            raise FabExportError("CPL contains a row without a designator")
        if reference in cpl_by_reference:
            raise FabExportError(f"CPL repeats reference {reference}")
        cpl_by_reference[reference] = row
    _exact_reference_set("CPL", populated, set(cpl_by_reference))

    if (
        not isinstance(outline_min, list)
        or not isinstance(outline_max, list)
        or len(outline_min) != 2
        or len(outline_max) != 2
    ):
        raise FabExportError("PCB outline min/max must be [x, y]")
    try:
        origin_x = float(outline_min[0])
        maximum_y = float(outline_max[1])
    except (TypeError, ValueError) as error:
        raise FabExportError("PCB outline coordinates are not numeric") from error

    for reference, component in components.items():
        (
            value,
            footprint_library,
            footprint_name,
            lcsc,
            jlc_class,
            unit_cost,
            description,
            _pins,
        ) = component
        full_footprint = f"{footprint_library}:{footprint_name}"
        expected_schematic = {
            "value": value,
            "footprint": full_footprint,
            "lcsc": lcsc,
            "jlc_class": jlc_class,
        }
        if schematic[reference] != expected_schematic:
            raise FabExportError(
                f"schematic {reference} metadata differs: "
                f"expected={expected_schematic}, "
                f"actual={schematic[reference]}"
            )
        board_footprint = footprints[reference]
        if board_footprint.get("footprint") != full_footprint:
            raise FabExportError(
                f"PCB {reference} footprint differs: "
                f"expected={full_footprint}, "
                f"actual={board_footprint.get('footprint')}"
            )
        if reference not in populated:
            continue

        if board_footprint.get("layer") != "F.Cu":
            raise FabExportError(
                f"PCB {reference} layer differs: expected=F.Cu, "
                f"actual={board_footprint.get('layer')}"
            )
        rotation = board_footprint.get("rotation_deg")
        if (
            isinstance(rotation, bool)
            or not isinstance(rotation, (int, float))
            or not math.isfinite(float(rotation))
        ):
            raise FabExportError(
                f"PCB {reference} rotation_deg must be a finite number"
            )
        rounded_rotation = round(float(rotation))
        if not math.isclose(
            float(rotation),
            float(rounded_rotation),
            abs_tol=1e-9,
        ):
            raise FabExportError(
                f"PCB {reference} rotation_deg must be integral; "
                f"actual={float(rotation):g}"
            )

        bom = bom_by_reference[reference]
        expected_bom = {
            "Comment": value,
            "Footprint": footprint_name,
            "LCSC Part #": lcsc,
            "JLC class": jlc_class,
            "Unit cost (USD)": f"{unit_cost:.3f}",
            "Description": description,
        }
        for field, expected in expected_bom.items():
            if bom.get(field) != expected:
                raise FabExportError(
                    f"BOM {reference} {field} differs: "
                    f"expected={expected!r}, actual={bom.get(field)!r}"
                )
        row_references = [
            item.strip()
            for item in bom["Designator"].split(",")
            if item.strip()
        ]
        expected_extended = f"{unit_cost * len(row_references):.3f}"
        if bom.get("Ext cost (USD)") != expected_extended:
            raise FabExportError(
                f"BOM {reference} Ext cost differs: "
                f"expected={expected_extended}, "
                f"actual={bom.get('Ext cost (USD)')}"
            )

        cpl = cpl_by_reference[reference]
        expected_cpl = {
            "Val": value,
            "Package": footprint_name,
            "Layer": "Top",
        }
        for field, expected in expected_cpl.items():
            if cpl.get(field) != expected:
                raise FabExportError(
                    f"CPL {reference} {field} differs: "
                    f"expected={expected!r}, actual={cpl.get(field)!r}"
                )
        try:
            actual_rotation = float(cpl.get("Rotation", ""))
        except (TypeError, ValueError) as error:
            raise FabExportError(
                f"CPL {reference} Rotation is not numeric"
            ) from error
        expected_rotation = float(rounded_rotation)
        rotation_delta = abs(
            (actual_rotation - expected_rotation + 180.0) % 360.0 - 180.0
        )
        if (
            not math.isfinite(actual_rotation)
            or rotation_delta > 1e-9
        ):
            raise FabExportError(
                f"CPL {reference} Rotation differs: "
                f"expected={expected_rotation:g} degrees modulo 360, "
                f"actual={actual_rotation:g}"
            )
        position = board_footprint.get("at")
        if not isinstance(position, list) or len(position) != 2:
            raise FabExportError(f"PCB {reference}.at must be [x, y]")
        expected_x = float(position[0]) - origin_x
        expected_y = maximum_y - float(position[1])
        actual_x = _parse_mm(cpl.get("Mid X", ""), f"CPL {reference} Mid X")
        actual_y = _parse_mm(cpl.get("Mid Y", ""), f"CPL {reference} Mid Y")
        if not math.isclose(actual_x, expected_x, abs_tol=0.0005):
            raise FabExportError(
                f"CPL {reference} Mid X differs: "
                f"expected={expected_x:.3f}mm, actual={actual_x:.3f}mm"
            )
        if not math.isclose(actual_y, expected_y, abs_tol=0.0005):
            raise FabExportError(
                f"CPL {reference} Mid Y differs: "
                f"expected={expected_y:.3f}mm, actual={actual_y:.3f}mm"
            )


def run_kicad(
    command: list[str],
    label: str,
    *,
    cwd: Path,
) -> None:
    """Run one non-interactive KiCad command and fail with its diagnostics."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FabExportError(f"{label} could not run: {error}") from error
    if completed.returncode != 0:
        raise FabExportError(
            f"{label} failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def _run_stdout(
    command: list[str],
    label: str,
    *,
    cwd: Path,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FabExportError(f"{label} could not run: {error}") from error
    if completed.returncode:
        raise FabExportError(
            f"{label} failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed.stdout


@contextmanager
def isolated_kicad_project(source: Path) -> Iterator[Path]:
    """Yield a same-basename project copy so KiCad sidecars stay disposable."""
    source = Path(os.path.abspath(source))
    if source.is_symlink() or not source.is_file():
        raise FabExportError(
            f"KiCad project source must be a regular non-symlink file: {source}"
        )
    source_directory = source.parent.resolve()
    if source.resolve().parent != source_directory:
        raise FabExportError(
            f"KiCad project source resolves outside its directory: {source}"
        )

    with tempfile.TemporaryDirectory(
        prefix="esp32tap-kicad-project-",
    ) as temporary:
        isolated_directory = Path(temporary)
        filenames = set(KICAD_PROJECT_FILES) | {source.name}
        for filename in sorted(filenames):
            candidate = source_directory / filename
            if not candidate.exists():
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise FabExportError(
                    "KiCad project companion must be a regular "
                    f"non-symlink file: {candidate}"
                )
            shutil.copy2(candidate, isolated_directory / filename)
        isolated_source = isolated_directory / source.name
        if not isolated_source.is_file():
            raise FabExportError(
                f"failed to isolate KiCad project source: {source}"
            )
        yield isolated_source


def _load_design(root: Path) -> Any:
    path = root / "tools" / "design.py"
    spec = importlib.util.spec_from_file_location(
        "esp32tap_fab_design",
        path,
    )
    if spec is None or spec.loader is None:
        raise FabExportError(f"cannot load design source: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        module.validate()
    except Exception as error:
        raise FabExportError(f"design source validation failed: {error}") from error
    return module


def _load_schematic_records(root: Path) -> dict[str, dict[str, str]]:
    schematic = root / "kicad" / "Esp32Tap.kicad_sch"
    with isolated_kicad_project(schematic) as isolated_schematic:
        output = isolated_schematic.parent / "Esp32Tap.xml"
        run_kicad(
            [
                "kicad-cli",
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                str(output),
                str(isolated_schematic),
            ],
            "KiCad schematic netlist export",
            cwd=isolated_schematic.parent,
        )
        try:
            xml_root = ET.parse(output).getroot()
        except (OSError, ET.ParseError) as error:
            raise FabExportError(
                f"cannot parse schematic netlist XML: {error}"
            ) from error

    records: dict[str, dict[str, str]] = {}
    for component in xml_root.findall("./components/comp"):
        reference = component.get("ref", "")
        properties = {
            property_element.get("name", ""): property_element.get("value", "")
            for property_element in component.findall("./property")
        }
        if not reference or reference in records:
            raise FabExportError(
                f"schematic netlist has blank/duplicate reference {reference!r}"
            )
        records[reference] = {
            "value": component.findtext("value", default=""),
            "footprint": component.findtext("footprint", default=""),
            "lcsc": properties.get("LCSC", ""),
            "jlc_class": properties.get("JLC Class", ""),
        }
    return records


def _load_board_record(root: Path) -> dict[str, Any]:
    inspector = root / "tools" / "inspect_kicad.py"
    if not inspector.is_file():
        raise FabExportError(f"PCB inspector is missing: {inspector}")
    output = _run_stdout(
        ["/usr/bin/python3", str(inspector), "--json"],
        "PCB inspector",
        cwd=root,
    )
    try:
        report = json.loads(output)
        board = report["board"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise FabExportError(
            f"PCB inspector returned invalid versioned JSON: {error}"
        ) from error
    if report.get("schema_version") != 1 or not isinstance(board, dict):
        raise FabExportError("PCB inspector schema_version must be 1")
    return board


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(
                reader.fieldnames
            ):
                raise FabExportError(
                    f"{path.name} has missing/duplicate CSV headings"
                )
            return [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise FabExportError(f"cannot parse {path}: {error}") from error


def audit_assembly(root: Path = ROOT) -> None:
    """Audit design/schematic/PCB/BOM/CPL parity and DNP exclusion."""
    design = _load_design(root)
    validate_assembly_records(
        components=dict(design.COMPONENTS),
        dnp=set(design.DNP),
        schematic=_load_schematic_records(root),
        board=_load_board_record(root),
        bom_rows=_load_csv_rows(root / "bom" / "BOM.csv"),
        cpl_rows=_load_csv_rows(root / "bom" / "CPL-positions.csv"),
    )


def _replace_text(path: Path, substitutions: Iterable[tuple[str, str]]) -> None:
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise FabExportError(f"cannot read text artifact {path}: {error}") from error
    normalized = original
    for pattern, replacement in substitutions:
        normalized, count = re.subn(pattern, replacement, normalized)
        if count != 1:
            raise FabExportError(
                f"{path.name} must contain exactly one timestamp matching "
                f"{pattern!r}; found {count}"
            )
    if normalized != original:
        path.write_text(normalized, encoding="utf-8", newline="\n")


def normalize_stage(directory: Path) -> None:
    """Normalize only KiCad's volatile creation-date fields."""
    for filename in GERBER_FUNCTIONS:
        _replace_text(
            directory / filename,
            (
                (
                    r"%TF\.CreationDate,[^*]*\*%",
                    f"%TF.CreationDate,{NORMALIZED_ISO_DATE}*%",
                ),
                (
                    r"(?m)^(G04 Created by KiCad .* date )[^\r\n*]+(\*)$",
                    rf"\g<1>{NORMALIZED_TEXT_DATE}\g<2>",
                ),
            ),
        )
    _replace_text(
        directory / "Esp32Tap.drl",
        (
            (
                r"(?m)^(;\s*DRILL file KiCad .* date )[^\r\n]+$",
                rf"\g<1>{NORMALIZED_DRILL_DATE}",
            ),
            (
                r"(?m)^(;\s*#@!\s*TF\.CreationDate,)[^\r\n]+$",
                rf"\g<1>{NORMALIZED_ISO_DATE}",
            ),
        ),
    )
    _replace_text(
        directory / "Esp32Tap-job.gbrjob",
        (
            (
                r'("CreationDate"\s*:\s*")[^"]+(")',
                rf"\g<1>{NORMALIZED_ISO_DATE}\g<2>",
            ),
        ),
    )


def _read_nonempty(path: Path) -> str:
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise FabExportError(f"cannot read fabrication member {path}: {error}") from error
    if not payload.strip():
        raise FabExportError(f"fabrication member is empty: {path.name}")
    if "\r" in payload:
        raise FabExportError(f"fabrication member is not LF-normalized: {path.name}")
    return payload


def _validate_profile_geometry(payload: str) -> None:
    if payload.count("%FSLAX46Y46*%") != 1 or payload.count("%MOMM*%") != 1:
        raise FabExportError(
            "Esp32Tap-Edge_Cuts.gm1 profile must use exact 4.6 metric format"
        )
    apertures = re.findall(r"(?m)^%ADD[^%\r\n]+\*%$", payload)
    if (
        apertures != ["%ADD10C,0.100000*%"]
        or len(re.findall(r"(?m)^D10\*$", payload)) != 1
    ):
        raise FabExportError(
            "Esp32Tap-Edge_Cuts.gm1 profile must use one 0.100 mm aperture"
        )
    if re.search(
        r"(?m)^(?:G0[23]\*|G0[123](?!\*)[^\r\n]+|"
        r"G3[67]\*|D0[123]\*|"
        r"(?:(?:X[+-]?\d+)(?:Y[+-]?\d+)?|Y[+-]?\d+)D03\*)$",
        payload,
    ):
        raise FabExportError(
            "Esp32Tap-Edge_Cuts.gm1 profile contains unsupported extra "
            "flash, arc, region, or modal geometry"
        )

    position: tuple[float, float] | None = None
    segments: set[
        tuple[tuple[float, float], tuple[float, float]]
    ] = set()
    coordinate_commands = re.findall(
        r"(?m)^X([+-]?\d+)Y([+-]?\d+)D0([12])\*$",
        payload,
    )
    all_coordinate_commands = re.findall(
        r"(?m)^(?:(?:X[+-]?\d+)(?:Y[+-]?\d+)?|"
        r"Y[+-]?\d+)D0[123]\*$",
        payload,
    )
    if (
        len(coordinate_commands) != 8
        or len(all_coordinate_commands) != len(coordinate_commands)
    ):
        raise FabExportError(
            "Esp32Tap-Edge_Cuts.gm1 profile must contain exactly four "
            "moves and four straight edge draws"
        )
    for raw_x, raw_y, operation in coordinate_commands:
        point = (int(raw_x) / 1_000_000, int(raw_y) / 1_000_000)
        if operation == "1":
            if position is None:
                raise FabExportError(
                    "Esp32Tap-Edge_Cuts.gm1 profile draws before moving"
                )
            segments.add(tuple(sorted((position, point))))
        position = point

    top_left = (100.0, -100.0)
    top_right = (200.0, -100.0)
    bottom_left = (100.0, -155.0)
    bottom_right = (200.0, -155.0)
    expected_segments = {
        tuple(sorted((top_left, top_right))),
        tuple(sorted((top_right, bottom_right))),
        tuple(sorted((bottom_right, bottom_left))),
        tuple(sorted((bottom_left, top_left))),
    }
    if segments != expected_segments:
        raise FabExportError(
            "Esp32Tap-Edge_Cuts.gm1 profile must be the closed "
            "100.0 x 55.0 mm Rev B rectangle"
        )


def _validate_drill_artwork(drill: str) -> None:
    tool_plating: dict[str, str] = {}
    pending_plating: str | None = None
    active_tool: str | None = None
    hits = {"Plated": 0, "NonPlated": 0}
    for line in (item.strip() for item in drill.splitlines()):
        aperture = re.fullmatch(
            r";\s*#@!\s*TA\.AperFunction,"
            r"(Plated|NonPlated),[^\r\n]+",
            line,
        )
        if aperture:
            pending_plating = aperture.group(1)
            continue
        definition = re.fullmatch(r"T(\d+)C([0-9]+(?:\.[0-9]+)?)", line)
        if definition:
            tool, raw_diameter = definition.groups()
            if (
                pending_plating is None
                or tool in tool_plating
                or not math.isfinite(float(raw_diameter))
                or float(raw_diameter) <= 0
            ):
                raise FabExportError(
                    "Esp32Tap.drl has an invalid or ambiguous drill tool"
                )
            tool_plating[tool] = pending_plating
            pending_plating = None
            continue
        selection = re.fullmatch(r"T(\d+)", line)
        if selection:
            active_tool = selection.group(1)
            if active_tool not in tool_plating:
                raise FabExportError(
                    "Esp32Tap.drl selects an undefined drill tool"
                )
            continue
        if re.fullmatch(
            r"X[+-]?[0-9]+(?:\.[0-9]+)?"
            r"Y[+-]?[0-9]+(?:\.[0-9]+)?",
            line,
        ):
            if active_tool is None:
                raise FabExportError(
                    "Esp32Tap.drl has a hit without a selected drill tool"
                )
            hits[tool_plating[active_tool]] += 1

    if not tool_plating:
        raise FabExportError("Esp32Tap.drl has no drill tool definitions")
    for plating in ("Plated", "NonPlated"):
        if hits[plating] < 1:
            raise FabExportError(
                f"Esp32Tap.drl has no {plating} drill hit"
            )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FabExportError(f"Gerber job has duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise FabExportError(
        f"Gerber job contains non-standard JSON constant {value!r}"
    )


def validate_stage(
    directory: Path,
    *,
    require_normalized: bool = False,
) -> None:
    """Fail closed unless the directory is the exact Rev B four-layer package."""
    if directory.is_symlink() or not directory.is_dir():
        raise FabExportError(f"fabrication stage is not a directory: {directory}")
    entries = list(directory.iterdir())
    actual = {path.name for path in entries}
    invalid = sorted(
        path.name
        for path in entries
        if path.is_symlink() or not path.is_file()
    )
    if invalid or actual != EXPECTED_FAB_FILES:
        missing = sorted(EXPECTED_FAB_FILES - actual)
        extra = sorted(actual - EXPECTED_FAB_FILES)
        raise FabExportError(
            "fabrication member set is not exact: "
            f"missing={missing}, extra={extra}, invalid={invalid}"
        )

    for filename, expected_function in GERBER_FUNCTIONS.items():
        payload = _read_nonempty(directory / filename)
        functions = re.findall(
            r"%TF\.FileFunction,([^*]*)\*%",
            payload,
        )
        if functions != [expected_function]:
            raise FabExportError(
                f"{filename} must have exactly one FileFunction "
                f"{expected_function}; actual={functions}"
            )
        expected_polarity = GERBER_POLARITIES[filename]
        polarities = re.findall(
            r"%TF\.FilePolarity,([^*]*)\*%",
            payload,
        )
        expected_polarities = (
            [] if expected_polarity is None else [expected_polarity]
        )
        if polarities != expected_polarities:
            raise FabExportError(
                f"{filename} FilePolarity differs: "
                f"expected={expected_polarities}, actual={polarities}"
            )
        if payload.count("%LPD*%") != 1 or "%LPC*%" in payload:
            raise FabExportError(
                f"{filename} must have exactly one LPD and no LPC"
            )
        if re.search(
            r"%(?:SR|LM|LR|LS|OF|SF|MI|IP|AS|IR)[^%\r\n]*\*%",
            payload,
        ):
            raise FabExportError(
                f"{filename} must not contain Gerber repeat or transform "
                "commands"
            )
        artwork = re.findall(
            r"(?m)^(?:(?:X[+-]?\d+)(?:Y[+-]?\d+)?|"
            r"Y[+-]?\d+)D0[13]\*$",
            payload,
        )
        if filename not in EMPTY_ARTWORK_LAYERS and not artwork:
            raise FabExportError(
                f"{filename} must contain actual Gerber artwork"
            )
        if filename == "Esp32Tap-Edge_Cuts.gm1":
            _validate_profile_geometry(payload)
        commands = [
            line.strip()
            for line in payload.splitlines()
            if line.strip()
        ]
        if commands.count("M02*") != 1 or commands[-1] != "M02*":
            raise FabExportError(
                f"{filename} Gerber end marker is missing or not final"
            )
        if require_normalized:
            creation_dates = re.findall(
                r"%TF\.CreationDate,([^*]*)\*%",
                payload,
            )
            text_dates = re.findall(
                r"(?m)^G04 Created by KiCad .* date ([^\r\n*]+)\*$",
                payload,
            )
            if (
                creation_dates != [NORMALIZED_ISO_DATE]
                or text_dates != [NORMALIZED_TEXT_DATE]
            ):
                raise FabExportError(
                    f"{filename} timestamps are not exactly normalized"
                )

    drill = _read_nonempty(directory / "Esp32Tap.drl")
    drill_functions = [
        value.strip()
        for value in re.findall(
            r"(?m)^;\s*#@!\s*TF\.FileFunction,([^\r\n]+)$",
            drill,
        )
    ]
    if drill_functions != ["MixedPlating,1,4"]:
        raise FabExportError(
            "Esp32Tap.drl must have exactly one drill FileFunction spanning "
            f"copper layers 1 through 4; actual={drill_functions}"
        )
    drill_commands = [
        line.strip()
        for line in drill.splitlines()
        if line.strip()
    ]
    unit_commands = [
        command
        for command in drill_commands
        if re.fullmatch(r"(?:METRIC|INCH)(?:,.*)?", command)
    ]
    legacy_mode_commands = [
        command
        for command in drill_commands
        if re.fullmatch(r"(?:M7[12]|G7[01]|ICI,.*)", command)
    ]
    if (
        len(re.findall(r"(?m)^M48\s*$", drill)) != 1
        or drill_commands.count("M30") != 1
        or drill_commands[-1] != "M30"
    ):
        raise FabExportError("Esp32Tap.drl is not a complete Excellon drill file")
    if (
        len(
            re.findall(
                r"(?m)^;\s*FORMAT=\{-:-/ absolute / metric / decimal\}\s*$",
                drill,
            )
        )
        != 1
        or drill_commands.count("FMAT,2") != 1
        or unit_commands != ["METRIC"]
        or drill_commands.count("G90") != 1
        or "G91" in drill_commands
        or legacy_mode_commands
    ):
        raise FabExportError(
            "Esp32Tap.drl must use exactly one absolute metric decimal mode"
        )
    _validate_drill_artwork(drill)
    if require_normalized:
        drill_creation_dates = re.findall(
            r"(?m)^;\s*#@!\s*TF\.CreationDate,([^\r\n]+)$",
            drill,
        )
        drill_text_dates = re.findall(
            r"(?m)^;\s*DRILL file KiCad .* date ([^\r\n]+)$",
            drill,
        )
        if (
            drill_creation_dates != [NORMALIZED_ISO_DATE]
            or drill_text_dates != [NORMALIZED_DRILL_DATE]
        ):
            raise FabExportError(
                "Esp32Tap.drl timestamps are not exactly normalized"
            )

    job_text = _read_nonempty(directory / "Esp32Tap-job.gbrjob")
    try:
        job = json.loads(
            job_text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise FabExportError(f"Gerber job JSON is invalid: {error}") from error
    try:
        entries = job["FilesAttributes"]
        if not isinstance(entries, list) or len(entries) != len(JOB_FUNCTIONS):
            raise FabExportError(
                "Gerber job must contain each file/function entry exactly once"
            )
        job_attributes: dict[str, dict[str, str]] = {}
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or set(entry)
                != {"Path", "FileFunction", "FilePolarity"}
            ):
                raise FabExportError(
                    "Gerber job FilesAttributes entries must contain exactly "
                    "Path, FileFunction, and FilePolarity"
                )
            path = entry["Path"]
            if not isinstance(path, str) or path in job_attributes:
                raise FabExportError(
                    "Gerber job has a blank or duplicate file entry"
                )
            job_attributes[path] = entry
    except (KeyError, TypeError) as error:
        raise FabExportError(
            "Gerber job lacks valid FilesAttributes"
        ) from error
    expected_job_attributes = {
        filename: {
            "Path": filename,
            "FileFunction": function,
            "FilePolarity": JOB_POLARITIES[filename],
        }
        for filename, function in JOB_FUNCTIONS.items()
    }
    if job_attributes != expected_job_attributes:
        raise FabExportError(
            "Gerber job file/function/polarity mapping is not the exact "
            "Rev B set"
        )
    general = job.get("GeneralSpecs")
    if not isinstance(general, dict):
        raise FabExportError(
            "Gerber job GeneralSpecs must be an object"
        )
    size = general.get("Size")
    numeric_specs = (
        isinstance(size, dict)
        and set(size) == {"X", "Y"}
        and all(
            not isinstance(size.get(axis), bool)
            and isinstance(size.get(axis), (int, float))
            and math.isfinite(float(size[axis]))
            and math.isclose(
                float(size[axis]),
                REQUIRED_GENERAL_SPECS["Size"][axis],
                abs_tol=1e-9,
            )
            for axis in ("X", "Y")
        )
        and not isinstance(general.get("LayerNumber"), bool)
        and isinstance(general.get("LayerNumber"), int)
        and general["LayerNumber"] == REQUIRED_GENERAL_SPECS["LayerNumber"]
        and not isinstance(general.get("BoardThickness"), bool)
        and isinstance(general.get("BoardThickness"), (int, float))
        and math.isfinite(float(general["BoardThickness"]))
        and math.isclose(
            float(general["BoardThickness"]),
            REQUIRED_GENERAL_SPECS["BoardThickness"],
            abs_tol=1e-9,
        )
    )
    if (
        not numeric_specs
        or general.get("Finish") != REQUIRED_GENERAL_SPECS["Finish"]
        or general.get("ImpedanceControlled")
        is not REQUIRED_GENERAL_SPECS["ImpedanceControlled"]
    ):
        raise FabExportError(
            "Gerber job GeneralSpecs differ from the locked Rev B "
            "size, layer count, thickness, finish, or impedance setting"
        )
    if require_normalized:
        try:
            job_creation_date = job["Header"]["CreationDate"]
        except (KeyError, TypeError) as error:
            raise FabExportError(
                "Gerber job lacks a normalized CreationDate"
            ) from error
        if job_creation_date != NORMALIZED_ISO_DATE:
            raise FabExportError(
                "Gerber job CreationDate is not normalized"
            )


def write_deterministic_archive(directory: Path, destination: Path) -> None:
    """Write sorted members with fixed ZIP metadata."""
    validate_stage(directory, require_normalized=True)
    payload = io.BytesIO()
    with zipfile.ZipFile(
        payload,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for filename in sorted(EXPECTED_FAB_FILES):
            info = zipfile.ZipInfo(filename=filename, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                (directory / filename).read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload.getvalue())


def _validated_archive_payload(directory: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="esp32tap-zip-") as temporary:
        archive = Path(temporary) / "Esp32Tap-gerbers.zip"
        write_deterministic_archive(directory, archive)
        payload = archive.read_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            if set(zipped.namelist()) != EXPECTED_FAB_FILES:
                raise FabExportError("deterministic ZIP member set changed")
            for filename in EXPECTED_FAB_FILES:
                if zipped.read(filename) != (directory / filename).read_bytes():
                    raise FabExportError(f"deterministic ZIP payload differs: {filename}")
        return payload


def publish_stage(
    stage: Path,
    destination: Path,
    archive: Path,
) -> None:
    """Atomically swap validated artifacts into their checked-in locations."""
    validate_stage(stage, require_normalized=True)
    archive_payload = _validated_archive_payload(stage)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)

    publish_root = Path(
        tempfile.mkdtemp(
            prefix=".esp32tap-fab-publish-",
            dir=destination.parent,
        )
    )
    staged_directory = publish_root / destination.name
    staged_archive = publish_root / archive.name
    backup_directory = publish_root / "previous-gerbers"
    backup_archive = publish_root / "previous-gerbers.zip"
    retain_recovery = False

    def remove_path(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    try:
        shutil.copytree(stage, staged_directory)
        staged_archive.write_bytes(archive_payload)
        validate_stage(staged_directory, require_normalized=True)
        try:
            if destination.exists():
                os.replace(destination, backup_directory)
            if archive.exists():
                os.replace(archive, backup_archive)
            os.replace(staged_directory, destination)
            os.replace(staged_archive, archive)
        except BaseException as publish_error:
            rollback_errors: list[str] = []
            try:
                if backup_directory.exists():
                    remove_path(destination)
                    os.replace(backup_directory, destination)
                elif not staged_directory.exists():
                    remove_path(destination)
            except OSError as error:
                rollback_errors.append(f"directory: {error}")
            try:
                if backup_archive.exists():
                    remove_path(archive)
                    os.replace(backup_archive, archive)
                elif not staged_archive.exists():
                    remove_path(archive)
            except OSError as error:
                rollback_errors.append(f"archive: {error}")
            if rollback_errors:
                retain_recovery = True
                raise FabExportError(
                    "fabrication publish failed and rollback was incomplete; "
                    f"recovery retained at {publish_root}: "
                    + "; ".join(rollback_errors)
                ) from publish_error
            raise
        if backup_directory.exists():
            shutil.rmtree(backup_directory)
        if backup_archive.exists():
            backup_archive.unlink()
    except (FabExportError, OSError, zipfile.BadZipFile) as error:
        if isinstance(error, FabExportError):
            raise
        raise FabExportError(f"cannot publish fabrication package: {error}") from error
    finally:
        if publish_root.exists() and not retain_recovery:
            shutil.rmtree(publish_root)


def export_to_stage(board: Path, stage: Path, *, kicad_cli: str) -> None:
    """Ask KiCad to generate a fresh package in an empty directory."""
    if not board.is_file():
        raise FabExportError(f"board source does not exist: {board}")
    stage.mkdir(parents=True, exist_ok=False)
    with isolated_kicad_project(board) as isolated_board:
        run_kicad(
            [
                kicad_cli,
                "pcb",
                "export",
                "gerbers",
                "--output",
                str(stage),
                "--layers",
                ",".join(LAYERS),
                "--precision",
                "6",
                "--check-zones",
                str(isolated_board),
            ],
            "KiCad Gerber export",
            cwd=isolated_board.parent,
        )
        run_kicad(
            [
                kicad_cli,
                "pcb",
                "export",
                "drill",
                "--output",
                str(stage),
                "--format",
                "excellon",
                "--drill-origin",
                "absolute",
                "--excellon-zeros-format",
                "decimal",
                "--excellon-oval-format",
                "route",
                "--excellon-units",
                "mm",
                str(isolated_board),
            ],
            "KiCad drill export",
            cwd=isolated_board.parent,
        )
    validate_stage(stage)
    normalize_stage(stage)
    validate_stage(stage, require_normalized=True)


def validate_publish_paths(
    board: Path,
    destination: Path,
    archive: Path,
) -> None:
    """Confine destructive publication to fixed siblings of the board."""
    board = Path(os.path.abspath(board))
    destination = Path(os.path.abspath(destination))
    archive = Path(os.path.abspath(archive))
    if board.is_symlink() or board.name != "Esp32Tap.kicad_pcb":
        raise FabExportError(
            "board must be the non-symlink Esp32Tap.kicad_pcb source"
        )
    if not board.is_file():
        raise FabExportError(f"board source does not exist: {board}")
    kicad_directory = board.parent.resolve()
    if board.resolve() != kicad_directory / "Esp32Tap.kicad_pcb":
        raise FabExportError("board source resolves outside its KiCad directory")

    if (
        destination.name != "gerbers"
        or destination.parent.resolve() != kicad_directory
    ):
        raise FabExportError(
            "output directory must be the board sibling kicad/gerbers"
        )
    if destination.is_symlink():
        raise FabExportError("output directory cannot be a symlink")
    if destination.exists() and not destination.is_dir():
        raise FabExportError("output directory exists but is not a directory")

    if (
        archive.name != "Esp32Tap-gerbers.zip"
        or archive.parent.resolve() != kicad_directory
    ):
        raise FabExportError(
            "archive must be the board sibling Esp32Tap-gerbers.zip"
        )
    if archive.is_symlink():
        raise FabExportError("archive cannot be a symlink")
    if archive.exists():
        metadata = archive.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise FabExportError("archive exists but is not a regular file")
        if metadata.st_nlink != 1:
            raise FabExportError("archive cannot be a hardlink alias")


def export_fab(
    *,
    board: Path = BOARD,
    destination: Path = GERBER_DIR,
    archive: Path = ARCHIVE,
    kicad_cli: str = "kicad-cli",
) -> None:
    """Generate, validate, normalize, and publish one fabrication package."""
    validate_publish_paths(board, destination, archive)
    audit_assembly(board.parent.parent)
    with tempfile.TemporaryDirectory(
        prefix=".esp32tap-fab-",
        dir=destination.parent,
    ) as temporary:
        stage = Path(temporary) / "gerbers"
        export_to_stage(board, stage, kicad_cli=kicad_cli)
        publish_stage(stage, destination, archive)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=BOARD)
    parser.add_argument("--output-dir", type=Path, default=GERBER_DIR)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--kicad-cli", default="kicad-cli")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="validate the checked-in Gerber directory and deterministic ZIP",
    )
    return parser


def _validate_checked_in(
    directory: Path,
    archive: Path,
    root: Path,
) -> None:
    validate_stage(directory, require_normalized=True)
    expected = _validated_archive_payload(directory)
    try:
        actual = archive.read_bytes()
    except OSError as error:
        raise FabExportError(f"cannot read fabrication archive: {error}") from error
    if actual != expected:
        raise FabExportError(
            "checked-in fabrication archive is not the deterministic package"
        )
    audit_assembly(root)


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.audit_only:
            _validate_checked_in(
                args.output_dir,
                args.archive,
                args.board.parent.parent,
            )
            print(
                f"PASS: exact {len(EXPECTED_FAB_FILES)}-member "
                "Rev B fabrication package and assembly parity"
            )
            return 0
        export_fab(
            board=args.board,
            destination=args.output_dir,
            archive=args.archive,
            kicad_cli=args.kicad_cli,
        )
        print(
            f"WROTE {args.output_dir} and {args.archive} "
            f"({len(EXPECTED_FAB_FILES)} deterministic members)"
        )
        return 0
    except FabExportError as error:
        print(f"export_fab: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
