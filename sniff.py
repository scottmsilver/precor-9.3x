#!/usr/bin/env python3
"""
Packet sniffer with multiple modes.

Usage:
    python3 sniff.py [seconds]              - Show unique packets (raw hex)
    python3 sniff.py --all                  - Show ALL packets (not just unique)
    python3 sniff.py --parsed               - Show decoded/parsed packets
    python3 sniff.py --all --parsed         - All packets, parsed
    python3 sniff.py --graph [seconds]      - Build state transition graph
"""

import argparse
import sys
import time
from collections import defaultdict

import serial

from protocol import (
    SERIAL_PORT, BAUD_RATE, FRAME_START, NAMES,
    hex_str, decode_packet, get_direction
)


class GraphAnalyzer:
    """Builds a state transition graph from packet sequences."""

    def __init__(self):
        # transitions[(from_type, to_type)] = count
        self.transitions = defaultdict(int)
        # sequences[n] = {(type1, type2, ..., typen): count}
        self.sequences = defaultdict(lambda: defaultdict(int))
        self.packet_counts = defaultdict(int)
        self.all_packets = []  # (timestamp, frame_type, frame_bytes)
        self.last_type = None
        self.recent_types = []  # sliding window for sequence detection
        self.max_seq_len = 6  # detect sequences up to this length

    def add_packet(self, frame_type, frame_bytes):
        """Record a packet and update graph."""
        ts = time.time()
        self.all_packets.append((ts, frame_type, frame_bytes))
        self.packet_counts[frame_type] += 1

        # Record transition from last packet
        if self.last_type is not None:
            self.transitions[(self.last_type, frame_type)] += 1

        # Update sliding window and record sequences
        self.recent_types.append(frame_type)
        if len(self.recent_types) > self.max_seq_len:
            self.recent_types.pop(0)

        # Record all subsequences ending at current packet
        for length in range(2, len(self.recent_types) + 1):
            seq = tuple(self.recent_types[-length:])
            self.sequences[length][seq] += 1

        self.last_type = frame_type

    def type_name(self, ftype):
        """Get human-readable name for frame type."""
        return NAMES.get(ftype, f'0x{ftype:02X}')

    def detect_cycles(self):
        """Find repeating cycles in the packet stream."""
        cycles = []

        # Look for sequences that appear multiple times
        for length in range(2, self.max_seq_len + 1):
            for seq, count in self.sequences[length].items():
                if count >= 3:  # sequence appears at least 3 times
                    # Check if it's a "primitive" cycle (not just a subset)
                    is_primitive = True
                    for sub_len in range(2, length):
                        if length % sub_len == 0:
                            sub_seq = seq[:sub_len]
                            if sub_seq * (length // sub_len) == seq:
                                is_primitive = False
                                break
                    if is_primitive:
                        cycles.append((seq, count))

        # Sort by frequency
        cycles.sort(key=lambda x: -x[1])
        return cycles

    def find_dominant_cycle(self):
        """Find the most common repeating pattern (heartbeat cycle)."""
        # Group packets into windows and find repeating sequences
        type_stream = [p[1] for p in self.all_packets]
        if len(type_stream) < 4:
            return None

        # Try different cycle lengths
        best_cycle = None
        best_score = 0

        for cycle_len in range(3, min(20, len(type_stream) // 3)):
            # Count how many times each cycle_len sequence appears
            cycle_counts = defaultdict(int)
            for i in range(len(type_stream) - cycle_len + 1):
                seq = tuple(type_stream[i:i + cycle_len])
                cycle_counts[seq] += 1

            # Find the most common
            for seq, count in cycle_counts.items():
                if count >= 3:
                    # Score = count * length (prefer longer cycles that repeat)
                    score = count * len(seq)
                    if score > best_score:
                        best_score = score
                        best_cycle = (seq, count)

        return best_cycle

    def print_report(self):
        """Print analysis report."""
        print("\n" + "=" * 60)
        print("STATE GRAPH ANALYSIS")
        print("=" * 60)

        # Packet type counts
        print("\n--- Packet Types (by frequency) ---")
        sorted_counts = sorted(self.packet_counts.items(), key=lambda x: -x[1])
        for ftype, count in sorted_counts:
            print(f"  {self.type_name(ftype):10} : {count:5} packets")

        # State transitions
        print("\n--- State Transitions (from -> to) ---")
        sorted_trans = sorted(self.transitions.items(), key=lambda x: -x[1])
        for (from_t, to_t), count in sorted_trans[:20]:
            print(f"  {self.type_name(from_t):10} -> {self.type_name(to_t):10} : {count:5}")

        # Dominant cycle detection
        print("\n--- Detected Program Cycle ---")
        dominant = self.find_dominant_cycle()
        if dominant:
            seq, count = dominant
            print(f"  Repeating cycle (seen {count}x):")
            print(f"    Length: {len(seq)} packets")
            print(f"    Sequence:")
            for i, ftype in enumerate(seq):
                arrow = "->" if i < len(seq) - 1 else "->(repeat)"
                print(f"      {i + 1}. {self.type_name(ftype)} {arrow}")
        else:
            print("  No clear repeating cycle detected")

        # Common sequences
        print("\n--- Common Sequences ---")
        cycles = self.detect_cycles()
        shown = 0
        for seq, count in cycles[:10]:
            if count >= 3:
                names = " -> ".join(self.type_name(t) for t in seq)
                print(f"  [{count:3}x] {names}")
                shown += 1
        if shown == 0:
            print("  No recurring sequences found")

        # ASCII state diagram
        print("\n--- State Diagram (ASCII) ---")
        self.print_ascii_diagram()

        # DOT format for graphviz
        print("\n--- DOT Format (for graphviz) ---")
        self.print_dot()

        print()

    def print_ascii_diagram(self):
        """Print a simple ASCII representation of the state machine."""
        if not self.transitions:
            print("  (no transitions recorded)")
            return

        # Get unique types that have transitions
        types = set()
        for (from_t, to_t) in self.transitions:
            types.add(from_t)
            types.add(to_t)
        types = sorted(types)

        # Build adjacency display
        print()
        for from_t in types:
            outgoing = []
            for to_t in types:
                count = self.transitions.get((from_t, to_t), 0)
                if count > 0:
                    outgoing.append(f"{self.type_name(to_t)}({count})")
            if outgoing:
                print(f"  {self.type_name(from_t):10} => {', '.join(outgoing)}")

    def print_dot(self):
        """Print DOT format for graphviz visualization."""
        print('  digraph PacketFlow {')
        print('    rankdir=LR;')
        print('    node [shape=box];')

        # Add nodes with counts
        for ftype, count in self.packet_counts.items():
            name = self.type_name(ftype)
            print(f'    "{name}" [label="{name}\\n({count})"];')

        # Add edges with weights
        for (from_t, to_t), count in self.transitions.items():
            from_name = self.type_name(from_t)
            to_name = self.type_name(to_t)
            # Edge thickness based on count
            width = min(1 + count // 10, 5)
            print(f'    "{from_name}" -> "{to_name}" [label="{count}" penwidth={width}];')

        print('  }')


def extract_frame(buf):
    """Try to extract a frame from buffer. Returns (frame, remaining) or (None, buf)."""
    i = 0
    while i < len(buf) - 3:
        if buf[i] == FRAME_START:
            for j in range(i + 1, min(i + 50, len(buf) - 1)):
                if buf[j] == 0x45 and buf[j + 1] == 0x01:
                    frame = bytes(buf[i:j + 2])
                    return frame, buf[j + 2:]
            # No end found yet, wait for more data
            return None, buf
        else:
            i += 1
    return None, buf[i:] if i > 0 else buf


def format_packet(frame, num, delta_ms, parsed=False):
    """Format a packet for display (same format as monitor.py)."""
    if len(frame) < 4:
        return hex_str(frame)

    frame_type = frame[1]
    payload = bytes(frame[2:-2])  # Strip start byte, type, and end marker
    name = NAMES.get(frame_type, f'0x{frame_type:02X}')
    direction, _, _ = get_direction(frame_type)
    raw_hex = hex_str(frame)

    if not parsed:
        return f"+{delta_ms:6.1f}ms  {raw_hex}"

    meaning, _ = decode_packet(frame_type, payload)

    # Same format as monitor.py list view with timing
    return f"{num:<5}|+{delta_ms:6.1f}ms|{name:<8}|{direction:<4}|{raw_hex:<36}|{meaning}"


def run_sniff_mode(ser, duration, unique_only=True, parsed=False):
    """Sniff packets with configurable options."""
    buf = bytearray()
    seen = set()
    count = 0
    start = time.time()
    last_packet_time = start

    mode_desc = "unique" if unique_only else "all"
    fmt_desc = "parsed" if parsed else "raw"
    print(f"Sniffing {mode_desc} packets ({fmt_desc}) for {duration}s... (Ctrl+C to stop)")
    print()

    # Print header if parsed mode
    if parsed:
        print(f"{'#':<5}|{'DELTA':^9}|{'TYPE':<8}|{'DIR':<4}|{'RAW BYTES':<36}|{'MEANING'}")
        print("-" * 95)

    try:
        while time.time() - start < duration:
            if ser.in_waiting:
                buf.extend(ser.read(ser.in_waiting))

            while True:
                frame, buf = extract_frame(buf)
                if frame is None:
                    break

                now = time.time()
                delta_ms = (now - last_packet_time) * 1000
                last_packet_time = now

                count += 1
                is_new = frame not in seen
                seen.add(frame)

                if not unique_only or is_new:
                    print(format_packet(frame, count, delta_ms, parsed))

            # Trim buffer if too long without frames
            if len(buf) > 100:
                buf = buf[-50:]

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print()
    print(f"Done. {count} total packets, {len(seen)} unique.")


def run_graph_mode(ser, duration):
    """Graph mode: build state transition graph."""
    buf = bytearray()
    analyzer = GraphAnalyzer()
    start = time.time()
    packet_count = 0

    print(f"Building packet graph for {duration}s... (Ctrl+C to stop early)")

    try:
        while time.time() - start < duration:
            if ser.in_waiting:
                buf.extend(ser.read(ser.in_waiting))

            while True:
                frame, buf = extract_frame(buf)
                if frame is None:
                    break
                if len(frame) >= 4:
                    frame_type = frame[1]
                    analyzer.add_packet(frame_type, frame)
                    packet_count += 1

                    # Progress indicator
                    if packet_count % 100 == 0:
                        elapsed = time.time() - start
                        print(f"\r  {packet_count} packets, {elapsed:.1f}s elapsed...", end='', flush=True)

            if len(buf) > 100:
                buf = buf[-50:]

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass

    print(f"\r  Captured {packet_count} packets.{' ' * 20}")
    analyzer.print_report()


def main():
    parser = argparse.ArgumentParser(
        description='Packet sniffer with multiple modes'
    )
    parser.add_argument('--graph', '-g', action='store_true',
                        help='Build state transition graph to detect patterns')
    parser.add_argument('--all', '-a', action='store_true',
                        help='Show ALL packets (default: unique only)')
    parser.add_argument('--parsed', '-p', action='store_true',
                        help='Show parsed/decoded packets (default: raw hex)')
    parser.add_argument('duration', nargs='?', type=float, default=30.0,
                        help='Sniff duration in seconds (default: 30)')

    args = parser.parse_args()

    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)

    try:
        if args.graph:
            run_graph_mode(ser, args.duration)
        else:
            run_sniff_mode(ser, args.duration,
                          unique_only=not args.all,
                          parsed=args.parsed)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
