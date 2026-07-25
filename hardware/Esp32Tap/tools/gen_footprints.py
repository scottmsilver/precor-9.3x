#!/usr/bin/env python3
"""Generate the project-local Rev D footprint libraries."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINNED_SOURCES = ROOT / "tools" / "footprint_sources"

RF_MODULE_FOOTPRINTS = {
    "ESP32-S3-WROOM-1": "b7f7c0eb5ecd56a08d127f464d0b0ffb5dc5e2b685bb493de1d731654e57bbd3",
}
# Rev D J1/J2: Molex 441440003 (LCSC C585890), unshielded right-angle SMD
# 8P8C RJ45, edge-mounted with the mating opening facing off the board
# edge.  Fetched with `python3 -m easyeda2kicad --full --lcsc_id=C585890`
# and re-saved through pcbnew to normalize to the current KiCad footprint
# file format and repoint its 3D model at the project-relative path
# committed under kicad/models/.  Pinned by digest like the RF footprint.
RJ45_FOOTPRINTS = {
    "RJ45-SMD_441440003": "aa1fe4ddaf8087ef440e4d2f76aa3db133c3651048a906d319bdc70c4fac92af",
}

SWITCH = """(footprint "SW_SPST_SKRPACE010"
\t(version 20240108)
\t(generator "pcbnew")
\t(generator_version "9.0")
\t(layer "F.Cu")
\t(descr "Alps Alpine SKRPACE010, official 5.2 x 2.8 mm land pattern")
\t(attr smd)
\t(fp_line
\t\t(start -1.4 -1.65)
\t\t(end 1.4 -1.65)
\t\t(stroke (width 0.16) (type default))
\t\t(layer "F.SilkS")
\t)
\t(fp_line
\t\t(start -1.4 1.65)
\t\t(end 1.4 1.65)
\t\t(stroke (width 0.16) (type default))
\t\t(layer "F.SilkS")
\t)
\t(fp_rect
\t\t(start -2.05 -1.55)
\t\t(end 2.05 1.55)
\t\t(stroke (width 0.1) (type default))
\t\t(fill none)
\t\t(layer "F.Fab")
\t)
\t(fp_rect
\t\t(start -2.8 -1.8)
\t\t(end 2.8 1.8)
\t\t(stroke (width 0.05) (type default))
\t\t(fill none)
\t\t(layer "F.CrtYd")
\t)
\t(pad "1" smd rect (at -2.075 -1.075) (size 1.05 0.65)
\t\t(layers "F.Cu" "F.Paste" "F.Mask"))
\t(pad "1" smd rect (at -2.075 1.075) (size 1.05 0.65)
\t\t(layers "F.Cu" "F.Paste" "F.Mask"))
\t(pad "2" smd rect (at 2.075 -1.075) (size 1.05 0.65)
\t\t(layers "F.Cu" "F.Paste" "F.Mask"))
\t(pad "2" smd rect (at 2.075 1.075) (size 1.05 0.65)
\t\t(layers "F.Cu" "F.Paste" "F.Mask"))
)
"""

TABLE = """(fp_lib_table
  (version 7)
  (lib (name "Button_Switch_SMD")(type "KiCad")(uri "${KIPRJMOD}/Button_Switch_SMD.pretty")(options "")(descr "Esp32Tap qualified switch footprints"))
  (lib (name "RF_Module")(type "KiCad")(uri "${KIPRJMOD}/RF_Module.pretty")(options "")(descr "Esp32Tap pinned ESP32 module footprint"))
  (lib (name "RJ45_SMD")(type "KiCad")(uri "${KIPRJMOD}/RJ45_SMD.pretty")(options "")(descr "Esp32Tap pinned RJ45 SMD jack footprint"))
)
"""


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> None:
    rj45_directory = ROOT / "kicad" / "RJ45_SMD.pretty"
    for target, expected_digest in RJ45_FOOTPRINTS.items():
        source_path = PINNED_SOURCES / f"{target}.kicad_mod"
        observed_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if observed_digest != expected_digest:
            raise ValueError(f"pinned footprint digest mismatch for {source_path.name}: " f"{observed_digest}")
        content = source_path.read_text(encoding="utf-8")
        atomic_write(rj45_directory / f"{target}.kicad_mod", content)
    rf_directory = ROOT / "kicad" / "RF_Module.pretty"
    for target, expected_digest in RF_MODULE_FOOTPRINTS.items():
        source_path = PINNED_SOURCES / f"{target}.kicad_mod"
        observed_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if observed_digest != expected_digest:
            raise ValueError(f"pinned footprint digest mismatch for {source_path.name}: " f"{observed_digest}")
        atomic_write(
            rf_directory / f"{target}.kicad_mod",
            source_path.read_text(encoding="utf-8"),
        )
    atomic_write(
        ROOT / "kicad" / "Button_Switch_SMD.pretty" / "SW_SPST_SKRPACE010.kicad_mod",
        SWITCH,
    )
    atomic_write(ROOT / "kicad" / "fp-lib-table", TABLE)
    print("wrote project-local Rev D footprint libraries")


if __name__ == "__main__":
    main()
