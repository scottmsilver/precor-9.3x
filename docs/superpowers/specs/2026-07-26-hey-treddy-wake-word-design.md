# "Hey Treddy" Wake Word — Design

**Date:** 2026-07-26
**Status:** Approved (brainstorm), pending implementation plan
**Scope:** Android tablet app (`kotlin/`) + one small server config addition

## Goal

Hands-free voice activation: say **"hey Treddy"** and the existing Gemini Live
voice pipeline activates — including same-breath commands ("hey Treddy, set
speed to three"). Detection is fully on-device; nothing streams off the tablet
until the wake word is heard.

## Decisions

| Question | Decision |
|---|---|
| Wake behavior | Wake + same-breath command (pre-roll forwarding) |
| Engine | Picovoice Porcupine, custom `hey_treddy.ppn` keyword |
| Session end | Auto-sleep after ~8 s of silence following Gemini's reply |
| Dedicated voice e-stop keyword | Out of scope (revisit later) |
| Background operation | Foreground-only; the tablet is a dedicated console, no service needed |

## Architecture

The mic becomes **always-running** while: wake word enabled in Settings, and
`RECORD_AUDIO` granted, and app foregrounded. `AudioCapture` is unchanged in
role; its chunk stream is **teed**:

1. **Always** → `WakeWordDetector` (new, `kotlin/.../voice/WakeWordDetector.kt`)
2. **When a voice session is active** → Gemini Live (exactly as today)

### WakeWordDetector

- Wraps the **raw `Porcupine` API** — not `PorcupineManager`, which owns its
  own mic and would conflict with our single `AudioRecord`.
- Re-buffers `AudioCapture`'s 2048-sample chunks into Porcupine's 512-sample
  frames.
- Maintains a **~1.5 s PCM ring buffer**.
- On detection, invokes `onWake(preRoll)` with ~300 ms of pre-roll audio so the
  first phoneme after the keyword isn't clipped.
- Detection is **paused** while Gemini is SPEAKING (self-trigger protection on
  top of AEC) and while a session is already active.

### VoiceViewModel wiring

- New `onWakeWord(preRoll)` entry point: behaves like `toggle()`-on
  (`userActivated = true`, state → LISTENING), sends the pre-roll PCM, then
  forwards live mic audio. Gemini Live's VAD handles utterance segmentation —
  no new endpointing logic on our side.
- **Auto-sleep:** after `onSpeakingEnd`, start an 8 s timer. If
  `AudioCapture.isSpeaking` never goes true in the window, stop forwarding and
  return to wake-only IDLE. Any detected speech cancels the timer. The same
  timeout applies if the user wakes it and says nothing.
- Tap-to-talk (`toggle()`) is unchanged and remains the fallback when wake word
  is unavailable. Tap-activated sessions keep today's stay-open-until-tapped
  behavior; only wake-activated sessions auto-sleep.

## Keyword model + AccessKey distribution

- Train **"Hey Treddy"** once on the Picovoice Console → `hey_treddy.ppn`,
  shipped in app assets. Bundled and offline, consistent with the app's
  no-external-CDN principle.
- The Picovoice **AccessKey is a secret**, handled like the Gemini key: a
  gitignored `.picovoice_key` on the Pi, served through `/api/config` as a new
  **nullable** `picovoiceKey` field. Postel-compliant: old servers omit the
  field → wake word simply stays off. Never hardcoded in the app or repo.

## UI / Settings

- Settings toggle **"Hey Treddy"** (on/off, persisted). Sensitivity slider is
  deferred unless false-trigger tuning proves necessary (Porcupine default 0.5).
- `VoiceOverlay` already visualizes LISTENING/SPEAKING; wake drives the same
  states. A short chime on wake confirms detection without looking.

## Failure modes

Missing key, missing model, Porcupine init failure, or missing mic permission →
wake word silently unavailable: logged, shown as disabled in Settings, and
tap-to-talk continues to work unchanged. There are no mic-ownership conflicts —
one `AudioRecord`, owned by `AudioCapture`.

## Testing

- **Unit:** frame re-buffering and ring-buffer/pre-roll logic (pure Kotlin, no
  Android deps).
- **End-to-end (emulator):** `~/scripts/start-emulator-audio.sh` virtual mic
  plays TTS clips of "hey Treddy, …" through the real pipeline; assert
  detection + command execution. Include negative clips (similar-sounding
  phrases) as a false-accept sanity check.
- **Real device:** verify on the tablet mounted on the treadmill with the motor
  running — belt noise is the environment that matters.

## Risks

- Porcupine free tier is personal-use, 3 active users/month — fine for a home
  device, but it is a commercial dependency with an account.
- "Treddy" is an invented word; console-trained models handle this well, but
  sensitivity may need one tuning pass against belt noise.
