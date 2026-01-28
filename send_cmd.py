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

import serial
import sys
import time

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

# Custom base-16 digit set
DIGITS = [0x9F, 0x9D, 0x9B, 0x99, 0x97, 0x95, 0x93, 0x91,
          0x8F, 0x8D, 0x7D, 0x7B, 0x79, 0x77, 0x75, 0x73]
DIGIT_TO_VAL = {b: i for i, b in enumerate(DIGITS)}

NAMES = {
    0x2A: 'SET_SPD', 0x4B: 'SET_INC', 0x4F: 'DISP1', 0x51: 'DISP2',
    0x52: 'UNK_52', 0x54: 'UNK_54', 0x9A: 'UNK_9A', 0xA2: 'UNK_A2',
    0xAA: 'UNK_AA', 0xD4: 'UNK_D4'
}

def encode_base16(value):
    """Encode integer to custom base-16 bytes."""
    if value == 0:
        return bytes([DIGITS[0]])
    elif value < 16:
        return bytes([DIGITS[value]])
    elif value < 256:
        return bytes([DIGITS[value // 16], DIGITS[value % 16]])
    else:
        return bytes([DIGITS[value // 256],
                      DIGITS[(value // 16) % 16],
                      DIGITS[value % 16]])

def decode_base16(payload):
    """Decode custom base-16 bytes to integer."""
    result = 0
    for b in payload:
        if b not in DIGIT_TO_VAL:
            return None
        result = result * 16 + DIGIT_TO_VAL[b]
    return result

def decode_speed(raw_bytes):
    """Decode speed from raw bytes. Returns mph or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 100.0

def decode_incline(raw_bytes):
    """Decode incline from raw bytes. Returns percent or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 2.0

def build_speed_packet(speed_mph):
    """Build a SET_SPD packet."""
    # SET_SPD: 52 2A 1F 2F 8B [speed payload] 45 01
    hundredths = round(speed_mph * 100)
    speed_bytes = encode_base16(hundredths)
    pkt = bytes([0x52, 0x2A, 0x1F, 0x2F, 0x8B]) + speed_bytes + bytes([0x45, 0x01])
    return pkt

def build_incline_packet(incline_pct):
    """Build a SET_INC packet."""
    # SET_INC: 52 4B CA 5A [incline payload] 45 01
    half_pct = round(incline_pct * 2)
    incline_bytes = encode_base16(half_pct)
    pkt = bytes([0x52, 0x4B, 0xCA, 0x5A]) + incline_bytes + bytes([0x45, 0x01])
    return pkt

def hex_str(data):
    return ' '.join(f'{b:02X}' for b in data)

def decode_packet(data):
    """Decode a packet and return human-readable string."""
    if len(data) < 4:
        return f"(too short: {len(data)} bytes)"

    if data[0] != 0x52:
        return "(doesn't start with 0x52)"

    # Find frame end (45 01)
    frames = []
    buf = bytearray(data)

    while len(buf) >= 4:
        if buf[0] != 0x52:
            buf = buf[1:]
            continue

        found = False
        for j in range(2, min(50, len(buf) - 1)):
            if buf[j] == 0x45 and buf[j+1] == 0x01:
                # Found a frame
                ftype = buf[1]
                payload = bytes(buf[2:j])
                frame_bytes = bytes(buf[:j+2])

                name = NAMES.get(ftype, f'0x{ftype:02X}')
                meaning = decode_meaning(ftype, payload)

                frames.append(f"{name}: {meaning} [{hex_str(frame_bytes)}]")
                buf = buf[j+2:]
                found = True
                break

        if not found:
            break

    if frames:
        return "\n         ".join(frames)
    return "(no valid frames found)"

def decode_meaning(ftype, payload):
    """Decode the meaning of a packet payload."""
    if ftype == 0x2A:  # SET_SPD
        if len(payload) >= 4 and payload[0] == 0x1F and payload[1] == 0x2F and payload[2] == 0x8B:
            speed_bytes = payload[3:]
            speed = decode_speed(speed_bytes)
            if speed is not None:
                return f"speed={speed:.1f}mph"
        return f"UNKNOWN payload: {hex_str(payload)}"

    elif ftype == 0x4B:  # SET_INC
        if len(payload) >= 3 and payload[0] == 0xCA and payload[1] == 0x5A:
            incline_bytes = payload[2:]
            if len(incline_bytes) == 0:
                return "incl=0%"
            incline = decode_incline(incline_bytes)
            if incline is not None:
                return f"incl={incline:.1f}%"
        return f"UNKNOWN payload: {hex_str(payload)}"

    elif ftype in (0x4F, 0x51):  # Display
        ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in payload)
        return f"display: '{ascii_str}'"

    else:
        return f"UNK: {hex_str(payload)}"

def wait_for_gap(ser, gap_ms=20, timeout=2.0):
    """Wait for a gap in bus traffic before sending."""
    start = time.time()
    last_byte_time = time.time()

    # First, drain any pending data
    if ser.in_waiting:
        ser.read(ser.in_waiting)
        last_byte_time = time.time()

    while time.time() - start < timeout:
        if ser.in_waiting:
            ser.read(ser.in_waiting)
            last_byte_time = time.time()
        elif (time.time() - last_byte_time) * 1000 >= gap_ms:
            # Found a gap!
            return True
        time.sleep(0.001)

    return False

def send_packet(pkt):
    """Send packet and print result."""
    print(f"Sending: {hex_str(pkt)}")
    print(f"Decoded: {decode_packet(pkt)}")
    print()

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
    time.sleep(0.05)  # Let serial settle

    # Wait for a gap in traffic
    print("Waiting for gap in bus traffic...")
    if wait_for_gap(ser, gap_ms=15):
        print("Gap found, sending now!")
    else:
        print("Warning: No clear gap found, sending anyway...")

    ser.write(pkt)
    ser.flush()

    # Collect responses for a bit
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
        print(f"Decoded: {decode_packet(bytes(responses))}")
    else:
        print("No response received")

    ser.close()
    print("\nDone.")

def send_repeated(pkt, duration=10.0, interval=0.1):
    """Send packet repeatedly for duration seconds."""
    print(f"Sending: {hex_str(pkt)}")
    print(f"Decoded: {decode_packet(pkt)}")
    print(f"\nSending repeatedly for {duration}s (interval={interval*1000:.0f}ms)")
    print("Press Ctrl+C to stop\n")

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    time.sleep(0.05)

    count = 0
    start = time.time()

    try:
        while time.time() - start < duration:
            # Wait for gap then send
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
    print(f"\n\nDone. Sent {count} packets in {time.time()-start:.1f}s")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'speed' and len(sys.argv) >= 3:
        speed = float(sys.argv[2])
        print(f"Setting speed to {speed} mph")
        pkt = build_speed_packet(speed)
        send_packet(pkt)

    elif cmd == 'speedloop' and len(sys.argv) >= 3:
        speed = float(sys.argv[2])
        duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        print(f"Setting speed to {speed} mph (repeated for {duration}s)")
        pkt = build_speed_packet(speed)
        send_repeated(pkt, duration=duration, interval=0.1)

    elif cmd == 'incline' and len(sys.argv) >= 3:
        incline = float(sys.argv[2])
        print(f"Setting incline to {incline}%")
        pkt = build_incline_packet(incline)
        send_packet(pkt)

    elif cmd == 'inclineloop' and len(sys.argv) >= 3:
        incline = float(sys.argv[2])
        duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        print(f"Setting incline to {incline}% (repeated for {duration}s)")
        pkt = build_incline_packet(incline)
        send_repeated(pkt, duration=duration, interval=0.1)

    elif cmd == 'raw' and len(sys.argv) >= 3:
        # Parse hex bytes from command line
        hex_bytes = []
        for arg in sys.argv[2:]:
            hex_bytes.append(int(arg, 16))
        pkt = bytes(hex_bytes)
        print("Sending raw packet")
        send_packet(pkt)

    elif cmd == 'stop':
        print("Sending STOP (speed = 0)")
        pkt = build_speed_packet(0.0)
        send_packet(pkt)

    else:
        print(__doc__)
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
