#!/usr/bin/env python3
"""
PRECOR Console Emulator - Replaces the console to control the motor directly.

WHAT WE KNOW:
  - SET_SPD (0x2A): 52 2A 1F 2F 8B [base16 speed] 45 01
  - SET_INC (0x4B): 52 4B CA 5A [base16 incline] 45 01
  - DISP1/DISP2: Display data
  - Speed: base-16 encoded, value = mph * 100
  - Incline: base-16 encoded, value = percent * 2

WHAT WE DON'T KNOW (replayed as-is from capture):
  - UNK_52: Unknown packet type 0x52
  - UNK_54, UNK_9A, UNK_A2, UNK_D4: Unknown status/timing packets

Usage:
  python3 emulate.py                    # Start with speed=0, incline=0
  python3 emulate.py --speed 3.5        # Start at 3.5 mph
  python3 emulate.py --incline 2.0      # Start at 2.0% incline

Runtime controls:
  +/=  : Increase speed 0.5 mph
  -    : Decrease speed 0.5 mph
  ]    : Increase incline 0.5%
  [    : Decrease incline 0.5%
  0    : Emergency stop (speed=0)
  q    : Quit
"""

import serial
import time
import sys
import select
import tty
import termios

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

# Custom base-16 digit set for speed/incline encoding (NO nibble swapping!)
DIGITS = [0x9F, 0x9D, 0x9B, 0x99, 0x97, 0x95, 0x93, 0x91,
          0x8F, 0x8D, 0x7D, 0x7B, 0x79, 0x77, 0x75, 0x73]

def encode_base16(value):
    """Encode integer to custom base-16 bytes. NO nibble swapping."""
    if value == 0:
        return bytes([DIGITS[0]])
    elif value < 16:
        return bytes([DIGITS[value]])
    elif value < 256:
        return bytes([DIGITS[value // 16], DIGITS[value % 16]])
    else:
        return bytes([DIGITS[value // 256],
                      DIGITS[(value // 16) % 16],
                      DIGITS[value % 16]])

def hex_str(data):
    return ' '.join(f'{b:02X}' for b in data)

# ============================================================
# PACKETS WE UNDERSTAND
# ============================================================

def build_set_spd(speed_mph):
    """
    Build SET_SPD packet (0x2A).
    Format: 52 2A 1F 2F 8B [base16 speed] 45 01
    Speed is encoded as hundredths of mph (3.5 mph = 350)
    """
    hundredths = int(speed_mph * 100)
    speed_bytes = encode_base16(hundredths)
    return bytes([0x52, 0x2A, 0x1F, 0x2F, 0x8B]) + speed_bytes + bytes([0x45, 0x01])

def build_set_inc(incline_pct):
    """
    Build SET_INC packet (0x4B).
    Format: 52 4B CA 5A [base16 incline] 45 01
    Incline is encoded as half-percent (2.0% = 4)
    """
    half_pct = int(incline_pct * 2)
    incline_bytes = encode_base16(half_pct)
    return bytes([0x52, 0x4B, 0xCA, 0x5A]) + incline_bytes + bytes([0x45, 0x01])

# ============================================================
# PACKETS WE DON'T UNDERSTAND (static replay from capture)
# ============================================================

# These are replayed exactly as captured - we don't know what they do
UNK_52_LONG = bytes.fromhex('52520a1f8b9595959f4501')   # 7-byte payload variant
UNK_52_SHORT = bytes.fromhex('525269174501')            # 2-byte payload variant
UNK_54 = bytes.fromhex('52541b4501')
UNK_9A_A = bytes.fromhex('529a17194501')
UNK_9A_B = bytes.fromhex('529a17314501')
UNK_A2 = bytes.fromhex('52a215194501')
UNK_D4 = bytes.fromhex('52d41b178b934501')

# Display packets (we know these are display data, content doesn't matter for motor)
DISP1 = bytes.fromhex('524f49945405524da3540552aa3a174501')
DISP2 = bytes.fromhex('5251e8542a055253a98a5a9f4501')

# ============================================================
# MAIN EMULATOR
# ============================================================

class ConsoleEmulator:
    def __init__(self, speed=0.0, incline=0.0):
        self.speed = speed      # mph
        self.incline = incline  # percent
        self.running = True
        self.ser = None

    def send(self, pkt, label=""):
        """Send a packet."""
        self.ser.write(pkt)
        self.ser.flush()
        # Drain any incoming data
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def run_cycle(self):
        """
        Run one heartbeat cycle based on captured timing (~310ms total).

        From capture analysis:
          DISP2       (0ms)
          UNK_52_LONG (20ms)   <- unknown, replaying as-is
          SET_INC     (20ms)   <- WE CONTROL THIS
          SET_SPD     (20ms)   <- WE CONTROL THIS (response? or command?)
          [gap ~100ms]
          DISP1       (140ms)
          UNK_A2      (0ms)
          UNK_52_SHORT(20ms)   <- unknown ping?
          UNK_9A_A    (20ms)
          UNK_9A_B    (0ms)
          UNK_D4      (20ms)
          UNK_54      (20ms)
        """

        # Phase 1: Command sequence
        self.send(DISP2, "DISP2")
        time.sleep(0.020)

        self.send(UNK_52_LONG, "UNK_52_LONG")
        time.sleep(0.020)

        # SET_INC - incline command (WE CONTROL)
        set_inc = build_set_inc(self.incline)
        self.send(set_inc, "SET_INC")
        time.sleep(0.020)

        # SET_SPD - speed command (WE CONTROL)
        set_spd = build_set_spd(self.speed)
        self.send(set_spd, "SET_SPD")
        time.sleep(0.020)

        # Phase 2: Status sequence (after gap)
        time.sleep(0.100)

        self.send(DISP1, "DISP1")
        self.send(UNK_A2, "UNK_A2")
        time.sleep(0.020)

        self.send(UNK_52_SHORT, "UNK_52_SHORT")
        time.sleep(0.020)

        self.send(UNK_9A_A, "UNK_9A_A")
        self.send(UNK_9A_B, "UNK_9A_B")
        time.sleep(0.020)

        self.send(UNK_D4, "UNK_D4")
        time.sleep(0.020)

        self.send(UNK_54, "UNK_54")
        time.sleep(0.020)

    def check_keyboard(self):
        """Check for keyboard input (non-blocking)."""
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == 'q' or ch == 'Q':
                self.running = False
            elif ch == '+' or ch == '=':
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

        print(f"Starting emulator: Speed={self.speed:.1f} mph, Incline={self.incline:.1f}%")
        print()
        print("Packets we KNOW and control:")
        print(f"  SET_SPD: {hex_str(build_set_spd(self.speed))}")
        print(f"  SET_INC: {hex_str(build_set_inc(self.incline))}")
        print()
        print("Packets we DON'T KNOW (replaying as-is):")
        print(f"  UNK_52_LONG:  {hex_str(UNK_52_LONG)}")
        print(f"  UNK_52_SHORT: {hex_str(UNK_52_SHORT)}")
        print(f"  UNK_54:       {hex_str(UNK_54)}")
        print(f"  UNK_9A_A:     {hex_str(UNK_9A_A)}")
        print(f"  UNK_9A_B:     {hex_str(UNK_9A_B)}")
        print(f"  UNK_A2:       {hex_str(UNK_A2)}")
        print(f"  UNK_D4:       {hex_str(UNK_D4)}")
        print()
        print("Controls: +/-=speed  [/]=incline  0=stop  q=quit")
        print()

        # Set terminal to raw mode for keyboard input
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

            # Send stop command
            print("\nSending stop command...")
            self.speed = 0.0
            for _ in range(5):
                self.run_cycle()

            self.ser.close()
            print("Done.")

def main():
    speed = 0.0
    incline = 0.0

    # Parse arguments
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--speed' and i+1 < len(args):
            speed = float(args[i+1])
            i += 2
        elif args[i] == '--incline' and i+1 < len(args):
            incline = float(args[i+1])
            i += 2
        else:
            i += 1

    print(__doc__)

    emu = ConsoleEmulator(speed=speed, incline=incline)
    emu.run()

if __name__ == "__main__":
    main()
