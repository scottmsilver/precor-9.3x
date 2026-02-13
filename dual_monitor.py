#!/usr/bin/env python3
"""
Dual Protocol Monitor — Client Edition

Connects to treadmill_io C binary via Unix socket for low-latency
GPIO I/O. Displays KV protocol data in a curses split-pane TUI.

  Left pane:  Console → Motor (and emulate output)
  Right pane: Motor → Console

Requires: sudo ./treadmill_io running

Usage:
  python3 dual_monitor.py
"""

import argparse
import curses
import threading
import time
from collections import deque

from treadmill_client import MAX_INCLINE, MAX_SPEED_TENTHS, SOCK_PATH, TreadmillClient

MAX_ENTRIES = 2000


def format_entry(entry, width):
    """Format a single entry as a display string."""
    ts, side, key, val, raw = entry
    if val:
        line = f" {ts:6.1f}  {key:<8} {val}"
    else:
        line = f" {ts:6.1f}  {key}"
    line = line.replace("\x00", "")
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
    curses.init_pair(1, curses.COLOR_GREEN, -1)  # console side
    curses.init_pair(2, curses.COLOR_CYAN, -1)  # motor side
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # headers
    curses.init_pair(4, curses.COLOR_RED, -1)  # proxy indicator
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # emulate indicator

    entries = deque(maxlen=MAX_ENTRIES)
    lock = threading.Lock()

    state = {
        "running": True,
        "proxy": True,
        "emulate": False,
        "emu_speed": 0,  # tenths of mph (12 = 1.2 mph)
        "emu_incline": 0,
        "console_bytes": 0,
        "motor_bytes": 0,
    }

    # Connect to treadmill_io
    client = TreadmillClient(args.socket)

    def on_message(msg):
        msg_type = msg.get("type")
        if msg_type == "kv":
            ts = msg.get("ts", 0)
            source = msg.get("source", "")
            key = msg.get("key", "")
            value = msg.get("value", "")
            if source == "console":
                side = "C"
            elif source == "motor":
                side = "M"
            elif source == "emulate":
                side = "E"
            else:
                side = "?"
            with lock:
                entries.append((ts, side, key, value, b""))
        elif msg_type == "status":
            state["proxy"] = msg.get("proxy", False)
            state["emulate"] = msg.get("emulate", False)
            state["emu_speed"] = msg.get("emu_speed", 0)
            state["emu_incline"] = msg.get("emu_incline", 0)
            state["console_bytes"] = msg.get("console_bytes", 0)
            state["motor_bytes"] = msg.get("motor_bytes", 0)

    client.on_message = on_message

    try:
        client.connect()
    except Exception as e:
        raise RuntimeError(f"Cannot connect to treadmill_io: {e}\n" "Is 'sudo ./treadmill_io' running?")

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

            c_entries = [e for e in all_entries if e[1] in ("C", "E")]
            m_entries = [e for e in all_entries if e[1] == "M"]

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

            left_title = " Console\u2192Motor (via treadmill_io)"
            right_title = "  Motor responses"

            if state["emulate"]:
                mph = state["emu_speed"] / 10
                status_str = f" [EMU {mph:.1f}mph inc={state['emu_incline']}]"
                status_color = curses.color_pair(5) | curses.A_BOLD
            elif state["proxy"]:
                status_str = " [PROXY]"
                status_color = curses.color_pair(4) | curses.A_BOLD
            else:
                status_str = ""
                status_color = 0

            try:
                stdscr.addstr(0, 0, left_title[:left_w].ljust(left_w), curses.color_pair(3) | curses.A_BOLD)
                stdscr.addstr(0, left_w, "\u2502", curses.A_DIM)
                stdscr.addstr(0, mid, right_title[:right_w], curses.color_pair(3) | curses.A_BOLD)
                if status_str:
                    px = left_w - len(status_str)
                    if px > 0:
                        stdscr.addstr(0, px, status_str, status_color)
            except curses.error:
                pass

            try:
                sep = "\u2500" * left_w + "\u253C" + "\u2500" * right_w
                stdscr.addstr(1, 0, sep[: width - 1], curses.A_DIM)
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
                        stdscr.addstr(y, 0, line.ljust(left_w)[:left_w], curses.color_pair(1))
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
                        stdscr.addstr(y, mid, line[:right_w], curses.color_pair(2))
                    except curses.error:
                        pass

            try:
                bot_sep = "\u2500" * left_w + "\u2534" + "\u2500" * right_w
                stdscr.addstr(height - 2, 0, bot_sep[: width - 1], curses.A_DIM)
            except curses.error:
                pass

            mode_str = ""
            if changes_only:
                mode_str = " [CHANGES]"
            elif unique_mode:
                mode_str = " [UNIQUE]"
            follow_str = "FOLLOW" if follow else "PAUSED"
            emu_keys = " +/-:spd [/]:inc" if state["emulate"] else ""
            footer = (
                f" q:quit f:{follow_str} c:chg u:uniq p:proxy e:emu"
                f" j/k:scroll{emu_keys}"
                f"  C:{c_count} M:{m_count}{mode_str}"
            )

            try:
                stdscr.addstr(height - 1, 0, footer[: width - 1], curses.A_REVERSE)
            except curses.error:
                pass

            stdscr.refresh()

            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if key == ord("q") or key == ord("Q"):
                break
            elif key == ord("f") or key == ord("F") or key == ord(" "):
                follow = not follow
            elif key == ord("c"):
                changes_only = not changes_only
                unique_mode = False
                c_scroll = m_scroll = 0
            elif key == ord("u"):
                unique_mode = not unique_mode
                changes_only = False
                c_scroll = m_scroll = 0
            elif key == ord("p") or key == ord("P"):
                if not state["proxy"]:
                    state["emulate"] = False
                    state["proxy"] = True
                    client.set_proxy(True)
                else:
                    state["proxy"] = False
                    client.set_proxy(False)
            elif key == ord("e") or key == ord("E"):
                if not state["emulate"]:
                    state["proxy"] = False
                    state["emulate"] = True
                    client.set_emulate(True)
                else:
                    state["emulate"] = False
                    client.set_emulate(False)
            elif key == ord("+") or key == ord("="):
                if state["emulate"]:
                    state["emu_speed"] = min(state["emu_speed"] + 5, MAX_SPEED_TENTHS)
                    client.set_speed(state["emu_speed"] / 10)
            elif key == ord("-") or key == ord("_"):
                if state["emulate"]:
                    state["emu_speed"] = max(state["emu_speed"] - 5, 0)
                    client.set_speed(state["emu_speed"] / 10)
            elif key == ord("]"):
                if state["emulate"]:
                    state["emu_incline"] = min(state["emu_incline"] + 1, MAX_INCLINE)
                    client.set_incline(state["emu_incline"])
            elif key == ord("["):
                if state["emulate"]:
                    state["emu_incline"] = max(state["emu_incline"] - 1, 0)
                    client.set_incline(state["emu_incline"])
            elif key == ord("j") or key == curses.KEY_DOWN:
                c_scroll += 1
                m_scroll += 1
                follow = False
            elif key == ord("k") or key == curses.KEY_UP:
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
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dual Protocol Monitor — Client Edition")
    parser.add_argument("--socket", "-s", default=SOCK_PATH, help="Path to treadmill_io Unix socket")
    args = parser.parse_args()
    curses.wrapper(lambda s: main(s, args))
