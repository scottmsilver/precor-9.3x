# Gemini Live Send Recovery

## Problem

`GeminiLiveClient.sendAudio()` ignores the Boolean returned by
`WebSocket.send()`. A closed or closing socket can therefore reject microphone
audio while the UI continues listening, with no outbound diagnostic explaining
the silence.

## Design

Route audio writes through a small send-result handler. Successful audio sends
record only metadata on the first accepted chunk and every 100th accepted chunk
thereafter. The accepted-chunk counter resets for each connection. Intermediate
chunks, PCM, and base64 payloads are never logged.

When `WebSocket.send()` returns `false`, log one concise error, clean up the
unusable connection, and publish `ClientState.ERROR` exactly once. Send
rejection and OkHttp's asynchronous `onFailure` callback share one synchronized,
idempotent terminal-failure path scoped to that socket so a race cannot publish
duplicate state changes. The helper also verifies that the reporting socket is
still current, preventing a delayed callback from an old socket from tearing
down a newer connection. `onClosed` must not downgrade or duplicate an already
reported error.
The existing `VoiceViewModel` state-change callback will stop capture and
schedule its normal reconnect when the treadmill server remains connected.
Repeated audio callbacks after cleanup become no-ops.

This change does not reconnect on every voice activation, change the Gemini
wire format, or add retry buffering. Retrying captured audio could replay stale
commands and is outside this fix.

## Verification

Unit tests cover accepted and rejected send results, including a rejection
followed by a listener failure producing one error transition, `onClosed` after
an error preserving that error, and stale callbacks not affecting a replacement
socket. Logging tests cover the first/100th-chunk cadence, per-connection reset,
suppression of intermediate chunks, and absence of payload text. Existing
Kotlin tests must remain green. On the connected tablet, verify that activating
voice produces outbound chunk metadata and that ordinary speech still receives
a response.
