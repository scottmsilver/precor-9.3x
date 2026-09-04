package com.precor.treadmill

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordActivationPolicyTest {
    @Test
    fun rejectsMeasuredAmbientPeakButAcceptsMeasuredIntentionalPhrase() {
        val policy = WakeWordActivationPolicy()

        assertFalse(policy.shouldActivate(score = 0.504f, nowMs = 1_000L))
        assertTrue(policy.shouldActivate(score = 0.725f, nowMs = 1_000L))
    }

    @Test
    fun suppressesRepeatedActivationUntilRearmIntervalExpires() {
        val policy = WakeWordActivationPolicy()

        assertTrue(policy.shouldActivate(score = 0.725f, nowMs = 1_000L))
        assertFalse(policy.shouldActivate(score = 0.725f, nowMs = 60_000L))

        policy.onListeningStarted(nowMs = 60_000L)

        assertFalse(policy.shouldActivate(score = 0.725f, nowMs = 69_999L))
        assertTrue(policy.shouldActivate(score = 0.725f, nowMs = 70_000L))
    }

    @Test
    fun invalidatesAcceptedHandoffAcrossDisableAndReenable() {
        val policy = WakeWordActivationPolicy()

        assertTrue(policy.shouldActivate(score = 0.725f, nowMs = 1_000L))
        val staleHandoff = policy.currentHandoffGeneration()

        policy.invalidatePendingHandoffs()
        policy.onListeningStarted(nowMs = 2_000L)

        assertFalse(policy.isHandoffCurrent(staleHandoff))
        assertFalse(policy.shouldActivate(score = 0.725f, nowMs = 11_999L))
        assertTrue(policy.shouldActivate(score = 0.725f, nowMs = 12_000L))
    }
}
