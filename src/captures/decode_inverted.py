#!/usr/bin/env python3
"""
Decode logic analyzer captures with INVERTED UART polarity.

Standard UART: idle=HIGH(1), start bit = falling edge (1->0)
Inverted (RS-485 on TTL): idle=LOW(0), start bit = rising edge (0->1), data bits inverted

This script decodes both channels of a capture with both normal and inverted
polarity to determine which interpretation produces valid data.
"""

import csv
import sys
from collections import defaultdict

BAUD = 9600
BIT_PERIOD = 1.0 / BAUD
HALF_BIT = BIT_PERIOD / 2.0


def load_csv(path):
    rows = []
    with open(path, "r") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 9:
                continue
            t = float(row[0])
            channels = [int(row[i + 1]) for i in range(8)]
            rows.append((t, channels))
    return rows


def extract_edges(rows, channel):
    edges = []
    prev_val = None
    for t, chs in rows:
        v = chs[channel]
        if v != prev_val:
            edges.append((t, v))
            prev_val = v
    return edges


def get_signal_state(edges, t):
    lo, hi = 0, len(edges) - 1
    if t < edges[0][0]:
        return edges[0][1]
    result = edges[0][1]
    while lo <= hi:
        mid = (lo + hi) // 2
        if edges[mid][0] <= t:
            result = edges[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def decode_uart(edges, inverted=False):
    """
    Decode UART from edge list.
    inverted=False: standard (idle=1, start=falling 1->0)
    inverted=True:  inverted (idle=0, start=rising 0->1, data bits inverted)
    """
    decoded = []
    i = 0
    n = len(edges)

    idle_val = 0 if inverted else 1
    start_val = 1 if inverted else 0

    while i < n:
        t_edge, val = edges[i]

        # Look for start bit edge
        if val == start_val and i > 0 and edges[i - 1][1] == idle_val:
            start_time = t_edge

            # Verify start bit at center
            t_start_center = t_edge + HALF_BIT
            sv = get_signal_state(edges, t_start_center)
            if sv != start_val:
                i += 1
                continue

            # Sample 8 data bits
            byte_val = 0
            for bit_idx in range(8):
                t_sample = t_edge + BIT_PERIOD * (1 + bit_idx) + HALF_BIT
                bit_val = get_signal_state(edges, t_sample)
                if inverted:
                    bit_val = 1 - bit_val  # invert data bits
                byte_val |= (bit_val << bit_idx)

            # Check stop bit
            t_stop = t_edge + BIT_PERIOD * 9 + HALF_BIT
            stop_raw = get_signal_state(edges, t_stop)
            stop_ok = (stop_raw == idle_val)  # stop bit should be idle level

            end_time = t_edge + BIT_PERIOD * 10
            decoded.append((start_time, end_time, byte_val, stop_ok))

            while i < n and edges[i][0] < end_time - BIT_PERIOD * 0.1:
                i += 1
            continue

        i += 1

    return decoded


def analyze_decode(decoded, label):
    """Print analysis of decoded bytes."""
    if not decoded:
        print(f"  {label}: 0 bytes decoded")
        return

    good = sum(1 for _, _, _, s in decoded if s)
    total = len(decoded)
    pct = good / total * 100 if total > 0 else 0
    print(f"  {label}: {total} bytes, {good} good stop bits ({pct:.1f}%)")

    # Show as text
    raw_bytes = bytes(b for _, _, b, _ in decoded)

    # Check for KV text patterns
    kv_count = raw_bytes.count(b'[')
    ff_count = raw_bytes.count(b'\xff')
    r52_count = sum(1 for b in raw_bytes if b == 0x52)

    print(f"    '[' count: {kv_count}, 0xFF count: {ff_count}, 0x52 count: {r52_count}")

    if kv_count > 5:
        print(f"    Looks like KV TEXT protocol!")
        # Extract KV pairs
        text = raw_bytes.decode('latin-1')
        pairs = []
        i = 0
        while i < len(text):
            start = text.find('[', i)
            if start == -1:
                break
            end = text.find(']', start)
            if end == -1:
                break
            pairs.append(text[start:end+1])
            i = end + 1
        print(f"    Found {len(pairs)} KV pairs")
        # Show unique keys
        keys = defaultdict(list)
        for p in pairs:
            if ':' in p:
                k, v = p[1:-1].split(':', 1)
                keys[k].append(v)
            else:
                keys[p[1:-1]].append('')
        print(f"    Unique keys ({len(keys)}):")
        for k in sorted(keys.keys()):
            vals = set(keys[k])
            sample = list(vals)[:5]
            print(f"      {k}: {len(keys[k])} occurrences, values={sample}")

    elif r52_count > 5:
        print(f"    Looks like BINARY R...E protocol")
        # Show first few frames
        print(f"    First 100 bytes hex: {' '.join(f'{b:02X}' for b in raw_bytes[:100])}")

    # Show raw text for first ~200 bytes
    text_repr = ''.join(chr(b) if 0x20 <= b < 0x7F else f'\\x{b:02x}' for b in raw_bytes[:200])
    print(f"    First 200 bytes as text: {text_repr}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 decode_inverted.py <csv_file> [channel]")
        print("  channel: 2 (pin 3), 5 (pin 6), or 'all' (default: all)")
        sys.exit(1)

    csv_file = sys.argv[1]
    ch_arg = sys.argv[2] if len(sys.argv) > 2 else 'all'

    print(f"Loading {csv_file}...")
    rows = load_csv(csv_file)
    print(f"  {len(rows)} samples, {rows[0][0]:.6f}s to {rows[-1][0]:.6f}s")

    # Find active channels
    active = set()
    prev = rows[0][1]
    for _, chs in rows[1:]:
        for i in range(8):
            if chs[i] != prev[i]:
                active.add(i)
        prev = chs

    print(f"  Active channels: {sorted(active)}")

    channels = []
    if ch_arg == 'all':
        if 5 in active:
            channels.append((5, "Channel 5 (Pin 6)"))
        if 2 in active:
            channels.append((2, "Channel 2 (Pin 3)"))
        if not channels:
            channels = [(ch, f"Channel {ch}") for ch in sorted(active)]
    else:
        ch = int(ch_arg)
        channels = [(ch, f"Channel {ch}")]

    for ch, label in channels:
        print(f"\n{'='*70}")
        print(f"{label}")
        print(f"{'='*70}")

        edges = extract_edges(rows, ch)
        print(f"  {len(edges)} edges")

        # Check idle state
        idle_durations = {0: 0.0, 1: 0.0}
        for i in range(len(edges) - 1):
            dur = edges[i+1][0] - edges[i][0]
            if dur > 0.003:  # gaps > 3ms = idle
                idle_durations[edges[i][1]] += dur
        total_idle = idle_durations[0] + idle_durations[1]
        if total_idle > 0:
            print(f"  Idle analysis (gaps > 3ms):")
            print(f"    Idle at 0: {idle_durations[0]:.4f}s ({idle_durations[0]/total_idle*100:.1f}%)")
            print(f"    Idle at 1: {idle_durations[1]:.4f}s ({idle_durations[1]/total_idle*100:.1f}%)")
            dominant_idle = 0 if idle_durations[0] > idle_durations[1] else 1
            print(f"    Dominant idle: {dominant_idle} -> {'INVERTED (RS-485)' if dominant_idle == 0 else 'STANDARD (TTL)'}")

        print(f"\n  --- Standard polarity (idle=1, start=falling) ---")
        decoded_std = decode_uart(edges, inverted=False)
        analyze_decode(decoded_std, "Standard")

        print(f"\n  --- Inverted polarity (idle=0, start=rising) ---")
        decoded_inv = decode_uart(edges, inverted=True)
        analyze_decode(decoded_inv, "Inverted")


if __name__ == "__main__":
    main()
