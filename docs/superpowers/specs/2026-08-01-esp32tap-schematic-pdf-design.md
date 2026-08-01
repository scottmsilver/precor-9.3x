# Esp32Tap Schematic PDF Export Design

## Goal

Provide a printable, searchable PDF of the Esp32Tap Rev E electrical schematic for bench bring-up.

## Source and output

- Source: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch`
- Output: `hardware/Esp32Tap/bringup/esp32tap-schematic.pdf`
- Exporter: the installed `kicad-cli`, using its schematic PDF export command

The KiCad file remains the source of truth. The export will not redraw, simplify, annotate, or otherwise reinterpret the circuit.

## Verification

The generated artifact must:

- be recognized as a valid PDF;
- contain the complete schematic sheet and its Rev E title block;
- retain searchable text;
- render legibly in a visual preview; and
- introduce no source-schematic changes.

