#!/usr/bin/env python3
"""
Dual Protocol Monitor — GPIO Edition

Reads treadmill KV protocol via Raspberry Pi GPIO pins with pigpio
bit-banged serial (inverted polarity for RS-485 signaling).

  Left pane:  Console → Motor (pin 6 console side)
  Right pane: Motor → Console (pin 3 motor responses)
  Write pin:  Pin 6 motor side — proxy forwarding or emulation output

Requires: sudo pigpiod

Usage:
  python3 dual_monitor.py
  python3 dual_monitor.py --gpio-console 27 --gpio-write 22 --gpio-motor 17
"""

import argparse
import curses
import threading
import time
from collections import deque

import pigpio

from gpio_pins import (
    get_gpio, BAUD, parse_kv_stream, build_kv_cmd,
    gpio_read_open, gpio_read_close, gpio_write_open, gpio_write_close,
    gpio_write_bytes, KV_CYCLE, KV_BURSTS,
)

MAX_ENTRIES = 2000


def console_read_thread(pi, gpio_read, gpio_write,
                        entries, lock, state, write_lock):
    """Read KV from console side of pin 6. Proxy-forward to motor if enabled."""
    buf = bytearray()
    while state['running']:
        count, data = pi.bb_serial_read(gpio_read)
        if count > 0:
            state['console_bytes'] += count
            if state['proxy']:
                gpio_write_bytes(pi, gpio_write, data, write_lock)
            buf.extend(data)
            pairs, buf = parse_kv_stream(buf)
            if pairs:
                now = time.time() - state['start']
                with lock:
                    for key, val in pairs:
                        entries.append((now, 'C', key, val, b''))
        else:
            time.sleep(0.02)


def motor_read_thread(pi, gpio, entries, lock, state):
    """Read KV responses from motor on pin 3."""
    buf = bytearray()
    while state['running']:
        count, data = pi.bb_serial_read(gpio)
        if count > 0:
            state['motor_bytes'] += count
            buf.extend(data)
            pairs, buf = parse_kv_stream(buf)
            if pairs:
                now = time.time() - state['start']
                with lock:
                    for key, val in pairs:
                        entries.append((now, 'M', key, val, b''))
        else:
            time.sleep(0.02)


def emulate_thread(pi, gpio_write, entries, lock, state, write_lock):
    """Send KV cycle to motor, emulating the controller."""
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
                gpio_write_bytes(pi, gpio_write, cmd, write_lock)
                now = time.time() - state['start']
                val_str = f'{value}' if value is not None else ''
                with lock:
                    entries.append((now, 'E', key, val_str, b''))
            time.sleep(0.1)


def format_entry(entry, width):
    """Format a single entry as a display string."""
    ts, side, key, val, raw = entry
    if val:
        line = f" {ts:6.1f}  {key:<8} {val}"
    else:
        line = f" {ts:6.1f}  {key}"
    line = line.replace('\x00', '')
    return line[:width] if len(line) > width else line


def _filter_changes(entries):
    """Keep only entries where a key's value changed."""
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


def main(stdscr, args):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)    # console side
    curses.init_pair(2, curses.COLOR_CYAN, -1)     # motor side
    curses.init_pair(3, curses.COLOR_YELLOW, -1)   # headers
    curses.init_pair(4, curses.COLOR_RED, -1)       # proxy indicator
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)   # emulate indicator

    entries = deque(maxlen=MAX_ENTRIES)
    lock = threading.Lock()
    write_lock = threading.Lock()

    state = {
        'running': True,
        'proxy': True,
        'start': time.time(),
        'console_bytes': 0,
        'motor_bytes': 0,
        'emulate': False,
        'emu_speed': 0,        # tenths of mph (12 = 1.2 mph)
        'emu_speed_raw': 0,    # hundredths, sent as hex (120 = 0x78)
        'emu_incline': 0,
    }

    gpio_console = args.gpio_console
    gpio_wr = args.gpio_write
    gpio_motor = args.gpio_motor

    pi = pigpio.pi()
    if not pi.connected:
        raise RuntimeError("Cannot connect to pigpiod. Run: sudo pigpiod")

    gpio_read_open(pi, gpio_console)
    gpio_read_open(pi, gpio_motor)
    gpio_write_open(pi, gpio_wr)

    threads = []
    t = threading.Thread(target=console_read_thread,
                         args=(pi, gpio_console, gpio_wr,
                               entries, lock, state, write_lock),
                         daemon=True)
    t.start()
    threads.append(t)

    t = threading.Thread(target=motor_read_thread,
                         args=(pi, gpio_motor, entries, lock, state),
                         daemon=True)
    t.start()
    threads.append(t)

    follow = True
    changes_only = False
    unique_mode = False
    c_scroll = 0
    m_scroll = 0

    stdscr.nodelay(True)

    try:
        while True:
            height, width = stdscr.getmaxyx()
            mid = width // 2

            with lock:
                all_entries = list(entries)

            c_entries = [e for e in all_entries if e[1] in ('C', 'E')]
            m_entries = [e for e in all_entries if e[1] == 'M']

            if changes_only:
                c_entries = _filter_changes(c_entries)
                m_entries = _filter_changes(m_entries)
            elif unique_mode:
                c_entries = _filter_unique(c_entries)
                m_entries = _filter_unique(m_entries)

            view_height = max(1, height - 4)
            c_count = len(c_entries)
            m_count = len(m_entries)

            if follow:
                c_scroll = max(0, c_count - view_height)
                m_scroll = max(0, m_count - view_height)
            c_scroll = max(0, min(c_scroll, max(0, c_count - view_height)))
            m_scroll = max(0, min(m_scroll, max(0, m_count - view_height)))

            stdscr.erase()

            left_w = mid - 1
            right_w = width - mid - 1

            left_title = f" Console\u2192Motor (GPIO {gpio_console}\u2192{gpio_wr})"
            right_title = f"  Motor responses (GPIO {gpio_motor})"

            if state['emulate']:
                mph = state['emu_speed'] / 10
                status_str = (f" [EMU {mph:.1f}mph"
                              f" inc={state['emu_incline']}]")
                status_color = curses.color_pair(5) | curses.A_BOLD
            elif state['proxy']:
                status_str = " [PROXY]"
                status_color = curses.color_pair(4) | curses.A_BOLD
            else:
                status_str = ""
                status_color = 0

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

            try:
                sep = "\u2500" * left_w + "\u253C" + "\u2500" * right_w
                stdscr.addstr(1, 0, sep[:width - 1], curses.A_DIM)
            except curses.error:
                pass

            for row in range(view_height):
                y = row + 2
                if y >= height - 2:
                    break

                c_idx = c_scroll + row
                if c_idx < c_count:
                    line = format_entry(c_entries[c_idx], left_w)
                    try:
                        stdscr.addstr(y, 0, line.ljust(left_w)[:left_w],
                                      curses.color_pair(1))
                    except curses.error:
                        pass

                try:
                    stdscr.addstr(y, left_w, "\u2502", curses.A_DIM)
                except curses.error:
                    pass

                m_idx = m_scroll + row
                if m_idx < m_count:
                    line = format_entry(m_entries[m_idx], right_w)
                    try:
                        stdscr.addstr(y, mid, line[:right_w],
                                      curses.color_pair(2))
                    except curses.error:
                        pass

            try:
                bot_sep = "\u2500" * left_w + "\u2534" + "\u2500" * right_w
                stdscr.addstr(height - 2, 0, bot_sep[:width - 1], curses.A_DIM)
            except curses.error:
                pass

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

            try:
                stdscr.addstr(height - 1, 0, footer[:width - 1],
                              curses.A_REVERSE)
            except curses.error:
                pass

            stdscr.refresh()

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
                    state['proxy'] = False
                    state['emulate'] = True
                    t = threading.Thread(
                        target=emulate_thread,
                        args=(pi, gpio_wr,
                              entries, lock, state, write_lock),
                        daemon=True)
                    t.start()
                    threads.append(t)
                else:
                    state['emulate'] = False
            elif key == ord('+') or key == ord('='):
                if state['emulate']:
                    state['emu_speed'] = min(state['emu_speed'] + 5, 120)
                    state['emu_speed_raw'] = state['emu_speed'] * 10
            elif key == ord('-') or key == ord('_'):
                if state['emulate']:
                    state['emu_speed'] = max(state['emu_speed'] - 5, 0)
                    state['emu_speed_raw'] = state['emu_speed'] * 10
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

    finally:
        state['running'] = False
        gpio_read_close(pi, gpio_console)
        gpio_read_close(pi, gpio_motor)
        gpio_write_close(pi, gpio_wr)
        pi.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Dual Protocol Monitor — GPIO Edition')
    parser.add_argument('--gpio-console', type=int,
                        default=get_gpio('console_read'),
                        help='GPIO for pin 6 console side read')
    parser.add_argument('--gpio-write', type=int,
                        default=get_gpio('motor_write'),
                        help='GPIO for pin 6 motor side write')
    parser.add_argument('--gpio-motor', type=int,
                        default=get_gpio('motor_read'),
                        help='GPIO for pin 3 motor responses read')
    parser.add_argument('--baud', '-b', type=int, default=BAUD,
                        help=f'Baud rate (default: {BAUD})')
    args = parser.parse_args()
    curses.wrapper(lambda s: main(s, args))
