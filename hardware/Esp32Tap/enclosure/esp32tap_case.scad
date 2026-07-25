// Esp32Tap two-part enclosure — parametric, sized to the Rev D board.
// Coordinates follow the PCB: origin = board top-left corner, +X right,
// +Y toward the bottom edge (the PCB "top edge" carries the antenna
// overhang).  Board-position parameters below are taken directly from
// kicad/Esp32Tap.kicad_pcb (see DIMENSIONS.md for the full table).
//
// Print: JLC3DP SLA resin (LEDO 6060 / 8001) or MJF PA12.  PLASTIC ONLY —
// no conductive finish (2.4 GHz antenna inside).  Orderable as two STLs:
//   openscad -D part=\"base\" -o esp32tap_base.stl esp32tap_case.scad
//   openscad -D part=\"lid\"  -o esp32tap_lid.stl  esp32tap_case.scad
// Checked-in STLs are rendered with the immutable OpenSCAD image documented
// in DIMENSIONS.md and validated by validate_enclosure.py.

part = "both";          // "base" | "lid" | "both" (preview)

/* ------------------------- board parameters (from the PCB) ----------- */
board_l      = 95.0;    // X
board_w      = 58.0;    // Y
board_t      = 1.6;
ant_overhang = -3.3;    // physical antenna edge is 3.3 mm inside board Y=0
ant_x0       = 69.0;    // physical U1 F.Fab antenna span in board X
ant_x1       = 87.0;
ant_air_gap  = 15.0;    // plastic/air void from antenna edge to inner wall

// Rev D: J1/J2 are both the Molex 441440003 right-angle SMD RJ45 jack
// (LCSC C585890) — the exact footprint anchors from inspect_kicad.  Unlike
// Rev C's two different Micro-Fit part numbers, J1 and J2 are now the
// identical part, edge-mounted with the mating opening facing off the
// board's X=0 edge (rotation places the jack's local -Y, the pin/opening
// side, onto world -X — see gen_pcb.py PLACE).  There is no mechanical
// keying between console and motor any more: both apertures are
// identical, standard 8P8C openings.  CONSOLE/MOTOR silkscreen is the
// only differentiator; a mis-plugged cable is a labeling/procedure risk
// now, not something this CAD rejects.
j1_yc = 18.0;
j2_yc = 40.0;
// Fabrication-body bbox from inspect_kicad (identical for J1 and J2 —
// same part): Y width (across the row of 8 pins) and X depth (pin row to
// the rear mechanical-tab cap).
rj45_body_w     = 15.48;
rj45_body_depth = 17.17;
rj45_body_h     = 13.4;  // jack shell height above the board (datasheet)
// Panel aperture: clears the mating 8P8C plug shell plus finger
// clearance for the latch tab — not the full jack body, which sits
// inside the case, recessed a few mm behind the wall.
aperture_w = 16.0;
aperture_h = 14.0;
latch_clearance = 6.0;          // straight insertion/extraction depth
cable_bend_radius = 18.0;       // external cable bend service envelope
cable_exit_direction = -1.0;    // both jacks open outward through X-min

// USB-C on the X=board_l wall.  The receptacle face sits at the board edge,
// 4.5 mm behind the exterior wall face (2.5 wall + 2.0 clearance), so the
// wall aperture must pass the CABLE OVERMOLD, not just the plug shell —
// otherwise no standard cable can mate.  13 x 8 covers typical overmolds
// (<=12 x 6.5) and doubles as the plug-alignment funnel.
usb_yc = 39.5;
usb_w = 9.4;
usb_h = 3.4;
usb_om_w = 13.0;
usb_om_h = 8.0;   // overmold aperture through the wall

// M2.5 board mounting holes (X, Y)
mh = [[20.0, 6.0], [48.0, 6.0], [92.0, 55.0]];

// LED light pipes + button access (X, Y) in board coords
led1 = [79.0, 12.97];   // status (green)
led2 = [32.5, 44.5];    // power (red)
sw1  = [42.0, 7.0];     // EN / reset
sw2  = [91.0, 20.0];    // BOOT

/* ------------------------- enclosure parameters ---------------------- */
wall      = 2.5;                     // 2.2→2.5: clears JLC3DP resin thin-wall
                                     // DFM margin (min ~1.2, but flagged near
                                     // thin features; 2.5 is comfortably above)
clr       = 2.0;                     // side clearance board->wall (X=0 side
                                     // effectively used by jack overhang)
bot_clr   = 9.0;                     // bottom-edge (Y=board_w side) clearance:
                                     // widened so the two OD7 lid screw posts
                                     // clear the PCB's bottom corners (board
                                     // edge to post edge = 2.25 mm)
standoff  = 3.0;                     // under-board space (THT pins ~2 mm)
headroom  = 16.5;                    // above-board space; RJ45 = 13.4 tall,
                                     // leaves 1.9 mm above the jacks under
                                     // the 1.2 mm lid registration lip
lid_t     = 3.0;                     // 2.2→3.0: the lid is a large flat plate;
                                     // 3.0 resists SLA-resin warp (JLC flagged
                                     // 2.5 as thin/deformation-risk)
lip       = 1.2;                     // 1.8→1.2: shorter lip = less of a thin
                                     // standing ring (JLC flagged the tall ring
                                     // as thin-wall); still registers the lid
lip_w     = 4.0;                     // 2.0→4.0: much wider band, no longer a
                                     // thin freestanding wall
post_d = 7.0;
post_wall_overlap = 0.25;
post_inset = post_d / 2 - post_wall_overlap;
snap_clearance = 0.3;

int_l = board_l + 2*clr;                              // interior X
int_w = board_w + bot_clr + ant_overhang + ant_air_gap; // interior Y
int_h = standoff + board_t + headroom;

out_l = int_l + 2*wall;
out_w = int_w + 2*wall;
base_h = wall + int_h;               // base floor + cavity (lid closes flush)

// board origin inside the enclosure (interior coords)
bx0 = clr;                            // X of board corner
by0 = ant_overhang + ant_air_gap;     // Y of board top edge (antenna void above)
bz0 = wall + standoff;                // Z of board underside

// Lid-post disks overlap the inner wall face by post_wall_overlap.  The
// former wall+post_d/2 centers made the solids exactly tangent and yielded a
// non-manifold base after tessellation.
posts = [[wall+post_inset, wall+post_inset],
         [out_l-wall-post_inset, wall+post_inset],
         [wall+post_inset, out_w-wall-post_inset],
         [out_l-wall-post_inset, out_w-wall-post_inset]];

$fn = 32;

module rrect(l, w, h, r) {
  hull() for (x=[r, l-r], y=[r, w-r]) translate([x, y, 0]) cylinder(h=h, r=r);
}

// Straight, unkeyed 8P8C wall aperture: clears the mating plug shell plus
// finger clearance for the latch tab, tunneling from the exterior face
// through latch_clearance past the board edge so a plug can seat against
// the board-mounted jack (recessed rj45_body_depth-ish behind the wall)
// and still be pinched/extracted without touching the aperture wall.
module rj45_wall_aperture(yc) {
  aperture_z = bz0 + board_t - 0.3;
  translate([-1, wall + by0 + yc - aperture_w/2, aperture_z])
    cube([wall + bx0 + latch_clearance + 2, aperture_w, aperture_h]);
}

// Model-only keep-clear volume: the aperture cross-section extended
// outward for the external cable bend radius and inward through
// latch_clearance.  Preview-only (excluded from base/lid STLs); confirms
// the wall opening doesn't require plastic in the plug's seat/extraction
// sweep.
module rj45_plug_service_envelope(yc) {
  translate([-cable_bend_radius,
             wall + by0 + yc - aperture_w/2,
             bz0 + board_t - 0.3])
    cube([cable_bend_radius + wall + bx0 + latch_clearance,
          aperture_w, aperture_h]);
}

module snap_latch(xc, far_side=false) {
  yc = far_side ? out_w - wall : 0;
  translate([xc - 4, yc - (far_side ? 0 : 1.2), base_h - 3])
    cube([8, 1.2, 3]);
}

/* ------------------------- base ------------------------------------- */
module base() {
  difference() {
    union() {
      rrect(out_l, out_w, base_h, 3);
      // zip-tie ears (mount near the treadmill lower board)
      for (yc = [out_w*0.25, out_w*0.75])
        translate([-6, yc-6, 0]) ear();
      for (yc = [out_w*0.25, out_w*0.75])
        translate([out_l, yc-6, 0]) ear();
    }
    // main cavity
    translate([wall, wall, wall]) rrect(int_l, int_w, int_h + 1, 2);

    // RJ45 wall apertures (both jacks are the identical part; unkeyed).
    rj45_wall_aperture(j1_yc);
    rj45_wall_aperture(j2_yc);

    // USB-C aperture, X = out_l wall — overmold-sized (see parameter note):
    // the receptacle is recessed 4.5 mm behind the exterior face, so the
    // opening passes the cable overmold all the way to the board edge.
    translate([out_l - wall - clr - 1,
               wall + by0 + usb_yc - usb_om_w/2,
               bz0 + board_t + usb_h/2 - usb_om_h/2])
      cube([wall + clr + 2, usb_om_w, usb_om_h]);

    // side vents (both long walls, away from the antenna end)
    for (i = [0:4]) {
      translate([wall + 30 + i*9, -1, base_h - 8])
        cube([4, wall + 2, 6]);
      translate([wall + 30 + i*9, out_w - wall - 1, base_h - 8])
        cube([4, wall + 2, 6]);
    }
  }
  // Two optional tool-less closure latches; supplied M3 screws remain usable.
  snap_latch(out_l/2, false);
  snap_latch(out_l/2, true);
  // board posts (M2.5 self-tap, 2.0 mm pilot)
  for (p = mh)
    translate([wall + bx0 + p[0], wall + by0 + p[1], wall])
      difference() {
        cylinder(h = standoff, d = 6.0);
        cylinder(h = standoff + 1, d = 2.0);
      }
  // board edge ledges (support the unsupported corners)
  for (p = [[bx0 + 50, by0 - 0.1], [bx0 + 20, by0 + board_w - 1.9],
            [bx0 + 70, by0 + board_w - 1.9]])
    translate([wall + p[0], wall + p[1], wall]) cube([8, 2, standoff]);
  // lid screw posts, M3 self-tap (2.5 mm pilot), inside the 4 corners
  // (bottom pair clears the PCB bottom edge by 2.25 mm thanks to bot_clr)
  for (p = posts)
    translate([p[0], p[1], wall])
      difference() {
        cylinder(h = int_h, d = post_d);
        translate([0, 0, int_h - 10]) cylinder(h = 11, d = 2.5);
      }
}

module ear() {
  difference() {
    cube([8, 12, 4]);                             // 6→8 wide: 2.5 mm walls
    translate([2.5, 3.5, -1]) cube([3, 5, 6]);    // zip-tie / #6 screw slot
  }
}

/* ------------------------- lid -------------------------------------- */
module lid() {
  difference() {
    union() {
      rrect(out_l, out_w, lid_t, 3);
      // registration lip: a PERIMETER RING (not a solid slab), so it never
      // lands on the screw posts or the 13.4 mm RJ45 bodies — the interior
      // stays open above the components.
      translate([wall + 0.15, wall + 0.15, lid_t])
        difference() {
          rrect(int_l - 0.3, int_w - 0.3, lip, 2);
          translate([lip_w, lip_w, -1])
            rrect(int_l - 0.3 - 2*lip_w, int_w - 0.3 - 2*lip_w, lip + 2, 2);
          // clear the four screw posts (0.3 mm radial clearance)
          for (p = posts)
            translate([p[0] - (wall + 0.15), p[1] - (wall + 0.15), -1])
              cylinder(h = lip + 2, d = post_d + 0.6);
        }
    }
    // M3 clearance holes + countersink over the 4 posts
    for (p = posts) {
      translate([p[0], p[1], -1]) cylinder(h = lid_t + lip + 2, d = 3.4);
      translate([p[0], p[1], -0.01]) cylinder(h = 1.8, d1 = 6.4, d2 = 3.4);
    }
    // Tool-less latch receiver slots (0.3 mm modeled clearance).
    for (yc = [wall - snap_clearance, out_w - wall + snap_clearance])
      translate([out_l/2 - 4 - snap_clearance, yc - 0.7, -1])
        cube([8 + 2*snap_clearance, 1.4, lid_t + lip + 2]);
    // light pipes (3.2 mm — press-fit pipe or leave open)
    for (p = [led1, led2])
      translate([wall + bx0 + p[0], wall + by0 + p[1], -1])
        cylinder(h = lid_t + lip + 2, d = 3.2);
    // recessed button access (2.5 mm tool holes)
    for (p = [sw1, sw2])
      translate([wall + bx0 + p[0], wall + by0 + p[1], -1])
        cylinder(h = lid_t + lip + 2, d = 2.5);
    // lid vents over the buck area
    for (i = [0:3])
      translate([wall + bx0 + 40 + i*7, wall + by0 + 48, -1])
        cube([4, 3, lid_t + lip + 2]);
    // (Antenna lid-thinning removed: it left a 1.4 mm-thin patch that tripped
    // JLC3DP's thin-wall DFM, and it served no purpose — resin is already
    // RF-transparent at 2.4 GHz and the 15 mm air gap is set by by0, not the
    // lid thickness.  The lid stays full thickness over the antenna.)
  }
}

/* ------------------------- output ----------------------------------- */
if (part == "base") base();
else if (part == "lid") lid();
else {
  base();
  translate([0, out_w + 12, 0]) lid();
  // Transparent preview-only service volumes; excluded from base/lid STLs.
  %rj45_plug_service_envelope(j1_yc);
  %rj45_plug_service_envelope(j2_yc);
}
