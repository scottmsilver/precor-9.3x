#!/usr/bin/env python3
"""
PRECOR Protocol Monitor - Curses UI with ASCII diagram detail view

Usage:
  python3 monitor.py              # Live capture from serial
  python3 monitor.py capture.jsonl # Load and view capture file
"""

import serial
import time
import curses
import threading
import json
import os
import sys
from collections import deque
from datetime import datetime

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600
MAX_PACKETS = 2000

def swap_nibbles(b):
    return ((b & 0x0F) << 4) | ((b & 0xF0) >> 4)

def hex_str(data):
    return ' '.join(f'{b:02X}' for b in data)

def ascii_char(b):
    return chr(b) if 32 <= b <= 126 else '.'

NAMES = {
    0x2A: 'SET_SPD', 0x4B: 'SET_INC', 0x4F: 'DISP1', 0x51: 'DISP2',
    0x52: 'UNK_52', 0x54: 'UNK_54', 0x9A: 'UNK_9A', 0xA2: 'UNK_A2',
    0xAA: 'UNK_AA', 0xD4: 'UNK_D4'
}

KNOWN_BYTES = {
    0xA0: "Address",
    0xAC: "HB marker",
    0xA5: "HB marker2",
    0xF1: "Speed cmd",
    0xF2: "Status",
    0xF5: "Stop cmd",
    0xF6: "Incline cmd",
    0xF9: "Heartbeat",
    0x59: "Idle byte",
}

# Custom base-16 digit set (avoiding control chars and 0x7F)
# Value:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15
# Byte:  9F   9D   9B   99   97   95   93   91   8F   8D   7D   7B   79   77   75   73
DIGITS = [0x9F, 0x9D, 0x9B, 0x99, 0x97, 0x95, 0x93, 0x91,
          0x8F, 0x8D, 0x7D, 0x7B, 0x79, 0x77, 0x75, 0x73]
DIGIT_TO_VAL = {b: i for i, b in enumerate(DIGITS)}

def decode_base16(payload):
    """Decode custom base-16 bytes to integer."""
    result = 0
    for b in payload:
        if b not in DIGIT_TO_VAL:
            return None
        result = result * 16 + DIGIT_TO_VAL[b]
    return result

def encode_base16(value):
    """Encode integer to custom base-16 bytes."""
    if value < 16:
        return bytes([DIGITS[value]])
    elif value < 256:
        return bytes([DIGITS[value // 16], DIGITS[value % 16]])
    else:
        return bytes([DIGITS[value // 256],
                      DIGITS[(value // 16) % 16],
                      DIGITS[value % 16]])

def decode_speed_raw(raw_bytes):
    """Decode speed from raw bytes. Returns mph or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 100.0  # hundredths of mph

def encode_speed(mph):
    """Encode speed in mph to raw bytes."""
    hundredths = round(mph * 100)
    return encode_base16(hundredths)

def decode_incline_raw(raw_bytes):
    """Decode incline from raw bytes. Returns percent or None."""
    val = decode_base16(raw_bytes)
    if val is None:
        return None
    return val / 2.0  # half-percent units

def encode_incline(percent):
    """Encode incline in percent to raw bytes."""
    half_pct = round(percent * 2)
    return encode_base16(half_pct)

FRAME_DESC = {
    0x2A: "Set speed (base-16 encoded)",
    0x4B: "Set incline (base-16 encoded)",
    0x4F: "Display segment 1",
    0x51: "Display segment 2",
    0x52: "Unknown type 0x52",
    0x54: "Unknown type 0x54",
    0x9A: "Unknown type 0x9A",
    0xA2: "Unknown type 0xA2",
    0xD4: "Unknown type 0xD4",
}

# Display frame patterns we recognize
DISPLAY_PATTERNS = {
    'IEP%': "Incline Elevation Potentiometer % (actual incline sensor)",
    'EP%': "Elevation Potentiometer % (incline sensor)",
    'SPD': "Speed display",
    'MPH': "Miles per hour",
    'KPH': "Kilometers per hour",
    'CAL': "Calories",
    'TIME': "Time elapsed",
}

def decode_bracketed(swapped):
    parts = []
    has_unknown = False
    i = 0
    while i < len(swapped):
        b = swapped[i]
        if b in KNOWN_BYTES:
            if b == 0x59 and i + 2 < len(swapped) and swapped[i+1] == 0x59 and swapped[i+2] == 0x59:
                parts.append("IDLE")
                i += 3
                continue
            parts.append(f"{b:02X}:{KNOWN_BYTES[b].split()[0].lower()}")
        else:
            parts.append(f"{b:02X}:???")
            has_unknown = True
        i += 1
    return "[" + "|".join(parts) + "]", has_unknown

def decode_english(swapped, ftype, raw_payload):
    if ftype == 0x52:
        # Unknown packet type - just show raw bytes
        return f"UNK_52: {len(raw_payload)} bytes", True
    elif ftype == 0x2A:
        # SET_SPD: 52 2A 1F 2F 8B [speed payload] 45 01
        # raw_payload is after type byte, so starts with 1F 2F 8B
        if len(raw_payload) >= 4 and raw_payload[0] == 0x1F and raw_payload[1] == 0x2F and raw_payload[2] == 0x8B:
            speed_bytes = raw_payload[3:]  # bytes after 1F 2F 8B
            speed = decode_speed_raw(speed_bytes)
            if speed is not None:
                return f"speed={speed:.1f}mph", False
        return f"SET_SPD: UNKNOWN", True
    elif ftype == 0x4B:
        # SET_INC: 52 4B CA 5A [incline payload] 45 01
        # raw_payload is after type byte, so starts with CA 5A
        if len(raw_payload) >= 3 and raw_payload[0] == 0xCA and raw_payload[1] == 0x5A:
            incline_bytes = raw_payload[2:]  # bytes after CA 5A
            if len(incline_bytes) == 0:
                return "incl=0%", False
            incline = decode_incline_raw(incline_bytes)
            if incline is not None:
                return f"incl={incline:.1f}%", False
            # Check for IDLE pattern (59 in raw)
            if all(b == 0x59 for b in incline_bytes):
                return "incl motor=IDLE", False
        return "SET_INC: UNKNOWN", True
    elif ftype in (0x4F, 0x51):
        printable = ''.join(chr(b) if 32 <= b <= 126 else '' for b in raw_payload)
        if printable:
            # Check for IEP% or EP% patterns (Incline Elevation Potentiometer?)
            if 'IEP%' in printable or 'EP%' in printable:
                return f"Display: '{printable[:15]}' (incline pot?)", False
            return f"Display: '{printable[:15]}'", False
        return "Display: UNKNOWN", True
    elif ftype == 0x9A:
        return f"UNK_9A: {len(raw_payload)} bytes", True
    elif ftype == 0xA2:
        return f"UNK_A2: {len(raw_payload)} bytes", True
    elif ftype == 0xD4:
        return f"UNK_D4: {len(raw_payload)} bytes", True
    elif ftype == 0x54:
        return f"UNK_54: {len(raw_payload)} bytes", True
    return f"UNK_{ftype:02X}: {len(raw_payload)} bytes", True

def build_display_diagram(raw_payload):
    """Build ASCII diagram for display frames showing text patterns."""
    lines = []

    # Convert to ASCII string
    ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in raw_payload)

    # Build box showing ASCII characters
    top = "+"
    hex_row = "|"
    asc_row = "|"

    for i, b in enumerate(raw_payload):
        top += "----+"
        hex_row += f" {b:02X} |"
        c = chr(b) if 32 <= b <= 126 else '.'
        asc_row += f"  {c} |"

    lines.append(top)
    lines.append(hex_row)
    lines.append(asc_row)
    lines.append(top)

    # Show full ASCII string
    lines.append("")
    lines.append(f"  ASCII: \"{ascii_str}\"")

    # Look for known patterns and annotate
    found_patterns = []
    for pattern, desc in DISPLAY_PATTERNS.items():
        if pattern in ascii_str:
            idx = ascii_str.find(pattern)
            found_patterns.append((idx, pattern, desc))

    if found_patterns:
        lines.append("")
        lines.append("  Recognized patterns:")
        for idx, pattern, desc in sorted(found_patterns):
            lines.append(f"    '{pattern}' at pos {idx}: {desc}")

    return lines

def build_frame_diagram(raw_frame, ftype):
    """Build ASCII diagram showing full frame structure."""
    lines = []

    if len(raw_frame) < 4:
        lines.append("  (malformed frame)")
        return lines

    # Frame structure: 52 [type] [payload...] 45 01
    # The byte before 45 01 is often status/checksum

    payload_start = 2
    payload_end = len(raw_frame) - 2  # exclude 45 01

    payload = raw_frame[payload_start:payload_end]
    payload_len = len(payload)

    # Build the diagram showing full frame structure - all bytes
    top = "+----+----+"
    hex_row = f"| 52 | {ftype:02X} |"
    lab_row = "|'R' |type|"

    # Show all payload bytes
    for b in payload:
        top += "----+"
        hex_row += f" {b:02X} |"
        lab_row += " .. |"

    top += "----+----+"
    hex_row += " 45 | 01 |"
    lab_row += "'E' |end |"

    lines.append(top)
    lines.append(hex_row)
    lines.append(lab_row)
    lines.append(top)

    return lines

def build_payload_diagram(swapped, ftype):
    """Build ASCII art diagram - box on top, labels below, no crossing lines."""
    lines = []

    if len(swapped) == 0:
        lines.append("  (empty payload)")
        return lines

    # Identify byte groups
    groups = []
    i = 0
    while i < len(swapped):
        b = swapped[i]
        if b == 0x59 and i + 2 < len(swapped) and swapped[i+1] == 0x59 and swapped[i+2] == 0x59:
            groups.append((i, i+2, [0x59, 0x59, 0x59], "IDLE (motor off)", True))
            i += 3
        elif b in KNOWN_BYTES:
            groups.append((i, i, [b], KNOWN_BYTES[b], True))
            i += 1
        else:
            groups.append((i, i, [b], f"??? ({b})", False))
            i += 1

    # Build the box first
    top = "+"
    hex_row = "|"
    asc_row = "|"

    for g in groups:
        for b in g[2]:
            top += "----+"
            hex_row += f" {b:02X} |"
            asc_row += f"  {ascii_char(b)} |"

    lines.append(top)
    lines.append(hex_row)
    lines.append(asc_row)
    lines.append(top)

    # Calculate center position for each group
    centers = []
    pos = 0
    for g in groups:
        width = len(g[2]) * 5
        centers.append(pos + width // 2 + 2)
        pos += width

    total_width = pos + 1

    # Draw vertical lines coming down from box
    vert_line = list(" " * (total_width + 50))
    for c in centers:
        if c < len(vert_line):
            vert_line[c] = "|"
    lines.append("".join(vert_line).rstrip())

    # Draw labels from right to left (so lines don't cross)
    # Rightmost label first (at top), leftmost label last (at bottom)
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        label = g[3]
        is_known = g[4]
        center = centers[idx]

        # Build line with vertical bars for groups to the left
        line = list(" " * (total_width + 50))

        # Add vertical bars for groups to the left of current
        for j in range(idx):
            c = centers[j]
            if c < len(line):
                line[c] = "|"

        # Add the corner and label for current group
        if center < len(line):
            line[center] = "+"

        # Add horizontal line and label
        label_text = "-- " + label
        for k, ch in enumerate(label_text):
            if center + 1 + k < len(line):
                line[center + 1 + k] = ch

        lines.append("".join(line).rstrip())

    return lines

def draw_detail_panel(stdscr, pkt, start_y, width, height):
    """Draw ASCII diagram detail panel."""
    num, name, bracketed, meaning, ftype, has_unknown, raw_payload, swapped, raw_frame = pkt

    lines = []
    lines.append(f"PACKET #{num}: {name} - {FRAME_DESC.get(ftype, 'Unknown')}")
    lines.append(f"Summary: {meaning}")
    lines.append("")

    # Show full frame structure
    lines.append("FULL FRAME:")
    lines.append(f"  Raw: {hex_str(raw_frame)}")
    frame_diagram = build_frame_diagram(raw_frame, ftype)
    for dl in frame_diagram:
        lines.append("  " + dl)

    lines.append("")

    # Show payload breakdown - different for display vs command frames
    if ftype in (0x4F, 0x51):
        # Display frames - show ASCII interpretation
        lines.append("DISPLAY CONTENT (ASCII text to screen):")
        lines.append(f"  Raw bytes: {hex_str(raw_payload)}")
        lines.append("")

        display_diagram = build_display_diagram(raw_payload)
        for dl in display_diagram:
            lines.append("  " + dl)
    else:
        # Command/status frames - show nibble-swapped decode
        lines.append("PAYLOAD BREAKDOWN (nibble-swapped):")
        lines.append(f"  Raw payload: {hex_str(raw_payload)}")
        lines.append(f"  Swapped:     {hex_str(swapped)}")
        lines.append("")

        payload_diagram = build_payload_diagram(swapped, ftype)
        for dl in payload_diagram:
            lines.append("  " + dl)

    for i, line in enumerate(lines):
        y = start_y + i
        if y < start_y + height - 1:
            try:
                if '???' in line or 'UNKNOWN' in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.color_pair(6))
                elif line.startswith('PACKET'):
                    stdscr.addstr(y, 0, line[:width-1], curses.A_BOLD | curses.color_pair(2))
                elif 'FULL FRAME:' in line or 'PAYLOAD BREAKDOWN' in line or 'DISPLAY CONTENT' in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.A_BOLD)
                elif 'Recognized patterns:' in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.A_BOLD | curses.color_pair(1))
                elif "IEP%" in line or "EP%" in line or "Incline" in line or "Elevation" in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.color_pair(1))
                elif '+' in line and '|' in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.color_pair(3))
                elif '+--' in line:
                    stdscr.addstr(y, 0, line[:width-1], curses.color_pair(3))
                else:
                    stdscr.addstr(y, 0, line[:width-1])
            except:
                pass

def build_speed_packet(speed_mph):
    """Build a SET_SPD packet for the given speed."""
    # SET_SPD: 52 2A 1F 2F 8B [speed payload] 45 01
    speed_bytes = encode_speed(speed_mph)
    return bytes([0x52, 0x2A, 0x1F, 0x2F, 0x8B]) + speed_bytes + bytes([0x45, 0x01])

def build_incline_packet(incline_pct):
    """Build a SET_INC packet for the given incline."""
    # SET_INC: 52 4B CA 5A [incline payload] 45 01
    incline_bytes = encode_incline(incline_pct)
    return bytes([0x52, 0x4B, 0xCA, 0x5A]) + incline_bytes + bytes([0x45, 0x01])

class PacketCapture:
    def __init__(self):
        self.packets = deque(maxlen=MAX_PACKETS)
        self.lock = threading.Lock()
        self.running = True
        self.paused = False
        self.count = 0
        self.error = None
        self.save_file = None
        self.save_filename = None
        self.save_count = 0
        # Control state
        self.current_speed = 0.0
        self.current_incline = 0.0
        self.target_speed = 0.0
        self.target_incline = 0.0
        self.command_queue = deque()
        self.serial = None
        self.control_enabled = False

    def start_save(self):
        """Start saving packets to a timestamped file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.save_filename = f"capture_{timestamp}.jsonl"
        self.save_file = open(self.save_filename, 'w')
        self.save_count = 0
        # Write header
        self.save_file.write(json.dumps({
            "type": "header",
            "timestamp": datetime.now().isoformat(),
            "description": "PRECOR treadmill packet capture"
        }) + "\n")
        self.save_file.flush()

    def stop_save(self):
        """Stop saving and close file."""
        if self.save_file:
            self.save_file.write(json.dumps({
                "type": "footer",
                "timestamp": datetime.now().isoformat(),
                "total_packets": self.save_count
            }) + "\n")
            self.save_file.close()
            self.save_file = None
            return self.save_filename
        return None

    def save_packet(self, pkt):
        """Save a packet to file if saving is active."""
        if self.save_file:
            num, fname, bracketed, meaning, ftype, has_unknown, payload, swapped, raw_frame = pkt
            record = {
                "type": "packet",
                "num": num,
                "timestamp": datetime.now().isoformat(),
                "frame_type": ftype,
                "frame_name": fname,
                "raw_frame": raw_frame.hex(),
                "payload": payload.hex(),
                "swapped": swapped.hex(),
                "meaning": meaning,
                "has_unknown": has_unknown
            }
            self.save_file.write(json.dumps(record) + "\n")
            self.save_file.flush()
            self.save_count += 1

    def set_speed(self, speed_mph):
        """Queue a speed command."""
        speed_mph = max(0.0, min(12.0, speed_mph))  # Clamp 0-12 mph
        self.target_speed = speed_mph
        if self.control_enabled:
            pkt = build_speed_packet(speed_mph)
            with self.lock:
                self.command_queue.append(pkt)

    def set_incline(self, incline_pct):
        """Queue an incline command."""
        incline_pct = max(0.0, min(15.0, incline_pct))  # Clamp 0-15%
        self.target_incline = incline_pct
        if self.control_enabled:
            pkt = build_incline_packet(incline_pct)
            with self.lock:
                self.command_queue.append(pkt)

    def adjust_speed(self, delta):
        """Adjust speed by delta mph."""
        self.set_speed(self.target_speed + delta)

    def adjust_incline(self, delta):
        """Adjust incline by delta percent."""
        self.set_incline(self.target_incline + delta)

    def extract_values(self, ftype, payload):
        """Extract speed/incline values from packet."""
        if ftype == 0x2A:  # SET_SPD
            if len(payload) >= 5 and payload[0] == 0x1F and payload[1] == 0x2F and payload[2] == 0x8B:
                speed = decode_speed_raw(payload[3:])
                if speed is not None:
                    self.current_speed = speed
                    if not self.control_enabled:
                        self.target_speed = speed
        elif ftype == 0x4B:  # SET_INC
            if len(payload) >= 3 and payload[0] == 0xCA and payload[1] == 0x5A:
                incline = decode_incline_raw(payload[2:])
                if incline is not None:
                    self.current_incline = incline
                    if not self.control_enabled:
                        self.target_incline = incline

    def capture_thread(self):
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
            self.serial = ser
        except Exception as e:
            self.error = str(e)
            return

        buf = bytearray()

        while self.running:
            try:
                # Process command queue - send any pending commands
                with self.lock:
                    while self.command_queue:
                        cmd = self.command_queue.popleft()
                        try:
                            ser.write(cmd)
                            ser.flush()
                        except Exception:
                            pass

                # ALWAYS read serial data - never drop bytes
                if ser.in_waiting:
                    new_data = ser.read(ser.in_waiting)
                    buf.extend(new_data)

                # Parse frames from buffer (even when paused for display)
                i = 0
                while i < len(buf) - 3:
                    if buf[i] == 0x52:
                        for j in range(i + 1, min(i + 50, len(buf) - 1)):
                            if buf[j] == 0x45 and buf[j+1] == 0x01:
                                ftype = buf[i+1]
                                raw_frame = bytes(buf[i:j+2])
                                payload = bytes(buf[i+2:j])
                                swapped = bytes(swap_nibbles(b) for b in payload)
                                fname = NAMES.get(ftype, f'0x{ftype:02X}')
                                bracketed, has_unk_decode = decode_bracketed(swapped)
                                meaning, has_unk_meaning = decode_english(swapped, ftype, payload)
                                has_unknown = has_unk_decode or has_unk_meaning

                                # Extract current values
                                self.extract_values(ftype, payload)

                                with self.lock:
                                    self.count += 1
                                    pkt = (
                                        self.count,
                                        fname,
                                        bracketed,
                                        meaning,
                                        ftype,
                                        has_unknown,
                                        payload,
                                        swapped,
                                        raw_frame
                                    )
                                    self.packets.append(pkt)
                                    self.save_packet(pkt)

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

                # Short sleep to prevent CPU spin, but fast enough to keep up
                time.sleep(0.005)
            except Exception:
                pass

        ser.close()

def load_capture_file(filename):
    """Load packets from a capture file."""
    packets = []
    with open(filename) as f:
        for line in f:
            record = json.loads(line)
            if record['type'] == 'packet':
                # Reconstruct packet tuple
                payload = bytes.fromhex(record['payload'])
                swapped = bytes.fromhex(record['swapped'])
                raw_frame = bytes.fromhex(record['raw_frame'])
                bracketed, _ = decode_bracketed(swapped)

                pkt = (
                    record['num'],
                    record['frame_name'],
                    bracketed,
                    record['meaning'],
                    record['frame_type'],
                    record['has_unknown'],
                    payload,
                    swapped,
                    raw_frame
                )
                packets.append(pkt)
    return packets

def main(stdscr, loaded_packets=None, source_file=None):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_RED, -1)
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_WHITE)

    replay_mode = loaded_packets is not None

    if replay_mode:
        cap = None
        static_packets = loaded_packets
    else:
        cap = PacketCapture()
        thread = threading.Thread(target=cap.capture_thread, daemon=True)
        thread.start()
        static_packets = None

    scroll_pos = 0
    follow = not replay_mode  # Don't follow in replay mode
    selected_idx = None
    cursor_offset = 0
    unique_mode = False
    status_msg = None
    status_time = 0
    stdscr.nodelay(True)

    while True:
        height, width = stdscr.getmaxyx()
        
        if selected_idx is not None:
            list_height = min(8, height // 3)
            detail_start = list_height + 4
            detail_height = height - detail_start - 1
        else:
            list_height = height - 5
            detail_start = None
            detail_height = 0

        header_lines = 4  # title + control panel + column headers + separator
        view_height = list_height

        if replay_mode:
            all_packets = static_packets
            total = len(static_packets)
            error = None
            is_saving = False
            save_count = 0
            save_filename = None
            current_speed = 0.0
            target_speed = 0.0
            current_incline = 0.0
            target_incline = 0.0
            control_enabled = False
        else:
            cap.paused = not follow
            with cap.lock:
                all_packets = list(cap.packets)
                total = cap.count
                error = cap.error
                is_saving = cap.save_file is not None
                save_count = cap.save_count
                save_filename = cap.save_filename
                current_speed = cap.current_speed
                target_speed = cap.target_speed
                current_incline = cap.current_incline
                target_incline = cap.target_incline
                control_enabled = cap.control_enabled

        # Filter to unique packets if unique_mode is on
        if unique_mode:
            seen = set()
            packets = []
            for pkt in all_packets:
                # Use (ftype, raw_payload) as unique key
                key = (pkt[4], pkt[6])
                if key not in seen:
                    seen.add(key)
                    packets.append(pkt)
        else:
            packets = all_packets

        num_packets = len(packets)

        if follow and num_packets > view_height:
            scroll_pos = num_packets - view_height
            cursor_offset = view_height - 1

        scroll_pos = max(0, min(scroll_pos, max(0, num_packets - view_height)))
        cursor_offset = max(0, min(cursor_offset, view_height - 1, num_packets - scroll_pos - 1))

        stdscr.erase()

        cursor_pkt = scroll_pos + cursor_offset
        unique_str = "UNIQUE " if unique_mode else ""
        save_str = f" | REC:{save_count}" if is_saving else ""
        if error:
            hdr = f" ERROR: {error} "
        elif replay_mode:
            short_file = os.path.basename(source_file) if source_file else "capture"
            if selected_idx is not None:
                hdr = f" REPLAY: {short_file} | {total} pkts ({num_packets} {unique_str}shown) | Pkt #{selected_idx+1} "
            else:
                hdr = f" REPLAY: {short_file} | {total} pkts ({num_packets} {unique_str}shown) | #{cursor_pkt+1} "
        elif selected_idx is not None:
            hdr = f" DETAIL VIEW | j/k=prev/next | ESC=back | Packet #{selected_idx+1}{save_str} "
        elif follow:
            hdr = f" PRECOR Monitor | {total} pkts ({num_packets} {unique_str}shown) | LIVE{save_str} "
        else:
            hdr = f" PRECOR Monitor | {total} pkts ({num_packets} {unique_str}shown) | PAUSED{save_str} "
        
        color = curses.A_REVERSE
        if is_saving:
            color |= curses.color_pair(6)  # Red when recording
        elif not follow and selected_idx is None:
            color |= curses.color_pair(6)
        elif selected_idx is not None:
            color |= curses.color_pair(2)
        stdscr.attron(color)
        stdscr.addstr(0, 0, hdr.ljust(width)[:width-1])
        stdscr.attroff(color)

        # Control panel line
        ctrl_status = "ON" if control_enabled else "OFF"
        ctrl_color = curses.color_pair(1) | curses.A_BOLD if control_enabled else curses.A_DIM
        ctrl_line = f" SPEED: {current_speed:4.1f} mph "
        if control_enabled:
            ctrl_line += f"(target: {target_speed:4.1f}) "
        ctrl_line += f"| INCLINE: {current_incline:4.1f}% "
        if control_enabled:
            ctrl_line += f"(target: {target_incline:4.1f}%) "
        ctrl_line += f"| CONTROL: {ctrl_status} "
        if control_enabled:
            ctrl_line += "| +/-:spd  [/]:incl "
        try:
            stdscr.addstr(1, 0, ctrl_line[:width-1], ctrl_color)
        except:
            pass

        col_hdr = f"{'#':<5}|{'FRAME':<8}|{'RAW BYTES':<35}|{'MEANING'}"
        stdscr.addstr(2, 0, col_hdr[:width-1], curses.A_BOLD)
        stdscr.addstr(3, 0, "-" * (width-1))

        for idx in range(view_height):
            pkt_idx = scroll_pos + idx
            if pkt_idx >= num_packets:
                break

            pkt = packets[pkt_idx]
            num, name, bracketed, meaning, ftype, has_unknown = pkt[:6]
            raw_frame = pkt[8] if len(pkt) > 8 else b''
            raw_hex = ' '.join(f'{b:02X}' for b in raw_frame)

            line = f"{num:<5}|{name:<8}|{raw_hex:<35}|{meaning}"

            y = header_lines + idx
            
            is_cursor = (not follow and idx == cursor_offset)
            is_selected = (selected_idx == pkt_idx)
            
            if is_selected:
                color = curses.color_pair(7) | curses.A_BOLD
            elif is_cursor:
                color = curses.A_REVERSE
            elif has_unknown:
                color = curses.color_pair(6)
            elif ftype == 0x52:
                color = curses.color_pair(1)
            elif ftype == 0x2A:
                color = curses.color_pair(2)
            elif ftype in (0x4F, 0x51):
                color = curses.color_pair(3)
            elif ftype == 0x4B:
                color = curses.color_pair(4)
            else:
                color = curses.color_pair(5)
            
            max_y = detail_start - 1 if detail_start else height - 1
            if y < max_y:
                try:
                    stdscr.addstr(y, 0, line[:width-1], color)
                except:
                    pass

        if selected_idx is not None and selected_idx < num_packets:
            stdscr.addstr(detail_start - 1, 0, "=" * (width-1), curses.A_DIM)
            draw_detail_panel(stdscr, packets[selected_idx], detail_start, width, detail_height)

        # Show status message or normal footer
        if status_msg and time.time() - status_time < 3.0:
            footer = f" >> {status_msg} << "
            footer_color = curses.A_REVERSE | curses.A_BOLD | curses.color_pair(1)
        else:
            status_msg = None
            if replay_mode:
                footer = f" j/k:move  ENTER:detail  u:unique  q:quit  (REPLAY MODE) "
            elif selected_idx is not None:
                save_hint = "s:stop rec" if is_saving else "s:record"
                footer = f" j/k:prev/next  ESC:back  u:unique  {save_hint}  q:quit "
            else:
                save_hint = "s:stop rec" if is_saving else "s:record"
                ctrl_hint = "+/-:spd [/]:incl 0:stop" if control_enabled else "c:control"
                pause_hint = "f:follow" if not follow else "f:pause"
                footer = f" j/k:move  ENTER:detail  {pause_hint}  u:unique  {save_hint}  q:quit "
            footer_color = curses.A_REVERSE
            if control_enabled:
                footer_color |= curses.color_pair(1)  # Green when control enabled

        stdscr.attron(footer_color)
        try:
            stdscr.addstr(height-1, 0, footer.ljust(width)[:width-1])
        except:
            pass
        stdscr.attroff(footer_color)

        stdscr.refresh()

        try:
            key = stdscr.getch()
        except:
            key = -1

        if key == ord('q') or key == ord('Q'):
            break
        elif key == 27:
            selected_idx = None
            if not replay_mode:
                follow = True
        elif key == ord('\n') or key == curses.KEY_ENTER or key == 10:
            if num_packets > 0:
                selected_idx = scroll_pos + cursor_offset
                if not replay_mode:
                    follow = False
        elif key == ord('f') or key == ord('F') or key == ord(' '):
            if selected_idx is None and not replay_mode:
                follow = not follow
        elif key == ord('j') or key == curses.KEY_DOWN:
            if selected_idx is not None:
                if selected_idx < num_packets - 1:
                    selected_idx += 1
                    if selected_idx >= scroll_pos + view_height:
                        scroll_pos = selected_idx - view_height + 1
            else:
                if cursor_offset < view_height - 1 and scroll_pos + cursor_offset < num_packets - 1:
                    cursor_offset += 1
                elif scroll_pos + view_height < num_packets:
                    scroll_pos += 1
            follow = False
        elif key == ord('k') or key == curses.KEY_UP:
            if selected_idx is not None:
                if selected_idx > 0:
                    selected_idx -= 1
                    if selected_idx < scroll_pos:
                        scroll_pos = selected_idx
            else:
                if cursor_offset > 0:
                    cursor_offset -= 1
                elif scroll_pos > 0:
                    scroll_pos -= 1
            follow = False
        elif key == curses.KEY_NPAGE:
            if selected_idx is not None:
                selected_idx = min(selected_idx + 10, num_packets - 1)
            else:
                scroll_pos += view_height
            follow = False
        elif key == curses.KEY_PPAGE:
            if selected_idx is not None:
                selected_idx = max(selected_idx - 10, 0)
            else:
                scroll_pos -= view_height
            follow = False
        elif key == ord('u') or key == ord('U'):
            unique_mode = not unique_mode
            scroll_pos = 0
            cursor_offset = 0
            selected_idx = None
        elif key == ord('s') or key == ord('S'):
            if not replay_mode and cap:
                with cap.lock:
                    if cap.save_file:
                        saved_file = cap.stop_save()
                        status_msg = f"Saved: {saved_file}"
                        status_time = time.time()
                    else:
                        cap.start_save()
                        status_msg = f"Recording to: {cap.save_filename}"
                        status_time = time.time()
        elif key == ord('c') or key == ord('C'):
            # Toggle control mode
            if not replay_mode and cap:
                cap.control_enabled = not cap.control_enabled
                if cap.control_enabled:
                    status_msg = "CONTROL ENABLED - +/-:speed  [/]:incline"
                else:
                    status_msg = "Control disabled"
                status_time = time.time()
        elif key == ord('+') or key == ord('='):
            # Increase speed by 0.1 mph
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_speed(0.1)
                status_msg = f"Speed -> {cap.target_speed:.1f} mph"
                status_time = time.time()
        elif key == ord('-') or key == ord('_'):
            # Decrease speed by 0.1 mph
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_speed(-0.1)
                status_msg = f"Speed -> {cap.target_speed:.1f} mph"
                status_time = time.time()
        elif key == ord(']') or key == ord('}'):
            # Increase incline by 0.5%
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_incline(0.5)
                status_msg = f"Incline -> {cap.target_incline:.1f}%"
                status_time = time.time()
        elif key == ord('[') or key == ord('{'):
            # Decrease incline by 0.5%
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_incline(-0.5)
                status_msg = f"Incline -> {cap.target_incline:.1f}%"
                status_time = time.time()
        elif key == ord('0'):
            # Emergency stop - set speed to 0
            if not replay_mode and cap and cap.control_enabled:
                cap.set_speed(0.0)
                status_msg = "STOP - Speed -> 0.0 mph"
                status_time = time.time()

        time.sleep(0.05)

    # Stop saving if still active when quitting
    if cap:
        with cap.lock:
            if cap.save_file:
                cap.stop_save()
        cap.running = False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Load capture file mode
        filename = sys.argv[1]
        if not os.path.exists(filename):
            print(f"Error: File not found: {filename}")
            sys.exit(1)
        print(f"Loading {filename}...")
        loaded_packets = load_capture_file(filename)
        print(f"Loaded {len(loaded_packets)} packets")

        def main_with_file(stdscr):
            main(stdscr, loaded_packets, filename)

        curses.wrapper(main_with_file)
    else:
        # Live capture mode
        curses.wrapper(lambda stdscr: main(stdscr, None, None))
