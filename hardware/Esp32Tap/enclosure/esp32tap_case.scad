// Esp32Tap two-part enclosure — parametric, sized to the rev A board.
// Coordinates follow the PCB: origin = board top-left corner, +X right,
// +Y toward the bottom edge (the PCB "top edge" carries the antenna
// overhang).  Board-position parameters below are taken directly from
// kicad/Esp32Tap.kicad_pcb (see DIMENSIONS.md for the full table).
//
// Print: JLC3DP SLA resin (LEDO 6060 / 8001) or MJF PA12.  PLASTIC ONLY —
// no conductive finish (2.4 GHz antenna inside).  Orderable as two STLs:
//   openscad -D part=\"base\" -o esp32tap_base.stl esp32tap_case.scad
//   openscad -D part=\"lid\"  -o esp32tap_lid.stl  esp32tap_case.scad
// (openscad CLI was unavailable in the design environment, so STLs are not
// checked in; the .scad is the deliverable, DIMENSIONS.md is the drawing.)

part = "both";          // "base" | "lid" | "both" (preview)

/* ------------------------- board parameters (from the PCB) ----------- */
board_l      = 100.0;   // X
board_w      = 55.0;    // Y
board_t      = 1.6;
ant_overhang = 6.3;     // module antenna past board Y=0 edge
ant_x0       = 52.0;    // antenna span in X
ant_x1       = 72.0;
ant_air_gap  = 3.0;     // required air to any wall at the antenna end

// RJ45 jacks on the X=0 wall (Y centers, body width 16.7, height 13.4)
j1_yc = 12.25;  j2_yc = 41.25;
rj45_w = 16.7;  rj45_h = 13.4;

// USB-C on the X=board_l wall.  The receptacle face sits at the board edge,
// ~4.2 mm behind the exterior wall face (2.2 wall + 2.0 clearance), so the
// wall aperture must pass the CABLE OVERMOLD, not just the plug shell —
// otherwise no standard cable can mate.  13 x 8 covers typical overmolds
// (<=12 x 6.5) and doubles as the plug-alignment funnel.
usb_yc = 36.5;  usb_w = 9.4;  usb_h = 3.4;
usb_om_w = 13.0;  usb_om_h = 8.0;   // overmold aperture through the wall

// M2.5 board mounting holes (X, Y)
mh = [[2.9, 26.5], [97.0, 3.0], [97.0, 52.0]];

// LED light pipes + button access (X, Y) in board coords
led1 = [79.0, 12.97];   // status (green)
led2 = [32.5, 44.5];    // power (red)
sw1  = [36.0, 5.0];     // EN / reset
sw2  = [78.0, 17.4];    // BOOT

/* ------------------------- enclosure parameters ---------------------- */
wall      = 2.2;
clr       = 2.0;                     // side clearance board->wall (X=0 side
                                     // effectively used by jack overhang)
bot_clr   = 9.0;                     // bottom-edge (Y=board_w side) clearance:
                                     // widened so the two OD7 lid screw posts
                                     // clear the PCB's bottom corners (board
                                     // edge to post edge = 2.0 mm)
standoff  = 3.0;                     // under-board space (THT pins ~2 mm)
headroom  = 16.5;                    // above-board space; RJ45 = 13.4 tall,
                                     // leaves 1.5 mm above the jacks even
                                     // under the 1.6 mm lid lip ring
lid_t     = 2.2;
lip       = 1.6;                     // lid registration lip (perimeter ring)
lip_w     = 2.0;                     // width of the registration ring band

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

// lid screw post centers (shared by base posts, lid holes, ring cutouts)
posts = [[wall+3.5, wall+3.5], [out_l-wall-3.5, wall+3.5],
         [wall+3.5, out_w-wall-3.5], [out_l-wall-3.5, out_w-wall-3.5]];

$fn = 32;

module rrect(l, w, h, r) {
  hull() for (x=[r, l-r], y=[r, w-r]) translate([x, y, 0]) cylinder(h=h, r=r);
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

    // RJ45 apertures, X=0 wall (jack face sits proud of the board edge)
    for (yc = [j1_yc, j2_yc])
      translate([-1, wall + by0 + yc - (rj45_w/2 + 0.5), bz0 + board_t - 0.3])
        cube([wall + bx0 + 4, rj45_w + 1.0, rj45_h + 1.0]);

    // USB-C aperture, X = out_l wall — overmold-sized (see parameter note):
    // the receptacle is recessed ~4.2 mm behind the exterior face, so the
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
  // (bottom pair clears the PCB bottom edge by 2.0 mm thanks to bot_clr)
  for (p = posts)
    translate([p[0], p[1], wall])
      difference() {
        cylinder(h = int_h, d = 7.0);
        translate([0, 0, int_h - 10]) cylinder(h = 11, d = 2.5);
      }
}

module ear() {
  difference() {
    cube([6, 12, 4]);
    translate([1.5, 3.5, -1]) cube([3, 5, 6]);   // zip-tie / #6 screw slot
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
          // clear the four screw posts (OD 7.0 + 0.3 both-sides clearance)
          for (p = posts)
            translate([p[0] - (wall + 0.15), p[1] - (wall + 0.15), -1])
              cylinder(h = lip + 2, d = 7.6);
        }
    }
    // M3 clearance holes + countersink over the 4 posts
    for (p = posts) {
      translate([p[0], p[1], -1]) cylinder(h = lid_t + lip + 2, d = 3.4);
      translate([p[0], p[1], -0.01]) cylinder(h = 1.8, d1 = 6.4, d2 = 3.4);
    }
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
    // antenna void: thin the lid over the antenna region (no metal anyway,
    // resin is RF-transparent; this just guarantees the air gap)
    translate([wall + bx0 + ant_x0 - 1, wall + 0.5, lid_t - 0.8])
      cube([ant_x1 - ant_x0 + 2, by0, lip + 1]);
  }
}

/* ------------------------- output ----------------------------------- */
if (part == "base") base();
else if (part == "lid") lid();
else {
  base();
  translate([0, out_w + 12, 0]) lid();
}
