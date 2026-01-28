#!/usr/bin/env python3
"""
PRECOR 9.3x Protocol Library

Shared constants and functions for encoding/decoding the treadmill serial protocol.
"""

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

# Frame markers
FRAME_START = 0x52  # 'R'
FRAME_END = bytes([0x45, 0x01])  # 'E' + 0x01

# Known packet types
TYPE_SET_SPD = 0x2A  # Speed command/status
TYPE_SET_INC = 0x4B  # Incline command/status
TYPE_DISP1 = 0x4F    # Display segment 1
TYPE_DISP2 = 0x51    # Display segment 2

# Packet type names
NAMES = {
    0x2A: 'SET_SPD',
    0x4B: 'SET_INC',
    0x4F: 'DISP1',
    0x51: 'DISP2',
    0x52: 'UNK_52',
    0x54: 'UNK_54',
    0x9A: 'UNK_9A',
    0xA2: 'UNK_A2',
    0xAA: 'UNK_AA',
    0xD4: 'UNK_D4',
}

# Custom base-16 digit set for speed/incline encoding
# Value:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
DIGITS = [0x9F, 0x9D, 0x9B, 0x99, 0x97, 0x95, 0x93, 0x91,
          0x8F, 0x8D, 0x7D, 0x7B, 0x79, 0x77, 0x75, 0x73]
DIGIT_TO_VAL = {b: i for i, b in enumerate(DIGITS)}

# Packet headers (after type byte)
SET_SPD_HEADER = bytes([0x1F, 0x2F, 0x8B])
SET_INC_HEADER = bytes([0xCA, 0x5A])


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
    """Decode custom base-16 bytes to integer. Returns None if invalid."""
    result = 0
    for b in payload:
        if b not in DIGIT_TO_VAL:
            return None
        result = result * 16 + DIGIT_TO_VAL[b]
    return result


def encode_speed(mph):
    """Encode speed in mph to protocol bytes."""
    hundredths = round(mph * 100)
    return encode_base16(hundredths)


def decode_speed(raw_bytes):
    """Decode speed from protocol bytes. Returns mph or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 100.0


def encode_incline(percent):
    """Encode incline in percent to protocol bytes."""
    half_pct = round(percent * 2)
    return encode_base16(half_pct)


def decode_incline(raw_bytes):
    """Decode incline from protocol bytes. Returns percent or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 2.0


def build_set_spd(speed_mph):
    """Build a complete SET_SPD packet."""
    speed_bytes = encode_speed(speed_mph)
    return bytes([FRAME_START, TYPE_SET_SPD]) + SET_SPD_HEADER + speed_bytes + FRAME_END


def build_set_inc(incline_pct):
    """Build a complete SET_INC packet."""
    incline_bytes = encode_incline(incline_pct)
    return bytes([FRAME_START, TYPE_SET_INC]) + SET_INC_HEADER + incline_bytes + FRAME_END


def parse_frame(data):
    """
    Parse a frame from data buffer.
    Returns (frame_type, payload, remaining_data) or (None, None, data) if no valid frame.
    """
    if len(data) < 4:
        return None, None, data

    if data[0] != FRAME_START:
        # Skip until we find frame start
        for i in range(1, len(data)):
            if data[i] == FRAME_START:
                return None, None, data[i:]
        return None, None, bytes()

    # Look for frame end
    for j in range(2, min(50, len(data) - 1)):
        if data[j] == 0x45 and data[j + 1] == 0x01:
            frame_type = data[1]
            payload = bytes(data[2:j])
            remaining = data[j + 2:]
            return frame_type, payload, remaining

    return None, None, data


def hex_str(data):
    """Format bytes as hex string."""
    return ' '.join(f'{b:02X}' for b in data)


def decode_packet(frame_type, payload):
    """
    Decode a packet payload to human-readable string.
    Returns (meaning, is_unknown).
    """
    if frame_type == TYPE_SET_SPD:
        # Header is 3 bytes (1F 2F 8B) + at least 1 byte speed = 4 min
        if len(payload) >= 4 and payload[:3] == SET_SPD_HEADER:
            speed = decode_speed(payload[3:])
            if speed is not None:
                return f"speed={speed:.1f}mph", False
        return f"SET_SPD: unknown format", True

    elif frame_type == TYPE_SET_INC:
        if len(payload) >= 2 and payload[:2] == SET_INC_HEADER:
            if len(payload) == 2:
                return "incl=0%", False
            incline = decode_incline(payload[2:])
            if incline is not None:
                return f"incl={incline:.1f}%", False
        return f"SET_INC: unknown format", True

    elif frame_type in (TYPE_DISP1, TYPE_DISP2):
        ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in payload)
        return f"'{ascii_str}'", False

    else:
        name = NAMES.get(frame_type, f'0x{frame_type:02X}')
        return f"{name}: {len(payload)} bytes", True


def decode_frames(data):
    """
    Decode all frames from a data buffer.
    Returns list of (name, meaning, frame_bytes) tuples.
    """
    frames = []
    buf = bytearray(data)

    while len(buf) >= 4:
        if buf[0] != FRAME_START:
            buf = buf[1:]
            continue

        found = False
        for j in range(2, min(50, len(buf) - 1)):
            if buf[j] == 0x45 and buf[j + 1] == 0x01:
                ftype = buf[1]
                payload = bytes(buf[2:j])
                frame_bytes = bytes(buf[:j + 2])
                name = NAMES.get(ftype, f'0x{ftype:02X}')
                meaning, _ = decode_packet(ftype, payload)
                frames.append((name, meaning, frame_bytes))
                buf = buf[j + 2:]
                found = True
                break

        if not found:
            break

    return frames


def wait_for_gap(ser, gap_ms=20, timeout=2.0):
    """Wait for a gap in bus traffic before sending."""
    import time
    start = time.time()
    last_byte_time = time.time()

    if ser.in_waiting:
        ser.read(ser.in_waiting)
        last_byte_time = time.time()

    while time.time() - start < timeout:
        if ser.in_waiting:
            ser.read(ser.in_waiting)
            last_byte_time = time.time()
        elif (time.time() - last_byte_time) * 1000 >= gap_ms:
            return True
        time.sleep(0.001)

    return False
