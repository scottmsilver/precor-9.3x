#!/usr/bin/env python3
"""
Read treadmill serial via Raspberry Pi GPIO pins using pigpio bit-banged UART.

The treadmill uses RS-485 signaling (idle=LOW, inverted polarity).
pigpio's bb_serial_read with invert=1 handles this natively.

Pin assignments are configured in gpio.json.

Usage:
  sudo pigpiod              # start daemon first
  python3 gpio_serial.py    # read both pins
  python3 gpio_serial.py --pin6-only
  python3 gpio_serial.py --pin3-only
  python3 gpio_serial.py --raw

Requires: pigpio, pigpiod running
"""

import argparse
import time
import pigpio

from gpio_pins import get_gpio, BAUD, parse_kv_stream, gpio_read_open, gpio_read_close


def main():
    parser = argparse.ArgumentParser(description='Read treadmill serial via GPIO')
    parser.add_argument('--gpio-pin6', type=int,
                        default=get_gpio('console_read'),
                        help='GPIO for treadmill pin 6 (console side)')
    parser.add_argument('--gpio-pin3', type=int,
                        default=get_gpio('motor_read'),
                        help='GPIO for treadmill pin 3 (motor responses)')
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
        gpio_read_open(pi, gpio6, args.baud)
        channels.append(('PIN6 TX', gpio6, bytearray()))
        print(f"Listening on GPIO {gpio6} (pin 6, console→motor) at {args.baud} baud, inverted")

    if not args.pin6_only:
        gpio3 = args.gpio_pin3
        gpio_read_open(pi, gpio3, args.baud)
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
                        pairs, remaining = parse_kv_stream(buf)
                        for key, val in pairs:
                            ts = time.strftime('%H:%M:%S')
                            if val:
                                print(f"  {ts}  {label}  [{key}:{val}]")
                            else:
                                print(f"  {ts}  {label}  [{key}]")
                        buf.clear()
                        buf.extend(remaining)
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\nStopping...")

    finally:
        for label, gpio, buf in channels:
            gpio_read_close(pi, gpio)
            print(f"  Closed GPIO {gpio} ({label})")
        pi.stop()
        print("Done.")

    return 0


if __name__ == '__main__':
    exit(main())
