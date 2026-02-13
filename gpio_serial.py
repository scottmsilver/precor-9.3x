#!/usr/bin/env python3
"""
Read treadmill serial via Raspberry Pi GPIO pins using pigpio bit-banged UART.

The treadmill uses RS-485 signaling (idle=LOW, inverted polarity).
pigpio's bb_serial_read with invert=1 handles this natively.

Wiring:
  Treadmill pin 6 (console→motor) → GPIO 17 (physical pin 11)
  Treadmill pin 3 (motor→console) → GPIO 27 (physical pin 13)
  Treadmill GND                   → Pi GND  (physical pin 14)

Usage:
  sudo pigpiod              # start daemon first
  python3 gpio_serial.py    # read both pins
  python3 gpio_serial.py --pin6-only
  python3 gpio_serial.py --pin3-only
  python3 gpio_serial.py --gpio-pin6 17 --gpio-pin3 27  # custom GPIO pins

Requires: pigpio (pip install pigpio), pigpiod running
"""

import argparse
import time
import pigpio

BAUD = 9600

# Default GPIO pins (BCM numbering)
DEFAULT_GPIO_PIN6 = 17  # physical pin 11
DEFAULT_GPIO_PIN3 = 27  # physical pin 13


def parse_kv(buf):
    """Extract [key:value] pairs from buffer. Returns (pairs, remaining)."""
    pairs = []
    text = buf.decode('latin-1', errors='replace')
    i = 0
    last_consumed = 0
    while i < len(text):
        start = text.find('[', i)
        if start == -1:
            break
        end = text.find(']', start)
        if end == -1:
            break
        pair = text[start:end+1]
        pairs.append(pair)
        last_consumed = end + 1
        # Skip optional 0xFF delimiter
        if last_consumed < len(text) and ord(text[last_consumed]) == 0xFF:
            last_consumed += 1
        i = last_consumed
    remaining = buf[last_consumed:] if last_consumed < len(buf) else b''
    return pairs, remaining


def main():
    parser = argparse.ArgumentParser(description='Read treadmill serial via GPIO')
    parser.add_argument('--gpio-pin6', type=int, default=DEFAULT_GPIO_PIN6,
                        help=f'GPIO pin for treadmill pin 6 (default: {DEFAULT_GPIO_PIN6})')
    parser.add_argument('--gpio-pin3', type=int, default=DEFAULT_GPIO_PIN3,
                        help=f'GPIO pin for treadmill pin 3 (default: {DEFAULT_GPIO_PIN3})')
    parser.add_argument('--pin6-only', action='store_true',
                        help='Only read pin 6 (console→motor)')
    parser.add_argument('--pin3-only', action='store_true',
                        help='Only read pin 3 (motor→console)')
    parser.add_argument('--raw', action='store_true',
                        help='Show raw hex instead of parsed KV')
    parser.add_argument('--baud', type=int, default=BAUD,
                        help=f'Baud rate (default: {BAUD})')
    args = parser.parse_args()

    pi = pigpio.pi()
    if not pi.connected:
        print("ERROR: Cannot connect to pigpiod. Run: sudo pigpiod")
        return 1

    channels = []

    if not args.pin3_only:
        gpio6 = args.gpio_pin6
        # bb_serial_read_open(gpio, baud, data_bits)
        # invert=1 handles RS-485 polarity (idle=LOW, start=rising)
        pi.bb_serial_read_open(gpio6, args.baud, 8)
        pi.bb_serial_invert(gpio6, 1)
        channels.append(('PIN6 TX', gpio6, bytearray()))
        print(f"Listening on GPIO {gpio6} (pin 6, console→motor) at {args.baud} baud, inverted")

    if not args.pin6_only:
        gpio3 = args.gpio_pin3
        pi.bb_serial_read_open(gpio3, args.baud, 8)
        pi.bb_serial_invert(gpio3, 1)
        channels.append(('PIN3 RX', gpio3, bytearray()))
        print(f"Listening on GPIO {gpio3} (pin 3, motor→console) at {args.baud} baud, inverted")

    if not channels:
        print("No channels selected!")
        pi.stop()
        return 1

    print("\nListening... (Ctrl+C to stop)\n")

    try:
        while True:
            for label, gpio, buf in channels:
                count, data = pi.bb_serial_read(gpio)
                if count > 0:
                    if args.raw:
                        hex_str = ' '.join(f'{b:02X}' for b in data)
                        print(f"  {label} ({count:3d}B): {hex_str}")
                    else:
                        buf.extend(data)
                        pairs, remaining = parse_kv(buf)
                        for pair in pairs:
                            ts = time.strftime('%H:%M:%S')
                            print(f"  {ts}  {label}  {pair}")
                        buf.clear()
                        buf.extend(remaining)
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\nStopping...")

    finally:
        for label, gpio, buf in channels:
            pi.bb_serial_read_close(gpio)
            print(f"  Closed GPIO {gpio} ({label})")
        pi.stop()
        print("Done.")

    return 0


if __name__ == '__main__':
    exit(main())
