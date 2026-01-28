#!/usr/bin/env python3
"""
Simple command sender for PRECOR treadmill.

Usage:
  python3 send_cmd.py speed 3.5          # Set speed once
  python3 send_cmd.py speedloop 3.5      # Send speed repeatedly for 10s
  python3 send_cmd.py speedloop 3.5 30   # Send speed repeatedly for 30s
  python3 send_cmd.py incline 2.0        # Set incline once
  python3 send_cmd.py inclineloop 2.0    # Send incline repeatedly for 10s
  python3 send_cmd.py stop               # Set speed to 0
  python3 send_cmd.py raw 52 2A ...      # Send raw hex bytes
"""

import sys
import time

import serial

from protocol import (
    SERIAL_PORT, BAUD_RATE,
    build_set_spd, build_set_inc, decode_frames, hex_str, wait_for_gap,
)


def format_frames(data):
    """Format decoded frames for display."""
    if len(data) < 4:
        return f"(invalid: {hex_str(data)})"
    frames = decode_frames(data)
    if not frames:
        return "(no valid frames)"
    return "\n         ".join(f"{name}: {meaning} [{hex_str(raw)}]"
                              for name, meaning, raw in frames)


def send_packet(pkt):
    """Send packet and print result."""
    print(f"Sending: {hex_str(pkt)}")
    print(f"Decoded: {format_frames(pkt)}")
    print()

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
    time.sleep(0.05)

    print("Waiting for gap in bus traffic...")
    if wait_for_gap(ser, gap_ms=15):
        print("Gap found, sending now!")
    else:
        print("Warning: No clear gap found, sending anyway...")

    ser.write(pkt)
    ser.flush()

    print("Listening for response...")
    responses = bytearray()
    end_time = time.time() + 1.0

    while time.time() < end_time:
        if ser.in_waiting:
            responses.extend(ser.read(ser.in_waiting))
        time.sleep(0.01)

    if responses:
        print(f"\nBus traffic after send:")
        print(f"Raw:     {hex_str(responses)}")
        print(f"Decoded: {format_frames(bytes(responses))}")
    else:
        print("No response received")

    ser.close()
    print("\nDone.")


def send_repeated(pkt, duration=10.0, interval=0.1):
    """Send packet repeatedly for duration seconds."""
    print(f"Sending: {hex_str(pkt)}")
    print(f"Decoded: {format_frames(pkt)}")
    print(f"\nSending repeatedly for {duration}s (interval={interval * 1000:.0f}ms)")
    print("Press Ctrl+C to stop\n")

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    time.sleep(0.05)

    count = 0
    start = time.time()

    try:
        while time.time() - start < duration:
            if wait_for_gap(ser, gap_ms=10, timeout=0.5):
                ser.write(pkt)
                ser.flush()
                count += 1
                elapsed = time.time() - start
                print(f"\r  Sent {count} packets ({elapsed:.1f}s)", end='', flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nStopped by user")

    ser.close()
    print(f"\n\nDone. Sent {count} packets in {time.time() - start:.1f}s")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'speed' and len(sys.argv) >= 3:
        speed = float(sys.argv[2])
        print(f"Setting speed to {speed} mph")
        send_packet(build_set_spd(speed))

    elif cmd == 'speedloop' and len(sys.argv) >= 3:
        speed = float(sys.argv[2])
        duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        print(f"Setting speed to {speed} mph (repeated for {duration}s)")
        send_repeated(build_set_spd(speed), duration=duration)

    elif cmd == 'incline' and len(sys.argv) >= 3:
        incline = float(sys.argv[2])
        print(f"Setting incline to {incline}%")
        send_packet(build_set_inc(incline))

    elif cmd == 'inclineloop' and len(sys.argv) >= 3:
        incline = float(sys.argv[2])
        duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        print(f"Setting incline to {incline}% (repeated for {duration}s)")
        send_repeated(build_set_inc(incline), duration=duration)

    elif cmd == 'raw' and len(sys.argv) >= 3:
        pkt = bytes(int(arg, 16) for arg in sys.argv[2:])
        print("Sending raw packet")
        send_packet(pkt)

    elif cmd == 'stop':
        print("Sending STOP (speed = 0)")
        send_packet(build_set_spd(0.0))

    else:
        print(__doc__)
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
