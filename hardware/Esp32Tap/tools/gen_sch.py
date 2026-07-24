#!/usr/bin/env python3
"""Generate the typed Rev C KiCad schematic from ``design.py``.

Each physical component is represented by a generated box symbol.  A global
label (or explicit no-connect marker) is placed directly on every pin, making
the generated connectivity a literal rendering of ``design.NETS`` and
``design.NC``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import design


PROJECT = "Esp32Tap"
SCHEMATIC_TITLE = "Esp32Tap Rev C - ESP32-S3 Precor serial-bus tap"
SCHEMATIC_DATE = "2026-07-24"
UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/ssilverman/precor-9.3x/hardware/Esp32Tap/rev-c",
)
FOOTPRINT_TABLE = """(fp_lib_table
  (version 7)
  (lib (name "Connector_Molex")(type "KiCad")(uri "${KIPRJMOD}/Connector_Molex.pretty")(options "")(descr "Esp32Tap qualified Molex footprints"))
  (lib (name "Button_Switch_SMD")(type "KiCad")(uri "${KIPRJMOD}/Button_Switch_SMD.pretty")(options "")(descr "Esp32Tap qualified switch footprints"))
)
"""
VALIDATION_FOOTPRINTS = {
    "Connector_Molex": (
        "Molex_Micro-Fit_3.0_43045-0809_2x04-1MP_P3.00mm_Horizontal",
        "Molex_Micro-Fit_3.0_43045-1010_2x05-1MP_P3.00mm_Horizontal",
    ),
    "Button_Switch_SMD": ("SW_SPST_SKRPACE010",),
}
PIN_TYPE_TOKENS = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri_state": "tri_state",
    "passive": "passive",
    "power_in": "power_in",
    "power_out": "power_out",
    "open_collector": "open_collector",
    "no_connect": "no_connect",
}
POWER_FLAGS = (
    ("#FLG01", "GND"),
    ("#FLG02", "VIN"),
    ("#FLG03", "+3V3"),
    ("#FLG04", "VBUS"),
)
UUID_PATTERN = re.compile(r'\(uuid "([^"]+)"\)')


def stable_uuid(*identity: object) -> str:
    """Return a project-stable UUID for one generated schematic identity."""
    encoded_identity = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(UUID_NAMESPACE, encoded_identity))


def included_in_bom(reference: str, component: tuple[object, ...]) -> bool:
    """Use the design assembly metadata for schematic BOM membership."""
    return reference not in design.DNP and component[4] != "none"


def validate_uuid_uniqueness(schematic: str) -> None:
    """Reject invalid or duplicate object UUIDs before writing anything."""
    seen = set()
    duplicates = set()
    for value in UUID_PATTERN.findall(schematic):
        uuid.UUID(value)
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(
            "duplicate schematic UUID(s): "
            + ", ".join(sorted(duplicates))
        )


def escape(value: object) -> str:
    """Escape a value for a quoted KiCad S-expression string."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
    )


def pin_type_token(reference: str, pad: str) -> str:
    """Translate a design pin type to the corresponding KiCad token."""
    design_type = design.PIN_TYPES[(reference, pad)]
    try:
        return PIN_TYPE_TOKENS[design_type]
    except KeyError as error:
        raise ValueError(
            f"unsupported KiCad pin type for {reference}.{pad}: "
            f"{design_type!r}"
        ) from error


def sorted_pins(pinmap: dict[str, str]) -> list[tuple[str, str]]:
    """Sort numeric pads numerically and named pads lexically."""

    def key(item: tuple[str, str]) -> tuple[int, int | str]:
        pad = item[0]
        if pad.isdigit():
            return 0, int(pad)
        return 1, pad

    return sorted(pinmap.items(), key=key)


def reference_prefix(reference: str) -> str:
    index = 0
    while index < len(reference) and not reference[index].isdigit():
        index += 1
    return reference[:index] or reference


def symbol_definition(
    symbol_name: str,
    reference: str,
    pins: list[tuple[str, str]],
    in_bom: bool,
) -> str:
    """Render one typed generated symbol definition."""
    height = (len(pins) + 1) * 2.54
    half_height = height / 2
    body_width = 20.32
    output = [
        f'    (symbol "esp32tap:{symbol_name}"',
        "      (pin_names (offset 1.016))"
        f" (exclude_from_sim no) (in_bom "
        f"{'yes' if in_bom else 'no'}) (on_board yes)",
        (
            f'      (property "Reference" "{reference_prefix(reference)}" '
            f"(at {body_width / 2:.2f} {half_height + 1.27:.2f} 0) "
            "(effects (font (size 1.27 1.27))))"
        ),
        (
            f'      (property "Value" "{escape(symbol_name)}" '
            f"(at {body_width / 2:.2f} {-half_height - 1.27:.2f} 0) "
            "(effects (font (size 1.27 1.27))))"
        ),
        (
            '      (property "Footprint" "" (at 0 0 0) '
            "(effects (font (size 1.27 1.27)) (hide yes)))"
        ),
        (
            '      (property "Datasheet" "" (at 0 0 0) '
            "(effects (font (size 1.27 1.27)) (hide yes)))"
        ),
        f'      (symbol "{symbol_name}_0_1"',
        (
            f"        (rectangle (start 0 {half_height:.2f}) "
            f"(end {body_width:.2f} {-half_height:.2f}) "
            "(stroke (width 0.254) (type default)) "
            "(fill (type background)))"
        ),
        "      )",
        f'      (symbol "{symbol_name}_1_1"',
    ]
    for index, (pad, pin_name) in enumerate(pins):
        pin_y = half_height - (index + 1) * 2.54
        output.extend(
            [
                (
                    f"        (pin {pin_type_token(reference, pad)} line "
                    f"(at -2.54 {pin_y:.2f} 0) (length 2.54)"
                ),
                (
                    f'          (name "{escape(pin_name)}" '
                    "(effects (font (size 1.27 1.27))))"
                ),
                (
                    f'          (number "{escape(pad)}" '
                    "(effects (font (size 1.27 1.27)))))"
                ),
            ]
        )
    output.extend(["      )", "    )"])
    return "\n".join(output)


def power_flag_definition() -> str:
    """Render the schematic-only power-output helper symbol."""
    return "\n".join(
        [
            '    (symbol "esp32tap:PWR_FLAG"',
            "      (pin_names (offset 1.016))"
            " (exclude_from_sim yes) (in_bom no) (on_board no)",
            (
                '      (property "Reference" "#FLG" (at 5.08 2.54 0) '
                "(effects (font (size 1.27 1.27))))"
            ),
            (
                '      (property "Value" "PWR_FLAG" (at 5.08 -2.54 0) '
                "(effects (font (size 1.27 1.27))))"
            ),
            (
                '      (property "Footprint" "" (at 0 0 0) '
                "(effects (font (size 1.27 1.27)) (hide yes)))"
            ),
            (
                '      (property "Datasheet" "" (at 0 0 0) '
                "(effects (font (size 1.27 1.27)) (hide yes)))"
            ),
            '      (symbol "PWR_FLAG_0_1"',
            (
                "        (rectangle (start 0 1.27) (end 10.16 -1.27) "
                "(stroke (width 0.254) (type default)) "
                "(fill (type background)))"
            ),
            "      )",
            '      (symbol "PWR_FLAG_1_1"',
            (
                "        (pin power_out line (at -2.54 0 0) "
                "(length 2.54)"
            ),
            (
                '          (name "Power flag" '
                "(effects (font (size 1.27 1.27))))"
            ),
            (
                '          (number "1" '
                "(effects (font (size 1.27 1.27)))))"
            ),
            "      )",
            "    )",
        ]
    )


def snap(value: float, grid: float = 2.54) -> float:
    return round(value / grid) * grid


def component_placements() -> dict[str, tuple[float, float]]:
    """Lay symbols out deterministically in columns on an A1 sheet."""
    placements = {}
    column_x = 40.0
    y_cursor = 30.0
    column_width = 75.0
    sheet_height = 570.0

    for reference, component in design.COMPONENTS.items():
        height = (len(component[7]) + 1) * 2.54
        if y_cursor + height + 20 > sheet_height:
            column_x += column_width
            y_cursor = 30.0
        top = snap(y_cursor)
        placements[reference] = (snap(column_x), top)
        y_cursor = top + height + 22.0
    return placements


def pin_connectivity() -> tuple[
    dict[tuple[str, str], str],
    set[tuple[str, str]],
]:
    pin_net = {
        pin: net
        for net, pins in design.NETS.items()
        for pin in pins
    }
    return pin_net, set(design.NC)


def render_schematic() -> tuple[str, list[str]]:
    """Render the complete schematic and reusable library definitions."""
    root_uuid = stable_uuid("schematic", "root")
    pin_net, no_connects = pin_connectivity()
    placements = component_placements()
    body = []
    library_symbols = []

    for reference, component in design.COMPONENTS.items():
        (
            value,
            footprint_lib,
            footprint,
            lcsc,
            jlc_class,
            unit_cost,
            description,
            pinmap,
        ) = component
        pins = sorted_pins(pinmap)
        symbol_name = f"SYM_{reference}"
        assembly_fitted = included_in_bom(reference, component)
        library_symbols.append(
            symbol_definition(
                symbol_name,
                reference,
                pins,
                assembly_fitted,
            )
        )
        x_position, top = placements[reference]
        half_height = (len(pins) + 1) * 2.54 / 2
        y_center = top + half_height
        dnp = "yes" if reference in design.DNP else "no"
        body.extend(
            [
                (
                    f'  (symbol (lib_id "esp32tap:{symbol_name}") '
                    f"(at {x_position:.2f} {y_center:.2f} 0) (unit 1)"
                ),
                (
                    "    (exclude_from_sim no) "
                    f"(in_bom {'yes' if assembly_fitted else 'no'}) "
                    f"(on_board yes) (dnp {dnp})"
                ),
                (
                    f'    (uuid "{stable_uuid("component", reference)}")'
                ),
                (
                    f'    (property "Reference" "{escape(reference)}" '
                    f"(at {x_position + 2:.2f} "
                    f"{y_center - half_height - 3:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (justify left)))"
                ),
                (
                    f'    (property "Value" "{escape(value)}" '
                    f"(at {x_position + 2:.2f} "
                    f"{y_center + half_height + 3:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (justify left)))"
                ),
                (
                    f'    (property "Footprint" '
                    f'"{escape(footprint_lib)}:{escape(footprint)}" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    '    (property "Datasheet" "" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    f'    (property "LCSC" "{escape(lcsc)}" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    f'    (property "JLC Class" "{escape(jlc_class)}" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    f'    (property "Unit Cost USD" "{unit_cost:.3f}" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    f'    (property "Description" "{escape(description)}" '
                    f"(at {x_position:.2f} {y_center:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
            ]
        )
        for pad, _pin_name in pins:
            body.append(
                f'    (pin "{escape(pad)}" '
                f'(uuid "{stable_uuid("component", reference, "pin", pad)}"))'
            )
        body.extend(
            [
                (
                    f'    (instances (project "{PROJECT}" '
                    f'(path "/{root_uuid}" (reference "{reference}") '
                    "(unit 1))))"
                ),
                "  )",
            ]
        )

        for index, (pad, _pin_name) in enumerate(pins):
            pin_x = x_position - 2.54
            pin_y = top + (index + 1) * 2.54
            design_pin = (reference, pad)
            if design_pin in no_connects:
                body.append(
                    f"  (no_connect (at {pin_x:.2f} {pin_y:.2f}) "
                    f'(uuid "{stable_uuid("no-connect", reference, pad)}"))'
                )
                continue
            net = pin_net[design_pin]
            body.append(
                f'  (global_label "{escape(net)}" (shape passive) '
                f"(at {pin_x:.2f} {pin_y:.2f} 180) "
                "(fields_autoplaced yes) "
                "(effects (font (size 1.27 1.27)) (justify right)) "
                f'(uuid "{stable_uuid("label", reference, pad, net)}"))'
            )

    library_symbols.append(power_flag_definition())
    for index, (reference, net) in enumerate(POWER_FLAGS):
        x_position = snap(500.0)
        y_position = snap(40.0) + index * 10.16
        body.extend(
            [
                (
                    '  (symbol (lib_id "esp32tap:PWR_FLAG") '
                    f"(at {x_position:.2f} {y_position:.2f} 0) (unit 1)"
                ),
                (
                    "    (exclude_from_sim yes) (in_bom no) "
                    "(on_board no) (dnp no)"
                ),
                (
                    f'    (uuid "{stable_uuid("power-flag", net)}")'
                ),
                (
                    f'    (property "Reference" "{reference}" '
                    f"(at {x_position + 2.54:.2f} "
                    f"{y_position - 2.54:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (justify left)))"
                ),
                (
                    f'    (property "Value" "PWR_FLAG" '
                    f"(at {x_position + 2.54:.2f} "
                    f"{y_position + 2.54:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (justify left)))"
                ),
                (
                    '    (property "Footprint" "" '
                    f"(at {x_position:.2f} {y_position:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    '    (property "Datasheet" "" '
                    f"(at {x_position:.2f} {y_position:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    '    (property "LCSC" "" '
                    f"(at {x_position:.2f} {y_position:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    '    (property "Description" '
                    '"Schematic-only ERC declaration for a rail entering '
                    'through passive power-path components" '
                    f"(at {x_position:.2f} {y_position:.2f} 0) "
                    "(effects (font (size 1.27 1.27)) (hide yes)))"
                ),
                (
                    '    (pin "1" '
                    f'(uuid "{stable_uuid("power-flag", net, "pin", "1")}"))'
                ),
                (
                    f'    (instances (project "{PROJECT}" '
                    f'(path "/{root_uuid}" (reference "{reference}") '
                    "(unit 1))))"
                ),
                "  )",
                (
                    f'  (global_label "{escape(net)}" (shape passive) '
                    f"(at {x_position - 2.54:.2f} {y_position:.2f} 180) "
                    "(fields_autoplaced yes) "
                    "(effects (font (size 1.27 1.27)) (justify right)) "
                    f'(uuid "{stable_uuid("power-flag", net, "label")}"))'
                ),
            ]
        )

    schematic = [
        '(kicad_sch (version 20231120) (generator "esp32tap_gen")',
        f'  (uuid "{root_uuid}")',
        '  (paper "A1")',
        (
            f'  (title_block (title "{SCHEMATIC_TITLE}") '
            f'(date "{SCHEMATIC_DATE}") (rev "C") '
            '(company "precor-9.3x") '
            '(comment 1 "Status: generated Rev C typed schematic") '
            '(comment 2 "Source of truth: tools/design.py") '
            '(comment 3 "Generated by tools/gen_sch.py; do not hand-edit"))'
        ),
        "  (lib_symbols",
        *library_symbols,
        "  )",
        *body,
        '  (sheet_instances (path "/" (page "1")))',
        ")",
    ]
    rendered = "\n".join(schematic) + "\n"
    validate_uuid_uniqueness(rendered)
    return rendered, library_symbols


def render_symbol_library(library_symbols: list[str]) -> str:
    library = [
        '(kicad_symbol_lib (version 20231120) '
        '(generator "esp32tap_gen")'
    ]
    library.extend(
        symbol.replace(
            '(symbol "esp32tap:',
            '(symbol "',
            1,
        )
        for symbol in library_symbols
    )
    library.append(")")
    return "\n".join(library) + "\n"


def render_symbol_table() -> str:
    return (
        "(sym_lib_table\n"
        "  (version 7)\n"
        '  (lib (name "esp32tap")(type "KiCad")'
        '(uri "${KIPRJMOD}/esp32tap.kicad_sym")'
        '(options "")(descr "generated"))\n'
        ")\n"
    )


def render_project() -> str:
    return (
        json.dumps(
            {
                "meta": {
                    "filename": "Esp32Tap.kicad_pro",
                    "version": 3,
                },
                "sheets": [],
                "boards": [],
            },
            indent=2,
        )
        + "\n"
    )


def run_kicad_validation(command: list[str], label: str) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def validate_staged_outputs(staging_directory: Path) -> None:
    """Have KiCad parse the staged symbol library and schematic."""
    run_kicad_validation(
        [
            "kicad-cli",
            "sym",
            "upgrade",
            "--force",
            "-o",
            str(staging_directory / "validated.kicad_sym"),
            str(staging_directory / "esp32tap.kicad_sym"),
        ],
        "KiCad symbol validation",
    )
    schematic = staging_directory / "Esp32Tap.kicad_sch"
    run_kicad_validation(
        [
            "kicad-cli",
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "-o",
            str(staging_directory / "validated.xml"),
            str(schematic),
        ],
        "KiCad schematic parse",
    )
    erc_report = staging_directory / "validated-erc.rpt"
    run_kicad_validation(
        [
            "kicad-cli",
            "sch",
            "erc",
            "--severity-all",
            "-o",
            str(erc_report),
            str(schematic),
        ],
        "KiCad ERC",
    )
    report = erc_report.read_text(encoding="utf-8")
    summary = re.search(
        r"\*\* ERC messages:\s+(\d+)\s+Errors\s+"
        r"(\d+)\s+Warnings\s+(\d+)",
        report,
    )
    if summary is None or any(int(value) for value in summary.groups()):
        raise RuntimeError(f"KiCad ERC found violations\n{report}")


def write_outputs(output_directory: Path) -> None:
    """Stage and validate every output before replacing destinations."""
    schematic, library_symbols = render_schematic()
    outputs = {
        "Esp32Tap.kicad_sch": schematic,
        "esp32tap.kicad_sym": render_symbol_library(library_symbols),
        "sym-lib-table": render_symbol_table(),
        "Esp32Tap.kicad_pro": render_project(),
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".esp32tap-sch-",
        dir=output_directory,
    ) as temporary:
        staging_directory = Path(temporary)
        for filename, content in outputs.items():
            (staging_directory / filename).write_text(
                content,
                encoding="utf-8",
                newline="\n",
            )
        (staging_directory / "fp-lib-table").write_text(
            FOOTPRINT_TABLE,
            encoding="utf-8",
            newline="\n",
        )
        for library, footprints in VALIDATION_FOOTPRINTS.items():
            source = output_directory / f"{library}.pretty"
            destination = staging_directory / f"{library}.pretty"
            if source.is_dir():
                destination.symlink_to(source, target_is_directory=True)
                continue
            destination.mkdir()
            for footprint in footprints:
                (destination / f"{footprint}.kicad_mod").write_text(
                    (
                        f'(footprint "{footprint}"\n'
                        "\t(version 20240108)\n"
                        '\t(generator "esp32tap_gen")\n'
                        '\t(layer "F.Cu")\n'
                        ")\n"
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
        validate_staged_outputs(staging_directory)

        if not (output_directory / "fp-lib-table").exists():
            (output_directory / "fp-lib-table").write_text(
                FOOTPRINT_TABLE,
                encoding="utf-8",
                newline="\n",
            )
        for library, footprints in VALIDATION_FOOTPRINTS.items():
            destination = output_directory / f"{library}.pretty"
            if destination.exists():
                continue
            destination.mkdir()
            for footprint in footprints:
                (destination / f"{footprint}.kicad_mod").write_text(
                    (
                        f'(footprint "{footprint}"\n'
                        "\t(version 20240108)\n"
                        '\t(generator "esp32tap_gen")\n'
                        '\t(layer "F.Cu")\n'
                        ")\n"
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
        replacements = [
            "Esp32Tap.kicad_sch",
            "esp32tap.kicad_sym",
            "sym-lib-table",
        ]
        project_path = output_directory / "Esp32Tap.kicad_pro"
        if not project_path.exists():
            replacements.append("Esp32Tap.kicad_pro")
        for filename in replacements:
            os.replace(
                staging_directory / filename,
                output_directory / filename,
            )

    schematic_path = output_directory / "Esp32Tap.kicad_sch"
    print(f"wrote {schematic_path}")


def main() -> None:
    design.validate()
    output_directory = Path(__file__).resolve().parent.parent / "kicad"
    write_outputs(output_directory)


if __name__ == "__main__":
    main()
