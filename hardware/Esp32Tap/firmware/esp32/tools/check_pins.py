#!/usr/bin/env python3
"""check_pins.py — verify pins.hpp against the Rev E hardware sources.

Re-derives every GPIO constant in components/esp_hal/pins.hpp from
hardware/Esp32Tap/tools/design.py (NETS + COMPONENTS["U1"] pad map) and
fails with a nonzero exit on any mismatch. Also asserts that the
TREAD_OK_MCU pad is an input in PIN_TYPES (the firmware must never drive
it — R32 isolation, NETLIST.md finding B2).

Run as the first step of `make -C host test`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP32_DIR = HERE.parent
ESP32TAP_DIR = ESP32_DIR.parents[1]  # hardware/Esp32Tap
sys.path.insert(0, str(ESP32TAP_DIR / "tools"))

import design  # noqa: E402

PINS_HPP = ESP32_DIR / "components" / "esp_hal" / "pins.hpp"

# net name -> pins.hpp constant name
EXPECTED_NETS = {
    "CONS_RX": "kConsRx",
    "ESP_TX": "kEspTx",
    "PIN3_RX": "kPin3Rx",
    "TREAD_OK_MCU": "kTreadOkMcu",
    "RELAY_CMD": "kRelayCmd",
    "TX_ENABLE": "kTxEnable",
    "K1_NC_FB": "kK1NcFb",
    "K1_NO_FB": "kK1NoFb",
    "VBUS_PRESENT_N": "kVbusPresentN",
    "STATUS_LED": "kStatusLed",
}

# Nets that must be MCU inputs (never driven by firmware)
INPUT_ONLY_NETS = {"TREAD_OK_MCU", "K1_NC_FB", "K1_NO_FB", "VBUS_PRESENT_N", "CONS_RX", "PIN3_RX"}
# Nets that must be MCU outputs
OUTPUT_NETS = {"RELAY_CMD", "TX_ENABLE", "ESP_TX", "STATUS_LED"}


def u1_pad_for_net(net: str) -> str:
    pads = [pad for (ref, pad) in design.NETS[net] if ref == "U1"]
    if len(pads) != 1:
        raise SystemExit(f"check_pins: net {net} has {len(pads)} U1 pads (expected 1)")
    return pads[0]


def gpio_for_pad(pad: str) -> int:
    pad_names = design.COMPONENTS["U1"][7]
    name = pad_names[pad]
    m = re.search(r"IO(\d+)", name)
    if m is None:
        raise SystemExit(f"check_pins: U1 pad {pad} ({name}) is not a GPIO pad")
    return int(m.group(1))


def parse_pins_hpp() -> dict[str, int]:
    text = PINS_HPP.read_text(encoding="utf-8")
    found: dict[str, int] = {}
    for m in re.finditer(r"inline constexpr int (k\w+) = (\d+);", text):
        found[m.group(1)] = int(m.group(2))
    return found


def main() -> int:
    errors: list[str] = []
    found = parse_pins_hpp()

    for net, const in EXPECTED_NETS.items():
        pad = u1_pad_for_net(net)
        expected_gpio = gpio_for_pad(pad)
        actual = found.get(const)
        if actual is None:
            errors.append(f"pins.hpp missing constant {const} (net {net})")
        elif actual != expected_gpio:
            errors.append(f"{const} = {actual} but design.py says net {net} is " f"U1 pad {pad} = GPIO{expected_gpio}")

        pin_type = design.PIN_TYPES.get(("U1", pad))
        if net in INPUT_ONLY_NETS and pin_type != "input":
            errors.append(
                f"net {net}: U1 pad {pad} PIN_TYPES is {pin_type!r}, " "expected 'input' (firmware treats it read-only)"
            )
        if net in OUTPUT_NETS and pin_type != "output":
            errors.append(f"net {net}: U1 pad {pad} PIN_TYPES is {pin_type!r}, " "expected 'output'")

    extra = set(found) - set(EXPECTED_NETS.values())
    if extra:
        errors.append(f"pins.hpp has unchecked constants: {sorted(extra)} — add them " "to check_pins.py EXPECTED_NETS")

    if errors:
        print("check_pins: FAIL", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"check_pins: OK ({len(EXPECTED_NETS)} nets verified against design.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
