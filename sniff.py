#!/usr/bin/env python3
"""
Simple packet sniffer - shows unique packets as hex bytes.
Usage: python3 sniff.py [seconds]
"""

import serial
import time
import sys

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    buf = bytearray()
    seen = set()
    start = time.time()

    print(f"Sniffing for {duration}s... (Ctrl+C to stop early)")
    print()

    try:
        while time.time() - start < duration:
            if ser.in_waiting:
                buf.extend(ser.read(ser.in_waiting))

            i = 0
            while i < len(buf) - 3:
                if buf[i] == 0x52:
                    for j in range(i + 1, min(i + 50, len(buf) - 1)):
                        if buf[j] == 0x45 and buf[j+1] == 0x01:
                            frame = bytes(buf[i:j+2])

                            if frame not in seen:
                                seen.add(frame)
                                hex_str = ' '.join(f'{b:02X}' for b in frame)
                                print(hex_str)

                            buf = buf[j+2:]
                            i = 0
                            break
                    else:
                        i += 1
                else:
                    i += 1
                    if i > 100:
                        buf = buf[i:]
                        i = 0

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    ser.close()
    print()
    print(f"Done. {len(seen)} unique packets.")

if __name__ == "__main__":
    main()
