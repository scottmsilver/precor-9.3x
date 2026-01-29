#!/usr/bin/env python3
"""
PRECOR Console Emulator - Replaces the console to control the motor directly.

WHAT WE KNOW:
  - SET_SPD (0x2A): 52 2A 1F 2F 8B [base16 speed] 45 01
  - SET_INC (0x4B): 52 4B CA 5A [base16 incline] 45 01
  - Speed: base-16 encoded, value = mph * 100
  - Incline: base-16 encoded, value = percent * 2

WHAT WE DON'T KNOW (replayed as-is from capture):
  - UNK_52, UNK_54, UNK_9A, UNK_A2, UNK_D4

Usage:
  python3 emulate.py                    # Start with speed=0, incline=0
  python3 emulate.py --speed 3.5        # Start at 3.5 mph
  python3 emulate.py --incline 2.0      # Start at 2.0% incline
  python3 emulate.py --playback file.jsonl  # Replay capture file
  python3 emulate.py --playback file.jsonl --loop  # Loop playback

Runtime controls:
  +/=  : Increase speed 0.5 mph
  -    : Decrease speed 0.5 mph
  ]    : Increase incline 0.5%
  [    : Decrease incline 0.5%
  0    : Emergency stop (speed=0)
  q    : Quit
"""

import json
import select
import sys
import termios
import time
import tty
from datetime import datetime

import serial

from protocol import SERIAL_PORT, BAUD_RATE, NAMES, build_set_spd, build_set_inc, hex_str

# Unknown packets - replayed exactly as captured
UNK_52_LONG = bytes.fromhex('52520a1f8b9595959f4501')
UNK_52_SHORT = bytes.fromhex('525269174501')
UNK_54 = bytes.fromhex('52541b4501')
UNK_9A_A = bytes.fromhex('529a17194501')
UNK_9A_B = bytes.fromhex('529a17314501')
UNK_A2 = bytes.fromhex('52a215194501')
UNK_D4 = bytes.fromhex('52d41b178b934501')

# Display packets
DISP1 = bytes.fromhex('524f49945405524da3540552aa3a174501')
DISP2 = bytes.fromhex('5251e8542a055253a98a5a9f4501')


class ConsoleEmulator:
    def __init__(self, speed=0.0, incline=0.0):
        self.speed = speed
        self.incline = incline
        self.running = True
        self.ser = None

    def send(self, pkt):
        """Send a packet."""
        self.ser.write(pkt)
        self.ser.flush()
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def run_cycle(self):
        """Run one heartbeat cycle (~310ms)."""
        # Phase 1: Command sequence
        self.send(DISP2)
        time.sleep(0.020)

        self.send(UNK_52_LONG)
        time.sleep(0.020)

        self.send(build_set_inc(self.incline))
        time.sleep(0.020)

        self.send(build_set_spd(self.speed))
        time.sleep(0.020)

        # Phase 2: Status sequence
        time.sleep(0.100)

        self.send(DISP1)
        self.send(UNK_A2)
        time.sleep(0.020)

        self.send(UNK_52_SHORT)
        time.sleep(0.020)

        self.send(UNK_9A_A)
        self.send(UNK_9A_B)
        time.sleep(0.020)

        self.send(UNK_D4)
        time.sleep(0.020)

        self.send(UNK_54)
        time.sleep(0.020)

    def check_keyboard(self):
        """Check for keyboard input (non-blocking)."""
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch in 'qQ':
                self.running = False
            elif ch in '+=':
                self.speed = min(12.0, self.speed + 0.5)
                print(f"\r  Speed: {self.speed:.1f} mph, Incline: {self.incline:.1f}%    ", end='', flush=True)
            elif ch == '-':
                self.speed = max(0.0, self.speed - 0.5)
                print(f"\r  Speed: {self.speed:.1f} mph, Incline: {self.incline:.1f}%    ", end='', flush=True)
            elif ch == ']':
                self.incline = min(15.0, self.incline + 0.5)
                print(f"\r  Speed: {self.speed:.1f} mph, Incline: {self.incline:.1f}%    ", end='', flush=True)
            elif ch == '[':
                self.incline = max(0.0, self.incline - 0.5)
                print(f"\r  Speed: {self.speed:.1f} mph, Incline: {self.incline:.1f}%    ", end='', flush=True)
            elif ch == '0':
                self.speed = 0.0
                print(f"\r  STOP! Speed: {self.speed:.1f} mph                    ", end='', flush=True)

    def run(self):
        """Main emulator loop."""
        print("Opening serial port...")
        self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        time.sleep(0.1)

        print(f"Starting: Speed={self.speed:.1f} mph, Incline={self.incline:.1f}%")
        print()
        print("Packets we KNOW:")
        print(f"  SET_SPD: {hex_str(build_set_spd(self.speed))}")
        print(f"  SET_INC: {hex_str(build_set_inc(self.incline))}")
        print()
        print("Packets we DON'T KNOW (replaying as-is):")
        print(f"  UNK_52_LONG:  {hex_str(UNK_52_LONG)}")
        print(f"  UNK_52_SHORT: {hex_str(UNK_52_SHORT)}")
        print(f"  UNK_54/9A/A2/D4: (static)")
        print()
        print("Controls: +/-=speed  [/]=incline  0=stop  q=quit")
        print()

        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())

            cycle = 0
            while self.running:
                self.run_cycle()
                cycle += 1

                if cycle % 10 == 0:
                    print(f"\r  Cycle {cycle}: Speed={self.speed:.1f} mph, Incline={self.incline:.1f}%    ", end='', flush=True)

                self.check_keyboard()

        except KeyboardInterrupt:
            print("\n\nInterrupted!")
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            print("\nSending stop...")
            self.speed = 0.0
            for _ in range(5):
                self.run_cycle()

            self.ser.close()
            print("Done.")


class CapturePlayback:
    """Replay packets from a JSONL capture file."""

    def __init__(self, filename, loop=False):
        self.filename = filename
        self.loop = loop
        self.running = True
        self.ser = None
        self.packets = []

    def load_capture(self):
        """Load packets from capture file."""
        print(f"Loading {self.filename}...")
        with open(self.filename) as f:
            for line in f:
                record = json.loads(line)
                if record['type'] == 'packet':
                    ts = datetime.fromisoformat(record['timestamp'])
                    raw = bytes.fromhex(record['raw_frame'])
                    name = record['frame_name']
                    self.packets.append((ts, name, raw))
        print(f"Loaded {len(self.packets)} packets")

    def send(self, pkt):
        """Send a packet."""
        self.ser.write(pkt)
        self.ser.flush()
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def check_keyboard(self):
        """Check for keyboard input (non-blocking)."""
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch in 'qQ':
                self.running = False
                return True
        return False

    def run(self):
        """Main playback loop."""
        self.load_capture()

        if not self.packets:
            print("No packets to replay!")
            return

        print("Opening serial port...")
        self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        time.sleep(0.1)

        print(f"Replaying {len(self.packets)} packets...")
        print("Press q to quit")
        print()

        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())

            loop_count = 0
            while self.running:
                loop_count += 1
                if self.loop:
                    print(f"\n=== Loop {loop_count} ===")

                # Get first packet time as reference
                base_time = self.packets[0][0]
                start_real = time.time()

                for i, (ts, name, raw) in enumerate(self.packets):
                    if not self.running:
                        break

                    # Calculate delay from start
                    offset = (ts - base_time).total_seconds()
                    target_time = start_real + offset
                    now = time.time()

                    if target_time > now:
                        # Wait, but check keyboard periodically
                        while time.time() < target_time and self.running:
                            if self.check_keyboard():
                                break
                            time.sleep(0.001)

                    if not self.running:
                        break

                    # Send packet
                    self.send(raw)
                    print(f"\r  [{i+1}/{len(self.packets)}] {name:8} {hex_str(raw)[:50]}...", end='', flush=True)

                if not self.loop:
                    break

        except KeyboardInterrupt:
            print("\n\nInterrupted!")
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            self.ser.close()
            print("\nDone.")


def main():
    speed = 0.0
    incline = 0.0
    playback_file = None
    loop = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--speed' and i + 1 < len(args):
            speed = float(args[i + 1])
            i += 2
        elif args[i] == '--incline' and i + 1 < len(args):
            incline = float(args[i + 1])
            i += 2
        elif args[i] == '--playback' and i + 1 < len(args):
            playback_file = args[i + 1]
            i += 2
        elif args[i] == '--loop':
            loop = True
            i += 1
        else:
            i += 1

    print(__doc__)

    if playback_file:
        player = CapturePlayback(playback_file, loop=loop)
        player.run()
    else:
        emu = ConsoleEmulator(speed=speed, incline=incline)
        emu.run()


if __name__ == "__main__":
    main()
