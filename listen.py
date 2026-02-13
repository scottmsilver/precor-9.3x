#!/usr/bin/env python3
"""
Simple KV protocol listener via treadmill_io.

Connects to the treadmill_io C binary and prints KV pairs as they arrive.

Usage:
    python3 listen.py                  # Listen on all sources
    python3 listen.py --source motor   # Only motor responses
    python3 listen.py --changes        # Only show when values change
    python3 listen.py --unique         # Only show unique key:value pairs
"""

import argparse
import threading

from treadmill_client import SOCK_PATH, TreadmillClient


def main():
    parser = argparse.ArgumentParser(description="Listen for KV protocol via treadmill_io")
    parser.add_argument(
        "--source",
        "-S",
        choices=["all", "console", "motor", "emulate"],
        default="all",
        help="Filter by source (default: all)",
    )
    parser.add_argument("--changes", "-c", action="store_true", help="Only show when a key's value changes")
    parser.add_argument("--unique", "-u", action="store_true", help="Only show each unique (key, value) pair once")
    parser.add_argument("--socket", "-s", default=SOCK_PATH, help="Path to treadmill_io Unix socket")
    args = parser.parse_args()

    mode = "changes" if args.changes else "unique" if args.unique else "all"
    source_filter = args.source

    last_seen = {}
    seen = set()
    shown = 0
    stop_event = threading.Event()

    def on_message(msg):
        nonlocal shown
        if msg.get("type") != "kv":
            return

        source = msg.get("source", "")
        if source_filter != "all" and source != source_filter:
            return

        key = msg.get("key", "")
        val = msg.get("value", "")
        ts = msg.get("ts", 0)

        if mode == "changes":
            if last_seen.get(key) == val:
                return
            last_seen[key] = val
        elif mode == "unique":
            pair = (key, val)
            if pair in seen:
                return
            seen.add(pair)

        shown += 1
        src_tag = source[0].upper() if source else "?"
        if val:
            print(f"{ts:10.2f}  [{src_tag}]  {key:<12} = {val}")
        else:
            print(f"{ts:10.2f}  [{src_tag}]  {key:<12}")

    client = TreadmillClient(args.socket)
    client.on_message = on_message

    try:
        client.connect()
    except Exception as e:
        print(f"ERROR: Cannot connect to treadmill_io: {e}")
        print("Is 'sudo ./treadmill_io' running?")
        return 1

    print(f"Listening via treadmill_io (source={source_filter}, mode={mode})")
    print("Press Ctrl+C to stop\n")

    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass

    client.close()
    print(f"\n{shown} entries shown")


if __name__ == "__main__":
    main()
