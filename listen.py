#!/usr/bin/env python3
"""
Simple KV protocol listener via GPIO.

Usage:
    python3 listen.py                  # Listen on console_read (default)
    python3 listen.py motor_read       # Listen on motor responses
    python3 listen.py --changes        # Only show when values change
    python3 listen.py --unique         # Only show unique key:value pairs
"""

import argparse
import time
import pigpio

from gpio_pins import get_gpio, BAUD, parse_kv_stream, gpio_read_open, gpio_read_close


def listen(pi, gpio, baud, mode='all'):
    """Listen on a GPIO pin and display KV pairs."""
    gpio_read_open(pi, gpio, baud)

    print(f"Listening on GPIO {gpio} at {baud} baud (inverted)")
    print(f"Mode: {mode}")
    print(f"Press Ctrl+C to stop\n")

    start = time.time()
    buf = bytearray()
    last_seen = {}
    seen = set()
    total = 0
    shown = 0

    try:
        while True:
            count, data = pi.bb_serial_read(gpio)
            if count > 0:
                total += count
                buf.extend(data)
                pairs, buf = parse_kv_stream(buf)
                for key, val in pairs:
                    t = (time.time() - start) * 1000

                    if mode == 'changes':
                        if last_seen.get(key) == val:
                            continue
                        last_seen[key] = val
                    elif mode == 'unique':
                        pair = (key, val)
                        if pair in seen:
                            continue
                        seen.add(pair)

                    shown += 1
                    if val:
                        print(f"{t:10.1f}  {key:<12} = {val}")
                    else:
                        print(f"{t:10.1f}  {key:<12}")
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass

    gpio_read_close(pi, gpio)
    print(f"\nTotal: {total} bytes, {shown} entries shown")


def main():
    parser = argparse.ArgumentParser(description='Listen on a GPIO pin for KV protocol')
    parser.add_argument('pin', nargs='?', default='console_read',
                        help='Pin name from gpio.json (default: console_read)')
    parser.add_argument('--baud', '-b', type=int, default=BAUD,
                        help=f'Baud rate (default: {BAUD})')
    parser.add_argument('--changes', '-c', action='store_true',
                        help='Only show when a key\'s value changes')
    parser.add_argument('--unique', '-u', action='store_true',
                        help='Only show each unique (key, value) pair once')
    args = parser.parse_args()

    gpio = get_gpio(args.pin)

    pi = pigpio.pi()
    if not pi.connected:
        print("ERROR: Cannot connect to pigpiod. Run: sudo pigpiod")
        return 1

    mode = 'changes' if args.changes else 'unique' if args.unique else 'all'

    try:
        listen(pi, gpio, args.baud, mode)
    finally:
        pi.stop()


if __name__ == "__main__":
    main()
