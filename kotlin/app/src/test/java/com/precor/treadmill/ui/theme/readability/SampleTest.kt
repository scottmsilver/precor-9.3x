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

    @Test fun cropMapIsIdentityWhenAspectMatches() {
        // container same aspect as image -> no crop, rect unchanged.
        val r = cropMapRect(100, 100, 100f, 100f, NormRect(0.2, 0.3, 0.4, 0.5))
        assertEquals(0.2, r.x, 1e-6); assertEquals(0.3, r.y, 1e-6)
        assertEquals(0.4, r.w, 1e-6); assertEquals(0.5, r.h, 1e-6)
    }

    @Test fun cropMapTallerContainerCropsSides() {
        // square image into a 100x200 (taller) container: ContentScale.Crop scales x2,
        // shows the middle 50% horizontally, full height.
        val r = cropMapRect(100, 100, 100f, 200f, NormRect(0.0, 0.0, 1.0, 1.0))
        assertEquals(0.25, r.x, 1e-6); assertEquals(0.0, r.y, 1e-6)
        assertEquals(0.5, r.w, 1e-6); assertEquals(1.0, r.h, 1e-6)
    }

    @Test fun cropMapWiderContainerCropsTopBottom() {
        // square image into a 200x100 (wider) container: shows full width, middle 50% vertically.
        val r = cropMapRect(100, 100, 200f, 100f, NormRect(0.0, 0.0, 1.0, 1.0))
        assertEquals(0.0, r.x, 1e-6); assertEquals(0.25, r.y, 1e-6)
        assertEquals(1.0, r.w, 1e-6); assertEquals(0.5, r.h, 1e-6)
    }

    @Test fun cropMapDegradesToInputOnZeroContainer() {
        val rect = NormRect(0.1, 0.1, 0.2, 0.2)
        assertEquals(rect, cropMapRect(100, 100, 0f, 0f, rect))
    }
}
