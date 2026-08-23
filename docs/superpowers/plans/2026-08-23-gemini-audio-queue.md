# Gemini Audio Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve complete Gemini Live voice responses by expanding the tablet's bounded PCM playback queue from 10 seconds to 120 seconds.

**Architecture:** Keep the existing single-producer FIFO `AudioPlayer` and its barge-in flush behavior. Express the byte limit as a sample-rate-derived 120-second policy, exercise that policy with a deterministic forced burst, and change no WebSocket or playback-thread behavior.

**Tech Stack:** Kotlin, Android `AudioTrack`, JUnit 4, Gradle, adb

---

### Task 1: Add a deterministic queue-capacity regression

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/voice/AudioPlayer.kt`
- Create: `kotlin/app/src/test/java/com/precor/treadmill/voice/AudioPlayerQueuePolicyTest.kt`

- [ ] **Step 1: Write the failing forced-burst test**

Add pure, internal queue-policy access points to the expected test API, then write:

```kotlin
package com.precor.treadmill.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioPlayerQueuePolicyTest {
    @Test
    fun admitsForcedBurstBeyondOldLimitThrough120Seconds() {
        val chunkBytes = 9_600 // 200 ms at 24 kHz PCM16
        val limit = AudioPlayer.maxQueueBytes(sampleRate = 24_000)
        var queued = 0

        repeat(120 * 5) {
            assertTrue(AudioPlayer.canAdmit(queued, chunkBytes, limit))
            queued += chunkBytes
        }

        assertEquals(5_760_000, queued)
        assertFalse(AudioPlayer.canAdmit(queued, chunkBytes, limit))
        assertFalse(AudioPlayer.canAdmit(Int.MAX_VALUE, chunkBytes, limit))
        assertTrue(queued > 480_000)
    }
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd kotlin
./gradlew :app:testDebugUnitTest --tests com.precor.treadmill.voice.AudioPlayerQueuePolicyTest
```

Expected: compilation fails because `maxQueueBytes` and `canAdmit` do not exist.

- [ ] **Step 3: Implement the minimal 120-second policy**

In `AudioPlayer`'s companion object, replace the fixed byte constant with:

```kotlin
internal const val MAX_QUEUE_SECONDS = 120
internal fun maxQueueBytes(sampleRate: Int): Int = sampleRate * 2 * MAX_QUEUE_SECONDS
internal fun canAdmit(queuedBytes: Int, incomingBytes: Int, limitBytes: Int): Boolean =
    queuedBytes.toLong() + incomingBytes.toLong() <= limitBytes.toLong()
```

Give each `AudioPlayer` instance a derived limit:

```kotlin
private val maxQueueBytes = maxQueueBytes(sampleRate)
```

Use the shared policy in `enqueue`:

```kotlin
if (!canAdmit(queuedBytes.get(), bytes.size, maxQueueBytes)) {
    Log.w(TAG, "Queue overflow (${queuedBytes.get()}B), dropping chunk")
    return
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the focused Gradle command from Step 2.

Expected: `BUILD SUCCESSFUL`, one test passing.

- [ ] **Step 5: Commit the tested capacity change**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/voice/AudioPlayer.kt \
  kotlin/app/src/test/java/com/precor/treadmill/voice/AudioPlayerQueuePolicyTest.kt
git commit -m "fix: retain longer Gemini voice responses"
```

### Task 2: Verify, install, and exercise the tablet

**Files:**
- Modify through `bd`: `.beads/`
- Build artifact: `kotlin/app/build/outputs/apk/debug/app-debug.apk`

- [ ] **Step 1: Run repository quality gates**

```bash
cd kotlin
./gradlew :app:testDebugUnitTest :app:assembleDebug
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 2: Discover, install, and launch on the SM-X115**

```bash
adb mdns services
adb connect '<discovered-IP:port>'
adb devices -l
export PRECOR_TABLET_SERIAL='<selector whose row contains model:SM_X115>'
adb -s "$PRECOR_TABLET_SERIAL" install -r \
  kotlin/app/build/outputs/apk/debug/app-debug.apk
adb -s "$PRECOR_TABLET_SERIAL" shell monkey \
  -p com.precor.treadmill -c android.intent.category.LAUNCHER 1
adb -s "$PRECOR_TABLET_SERIAL" shell pidof com.precor.treadmill
```

Expected: the selected row identifies `model:SM_X115`, install reports `Success`,
and `pidof` returns a live `com.precor.treadmill` process.

- [ ] **Step 3: Verify long playback and barge-in on the device**

Clear logcat, request a response long enough to stress playback, then interrupt a
second queued response by speaking over the model:

```bash
adb devices -l
export PRECOR_TABLET_SERIAL='<current selector whose row contains model:SM_X115>'
adb -s "$PRECOR_TABLET_SERIAL" logcat -c
# Perform the two voice interactions on the tablet.
adb -s "$PRECOR_TABLET_SERIAL" logcat -d -v threadtime | \
  rg "GeminiLive|AudioPlayer|Queue overflow|>>> INTERRUPTED|Playback flushed|Playback done" | \
  tee /tmp/precor-gemini-audio-queue-verification.log
```

Expected: the retained timestamped capture contains `Playback done` for the long
response and no `Queue overflow`; the second interaction contains
`>>> INTERRUPTED` followed promptly by `Playback flushed`. Confirm audibly that
no stale pre-interruption speech continues.

- [ ] **Step 4: Close the bead and complete repository sync**

```bash
bd close precor-9_3x-483 --reason "120-second bounded queue verified by regression suite, Android build, and tablet playback/barge-in checks."
bd dolt push
git add .beads/
git commit -m "chore: close Gemini audio queue bug"
git pull --rebase
git push
git status --short --branch
```

Expected: branch is up to date with `origin/main`; only the unrelated `static/` directory remains untracked.
