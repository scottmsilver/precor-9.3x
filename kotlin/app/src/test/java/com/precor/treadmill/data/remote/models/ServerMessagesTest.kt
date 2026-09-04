package com.precor.treadmill.data.remote.models

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerMessagesTest {

    @Test
    fun `interval preserves fractional duration from server`() {
        val interval = Json.decodeFromString<Interval>(
            """{"name":"stride","duration":0.25,"speed":4.5,"incline":3.0}""",
        )

        assertEquals(0.25, interval.duration, 1e-9)
    }

    @Test
    fun `history entry is resumable only before its terminal position`() {
        val partial = HistoryEntry(totalDuration = 240.0, lastElapsed = 130.0)
        val terminalWithStaleFlag = HistoryEntry(
            totalDuration = 240.0,
            completed = false,
            lastElapsed = 240.0,
        )

        assertTrue(partial.isResumable)
        assertEquals(110.0, partial.remainingOnResume, 1e-9)
        assertFalse(terminalWithStaleFlag.isResumable)
    }

    @Test
    fun `fractional history duration preserves the exact resume boundary`() {
        val entry = Json.decodeFromString<HistoryEntry>(
            """{"total_duration":60.5,"last_elapsed":60,"program":{"name":"Fractional","intervals":[{"name":"Only","duration":60.5,"speed":3,"incline":0}]}}""",
        )

        assertEquals(60.5, entry.totalDuration, 1e-9)
        assertTrue(entry.isResumable)
        assertEquals(0.5, entry.remainingOnResume, 1e-9)
    }
}
