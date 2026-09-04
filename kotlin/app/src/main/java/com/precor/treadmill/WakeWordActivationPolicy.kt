package com.precor.treadmill

/** Filters low-confidence and repeated wake-word detections. */
internal class WakeWordActivationPolicy {
    companion object {
        const val MINIMUM_SCORE = 0.70f
        const val REARM_INTERVAL_MS = 10_000L
    }

    private var waitingForListeningRestart = false
    private var suppressUntilMs = Long.MIN_VALUE
    private var handoffGeneration = 0L

    fun currentHandoffGeneration(): Long = handoffGeneration

    fun invalidatePendingHandoffs() {
        handoffGeneration++
    }

    fun isHandoffCurrent(generation: Long): Boolean = generation == handoffGeneration

    fun onListeningStarted(nowMs: Long) {
        if (!waitingForListeningRestart) return
        waitingForListeningRestart = false
        suppressUntilMs = nowMs + REARM_INTERVAL_MS
    }

    fun shouldActivate(score: Float, nowMs: Long): Boolean {
        if (score <= MINIMUM_SCORE) return false
        if (waitingForListeningRestart || nowMs < suppressUntilMs) return false

        waitingForListeningRestart = true
        return true
    }
}
