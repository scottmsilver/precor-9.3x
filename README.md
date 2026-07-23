# Treddy

> A 2005 treadmill that listens when you talk to it.

A Raspberry Pi sits between the console and motor controller of a Precor 9.31 treadmill, intercepting the serial bus. It runs an AI coach (Gemini), voice control, a tablet UI, and a Bluetooth daemon that makes Zwift think it's a modern smart treadmill.

<img src="docs/screenshots/ridgeline-hud.jpg" width="820" alt="The Ridgeline HUD: the workout's route switchbacks up a real mountain photo, with floating glass instruments">

*The Ridgeline HUD — your workout drawn as a trail up a real mountain. The route's switchbacks tighten with the grade, the strip on the right shows what's coming and when, and every panel is adaptive glass floating on the photo.*

---

## Voice + AI Coach

<img src="docs/screenshots/ridgeline-trail-detail.jpg" width="700" alt="Detail: the interval route climbing the ridgeline, with grade/speed chips at each transition">

- "Set speed to 5" or "give me a 20-minute hill workout" — Gemini controls the belt directly
- Voice works mid-run via Gemini Live (real-time, no wake word)
- Says "this is the hardest interval" or "you're faster than last time" because it can query your run history

## Apps

<img src="docs/screenshots/android-lobby.png" width="700" alt="Lobby screen on Android tablet">

- **Android** (Kotlin + Compose): runs on a tablet mounted on the treadmill console
- Workout library, run history, live elevation profile, calorie tracking

## Bluetooth

- Rust daemon makes the treadmill show up in Zwift, Peloton, QZ Fitness, and Apple Watch as a standard FTMS device
- Fitness apps can read speed/incline and write speed/incline commands back

## Heart Rate

- Connects to any Bluetooth heart rate strap
- HR shows in the UI and feeds into calorie calculations

---

## Architecture

```
┌───────────────────────────────────────────────────────┐
│  Android (Kotlin + Compose)                           │
├───────────────────────────────────────────────────────┤
│  REST / WebSocket / Gemini Live (voice)               │
├───────────────────────────────────────────────────────┤
│  server.py (FastAPI)                                  │
│  Sessions, programs, AI chat, workout query DB        │
├──────────────────┬──────────────┬─────────────────────┤
│  treadmill_io    │  ftms-daemon │  hrm-daemon         │
│  C++20, GPIO     │  Rust, BLE   │  Rust, BLE          │
│  serial, safety  │  FTMS        │  heart rate         │
└──────────────────┴──────────────┴─────────────────────┘
                         │
                    Precor 9.31
                  RS-485 serial bus
```

- **C++ binary** — reads/writes the serial bus, enforces safety (3-hour timeout, physical buttons always override software)
- **Python server** — FastAPI. All the logic: Gemini AI, workouts, sessions, run history
- **FTMS daemon** — Rust. Bluetooth for fitness apps
- **HRM daemon** — Rust. Connects to heart rate straps
- **Android** — display layer. All decisions happen server-side.

Details: [CLAUDE.md](CLAUDE.md)

---

## The Hardware Story

> The serial protocol turned out to be plain ASCII text. We just had the polarity wrong and spent days decoding "binary" that was actually `[key:value]` pairs with every bit flipped.

[Full reverse engineering writeup →](HARDWARE.md)

**Custom hardware:** the breadboard tap was productized into a custom Pi
Zero 2 W hat — dual RJ45 (`From Console` / `To Motor`), a Pololu D24V10F5
buck so the treadmill's `+8V` rail powers the Pi, full 2×20 header. KiCad
sources, gerbers, and wiring guides are in
[`hardware/PiZeroHat/`](hardware/PiZeroHat/README.md) (adapted from the
[vasya-zh/PiZeroHat](https://github.com/vasya-zh/PiZeroHat) pogo-pin base).

---

## Quick Start

You need: a Raspberry Pi (any aarch64 model — Zero 2 W or 4), the treadmill
serial tap (the custom hat in [`hardware/PiZeroHat/`](hardware/PiZeroHat/README.md),
or a breadboard tap wired per [HARDWARE.md](HARDWARE.md)), Docker on your dev
machine (for the cross toolchain), and a Gemini API key.

**1. Build the OS image for your Pi.** The Pi runs a stock Debian-based Pi
OS; you flash a reproducible image built for your hardware. See
[`provisioning/`](provisioning/dietpi/README.md) for the image builder
(DietPi is the reference implementation — adapt the regulatory/board
settings for yours). Flash it, boot, confirm SSH.

**2. Deploy the software.** Compiled code is **cross-built off-Pi** in one
Docker toolchain — nothing is compiled on the Pi. The OS runtime
prerequisites (`python3`, `libpigpio1`) are **installed automatically** by
the deploy; you do not apt-install anything by hand.

```bash
make deploy        # cross-build → rsync → restart all 4 services
make deploy-key    # one-time per device: push your ./.gemini_key (a secret;
                   #   never rsync'd by a normal deploy)
make image         # OR: bake a flashable .img with everything pre-installed
```

**Local dev (no Pi needed):**
```bash
TREADMILL_MOCK=1 ./scripts/dev.sh    # server.py in mock mode
```

API reference, deploy details, tests: [CLAUDE.md](CLAUDE.md)

## License

MIT
