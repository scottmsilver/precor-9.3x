# Esp32Tap DevKit Bare-Board Bring-Up Design

**Issue:** `precor-9_3x-1y2`  
**Target:** ESP32-S3-DevKitC-1 v1.1, N8R8 (8 MB flash, 8 MB PSRAM)  
**Bench host:** Raspberry Pi 4 `rpi`, verified at `192.168.1.155` and Tailscale `100.117.206.120`  
**Serial device:** `/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_4cd513f253bff0119bc5c57948e9de0f-if00-port0`

## Purpose

Produce the first image that can be flashed to the physical development device and prove the ESP32-S3, flash, PSRAM, build chain, remote flashing path, and protected GPIO state before any treadmill circuitry is connected.

The first artifact is deliberately not the production treadmill application. It is a small, separately named `devkit-bringup` binary whose defining property is that it cannot command the relay or transmit to the motor. The production binary and its safety contract remain unchanged.

Real Wi-Fi is not part of this first artifact. The current Rust network tier brings up QEMU's OpenETH device; it does not implement a hardware Wi-Fi station or provisioning. The Pi's USB/UART connection is the honest control and observation path for this milestone. Hardware Wi-Fi remains a later stage tracked separately.

## Approach and rejected alternatives

The bring-up proceeds in two physical stages:

1. Flash and boot the DevKit with only its UART USB cable connected.
2. After a machine-checked pass, attach a sidecar breadboard that simulates four inputs and provides visible tripwires on two protected outputs.

Flashing the production image directly was rejected because it configures GPIO 15, 21, and 38 as outputs, initializes the treadmill UARTs, and expects board-level feedback and interlocks that do not exist on the DevKit. A disposable hello-world image was also rejected: it would prove the toolchain but not the real repository's flash layout, safety boundaries, or repeatable hardware workflow.

## Artifact boundary

`devkit-bringup` is a separate binary target with no dependency on the production control loop, program executor, motor/console UART implementation, BLE, Wi-Fi, TLS, HTTP server, or treadmill persistence layer.

Its build identity and UART banner must say:

```text
ESP32TAP DEVKIT BRINGUP — NO CONTROL OUTPUTS
```

The QEMU test image must never be flashable through this workflow. The manifest identifies the target, profile, Git commit, dirty-state verdict, image hashes, flash geometry, and required serial device. The Pi-side flash command refuses an absent or mismatched manifest.

## Protected GPIO contract

The source of truth for production GPIO numbers remains `hardware/Esp32Tap/tools/design.py`, checked by `tools/check_pins.py`.

| GPIO | Production net | DevKit bring-up rule |
|---:|---|---|
| 4 | `K1_NC_FB` | Input only; Stage 0 untouched, Stage 1 sampled |
| 5 | `K1_NO_FB` | Input only; Stage 0 untouched, Stage 1 sampled |
| 6 | `TREAD_OK_MCU` | Input only; Stage 0 untouched, Stage 1 sampled |
| 7 | `VBUS_PRESENT_N` | Input only; Stage 0 untouched, Stage 1 sampled |
| 15 | `TX_ENABLE` | Must remain input/Hi-Z; output tripwire must stay dark |
| 16 | `PIN3_RX` | Untouched |
| 17 | `ESP_TX` | Must remain input/Hi-Z and untouched |
| 18 | `CONS_RX` | Untouched |
| 21 | `RELAY_CMD` | Must remain input/Hi-Z; output tripwire must stay dark |
| 38 | `STATUS_LED` | Untouched; on DevKitC-1 v1.1 this drives an addressable RGB LED, not the production board's simple LED |

No internal pull may be enabled on GPIO 15, 17, or 21. GPIO direction proof must read the hardware direction registers after initialization; a log statement that merely describes intent is not evidence.

## Diagnostic report

UART0 emits one bounded, machine-parseable report containing:

- Build identity, Git commit, and artifact manifest hash.
- Chip model, revision, base MAC, reset reason, and crystal frequency where available.
- Detected flash size and PSRAM size.
- Free internal heap and free PSRAM after initialization.
- Direction and level readback for every protected GPIO.
- One final `BRINGUP PASS` or one specific `BRINGUP FAIL <code>`.

Any failed invariant produces the failure record and enters a bounded safe halt. The diagnostic application does not compensate by configuring a pin, does not continue into another tier, and does not intentionally reboot-loop.

## Build, backup, and flash flow

```text
clean checkout
  -> pinned Docker build
  -> host safety and clean-input gates
  -> image manifest + SHA-256 hashes
  -> copy bundle to Raspberry Pi
  -> verify manifest and hashes on Pi
  -> read and hash the original 8 MB flash
  -> flash bring-up bundle
  -> capture UART report
  -> machine-check PASS and pin directions
  -> cold power-cycle and repeat
```

Before the first erase, `esptool` reads the complete 8 MB device flash. The workflow records the backup's SHA-256 and chip MAC and performs a byte-count check. It never commits the backup to Git. Restoring that backup is documented, but restoration is not performed during a passing bring-up.

The clean checkout must contain every declared build input. In particular, the currently missing `partitions_esp32tap.csv` source must be tracked or replaced by a tracked generation mechanism. Ignored files and retained build directories cannot satisfy this gate.

The Pi uses the isolated `/home/ssilver/.local/bin/esptool` installation and the stable `/dev/serial/by-id/...` path, never `/dev/ttyUSB0` directly. SSH uses the previously verified `rpi` host key even when connecting by IP.

## Physical layout

### Stage 0: USB only

- Raspberry Pi USB connects to the DevKit port labeled **UART**.
- The native port labeled **USB** remains disconnected.
- Every header pin remains disconnected.
- The DevKit is powered only through the UART USB cable.

### Stage 1: sidecar breadboard

The DevKit stays beside, not inserted into, the breadboard. Female-to-male jumpers keep header labels visible. One DevKit `G` pin feeds the breadboard ground rail; one `3V3` pin feeds the 3.3 V rail. The 5 V pin is not connected.

#### Exact netlist

| Net | Connections | Function |
|---|---|---|
| `BENCH_3V3` | DevKit J1-1 or J1-2 `3V3`; R1.1; R2.1; R3.1; S2.1 | 3.3 V breadboard rail |
| `BENCH_GND` | DevKit J3-21 or J3-22 `G`; S1A bypass throw; S1B emulate throw; R4.2; S3.2; R5.2; LED1.K; R6.2; LED2.K | Common ground rail |
| `NC_FB_SIM` | DevKit J1-4 / GPIO4; R1.2 (10 kΩ pull-up); S1A common | Relay NC feedback simulation |
| `NO_FB_SIM` | DevKit J1-5 / GPIO5; R2.2 (10 kΩ pull-up); S1B common | Relay NO feedback simulation |
| `TREAD_OK_SIM` | DevKit J1-6 / GPIO6; R4.1 (47 kΩ pull-down); R7.2 | Treadmill-power-valid simulation |
| `TREAD_OK_SWITCHED` | S2.2; R7.1 (1 kΩ series) | S2 closed drives `TREAD_OK_SIM` high |
| `VBUS_PRESENT_SIM_N` | DevKit J1-7 / GPIO7; R3.2 (10 kΩ pull-up); S3.1 | S3 closed drives active-low VBUS-present input low |
| `TX_ENABLE_TRIP` | DevKit J1-8 / GPIO15; R5.1 (47 kΩ pull-down); R8.1 | Protected-output observation |
| `TX_ENABLE_LED` | R8.2 (1 kΩ series); LED1.A | LED1 lights only if GPIO15 is wrongly driven high |
| `RELAY_CMD_TRIP` | DevKit J3-18 / GPIO21; R6.1 (47 kΩ pull-down); R9.1 | Protected-output observation |
| `RELAY_CMD_LED` | R9.2 (1 kΩ series); LED2.A | LED2 lights only if GPIO21 is wrongly driven high |

S1 is a DPDT ON-ON switch:

- Pole A common connects to `NC_FB_SIM`; its BYPASS throw connects to ground and its EMULATE throw is unconnected.
- Pole B common connects to `NO_FB_SIM`; its BYPASS throw is unconnected and its EMULATE throw connects to ground.

Therefore BYPASS reads GPIO4 low/GPIO5 high, and EMULATE reads GPIO4 high/GPIO5 low. S2 and S3 are SPST switches.

GPIO16, GPIO17, GPIO18, GPIO38, native USB, every treadmill wire, and the 5 V rail remain disconnected.

#### Bench BOM

- One solderless breadboard.
- Female-to-male Dupont jumpers.
- One DPDT ON-ON switch and two SPST switches.
- Three 10 kΩ resistors.
- Three 47 kΩ resistors.
- Three 1 kΩ resistors.
- Two visible LEDs.

## Verification

### Host gates

1. A clean-checkout build proves all inputs are tracked.
2. Unit/source-contract tests reject any output-mode configuration or internal pull on GPIO 15, 17, or 21.
3. Tests reject any reference from `devkit-bringup` to production control, UART, BLE, or network modules.
4. Image header, flash geometry, partition fit, binary identity, and manifest hashes are checked.
5. Existing pin-map and production safety gates remain green.

### Hardware gates

1. `esptool` re-identifies the expected ESP32-S3, N8R8 capabilities, and MAC.
2. The full 8 MB original flash backup completes, has the exact byte count, and receives a SHA-256 record.
3. Flashing succeeds only after Pi-side manifest verification.
4. UART emits exactly one complete diagnostic report ending in `BRINGUP PASS`.
5. A cold USB power cycle produces the same pass without manual BOOT-button intervention.
6. With Stage 1 installed, the parser observes both relay-simulator states and both SPST states correctly.
7. LED1 and LED2 remain dark through boot, switch operation, reset, and a five-minute observation window.

## Relationship to other work

- `precor-9_3x-344` is a required clean-build fix, not an exemption.
- Hardware Wi-Fi/provisioning remains separate from this first artifact (`precor-9_3x-9cz` and related production-readiness work).
- The fast-loop performance decision preserves required safety coverage and does not block this hardware milestone.
- Production BOM migration to N8R8 remains separate; this design targets the already-owned N8R8 development device.

## Completion boundary

This milestone is complete when the committed clean-checkout workflow produces a uniquely identified bring-up image, the factory flash backup is verified, the physical DevKit cold-boots twice with parsed `BRINGUP PASS`, and the approved sidecar harness exercises all four simulated inputs while both protected-output LEDs remain dark.

It does not claim treadmill connectivity, relay timing, motor UART electrical compatibility, Wi-Fi/API availability, BLE behavior, or production readiness.
