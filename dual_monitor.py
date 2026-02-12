#!/usr/bin/env python3
"""
Dual Protocol Monitor — Side-by-Side Curses UI

Shows both treadmill serial protocols decoded in real time:
  Left pane:  Controller → Motor (pin 6, KV text format, 0xFF delimited)
  Right pane: Motor → Controller (pin 3, binary R...E frames)

Usage:
  python3 dual_monitor.py
  python3 dual_monitor.py --kv-read /dev/ttyUSB2 --kv-write /dev/ttyUSB1 --bin /dev/ttyUSB0
  python3 dual_monitor.py --baud 9600
"""

import argparse
import curses
import threading
import time
from collections import deque

import serial

from ports import get_port
from protocol import (
    FRAME_START, NAMES, hex_str,
    TYPE_SET_SPD, TYPE_SET_INC, TYPE_DISP1, TYPE_DISP2,
    SET_SPD_HEADER, SET_INC_HEADER,
    DIGIT_TO_VAL, decode_speed, decode_incline,
)
from listen import parse_kv_fields

MAX_ENTRIES = 2000

# Extended names for pin 3 frame types (based on KV correlation analysis)
PIN3_NAMES = dict(NAMES)
PIN3_NAMES.update({
    0x12: 'HMPH_R',   # response correlated with hmph query
    0x22: 'MPH_R',    # response correlated with mph query
    0x49: 'BELT_R',   # response correlated with belt query
    0x4D: 'PART_R',   # response correlated with part query
    0x92: 'UNK_92',
    0xA4: 'TYPE_R2',  # rare, correlated with type query
})

# KV cycle that the real controller sends (15 keys, repeating)
KV_CYCLE = [
    ('inc',  lambda s: str(s['emu_incline'])),
    ('hmph', lambda s: str(s['emu_speed'])),
    ('mph',  lambda s: str(s['emu_speed'])),
    ('amps', None),
    ('err',  None),
    ('belt', None),
    ('vbus', None),
    ('lift', None),
    ('lfts', None),
    ('lftg', None),
    ('part', lambda s: '6'),
    ('ver',  None),
    ('type', None),
    ('diag', lambda s: '0'),
    ('loop', lambda s: '5550'),
]

# Burst groupings matching real controller timing
KV_BURSTS = [
    [0, 1, 2],       # inc, hmph, mph
    [3, 4, 5],       # amps, err, belt
    [6, 7, 8, 9],    # vbus, lift, lfts, lftg
    [10, 11, 12],    # part, ver, type
    [13, 14],         # diag, loop
]


def build_kv_cmd(key, value=None):
    """Build a KV command as bytes: [key:value]\\xff or [key]\\xff."""
    if value is not None:
        return f'[{key}:{value}]'.encode() + b'\xff'
    return f'[{key}]'.encode() + b'\xff'


def emulate_thread(ser_write, entries, lock, state):
    """Send KV cycle to motor, emulating the real controller."""
    while state['running'] and state['emulate']:
        for burst in KV_BURSTS:
            if not state['running'] or not state['emulate']:
                return
            for idx in burst:
                if not state['running'] or not state['emulate']:
                    return
                key, val_fn = KV_CYCLE[idx]
                value = val_fn(state) if val_fn else None
                cmd = build_kv_cmd(key, value)
                try:
                    ser_write.write(cmd)
                    ser_write.flush()
                except Exception:
                    pass
                # Log to entries
                now = time.time() - state['start']
                val_str = f'{value}' if value is not None else ''
                with lock:
                    entries.append((now, 'E', key, val_str, b''))
            # ~100ms between bursts
            time.sleep(0.1)


def decode_pin3(ftype, payload):
    """Decode a pin 3 binary frame. Handles merged-frame payloads
    by stopping at the first non-b16 byte for speed/incline."""
    if ftype == TYPE_SET_SPD:
        if len(payload) >= 4 and payload[:3] == SET_SPD_HEADER:
            spd_bytes = payload[3:]
            # Truncate at first non-b16 byte (rest is merged frame data)
            clean = bytearray()
            for b in spd_bytes:
                if b in DIGIT_TO_VAL:
                    clean.append(b)
                else:
                    break
            if clean:
                speed = decode_speed(bytes(clean))
                if speed is not None:
                    return f"spd={speed:.2f}mph"
        return f"SET_SPD {len(payload)}B"

    elif ftype == TYPE_SET_INC:
        if len(payload) >= 2 and payload[:2] == SET_INC_HEADER:
            if len(payload) == 2:
                return "inc=0%"
            inc_bytes = payload[2:]
            clean = bytearray()
            for b in inc_bytes:
                if b in DIGIT_TO_VAL:
                    clean.append(b)
                else:
                    break
            if clean:
                incline = decode_incline(bytes(clean))
                if incline is not None:
                    return f"inc={incline:.1f}%"
        return f"SET_INC {len(payload)}B"

    elif ftype in (TYPE_DISP1, TYPE_DISP2):
        # Only decode the clean ASCII portion
        chars = []
        for b in payload:
            if 32 <= b <= 126:
                chars.append(chr(b))
            else:
                break
        if chars:
            return f"'{''.join(chars)}'"
        return f"DISP {len(payload)}B"

    else:
        name = PIN3_NAMES.get(ftype, f'0x{ftype:02X}')
        # Many pin 3 payloads have: [header bytes] 0x8B [b16 digits]
        # Try to find 0x8B separator and decode digits after it
        if 0x8B in payload:
            sep_idx = payload.index(0x8B)
            after = payload[sep_idx + 1:]
            digits = []
            for b in after:
                if b in DIGIT_TO_VAL:
                    digits.append(DIGIT_TO_VAL[b])
                else:
                    break
            if digits:
                val = 0
                for d in digits:
                    val = val * 16 + d
                return f"{name} ={val}"
        # Fallback: try b16 from start
        digits = []
        for b in payload:
            if b in DIGIT_TO_VAL:
                digits.append(DIGIT_TO_VAL[b])
            else:
                break
        if digits:
            val = 0
            for d in digits:
                val = val * 16 + d
            return f"{name} ={val}"
        return f"{name} {len(payload)}B"


def kv_read_thread(ser_read, ser_write, entries, lock, state):
    """Read KV text protocol from controller side of pin 6."""
    buf = bytearray()
    while state['running']:
        try:
            data = ser_read.read(256)
            if data:
                state['kv_bytes'] += len(data)
                buf.extend(data)
                # Proxy: forward raw bytes to motor side
                if state['proxy'] and ser_write:
                    try:
                        ser_write.write(data)
                        ser_write.flush()
                    except Exception:
                        pass
                # Parse KV fields
                fields, buf = parse_kv_fields(buf)
                if fields:
                    now = time.time() - state['start']
                    with lock:
                        for key, val in fields:
                            entry = (now, 'C', key, val, b'')
                            entries.append(entry)
            else:
                time.sleep(0.005)
        except Exception:
            time.sleep(0.01)


def bin_read_thread(ser, entries, lock, state):
    """Read binary R...E frames from motor side of pin 3.
    Pin 3 uses 45 00 as frame end, or 45 followed by 52 (next frame start)."""
    buf = bytearray()
    while state['running']:
        try:
            data = ser.read(256)
            if data:
                state['bin_bytes'] += len(data)
                buf.extend(data)
                # Parse frames
                i = 0
                while i < len(buf) - 3:
                    if buf[i] == FRAME_START:
                        found = False
                        for j in range(i + 2, min(i + 30, len(buf))):
                            if buf[j] == 0x45 and j + 1 < len(buf) and buf[j + 1] in (0x00, FRAME_START):
                                ftype = buf[i + 1]
                                payload = bytes(buf[i + 2:j])
                                raw = bytes(buf[i:j + 2])
                                name = PIN3_NAMES.get(ftype, f'0x{ftype:02X}')
                                meaning = decode_pin3(ftype, payload)
                                now = time.time() - state['start']
                                with lock:
                                    entry = (now, 'M', name, meaning, raw)
                                    entries.append(entry)
                                if buf[j + 1] == 0x00:
                                    buf = buf[j + 2:]
                                else:
                                    buf = buf[j + 1:]  # keep the 0x52 for next frame
                                i = 0
                                found = True
                                break
                        if not found:
                            i += 1
                    else:
                        i += 1
                        if i > 100:
                            buf = buf[i:]
                            i = 0
            else:
                time.sleep(0.005)
        except Exception:
            time.sleep(0.01)


def format_entry(entry, width):
    """Format a single entry as a display string, truncated to width."""
    ts, side, key, val, raw = entry
    if val:
        line = f" {ts:6.1f}  {key:<8} {val}"
    else:
        line = f" {ts:6.1f}  {key}"
    if len(line) > width:
        line = line[:width]
    return line


def main(stdscr, args):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)   # KV side
    curses.init_pair(2, curses.COLOR_CYAN, -1)    # Binary side
    curses.init_pair(3, curses.COLOR_YELLOW, -1)   # headers
    curses.init_pair(4, curses.COLOR_RED, -1)      # proxy indicator
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)

    entries = deque(maxlen=MAX_ENTRIES)
    lock = threading.Lock()
    state = {
        'running': True,
        'proxy': True,
        'start': time.time(),
        'kv_bytes': 0,
        'bin_bytes': 0,
        'emulate': False,
        'emu_speed': 0,
        'emu_incline': 0,
    }

    # Open serial ports
    kv_read_dev = get_port(args.kv_read)
    bin_dev = get_port(args.bin_read)

    errors = []
    ser_kv_read = None
    ser_kv_write = None
    ser_bin = None
    kv_write_dev = None

    try:
        ser_kv_read = serial.Serial(kv_read_dev, args.baud, timeout=0.1)
        ser_kv_read.reset_input_buffer()
    except Exception as e:
        errors.append(f"kv-read ({kv_read_dev}): {e}")

    try:
        kv_write_dev = get_port(args.kv_write)
        ser_kv_write = serial.Serial(kv_write_dev, args.baud, timeout=0)
    except Exception as e:
        errors.append(f"kv-write ({args.kv_write}): {e}")

    try:
        ser_bin = serial.Serial(bin_dev, args.baud, timeout=0.1)
        ser_bin.reset_input_buffer()
    except Exception as e:
        errors.append(f"bin-read ({bin_dev}): {e}")

    # Start reader threads
    threads = []
    if ser_kv_read:
        t = threading.Thread(target=kv_read_thread,
                             args=(ser_kv_read, ser_kv_write, entries, lock, state),
                             daemon=True)
        t.start()
        threads.append(t)

    if ser_bin:
        t = threading.Thread(target=bin_read_thread,
                             args=(ser_bin, entries, lock, state),
                             daemon=True)
        t.start()
        threads.append(t)

    # UI state
    follow = True
    changes_only = False
    unique_mode = False
    c_scroll = 0
    m_scroll = 0

    stdscr.nodelay(True)

    while True:
        height, width = stdscr.getmaxyx()
        mid = width // 2

        # Snapshot entries
        with lock:
            all_entries = list(entries)

        # Split by side (left pane shows both controller 'C' and emulated 'E')
        c_entries = [e for e in all_entries if e[1] in ('C', 'E')]
        m_entries = [e for e in all_entries if e[1] == 'M']

        # Apply filters
        if changes_only:
            c_entries = _filter_changes(c_entries)
            m_entries = _filter_changes(m_entries)
        elif unique_mode:
            c_entries = _filter_unique(c_entries)
            m_entries = _filter_unique(m_entries)

        # View area
        view_height = height - 4  # header(1) + separator(1) + bot_sep(1) + footer(1)
        if view_height < 1:
            view_height = 1

        c_count = len(c_entries)
        m_count = len(m_entries)

        if follow:
            c_scroll = max(0, c_count - view_height)
            m_scroll = max(0, m_count - view_height)
        c_scroll = max(0, min(c_scroll, max(0, c_count - view_height)))
        m_scroll = max(0, min(m_scroll, max(0, m_count - view_height)))

        stdscr.erase()

        # Header line 1: titles
        left_w = mid - 1
        right_w = width - mid - 1

        kv_write_label = kv_write_dev if ser_kv_write else "N/A"
        left_title = f" pin6 KV ({kv_read_dev}\u2194{kv_write_label})"
        right_title = f"  MOT\u2192CTRL pin3 bin ({bin_dev})"
        if state['emulate']:
            status_str = (f" [EMULATE spd={state['emu_speed']}"
                          f" inc={state['emu_incline']}]")
            status_color = curses.color_pair(5) | curses.A_BOLD
        elif state['proxy']:
            status_str = " [PROXY ON]"
            status_color = curses.color_pair(4) | curses.A_BOLD
        else:
            status_str = ""
            status_color = 0

        # Draw header
        try:
            stdscr.addstr(0, 0, left_title[:left_w].ljust(left_w),
                          curses.color_pair(3) | curses.A_BOLD)
            stdscr.addstr(0, left_w, "\u2502", curses.A_DIM)
            stdscr.addstr(0, mid, right_title[:right_w],
                          curses.color_pair(3) | curses.A_BOLD)
            if status_str:
                px = left_w - len(status_str)
                if px > 0:
                    stdscr.addstr(0, px, status_str, status_color)
        except curses.error:
            pass

        # Header line 2: separator
        try:
            sep = "\u2500" * left_w + "\u253C" + "\u2500" * right_w
            stdscr.addstr(1, 0, sep[:width - 1], curses.A_DIM)
        except curses.error:
            pass

        # Draw entries side by side (independent scroll per pane)
        for row in range(view_height):
            y = row + 2
            if y >= height - 2:
                break

            # Left pane (controller KV)
            c_idx = c_scroll + row
            if c_idx < c_count:
                line = format_entry(c_entries[c_idx], left_w)
                try:
                    stdscr.addstr(y, 0, line.ljust(left_w)[:left_w],
                                  curses.color_pair(1))
                except curses.error:
                    pass

            # Divider
            try:
                stdscr.addstr(y, left_w, "\u2502", curses.A_DIM)
            except curses.error:
                pass

            # Right pane (motor binary)
            m_idx = m_scroll + row
            if m_idx < m_count:
                line = format_entry(m_entries[m_idx], right_w)
                try:
                    stdscr.addstr(y, mid, line[:right_w],
                                  curses.color_pair(2))
                except curses.error:
                    pass

        # Bottom separator
        try:
            bot_sep = "\u2500" * left_w + "\u2534" + "\u2500" * right_w
            stdscr.addstr(height - 2, 0, bot_sep[:width - 1], curses.A_DIM)
        except curses.error:
            pass

        # Footer
        mode_str = ""
        if changes_only:
            mode_str = " [CHANGES]"
        elif unique_mode:
            mode_str = " [UNIQUE]"
        follow_str = "FOLLOW" if follow else "PAUSED"
        emu_keys = " +/-:spd [/]:inc" if state['emulate'] else ""
        footer = (f" q:quit f:{follow_str} c:chg u:uniq p:proxy e:emu"
                  f" j/k:scroll{emu_keys}"
                  f"  C:{c_count} M:{m_count}{mode_str}")

        if errors:
            err_str = " | ERRORS: " + "; ".join(errors)
            footer += err_str

        try:
            stdscr.addstr(height - 1, 0, footer[:width - 1],
                          curses.A_REVERSE)
        except curses.error:
            pass

        stdscr.refresh()

        # Key handling
        try:
            key = stdscr.getch()
        except curses.error:
            key = -1

        if key == ord('q') or key == ord('Q'):
            break
        elif key == ord('f') or key == ord('F') or key == ord(' '):
            follow = not follow
        elif key == ord('c'):
            changes_only = not changes_only
            unique_mode = False
            c_scroll = m_scroll = 0
        elif key == ord('u'):
            unique_mode = not unique_mode
            changes_only = False
            c_scroll = m_scroll = 0
        elif key == ord('p') or key == ord('P'):
            state['proxy'] = not state['proxy']
        elif key == ord('e') or key == ord('E'):
            if not state['emulate']:
                # Start emulation: stop proxy, start emulate thread
                state['proxy'] = False
                state['emulate'] = True
                if ser_kv_write:
                    t = threading.Thread(
                        target=emulate_thread,
                        args=(ser_kv_write, entries, lock, state),
                        daemon=True)
                    t.start()
                    threads.append(t)
            else:
                # Stop emulation: thread will exit on its own
                state['emulate'] = False
        elif key == ord('+') or key == ord('='):
            if state['emulate']:
                state['emu_speed'] = min(state['emu_speed'] + 1, 999)
        elif key == ord('-') or key == ord('_'):
            if state['emulate']:
                state['emu_speed'] = max(state['emu_speed'] - 1, 0)
        elif key == ord(']'):
            if state['emulate']:
                state['emu_incline'] = min(state['emu_incline'] + 1, 99)
        elif key == ord('['):
            if state['emulate']:
                state['emu_incline'] = max(state['emu_incline'] - 1, 0)
        elif key == ord('j') or key == curses.KEY_DOWN:
            c_scroll += 1
            m_scroll += 1
            follow = False
        elif key == ord('k') or key == curses.KEY_UP:
            c_scroll = max(0, c_scroll - 1)
            m_scroll = max(0, m_scroll - 1)
            follow = False
        elif key == curses.KEY_NPAGE:
            c_scroll += view_height
            m_scroll += view_height
            follow = False
        elif key == curses.KEY_PPAGE:
            c_scroll = max(0, c_scroll - view_height)
            m_scroll = max(0, m_scroll - view_height)
            follow = False

        time.sleep(0.05)

    # Cleanup
    state['running'] = False
    for s in (ser_kv_read, ser_kv_write, ser_bin):
        if s:
            try:
                s.close()
            except Exception:
                pass


def _filter_changes(entries):
    """Keep only entries where a key's value changed from last seen."""
    last = {}
    result = []
    for e in entries:
        ts, side, key, val, raw = e
        if last.get(key) != val:
            last[key] = val
            result.append(e)
    return result


def _filter_unique(entries):
    """Keep only the first occurrence of each (key, value) pair."""
    seen = set()
    result = []
    for e in entries:
        ts, side, key, val, raw = e
        pair = (key, val)
        if pair not in seen:
            seen.add(pair)
            result.append(e)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Dual Protocol Monitor — side-by-side view')
    parser.add_argument('--kv-read', default='controller_tx',
                        help='KV read port: name or device (default: controller_tx)')
    parser.add_argument('--kv-write', default='motor_rx',
                        help='KV write/proxy port: name or device (default: motor_rx)')
    parser.add_argument('--bin', '--bin-read', dest='bin_read', default='motor_tx',
                        help='Binary read port: name or device (default: motor_tx)')
    parser.add_argument('--baud', '-b', type=int, default=9600,
                        help='Baud rate (default: 9600)')
    args = parser.parse_args()
    curses.wrapper(lambda s: main(s, args))
