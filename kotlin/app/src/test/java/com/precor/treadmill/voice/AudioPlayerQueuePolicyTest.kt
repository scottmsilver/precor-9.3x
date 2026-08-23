package com.precor.treadmill.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioPlayerQueuePolicyTest {
    @Test
    fun admitsForcedBurstBeyondOldLimitThrough120Seconds() {
        val chunkBytes = 9_600
        val limitBytes = AudioPlayer.maxQueueBytes(24_000)
        var queued = 0

        repeat(600) {
            assertTrue(AudioPlayer.canAdmit(queued, chunkBytes, limitBytes))
            queued += chunkBytes
        }

        assertEquals(5_760_000, queued)
        assertFalse(AudioPlayer.canAdmit(queued, chunkBytes, limitBytes))
        assertFalse(AudioPlayer.canAdmit(Int.MAX_VALUE, chunkBytes, limitBytes))
        assertTrue(queued > 480_000)
    }
}
