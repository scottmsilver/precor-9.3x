#!/usr/bin/env python3
"""
Analyze logic analyzer CSV capture of treadmill serial protocol.

Decodes 9600 baud 8N1 UART on two channels that share a half-duplex bus:
  - Channel 5 (pin 6, 1-based): Controller->Motor, binary R...E frames ending 45 01
  - Channel 2 (pin 3, 1-based): Motor->Controller, binary R...E frames ending 45 00

Key finding: The two channels take turns (half-duplex). When one transmits,
the other sits at 0 (bus released). Standard UART polarity: idle=HIGH(1),
start bit=LOW(0), 8 data bits LSB-first, 1 stop bit HIGH(1).

The last byte of each frame may have a "bad" stop bit because the transmitter
releases the bus (goes to 0) after the final byte.

Bit period at 9600 baud = 1/9600 = ~104.167 us
"""

import csv
from collections import defaultdict

# ── Constants ──────────────────────────────────────────────────────────────
BAUD = 9600
BIT_PERIOD = 1.0 / BAUD  # ~104.167 us
HALF_BIT = BIT_PERIOD / 2.0

CSV_FILE = "/home/ssilver/development/treadmill/TRY4.csv"

# Channels of interest (0-based from CSV)
CH_PIN3 = 2   # Motor->Controller binary frames
CH_PIN6 = 5   # Controller->Motor binary frames


def load_csv(path):
    """Load the CSV and return list of (time, [ch0..ch7])."""
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


def find_active_channels(rows):
    """Find which channels toggle (have both 0 and 1 values)."""
    seen_0 = set()
    seen_1 = set()
    for _, chs in rows:
        for i, v in enumerate(chs):
            if v == 0:
                seen_0.add(i)
            else:
                seen_1.add(i)
    return sorted(seen_0 & seen_1)


def extract_edges(rows, channel):
    """Extract list of (time, value) for a specific channel, only at transitions."""
    edges = []
    prev_val = None
    for t, chs in rows:
        v = chs[channel]
        if v != prev_val:
            edges.append((t, v))
            prev_val = v
    return edges


def get_signal_state(edges, t):
    """Get signal state at a given time using binary search on edges list."""
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


def decode_uart_sequential(edges):
    """
    Decode 9600 8N1 UART from edge list sequentially.
    Returns list of (start_time, end_time, byte_value, stop_ok).

    UART: idle=1 (HIGH), start bit=0 (LOW), 8 data LSB-first, stop bit=1 (HIGH)

    Sequential: after decoding one byte, skip to the next falling edge after
    the byte ends. This prevents mid-byte transitions from being interpreted
    as new start bits.
    """
    decoded = []
    i = 0
    n = len(edges)

    while i < n:
        t_edge, val = edges[i]

        # Look for falling edge (1->0) = start bit
        if val == 0 and i > 0 and edges[i - 1][1] == 1:
            start_time = t_edge

            # Verify start bit at center of start bit period
            t_start_center = t_edge + HALF_BIT
            start_val = get_signal_state(edges, t_start_center)
            if start_val != 0:
                i += 1
                continue

            # Sample 8 data bits at center of each bit period
            byte_val = 0
            for bit_idx in range(8):
                t_sample = t_edge + BIT_PERIOD * (1 + bit_idx) + HALF_BIT
                bit_val = get_signal_state(edges, t_sample)
                byte_val |= (bit_val << bit_idx)  # LSB first

            # Check stop bit
            t_stop = t_edge + BIT_PERIOD * 9 + HALF_BIT
            stop_val = get_signal_state(edges, t_stop)

            end_time = t_edge + BIT_PERIOD * 10

            decoded.append((start_time, end_time, byte_val, stop_val == 1))

            # Skip to first edge after end of this byte
            while i < n and edges[i][0] < end_time - BIT_PERIOD * 0.1:
                i += 1
            continue

        i += 1

    return decoded


def group_frames_45_01(decoded_bytes):
    """
    Group bytes into R...E frames with 0x45 0x01 end marker (Pin 6 / Channel 5).
    Frames start with 0x52 ('R') and end with the sequence 0x45 0x01.
    Returns list of (start_time, end_time, [bytes]).
    """
    frames = []
    current_bytes = []
    current_times = []
    in_frame = False

    for start_t, end_t, bval, stop_ok in decoded_bytes:
        if bval == 0x52 and not in_frame:
            in_frame = True
            current_bytes = [bval]
            current_times = [(start_t, end_t)]
        elif in_frame:
            current_bytes.append(bval)
            current_times.append((start_t, end_t))
            # Check for 45 01 end marker
            if len(current_bytes) >= 2 and current_bytes[-2] == 0x45 and current_bytes[-1] == 0x01:
                frames.append((
                    current_times[0][0],
                    current_times[-1][1],
                    list(current_bytes)
                ))
                in_frame = False
                current_bytes = []
                current_times = []

    # Flush incomplete frame
    if current_bytes:
        frames.append((
            current_times[0][0],
            current_times[-1][1],
            list(current_bytes)
        ))

    return frames


def group_frames_45_00(decoded_bytes):
    """
    Group bytes into R...E frames with 0x45 0x00 end marker (Pin 3 / Channel 2).
    Frames start with 0x52 ('R') and end with 0x45 0x00.
    Also handles frames that end with just 0x45 followed by next frame's 0x52.
    Returns list of (start_time, end_time, [bytes]).
    """
    frames = []
    current_bytes = []
    current_times = []
    in_frame = False

    for start_t, end_t, bval, stop_ok in decoded_bytes:
        if bval == 0x52 and not in_frame:
            in_frame = True
            current_bytes = [bval]
            current_times = [(start_t, end_t)]
        elif bval == 0x52 and in_frame:
            # New frame start while in frame - check if previous ended with 0x45
            if current_bytes and current_bytes[-1] == 0x45:
                frames.append((
                    current_times[0][0],
                    current_times[-1][1],
                    list(current_bytes)
                ))
            elif current_bytes:
                # Flush incomplete
                frames.append((
                    current_times[0][0],
                    current_times[-1][1],
                    list(current_bytes)
                ))
            current_bytes = [bval]
            current_times = [(start_t, end_t)]
        elif in_frame:
            current_bytes.append(bval)
            current_times.append((start_t, end_t))
            # Check for 45 00 end marker
            if len(current_bytes) >= 2 and current_bytes[-2] == 0x45 and current_bytes[-1] == 0x00:
                frames.append((
                    current_times[0][0],
                    current_times[-1][1],
                    list(current_bytes)
                ))
                in_frame = False
                current_bytes = []
                current_times = []

    # Flush incomplete frame
    if current_bytes:
        frames.append((
            current_times[0][0],
            current_times[-1][1],
            list(current_bytes)
        ))

    return frames


def group_by_idle_gap(decoded_bytes, gap_threshold_ms=5.0):
    """
    Alternative grouping: split into bursts by idle gaps.
    Any gap > gap_threshold_ms between consecutive bytes = new burst.
    Returns list of (start_time, end_time, [bytes]).
    """
    bursts = []
    current_bytes = []
    current_start = None
    current_end = None
    prev_end = None

    for start_t, end_t, bval, stop_ok in decoded_bytes:
        if prev_end is not None and (start_t - prev_end) > gap_threshold_ms / 1000.0:
            if current_bytes:
                bursts.append((current_start, current_end, list(current_bytes)))
            current_bytes = [bval]
            current_start = start_t
            current_end = end_t
        else:
            if current_start is None:
                current_start = start_t
            current_bytes.append(bval)
            current_end = end_t
        prev_end = end_t

    if current_bytes:
        bursts.append((current_start, current_end, list(current_bytes)))

    return bursts


def timing_analysis(ch5_frames, ch2_frames):
    """
    Analyze timing between ch5 frames and ch2 frames.
    Since they are half-duplex, measure:
    - ch5 frame END -> next ch2 frame START (controller request -> motor response)
    - ch2 frame END -> next ch5 frame START (motor response -> next controller request)
    """
    ch5_events = [(f[0], f[1], "ch5", f) for f in ch5_frames]
    ch2_events = [(f[0], f[1], "ch2", f) for f in ch2_frames]

    all_events = sorted(ch5_events + ch2_events, key=lambda x: x[0])

    # ch5 end -> next ch2 start (request -> response)
    req_resp = []
    for idx, ev in enumerate(all_events):
        if ev[2] == "ch5":
            ch5_end = ev[1]
            for j in range(idx + 1, len(all_events)):
                if all_events[j][2] == "ch2":
                    ch2_start = all_events[j][0]
                    latency = ch2_start - ch5_end
                    if 0 <= latency < 1.0:
                        req_resp.append((ch5_end, ch2_start, latency, ev[3], all_events[j][3]))
                    break

    # ch2 end -> next ch5 start (response -> next request)
    resp_req = []
    for idx, ev in enumerate(all_events):
        if ev[2] == "ch2":
            ch2_end = ev[1]
            for j in range(idx + 1, len(all_events)):
                if all_events[j][2] == "ch5":
                    ch5_start = all_events[j][0]
                    latency = ch5_start - ch2_end
                    if 0 <= latency < 1.0:
                        resp_req.append((ch2_end, ch5_start, latency, ev[3], all_events[j][3]))
                    break

    return req_resp, resp_req


def hex_dump(blist, max_bytes=20):
    """Format a byte list as hex string, truncating if needed."""
    if len(blist) <= max_bytes:
        return " ".join(f"{b:02X}" for b in blist)
    return " ".join(f"{b:02X}" for b in blist[:max_bytes]) + "..."


def ascii_repr(blist):
    """Format a byte list as ASCII, with dots for non-printable."""
    return "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in blist)


def main():
    print("=" * 80)
    print("TREADMILL LOGIC ANALYZER CAPTURE ANALYSIS")
    print(f"File: {CSV_FILE}")
    print("=" * 80)

    # Load data
    print("\nLoading CSV data...")
    rows = load_csv(CSV_FILE)
    print(f"  Loaded {len(rows)} samples")
    print(f"  Time range: {rows[0][0]:.6f}s to {rows[-1][0]:.6f}s "
          f"(duration: {rows[-1][0] - rows[0][0]:.3f}s)")

    # ── Channel activity summary ──────────────────────────────────────────
    print("\n" + "-" * 80)
    print("CHANNEL ACTIVITY SUMMARY")
    print("-" * 80)
    active = find_active_channels(rows)
    for ch in range(8):
        status = "ACTIVE" if ch in active else "static"
        first_val = rows[0][1][ch]
        last_val = rows[-1][1][ch]
        edges = extract_edges(rows, ch)
        n_transitions = len(edges) - 1 if len(edges) > 1 else 0
        label = ""
        if ch == CH_PIN3:
            label = " <-- Pin 3 (Motor->Controller)"
        elif ch == CH_PIN6:
            label = " <-- Pin 6 (Controller->Motor)"
        print(f"  Ch {ch}: {status:6s}  {n_transitions:6d} transitions  "
              f"first={first_val} last={last_val}{label}")

    # Check half-duplex behavior
    print("\n  Half-duplex check:")
    both = 0
    only2 = 0
    only5 = 0
    prev_chs = rows[0][1]
    for i in range(1, len(rows)):
        chs = rows[i][1]
        c2 = chs[2] != prev_chs[2]
        c5 = chs[5] != prev_chs[5]
        if c2 and c5:
            both += 1
        elif c2:
            only2 += 1
        elif c5:
            only5 += 1
        prev_chs = chs
    print(f"    Only ch2 changes: {only2}")
    print(f"    Only ch5 changes: {only5}")
    print(f"    Both change simultaneously: {both} (quantization artifacts)")

    # ── Decode Channel 5 (Pin 6) ─────────────────────────────────────────
    print("\n" + "-" * 80)
    print("DECODING CHANNEL 5 (Pin 6: Controller -> Motor)")
    print("-" * 80)

    edges5 = extract_edges(rows, CH_PIN6)
    print(f"  {len(edges5)} edges on channel 5")

    decoded5 = decode_uart_sequential(edges5)
    good5 = sum(1 for _, _, _, s in decoded5 if s)
    bad5 = len(decoded5) - good5
    print(f"  Decoded {len(decoded5)} bytes ({good5} good stop, {bad5} bad stop)")

    # Group by idle gap first to see natural burst structure
    bursts5 = group_by_idle_gap(decoded5, gap_threshold_ms=3.0)
    print(f"  {len(bursts5)} bursts (>3ms idle gap)")

    # Group into R...E 45 01 frames
    frames5 = group_frames_45_01(decoded5)
    print(f"  {len(frames5)} frames (R...E with 45 01 end marker)")

    print(f"\n  Raw bursts (first 40):")
    for i, (start_t, end_t, blist) in enumerate(bursts5[:40]):
        dur = (end_t - start_t) * 1000
        print(f"    [{i:3d}] t={start_t:+.6f}s  dur={dur:5.1f}ms  "
              f"({len(blist):2d} bytes)  {hex_dump(blist, 16)}")
        print(f"           ascii: {ascii_repr(blist)}")

    print(f"\n  Frames with 45 01 end marker (showing all {len(frames5)}):")
    for i, (start_t, end_t, blist) in enumerate(frames5):
        dur = (end_t - start_t) * 1000
        type_byte = f"0x{blist[1]:02X}" if len(blist) > 1 else "?"
        # Show payload without R start and 45 01 end
        payload = blist[1:-2] if len(blist) > 3 else blist[1:]
        print(f"    [{i:3d}] t={start_t:+.6f}s  dur={dur:5.1f}ms  "
              f"({len(blist):2d} bytes)  type={type_byte}")
        print(f"           hex: {hex_dump(blist, 24)}")

    # ── Decode Channel 2 (Pin 3) ─────────────────────────────────────────
    print("\n" + "-" * 80)
    print("DECODING CHANNEL 2 (Pin 3: Motor -> Controller)")
    print("-" * 80)

    edges2 = extract_edges(rows, CH_PIN3)
    print(f"  {len(edges2)} edges on channel 2")

    decoded2 = decode_uart_sequential(edges2)
    good2 = sum(1 for _, _, _, s in decoded2 if s)
    bad2 = len(decoded2) - good2
    print(f"  Decoded {len(decoded2)} bytes ({good2} good stop, {bad2} bad stop)")

    bursts2 = group_by_idle_gap(decoded2, gap_threshold_ms=3.0)
    print(f"  {len(bursts2)} bursts (>3ms idle gap)")

    frames2 = group_frames_45_00(decoded2)
    print(f"  {len(frames2)} frames (R...E with 45 00 end marker)")

    print(f"\n  Raw bursts (first 40):")
    for i, (start_t, end_t, blist) in enumerate(bursts2[:40]):
        dur = (end_t - start_t) * 1000
        print(f"    [{i:3d}] t={start_t:+.6f}s  dur={dur:5.1f}ms  "
              f"({len(blist):2d} bytes)  {hex_dump(blist, 16)}")
        print(f"           ascii: {ascii_repr(blist)}")

    print(f"\n  Frames with 45 00 end marker (showing all {len(frames2)}):")
    for i, (start_t, end_t, blist) in enumerate(frames2):
        dur = (end_t - start_t) * 1000
        type_byte = f"0x{blist[1]:02X}" if len(blist) > 1 else "?"
        print(f"    [{i:3d}] t={start_t:+.6f}s  dur={dur:5.1f}ms  "
              f"({len(blist):2d} bytes)  type={type_byte}")
        print(f"           hex: {hex_dump(blist, 24)}")

    # ── Interleaved Timeline ─────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("INTERLEAVED TIMELINE (bursts, first 80)")
    print("-" * 80)

    timeline = []
    for b in bursts5:
        start_t, end_t, blist = b
        timeline.append((start_t, end_t, "PIN6_TX", blist))
    for b in bursts2:
        start_t, end_t, blist = b
        timeline.append((start_t, end_t, "PIN3_RX", blist))

    timeline.sort(key=lambda x: x[0])

    prev_end = None
    prev_ch = None
    for i, (start_t, end_t, ch, blist) in enumerate(timeline[:80]):
        gap_str = ""
        if prev_end is not None:
            gap = (start_t - prev_end) * 1000
            cross = " CROSS" if prev_ch != ch else ""
            gap_str = f"  gap={gap:+7.2f}ms{cross}"

        dur = (end_t - start_t) * 1000
        print(f"  [{i:3d}] {start_t:+.6f}s  {ch:8s}  {len(blist):2d}B  "
              f"dur={dur:5.1f}ms  {hex_dump(blist, 12)}{gap_str}")

        prev_end = end_t
        prev_ch = ch

    # ── Timing Analysis ──────────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("TIMING ANALYSIS: REQUEST-RESPONSE LATENCY")
    print("-" * 80)

    # Use bursts for timing (more granular than frames)
    req_resp_burst, resp_req_burst = timing_analysis(
        [(s, e, b) for s, e, b in bursts5],
        [(s, e, b) for s, e, b in bursts2]
    )

    print(f"\n  Pin 6 burst END -> Pin 3 burst START (controller -> motor response):")
    print(f"  Found {len(req_resp_burst)} pairs")
    if req_resp_burst:
        lats = [l * 1000 for _, _, l, _, _ in req_resp_burst]
        print(f"    Min:  {min(lats):8.3f} ms")
        print(f"    Max:  {max(lats):8.3f} ms")
        print(f"    Mean: {sum(lats)/len(lats):8.3f} ms")
        print(f"    Median: {sorted(lats)[len(lats)//2]:8.3f} ms")
        print(f"\n  Detail (first 30):")
        for i, (t5e, t2s, lat, f5, f2) in enumerate(req_resp_burst[:30]):
            print(f"    [{i:3d}] ch5_end={t5e:+.6f} -> ch2_start={t2s:+.6f}  "
                  f"lat={lat*1000:7.2f}ms")
            print(f"          req:  {hex_dump(f5[2], 10)}")
            print(f"          resp: {hex_dump(f2[2], 10)}")

    print(f"\n  Pin 3 burst END -> Pin 6 burst START (motor response -> next controller req):")
    print(f"  Found {len(resp_req_burst)} pairs")
    if resp_req_burst:
        lats2 = [l * 1000 for _, _, l, _, _ in resp_req_burst]
        print(f"    Min:  {min(lats2):8.3f} ms")
        print(f"    Max:  {max(lats2):8.3f} ms")
        print(f"    Mean: {sum(lats2)/len(lats2):8.3f} ms")
        print(f"    Median: {sorted(lats2)[len(lats2)//2]:8.3f} ms")
        print(f"\n  Detail (first 30):")
        for i, (t2e, t5s, lat, f2, f5) in enumerate(resp_req_burst[:30]):
            print(f"    [{i:3d}] ch2_end={t2e:+.6f} -> ch5_start={t5s:+.6f}  "
                  f"lat={lat*1000:7.2f}ms")
            print(f"          resp: {hex_dump(f2[2], 10)}")
            print(f"          next: {hex_dump(f5[2], 10)}")

    # ── Frame-level timing ───────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("FRAME-LEVEL TIMING ANALYSIS")
    print("-" * 80)

    # Use frame-level grouping for broader pattern analysis
    req_resp_frame, resp_req_frame = timing_analysis(
        [(s, e, b) for s, e, b in frames5],
        [(s, e, b) for s, e, b in frames2]
    )

    print(f"\n  Pin 6 frame END -> Pin 3 frame START:")
    print(f"  Found {len(req_resp_frame)} pairs")
    if req_resp_frame:
        lats = [l * 1000 for _, _, l, _, _ in req_resp_frame]
        print(f"    Min:  {min(lats):8.3f} ms")
        print(f"    Max:  {max(lats):8.3f} ms")
        print(f"    Mean: {sum(lats)/len(lats):8.3f} ms")
        for i, (t5e, t2s, lat, f5, f2) in enumerate(req_resp_frame[:15]):
            print(f"    [{i:3d}] lat={lat*1000:7.2f}ms  "
                  f"req({len(f5[2])}B): {hex_dump(f5[2], 8)}  "
                  f"resp({len(f2[2])}B): {hex_dump(f2[2], 8)}")

    print(f"\n  Pin 3 frame END -> Pin 6 frame START:")
    print(f"  Found {len(resp_req_frame)} pairs")
    if resp_req_frame:
        lats = [l * 1000 for _, _, l, _, _ in resp_req_frame]
        print(f"    Min:  {min(lats):8.3f} ms")
        print(f"    Max:  {max(lats):8.3f} ms")
        print(f"    Mean: {sum(lats)/len(lats):8.3f} ms")
        for i, (t2e, t5s, lat, f2, f5) in enumerate(resp_req_frame[:15]):
            print(f"    [{i:3d}] lat={lat*1000:7.2f}ms  "
                  f"resp({len(f2[2])}B): {hex_dump(f2[2], 8)}  "
                  f"next({len(f5[2])}B): {hex_dump(f5[2], 8)}")

    # ── Statistics ────────────────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("STATISTICS")
    print("-" * 80)

    for label, bursts, frames, ch_name in [
        ("Channel 5 (Pin 6, Controller->Motor)", bursts5, frames5, "ch5"),
        ("Channel 2 (Pin 3, Motor->Controller)", bursts2, frames2, "ch2"),
    ]:
        print(f"\n  {label}:")
        if bursts:
            total_time = bursts[-1][1] - bursts[0][0]
            print(f"    Bursts:         {len(bursts)}")
            print(f"    Frames (R..E):  {len(frames)}")
            print(f"    Time span:      {total_time:.3f}s")
            if total_time > 0:
                print(f"    Burst rate:     {len(bursts)/total_time:.1f}/s")
                print(f"    Frame rate:     {len(frames)/total_time:.1f}/s")

            sizes = [len(b[2]) for b in bursts]
            print(f"    Burst sizes:    min={min(sizes)} avg={sum(sizes)/len(sizes):.1f} max={max(sizes)}")

            if len(bursts) > 1:
                gaps = [(bursts[i+1][0] - bursts[i][1]) * 1000 for i in range(len(bursts)-1)]
                print(f"    Inter-burst gaps: min={min(gaps):.2f}ms avg={sum(gaps)/len(gaps):.2f}ms max={max(gaps):.2f}ms")

        if frames:
            sizes = [len(f[2]) for f in frames]
            print(f"    Frame sizes:    min={min(sizes)} avg={sum(sizes)/len(sizes):.1f} max={max(sizes)}")

            # Frame type distribution (second byte)
            type_counts = defaultdict(int)
            for _, _, blist in frames:
                if len(blist) > 1:
                    type_counts[blist[1]] += 1
            print(f"    Frame type distribution (byte after 0x52):")
            for tb, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"      0x{tb:02X}: {cnt:3d} frames")

    # ── Conversation pattern ─────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("CONVERSATION PATTERN (full request-response pairs)")
    print("-" * 80)

    # Match each ch5 burst with the subsequent ch2 burst
    ch5_list = [(s, e, b, "ch5") for s, e, b in bursts5]
    ch2_list = [(s, e, b, "ch2") for s, e, b in bursts2]
    all_bursts = sorted(ch5_list + ch2_list, key=lambda x: x[0])

    pairs = []
    i = 0
    while i < len(all_bursts) - 1:
        if all_bursts[i][3] == "ch5" and all_bursts[i+1][3] == "ch2":
            req = all_bursts[i]
            resp = all_bursts[i+1]
            latency = (resp[0] - req[1]) * 1000
            pairs.append((req, resp, latency))
            i += 2
        else:
            i += 1

    print(f"\n  Found {len(pairs)} request-response pairs (ch5 burst -> ch2 burst)")
    print(f"\n  All pairs:")
    for i, (req, resp, lat) in enumerate(pairs):
        req_hex = hex_dump(req[2], 12)
        resp_hex = hex_dump(resp[2], 12)
        req_dur = (req[1] - req[0]) * 1000
        resp_dur = (resp[1] - resp[0]) * 1000
        print(f"  [{i:3d}] REQ  t={req[0]:+.6f}s  {len(req[2]):2d}B  dur={req_dur:5.1f}ms  {req_hex}")
        print(f"        RESP t={resp[0]:+.6f}s  {len(resp[2]):2d}B  dur={resp_dur:5.1f}ms  {resp_hex}")
        print(f"        Latency: {lat:7.2f}ms")

    if pairs:
        lats = [p[2] for p in pairs]
        print(f"\n  Pair latency summary:")
        print(f"    Count:  {len(lats)}")
        print(f"    Min:    {min(lats):7.2f} ms")
        print(f"    Max:    {max(lats):7.2f} ms")
        print(f"    Mean:   {sum(lats)/len(lats):7.2f} ms")
        print(f"    Median: {sorted(lats)[len(lats)//2]:7.2f} ms")

    # ── Full cycle timing ────────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("FULL CYCLE TIMING")
    print("-" * 80)

    # Measure complete cycle: ch5 start -> ch5 start (next)
    ch5_starts = [b[0] for b in bursts5]
    if len(ch5_starts) > 1:
        cycles = [(ch5_starts[i+1] - ch5_starts[i]) * 1000 for i in range(len(ch5_starts)-1)]
        print(f"\n  Ch5 burst-to-burst cycle time:")
        print(f"    Count:  {len(cycles)}")
        print(f"    Min:    {min(cycles):7.2f} ms")
        print(f"    Max:    {max(cycles):7.2f} ms")
        print(f"    Mean:   {sum(cycles)/len(cycles):7.2f} ms")
        print(f"    => {1000.0 / (sum(cycles)/len(cycles)):.1f} Hz effective polling rate")

    ch2_starts = [b[0] for b in bursts2]
    if len(ch2_starts) > 1:
        cycles = [(ch2_starts[i+1] - ch2_starts[i]) * 1000 for i in range(len(ch2_starts)-1)]
        print(f"\n  Ch2 burst-to-burst cycle time:")
        print(f"    Count:  {len(cycles)}")
        print(f"    Min:    {min(cycles):7.2f} ms")
        print(f"    Max:    {max(cycles):7.2f} ms")
        print(f"    Mean:   {sum(cycles)/len(cycles):7.2f} ms")

    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
