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

    @Test fun legibleTreatmentDarkensBackgroundAndStaysLegibleKeepingHue() {
        val bg = rgb(180, 200, 150)        // bright greenish gap (low contrast for a green accent)
        val accent = rgb(107, 200, 139)    // mid green accent
        val scrimTint = rgb(20, 24, 20)    // dark engine tint
        assertTrue("starts illegible", abs(apcaLc(accent, bg)) < 60.0)
        val t = legibleTreatment(accent, bg, scrimTint, 70.0)
        assertTrue("applies a local scrim (darkens the background)", t.scrimAlpha > 0.0)
        val over = composite(bg, scrimTint, t.scrimAlpha)
        assertTrue("legible after treatment, got ${abs(apcaLc(t.color, over))}", abs(apcaLc(t.color, over)) >= 69.0)
        assertTrue("hue stays green, got ${rgbToOklch(t.color).h}", rgbToOklch(t.color).h in 120.0..200.0)
    }

    @Test fun legibleTreatmentKeepsColorExactlyWhenScrimAloneSuffices() {
        // A light accent reaches the target purely by darkening the background — color untouched.
        val bg = rgb(150, 170, 140)
        val accent = rgb(210, 235, 205)
        val scrimTint = rgb(15, 18, 15)
        val t = legibleTreatment(accent, bg, scrimTint, 55.0)
        assertTrue("applies a scrim", t.scrimAlpha > 0.0)
        assertEquals("accent unchanged — scrim did the work", accent, t.color)
    }

    @Test fun surfaceScrimAlphaMakesSurfaceStandOutFromBackground() {
        val bg = rgb(180, 160, 120)   // bright photo
        val scrim = rgb(20, 24, 20)   // dark tint
        val a = surfaceScrimAlpha(bg, scrim, 20.0)
        assertTrue("needs some scrim", a > 0.0)
        assertTrue("surface clears the affordance target", abs(apcaLc(composite(bg, scrim, a), bg)) >= 19.0)
    }

    @Test fun surfaceScrimAlphaNeedsMoreWhenScrimMatchesBackground() {
        // On BRIGHT green foliage, a green surface must be more opaque to stand out than a dark
        // scrim (which diverges fast because dark-on-bright is high contrast).
        val bg = rgb(120, 165, 95)
        val similar = rgb(130, 180, 105)
        val dark = rgb(18, 20, 18)
        assertTrue(surfaceScrimAlpha(bg, similar, 20.0) > surfaceScrimAlpha(bg, dark, 20.0))
    }

    @Test fun legibleTreatmentNoScrimWhenAlreadyLegible() {
        val t = legibleTreatment(rgb(255, 255, 255), rgb(20, 24, 20), rgb(20, 24, 20), 60.0)
        assertEquals(0.0, t.scrimAlpha, 1e-9)
    }

    @Test fun solveFreeTextEscalatesWhenChosenPolarityMissesTarget() {
        // Mid-tone grey: charcoal naturally out-contrasts ivory, but charcoal alone still misses
        // the HERO target. The old logic returned CHARCOAL with no scrim (washed-out timer); the
        // solver must escalate so the final text + its scrim actually clears the target.
        val bg = rgb(176, 176, 176)
        val tint = rgb(16, 16, 18)
        val target = 75.0
        assertTrue("charcoal naturally wins here", abs(apcaLc(CHARCOAL, bg)) > abs(apcaLc(IVORY, bg)))
        assertTrue("but charcoal alone misses target", abs(apcaLc(CHARCOAL, bg)) < target)
        val (color, scrim) = solveFreeText(bg, tint, target)
        val over = composite(bg, tint, scrim)
        assertTrue("legible after its scrim, got ${abs(apcaLc(color, over))}", abs(apcaLc(color, over)) >= target - 1.0)
    }

    @Test fun solveFreeTextKeepsCharcoalGlassyOnBrightBg() {
        // On a genuinely bright background charcoal clears on its own → no scrim (stays glassy).
        val (color, scrim) = solveFreeText(rgb(235, 235, 230), rgb(16, 16, 18), 75.0)
        assertEquals("charcoal chosen", CHARCOAL, color)
        assertEquals("no scrim needed", 0.0, scrim, 1e-9)
    }

    @Test fun solveFreeTextUsesIvoryWithoutScrimOnDarkBg() {
        val (color, scrim) = solveFreeText(rgb(20, 22, 20), rgb(16, 16, 18), 75.0)
        assertEquals("ivory chosen", IVORY, color)
        assertEquals("no scrim needed", 0.0, scrim, 1e-9)
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
