package com.precor.treadmill.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Regression: the mm→dp touch sizing trusted displayMetrics.xdpi/ydpi blindly.
 * Emulators (and some buggy panels) report a physical dpi wildly different from
 * the density bucket — e.g. budtmo docker-android reports 640 physical dpi while
 * `wm density 160` sets the bucket to 160, inflating a 20mm Stop button to ~500dp.
 * sanitizedDpi() must fall back to densityDpi outside a 0.5×–2× sanity band and
 * pass honest values (like the SM-X115 tablet's 179.8 @ 213) through unchanged.
 */
class TouchSanitizedDpiTest {

    @Test
    fun honestPanelDpiPassesThrough() {
        // SM-X115 tablet: xdpi 179.76, bucket 213 → ratio 0.84, trusted
        assertEquals(179.76f, Touch.sanitizedDpi(179.76f, 213f))
    }

    @Test
    fun emulatorDensityOverrideFallsBackToBucket() {
        // budtmo emulator: physical 640, bucket overridden to 160 → ratio 4, fall back
        assertEquals(160f, Touch.sanitizedDpi(640f, 160f))
    }

    @Test
    fun tooLowReportedDpiFallsBackToBucket() {
        // A panel reporting nonsense-low physical dpi (seen on cheap devices)
        assertEquals(320f, Touch.sanitizedDpi(30f, 320f))
    }

    @Test
    fun bandEdgesAreTrusted() {
        assertEquals(80f, Touch.sanitizedDpi(80f, 160f))   // exactly 0.5×
        assertEquals(320f, Touch.sanitizedDpi(320f, 160f)) // exactly 2×
    }

    @Test
    fun brokenBaselineBottomsOutAtMdpi() {
        // Garbage densityDpi must not become the fallback itself.
        assertEquals(160f, Touch.sanitizedDpi(640f, 0f))
        assertEquals(160f, Touch.sanitizedDpi(640f, -240f))
        assertEquals(160f, Touch.sanitizedDpi(640f, Float.NaN))
        // ...but a reported dpi inside the mdpi band is still trusted.
        assertEquals(140f, Touch.sanitizedDpi(140f, 0f))
    }

    @Test
    fun fallbackKeepsPhysicalSizeAtDensityAssumption() {
        // With the bucket fallback, mm→dp collapses to the classic 160dp/inch rule:
        // dp = mm/25.4 * densityDpi / (densityDpi/160) = mm/25.4*160 — 20mm ≈ 126dp.
        val densityDpi = 160f
        val density = densityDpi / 160f
        val dpi = Touch.sanitizedDpi(640f, densityDpi)
        val stopDp = (20f / 25.4f) * dpi / density
        assertEquals(125.98f, stopDp, 0.05f)
    }
}
