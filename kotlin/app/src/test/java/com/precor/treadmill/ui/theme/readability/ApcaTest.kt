package com.precor.treadmill.ui.theme.readability

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class ApcaTest {
    @Test fun matchesCanonicalAnchors() {
        assertEquals(106.04, apcaLc(rgb(0,0,0), rgb(255,255,255)), 0.2)
        assertEquals(-107.88, apcaLc(rgb(255,255,255), rgb(0,0,0)), 0.2)
    }
    @Test fun matchesEveryGoldenRow() {
        val json = JSONObject(javaClass.getResource("/golden.json")!!.readText())
        val rows = json.getJSONArray("apca")
        for (i in 0 until rows.length()) {
            val r = rows.getJSONObject(i)
            assertEquals(r.getDouble("lc"), apcaLc(hexToRgb(r.getString("text")), hexToRgb(r.getString("bg"))), 0.2)
        }
    }
}
