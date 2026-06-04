package com.precor.treadmill.ui.theme.readability

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class EngineTest {
    private fun region(j: JSONObject): RegionStats {
        fun col(o: JSONObject) = Rgb(o.getDouble("r"), o.getDouble("g"), o.getDouble("b"))
        return RegionStats(j.getString("id"), Role.valueOf(j.getString("role").uppercase()),
            col(j.getJSONObject("avg")), col(j.getJSONObject("dominant")), j.getDouble("luma"))
    }

    @Test fun reproducesGoldenThemes() {
        val json = JSONObject(javaClass.getResource("/golden.json")!!.readText())
        val cases = json.getJSONArray("themes")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val regions = (0 until c.getJSONArray("regions").length()).map { region(c.getJSONArray("regions").getJSONObject(it)) }
            val priorJ = c.getJSONObject("prior")
            val prior = AdvicePrior(paletteHue = if (priorJ.has("paletteHue")) priorJ.getDouble("paletteHue") else null)
            val theme = chooseTheme(regions, prior).theme
            val expected = c.getJSONObject("theme")
            assertEquals("tint.r ${c.getString("name")}", expected.getJSONObject("tint").getDouble("r"), theme.tint.r, 1.0)
            assertEquals("scrim ${c.getString("name")}", expected.getDouble("baseScrimAlpha"), theme.baseScrimAlpha, 0.001)
        }
    }

    @Test fun everyRegionMeetsTargetUnderChosenTheme() {
        val regions = listOf(
            RegionStats("timer", Role.HERO, rgb(90,110,95), rgb(90,110,95), 100.0),
            RegionStats("speed", Role.BODY, rgb(200,205,190), rgb(200,205,190), 200.0),
        )
        val theme = chooseTheme(regions, AdvicePrior(paletteHue = 150.0)).theme
        for (r in regions) assertTrue(fitRegion(theme, r).met)
    }

    @Test fun ensureLegibleLeavesAlreadyLegibleColorUnchanged() {
        // white on black already clears any target → returned untouched.
        assertEquals(rgb(255, 255, 255), ensureLegible(rgb(255, 255, 255), rgb(0, 0, 0), 60.0))
    }

    @Test fun ensureLegibleFixesLowContrastAccent() {
        val bg = rgb(100, 130, 120)        // greenish panel background
        val accent = rgb(107, 143, 139)    // muted grey-teal — nearly same luminance
        assertTrue("accent should start illegible", abs(apcaLc(accent, bg)) < 45.0)
        val fixed = ensureLegible(accent, bg, 60.0)
        assertTrue("fixed should clear the target", abs(apcaLc(fixed, bg)) >= 59.0)
        // hue broadly preserved (still greenish, not shifted to a different family)
        val h = rgbToOklch(fixed).h
        assertTrue("hue stays in green range, got $h", h in 120.0..200.0)
    }
}
