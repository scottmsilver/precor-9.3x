#!/usr/bin/env python3
"""
PRECOR Protocol Monitor - Curses UI with packet capture and decode.

Usage:
  python3 monitor.py              # Live capture from serial
  python3 monitor.py capture.jsonl # Load and view capture file
"""

import curses
import threading
import json
import os
import sys
import time
from collections import deque
from datetime import datetime

import serial

from protocol import (
    SERIAL_PORT, BAUD_RATE, FRAME_START, NAMES, DIGIT_TO_VAL,
    decode_speed, decode_incline, decode_base16, decode_packet, hex_str,
    build_set_spd, build_set_inc,
    SET_SPD_HEADER, SET_INC_HEADER, TYPE_SET_SPD, TYPE_SET_INC,
    TYPE_DISP1, TYPE_DISP2,
)

MAX_PACKETS = 2000


def format_wire_display(ftype, payload, raw_frame):
    """Generate wire-protocol analyzer style display."""
    lines = []

    # Build field map: list of (start_byte, end_byte, label, value, style)
    fields = []
    pos = 0

    # Frame start
    fields.append((0, 1, "START", "'R'", "dim"))
    pos = 1

    # Type byte
    type_name = NAMES.get(ftype, f'0x{ftype:02X}')
    fields.append((1, 2, "TYPE", type_name, "type"))
    pos = 2

    if ftype == TYPE_SET_SPD and len(payload) >= 4 and payload[:3] == SET_SPD_HEADER:
        fields.append((2, 5, "HDR", "SET_SPD", "dim"))
        speed_bytes = payload[3:]
        speed = decode_speed(speed_bytes)
        digits = ''.join(f"{DIGIT_TO_VAL.get(b, '?'):X}" for b in speed_bytes)
        if speed is not None:
            fields.append((5, 5 + len(speed_bytes), "SPEED", f"{speed:.2f} mph (0x{digits}={int(speed*100)})", "highlight"))
        else:
            fields.append((5, 5 + len(speed_bytes), "SPEED", f"? (0x{digits})", "value"))
        pos = 5 + len(speed_bytes)

    elif ftype == TYPE_SET_INC and len(payload) >= 2 and payload[:2] == SET_INC_HEADER:
        fields.append((2, 4, "HDR", "SET_INC", "dim"))
        if len(payload) > 2:
            inc_bytes = payload[2:]
            incline = decode_incline(inc_bytes)
            digits = ''.join(f"{DIGIT_TO_VAL.get(b, '?'):X}" for b in inc_bytes)
            if incline is not None:
                fields.append((4, 4 + len(inc_bytes), "INCL", f"{incline:.1f}% (0x{digits}={int(incline*2)})", "highlight"))
            else:
                fields.append((4, 4 + len(inc_bytes), "INCL", f"? (0x{digits})", "value"))
            pos = 4 + len(inc_bytes)
        else:
            pos = 4

    elif ftype in (TYPE_DISP1, TYPE_DISP2):
        ascii_pl = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in payload)
        fields.append((2, 2 + len(payload), "DISP", f"'{ascii_pl}'", "highlight"))
        pos = 2 + len(payload)

    else:
        if payload:
            fields.append((2, 2 + len(payload), "DATA", f"{len(payload)} bytes", "value"))
            pos = 2 + len(payload)

    # Frame end
    fields.append((pos, pos + 2, "END", "", "dim"))

    # Line 1: Hex bytes with spacing
    hex_line = "  "
    for i, b in enumerate(raw_frame):
        hex_line += f"{b:02X} "
    lines.append(("hex", hex_line.rstrip(), None))

    # Line 2: ASCII representation
    ascii_line = "  "
    for b in raw_frame:
        ch = chr(b) if 32 <= b <= 126 else '·'
        ascii_line += f"{ch}  "
    lines.append(("ascii", ascii_line.rstrip(), "dim"))

    # Line 3: Field brackets
    bracket_line = "  "
    for i in range(len(raw_frame)):
        in_field = False
        for start, end, label, value, style in fields:
            if start <= i < end:
                if i == start:
                    if end - start == 1:
                        bracket_line += "── "
                    else:
                        bracket_line += "└─"
                elif i == end - 1:
                    bracket_line += "─┘ "
                else:
                    bracket_line += "──"
                in_field = True
                break
        if not in_field:
            bracket_line += "   "
    lines.append(("bracket", bracket_line, "dim"))

    # Field annotations
    for start, end, label, value, style in fields:
        byte_str = ' '.join(f'{raw_frame[i]:02X}' for i in range(start, min(end, len(raw_frame))))
        if value:
            lines.append(("field", f"  {label:6} [{byte_str:12}] = {value}", style))
        else:
            lines.append(("field", f"  {label:6} [{byte_str:12}]", style))

        # Add b16 decode line below DATA field
        if label == "DATA":
            data_bytes = bytes(raw_frame[i] for i in range(start, min(end, len(raw_frame))))
            # Build decode line with same spacing as byte_str
            b16_parts = []
            all_valid = True
            for b in data_bytes:
                if b in DIGIT_TO_VAL:
                    b16_parts.append(f" {DIGIT_TO_VAL[b]:X}")
                else:
                    b16_parts.append(f"{b:02X}")
                    all_valid = False
            b16_str = ' '.join(b16_parts)
            # Calculate numeric if all valid
            numeric = decode_base16(data_bytes) if all_valid else None
            if numeric is not None:
                lines.append(("field", f"  {'B16':6} [{b16_str:12}] = {numeric} (decoded)", "highlight"))
            else:
                lines.append(("field", f"  {'B16':6} [{b16_str:12}]   (partial decode)", "dim"))

    return lines


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
            num, fname, meaning, ftype, has_unknown, payload, raw_frame = pkt
            record = {
                "type": "packet",
                "num": num,
                "timestamp": datetime.now().isoformat(),
                "frame_type": ftype,
                "frame_name": fname,
                "raw_frame": raw_frame.hex(),
                "payload": payload.hex(),
                "meaning": meaning,
                "has_unknown": has_unknown
            }
            self.save_file.write(json.dumps(record) + "\n")
            self.save_file.flush()
            self.save_count += 1

    def set_speed(self, speed_mph):
        """Queue a speed command."""
        speed_mph = max(0.0, min(12.0, speed_mph))
        self.target_speed = speed_mph
        if self.control_enabled:
            pkt = build_set_spd(speed_mph)
            with self.lock:
                self.command_queue.append(pkt)

    def set_incline(self, incline_pct):
        """Queue an incline command."""
        incline_pct = max(0.0, min(15.0, incline_pct))
        self.target_incline = incline_pct
        if self.control_enabled:
            pkt = build_set_inc(incline_pct)
            with self.lock:
                self.command_queue.append(pkt)

    def adjust_speed(self, delta):
        self.set_speed(self.target_speed + delta)

    def adjust_incline(self, delta):
        self.set_incline(self.target_incline + delta)

    def extract_values(self, ftype, payload):
        """Extract speed/incline values from packet."""
        if ftype == TYPE_SET_SPD:
            if len(payload) >= 4 and payload[:3] == SET_SPD_HEADER:
                speed = decode_speed(payload[3:])
                if speed is not None:
                    self.current_speed = speed
                    if not self.control_enabled:
                        self.target_speed = speed
        elif ftype == TYPE_SET_INC:
            if len(payload) >= 2 and payload[:2] == SET_INC_HEADER:
                incline = decode_incline(payload[2:]) if len(payload) > 2 else 0.0
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
                # Process command queue
                with self.lock:
                    while self.command_queue:
                        cmd = self.command_queue.popleft()
                        try:
                            ser.write(cmd)
                            ser.flush()
                        except Exception:
                            pass

                # Read serial data
                if ser.in_waiting:
                    buf.extend(ser.read(ser.in_waiting))

                # Parse frames
                i = 0
                while i < len(buf) - 3:
                    if buf[i] == FRAME_START:
                        for j in range(i + 1, min(i + 50, len(buf) - 1)):
                            if buf[j] == 0x45 and buf[j + 1] == 0x01:
                                ftype = buf[i + 1]
                                raw_frame = bytes(buf[i:j + 2])
                                payload = bytes(buf[i + 2:j])
                                fname = NAMES.get(ftype, f'0x{ftype:02X}')
                                meaning, has_unknown = decode_packet(ftype, payload)

                                self.extract_values(ftype, payload)

                                with self.lock:
                                    self.count += 1
                                    pkt = (
                                        self.count,
                                        fname,
                                        meaning,
                                        ftype,
                                        has_unknown,
                                        payload,
                                        raw_frame
                                    )
                                    self.packets.append(pkt)
                                    self.save_packet(pkt)

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
                payload = bytes.fromhex(record['payload'])
                raw_frame = bytes.fromhex(record['raw_frame'])
                meaning, has_unknown = decode_packet(record['frame_type'], payload)

                pkt = (
                    record['num'],
                    record['frame_name'],
                    meaning,
                    record['frame_type'],
                    has_unknown,
                    payload,
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
    follow = not replay_mode
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

        header_lines = 4
        view_height = list_height

        if replay_mode:
            all_packets = static_packets
            total = len(static_packets)
            error = None
            is_saving = False
            save_count = 0
            current_speed = 0.0
            target_speed = 0.0
            current_incline = 0.0
            target_incline = 0.0
            control_enabled = False
        else:
            with cap.lock:
                all_packets = list(cap.packets)
                total = cap.count
                error = cap.error
                is_saving = cap.save_file is not None
                save_count = cap.save_count
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
                key = (pkt[3], pkt[5])  # ftype, payload
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

        # Header
        cursor_pkt = scroll_pos + cursor_offset
        unique_str = "UNIQUE " if unique_mode else ""
        save_str = f" | REC:{save_count}" if is_saving else ""

        if error:
            hdr = f" ERROR: {error} "
        elif replay_mode:
            short_file = os.path.basename(source_file) if source_file else "capture"
            hdr = f" REPLAY: {short_file} | {total} pkts ({num_packets} {unique_str}shown) | #{cursor_pkt + 1} "
        elif follow:
            hdr = f" PRECOR Monitor | {total} pkts ({num_packets} {unique_str}shown) | LIVE{save_str} "
        else:
            hdr = f" PRECOR Monitor | {total} pkts ({num_packets} {unique_str}shown) | PAUSED{save_str} "

        color = curses.A_REVERSE
        if is_saving:
            color |= curses.color_pair(6)
        elif not follow and selected_idx is None and not replay_mode:
            color |= curses.color_pair(6)
        stdscr.attron(color)
        stdscr.addstr(0, 0, hdr.ljust(width)[:width - 1])
        stdscr.attroff(color)

        # Control panel line
        ctrl_color = curses.color_pair(1) | curses.A_BOLD if control_enabled else curses.A_DIM
        ctrl_line = f" SPEED: {current_speed:4.1f} mph | INCLINE: {current_incline:4.1f}%"
        if control_enabled:
            ctrl_line += f" | TARGET: {target_speed:.1f}/{target_incline:.1f} | CTRL ON"
        try:
            stdscr.addstr(1, 0, ctrl_line[:width - 1], ctrl_color)
        except:
            pass

        # Column headers
        col_hdr = f"{'#':<5}|{'TYPE':<8}|{'RAW BYTES':<40}|{'MEANING'}"
        stdscr.addstr(2, 0, col_hdr[:width - 1], curses.A_BOLD)
        stdscr.addstr(3, 0, "-" * (width - 1))

        # Packet list
        for idx in range(view_height):
            pkt_idx = scroll_pos + idx
            if pkt_idx >= num_packets:
                break

            pkt = packets[pkt_idx]
            num, name, meaning, ftype, has_unknown, payload, raw_frame = pkt
            raw_hex = hex_str(raw_frame)

            line = f"{num:<5}|{name:<8}|{raw_hex:<40}|{meaning}"

            y = header_lines + idx
            is_cursor = (not follow and idx == cursor_offset)
            is_selected = (selected_idx == pkt_idx)

            if is_selected:
                pkt_color = curses.color_pair(7) | curses.A_BOLD
            elif is_cursor:
                pkt_color = curses.A_REVERSE
            elif has_unknown:
                pkt_color = curses.color_pair(6)
            elif ftype == TYPE_SET_SPD:
                pkt_color = curses.color_pair(2)
            elif ftype == TYPE_SET_INC:
                pkt_color = curses.color_pair(4)
            elif ftype in (TYPE_DISP1, TYPE_DISP2):
                pkt_color = curses.color_pair(3)
            else:
                pkt_color = curses.color_pair(5)

            max_y = detail_start - 1 if detail_start else height - 1
            if y < max_y:
                try:
                    stdscr.addstr(y, 0, line[:width - 1], pkt_color)
                except:
                    pass

        # Detail panel
        if selected_idx is not None and selected_idx < num_packets:
            pkt = packets[selected_idx]
            num, name, meaning, ftype, has_unknown, payload, raw_frame = pkt

            # Separator line
            stdscr.addstr(detail_start - 1, 0, "═" * (width - 1), curses.A_DIM)

            # Title bar
            title = f" PACKET #{num}: {name} │ {meaning} "
            stdscr.addstr(detail_start, 0, title[:width-1], curses.A_REVERSE | curses.A_BOLD)

            # Wire protocol display
            wire_lines = format_wire_display(ftype, payload, raw_frame)
            for i, (line_type, text, style) in enumerate(wire_lines):
                y = detail_start + 2 + i
                if y >= height - 1:
                    break
                try:
                    if line_type == "hex":
                        stdscr.addstr(y, 0, text[:width-1], curses.A_BOLD)
                    elif line_type == "ascii":
                        stdscr.addstr(y, 0, text[:width-1], curses.color_pair(3))
                    elif line_type == "bracket":
                        stdscr.addstr(y, 0, text[:width-1], curses.A_DIM)
                    elif line_type == "field":
                        if style == "highlight":
                            stdscr.addstr(y, 0, text[:width-1], curses.color_pair(1) | curses.A_BOLD)
                        elif style == "type":
                            stdscr.addstr(y, 0, text[:width-1], curses.color_pair(4))
                        elif style == "value":
                            stdscr.addstr(y, 0, text[:width-1], curses.color_pair(2))
                        elif style == "dim":
                            stdscr.addstr(y, 0, text[:width-1], curses.A_DIM)
                        else:
                            stdscr.addstr(y, 0, text[:width-1])
                except:
                    pass

        # Footer
        if status_msg and time.time() - status_time < 3.0:
            footer = f" >> {status_msg} << "
            footer_color = curses.A_REVERSE | curses.A_BOLD | curses.color_pair(1)
        else:
            status_msg = None
            if replay_mode:
                footer = " j/k:move  ENTER:detail  u:unique  q:quit  (REPLAY) "
            elif selected_idx is not None:
                pause_hint = "f:follow" if not follow else "f:pause"
                footer = f" j/k:prev/next  {pause_hint}  ESC:back  q:quit "
            else:
                save_hint = "s:stop" if is_saving else "s:rec"
                pause_hint = "f:follow" if not follow else "f:pause"
                footer = f" j/k:move  ENTER:detail  {pause_hint}  u:unique  {save_hint}  c:ctrl  q:quit "
            footer_color = curses.A_REVERSE

        stdscr.attron(footer_color)
        try:
            stdscr.addstr(height - 1, 0, footer.ljust(width)[:width - 1])
        except:
            pass
        stdscr.attroff(footer_color)

        stdscr.refresh()

        # Key handling
        try:
            key = stdscr.getch()
        except:
            key = -1

        if key == ord('q') or key == ord('Q'):
            break
        elif key == 27:  # ESC
            selected_idx = None
            if not replay_mode:
                follow = True
        elif key == ord('\n') or key == curses.KEY_ENTER or key == 10:
            if num_packets > 0:
                selected_idx = scroll_pos + cursor_offset
                follow = False
        elif key == ord('f') or key == ord('F') or key == ord(' '):
            if not replay_mode:
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
            scroll_pos += view_height
            follow = False
        elif key == curses.KEY_PPAGE:
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
                    else:
                        cap.start_save()
                        status_msg = f"Recording: {cap.save_filename}"
                    status_time = time.time()
        elif key == ord('c') or key == ord('C'):
            if not replay_mode and cap:
                cap.control_enabled = not cap.control_enabled
                status_msg = "Control ON" if cap.control_enabled else "Control OFF"
                status_time = time.time()
        elif key == ord('+') or key == ord('='):
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_speed(0.5)
                status_msg = f"Speed -> {cap.target_speed:.1f}"
                status_time = time.time()
        elif key == ord('-') or key == ord('_'):
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_speed(-0.5)
                status_msg = f"Speed -> {cap.target_speed:.1f}"
                status_time = time.time()
        elif key == ord(']') or key == ord('}'):
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_incline(0.5)
                status_msg = f"Incline -> {cap.target_incline:.1f}"
                status_time = time.time()
        elif key == ord('[') or key == ord('{'):
            if not replay_mode and cap and cap.control_enabled:
                cap.adjust_incline(-0.5)
                status_msg = f"Incline -> {cap.target_incline:.1f}"
                status_time = time.time()
        elif key == ord('0'):
            if not replay_mode and cap and cap.control_enabled:
                cap.set_speed(0.0)
                status_msg = "STOP"
                status_time = time.time()

        time.sleep(0.05)

    if cap:
        with cap.lock:
            if cap.save_file:
                cap.stop_save()
        cap.running = False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        filename = sys.argv[1]
        if not os.path.exists(filename):
            print(f"Error: File not found: {filename}")
            sys.exit(1)
        print(f"Loading {filename}...")
        loaded_packets = load_capture_file(filename)
        print(f"Loaded {len(loaded_packets)} packets")
        curses.wrapper(lambda s: main(s, loaded_packets, filename))
    else:
        curses.wrapper(lambda s: main(s, None, None))
