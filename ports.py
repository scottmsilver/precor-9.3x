#!/usr/bin/env python3
"""
Port configuration helper.

Resolves logical port names (console_rx, motor_tx, motor_rx) to /dev/ttyUSBx paths.
Config lives in ports.json next to this file.

Usage:
  from ports import get_port, list_ports

  dev = get_port("console_rx")   # returns "/dev/ttyUSB0"
  dev = get_port("/dev/ttyUSB3") # passthrough if already a device path
"""

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ports.json')
_cache = None


def _load():
    global _cache
    if _cache is None:
        with open(_CONFIG_PATH) as f:
            _cache = json.load(f)
    return _cache


def get_port(name):
    """Resolve a logical name or device path to a device path."""
    if name.startswith('/dev/'):
        return name
    cfg = _load()
    if name not in cfg:
        raise KeyError(f"Unknown port '{name}'. Known: {', '.join(cfg.keys())}")
    return cfg[name]['device']


def list_ports():
    """Return dict of {name: {device, description, ...}}."""
    return _load()


def port_arg(value):
    """argparse type function - accepts logical name or device path."""
    if value.startswith('/dev/'):
        return value
    cfg = _load()
    if value in cfg:
        return cfg[value]['device']
    raise argparse.ArgumentTypeError(
        f"Unknown port '{value}'. Use a device path or one of: {', '.join(cfg.keys())}"
    )
