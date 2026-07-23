#!/usr/bin/env python3
"""Generate kicad/Esp32Tap.kicad_pcb from design.py using the pcbnew API.

Run with:
  LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 /usr/bin/python3 gen_pcb.py

Board: 100 x 55 mm, 2 layer, origin = board top-left, +y down.
  * Left edge: J1 (console, top) + J2 (motor, bottom) RJ45 jacks, opening -x.
  * Pins 2,3,4,5,8 pass between the jacks as B.Cu verticals with F.Cu entry
    stubs + vias (pins 1/7 GND connect through the B.Cu plane directly).
  * K1 fail-safe relay between the jacks and the module on the pin-6 path.
  * ESP32-S3 module top-center; the PCB-antenna section overhangs the top
    board edge so the whole Espressif keep-out area is off-board.
  * Power entry chain along the bottom edge; USB-C on the right edge.
  * B.Cu is a GND plane; every SMD GND pad gets a stitching via.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design
import pcbnew
from pcbnew import VECTOR2I
from pcbnew import FromMM as MM

design.validate()

FP = design.FPLIB
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kicad", "Esp32Tap.kicad_pcb"))

OX, OY = 100.0, 100.0
BW, BH = 100.0, 55.0

board = pcbnew.NewBoard(OUT)
bds = board.GetDesignSettings()
bds.m_CopperEdgeClearance = MM(0.25)
bds.m_MinThroughDrill = MM(0.3)  # JLC 2-layer minimum drill is 0.3mm (0.2mm needs 4+ layers)

netinfo = {}
for name in design.NETS:
    n = pcbnew.NETINFO_ITEM(board, name)
    board.Add(n)
    netinfo[name] = n

pad_net = {}
for name, pads in design.NETS.items():
    for rp in pads:
        pad_net[rp] = name

# ------------------------------------------------------------- placement
PLACE = {
    "J1": (12.5, 8.0, 270),  # rot270 = opening -x; pads spread +y from anchor
    "J2": (12.5, 37.0, 270),
    "K1": (30.0, 26.0, 0),  # pads: L col x26.5 (1,2,3,4 @ y22.2/25.4/27.6/29.8)
    # bus protection / series parts
    "D5": (21.0, 16.2, 270),  # PESD console pin6: pad1 (21,15.15), pad2 (21,17.25)
    "D6": (28.2, 44.2, 270),  # PESD motor pin6: pad1 (22,43.35)
    "D7": (21.5, 41.0, 270),  # PESD pin3: pad1 (17.9,39.15)
    "R7": (44.0, 14.24, 0),  # cons RX 4.7k: 1 W=CONS6, 2 E=CONS_RX
    "R6": (40.5, 12.97, 180),  # TX 100R: pad1 E=ESP_TX, pad2 W=TX_DRV
    "R8": (24.5, 36.0, 180),  # pin3 RX 4.7k: pad1 E=PIN3, pad2 W=PIN3_RX
    # relay driver
    "D4": (33.0, 17.5, 0),  # 1 K W=+3V3, 2 A E=RELAY_SW
    "Q1": (38.5, 17.5, 0),  # B(37.56,16.55) E(37.56,18.45) C(39.44,17.5)
    "R9": (42.0, 16.55, 180),  # pad2 W=Q1_B, pad1 E=RELAY_EN
    "R10": (33.5, 15.6, 180),  # pad1 E=Q1_B, pad2 W=GND
    # ESP32 module (antenna overhangs top edge)
    "U1": (62.0, 6.8, 0),
    "C9": (48.4, 2.81, 180),  # 100n at 3V3 pin: pad1 E=3V3
    "C8": (49.2, 6.5, 180),  # 10u: pad1 E=3V3
    # EN / BOOT
    "R13": (43.0, 2.6, 270),  # pad1 N=+3V3 (43,1.78), pad2 S=EN (43,3.42)
    "C10": (46.0, 6.0, 180),  # pad1 E=EN, pad2 W=GND
    "SW1": (36.0, 5.0, 0),  # EN button
    "SW2": (78.0, 17.4, 0),  # BOOT button
    # LEDs / test pads
    "R11": (75.0, 12.97, 0),  # pad1 W=STATUS_LED, pad2 E=LED1_A
    "LED1": (79.0, 12.97, 180),  # pad2 A W, pad1 K E (GND)
    "R12": (36.0, 44.5, 180),  # pad1 E=+3V3, pad2 W=LED2_A
    "LED2": (32.5, 44.5, 0),  # pad2 A E, pad1 K W (GND)
    "TP1": (75.5, 4.6, 0),
    "TP2": (75.5, 7.4, 0),
    "TP3": (69.0, 52.0, 0),
    "TP4": (72.0, 52.0, 0),
    # power chain
    "F1": (9.0, 52.5, 0),  # pad1 W=+8V_RAW, pad2 E=+8V_F
    "D3": (19.3, 50.9, 90),  # TVS: pad1 S(13,52.15)=+8V_F, pad2 N=GND
    "C1": (26.0, 50.0, 90),  # elec: pad1 S(21,53.7)=+8V_F, pad2 N(21,48.3)=GND
    "C2": (31.5, 51.0, 90),  # pad1 S(26,52.4)=+8V_F, pad2 N=GND
    "D1": (37.0, 52.5, 180),  # pad1 E(33,52.5)=K=VIN, pad2 W(29,52.5)=A=+8V_F
    "L1": (45.5, 49.5, 180),  # pad1 E(47,49.5)=SW, pad2 W(44,49.5)=+3V3
    "U2": (54.0, 49.5, 0),  # 1 GND TL,2 SW ML,3 VIN BL,4 FB BR,5 EN MR,6 BOOT TR
    "C5": (49.6, 46.8, 180),  # boot cap: pad1 E(50.38)=BST, pad2 W(48.82)=SW
    "C3": (50.0, 52.3, 270),  # VIN 4.7u: pad1 N(50,50.4), pad2 S(50,53.2)=GND
    "C4": (52.9, 53.0, 270),  # VIN 100n: pad1 N(51.6,51.22), pad2 S=GND
    "R3": (58.2, 49.5, 180),  # pad1 E=VIN, pad2 W=BUCK_EN
    "R14": (57.38, 47.2, 270),  # EN divider bottom: pad1 N=GND (57.38,46.38), pad2 S=BUCK_EN (57.38,48.02)
    "R2": (60.0, 52.0, 270),  # pad1 N(59.5,50.68)=FB, pad2 S(59.5,52.32)=GND
    "R1": (63.0, 50.45, 180),  # pad2 W(62.18)=FB, pad1 E(63.82)=+3V3
    "C6": (40.5, 48.0, 90),  # 22u: pad1 S(40.5,48.95)=+3V3, pad2 N=GND
    "C7": (38.0, 48.0, 90),  # 22u
    # USB
    "J3": (96.2, 36.5, 90),  # rot90 = opening +x; pad col x=92.16
    "U3": (86.0, 36.5, 180),  # 1(87.14,37.45) 2 GND(87.14,36.5) 3(87.14,35.55)
    # 4(84.86,35.55) 5(84.86,36.5) 6(84.86,37.45)
    "C11": (88.5, 41.0, 180),  # pad1 E(89.28,41)=VBUS, pad2 W=GND
    "D2": (88.0, 44.5, 0),  # pad1 W(86,44.5)=K=VIN, pad2 E(90,44.5)=A=VBUS
    "R4": (94.5, 43.7, 270),  # pad1 N(94.5,41.68)=CC1, pad2 S=GND
    "R5": (94.5, 28.3, 90),  # pad1 S(94.5,30.32)=CC2, pad2 N=GND
}

fps = {}
for ref, comp in design.COMPONENTS.items():
    val, flib, fname = comp[0], comp[1], comp[2]
    fp = pcbnew.FootprintLoad(f"{FP}/{flib}.pretty", fname)
    assert fp, f"footprint {flib}:{fname}"
    fp.SetReference(ref)
    fp.SetValue(val)
    x, y, rot = PLACE[ref]
    fp.SetPosition(VECTOR2I(MM(OX + x), MM(OY + y)))
    fp.SetOrientationDegrees(rot)
    if ref == "U1":
        # The library ESP32-S3-WROOM-1 EP thermal vias are 0.2mm drill —
        # below the JLC 2-layer 0.3mm minimum.  Enlarge to 0.3mm drill
        # (pad enlarged to keep >=0.05mm annulus per side).
        for pad in fp.Pads():
            ds = pad.GetDrillSize()
            if ds.x > 0 and pcbnew.ToMM(ds.x) < 0.3:
                pad.SetDrillSize(pcbnew.VECTOR2I(MM(0.3), MM(0.3)))
                sz = pad.GetSize()
                if pcbnew.ToMM(sz.x) < 0.45:
                    pad.SetSize(pcbnew.VECTOR2I(MM(0.45), MM(0.45)))
    board.Add(fp)
    fps[ref] = fp

for ref, fp in fps.items():
    fp.Reference().SetLayer(pcbnew.F_Fab)
    fp.Reference().SetVisible(True)
    for pad in fp.Pads():
        num = str(pad.GetNumber())
        if not num:
            continue
        net = pad_net.get((ref, num))
        if net:
            pad.SetNet(netinfo[net])


def pad_pos(ref, num):
    for pad in fps[ref].Pads():
        if str(pad.GetNumber()) == num:
            p = pad.GetPosition()
            return (pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY)
    raise KeyError((ref, num))


def all_pad_pos(ref, num):
    out = []
    for pad in fps[ref].Pads():
        if str(pad.GetNumber()) == num:
            p = pad.GetPosition()
            out.append((pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY))
    return out


def edge_rect(x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(pts, pts[1:]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(VECTOR2I(MM(OX + a[0]), MM(OY + a[1])))
        seg.SetEnd(VECTOR2I(MM(OX + b[0]), MM(OY + b[1])))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(MM(0.1))
        board.Add(seg)


edge_rect(0, 0, BW, BH)

F, B = pcbnew.F_Cu, pcbnew.B_Cu


def track(pts, net, width=0.3, layer=F):
    for a, b in zip(pts, pts[1:]):
        if a == b:
            continue
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(VECTOR2I(MM(OX + a[0]), MM(OY + a[1])))
        t.SetEnd(VECTOR2I(MM(OX + b[0]), MM(OY + b[1])))
        t.SetWidth(MM(width))
        t.SetLayer(layer)
        t.SetNet(netinfo[net])
        board.Add(t)


def via(x, y, net, size=0.6, drill=0.3):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(VECTOR2I(MM(OX + x), MM(OY + y)))
    v.SetWidth(MM(size))
    v.SetDrill(MM(drill))
    v.SetLayerPair(F, B)
    v.SetNet(netinfo[net])
    board.Add(v)


# GND zone on B.Cu
z = pcbnew.ZONE(board)
z.SetLayer(B)
z.SetNet(netinfo["GND"])
z.SetLocalClearance(MM(0.3))
z.SetMinThickness(MM(0.25))
z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
chain = pcbnew.SHAPE_LINE_CHAIN()
for x, y in [(1, 1), (BW - 1, 1), (BW - 1, BH - 1), (1, BH - 1)]:
    chain.Append(MM(OX + x), MM(OY + y))
chain.SetClosed(True)
z.Outline().AddOutline(chain)
board.Add(z)

P = pad_pos


# ============================ pass-through bus (F stubs + B verticals) ===
def passthrough(num, netname, w, lane, side):
    a, b2 = P("J1", num), P("J2", num)
    track([a, (lane, a[1])], netname, w)
    via(lane, a[1], netname)
    track([(lane, a[1]), (lane, b2[1])], netname, w, B)
    via(lane, b2[1], netname)
    track([(lane, b2[1]), b2], netname, w)


passthrough("2", "+8V_RAW", 0.5, 16.6, "E")
passthrough("8", "+8V_RAW", 0.5, 17.4, "E")
passthrough("4", "PIN4_PASS", 0.3, 18.2, "E")
passthrough("5", "PIN5_SAFETY", 0.5, 10.9, "W")
passthrough("3", "PIN3", 0.3, 10.1, "W")

# +8V feed to fuse: continue lane 17.4 down on B, then F into F1.1
j28 = P("J2", "8")
f11, f12 = P("F1", "1"), P("F1", "2")
track([(16.6, 38.27), (17.4, 38.27)], "+8V_RAW", 0.5)
via(17.4, 38.27, "+8V_RAW")
track([(17.4, j28[1]), (17.4, 52.5), (7.8, 52.5)], "+8V_RAW", 0.5, B)
via(7.8, 52.5, "+8V_RAW")
track([(7.8, 52.5), f11], "+8V_RAW", 0.5)

# ============================ 8V filter chain (F, y=52.5 run) ============
d31 = P("D3", "1")
c11_ = P("C1", "1")
c21 = P("C2", "1")
d12 = P("D1", "2")
track([f12, (29.0, 52.5), d12], "+8V_F", 0.8)
track([(d31[0], 52.5), d31], "+8V_F", 0.5)
track([(c11_[0], 52.5), (c11_[0], c11_[1])], "+8V_F", 0.5)
# C2 pad1 (26,52.4) sits on the run; stub down
track([(c21[0], 52.5), c21], "+8V_F", 0.5)

# ============================ VIN =======================================
d11 = P("D1", "1")
u2_vin = P("U2", "3")
c31 = P("C3", "1")
c41 = P("C4", "1")
d2k = P("D2", "1")
r3vin = P("R3", "1")
# D1 K -> junction (45.5,52.5) -> pad3 approach + C3/C4 taps
track([d11, (48.0, 52.5)], "VIN", 0.8)
track([(48.0, 52.5), (48.0, u2_vin[1]), u2_vin], "VIN", 0.5)
track([c31, (c31[0], u2_vin[1])], "VIN", 0.5)  # C3.1 (50,50.4) up to run
track([c41, (c41[0], u2_vin[1])], "VIN", 0.5)  # C4.1 (51.6,51.22) up
# long leg to D2 (USB ORing) via x=45.5 corridor between L1 pads
track([(45.5, 52.5), (45.5, 44.5), d2k], "VIN", 0.8)
# R3 pull-up leg off the y=44.5 run
track([(r3vin[0], 44.5), r3vin], "VIN", 0.3)

u2_en = P("U2", "5")
r3en = P("R3", "2")
track([u2_en, r3en], "BUCK_EN", 0.3)
# R14 divider bottom hangs off the R3.2/EN node (vertical at x=57.38)
r14en = P("R14", "2")
track([r3en, r14en], "BUCK_EN", 0.3)

# ============================ SW / BST / 3V3 ============================
u2_sw = P("U2", "2")
l1_sw = P("L1", "1")
c5_sw = P("C5", "2")
c5_bst = P("C5", "1")
u2_bst = P("U2", "6")
track([u2_sw, l1_sw], "SW_NODE", 0.5)
track([c5_sw, (c5_sw[0], u2_sw[1])], "SW_NODE", 0.4)
track([u2_bst, (u2_bst[0], 47.2), (c5_bst[0], 47.2), c5_bst], "BST", 0.3)

l1_out = P("L1", "2")
c61 = P("C6", "1")
c71 = P("C7", "1")
r12_3v3 = P("R12", "1")
# F chain west along y=49.5
track([l1_out, (38.0, 49.5)], "+3V3", 0.8)
track([(39.25, 49.5), (39.25, 44.5), P("R12", "1")], "+3V3", 0.4)
track([(c61[0], 49.5), c61], "+3V3", 0.5)
track([(c71[0], 49.5), c71], "+3V3", 0.5)
# R12/LED2 power LED sits on the chain end (pad1 at 34.82 on the run)
track([P("R12", "2"), P("LED2", "2")], "LED2_A", 0.3)
# B trunk up to the module 3V3 pin
via(42.8, 49.5, "+3V3")
track([(42.8, 49.5), (42.8, 1.6), (50.5, 1.6), (50.5, 2.81)], "+3V3", 0.5, B)
via(50.5, 2.81, "+3V3")
m3v3 = P("U1", "2")
track([(50.5, 2.81), m3v3], "+3V3", 0.5)
# C9 100n on the same F row
c91 = P("C9", "1")
track([(50.5, 2.81), c91], "+3V3", 0.4)
# C8 10u fed by B spur at y=6.5
track([(42.8, 6.5), (51.3, 6.5)], "+3V3", 0.4, B)
via(51.3, 6.5, "+3V3")
track([(51.3, 6.5), P("C8", "1")], "+3V3", 0.4)
# R13 (EN pull-up) 3V3 via B at top
r13_3v3 = P("R13", "1")
via(44.0, 0.95, "+3V3")
track([r13_3v3, (43.0, 0.95), (44.0, 0.95)], "+3V3", 0.3)
track([(44.0, 0.95), (42.8, 0.95), (42.8, 1.6)], "+3V3", 0.3, B)
# relay coil + flyback K
d4k = P("D4", "1")
k1cp = P("K1", "1")
track([(42.8, 20.5), (28.0, 20.5)], "+3V3", 0.4, B)
via(28.0, 20.5, "+3V3")
track([(28.0, 20.5), (k1cp[0], 20.5), k1cp], "+3V3", 0.4)
track([(28.0, 20.5), (28.0, 17.5), d4k], "+3V3", 0.3)
# R1 (FB top) + TP3 fed from B
r1_3v3 = P("R1", "1")
via(65.1, 50.45, "+3V3")
track([r1_3v3, (65.1, 50.45)], "+3V3", 0.3)
track([(65.1, 50.45), (65.1, 52.6), (42.8, 52.6), (42.8, 49.5)], "+3V3", 0.4, B)
tp3 = P("TP3", "1")
track([(65.1, 50.45), (tp3[0], 50.45), tp3], "+3V3", 0.3)

# FB divider
u2_fb = P("U2", "4")
r2fb = P("R2", "1")
r1fb = P("R1", "2")
track([u2_fb, (r2fb[0], u2_fb[1]), r2fb], "FB", 0.3)
track([(r2fb[0], 50.45), r1fb], "FB", 0.3)

# ============================ pin 6 fail-safe ===========================
j16 = P("J1", "6")
k2, k7 = P("K1", "2"), P("K1", "7")
d51 = P("D5", "1")
r71 = P("R7", "1")
# console line: jack -> relay NC pads -> RX resistor, one horizontal at y=14.35
track([j16, (43.18, 14.35)], "CONS6", 0.3)  # ends inside R7.1 pad
track([(21.0, 14.35), d51], "CONS6", 0.3)  # PESD stub
track([(18.5, 14.35), (18.5, k2[1]), k2], "CONS6", 0.3)
track([k2, k7], "CONS6", 0.3)

j26 = P("J2", "6")
k3, k6 = P("K1", "3"), P("K1", "6")
d61 = P("D6", "1")
track([j26, (28.2, 43.35)], "MOT6", 0.3)
track([(28.2, 43.35), d61], "MOT6", 0.3)  # D6.1 sits at (22,43.35)
via(19.5, 43.35, "MOT6")
track([(19.5, 43.35), (19.5, k3[1])], "MOT6", 0.3, B)
via(19.5, k3[1], "MOT6")
track([(19.5, k3[1]), k3], "MOT6", 0.3)
track([k3, k6], "MOT6", 0.3)

k4, k5 = P("K1", "4"), P("K1", "5")
r6tx = P("R6", "2")
track([k4, k5], "TX_DRV", 0.3)
track([r6tx, (24.0, 12.97)], "TX_DRV", 0.3)
via(24.0, 12.97, "TX_DRV")
track([(24.0, 12.97), (24.0, k4[1])], "TX_DRV", 0.3, B)
via(24.0, k4[1], "TX_DRV")
track([(24.0, k4[1]), k4], "TX_DRV", 0.3)

track([P("R6", "1"), P("U1", "10")], "ESP_TX", 0.3)
track([P("R7", "2"), P("U1", "11")], "CONS_RX", 0.3)

# ============================ pin 3 tap =================================
r81 = P("R8", "1")
d71 = P("D7", "1")
j23 = P("J2", "3")
track([j23, (r81[0], 39.54), r81], "PIN3", 0.3)  # R8.1 at (25.32,36)? see below
# NOTE: R8.1 is at y=36; approach: run y=39.54 then up x=r81.x
r82 = P("R8", "2")
track([(d71[0], 39.54), d71], "PIN3", 0.3)
track([r82, (22.5, 36.0)], "PIN3_RX", 0.3)
via(22.5, 36.0, "PIN3_RX")
track([(22.5, 36.0), (22.5, 11.7)], "PIN3_RX", 0.3, B)
via(22.5, 11.7, "PIN3_RX")
m9 = P("U1", "9")
track([(22.5, 11.7), (52.5, 11.7), m9], "PIN3_RX", 0.3)

# ============================ relay driver ==============================
q1c = P("Q1", "3")
d4a = P("D4", "2")
k8 = P("K1", "8")
track([d4a, q1c], "RELAY_SW", 0.3)
track([k8, (k8[0], 20.4), (36.2, 20.4), (36.2, 17.5)], "RELAY_SW", 0.3)
q1b = P("Q1", "1")
r9b = P("R9", "2")
r10b = P("R10", "1")
track([q1b, (r9b[0], q1b[1]), r9b], "Q1_B", 0.3)
track([(36.9, q1b[1]), (36.9, r10b[1]), r10b], "Q1_B", 0.3)
r9en = P("R9", "1")
m23 = P("U1", "23")
track([r9en, (44.3, r9en[1]), (44.3, 21.0), (m23[0], 21.0), m23], "RELAY_EN", 0.3)

# ============================ USB =======================================
# merged pads: VBUS at (92.16,38.95)+(92.16,34.05); GND at 39.75/33.25
vb_n = (92.16, 38.95)
vb_s = (92.16, 34.05)
u35 = P("U3", "5")
c11v = P("C11", "1")
d2a = P("D2", "2")
track([vb_n, (90.9, 38.95)], "VBUS", 0.4)
via(90.9, 38.95, "VBUS")
track([vb_s, (90.9, 34.05)], "VBUS", 0.4)
via(90.9, 34.05, "VBUS")
track([(90.9, 34.05), (90.9, 38.95)], "VBUS", 0.5, B)
track([(90.9, 38.95), (90.9, 44.5)], "VBUS", 0.5, B)
via(90.9, 44.5, "VBUS")
track([(90.9, 44.5), d2a], "VBUS", 0.5)
via(90.9, 41.0, "VBUS")
track([(90.9, 41.0), c11v], "VBUS", 0.4)
# U3.5 via B spur around the south
track([(90.9, 34.05), (90.9, 33.5), (84.5, 33.5), (84.5, 36.5)], "VBUS", 0.4, B)
via(84.5, 36.5, "VBUS")
track([(84.5, 36.5), u35], "VBUS", 0.4)

# USB_DN all on F: bridge A7-B7 at x=91.0, tap to U3.1
a7, b7 = (92.16, 36.25), (92.16, 37.25)
u31 = P("U3", "1")
track([a7, (91.0, 36.25), (91.0, 37.25), b7], "USB_DN", 0.3)
track([(91.0, 37.25), (91.0, 37.45), u31], "USB_DN", 0.3)
# USB_DP: bridge A6-B6 with B hop under the connector (x=93.5), tap W of B6
a6, b6 = (92.16, 36.75), (92.16, 35.75)
u33 = P("U3", "3")
track([a6, (93.5, 36.75)], "USB_DP", 0.3)
via(93.5, 36.75, "USB_DP", size=0.5, drill=0.3)
track([(93.5, 36.75), (93.5, 35.75)], "USB_DP", 0.3, B)
via(93.5, 35.75, "USB_DP", size=0.5, drill=0.3)
track([(93.5, 35.75), b6], "USB_DP", 0.3)
track([b6, (90.2, 35.75), (90.2, 35.55), u33], "USB_DP", 0.3)

# MCU legs on B.Cu
u36 = P("U3", "6")
u34 = P("U3", "4")
m13 = P("U1", "13")
m14 = P("U1", "14")
track([u36, (83.5, u36[1])], "USB_DN_MCU", 0.3)
via(83.5, u36[1], "USB_DN_MCU")
track([(83.5, u36[1]), (83.5, 15.5), (54.9, 15.5)], "USB_DN_MCU", 0.3, B)
via(54.9, 15.5, "USB_DN_MCU")
track([(54.9, 15.5), (54.9, m13[1]), m13], "USB_DN_MCU", 0.3)
track([u34, (82.8, u34[1])], "USB_DP_MCU", 0.3)
via(82.8, u34[1], "USB_DP_MCU")
track([(82.8, u34[1]), (82.8, 21.2), (55.4, 21.2), (55.4, m14[1])], "USB_DP_MCU", 0.3, B)
via(55.4, m14[1], "USB_DP_MCU")
track([(55.4, m14[1]), m14], "USB_DP_MCU", 0.3)

# CC pull-downs (corridor between shield pads x=95.1)
cc1_pad = (92.16, 37.75)
cc2_pad = (92.16, 34.75)
r4cc = P("R4", "1")
r5cc = P("R5", "1")
track([cc1_pad, (93.2, 37.75), (95.1, 37.75), (95.1, r4cc[1]), r4cc], "CC1", 0.3)
track([cc2_pad, (93.2, 34.75), (95.1, 34.75), (95.1, r5cc[1]), r5cc], "CC2", 0.3)

# ============================ EN / IO0 / LEDs / TPs =====================
m_en = P("U1", "3")
r13en = P("R13", "2")
c10en = P("C10", "1")
sw1 = all_pad_pos("SW1", "1")
sw1e = max(sw1, key=lambda p: p[0])  # east pad copy
track([m_en, (38.5, 4.08)], "EN", 0.3)
track([(43.0, 4.08), r13en], "EN", 0.3)
track([(c10en[0], 4.08), (c10en[0], c10en[1])], "EN", 0.3)
track([(38.5, 4.08), (38.5, sw1e[1]), sw1e], "EN", 0.3)
sw1w = min(sw1, key=lambda p: p[0])
track([sw1w, sw1e], "EN", 0.3)
g1 = all_pad_pos("SW1", "2")
track([min(g1), max(g1)], "GND", 0.3)

m27 = P("U1", "27")
sw2 = all_pad_pos("SW2", "1")
sw2w = min(sw2, key=lambda p: p[0])
track([m27, (74.5, m27[1]), (74.5, sw2w[1]), sw2w], "IO0", 0.3)
sw2e = max(sw2, key=lambda p: p[0])
track([sw2w, sw2e], "IO0", 0.3)
g2 = all_pad_pos("SW2", "2")
track([min(g2), max(g2)], "GND", 0.3)

m31 = P("U1", "31")
track([m31, P("R11", "1")], "STATUS_LED", 0.3)
track([P("R11", "2"), P("LED1", "2")], "LED1_A", 0.3)

tp1 = P("TP1", "1")
tp2 = P("TP2", "1")
m37 = P("U1", "37")
m36 = P("U1", "36")
track([m37, (73.5, m37[1]), (73.5, tp1[1]), tp1], "U0TXD", 0.3)
track([m36, (73.0, m36[1]), (73.0, tp2[1]), tp2], "U0RXD", 0.3)

# ============================ GND stitching =============================
GND_STITCH = {
    ("U1", "1"): (-1.3, 0.0),
    ("U1", "40"): (1.3, 0.0),
    ("U2", "1"): (-1.3, 0.0),
    ("U3", "2"): (0.0, 0.0),
    ("Q1", "2"): (0.0, 1.3),
    ("R2", "2"): (0.0, 1.3),
    ("R4", "2"): (0.0, 1.3),
    ("R5", "2"): (0.0, -1.3),
    ("R10", "2"): (-1.3, 0.0),
    ("R14", "1"): (-1.3, 0.0),
    ("C1", "2"): (1.5, 0.0),
    ("C2", "2"): (0.0, -1.3),
    ("C3", "2"): (-1.3, 0.0),
    ("C4", "2"): (1.3, 0.0),
    ("C6", "2"): (0.0, -1.3),
    ("C7", "2"): (0.0, -1.3),
    ("C8", "2"): (0.0, 1.3),
    ("C9", "2"): (-1.2, 0.0),
    ("C10", "2"): (0.0, 1.4),
    ("C11", "2"): (-1.3, 0.0),
    ("D3", "2"): (0.0, -1.4),
    ("D5", "2"): (0.0, 1.3),
    ("D6", "2"): (0.0, 1.3),
    ("D7", "2"): (1.3, 0.0),
    ("LED1", "1"): (1.3, 0.0),
    ("LED2", "1"): (-1.3, 0.0),
    ("SW1", "2"): (0.0, 1.4),
    ("SW2", "2"): (0.0, 1.4),
    ("TP4", "1"): (1.3, 0.0),
}
for (ref, num), (dx, dy) in GND_STITCH.items():
    pads = all_pad_pos(ref, num)
    if ref.startswith("SW"):  # buttons: two pads per number, use east
        pads = [max(pads, key=lambda p: p[0])]
    for px, py in pads:
        if dx == 0.0 and dy == 0.0:
            via(px, py, "GND")  # via-in-pad (U3 center GND pad)
            continue
        track([(px, py), (px + dx, py + dy)], "GND", 0.35)
        via(px + dx, py + dy, "GND")

# module EP already has 13 thermal PTH vias in the footprint

# USB shield pads: add stitch vias if the pads are SMD (THT connect via plane)
for pad in fps["J3"].Pads():
    if str(pad.GetNumber()) == "S1" and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
        p = pad.GetPosition()
        px, py = pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY
        dy = -1.6 if py < 36.5 else 1.6
        track([(px, py), (px, py + dy)], "GND", 0.35)
        via(px, py + dy, "GND")


# J3 GND pads (merged A1/B12 and A12/B1) tie to the PTH shield lugs
track([(92.16, 39.75), (93.07, 40.82)], "GND", 0.4)
track([(92.16, 33.25), (93.07, 32.18)], "GND", 0.4)

# mounting holes (NPTH, board-only mechanical items)
MH = {"MH1": (2.9, 26.5), "MH2": (97.0, 3.0), "MH3": (97.0, 52.0)}
for _ref, (_x, _y) in MH.items():
    _fp = pcbnew.FootprintLoad(f"{FP}/MountingHole.pretty", "MountingHole_2.7mm_M2.5")
    _fp.SetReference(_ref)
    _fp.SetValue("M2.5")
    _fp.Reference().SetLayer(pcbnew.F_Fab)
    _fp.SetPosition(VECTOR2I(MM(OX + _x), MM(OY + _y)))
    board.Add(_fp)
    fps[_ref] = _fp

# remove footprint silkscreen segments that cross the board edge
for _ref in ("J1", "J2", "J3", "U1"):
    _kill = []
    for _gi in fps[_ref].GraphicalItems():
        if _gi.GetLayer() == pcbnew.F_SilkS:
            _bb = _gi.GetBoundingBox()
            if (
                pcbnew.ToMM(_bb.GetLeft()) < OX + 0.2
                or pcbnew.ToMM(_bb.GetRight()) > OX + BW - 0.2
                or pcbnew.ToMM(_bb.GetTop()) < OY + 0.2
                or pcbnew.ToMM(_bb.GetBottom()) > OY + BH - 0.2
            ):
                _kill.append(_gi)
    for _gi in _kill:
        fps[_ref].Remove(_gi)


# ============================ silkscreen ================================
def silk(x, y, s, size=1.2):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(VECTOR2I(MM(OX + x), MM(OY + y)))
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(VECTOR2I(MM(size), MM(size)))
    t.SetTextThickness(MM(0.15))
    board.Add(t)


silk(7.0, 22.5, "CONSOLE", 1.0)
silk(7.0, 30.5, "MOTOR", 1.0)
silk(66.0, 33.0, "Esp32Tap rev A - precor-9.3x", 1.2)

import math

for _ref in ("J1", "J2", "J3", "MH1", "MH2", "MH3"):
    for _pad in fps[_ref].Pads():
        if _pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            _p = _pad.GetPosition()
            _r = pcbnew.ToMM(max(_pad.GetDrillSize().x, _pad.GetDrillSize().y)) / 2 + 0.4
            _ka = pcbnew.ZONE(board)
            _ka.SetIsRuleArea(True)
            _ka.SetDoNotAllowZoneFills(True)
            _ka.SetDoNotAllowTracks(False)
            _ka.SetDoNotAllowVias(False)
            _ka.SetLayer(B)
            _kchain = pcbnew.SHAPE_LINE_CHAIN()
            for _i in range(16):
                _a = _i * math.tau / 16
                _kchain.Append(int(_p.x + MM(_r) * math.cos(_a)), int(_p.y + MM(_r) * math.sin(_a)))
            _kchain.SetClosed(True)
            _ka.Outline().AddOutline(_kchain)
            board.Add(_ka)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())

pcbnew.SaveBoard(OUT, board)
print("wrote", OUT)
