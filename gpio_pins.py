#!/usr/bin/env python3
"""
GPIO pin configuration and shared I/O helpers for treadmill serial protocol.

Resolves logical pin names from gpio.json and provides common functions
for reading/writing inverted RS-485 serial via pigpio.

Usage:
  from gpio_pins import get_gpio, parse_kv_stream, gpio_write_bytes, build_kv_cmd

  pin = get_gpio("console_read")   # returns 27
"""

import json
import os
import time
import threading

import pigpio

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gpio.json')
_cache = None

BAUD = 9600


# --- Config ---

def _load():
    global _cache
    if _cache is None:
        with open(_CONFIG_PATH) as f:
            _cache = json.load(f)
    return _cache


def get_gpio(name):
    """Resolve a logical name to a BCM GPIO number."""
    cfg = _load()
    if name not in cfg:
        raise KeyError(f"Unknown pin '{name}'. Known: {', '.join(cfg.keys())}")
    return cfg[name]['gpio']


def list_pins():
    """Return dict of {name: {gpio, physical_pin, description, direction}}."""
    return _load()


# --- KV Emulation Cycle ---

# 14-key cycle the real controller sends, repeating.
# Each entry is (key, value_fn_or_None). value_fn takes a state dict.
KV_CYCLE = [
    ('inc',  lambda s: str(s['emu_incline'])),
    ('hmph', lambda s: format(s['emu_speed_raw'], 'X')),
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
    [0, 1],           # inc, hmph
    [2, 3, 4],        # amps, err, belt
    [5, 6, 7, 8],     # vbus, lift, lfts, lftg
    [9, 10, 11],      # part, ver, type
    [12, 13],          # diag, loop
]


# --- KV Protocol ---

def build_kv_cmd(key, value=None):
    """Build a KV command: [key:value]\\xff or [key]\\xff."""
    if value is not None:
        return f'[{key}:{value}]'.encode() + b'\xff'
    return f'[{key}]'.encode() + b'\xff'


def parse_kv_stream(buf):
    """Parse [key:value] pairs from byte buffer.
    Handles both \\xff-delimited (console) and bare (motor) formats.
    Rejects non-printable content, returns it tagged as 'BIN'.
    Returns (list of (key, value) tuples, remaining buffer)."""
    pairs = []
    i = 0
    while i < len(buf):
        if buf[i] in (0xFF, 0x00):
            i += 1
            continue
        if buf[i] == ord('['):
            end_idx = buf.find(b']', i)
            if end_idx == -1:
                break
            raw = buf[i + 1:end_idx]
            if all(0x20 <= b <= 0x7E for b in raw):
                content = raw.decode('ascii')
                if ':' in content:
                    k, v = content.split(':', 1)
                    pairs.append((k, v))
                elif content:
                    pairs.append((content, ''))
            elif raw:
                hex_str = ' '.join(f'{b:02X}' for b in raw)
                pairs.append(('BIN', hex_str))
            i = end_idx + 1
        else:
            i += 1
    return pairs, bytearray(buf[i:])


# --- GPIO Serial I/O ---

def gpio_read_open(pi, gpio, baud=BAUD):
    """Open a GPIO pin for inverted RS-485 serial reading."""
    pi.bb_serial_read_open(gpio, baud, 8)
    pi.bb_serial_invert(gpio, 1)


def gpio_read_close(pi, gpio):
    """Close a GPIO pin used for serial reading."""
    try:
        pi.bb_serial_read_close(gpio)
    except Exception:
        pass


def gpio_write_open(pi, gpio):
    """Configure a GPIO pin for inverted RS-485 serial writing (idle LOW)."""
    pi.set_mode(gpio, pigpio.OUTPUT)
    pi.write(gpio, 0)


def gpio_write_close(pi, gpio):
    """Reset a GPIO write pin to input."""
    pi.write(gpio, 0)
    pi.set_mode(gpio, pigpio.INPUT)


def gpio_write_bytes(pi, gpio, data, write_lock, baud=BAUD):
    """Write bytes as inverted serial (RS-485 polarity) on GPIO pin.
    Idle=LOW, start bit=HIGH, data bits inverted, stop bit=LOW."""
    if not data:
        return
    bit_us = int(1_000_000 / baud)
    mask = 1 << gpio

    with write_lock:
        while pi.wave_tx_busy():
            time.sleep(0.001)

        pulses = []
        for byte_val in data:
            # Start bit: HIGH (inverted from standard LOW)
            pulses.append(pigpio.pulse(mask, 0, bit_us))
            # 8 data bits, LSB first, INVERTED
            for bit in range(8):
                if (byte_val >> bit) & 1:
                    pulses.append(pigpio.pulse(0, mask, bit_us))  # 1 → LOW
                else:
                    pulses.append(pigpio.pulse(mask, 0, bit_us))  # 0 → HIGH
            # Stop bit: LOW (inverted idle)
            pulses.append(pigpio.pulse(0, mask, bit_us))

        pi.wave_clear()
        pi.wave_add_generic(pulses)
        wid = pi.wave_create()
        pi.wave_send_once(wid)

        while pi.wave_tx_busy():
            time.sleep(0.001)

        pi.wave_delete(wid)
