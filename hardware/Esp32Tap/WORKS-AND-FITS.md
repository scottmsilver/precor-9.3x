# Esp32Tap Rev B: works-and-fits assessment

**Status: HOLD.** The digital artifacts agree with one another and the
enclosure geometry fits the generated PCB model. Nothing in this document
replaces a vendor preview or a physical verification build.

## Short answer

| Question | Answer |
|---|---|
| Does the generated circuit implement treadmill-only power and fail-to-bypass topology? | Yes, at source/schematic/PCB connectivity level |
| Do schematic, board, BOM, CPL, and fabrication files agree? | Yes, under independent automated comparison |
| Does the PCB clear KiCad checks? | Yes: zero ERC findings; zero DRC violations, unconnected pads, and footprint errors under the locked policy |
| Do the behavioral models meet their declared numeric assertions? | Yes, on host ngspice 42 and pinned Docker ngspice 39 |
| Does the checked PCB geometry fit the checked enclosure geometry? | Yes, under mesh and functional-probe validation |
| Is production firmware implemented and physically tested? | No |
| Was the exact archive observed completing JLC's online PCB DFM analysis? | Yes; the operator-recorded raw counts preserve four generic slot dangers and their engineering disposition, leaving zero unresolved actionable dangers |
| Has JLC production engineering accepted placement, stackup, carrier, THT process, or substitutions? | No |
| Has any assembled board been powered or connected to a treadmill? | No |

## Electrical coherence

The source tables, typed schematic, PCB pad nets, and generated netlist agree
on these safety-critical facts:

- J1/J2 +8 V and ground are direct pass-through nets.
- Local power branches through F1 and D1; USB VBUS is not a board-power net.
- J3 signal ground and shield share board/treadmill ground; USB is not
  galvanically isolated even though VBUS cannot power the board.
- U4 creates treadmill-derived `TREAD_OK`.
- U6 computes `RELAY_GATE = RELAY_CMD AND TREAD_OK` and
  `TX_GATE = TX_ENABLE AND TREAD_OK`.
- U5 and Q1 are series coil controls; U7 is the independent TX output-enable.
- K1 pole A transfers MOT6 between CONS6 and TX_DRV.
- K1 pole B reports normally closed/normally open armature state.
- R7/R8 are passive 10 kΩ receive taps and do not interrupt the through bus.

The static topology therefore fails toward the NC bypass. Real component
failure modes and relay contact timing remain outside that proof.

## PCB geometry

| Feature | Checked value |
|---|---|
| Outline | 100.0 × 55.0 mm |
| Copper layers | F.Cu, In1.Cu, In2.Cu, B.Cu |
| Finished thickness metadata | 1.59 mm |
| Reference plane | One In1.Cu GND zone, continuous below USB except normal antipads; antenna keepout |
| ESP32 antenna span | X 69.0–87.0 mm in board coordinates |
| Antenna overhang | 6.3 mm beyond the board edge |
| USB pair | F.Cu only; no signal vias; 0.2906/0.2000 mm controlled run plus four short 0.20 mm breakouts |
| Mounting holes | MH1 (2.9, 26.5), MH2 (97.0, 3.0), MH3 (97.0, 52.0) mm |

The board validator checks exact route topology, keepouts, layer use,
decoupling/ground-via proximity, test access, silkscreen, BOM/CPL parity, and
fabrication membership. An operator also observed the exact archive complete
online JLCDFM analysis; the SHA-bound record is not vendor-signed or
independent upload-provenance evidence. A production field-solver result and
written stack/impedance acceptance are still required.

## Enclosure fit

The board top-left corner sits at enclosure-interior coordinate (2.0, 21.3)
mm. The 21.3 mm offset consists of 6.3 mm module overhang plus 15.0 mm axial
clearance beyond the antenna end; it is not a 15 mm all-direction clearance.

| Fit item | Checked result |
|---|---|
| Interior cavity | 104.0 × 85.3 × 21.1 mm |
| Board side clearance | 2.0 mm each X side |
| Bottom-edge clearance | 9.0 mm |
| Board underside above floor | 5.5 mm |
| RJ45 above-board headroom | 1.9 mm below lid lip |
| RJ45 centers | 12.445 and 41.445 mm, derived from PCB pads |
| USB-C | 13.0 × 8.0 mm overmold aperture; receptacle face recessed 4.5 mm behind exterior |
| Antenna end | 15.0 mm axial plastic/air clearance, no conductive finish or hardware |
| Base/lid | One connected, watertight, winding-consistent body each |

The RJ45 receptacle faces are about 2.7 mm behind the exterior wall. The
apertures are intended for ordinary unbooted or slim-boot 8P8C plugs; arbitrary
oversize molded boots are not claimed. USB access is sized so a normal cable
overmold enters the wall recess.

The meshes prove geometry, not manufacturing outcome. Resin shrink, warp,
surface finish, screw retention, cable latch access, and installed service
clearance must be checked on physical parts.

## RF and installation

Every copper layer is excluded beneath the module antenna, and the enclosure
adds 15 mm axial clearance beyond its end. Keep the antenna end away from the
metal motor hood and do not select metal-filled or conductively coated
material. These precautions do not predict BLE/Wi-Fi range; perform an
installed RSSI and coexistence survey.

## What must happen next

1. Regenerate and pass the canonical repository gate from clean sources.
2. Refresh the exact JLC stock snapshot.
3. Complete the JLC PCBA BOM/CPL placement preview and obtain engineering
   confirmation for stackup/impedance, the RJ45 fixture/process, and the
   antenna-overhang carrier.
4. Obtain JLC3DP mesh/material/DFM confirmation.
5. If the owner authorizes a verification build, perform the staged bench
   program in `VALIDATION.md` and `firmware/PLAN.md`.
6. Keep treadmill operation blocked until production firmware and physical
   safety evidence are complete.

The accurate statement is: the repository model works and the modeled
geometry fits; the physical product remains unproven and on HOLD.
