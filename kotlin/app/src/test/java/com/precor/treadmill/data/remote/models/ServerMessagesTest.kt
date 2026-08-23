package com.precor.treadmill.data.remote.models

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

class ServerMessagesTest {

    @Test
    fun `interval preserves fractional duration from server`() {
        val interval = Json.decodeFromString<Interval>(
            """{"name":"stride","duration":0.25,"speed":4.5,"incline":3.0}""",
        )

        assertEquals(0.25, interval.duration, 1e-9)
    }
}
