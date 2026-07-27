# Esp32Tap Rev E: works-and-fits assessment

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
| Was the exact current archive observed completing JLC's online PCB DFM analysis? | No. An operator-recorded analysis is retained for a superseded archive only (`vendor/JLC-DFM-REVIEW.json` is `NOT_REVIEWED_EXACT_ARCHIVE`); the current archive must be re-uploaded before its DFM state is evidence |
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
| Outline | 95.0 × 58.0 mm |
| Copper layers | F.Cu, In1.Cu, In2.Cu, B.Cu |
| Finished thickness metadata | 1.59 mm |
| Reference plane | One In1.Cu GND zone, continuous below USB except normal antipads; antenna keepout |
| ESP32 antenna span | X 69.0–87.0 mm in board coordinates, fully on-board (physical antenna edge 3.3 mm inside the board edge) |
| Antenna copper exclusion | Module's stock all-copper-layer keepout is track/via-free on every layer (audited), plus the named on-board rule area |
| RJ45 jacks | One per short edge: J1 (CONSOLE) opens off X=0, J2 (MOTOR) opens off X=95 |
| USB pair | F.Cu only; no signal vias; 0.2906/0.2000 mm controlled run plus four short 0.20 mm breakouts; J3 on the bottom edge |
| UART probe pads | TP1/TP2 beside U1's east pad column (short local links; they no longer cross the board) |
| Mounting holes | MH1 (20.0, 6.0), MH2 (48.0, 6.0), MH3 (92.0, 55.0) mm (outline-minimum coordinates) |

The board validator checks exact route topology, keepouts, layer use,
decoupling/ground-via proximity, test access, silkscreen, BOM/CPL parity, and
fabrication membership. The retained operator DFM record applies to a
superseded archive; the current exact archive has not been re-uploaded, and a
production field-solver result and written stack/impedance acceptance are
still required.

## Enclosure fit

Numbers below restate the current model record in
[`enclosure/DIMENSIONS.md`](enclosure/DIMENSIONS.md), which is maintained with
the meshes and is the authoritative geometry table.

| Fit item | Checked result |
|---|---|
| Interior cavity | 99.0 × 78.7 × 21.1 mm |
| Board | 95.0 × 58.0 × 1.6 mm, 2.0 mm clearance each X side |
| PCB under-clearance / headroom | 3.0 / 16.5 mm |
| RJ45 apertures | One per end wall (J1 X-min, J2 X-max), identical 16.0 × 14.0 mm openings for the 15.48 × 13.4 mm jack body |
| USB-C | 13.0 × 8.0 mm overmold aperture through the bottom-edge wall |
| Antenna end | 15.0 mm axial plastic/air void beyond the physical antenna edge, no conductive finish or hardware |
| Base/lid | One connected, watertight, winding-consistent body each |

Because both jacks are the identical unshielded 8P8C part there is no
mechanical keying between console and motor; the CONSOLE/MOTOR silkscreen and
the opposite-end cable exits are the only differentiators. The apertures are
intended for ordinary unbooted or slim-boot 8P8C plugs; arbitrary oversize
molded boots are not claimed. USB access is sized so a normal cable overmold
enters the wall recess.

The meshes prove geometry, not manufacturing outcome. Resin shrink, warp,
surface finish, screw retention, cable latch access, and installed service
clearance must be checked on physical parts.

## RF and installation

Every copper layer is excluded beneath the module antenna (the router now
honours the module's stock keepout, and an audit test holds it to zero
track/via copper), and the enclosure adds a 15 mm axial void beyond the
antenna edge. Keep the antenna end away from the metal motor hood and do not
select metal-filled or conductively coated material. These precautions do not
predict BLE/Wi-Fi range; perform an installed RSSI and coexistence survey.

## What must happen next

1. Regenerate and pass the canonical repository gate from clean sources.
2. Refresh the exact JLC stock snapshot.
3. Upload the current exact archive to JLC DFM analysis, then complete the
   JLC PCBA BOM/CPL placement preview and obtain engineering confirmation for
   stackup/impedance, the RJ45 fixture/process, and the antenna carrier.
4. Obtain JLC3DP mesh/material/DFM confirmation.
5. If the owner authorizes a verification build, perform the staged bench
   program in `VALIDATION.md` and `firmware/PLAN.md`.
6. Keep treadmill operation blocked until production firmware and physical
   safety evidence are complete.

The accurate statement is: the repository model works and the modeled
geometry fits; the physical product remains unproven and on HOLD.
