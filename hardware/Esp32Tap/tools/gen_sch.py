#!/usr/bin/env python3
"""Generate kicad/Esp32Tap.kicad_sch from design.py.

Layout: one generated box symbol per component, all pins on the left edge at
2.54mm pitch, a global net label (or no-connect marker) on every pin.  The
schematic is electrically exact; NETLIST.md remains the human-readable source
of truth for review.
"""
import uuid

import design

design.validate()

ROOT = uuid.uuid4()


def u():
    return str(uuid.uuid4())


def esc(s):
    return s.replace('"', "'")


# ---------------------------------------------------------------- symbols
def sym_def(name, ref_prefix, pins):
    """Box symbol, pins stacked on the left edge, pin 'at' = connection point."""
    n = len(pins)
    h = (n + 1) * 2.54
    half = h / 2
    body_w = 20.32
    out = []
    out.append(f'    (symbol "esp32tap:{name}"')
    out.append("      (pin_names (offset 1.016)) (exclude_from_sim no)" " (in_bom yes) (on_board yes)")
    out.append(
        f'      (property "Reference" "{ref_prefix}" (at {body_w/2:.2f} '
        f"{half + 1.27:.2f} 0) (effects (font (size 1.27 1.27))))"
    )
    out.append(
        f'      (property "Value" "{esc(name)}" (at {body_w/2:.2f} '
        f"{-half - 1.27:.2f} 0) (effects (font (size 1.27 1.27))))"
    )
    out.append('      (property "Footprint" "" (at 0 0 0)' " (effects (font (size 1.27 1.27)) (hide yes)))")
    out.append('      (property "Datasheet" "" (at 0 0 0)' " (effects (font (size 1.27 1.27)) (hide yes)))")
    out.append(f'      (symbol "{name}_0_1"')
    out.append(
        f"        (rectangle (start 0 {half:.2f}) (end {body_w:.2f} "
        f"{-half:.2f}) (stroke (width 0.254) (type default))"
        " (fill (type background)))"
    )
    out.append("      )")
    out.append(f'      (symbol "{name}_1_1"')
    for i, (num, pname) in enumerate(pins):
        y = half - (i + 1) * 2.54
        out.append(f"        (pin passive line (at -2.54 {y:.2f} 0) (length 2.54)")
        out.append(f'          (name "{esc(pname)}" (effects (font (size 1.27 1.27))))')
        out.append(f'          (number "{num}" (effects (font (size 1.27 1.27)))))')
    out.append("      )")
    out.append("    )")
    return "\n".join(out)


def ref_prefix(ref):
    i = 0
    while i < len(ref) and not ref[i].isdigit():
        i += 1
    return ref[:i] or ref


# pin -> net / nc lookup
pin_net = {}
for net, pads in design.NETS.items():
    for rp in pads:
        pin_net[rp] = net
nc_set = set(design.NC)

# ------------------------------------------------------------- placement
# Columns of symbols across an A2 sheet (594 x 420 mm usable).
order = list(design.COMPONENTS.keys())
placements = {}  # ref -> (x, y) of symbol body-left-edge anchor
col_x = 40.0
y_cursor = 30.0
COL_W = 75.0
SHEET_H = 400.0
def snap(v, g=2.54):
    return round(v / g) * g

for ref in order:
    pins = design.COMPONENTS[ref][7]
    h = (len(pins) + 1) * 2.54
    if y_cursor + h + 20 > SHEET_H:
        col_x += COL_W
        y_cursor = 30.0
    top = snap(y_cursor)          # pin i sits at top + (i+1)*2.54  (on grid)
    placements[ref] = (snap(col_x), top)
    y_cursor = top + h + 22.0

body = []
lib_syms = []
seen_syms = set()

for ref in order:
    val, flib, fname, lcsc, jclass, cost, desc, pinmap = design.COMPONENTS[ref]
    pins = sorted(pinmap.items(), key=lambda kv: ((0, int(kv[0])) if kv[0].isdigit() else (1, kv[0])))
    sname = f"SYM_{ref}"
    lib_syms.append(sym_def(sname, ref_prefix(ref), pins))
    x, top = placements[ref]
    n = len(pins)
    half = (n + 1) * 2.54 / 2
    ycen = top + half
    body.append(f'  (symbol (lib_id "esp32tap:{sname}") (at {x:.2f} {ycen:.2f} 0)' " (unit 1)")
    body.append("    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)")
    body.append(f'    (uuid "{u()}")')
    body.append(
        f'    (property "Reference" "{ref}" (at {x+2:.2f} '
        f"{ycen-half-3:.2f} 0) (effects (font (size 1.27 1.27))"
        " (justify left)))"
    )
    body.append(
        f'    (property "Value" "{esc(val)}" (at {x+2:.2f} '
        f"{ycen+half+3:.2f} 0) (effects (font (size 1.27 1.27))"
        " (justify left)))"
    )
    body.append(
        f'    (property "Footprint" "{flib}:{fname}" (at {x:.2f} '
        f"{ycen:.2f} 0) (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    body.append(
        f'    (property "Datasheet" "" (at {x:.2f} {ycen:.2f} 0)' " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    body.append(
        f'    (property "LCSC" "{lcsc}" (at {x:.2f} {ycen:.2f} 0)' " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    for num, _pn in pins:
        body.append(f'    (pin "{num}" (uuid "{u()}"))')
    body.append(f'    (instances (project "Esp32Tap" (path "/{ROOT}"' f' (reference "{ref}") (unit 1))))')
    body.append("  )")

    # labels / no-connects at each pin connection point
    for i, (num, _pn) in enumerate(pins):
        # symbol coords: pin at (-2.54, half - (i+1)*2.54); instance y is
        # inverted:  abs = (x + px, ycen - py)
        ax, ay = x - 2.54, top + (i + 1) * 2.54
        key = (ref, num)
        if key in nc_set:
            body.append(f'  (no_connect (at {ax:.2f} {ay:.2f}) (uuid "{u()}"))')
        else:
            net = pin_net[key]
            body.append(
                f'  (global_label "{net}" (shape passive) (at {ax:.2f} '
                f"{ay:.2f} 180) (fields_autoplaced yes)"
                " (effects (font (size 1.27 1.27))"
                f' (justify right)) (uuid "{u()}"))'
            )

sch = []
sch.append('(kicad_sch (version 20231120) (generator "esp32tap_gen")')
sch.append(f'  (uuid "{ROOT}")')
sch.append('  (paper "A2")')
sch.append(
    '  (title_block (title "Esp32Tap - ESP32-S3 Precor serial-bus tap")'
    ' (date "2026-07-23") (rev "A")'
    ' (company "precor-9.3x")'
    ' (comment 1 "Netlist source of truth: ../NETLIST.md")'
    ' (comment 2 "Generated by tools/gen_sch.py from tools/design.py"))'
)
sch.append("  (lib_symbols")
sch.extend(lib_syms)
sch.append("  )")
sch.extend(body)
sch.append('  (sheet_instances (path "/" (page "1")))')
sch.append(")")

kdir = "/home/ssilver/development/precor-9.3x/.claude/worktrees/wf_4b2fe7a1-b29-6/hardware/Esp32Tap/kicad"
lib = ['(kicad_symbol_lib (version 20231120) (generator "esp32tap_gen")']
for s in lib_syms:
    lib.append(s.replace('(symbol "esp32tap:', '(symbol "'))
lib.append(')')
with open(kdir + "/esp32tap.kicad_sym", "w") as f:
    f.write("\n".join(lib) + "\n")
with open(kdir + "/sym-lib-table", "w") as f:
    f.write('(sym_lib_table\n  (version 7)\n'
            '  (lib (name "esp32tap")(type "KiCad")'
            '(uri "${KIPRJMOD}/esp32tap.kicad_sym")(options "")(descr "generated"))\n)\n')
import json, os
pro = kdir + "/Esp32Tap.kicad_pro"
if not os.path.exists(pro):
    with open(pro, "w") as f:
        json.dump({"meta": {"filename": "Esp32Tap.kicad_pro", "version": 3},
                   "sheets": [], "boards": []}, f, indent=2)

out = "/home/ssilver/development/precor-9.3x/.claude/worktrees/wf_4b2fe7a1-b29-6/hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch"
with open(out, "w") as f:
    f.write("\n".join(sch) + "\n")
print("wrote", out)
