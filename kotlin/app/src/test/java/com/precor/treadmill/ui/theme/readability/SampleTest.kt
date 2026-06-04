package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertEquals
import org.junit.Test

class SampleTest {
    @Test fun meanColorOverRect() {
        val w = 100; val h = 100
        val pixels = IntArray(w*h) { (0xFF shl 24) or (80 shl 16) or (160 shl 8) or 120 }
        val s = sampleRegionPixels(pixels, w, h, NormRect(0.25, 0.25, 0.5, 0.5), "m", Role.BODY)
        assertEquals(80.0, s.avg.r, 1.0)
        assertEquals(160.0, s.avg.g, 1.0)
    }
}
