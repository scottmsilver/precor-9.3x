# Gemini Live Send Recovery

## Problem

`GeminiLiveClient.sendAudio()` ignores the Boolean returned by
`WebSocket.send()`. A closed or closing socket can therefore reject microphone
audio while the UI continues listening, with no outbound diagnostic explaining
the silence.

## Design

Route audio writes through a small send-result handler. Successful audio sends
record only metadata: the first accepted chunk and a rate-limited cumulative
chunk count. PCM and base64 payloads are never logged.

When `WebSocket.send()` returns `false`, log one concise error, clean up the
unusable connection, and publish `ClientState.ERROR` exactly once. The existing
`VoiceViewModel` error callback will stop capture and schedule its normal
reconnect when the treadmill server remains connected. Repeated audio callbacks
after cleanup become no-ops.

This change does not reconnect on every voice activation, change the Gemini
wire format, or add retry buffering. Retrying captured audio could replay stale
commands and is outside this fix.

## Verification

Unit tests cover accepted and rejected send results, including the single error
transition. Existing Kotlin tests must remain green. On the connected tablet,
verify that activating voice produces outbound chunk metadata and that ordinary
speech still receives a response.
