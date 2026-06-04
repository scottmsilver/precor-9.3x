package com.precor.treadmill.ui.theme.readability

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

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
}
