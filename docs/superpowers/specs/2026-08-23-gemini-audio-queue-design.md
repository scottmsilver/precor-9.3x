# Gemini Audio Queue Design

## Problem

Gemini Live can deliver synthesized PCM faster than the tablet plays it. The
current 10-second queue reached its limit during a 16.7-second response and
silently dropped seven late audio chunks, producing an audible cutoff even
though Gemini completed the turn normally.

## Design

Keep the existing single-producer FIFO player and immediate barge-in flush, but
increase its nominal queue limit from 10 seconds to 120 seconds. At 24 kHz mono
PCM16, the configured payload limit is 5,760,000 bytes, which is modest on the
target tablet. The WebSocket reader continues to drain promptly so interruption
and control messages are not delayed.

An overflow beyond 120 seconds remains rejected and logged as a safety guard;
normal audio is never intentionally reordered or discarded. Prompt length and
output-token limits remain independent latency controls rather than data-loss
protection.

This change does not redesign the existing enqueue/flush concurrency protocol.
Its non-atomic byte accounting and post-interruption enqueue race predate the
observed overflow and are outside this narrowly approved queue-capacity fix.

## Verification

- Add a deterministic forced-burst regression test that exceeds the former
  480,000-byte limit, admits audio through 5,760,000 bytes, and rejects audio
  beyond the new limit.
- Run the Android unit suite and build the debug APK.
- Install on the SM-X115 tablet and obtain a long voice response; verify that
  playback completes with no `Queue overflow` log entries.
- During a response with queued audio, speak over the model and verify an
  `interrupted` event is followed promptly by `Playback flushed`, with no stale
  pre-interruption speech continuing afterward.
