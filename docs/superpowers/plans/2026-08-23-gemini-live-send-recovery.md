# Gemini Live Send Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rejected Gemini Live audio sends visible and recover through the existing reconnect path.

**Architecture:** Keep the change inside `GeminiLiveClient`: each socket gets an atomic exactly-once terminal gate, and terminal handling verifies that socket is still current before touching client state. Accepted audio sends increment a per-connection counter and log only the first and every 100th chunk. A constructor-injected WebSocket factory provides a narrow deterministic test seam; no PCM payload is logged or retried.

**Tech Stack:** Kotlin, OkHttp WebSocket, JUnit 4, Android Gradle unit tests

---

### Task 1: Specify send-result behavior with tests

**Files:**
- Create: `kotlin/app/src/test/java/com/precor/treadmill/voice/GeminiLiveClientSendTest.kt`
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/voice/GeminiLiveClient.kt`

- [ ] **Step 1: Write failing tests**

Add a fake `WebSocket`, recording callbacks, an injected WebSocket factory, and
injected debug/error log sinks. Test that accepted sends remain connected and
log chunk numbers 1 and 100 only without payload text, and that the counter
restarts at 1 on a replacement connection. Test that a rejected send followed
by `onFailure` and `onClosed` publishes one `ERROR`, reports one error, and
ignores subsequent audio. Test that a delayed callback from an old socket does
not terminate its replacement.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd kotlin && ./gradlew testDebugUnitTest --tests '*GeminiLiveClientSendTest'`

Expected: compilation/test failure because the WebSocket/log injection seams and socket-scoped idempotent failure path do not exist.

- [ ] **Step 3: Implement the minimal client change**

Add a constructor-injected WebSocket factory and log sinks with production
defaults. Each `connect()` creates a socket-scoped `AtomicBoolean` terminal gate
captured by its listener and resets an `AtomicInteger` accepted-audio counter.
Route `sendAudio()`'s Boolean result through a helper: accepted sends log at
counts 1 and multiples of 100; rejected sends use the shared terminal helper.
Route `onFailure`, `onClosed`, and explicit `disconnect()` through that helper,
which must verify both the socket identity and its gate before cleaning up or
publishing state.

- [ ] **Step 4: Run focused and full unit tests**

Run:

```bash
cd kotlin
./gradlew testDebugUnitTest --tests '*GeminiLiveClientSendTest'
./gradlew testDebugUnitTest
```

Expected: PASS.

- [ ] **Step 5: Commit**

Commit the test and production files together with a focused bug-fix message.

### Task 2: Verify on the tablet

**Files:** none

- [ ] **Step 1: Build and install the debug APK**

Run `cd kotlin && ./gradlew assembleDebug`, install it on the connected SM-X115, and relaunch the app without changing its server configuration.

- [ ] **Step 2: Exercise voice and inspect logs**

Activate voice, speak a short command, and require both `GeminiLive` accepted
chunk metadata (without PCM/base64 data) and a successful ordinary Gemini audio
response. If practical, induce send rejection separately and verify its error
and reconnect; rejection alone does not satisfy the normal-path check.

- [ ] **Step 3: Close the bead, commit metadata, pull/rebase, and push**

Close `precor-9_3x-n9t`, verify the worktree, push Git and Dolt state, then confirm `main` is up to date with `origin/main`.
