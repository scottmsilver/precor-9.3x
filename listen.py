#!/usr/bin/env python3
"""
Simple serial listener - show raw bytes as they arrive.

Usage:
    python3 listen.py                        # Listen on controller_tx (default)
    python3 listen.py motor_rx               # Listen on a named port
    python3 listen.py /dev/ttyUSB0           # Listen on a device path
    python3 listen.py /dev/ttyACM1 --changes # Only show when data changes
"""

import argparse
import sys
import time

import serial

from ports import get_port
from protocol import FRAME_START, NAMES, decode_packet, hex_str

BAUD_DEFAULT = 9600


def listen(port, baud):
    dev = get_port(port)
    ser = serial.Serial(dev, baud, timeout=0)
    ser.reset_input_buffer()

    print(f"Listening on {dev} ({port}) at {baud} baud")
    print(f"Press Ctrl+C to stop")
    print()
    print(f"{'TIME':>10}  {'LEN':>4}  {'HEX':<48}  ASCII")
    print("-" * 80)

    start = time.time()
    total = 0

    try:
        while True:
            data = ser.read(256)
            if data:
                t = (time.time() - start) * 1000
                total += len(data)
                h = ' '.join(f'{b:02X}' for b in data[:40])
                a = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data[:40])
                print(f"{t:10.1f}  {len(data):>4}  {h:<48}  {a}")
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print(f"\nTotal: {total} bytes")
    ser.close()


def parse_kv_fields(buf):
    """Parse [key:value] fields delimited by 0xFF. Returns list of (key, value) and remaining buf."""
    fields = []
    while 0xFF in buf:
        idx = buf.index(0xFF)
        chunk = buf[:idx]
        buf = buf[idx + 1:]
        text = bytes(chunk).decode('ascii', errors='replace').strip()
        if text.startswith('[') and text.endswith(']'):
            text = text[1:-1]
        if ':' in text:
            k, v = text.split(':', 1)
            fields.append((k, v))
        elif text:
            fields.append((text, ''))
    return fields, buf


def listen_parsed(port, baud):
    dev = get_port(port)
    ser = serial.Serial(dev, baud, timeout=0)
    ser.reset_input_buffer()

    print(f"Listening on {dev} ({port}) at {baud} baud (parsed)")
    print(f"Press Ctrl+C to stop")
    print()

    start = time.time()
    buf = bytearray()
    total = 0
    count = 0

    # Auto-detect: sniff first bytes to decide format
    detected = None

    try:
        while True:
            data = ser.read(256)
            if data:
                total += len(data)
                buf.extend(data)

                # Auto-detect on first data
                if detected is None and len(buf) > 10:
                    if 0xFF in buf and b'[' in buf:
                        detected = 'kv'
                        print("Detected: [key:value] format (0xFF delimited)")
                        print()
                    elif FRAME_START in buf:
                        detected = 'binary'
                        print("Detected: binary R...E frame format")
                        print(f"{'#':>5}  {'TIME':>10}  {'TYPE':<8}  {'RAW':<48}  MEANING")
                        print("-" * 90)

                if detected == 'kv':
                    fields, buf = parse_kv_fields(buf)
                    for key, val in fields:
                        count += 1
                        t = (time.time() - start) * 1000
                        if val:
                            print(f"{t:10.1f}  {key:<12} = {val}")
                        else:
                            print(f"{t:10.1f}  {key:<12}")

                elif detected == 'binary':
                    i = 0
                    while i < len(buf) - 3:
                        if buf[i] == FRAME_START:
                            for j in range(i + 1, min(i + 50, len(buf) - 1)):
                                if buf[j] == 0x45 and buf[j + 1] == 0x01:
                                    raw = bytes(buf[i:j + 2])
                                    ftype = buf[i + 1]
                                    payload = bytes(buf[i + 2:j])
                                    name = NAMES.get(ftype, f'0x{ftype:02X}')
                                    meaning, _ = decode_packet(ftype, payload)
                                    count += 1
                                    t = (time.time() - start) * 1000
                                    print(f"{count:>5}  {t:10.1f}  {name:<8}  {hex_str(raw):<48}  {meaning}")
                                    buf = buf[j + 2:]
                                    i = 0
                                    break
                            else:
                                i += 1
                        else:
                            i += 1
                            if i > 100:
                                buf = buf[i:]
                                i = 0
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print(f"\nTotal: {total} bytes, {count} fields")
    ser.close()


def listen_changes(port, baud):
    dev = get_port(port)
    ser = serial.Serial(dev, baud, timeout=0)
    ser.reset_input_buffer()

    print(f"Listening on {dev} ({port}) at {baud} baud (changes only)")
    print(f"Press Ctrl+C to stop")
    print()

    start = time.time()
    buf = bytearray()
    last_seen = {}
    total = 0
    shown = 0
    detected = None

    try:
        while True:
            data = ser.read(256)
            if data:
                total += len(data)
                buf.extend(data)

                if detected is None and len(buf) > 10:
                    if 0xFF in buf and b'[' in buf:
                        detected = 'kv'
                        print("Detected: [key:value] format (0xFF delimited)")
                        print()
                    elif FRAME_START in buf:
                        detected = 'binary'
                        print("Detected: binary R...E frame format")
                        print()

                if detected == 'kv':
                    fields, buf = parse_kv_fields(buf)
                    for key, val in fields:
                        if last_seen.get(key) != val:
                            last_seen[key] = val
                            shown += 1
                            t = (time.time() - start) * 1000
                            if val:
                                print(f"{t:10.1f}  {key:<12} = {val}")
                            else:
                                print(f"{t:10.1f}  {key:<12}")

                elif detected == 'binary':
                    i = 0
                    while i < len(buf) - 3:
                        if buf[i] == FRAME_START:
                            for j in range(i + 1, min(i + 50, len(buf) - 1)):
                                if buf[j] == 0x45 and buf[j + 1] == 0x01:
                                    raw = bytes(buf[i:j + 2])
                                    ftype = buf[i + 1]
                                    payload = bytes(buf[i + 2:j])
                                    name = NAMES.get(ftype, f'0x{ftype:02X}')
                                    if last_seen.get(ftype) != payload:
                                        last_seen[ftype] = payload
                                        shown += 1
                                        t = (time.time() - start) * 1000
                                        meaning, _ = decode_packet(ftype, payload)
                                        print(f"{t:10.1f}  {name:<8}  {hex_str(raw):<48}  {meaning}")
                                    buf = buf[j + 2:]
                                    i = 0
                                    break
                            else:
                                i += 1
                        else:
                            i += 1
                            if i > 100:
                                buf = buf[i:]
                                i = 0
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print(f"\nTotal: {total} bytes, {shown} changes shown")
    ser.close()


def listen_unique(port, baud):
    dev = get_port(port)
    ser = serial.Serial(dev, baud, timeout=0)
    ser.reset_input_buffer()

    print(f"Listening on {dev} ({port}) at {baud} baud (unique only)")
    print(f"Press Ctrl+C to stop")
    print()

    start = time.time()
    buf = bytearray()
    seen = set()
    total = 0
    shown = 0
    count = 0
    detected = None

    try:
        while True:
            data = ser.read(256)
            if data:
                total += len(data)
                buf.extend(data)

                if detected is None and len(buf) > 10:
                    if 0xFF in buf and b'[' in buf:
                        detected = 'kv'
                        print("Detected: [key:value] format (0xFF delimited)")
                        print()
                    elif FRAME_START in buf:
                        detected = 'binary'
                        print("Detected: binary R...E frame format")
                        print(f"{'#':>5}  {'TIME':>10}  {'TYPE':<8}  {'RAW':<48}  MEANING")
                        print("-" * 90)

                if detected == 'kv':
                    fields, buf = parse_kv_fields(buf)
                    for key, val in fields:
                        count += 1
                        pair = (key, val)
                        if pair not in seen:
                            seen.add(pair)
                            shown += 1
                            t = (time.time() - start) * 1000
                            if val:
                                print(f"{t:10.1f}  {key:<12} = {val}  ({shown} unique / {count} total)")
                            else:
                                print(f"{t:10.1f}  {key:<12}  ({shown} unique / {count} total)")

                elif detected == 'binary':
                    i = 0
                    while i < len(buf) - 3:
                        if buf[i] == FRAME_START:
                            for j in range(i + 1, min(i + 50, len(buf) - 1)):
                                if buf[j] == 0x45 and buf[j + 1] == 0x01:
                                    raw = bytes(buf[i:j + 2])
                                    ftype = buf[i + 1]
                                    payload = bytes(buf[i + 2:j])
                                    count += 1
                                    pair = (ftype, payload)
                                    if pair not in seen:
                                        seen.add(pair)
                                        shown += 1
                                        name = NAMES.get(ftype, f'0x{ftype:02X}')
                                        meaning, _ = decode_packet(ftype, payload)
                                        t = (time.time() - start) * 1000
                                        print(f"{shown:>5}  {t:10.1f}  {name:<8}  {hex_str(raw):<48}  {meaning}")
                                    buf = buf[j + 2:]
                                    i = 0
                                    break
                            else:
                                i += 1
                        else:
                            i += 1
                            if i > 100:
                                buf = buf[i:]
                                i = 0
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print(f"\nTotal: {total} bytes, {shown} unique / {count} total")
    ser.close()


def main():
    parser = argparse.ArgumentParser(description='Listen on a serial port and show raw bytes')
    parser.add_argument('port', nargs='?', default='controller_tx',
                        help='Port name from ports.json or device path (default: controller_tx)')
    parser.add_argument('--baud', '-b', type=int, default=BAUD_DEFAULT,
                        help=f'Baud rate (default: {BAUD_DEFAULT})')
    parser.add_argument('--parse', '-p', action='store_true',
                        help='Parse frames and decode them')
    parser.add_argument('--changes', '-c', action='store_true',
                        help='Parse frames and only show when a frame type\'s payload changes')
    parser.add_argument('--unique', '-u', action='store_true',
                        help='Only show each unique (key, value) pair once ever')
    args = parser.parse_args()

    if args.unique:
        listen_unique(args.port, args.baud)
    elif args.changes:
        listen_changes(args.port, args.baud)
    elif args.parse:
        listen_parsed(args.port, args.baud)
    else:
        listen(args.port, args.baud)


if __name__ == "__main__":
    main()
